"""Lifecycle provider for disposable codebase consultation workers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import shlex
from typing import Any

from evander.consultations import (
    CONSULTATION_REQUESTED_EVENT_TYPE,
    CONSULTATION_RESULT_TOKEN_PATH,
    CONSULTATION_RESULT_TOOL,
    TERMINAL_CONSULTATION_STATUSES,
    CodeConsultationRecord,
    CodeConsultationStore,
    build_consultation_prompt,
    consultation_snapshot_ref,
    consultation_store_path,
    issue_consultation_token,
)
from evander.events.internal import InternalEventBus, InternalEventSource, internal_event_store_path
from evander.events.provider_models import (
    AgentCloneRepoEffectDefinition,
    AgentCreateEffectDefinition,
    AgentDestroyEffectDefinition,
    AgentExecEffectDefinition,
    CodeConsultationEffectDefinition,
)

from .agent_clone_repo import AgentCloneRepoEffectProvider
from .agent_create import AgentCreateEffectProvider
from .agent_destroy import AgentDestroyEffectProvider
from .agent_exec import AgentExecEffectProvider
from .agent_session import container_backend, effective_config_for_session
from .base import EffectProviderBase


class CodeConsultationEffectProvider(EffectProviderBase):
    """Prepare, finalize, or terminate one persisted consultation."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        payload = {
            "consultation_id": effect.consultation_id,
            "operation": effect.operation,
            "outcome": effect.outcome,
            "status": effect.status,
            "reason": effect.reason,
        }
        if resolved_config:
            payload.update(dict(resolved_config))
        consultation_id = str(payload.get("consultation_id") or "").strip()
        operation = str(payload.get("operation") or "").strip().casefold()
        if not consultation_id:
            raise ValueError("effect:agent:code-consultation requires consultation_id")
        if operation == "prepare":
            return self._prepare(consultation_id)
        if operation == "finalize":
            return self._finalize(
                consultation_id,
                outcome=str(payload.get("outcome") or "completed").strip().casefold(),
                reason=str(payload.get("reason") or "").strip(),
            )
        if operation == "terminate":
            return self._terminate(
                consultation_id,
                status=str(payload.get("status") or "timed_out").strip().casefold(),
                reason=str(payload.get("reason") or "consultation terminated").strip(),
            )
        raise ValueError(
            "effect:agent:code-consultation operation must be prepare, finalize, or terminate"
        )

    def _prepare(self, consultation_id: str) -> dict[str, Any]:
        store = self._store()
        record = self._require_record(store, consultation_id)
        if record.status in TERMINAL_CONSULTATION_STATUSES:
            if record.cleanup_status != "completed":
                record = self._cleanup(store, record)
                self._release_capacity(store, record)
            raise RuntimeError(
                f"consultation {consultation_id} is already terminal ({record.status})"
            )
        if record.status == "running":
            return self._prepare_output(record)
        if record.status != "provisioning":
            raise ValueError(f"consultation {consultation_id} is {record.status}, not provisioning")

        try:
            profile = record.profile
            create_result = self._execute_nested(
                AgentCreateEffectProvider,
                AgentCreateEffectDefinition(
                    id=f"{self._effect().id}.create",
                    display_name="Create consultation worker",
                    type="effect:agent:create",
                    execution_backend="container_worker_vm",
                    session_id=record.session_id,
                    existing_agent_behavior="noop",
                    agent_provider=profile.get("agent_provider"),
                    model=profile.get("model"),
                    model_provider_mode=profile.get("model_provider_mode"),
                    container_image=profile.get("container_image"),
                    allowed_tools=[CONSULTATION_RESULT_TOOL],
                    ttl_hours=1,
                    web_search_enabled=False,
                    directory_path=record.directory_path,
                    metadata={
                        "agent_consultation": {
                            "consultation_id": record.consultation_id,
                            "managed_repo_id": record.managed_repo_id,
                            "commit_sha": record.commit_sha,
                            "request_scoped": True,
                        }
                    },
                ),
            )
            if not bool(create_result.get("success")):
                raise RuntimeError(
                    str(create_result.get("message") or "consultation worker creation failed")
                )
            self._write_result_token(record)
            clone_result = self._execute_nested(
                AgentCloneRepoEffectProvider,
                AgentCloneRepoEffectDefinition(
                    id=f"{self._effect().id}.clone",
                    display_name="Clone consultation repository",
                    type="effect:agent:clone-repo",
                    session_id=record.session_id,
                    managed_repo_id=record.managed_repo_id,
                    git_access_mode="read-only",
                ),
            )
            workspace_path = str(clone_result.get("target_dir") or "").strip()
            if not workspace_path:
                raise RuntimeError("consultation repository clone returned no target_dir")
            commit = shlex.quote(record.commit_sha)
            snapshot_ref = shlex.quote(consultation_snapshot_ref(record.consultation_id))
            self._execute_nested(
                AgentExecEffectProvider,
                AgentExecEffectDefinition(
                    id=f"{self._effect().id}.checkout",
                    display_name="Pin consultation repository snapshot",
                    type="effect:agent:exec",
                    session_id=record.session_id,
                    working_directory=workspace_path,
                    command=(
                        f"git fetch --no-tags origin {snapshot_ref} && "
                        f'test "$(git rev-parse FETCH_HEAD)" = {commit} && '
                        "git checkout --detach FETCH_HEAD && "
                        f'test "$(git rev-parse HEAD)" = {commit} && '
                        "git status --short"
                    ),
                    timeout_seconds=120,
                    check=True,
                ),
            )
            record = store.mark_running(
                record.consultation_id,
                workspace_path=workspace_path,
            )
            return self._prepare_output(record)
        except Exception as exc:
            record = store.fail(
                consultation_id,
                error=f"consultation preparation failed: {exc}",
            )
            try:
                record = self._cleanup(store, record)
            except Exception as cleanup_exc:
                raise RuntimeError(
                    f"consultation preparation failed ({exc}); cleanup also failed: {cleanup_exc}"
                ) from cleanup_exc
            self._release_capacity(store, record)
            raise

    def _finalize(
        self,
        consultation_id: str,
        *,
        outcome: str,
        reason: str,
    ) -> dict[str, Any]:
        store = self._store()
        record = self._require_record(store, consultation_id)
        if record.status not in TERMINAL_CONSULTATION_STATUSES:
            if outcome == "completed":
                record = store.fail(
                    consultation_id,
                    error="consultation worker exited without submitting a structured result",
                )
            else:
                record = store.fail(
                    consultation_id,
                    error=reason or "consultation worker execution failed",
                )
        record = self._cleanup(store, record)
        self._release_capacity(store, record)
        return self._output(record)

    def _terminate(
        self,
        consultation_id: str,
        *,
        status: str,
        reason: str,
    ) -> dict[str, Any]:
        if status not in {"timed_out", "cancelled"}:
            raise ValueError("consultation termination status must be timed_out or cancelled")
        store = self._store()
        previous = self._require_record(store, consultation_id)
        record = store.terminate(consultation_id, status=status, error=reason)
        if previous.cleanup_status != "completed" and previous.started_at is not None:
            record = self._cleanup(store, record)
        elif record.cleanup_status != "completed":
            record = self._cleanup(store, record, destroy_worker=False)
        self._release_capacity(store, record)
        return self._output(record)

    def _cleanup(
        self,
        store: CodeConsultationStore,
        record: CodeConsultationRecord,
        *,
        destroy_worker: bool = True,
    ) -> CodeConsultationRecord:
        if record.cleanup_status == "completed":
            return record
        try:
            if destroy_worker:
                result = self._execute_nested(
                    AgentDestroyEffectProvider,
                    AgentDestroyEffectDefinition(
                        id=f"{self._effect().id}.destroy",
                        display_name="Destroy consultation worker",
                        type="effect:agent:destroy",
                        session_id=record.session_id,
                        execution_backend="container_worker_vm",
                        directory_path=record.directory_path,
                    ),
                )
                if not bool(result.get("success")):
                    raise RuntimeError(str(result.get("message") or "worker cleanup failed"))
            self._release_snapshot_pin(record)
            return store.mark_cleanup(record.consultation_id, success=True)
        except Exception as exc:
            store.mark_cleanup(record.consultation_id, success=False, error=str(exc))
            raise RuntimeError(f"consultation cleanup failed: {exc}") from exc

    def _release_snapshot_pin(self, record: CodeConsultationRecord) -> None:
        managed_git = getattr(self._context().admin_service, "managed_git_admin", None)
        release_pin = getattr(managed_git, "release_snapshot_pin", None)
        if not callable(release_pin):
            raise RuntimeError("managed Git consultation snapshot release is unavailable")
        release_pin(
            record.managed_repo_id,
            record.directory_path,
            consultation_id=record.consultation_id,
            expected_commit_sha=record.commit_sha,
        )

    def _release_capacity(
        self,
        store: CodeConsultationStore,
        record: CodeConsultationRecord,
    ) -> None:
        if record.cleanup_status != "completed":
            return
        claimed = store.claim_available(
            directory_path=record.directory_path,
            profile_name=record.profile_name,
        )
        if not claimed:
            return
        configured_path = self._context().metadata.get("internal_event_store_path")
        bus = InternalEventBus(
            Path(configured_path)
            if configured_path
            else internal_event_store_path(self._runtime_dir())
        )
        for next_record in claimed:
            bus.publish(
                event_type=CONSULTATION_REQUESTED_EVENT_TYPE,
                payload={
                    "consultation_id": next_record.consultation_id,
                    "directory_path": next_record.directory_path,
                },
                correlation_key=next_record.consultation_id,
                idempotency_key=f"code-consultation:start:{next_record.consultation_id}",
                source=InternalEventSource(
                    source_type="workflow_effect",
                    source_name="effect:agent:code-consultation",
                    node_id=self._effect().id,
                    node_type=self._effect().type,
                ),
            )

    def _prepare_output(self, record: CodeConsultationRecord) -> dict[str, Any]:
        return {
            **self._output(record),
            "session_id": record.session_id,
            "workspace_path": record.workspace_path,
            "prompt": build_consultation_prompt(record),
            "model": record.profile.get("model"),
        }

    def _write_result_token(self, record: CodeConsultationRecord) -> None:
        session_config = effective_config_for_session(
            self._context(),
            record.session_id,
            fallback_directory_path=record.directory_path,
        )
        env_name = str(
            getattr(session_config, "agent_consultation_token_secret_env", "")
            or "AGENT_PROMPT_TOKEN_SECRET"
        ).strip()
        secret = str(self._context().env.get(env_name) or "").strip()
        if not secret:
            raise RuntimeError(
                f"Agent consultation token secret is missing in environment variable {env_name!r}"
            )
        token = issue_consultation_token(
            secret=secret,
            consultation_id=record.consultation_id,
            session_id=record.session_id,
            expires_at=record.expires_at,
        )
        container_backend(self._context(), session_config).write_files(
            session_id=record.session_id,
            files=[
                {
                    "destination_path": CONSULTATION_RESULT_TOKEN_PATH,
                    "content": token,
                    "mode": "0600",
                }
            ],
        )

    @staticmethod
    def _output(record: CodeConsultationRecord, *, skipped: bool = False) -> dict[str, Any]:
        return {
            "success": record.status not in {"failed", "timed_out", "cancelled"},
            "consultation_id": record.consultation_id,
            "status": record.status,
            "session_id": record.session_id,
            "workspace_path": record.workspace_path,
            "commit_sha": record.commit_sha,
            "cleanup_status": record.cleanup_status,
            "skipped": skipped,
        }

    def _execute_nested(self, provider_type: type[Any], definition: Any) -> dict[str, Any]:
        provider = provider_type()
        provider.initialize(definition, self._context())
        return provider.execute()

    def _store(self) -> CodeConsultationStore:
        return CodeConsultationStore(consultation_store_path(self._runtime_dir()))

    def _runtime_dir(self) -> Path:
        value = getattr(self._context().resolved_config, "runtime_dir", None)
        if value is None:
            value = self._context().metadata.get("runtime_dir") or "runtime"
        return Path(value)

    @staticmethod
    def _require_record(
        store: CodeConsultationStore,
        consultation_id: str,
    ) -> CodeConsultationRecord:
        record = store.get(consultation_id)
        if record is None:
            raise ValueError(f"Unknown consultation_id: {consultation_id}")
        return record

    def _effect(self) -> CodeConsultationEffectDefinition:
        if not isinstance(self.effect_definition, CodeConsultationEffectDefinition):
            raise RuntimeError(
                "CodeConsultationEffectProvider initialized with an incompatible definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("CodeConsultationEffectProvider has no runtime context")
        return self.runtime_context


__all__ = ["CodeConsultationEffectProvider"]
