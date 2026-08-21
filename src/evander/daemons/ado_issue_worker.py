from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from evander.bootstrap import load_runtime_gateway_config
from evander.audit import AuditEventType, AuditLogger
from evander.azure_devops import resolve_azure_devops_organization_url
from evander.config import (
    AdoWorkerGlobalConfig,
    GatewayConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from evander.directories import DirectoryService, DirectoryStore
from evander.events.models import TriggerEvent, materialize_node_definition
from evander.events.provider_models import (
    PullRequestCompletedTriggerDefinition,
    PullRequestNewFeedbackTriggerDefinition,
)
from evander.models import (
    DirectoryPath,
    PullRequestItem,
    PrFeedbackItem,
)
from evander.pr_tracking import (
    PrMapStore,
    completion_event_id,
    completion_event_payload,
    is_pr_completed,
    status_snapshot_from_pull_request,
)
from evander.pr_feedback.agent_directed import annotate_agent_directed_feedback
from evander.pr_feedback.providers import (
    AzureDevOpsFeedbackAdapter,
    pr_feedback_item_payload,
)
from evander.pr_feedback.store import FeedbackEnvelope, PrFeedbackStore, _stream_key
from evander.provider_config import ProviderConfigResolver
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.orchestration.host_polling import notify_wake_socket_for_lock
from evander.services import AzureDevOpsPrQueryProvider, _normalize_ignored_feedback_payload
from evander.triggers.providers._cursor_state import TriggerCursorStore
from evander.triggers.providers.pull_request_new_feedback import _parse_dt
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.workflow.node_state import workflow_node_is_enabled
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config

_LOG = logging.getLogger(__name__)
_ADO_WORKER_CONFIG_RELATIVE_PATH = Path("config") / "ado-worker" / "config.json"
_MIN_ADO_DATETIME = datetime.min.replace(tzinfo=timezone.utc)
_ADO_PR_COMPLETED_DEFAULT_STATUSES = ("completed", "abandoned")


@dataclass(frozen=True)
class AdoWorkerRuntimeConfig:
    poll_interval_seconds: float
    directories: tuple[str, ...]
    runtime_dir: Path

    @classmethod
    def load(cls, *, workspace_root: Path) -> AdoWorkerRuntimeConfig:
        raw_config = _load_json_object(workspace_root / _ADO_WORKER_CONFIG_RELATIVE_PATH)
        parsed = AdoWorkerGlobalConfig.model_validate(raw_config)
        runtime_dir = parsed.runtime_dir.expanduser()
        if not runtime_dir.is_absolute():
            runtime_dir = workspace_root / runtime_dir
        return cls(
            poll_interval_seconds=parsed.poll_interval_seconds,
            runtime_dir=runtime_dir,
            directories=tuple(parsed.directories),
        )


@dataclass(frozen=True)
class WorkerConfig:
    organization_url: str
    project: str
    poll_interval_seconds: float
    runtime_dir: Path
    directories: tuple[str, ...] = ()
    directory_path: str = "root"

    @classmethod
    def load(cls) -> WorkerConfig:
        worker_runtime_config = AdoWorkerRuntimeConfig.load(workspace_root=Path.cwd())
        runtime_dir = worker_runtime_config.runtime_dir
        runtime_gateway_config = load_runtime_gateway_config(runtime_dir=runtime_dir)
        initial_directory_path = (
            worker_runtime_config.directories[0] if worker_runtime_config.directories else "root"
        )
        return _resolve_worker_config_for_directory(
            worker_runtime_config=worker_runtime_config,
            runtime_gateway_config=runtime_gateway_config,
            directory_path=initial_directory_path,
        )


def _resolve_worker_config_for_directory(
    *,
    worker_runtime_config: AdoWorkerRuntimeConfig,
    runtime_gateway_config: GatewayConfig,
    directory_path: str,
) -> WorkerConfig:
    directory_store = DirectoryStore(runtime_gateway_config.directories_store_path)
    directory_store.ensure_root()
    directory_service = DirectoryService(directory_store)
    requested_directory_path = DirectoryPath(segments=directory_path).as_posix()
    try:
        normalized_directory_path = directory_service.show(requested_directory_path).directory.path
        resolution_directory_path = normalized_directory_path
    except ValueError:
        normalized_directory_path = requested_directory_path
        resolution_directory_path = "root"
    provider_config = ProviderConfigResolver(runtime_gateway_config).resolve_for_directory(
        resolution_directory_path
    )
    organization_url = provider_config.azure_devops_organization_url
    project = provider_config.azure_devops_project
    return WorkerConfig(
        organization_url=organization_url.rstrip("/"),
        project=project,
        poll_interval_seconds=worker_runtime_config.poll_interval_seconds,
        runtime_dir=worker_runtime_config.runtime_dir,
        directories=worker_runtime_config.directories,
        directory_path=normalized_directory_path,
    )


@dataclass(frozen=True)
class _ExplicitAdoPrCandidates:
    organization_url: str
    project: str
    pull_requests: list[PullRequestItem]


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON config file {path} must contain an object.")
    return raw


def _resolve_directory_worker_config(
    config: WorkerConfig,
    runtime_gateway_config: GatewayConfig,
    directory_path: str,
) -> WorkerConfig:
    return _resolve_worker_config_for_directory(
        worker_runtime_config=AdoWorkerRuntimeConfig(
            poll_interval_seconds=config.poll_interval_seconds,
            directories=config.directories,
            runtime_dir=config.runtime_dir,
        ),
        runtime_gateway_config=runtime_gateway_config,
        directory_path=directory_path,
    )


def _worker_runtime_config(config: WorkerConfig) -> GatewayConfig:
    return load_runtime_gateway_config(runtime_dir=config.runtime_dir)


def _trigger_orchestrator_config_path(config: WorkerConfig) -> Path:
    project_dir = (
        config.runtime_dir.parent if config.runtime_dir.name == "runtime" else config.runtime_dir
    )
    return project_dir / "config" / "trigger-orchestrator" / "config.json"


def _load_ado_pr_feedback_triggers(
    config: WorkerConfig,
) -> list[PullRequestNewFeedbackTriggerDefinition]:
    worker_config = _load_trigger_orchestrator_worker_config(config)
    if worker_config is None:
        return []
    triggers: list[PullRequestNewFeedbackTriggerDefinition] = []
    for trigger in worker_config.triggers:
        if not workflow_node_is_enabled(trigger):
            continue
        materialized = materialize_node_definition(trigger)
        if (
            isinstance(materialized, PullRequestNewFeedbackTriggerDefinition)
            and materialized.provider == "ado"
        ):
            triggers.append(materialized)
    return triggers


def _load_ado_pr_completed_triggers(
    config: WorkerConfig,
) -> list[PullRequestCompletedTriggerDefinition]:
    worker_config = _load_trigger_orchestrator_worker_config(config)
    if worker_config is None:
        return []
    triggers: list[PullRequestCompletedTriggerDefinition] = []
    for trigger in worker_config.triggers:
        if not workflow_node_is_enabled(trigger):
            continue
        materialized = materialize_node_definition(trigger)
        if (
            isinstance(materialized, PullRequestCompletedTriggerDefinition)
            and materialized.provider == "ado"
        ):
            triggers.append(materialized)
    return triggers


def _load_trigger_orchestrator_worker_config(
    config: WorkerConfig,
) -> TriggerOrchestratorWorkerGlobalConfig | None:
    config_path = _trigger_orchestrator_config_path(config)
    if not config_path.exists():
        return None
    return load_trigger_orchestrator_worker_global_config(
        config_path.parents[2],
        base_config=GatewayConfig(runtime_dir=config.runtime_dir),
    )


def _build_trigger_event_queue_for_worker_config(
    config: WorkerConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig | None,
):
    return SqliteTriggerEventQueue(
        (
            worker_config.orchestration_store_path
            if worker_config is not None
            else config.runtime_dir / "orchestration.db"
        ),
        retention_days=(
            worker_config.orchestration_retention_days if worker_config is not None else None
        ),
        compact_history_retention_days=(
            worker_config.orchestration_compact_history_retention_days
            if worker_config is not None
            else 365
        ),
    )


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _trigger_has_explicit_pr_criteria(
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
) -> bool:
    return _normalize_optional_text(trigger.repo) is not None


def _explicit_ado_trigger_pull_request_statuses(
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
) -> tuple[str | None, ...]:
    status = _normalize_optional_text(trigger.status)
    if status:
        return (status,)
    if trigger.type == "trigger:pull-request:completed":
        return _ADO_PR_COMPLETED_DEFAULT_STATUSES
    return (None,)


def _trigger_effective_directory_path(
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
    current_directory_path: str,
) -> str:
    return (
        _normalize_optional_text(trigger.directory_path)
        or DirectoryPath(segments=current_directory_path).as_posix()
    )


def _ado_organization_url_for_trigger(
    *,
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
    legacy_organization_url: str | None,
) -> str:
    return resolve_azure_devops_organization_url(
        ado_organization=trigger.ado_organization,
        legacy_organization_url=legacy_organization_url,
        node_type=trigger.type,
    )


def _list_explicit_ado_trigger_pull_requests(
    *,
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
    config: WorkerConfig,
    runtime_gateway_config: GatewayConfig,
    current_directory_path: str,
) -> _ExplicitAdoPrCandidates:
    normalized_current_directory_path = DirectoryPath(segments=current_directory_path).as_posix()
    trigger_directory_path = _normalize_optional_text(trigger.directory_path)
    if trigger_directory_path is not None:
        effective_directory_path = DirectoryPath(segments=trigger_directory_path).as_posix()
        if effective_directory_path != normalized_current_directory_path:
            _LOG.debug(
                "Skipping Azure PR polling for trigger_id=%s directory=%s current_directory=%s",
                trigger.id,
                effective_directory_path,
                normalized_current_directory_path,
            )
            return _ExplicitAdoPrCandidates(
                organization_url="",
                project="",
                pull_requests=[],
            )
    effective_directory_path = trigger_directory_path or normalized_current_directory_path
    directory_config = _resolve_directory_worker_config(
        config, runtime_gateway_config, effective_directory_path
    )
    organization_url = _ado_organization_url_for_trigger(
        trigger=trigger,
        legacy_organization_url=(
            _normalize_optional_text(directory_config.organization_url)
            or _normalize_optional_text(config.organization_url)
        ),
    )
    pat_env_name = runtime_gateway_config.azure_devops_pr_pat_env
    pat = os.environ.get(pat_env_name, "").strip()
    if not pat:
        _LOG.debug(
            "Skipping Azure PR polling for directory=%s because %s is unset",
            effective_directory_path,
            pat_env_name,
        )
        return _ExplicitAdoPrCandidates(
            organization_url="",
            project="",
            pull_requests=[],
        )

    repo = _normalize_optional_text(trigger.repo)
    if not repo:
        return _ExplicitAdoPrCandidates(
            organization_url="",
            project="",
            pull_requests=[],
        )

    project = (
        _normalize_optional_text(trigger.project)
        or _normalize_optional_text(directory_config.project)
        or _normalize_optional_text(config.project)
    )
    provider = AzureDevOpsPrQueryProvider(
        organization_url=organization_url,
        project=project or "",
        pat=pat,
        timeout_seconds=30.0,
    )
    pull_requests_by_key: dict[tuple[str, str, str], PullRequestItem] = {}
    for status in _explicit_ado_trigger_pull_request_statuses(trigger):
        response = provider.list_pull_requests(
            repo=repo,
            project=project,
            status=status,
            creator_id=trigger.creator_id,
            reviewer_id=trigger.reviewer_id,
            source_ref_name=trigger.source_ref_name,
            target_ref_name=trigger.target_ref_name,
            source_repository_id=trigger.source_repository_id,
            repository_id=trigger.repository_id,
            min_time=trigger.min_time,
            max_time=trigger.max_time,
            query_time_range_type=trigger.query_time_range_type,
            include_links=trigger.include_links,
            max_comment_length=trigger.max_comment_length,
            tags=trigger.tags,
            top=trigger.top,
            skip=trigger.skip,
            cursor=trigger.cursor,
        )
        for pull_request in response.pull_requests:
            pull_requests_by_key[(pull_request.provider, pull_request.repo, pull_request.pr_id)] = (
                pull_request
            )
    return _ExplicitAdoPrCandidates(
        organization_url=organization_url,
        project=project or "",
        pull_requests=list(pull_requests_by_key.values()),
    )


def _effect_lock_path_for_worker_config(
    config: WorkerConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig | None,
) -> Path:
    if worker_config is not None:
        return worker_config.effect_lock_path
    return config.runtime_dir / "effect-orchestrator.lock"


def _load_ado_pr_feedback_session_bindings(
    *,
    session_store_path: Path,
    pr_map_path: Path,
    directory_path: str,
) -> dict[tuple[str, str], dict[str, str]]:
    if not pr_map_path.exists():
        return {}
    try:
        payload = json.loads(pr_map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}

    session_directory_paths: dict[str, str] = {}
    session_vm_ids: dict[str, str] = {}
    session_repository = JsonSessionManifestRepository(session_store_path)
    session_ado_projects: dict[str, str] = {}
    for manifest in session_repository.list_sessions():
        normalized_session_id = str(manifest.session_id).strip()
        if not normalized_session_id:
            continue
        normalized_directory_path = str(manifest.directory_path or "root").strip() or "root"
        normalized_vm_id = str(manifest.vm_id or "").strip()
        session_directory_paths[normalized_session_id] = normalized_directory_path
        if normalized_vm_id:
            session_vm_ids[normalized_session_id] = normalized_vm_id
        metadata = getattr(manifest, "metadata", None)
        if isinstance(metadata, dict):
            normalized_ado_project = str(metadata.get("ado_project", "")).strip()
            if normalized_ado_project:
                session_ado_projects[normalized_session_id] = normalized_ado_project

    bindings: dict[tuple[str, str], dict[str, str]] = {}
    for raw in entries.values():
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider", "")).strip()
        repo = str(raw.get("repo", "")).strip()
        pr_id = str(raw.get("pr_id", "")).strip()
        session_id = str(raw.get("session_id", "")).strip()
        mapped_directory_path = (
            str(raw.get("directory_path", "")).strip()
            or session_directory_paths.get(session_id)
            or "root"
        )
        if provider != "azure_devops" or not repo or not pr_id:
            continue
        if directory_path and mapped_directory_path != directory_path:
            continue
        bindings[(repo, pr_id)] = {
            "provider": provider,
            "repo": repo,
            "pr_id": pr_id,
            "session_id": session_id,
            "vm_id": session_vm_ids.get(session_id, ""),
            "ado_project": session_ado_projects.get(session_id, ""),
        }
    return bindings


def _latest_cursor_value(*values: str | None) -> str | None:
    latest: datetime | None = None
    latest_raw: str | None = None
    for value in values:
        parsed = _parse_dt(value)
        if parsed is None:
            continue
        if latest is None or parsed > latest:
            latest = parsed
            latest_raw = value
    return latest_raw


def _ado_project_for_feedback_stream(
    *,
    repo: str,
    binding: dict[str, str],
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
    config: WorkerConfig,
) -> str:
    parts = [part.strip() for part in repo.split("/") if part.strip()]
    if len(parts) >= 3:
        return parts[-2]
    return binding.get("ado_project") or trigger.project or config.project


def _publish_directory_pr_feedback_events(
    *,
    client: httpx.Client,
    config: WorkerConfig,
    runtime_gateway_config: GatewayConfig,
    directory_path: str,
) -> int:
    triggers = _load_ado_pr_feedback_triggers(config)
    if not triggers:
        return 0

    legacy_bindings: dict[tuple[str, str], dict[str, str]] = {}
    if any(not _trigger_has_explicit_pr_criteria(trigger) for trigger in triggers):
        legacy_bindings = _load_ado_pr_feedback_session_bindings(
            session_store_path=runtime_gateway_config.session_store_path,
            pr_map_path=runtime_gateway_config.pr_map_path,
            directory_path=directory_path,
        )

    pat_env_name = runtime_gateway_config.azure_devops_pr_pat_env
    pat = os.environ.get(pat_env_name, "").strip()
    if not pat:
        _LOG.debug(
            "Skipping Azure PR feedback polling for directory=%s because %s is unset",
            directory_path,
            pat_env_name,
        )
        return 0

    cursor_store = TriggerCursorStore(config.runtime_dir / "trigger-cursors-pr-feedback.json")
    trigger_worker_config = _load_trigger_orchestrator_worker_config(config)
    event_queue = _build_trigger_event_queue_for_worker_config(config, trigger_worker_config)
    effect_wake_lock_path = _effect_lock_path_for_worker_config(config, trigger_worker_config)
    feedback_store = PrFeedbackStore(config.runtime_dir / "pr-feedback-store.json")
    audit_logger = AuditLogger(runtime_gateway_config.audit_log_path)
    ignored_feedback_ids = _read_ignored_feedback_ids(
        config.runtime_dir / "ignored-feedback-ids.json"
    )
    published_count = 0

    for trigger in triggers:
        if _trigger_has_explicit_pr_criteria(trigger):
            explicit_candidates = _list_explicit_ado_trigger_pull_requests(
                trigger=trigger,
                config=config,
                runtime_gateway_config=runtime_gateway_config,
                current_directory_path=directory_path,
            )
            streams = [
                (
                    {
                        "provider": candidate.provider,
                        "repo": candidate.repo,
                        "pr_id": candidate.pr_id,
                    },
                    {},
                    explicit_candidates.organization_url,
                    explicit_candidates.project,
                )
                for candidate in explicit_candidates.pull_requests
            ]
        else:
            organization_url = _ado_organization_url_for_trigger(
                trigger=trigger,
                legacy_organization_url=config.organization_url,
            )
            streams = [
                (
                    {"provider": "azure_devops", "repo": repo, "pr_id": pr_id},
                    legacy_bindings.get((repo, pr_id), {}),
                    organization_url,
                    _ado_project_for_feedback_stream(
                        repo=repo,
                        binding=legacy_bindings.get((repo, pr_id), {}),
                        trigger=trigger,
                        config=config,
                    ),
                )
                for repo, pr_id in sorted(legacy_bindings.keys())
            ]

        state = cursor_store.get(trigger.id)
        state_cursors = state.get("stream_cursors", {})
        if not isinstance(state_cursors, dict):
            state_cursors = {}
        previous_cursors = dict(state_cursors)
        next_cursors = dict(state_cursors)
        discovered: list[tuple[dict[str, str], dict[str, str], PrFeedbackItem]] = []

        for stream, binding, organization_url, project in streams:
            repo = stream["repo"]
            pr_id = stream["pr_id"]
            adapter = AzureDevOpsFeedbackAdapter(
                organization_url=organization_url,
                project=project,
                pat=pat,
                pat_env_name=pat_env_name,
            )
            stream_key = _stream_key("azure_devops", repo, pr_id)
            try:
                result = adapter.poll(
                    client=client,
                    repo=repo,
                    pr_id=pr_id,
                    cursor=previous_cursors.get(stream_key),
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 404:
                    _LOG.warning(
                        "Skipping Azure PR feedback stream after 404 "
                        "directory=%s trigger_id=%s repo=%s pr_id=%s project=%s",
                        directory_path,
                        trigger.id,
                        repo,
                        pr_id,
                        adapter.project,
                    )
                    continue
                raise
            for envelope in result.feedback:
                feedback_item = annotate_agent_directed_feedback(
                    PrFeedbackItem.model_validate(pr_feedback_item_payload(envelope)),
                    agent_author_names=trigger.agent_author_names,
                    agent_mention_names=trigger.agent_mention_names,
                    agent_author_ids=trigger.agent_author_ids,
                    agent_mention_ids=trigger.agent_mention_ids,
                )
                discovered.append(
                    (
                        {
                            "provider": feedback_item.provider,
                            "repo": feedback_item.repo,
                            "pr_id": feedback_item.pr_id,
                        },
                        binding,
                        feedback_item,
                    )
                )
            next_cursors[stream_key] = _latest_cursor_value(
                next_cursors.get(stream_key), result.next_cursor
            )

        discovered.sort(key=lambda item: item[2].created_at)
        for stream, binding, feedback in discovered:
            stream_key = _stream_key(stream["provider"], stream["repo"], stream["pr_id"])
            previous = _parse_dt(previous_cursors.get(stream_key))
            if previous is not None and feedback.created_at <= previous:
                continue

            stored = feedback_store.add_feedback_if_new(
                FeedbackEnvelope.model_validate(feedback.model_dump(mode="json"))
            )
            next_cursors[stream_key] = _latest_cursor_value(
                next_cursors.get(stream_key),
                feedback.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            )
            feedback_store.set_cursor(stream_key, next_cursors[stream_key])
            if feedback.feedback_id in ignored_feedback_ids:
                continue
            if trigger.suppress_agent_authored_feedback and bool(
                (feedback.metadata or {}).get("agent_directed_author_self")
            ):
                continue
            if stored:
                audit_logger.log_decision(
                    event_type=AuditEventType.FEEDBACK_DETECTED,
                    session_id=binding["session_id"]
                    if binding and binding.get("session_id")
                    else None,
                    vm_id=binding["vm_id"] if binding and binding.get("vm_id") else None,
                    provider=feedback.provider,
                    repo=feedback.repo,
                    pr_id=feedback.pr_id,
                    decision="detected",
                    reason="new-feedback",
                    args={
                        "feedback": feedback.model_dump(mode="json"),
                        "stream_bindings": [binding] if binding else [],
                    },
                )
            event = TriggerEvent(
                event_id=f"{trigger.id}:{stream['provider']}:{stream['repo']}:{stream['pr_id']}:{feedback.feedback_id}",
                trigger_id=trigger.id,
                trigger_type=trigger.type,
                occurred_at=feedback.created_at.astimezone(UTC),
                payload={
                    "pr": stream,
                    "feedback": feedback.model_dump(mode="json"),
                }
                | (
                    {"session_id": binding["session_id"]}
                    if binding and binding.get("session_id")
                    else {}
                ),
            )
            publish_result = event_queue.publish_result(
                event,
                directory_path=directory_path,
            )
            record = publish_result.record
            if publish_result.created and record.event_id == event.event_id:
                published_count += 1
                notify_wake_socket_for_lock(effect_wake_lock_path)
        cursor_store.put(trigger.id, {"stream_cursors": next_cursors})

    return published_count


def _publish_directory_pr_completion_events(
    *,
    client: httpx.Client,
    config: WorkerConfig,
    runtime_gateway_config: GatewayConfig,
    directory_path: str,
) -> int:
    _ = client
    triggers = _load_ado_pr_completed_triggers(config)
    if not triggers:
        return 0

    legacy_bindings: dict[tuple[str, str], dict[str, str]] = {}
    if any(not _trigger_has_explicit_pr_criteria(trigger) for trigger in triggers):
        legacy_bindings = _load_ado_pr_feedback_session_bindings(
            session_store_path=runtime_gateway_config.session_store_path,
            pr_map_path=runtime_gateway_config.pr_map_path,
            directory_path=directory_path,
        )

    pat_env_name = runtime_gateway_config.azure_devops_pr_pat_env
    pat = os.environ.get(pat_env_name, "").strip()
    if not pat:
        _LOG.debug(
            "Skipping Azure PR completion polling for directory=%s because %s is unset",
            directory_path,
            pat_env_name,
        )
        return 0

    trigger_worker_config = _load_trigger_orchestrator_worker_config(config)
    event_queue = _build_trigger_event_queue_for_worker_config(config, trigger_worker_config)
    effect_wake_lock_path = _effect_lock_path_for_worker_config(config, trigger_worker_config)
    pr_map_store = PrMapStore(runtime_gateway_config.pr_map_path)
    audit_logger = AuditLogger(runtime_gateway_config.audit_log_path)
    completion_cursor_store: TriggerCursorStore | None = None
    if any(_trigger_has_explicit_pr_criteria(trigger) for trigger in triggers):
        completion_cursor_store = TriggerCursorStore(
            config.runtime_dir / "trigger-cursors-pr-completed.json"
        )
    published_count = 0

    for trigger in triggers:
        explicit_pr_criteria = _trigger_has_explicit_pr_criteria(trigger)
        legacy_organization_url = ""
        completed_streams: dict[str, Any] = {}
        if explicit_pr_criteria and completion_cursor_store is not None:
            state = completion_cursor_store.get(trigger.id)
            cursor_streams = state.get("completed_streams", {}) if isinstance(state, dict) else {}
            if isinstance(cursor_streams, dict):
                completed_streams = dict(cursor_streams)

        if explicit_pr_criteria:
            explicit_candidates = _list_explicit_ado_trigger_pull_requests(
                trigger=trigger,
                config=config,
                runtime_gateway_config=runtime_gateway_config,
                current_directory_path=directory_path,
            )
            candidates = explicit_candidates.pull_requests
        else:
            explicit_candidates = None
            legacy_organization_url = _ado_organization_url_for_trigger(
                trigger=trigger,
                legacy_organization_url=config.organization_url,
            )
            candidates = [
                PullRequestItem(provider="azure_devops", repo=repo, pr_id=pr_id)
                for repo, pr_id in sorted(legacy_bindings.keys())
            ]

        for candidate in candidates:
            repo = candidate.repo
            pr_id = candidate.pr_id
            stream_key = _stream_key("azure_devops", repo, pr_id)
            if explicit_pr_criteria and stream_key in completed_streams:
                continue
            binding = {} if explicit_pr_criteria else legacy_bindings.get((repo, pr_id), {})
            provider = AzureDevOpsPrQueryProvider(
                organization_url=(
                    explicit_candidates.organization_url
                    if explicit_candidates is not None
                    else legacy_organization_url
                ),
                project=(
                    explicit_candidates.project
                    if explicit_candidates is not None
                    else _ado_project_for_feedback_stream(
                        repo=repo,
                        binding=binding,
                        trigger=trigger,
                        config=config,
                    )
                ),
                pat=pat,
                timeout_seconds=30.0,
            )
            try:
                response = provider.get_pull_request(
                    repo=repo,
                    pr_id=pr_id,
                    include_reviewers=False,
                    include_commits=False,
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 404:
                    _LOG.warning(
                        "Skipping Azure PR completion stream after 404 "
                        "directory=%s trigger_id=%s repo=%s pr_id=%s project=%s",
                        directory_path,
                        trigger.id,
                        repo,
                        pr_id,
                        provider.project,
                    )
                    continue
                raise
            snapshot = status_snapshot_from_pull_request(response, repo=repo, pr_id=pr_id)
            if not is_pr_completed(snapshot):
                continue
            if explicit_pr_criteria:
                event_entry = snapshot
                mark_published = False
            else:
                update = pr_map_store.update_status(
                    snapshot,
                    trigger_id=trigger.id,
                )
                if not update.completed_ready:
                    continue
                event_entry = update.entry
                mark_published = True

            event_id = completion_event_id(trigger.id, event_entry)
            event = TriggerEvent(
                event_id=event_id,
                trigger_id=trigger.id,
                trigger_type=trigger.type,
                occurred_at=_pr_completion_occurred_at(event_entry),
                payload=completion_event_payload(event_entry),
            )
            publish_result = event_queue.publish_result(
                event,
                directory_path=directory_path,
            )
            record = publish_result.record
            if record.event_id == event.event_id:
                if mark_published:
                    pr_map_store.mark_completion_event_published(
                        provider=event_entry["provider"],
                        repo=event_entry["repo"],
                        pr_id=event_entry["pr_id"],
                        trigger_id=trigger.id,
                        event_id=event.event_id,
                    )
                if explicit_pr_criteria:
                    completed_streams[stream_key] = event.event_id
                    completion_cursor_store.put(
                        trigger.id,
                        {"completed_streams": completed_streams},
                    )
                audit_logger.log_decision(
                    event_type=AuditEventType.PR_COMPLETION_DETECTED,
                    session_id=binding["session_id"]
                    if binding and binding.get("session_id")
                    else None,
                    vm_id=binding["vm_id"] if binding and binding.get("vm_id") else None,
                    provider=event_entry["provider"],
                    repo=event_entry["repo"],
                    pr_id=event_entry["pr_id"],
                    decision="detected",
                    reason="pr-completed",
                    args={"pr": completion_event_payload(event_entry)["pr"]},
                    result={"event_id": event.event_id},
                )
            if publish_result.created and record.event_id == event.event_id:
                published_count += 1
                notify_wake_socket_for_lock(effect_wake_lock_path)

    return published_count


def _pr_completion_occurred_at(entry: dict[str, Any]) -> datetime:
    parsed = _parse_dt(entry.get("completed_at")) or _parse_dt(entry.get("pr_status_updated_at"))
    return parsed or datetime.now(UTC)


def _read_ignored_feedback_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    normalized_payload, feedback_ids, changed = _normalize_ignored_feedback_payload(payload)
    if changed:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(normalized_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(path)
    return feedback_ids


def run_worker(config: WorkerConfig) -> None:
    if not config.directories:
        while True:
            logging.info("cycle skipped=no_directories")
            time.sleep(config.poll_interval_seconds)

    runtime_gateway_config = _worker_runtime_config(config)

    timeout = httpx.Timeout(connect=5.0, write=10.0, read=20.0, pool=5.0)
    with httpx.Client(timeout=timeout) as client:
        while True:
            cycle_start = time.monotonic()
            try:
                for directory_path in config.directories:
                    try:
                        _publish_directory_pr_feedback_events(
                            client=client,
                            config=config,
                            runtime_gateway_config=runtime_gateway_config,
                            directory_path=directory_path,
                        )
                        _publish_directory_pr_completion_events(
                            client=client,
                            config=config,
                            runtime_gateway_config=runtime_gateway_config,
                            directory_path=directory_path,
                        )
                    except httpx.HTTPError as exc:
                        logging.warning(
                            "cycle directory=%s error=%s",
                            directory_path,
                            exc.__class__.__name__,
                        )
                    except Exception:
                        logging.exception("cycle directory=%s error=unexpected", directory_path)
            except Exception:  # pragma: no cover
                logging.exception("cycle error=unexpected")

            elapsed = time.monotonic() - cycle_start
            sleep_seconds = max(0.0, config.poll_interval_seconds - elapsed)
            time.sleep(sleep_seconds)


def run_worker_command(
    *,
    log_level: str = "INFO",
) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = WorkerConfig.load()

    run_worker(config)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish Azure DevOps PR feedback/completion events."
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    args = parser.parse_args()

    run_worker_command(
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
