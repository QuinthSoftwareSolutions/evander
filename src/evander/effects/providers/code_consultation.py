"""Lifecycle provider for disposable codebase consultation workers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evander.code_consultation_service import (
    CodeConsultationService,
    CodeConsultationServiceFactoryPort,
    resolve_consultation_secret,
)

from evander.consultations import (
    CONSULTATION_RESULT_TOKEN_PATH,
    CONSULTATION_RESULT_TOOL,
    TERMINAL_CONSULTATION_STATUSES,
    CodeConsultationRecord,
    build_consultation_prompt,
    consultation_snapshot_ref,
    issue_consultation_token,
)
from evander.events.internal import InternalEventSource
from evander.orchestration.internal_events import build_internal_event_bus_from_metadata
from evander.events.provider_models import (
    AgentCloneRepoEffectDefinition,
    AgentCreateEffectDefinition,
    AgentDestroyEffectDefinition,
    CodeConsultationEffectDefinition,
)

from .agent_session import container_backend, effective_config_for_session
from .base import EffectProviderBase
from .consultation_worker import ConsultationWorkerLifecycle, NestedEffectConsultationWorker


class CodeConsultationEffectProvider(EffectProviderBase):
    """Prepare, finalize, or terminate one persisted consultation."""

    def __init__(
        self,
        *,
        worker_lifecycle: ConsultationWorkerLifecycle | None = None,
        service_factory: CodeConsultationServiceFactoryPort | None = None,
    ) -> None:
        super().__init__()
        self._worker_lifecycle = worker_lifecycle
        self._service_factory = service_factory
        self._consultation_service_instance: CodeConsultationService | None = None

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
        service = self._service()
        record = service.require_record(consultation_id)
        if record.status in TERMINAL_CONSULTATION_STATUSES:
            if record.cleanup_status != "completed":
                record = self._cleanup(service, record)
                service.release_capacity(record)
            raise RuntimeError(
                f"consultation {consultation_id} is already terminal ({record.status})"
            )
        if record.status == "running":
            return self._prepare_output(record)
        if record.status != "provisioning":
            raise ValueError(f"consultation {consultation_id} is {record.status}, not provisioning")

        try:
            profile = record.profile
            create_result = self._workers().create(
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
            clone_result = self._workers().clone(
                AgentCloneRepoEffectDefinition(
                    id=f"{self._effect().id}.clone",
                    display_name="Clone consultation repository",
                    type="effect:agent:clone-repo",
                    session_id=record.session_id,
                    managed_repo_id=record.managed_repo_id,
                    git_access_mode="read-only",
                    git_access_lifetime="operation",
                    pinned_ref=consultation_snapshot_ref(record.consultation_id),
                    pinned_commit_sha=record.commit_sha,
                ),
            )
            workspace_path = str(clone_result.get("target_dir") or "").strip()
            if not workspace_path:
                raise RuntimeError("consultation repository clone returned no target_dir")
            record = service.mark_running(
                record.consultation_id,
                workspace_path=workspace_path,
            )
            return self._prepare_output(record)
        except Exception as exc:
            record = service.fail(
                consultation_id,
                error=f"consultation preparation failed: {exc}",
            )
            try:
                record = self._cleanup(service, record)
            except Exception as cleanup_exc:
                raise RuntimeError(
                    f"consultation preparation failed ({exc}); cleanup also failed: {cleanup_exc}"
                ) from cleanup_exc
            service.release_capacity(record)
            raise

    def _finalize(
        self,
        consultation_id: str,
        *,
        outcome: str,
        reason: str,
    ) -> dict[str, Any]:
        service = self._service()
        record = service.finalize_worker(
            consultation_id,
            outcome=outcome,
            reason=reason,
        )
        record = self._cleanup(service, record)
        service.release_capacity(record)
        return self._output(record)

    def _terminate(
        self,
        consultation_id: str,
        *,
        status: str,
        reason: str,
    ) -> dict[str, Any]:
        service = self._service()
        previous, record = service.terminate_worker(
            consultation_id,
            status=status,
            reason=reason,
        )
        if previous.cleanup_status != "completed" and previous.started_at is not None:
            record = self._cleanup(service, record)
        elif record.cleanup_status != "completed":
            record = self._cleanup(service, record, destroy_worker=False)
        service.release_capacity(record)
        return self._output(record)

    def _cleanup(
        self,
        service: CodeConsultationService,
        record: CodeConsultationRecord,
        *,
        destroy_worker: bool = True,
    ) -> CodeConsultationRecord:
        if record.cleanup_status == "completed":
            return record
        try:
            if destroy_worker:
                result = self._workers().destroy(
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
            service.release_snapshot(record)
            return service.mark_cleanup(record.consultation_id, success=True)
        except Exception as exc:
            service.mark_cleanup(record.consultation_id, success=False, error=str(exc))
            raise RuntimeError(f"consultation cleanup failed: {exc}") from exc

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
        token = issue_consultation_token(
            secret=resolve_consultation_secret(session_config, self._context().env),
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

    def _workers(self) -> ConsultationWorkerLifecycle:
        if self._worker_lifecycle is None:
            self._worker_lifecycle = NestedEffectConsultationWorker(self._context())
        return self._worker_lifecycle

    def _service(self) -> CodeConsultationService:
        if self._consultation_service_instance is None:
            factory = self._service_factory or self._context().code_consultation_service_factory
            if factory is None:
                raise RuntimeError("code consultation application service is unavailable")
            event_bus = build_internal_event_bus_from_metadata(
                self._context().metadata,
                runtime_dir=self._runtime_dir(),
            )
            self._consultation_service_instance = factory.create(
                event_bus=event_bus,
                environment=self._context().env,
                requested_source=InternalEventSource(
                    source_type="workflow_effect",
                    source_name="effect:agent:code-consultation",
                    node_id=self._effect().id,
                    node_type=self._effect().type,
                ),
            )
        return self._consultation_service_instance

    def _runtime_dir(self) -> Path:
        value = getattr(self._context().resolved_config, "runtime_dir", None)
        if value is None:
            value = self._context().metadata.get("runtime_dir") or "runtime"
        return Path(value)

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
