"""Read prompt execution records and reconcile queued dispatch reservations."""

from __future__ import annotations

from collections.abc import (
    Mapping,
)
from datetime import (
    UTC,
    datetime,
)
from pathlib import (
    Path,
)
from types import (
    SimpleNamespace,
)
from typing import (
    Any,
)

from evander.agent_threads import (
    agent_resume_thread_missing,
    usable_agent_thread_id,
)
from evander.effects.providers.agent_prompt import (
    AgentPromptEffectProvider,
)
from evander.effects.providers.base import (
    EffectRuntimeContext,
)
from evander.execution import (
    ExecutionBackendKind,
    ExecutionStatusKind,
)
from evander.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
)
from evander.orchestration.effect_extensions import (
    list_vm_summaries_for_agent_reconciliation,
)

from .models import (
    TeamDispatchStatus,
    TeamState,
)
from .provider_constants import (
    logger,
)
from .provider_triggers import (
    _team_payload_datetime,
)
from .provider_values import (
    _optional_text,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .services.runtime_service import (
    AgentDispatchNativeThreadReceipt,
    TeamRuntimeService,
)


def _runtime_dir_from_worker(worker: Any) -> Path | None:
    for source in (getattr(worker, "worker_config", None), getattr(worker, "config", None)):
        runtime_dir = getattr(source, "runtime_dir", None)
        if runtime_dir is not None:
            return Path(runtime_dir)
    return None


def _effect_context_from_worker(worker: Any, *, runtime_dir: Path) -> EffectRuntimeContext:
    factory = getattr(worker, "provider_factory", None)
    admin_service = getattr(factory, "admin_service", None)
    publisher_getter = getattr(factory, "get_internal_event_publisher", None)
    return EffectRuntimeContext(
        resolved_config={"runtime_dir": str(runtime_dir)},
        admin_service=admin_service,
        internal_event_publisher=(
            publisher_getter(runtime_dir=runtime_dir) if callable(publisher_getter) else None
        ),
    )


def _team_queue_dispatch_is_current(
    state: TeamState,
    *,
    dispatch_id: str,
    record_session_id: str | None,
) -> bool:
    dispatch = state.dispatches.get(dispatch_id)
    if dispatch is None or dispatch.status != TeamDispatchStatus.PREPARING:
        return False
    runtime = state.agent_runtimes.get(dispatch.agent_id)
    return bool(
        runtime is not None
        and runtime.active_dispatch_id == dispatch.id
        and (record_session_id is None or record_session_id == dispatch.session_id)
    )


def _team_queue_governance_group_id(
    state: TeamState,
    *,
    dispatch_id: str,
    record_session_id: str | None,
) -> str | None:
    for group_id, runtime in state.governance_runtimes.items():
        if runtime.active_dispatch_id != dispatch_id or runtime.wake_leased_until is not None:
            continue
        group = state.governance_groups.get(group_id)
        if group is None:
            continue
        manager = state.agents.get(group.manager_agent_id)
        if manager is None:
            continue
        if record_session_id is not None and record_session_id != manager.session_id:
            continue
        return group_id
    return None


def _reconcile_team_queued_prompt_reservations(
    worker: Any,
    *,
    repo: TeamStateRepository,
    state: TeamState,
    now: datetime,
    queued_records: Mapping[str, Any] | None = None,
) -> tuple[TeamState, int]:
    """Validate Team queue ownership before the generic replay phase runs."""

    queue_store = getattr(worker, "queued_agent_prompt_store", None)
    read_all = getattr(queue_store, "read_all", None)
    delete = getattr(queue_store, "delete", None)
    compare_delete = getattr(queue_store, "delete_if_dispatch_id", None)
    if not callable(read_all):
        return state, 0
    try:
        if queued_records is None:
            queued_records = read_all()
    except Exception:
        logger.exception("Team queued-prompt reservation reconciliation could not read the queue")
        return state, 0
    if not isinstance(queued_records, Mapping) or not queued_records:
        return state, 0

    def delete_current_record(queue_key: str, dispatch_id: str) -> bool:
        if callable(compare_delete):
            return bool(compare_delete(queue_key, dispatch_id))
        if not callable(delete):
            return False
        current_records = read_all()
        current = current_records.get(queue_key) if isinstance(current_records, Mapping) else None
        current_dispatch_id = _optional_text(getattr(current, "dispatch_id", None))
        if current_dispatch_id != dispatch_id:
            return False
        delete(queue_key)
        return True

    reconciled_count = 0
    for queue_key, record in sorted(queued_records.items()):
        dispatch_id = _optional_text(getattr(record, "dispatch_id", None))
        if dispatch_id is None:
            continue
        record_session_id = _optional_text(getattr(record, "session_id", None))
        dispatch = state.dispatches.get(dispatch_id)
        if dispatch is not None:
            if _team_queue_dispatch_is_current(
                state,
                dispatch_id=dispatch_id,
                record_session_id=record_session_id,
            ):
                if dispatch.queued_at is None:
                    try:
                        state = TeamRuntimeService(repo).mark_agent_dispatch_queued(
                            agent_id=dispatch.agent_id,
                            dispatch_id=dispatch.id,
                            now=now,
                            state=state,
                        )
                    except (RuntimeError, ValueError, TeamStateConflictError):
                        state = repo.read()
                        if _team_queue_dispatch_is_current(
                            state,
                            dispatch_id=dispatch_id,
                            record_session_id=record_session_id,
                        ):
                            try:
                                state = TeamRuntimeService(repo).mark_agent_dispatch_queued(
                                    agent_id=dispatch.agent_id,
                                    dispatch_id=dispatch.id,
                                    now=now,
                                    state=state,
                                )
                            except (RuntimeError, ValueError, TeamStateConflictError):
                                state = repo.read()
                                if _team_queue_dispatch_is_current(
                                    state,
                                    dispatch_id=dispatch_id,
                                    record_session_id=record_session_id,
                                ):
                                    continue
                            else:
                                reconciled_count += 1
                                continue
                    else:
                        reconciled_count += 1
                        continue
                elif _team_queue_dispatch_is_current(
                    state,
                    dispatch_id=dispatch_id,
                    record_session_id=record_session_id,
                ):
                    continue
            if not delete_current_record(queue_key, dispatch_id):
                continue
            logger.warning(
                "discarded stale Team queued prompt queue_key=%s dispatch_id=%s",
                queue_key,
                dispatch_id,
            )
            reconciled_count += 1
            continue

        governance_group_id = _team_queue_governance_group_id(
            state,
            dispatch_id=dispatch_id,
            record_session_id=record_session_id,
        )
        if governance_group_id is not None:
            runtime = state.governance_runtimes[governance_group_id]
            if runtime.queued_at is None:
                try:
                    state = TeamRuntimeService(repo).mark_governance_dispatch_queued(
                        governance_group_id=governance_group_id,
                        dispatch_id=dispatch_id,
                        now=now,
                    )
                except (RuntimeError, ValueError, TeamStateConflictError):
                    state = repo.read()
                    refreshed_group_id = _team_queue_governance_group_id(
                        state,
                        dispatch_id=dispatch_id,
                        record_session_id=record_session_id,
                    )
                    if refreshed_group_id is not None:
                        try:
                            state = TeamRuntimeService(repo).mark_governance_dispatch_queued(
                                governance_group_id=refreshed_group_id,
                                dispatch_id=dispatch_id,
                                now=now,
                            )
                        except (RuntimeError, ValueError, TeamStateConflictError):
                            state = repo.read()
                            if (
                                _team_queue_governance_group_id(
                                    state,
                                    dispatch_id=dispatch_id,
                                    record_session_id=record_session_id,
                                )
                                is not None
                            ):
                                continue
                        else:
                            reconciled_count += 1
                            continue
                else:
                    reconciled_count += 1
                    continue
            elif (
                _team_queue_governance_group_id(
                    state,
                    dispatch_id=dispatch_id,
                    record_session_id=record_session_id,
                )
                is not None
            ):
                continue

        if dispatch_id.startswith(("team-dispatch-", "team-chat:", "team-governance-dispatch-")):
            if not delete_current_record(queue_key, dispatch_id):
                continue
            logger.warning(
                "discarded unowned Team queued prompt queue_key=%s dispatch_id=%s",
                queue_key,
                dispatch_id,
            )
            reconciled_count += 1

    return state, reconciled_count


def _agent_prompt_records_for_dispatches(worker: Any, dispatch_ids: set[str]) -> list[Any]:
    if not dispatch_ids:
        return []
    store = getattr(worker, "execution_store", None)
    query = getattr(store, "list_prompt_lifecycle_receipts", None)
    if not callable(query):
        query = getattr(store, "list_prompt_records_for_dispatches", None)
    if callable(query):
        return [r for r in query(dispatch_ids) if not _record_is_queued_prompt_marker(r)]
    # Compatibility for third-party repositories. Production SQLite uses the
    # indexed lookup above and never deserializes unrelated terminal history.
    return [
        r
        for r in [*_waiting_agent_prompt_records(worker), *_completed_agent_prompt_records(worker)]
        if _optional_text(_record_outputs(r).get("dispatch_id")) in dispatch_ids
    ]


def _completed_agent_prompt_records(worker: Any) -> list[Any]:
    execution_store = getattr(worker, "execution_store", None)
    if execution_store is None:
        return []
    records: list[Any] = []
    list_by_type_and_statuses = getattr(
        execution_store,
        "list_records_by_node_type_and_statuses",
        None,
    )
    if callable(list_by_type_and_statuses):
        try:
            records = list(
                list_by_type_and_statuses(
                    node_type="effect:agent:enqueue-prompt",
                    statuses={"succeeded", "failed"},
                )
            )
        except Exception:
            records = []
        else:
            return _completed_agent_prompt_records_from_records(records)
    list_executions = getattr(execution_store, "list_executions", None)
    if callable(list_executions):
        try:
            records = list(list_executions())
        except Exception:
            records = []
    if not records:
        read_all = getattr(execution_store, "read_all", None)
        if callable(read_all):
            try:
                raw = read_all()
                records = list(raw.values()) if isinstance(raw, Mapping) else []
            except Exception:
                records = []
    return _completed_agent_prompt_records_from_records(records)


def _completed_agent_prompt_records_from_records(records: list[Any]) -> list[Any]:
    candidates = [
        record
        for record in records
        if _optional_text(getattr(record, "effect_type", None)) == "effect:agent:enqueue-prompt"
        and _optional_text(getattr(record, "status", None)) in {"succeeded", "failed"}
        and _record_outputs(record)
        and not _record_is_queued_prompt_marker(record)
    ]
    candidates.sort(
        key=lambda record: (
            getattr(record, "finished_at", datetime.min.replace(tzinfo=UTC)),
            _optional_text(getattr(record, "execution_id", None)) or "",
        )
    )
    return candidates


def _waiting_agent_prompt_records(worker: Any) -> list[Any]:
    execution_store = getattr(worker, "execution_store", None)
    if execution_store is None:
        return []
    list_waiting = getattr(execution_store, "list_waiting_records", None)
    if callable(list_waiting):
        try:
            records = list(list_waiting("effect:agent:enqueue-prompt"))
        except Exception:
            return []
        return [
            record
            for record in records
            if _optional_text(getattr(record, "effect_type", None)) == "effect:agent:enqueue-prompt"
            and _optional_text(getattr(record, "status", None)) == "waiting"
            and not _record_is_queued_prompt_marker(record)
        ]
    list_executions = getattr(execution_store, "list_executions", None)
    if callable(list_executions):
        try:
            records = list(list_executions())
        except Exception:
            return []
    else:
        read_all = getattr(execution_store, "read_all", None)
        if not callable(read_all):
            return []
        try:
            raw = read_all()
            records = list(raw.values()) if isinstance(raw, Mapping) else []
        except Exception:
            return []
    return [
        record
        for record in records
        if _optional_text(getattr(record, "effect_type", None)) == "effect:agent:enqueue-prompt"
        and _optional_text(getattr(record, "status", None)) == "waiting"
        and not _record_is_queued_prompt_marker(record)
    ]


def _team_dispatch_execution_is_live(
    worker: Any,
    *,
    record: Any,
    dispatch_id: str,
    session_id: str,
) -> bool:
    outputs = _record_outputs(record)
    if (
        _optional_text(outputs.get("dispatch_id")) != dispatch_id
        or _optional_text(outputs.get("session_id")) != session_id
    ):
        return False
    execution_backend = _optional_text(outputs.get("execution_backend"))
    if execution_backend == ExecutionBackendKind.CONTAINER_WORKER_VM.value:
        execution_id = _optional_text(outputs.get("execution_id"))
        if execution_id is None:
            return False
        backend = AgentPromptEffectProvider._container_backend_for_worker(
            worker=worker,
            session_id=session_id,
        )
        if backend is None:
            return False
        try:
            status = backend.inspect(execution_id)
        except Exception:
            return False
        status_execution_id = _optional_text(getattr(status, "execution_id", None))
        if status_execution_id is not None and status_execution_id != execution_id:
            return False
        status_session_id = _optional_text(getattr(status, "session_id", None))
        if status_session_id is not None and status_session_id != session_id:
            return False
        raw_status = getattr(status, "status", None)
        status_value = _optional_text(getattr(raw_status, "value", raw_status))
        return status_value in {
            ExecutionStatusKind.PREPARING.value,
            ExecutionStatusKind.STARTING.value,
            ExecutionStatusKind.RUNNING.value,
        }
    if execution_backend != ExecutionBackendKind.LIBVIRT_VM.value:
        return False
    try:
        summaries = list_vm_summaries_for_agent_reconciliation(worker)
    except Exception:
        return False
    return any(
        _optional_text(getattr(summary, "dispatch_id", None)) == dispatch_id
        and _optional_text(getattr(summary, "agent_state", None)) == "running"
        and (
            not getattr(summary, "session_ids", None)
            or session_id
            in {_optional_text(candidate) for candidate in getattr(summary, "session_ids", ())}
        )
        for summary in summaries
    )


def _team_native_thread_receipt(record: Any) -> AgentDispatchNativeThreadReceipt | None:
    """Use the same receipt validation for batch and workflow finalization."""
    if isinstance(record, Mapping):
        record = SimpleNamespace(**record)
    if record is None or _record_is_queued_prompt_marker(record):
        return None
    outputs = _record_outputs(record)
    dispatch_id = _optional_text(outputs.get("dispatch_id"))
    session_id = _completed_prompt_session_id(record)
    if dispatch_id is None or session_id is None:
        return None
    observed_at = _team_payload_datetime(outputs.get("native_thread_observed_at")) or (
        _team_payload_datetime(getattr(record, "finished_at", None))
    )
    requested_thread_id = _optional_text(
        outputs.get("resume_thread_id")
        if "resume_thread_id" in outputs
        else AgentPromptEffectProvider._record_text_value(record, "resume_thread_id")
    )
    native_thread_observed = usable_agent_thread_id(
        outputs.get("native_thread_id")
    ) is not None and outputs.get("native_thread_execution_id") == outputs.get("execution_id")
    if not native_thread_observed and _resume_failure_reports_missing_thread(
        outputs, resume_thread_id=requested_thread_id
    ):
        return AgentDispatchNativeThreadReceipt(
            session_id=session_id,
            dispatch_id=dispatch_id,
            thread_id=requested_thread_id,
            observed_at=observed_at,
            missing_thread=True,
        )
    thread_id = usable_agent_thread_id(
        outputs.get("native_thread_id", outputs.get("thread_id")),
        dispatch_id,
        outputs.get("execution_id"),
        getattr(record, "execution_id", None),
    )
    if thread_id is None or outputs.get("native_thread_execution_id") not in {
        None,
        outputs.get("execution_id"),
    }:
        return None
    return AgentDispatchNativeThreadReceipt(
        session_id=session_id,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        observed_at=observed_at,
    )


def _record_is_queued_prompt_marker(record: Any) -> bool:
    # Queue replay deletes queue entries, so this must rely on the immutable
    # execution outputs instead of current queued-agent-prompts state.
    return _record_outputs(record).get("queued") is True


def _record_outputs(record: Any) -> dict[str, Any]:
    outputs = getattr(record, "outputs", None)
    if isinstance(outputs, Mapping):
        return dict(outputs)
    if isinstance(record, Mapping):
        outputs = record.get("outputs")
        if isinstance(outputs, Mapping):
            return dict(outputs)
    return {}


def _completed_prompt_session_id(record: Any) -> str | None:
    outputs = _record_outputs(record)
    session_id = _optional_text(outputs.get("session_id"))
    if session_id is not None:
        return session_id
    scope = outputs.get("scope")
    if isinstance(scope, Mapping):
        session_id = _optional_text(scope.get("session_id"))
        if session_id is not None:
            return session_id
    resolved_config = getattr(record, "resolved_config", None)
    if isinstance(resolved_config, Mapping):
        return _optional_text(resolved_config.get("session_id"))
    return None


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {"result": value}


def _enqueue_team_agent_prompt(resumer: Any, **kwargs: Any) -> Any:
    enqueue = getattr(resumer, "enqueue_agent_prompt", None)
    if callable(enqueue):
        return enqueue(**kwargs)
    return resumer.resume_agent(**kwargs)


def _result_reports_failure(result: Mapping[str, Any]) -> bool:
    if "success" not in result:
        return False
    return not _truthy_result_success(result.get("success"))


def _truthy_result_success(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
        return value != 0
    text = str(value).strip().lower()
    if text in {"", "0", "false", "no", "off"}:
        return False
    return True


def _resume_failure_message(result: Mapping[str, Any]) -> str:
    return (
        _optional_text(result.get("message"))
        or _optional_text(result.get("error"))
        or "resume_agent failed"
    )


def _resume_failure_reports_missing_thread(
    result: Mapping[str, Any],
    *,
    resume_thread_id: str | None,
) -> bool:
    normalized_thread_id = _optional_text(resume_thread_id)
    if normalized_thread_id is None:
        return False

    mappings = [result]
    for key in ("provider_details", "details", "result"):
        nested = result.get(key)
        if isinstance(nested, Mapping):
            mappings.append(nested)
    if not _result_reports_failure(result) and not any(
        _optional_text(payload.get("dispatch_error")) is not None for payload in mappings
    ):
        return False
    if "resume_thread_missing" in result:
        return result.get("resume_thread_missing") is True
    missing_codes = {
        "conversation_not_found",
        "missing_resume_thread",
        "resume_thread_missing",
        "resume_thread_not_found",
        "rollout_not_found",
        "thread_missing",
        "thread_not_found",
    }
    for payload in mappings:
        for key in ("code", "error_code", "reason_code"):
            code = _optional_text(payload.get(key))
            if code is not None and code.lower().replace("-", "_") in missing_codes:
                return True

    return any(
        agent_resume_thread_missing(payload.get(key), resume_thread_id=normalized_thread_id)
        for payload in mappings
        for key in ("message", "error", "detail", "diagnostic", "stderr", "dispatch_error", "logs")
    )


def _discard_failed_team_prompt_queue_record(
    repo: TeamStateRepository,
    *,
    result: Mapping[str, Any],
    dispatch_id: object,
) -> bool:
    if not bool(result.get("queued")):
        return True
    normalized_dispatch_id = _optional_text(dispatch_id)
    queue_key = _optional_text(result.get("queue_key"))
    if queue_key is None:
        provider_details = result.get("provider_details")
        if isinstance(provider_details, Mapping):
            queue_key = _optional_text(provider_details.get("queue_key"))
    if normalized_dispatch_id is None or queue_key is None:
        return False
    queue_store = AgentPromptQueueStore(repo.runtime_dir / "queued-agent-prompts.json")
    queued = queue_store.get(queue_key)
    if queued is None:
        return True
    return queue_store.delete_if_dispatch_id(queue_key, normalized_dispatch_id)
