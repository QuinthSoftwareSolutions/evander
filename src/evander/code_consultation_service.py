"""Application service for durable codebase consultations.

The service owns consultation persistence and dispatch transitions.  Transport adapters
remain responsible for authorization, auditing, and translating service errors into their
protocol-specific error type.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
import time
from typing import Any, Protocol
from uuid import uuid4

from evander.config import AgentConsultationProfile, GatewayConfig
from evander.consultations import (
    CONSULTATION_REQUESTED_EVENT_TYPE,
    CONSULTATION_TERMINATE_EVENT_TYPE,
    TERMINAL_CONSULTATION_STATUSES,
    CodeConsultationRecord,
    CodeConsultationResult,
    CodeConsultationStore,
    consultation_store_path,
    hash_consultation_request,
    hash_consultation_token,
    issue_consultation_token,
    utc_now,
    verify_consultation_token,
)
from evander.events.internal import InternalEventBus, InternalEventSource, internal_event_store_path
from evander.events.scheduled import (
    ScheduledInternalEventStore,
    scheduled_internal_event_store_path,
)


class ConsultationSnapshotPort(Protocol):
    """Managed-Git operations required by consultation orchestration."""

    def pin_snapshot(
        self,
        managed_repo_id: str,
        directory_path: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...

    def release_snapshot_pin(
        self,
        managed_repo_id: str,
        directory_path: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...

    def validate_snapshot_evidence(
        self,
        managed_repo_id: str,
        directory_path: str,
        **kwargs: Any,
    ) -> Sequence[Mapping[str, Any]]: ...


class CodeConsultationServiceFactoryPort(Protocol):
    """Build one request-scoped consultation application service."""

    def create(
        self,
        config: GatewayConfig | None = None,
        *,
        requested_source: InternalEventSource,
        cancellation_source: InternalEventSource | None = None,
        event_bus: InternalEventBus | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> "CodeConsultationService": ...


class CodeConsultationEventDispatcher:
    """Durable event adapter used by both gateway and effect execution."""

    def __init__(
        self,
        runtime_dir: str | Path,
        *,
        event_bus: InternalEventBus | None = None,
        scheduled_events: ScheduledInternalEventStore | None = None,
    ) -> None:
        self._event_bus = event_bus or InternalEventBus(internal_event_store_path(runtime_dir))
        self._scheduled_events = scheduled_events or ScheduledInternalEventStore(
            scheduled_internal_event_store_path(runtime_dir)
        )

    def publish_requested(
        self,
        records: Sequence[CodeConsultationRecord],
        *,
        source: InternalEventSource,
    ) -> None:
        for record in records:
            self._event_bus.publish(
                event_type=CONSULTATION_REQUESTED_EVENT_TYPE,
                payload={
                    "consultation_id": record.consultation_id,
                    "directory_path": record.directory_path,
                },
                correlation_key=record.consultation_id,
                idempotency_key=f"code-consultation:start:{record.consultation_id}",
                source=source,
            )

    def publish_termination(
        self,
        record: CodeConsultationRecord,
        *,
        reason: str,
        status: str,
        idempotency_key: str,
        source: InternalEventSource,
    ) -> None:
        self._event_bus.publish(
            event_type=CONSULTATION_TERMINATE_EVENT_TYPE,
            payload={
                "consultation_id": record.consultation_id,
                "reason": reason,
                "status": status,
            },
            correlation_key=record.consultation_id,
            idempotency_key=idempotency_key,
            source=source,
        )

    def schedule_timeout(
        self,
        record: CodeConsultationRecord,
        *,
        source: InternalEventSource,
    ) -> None:
        self._scheduled_events.schedule_event(
            event_type=CONSULTATION_TERMINATE_EVENT_TYPE,
            payload={
                "consultation_id": record.consultation_id,
                "reason": "consultation deadline exceeded",
                "status": "timed_out",
            },
            correlation_key=record.consultation_id,
            run_at=record.expires_at,
            idempotency_key=f"code-consultation:timeout:{record.consultation_id}",
            source=source,
        )


class BoundCodeConsultationServiceFactory:
    """Composition-root binding between consultations and managed-Git snapshots."""

    def __init__(
        self,
        snapshot_port_factory: Callable[[], ConsultationSnapshotPort],
        *,
        config: GatewayConfig | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._snapshot_port_factory = snapshot_port_factory
        self._config = config
        self._environment = environment

    def create(
        self,
        config: GatewayConfig | None = None,
        *,
        requested_source: InternalEventSource,
        cancellation_source: InternalEventSource | None = None,
        event_bus: InternalEventBus | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> "CodeConsultationService":
        resolved_config = config or self._config
        if resolved_config is None:
            raise RuntimeError("code consultation configuration is unavailable")
        return CodeConsultationService(
            resolved_config,
            snapshot_port_factory=self._snapshot_port_factory,
            environment=self._environment if environment is None else environment,
            dispatcher=CodeConsultationEventDispatcher(
                resolved_config.runtime_dir,
                event_bus=event_bus,
            ),
            requested_source=requested_source,
            cancellation_source=cancellation_source,
        )


def resolve_consultation_secret(
    config: GatewayConfig,
    environment: Mapping[str, str],
) -> str:
    """Resolve the configured consultation token secret from a host-owned environment."""

    env_name = str(
        getattr(config, "agent_consultation_token_secret_env", "") or "AGENT_PROMPT_TOKEN_SECRET"
    ).strip()
    secret = str(environment.get(env_name) or "").strip() if env_name else ""
    if not secret:
        raise ValueError(
            f"Agent consultation token secret is missing in environment variable {env_name!r}"
        )
    return secret


class CodeConsultationService:
    """Coordinate the durable lifecycle of request-scoped codebase consultations."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        snapshot_port_factory: Callable[[], ConsultationSnapshotPort] | None = None,
        environment: Mapping[str, str] | None = None,
        store: CodeConsultationStore | None = None,
        dispatcher: CodeConsultationEventDispatcher | None = None,
        requested_source: InternalEventSource | None = None,
        cancellation_source: InternalEventSource | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._snapshot_port_factory = snapshot_port_factory
        self._snapshot_port: ConsultationSnapshotPort | None = None
        self._environment = environment or {}
        self._store = store or CodeConsultationStore(consultation_store_path(config.runtime_dir))
        self._dispatcher = dispatcher or CodeConsultationEventDispatcher(config.runtime_dir)
        self._requested_source = requested_source or InternalEventSource(
            source_type="application_service",
            source_name="code_consultation",
        )
        self._cancellation_source = cancellation_source or self._requested_source
        self._sleep = sleeper

    def create(
        self,
        *,
        source_session_id: str,
        directory_path: str,
        managed_repo_id: str,
        query: str,
        source_ref: str | None = None,
        commit_sha: str | None = None,
        profile: str | None = None,
        wait_seconds: float = 30.0,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("query is required")
        if len(normalized_query) > 50_000:
            raise ValueError("query exceeds the 50000 character limit")
        profile_name, selected_profile = self._profile(profile)
        normalized_repo_id = str(managed_repo_id or "").strip()
        request_hash = hash_consultation_request(
            managed_repo_id=normalized_repo_id,
            source_ref=source_ref,
            commit_sha=commit_sha,
            profile_name=profile_name,
            query=normalized_query,
        )
        self._store.compact(
            before=utc_now() - timedelta(days=self.config.agent_consultation_retention_days)
        )
        record = self._store.get_by_idempotency_key(
            source_session_id=source_session_id,
            idempotency_key=idempotency_key,
        )
        created = False
        if record is not None:
            if record.request_hash != request_hash:
                raise ValueError(
                    "idempotency_key is already bound to a different consultation request"
                )
        else:
            record, created = self._create_record(
                source_session_id=source_session_id,
                directory_path=directory_path,
                managed_repo_id=normalized_repo_id,
                source_ref=source_ref,
                commit_sha=commit_sha,
                profile_name=profile_name,
                selected_profile=selected_profile,
                query=normalized_query,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        if record.status not in TERMINAL_CONSULTATION_STATUSES:
            self._set_up_dispatch(record)
        waited = self._wait_for_record(
            record.consultation_id,
            wait_seconds=self._bounded_wait(wait_seconds),
        )
        if waited is None:
            raise RuntimeError("consultation disappeared from the runtime store")
        return {**waited.public_payload(), "created": created}

    def get(
        self,
        *,
        source_session_id: str,
        consultation_id: str,
        wait_seconds: float = 0.0,
    ) -> dict[str, Any]:
        record = self._owned_source_record(consultation_id, source_session_id)
        self._ensure_dispatched(record)
        waited = self._wait_for_record(
            record.consultation_id,
            wait_seconds=self._bounded_wait(wait_seconds),
        )
        if waited is None:
            raise RuntimeError("consultation disappeared from the runtime store")
        return waited.public_payload()

    def cancel(
        self,
        *,
        source_session_id: str,
        consultation_id: str,
    ) -> dict[str, Any]:
        record = self._owned_source_record(consultation_id, source_session_id)
        requested = False
        if record.status == "queued":
            record = self._store.terminate(
                record.consultation_id,
                status="cancelled",
                error="cancelled by the requesting session",
            )
        needs_cleanup_event = record.status not in TERMINAL_CONSULTATION_STATUSES or (
            record.status == "cancelled" and record.cleanup_status != "completed"
        )
        if needs_cleanup_event:
            self._dispatcher.publish_termination(
                record,
                reason="cancelled by the requesting session",
                status="cancelled",
                idempotency_key=f"code-consultation:cancel:{record.consultation_id}",
                source=self._cancellation_source,
            )
            requested = True
        return {
            **record.public_payload(),
            "cancellation_requested": requested,
        }

    def submit(
        self,
        *,
        worker_session_id: str,
        consultation_id: str,
        result_token: str,
        answer: str,
        evidence: list[dict[str, Any]],
        confidence: float,
        limitations: list[str] | None = None,
    ) -> dict[str, Any]:
        record = self._owned_worker_record(consultation_id, worker_session_id)
        verify_consultation_token(
            result_token,
            secret=resolve_consultation_secret(self.config, self._environment),
            consultation_id=record.consultation_id,
            session_id=worker_session_id,
        )
        result = CodeConsultationResult(
            answer=answer,
            evidence=evidence,
            confidence=confidence,
            limitations=limitations or [],
        )
        validated_evidence = self._snapshots().validate_snapshot_evidence(
            record.managed_repo_id,
            record.directory_path,
            commit_sha=record.commit_sha,
            evidence=[item.model_dump(mode="python") for item in result.evidence],
        )
        result = CodeConsultationResult.model_validate(
            {
                **result.model_dump(mode="python"),
                "evidence": validated_evidence,
            }
        )
        record = self._store.submit_result(
            record.consultation_id,
            session_id=worker_session_id,
            token_hash=hash_consultation_token(result_token),
            result=result,
        )
        return {
            "accepted": True,
            "consultation_id": record.consultation_id,
            "status": record.status,
            "commit_sha": record.commit_sha,
        }

    def require_record(self, consultation_id: str) -> CodeConsultationRecord:
        record = self._store.get(str(consultation_id or "").strip())
        if record is None:
            raise ValueError(f"Unknown consultation_id: {consultation_id}")
        return record

    def mark_running(
        self,
        consultation_id: str,
        *,
        workspace_path: str,
    ) -> CodeConsultationRecord:
        return self._store.mark_running(consultation_id, workspace_path=workspace_path)

    def fail(self, consultation_id: str, *, error: str) -> CodeConsultationRecord:
        return self._store.fail(consultation_id, error=error)

    def finalize_worker(
        self,
        consultation_id: str,
        *,
        outcome: str,
        reason: str,
    ) -> CodeConsultationRecord:
        record = self.require_record(consultation_id)
        if record.status not in TERMINAL_CONSULTATION_STATUSES:
            error = (
                "consultation worker exited without submitting a structured result"
                if outcome == "completed"
                else reason or "consultation worker execution failed"
            )
            record = self._store.fail(consultation_id, error=error)
        return record

    def terminate_worker(
        self,
        consultation_id: str,
        *,
        status: str,
        reason: str,
    ) -> tuple[CodeConsultationRecord, CodeConsultationRecord]:
        if status not in {"timed_out", "cancelled"}:
            raise ValueError("consultation termination status must be timed_out or cancelled")
        previous = self.require_record(consultation_id)
        current = self._store.terminate(consultation_id, status=status, error=reason)
        return previous, current

    def mark_cleanup(
        self,
        consultation_id: str,
        *,
        success: bool,
        error: str | None = None,
    ) -> CodeConsultationRecord:
        return self._store.mark_cleanup(consultation_id, success=success, error=error)

    def release_snapshot(self, record: CodeConsultationRecord) -> None:
        self._snapshots().release_snapshot_pin(
            record.managed_repo_id,
            record.directory_path,
            consultation_id=record.consultation_id,
            expected_commit_sha=record.commit_sha,
        )

    def release_capacity(self, record: CodeConsultationRecord) -> None:
        if record.cleanup_status != "completed":
            return
        claimed = self._store.claim_available(
            directory_path=record.directory_path,
            profile_name=record.profile_name,
        )
        if claimed:
            self._dispatcher.publish_requested(claimed, source=self._requested_source)

    def _create_record(
        self,
        *,
        source_session_id: str,
        directory_path: str,
        managed_repo_id: str,
        source_ref: str | None,
        commit_sha: str | None,
        profile_name: str,
        selected_profile: AgentConsultationProfile,
        query: str,
        idempotency_key: str | None,
        request_hash: str,
    ) -> tuple[CodeConsultationRecord, bool]:
        consultation_id = f"consult-{uuid4().hex}"
        worker_session_id = f"consult-{uuid4().hex[:24]}"
        expires_at = utc_now() + timedelta(seconds=selected_profile.timeout_seconds)
        token = issue_consultation_token(
            secret=resolve_consultation_secret(self.config, self._environment),
            consultation_id=consultation_id,
            session_id=worker_session_id,
            expires_at=expires_at,
        )
        snapshots = self._snapshots()
        snapshot = snapshots.pin_snapshot(
            managed_repo_id,
            directory_path,
            consultation_id=consultation_id,
            source_ref=source_ref,
            commit_sha=commit_sha,
        )
        try:
            record, created = self._store.create(
                consultation_id=consultation_id,
                session_id=worker_session_id,
                source_session_id=source_session_id,
                directory_path=directory_path,
                managed_repo_id=str(snapshot["managed_repo_id"]),
                source_ref=snapshot.get("source_ref"),
                commit_sha=str(snapshot["commit_sha"]),
                profile_name=profile_name,
                profile=selected_profile.model_dump(mode="json"),
                query=query,
                idempotency_key=idempotency_key,
                max_concurrency=selected_profile.max_concurrency,
                expires_at=expires_at,
                token_hash=hash_consultation_token(token),
                request_hash=request_hash,
            )
        except Exception:
            snapshots.release_snapshot_pin(
                str(snapshot["managed_repo_id"]),
                directory_path,
                consultation_id=consultation_id,
                expected_commit_sha=str(snapshot["commit_sha"]),
            )
            raise
        if not created:
            snapshots.release_snapshot_pin(
                str(snapshot["managed_repo_id"]),
                directory_path,
                consultation_id=consultation_id,
                expected_commit_sha=str(snapshot["commit_sha"]),
            )
        return record, created

    def _set_up_dispatch(self, record: CodeConsultationRecord) -> None:
        try:
            self._snapshots().pin_snapshot(
                record.managed_repo_id,
                record.directory_path,
                consultation_id=record.consultation_id,
                commit_sha=record.commit_sha,
            )
            self._dispatcher.schedule_timeout(record, source=self._requested_source)
            self._ensure_dispatched(record)
        except Exception as setup_error:
            try:
                self._cleanup_failed_dispatch_setup(record.consultation_id, error=setup_error)
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"{setup_error}; consultation cleanup setup also failed: {cleanup_error}"
                ) from cleanup_error
            raise

    def _ensure_dispatched(self, record: CodeConsultationRecord) -> None:
        if record.status == "queued":
            claimed = self._store.claim_available(
                directory_path=record.directory_path,
                profile_name=record.profile_name,
            )
            self._dispatcher.publish_requested(claimed, source=self._requested_source)
        elif record.status == "provisioning":
            # Publishing is idempotent and heals a process failure after the SQLite claim.
            self._dispatcher.publish_requested([record], source=self._requested_source)

    def _cleanup_failed_dispatch_setup(
        self,
        consultation_id: str,
        *,
        error: Exception,
    ) -> None:
        reason = f"consultation dispatch setup failed: {error}"
        record = self._store.fail(consultation_id, error=reason)
        if record.started_at is None:
            try:
                self.release_snapshot(record)
            except Exception as cleanup_error:
                self._store.mark_cleanup(
                    record.consultation_id,
                    success=False,
                    error=str(cleanup_error),
                )
                try:
                    self._publish_setup_cleanup(record, reason=reason)
                except Exception as dispatch_error:
                    raise RuntimeError(
                        f"snapshot release failed ({cleanup_error}); cleanup dispatch failed: "
                        f"{dispatch_error}"
                    ) from dispatch_error
                return
            self._store.mark_cleanup(record.consultation_id, success=True)
            return
        try:
            self._publish_setup_cleanup(record, reason=reason)
        except Exception as cleanup_error:
            self._store.mark_cleanup(
                record.consultation_id,
                success=False,
                error=f"cleanup dispatch failed: {cleanup_error}",
            )
            raise

    def _publish_setup_cleanup(self, record: CodeConsultationRecord, *, reason: str) -> None:
        self._dispatcher.publish_termination(
            record,
            reason=reason,
            status="cancelled",
            idempotency_key=f"code-consultation:setup-cleanup:{record.consultation_id}",
            source=self._requested_source,
        )

    def _wait_for_record(
        self,
        consultation_id: str,
        *,
        wait_seconds: float,
    ) -> CodeConsultationRecord | None:
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            record = self._store.get(consultation_id)
            if record is None or record.status in TERMINAL_CONSULTATION_STATUSES:
                return record
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return record
            self._sleep(min(0.2, remaining))

    def _profile(
        self,
        requested_name: str | None,
    ) -> tuple[str, AgentConsultationProfile]:
        name = str(requested_name or self.config.agent_consultation_default_profile or "").strip()
        if not name:
            raise ValueError(
                "profile is required because agent_consultation_default_profile is not configured"
            )
        profile = self.config.agent_consultation_profiles.get(name)
        if profile is None:
            available = ", ".join(sorted(self.config.agent_consultation_profiles)) or "none"
            raise ValueError(f"Unknown agent consultation profile {name!r}; available: {available}")
        return name, profile

    def _bounded_wait(self, wait_seconds: float) -> float:
        return min(
            max(0.0, float(wait_seconds)),
            float(self.config.agent_consultation_max_wait_seconds),
        )

    def _owned_source_record(
        self,
        consultation_id: str,
        source_session_id: str,
    ) -> CodeConsultationRecord:
        record = self._store.get(str(consultation_id or "").strip())
        if record is None or record.source_session_id != source_session_id:
            raise ValueError(f"Unknown consultation_id: {consultation_id}")
        return record

    def _owned_worker_record(
        self,
        consultation_id: str,
        worker_session_id: str,
    ) -> CodeConsultationRecord:
        record = self._store.get(str(consultation_id or "").strip())
        if record is None or record.session_id != worker_session_id:
            raise ValueError(f"Unknown consultation_id: {consultation_id}")
        return record

    def _snapshots(self) -> ConsultationSnapshotPort:
        if self._snapshot_port is None:
            if self._snapshot_port_factory is None:
                raise RuntimeError("managed Git consultation snapshot operations are unavailable")
            self._snapshot_port = self._snapshot_port_factory()
        return self._snapshot_port


__all__ = [
    "BoundCodeConsultationServiceFactory",
    "CodeConsultationEventDispatcher",
    "CodeConsultationService",
    "CodeConsultationServiceFactoryPort",
    "ConsultationSnapshotPort",
    "resolve_consultation_secret",
]
