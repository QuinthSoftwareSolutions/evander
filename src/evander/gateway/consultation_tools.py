"""Request-scoped codebase consultation tools for the MCP gateway."""

from __future__ import annotations

from datetime import timedelta
import os
import time
from typing import Any, Callable
from uuid import uuid4

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from evander.config import AgentConsultationProfile, GatewayConfig
from evander.consultations import (
    CONSULTATION_REQUESTED_EVENT_TYPE,
    CONSULTATION_TERMINATE_EVENT_TYPE,
    TERMINAL_CONSULTATION_STATUSES,
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
from evander.gateway.dependencies import GatewayToolDependencies
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import ToolName
from evander.policy import PolicyError
from evander.runtime import RuntimeContext


def _consultation_secret(config: GatewayConfig) -> str:
    env_name = str(config.agent_consultation_token_secret_env or "").strip()
    secret = os.environ.get(env_name, "").strip() if env_name else ""
    if not secret:
        raise ToolError(
            f"Agent consultation token secret is missing in environment variable {env_name!r}"
        )
    return secret


def _profile(
    config: GatewayConfig,
    requested_name: str | None,
) -> tuple[str, AgentConsultationProfile]:
    name = str(requested_name or config.agent_consultation_default_profile or "").strip()
    if not name:
        raise ToolError(
            "profile is required because agent_consultation_default_profile is not configured"
        )
    profile = config.agent_consultation_profiles.get(name)
    if profile is None:
        available = ", ".join(sorted(config.agent_consultation_profiles)) or "none"
        raise ToolError(f"Unknown agent consultation profile {name!r}; available: {available}")
    return name, profile


def _publish_claimed(config: GatewayConfig, records: list[Any]) -> None:
    if not records:
        return
    bus = InternalEventBus(internal_event_store_path(config.runtime_dir))
    for record in records:
        bus.publish(
            event_type=CONSULTATION_REQUESTED_EVENT_TYPE,
            payload={
                "consultation_id": record.consultation_id,
                "directory_path": record.directory_path,
            },
            correlation_key=record.consultation_id,
            idempotency_key=f"code-consultation:start:{record.consultation_id}",
            source=InternalEventSource(
                source_type="mcp_tool",
                source_name=ToolName.CONSULT_CODEBASE.value,
            ),
        )


def _schedule_timeout(config: GatewayConfig, consultation_id: str, expires_at: Any) -> None:
    ScheduledInternalEventStore(
        scheduled_internal_event_store_path(config.runtime_dir)
    ).schedule_event(
        event_type=CONSULTATION_TERMINATE_EVENT_TYPE,
        payload={
            "consultation_id": consultation_id,
            "reason": "consultation deadline exceeded",
            "status": "timed_out",
        },
        correlation_key=consultation_id,
        run_at=expires_at,
        idempotency_key=f"code-consultation:timeout:{consultation_id}",
        source=InternalEventSource(
            source_type="mcp_tool",
            source_name=ToolName.CONSULT_CODEBASE.value,
        ),
    )


def _ensure_dispatched(
    config: GatewayConfig,
    store: CodeConsultationStore,
    record: Any,
) -> None:
    if record.status == "queued":
        _publish_claimed(
            config,
            store.claim_available(
                directory_path=record.directory_path,
                profile_name=record.profile_name,
            ),
        )
    elif record.status == "provisioning":
        # Publishing is idempotent and heals a gateway failure after the SQLite claim.
        _publish_claimed(config, [record])


def _cleanup_failed_dispatch_setup(
    config: GatewayConfig,
    admin: Any,
    store: CodeConsultationStore,
    consultation_id: str,
    *,
    error: Exception,
) -> None:
    reason = f"consultation dispatch setup failed: {error}"
    record = store.fail(consultation_id, error=reason)
    if record.started_at is None:
        try:
            admin.managed_git_admin.release_snapshot_pin(
                record.managed_repo_id,
                record.directory_path,
                consultation_id=record.consultation_id,
                expected_commit_sha=record.commit_sha,
            )
        except Exception as cleanup_exc:
            store.mark_cleanup(
                record.consultation_id,
                success=False,
                error=str(cleanup_exc),
            )
            try:
                _publish_failed_dispatch_cleanup(config, record, reason=reason)
            except Exception as cleanup_dispatch_exc:
                raise RuntimeError(
                    f"snapshot release failed ({cleanup_exc}); cleanup dispatch failed: "
                    f"{cleanup_dispatch_exc}"
                ) from cleanup_dispatch_exc
            return
        store.mark_cleanup(record.consultation_id, success=True)
        return

    try:
        _publish_failed_dispatch_cleanup(config, record, reason=reason)
    except Exception as cleanup_exc:
        store.mark_cleanup(
            record.consultation_id,
            success=False,
            error=f"cleanup dispatch failed: {cleanup_exc}",
        )
        raise


def _publish_failed_dispatch_cleanup(
    config: GatewayConfig,
    record: Any,
    *,
    reason: str,
) -> None:
    InternalEventBus(internal_event_store_path(config.runtime_dir)).publish(
        event_type=CONSULTATION_TERMINATE_EVENT_TYPE,
        payload={
            "consultation_id": record.consultation_id,
            "reason": reason,
            "status": "cancelled",
        },
        correlation_key=record.consultation_id,
        idempotency_key=f"code-consultation:setup-cleanup:{record.consultation_id}",
        source=InternalEventSource(
            source_type="mcp_tool",
            source_name=ToolName.CONSULT_CODEBASE.value,
        ),
    )


def _wait_for_record(
    store: CodeConsultationStore,
    consultation_id: str,
    *,
    wait_seconds: float,
) -> Any:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        record = store.get(consultation_id)
        if record is None or record.status in TERMINAL_CONSULTATION_STATUSES:
            return record
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return record
        time.sleep(min(0.2, remaining))


def register_consultation_tools(
    server: FastMCP,
    context: RuntimeContext,
    deps: GatewayToolDependencies,
    *,
    transport_session_map: dict[str, str],
    audit_success: Callable[..., None],
    audit_failure: Callable[..., None],
) -> None:
    """Register source-agent and worker-agent consultation operations."""

    @server.tool()
    def consult_codebase(
        managed_repo_id: str,
        query: str,
        source_ref: str | None = None,
        commit_sha: str | None = None,
        profile: str | None = None,
        wait_seconds: float = 30.0,
        idempotency_key: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Ask a disposable agent to inspect an exact managed Git snapshot."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "managed_repo_id": managed_repo_id,
            "query": query,
            "source_ref": source_ref,
            "commit_sha": commit_sha,
            "profile": profile,
            "wait_seconds": wait_seconds,
            "idempotency_key": idempotency_key,
        }
        try:
            source_session = context.policy.require_tool(
                resolved_session_id, ToolName.CONSULT_CODEBASE
            )
            normalized_query = str(query or "").strip()
            if not normalized_query:
                raise ToolError("query is required")
            if len(normalized_query) > 50_000:
                raise ToolError("query exceeds the 50000 character limit")
            config = context.resolve_provider_config_for_session(resolved_session_id)
            profile_name, selected_profile = _profile(config, profile)
            wait = min(
                max(0.0, float(wait_seconds)),
                float(config.agent_consultation_max_wait_seconds),
            )
            normalized_repo_id = str(managed_repo_id or "").strip()
            request_hash = hash_consultation_request(
                managed_repo_id=normalized_repo_id,
                source_ref=source_ref,
                commit_sha=commit_sha,
                profile_name=profile_name,
                query=normalized_query,
            )
            store = CodeConsultationStore(consultation_store_path(config.runtime_dir))
            store.compact(
                before=utc_now() - timedelta(days=config.agent_consultation_retention_days)
            )
            admin = deps.admin_runtime_factory()
            record = store.get_by_idempotency_key(
                source_session_id=resolved_session_id,
                idempotency_key=idempotency_key,
            )
            created = False
            if record is not None:
                if record.request_hash != request_hash:
                    raise ToolError(
                        "idempotency_key is already bound to a different consultation request"
                    )
            else:
                consultation_id = f"consult-{uuid4().hex}"
                worker_session_id = f"consult-{uuid4().hex[:24]}"
                expires_at = utc_now() + timedelta(seconds=selected_profile.timeout_seconds)
                token = issue_consultation_token(
                    secret=_consultation_secret(config),
                    consultation_id=consultation_id,
                    session_id=worker_session_id,
                    expires_at=expires_at,
                )
                snapshot = admin.managed_git_admin.pin_snapshot(
                    normalized_repo_id,
                    source_session.directory_path,
                    consultation_id=consultation_id,
                    source_ref=source_ref,
                    commit_sha=commit_sha,
                )
                try:
                    record, created = store.create(
                        consultation_id=consultation_id,
                        session_id=worker_session_id,
                        source_session_id=resolved_session_id,
                        directory_path=source_session.directory_path,
                        managed_repo_id=snapshot["managed_repo_id"],
                        source_ref=snapshot["source_ref"],
                        commit_sha=snapshot["commit_sha"],
                        profile_name=profile_name,
                        profile=selected_profile.model_dump(mode="json"),
                        query=normalized_query,
                        idempotency_key=idempotency_key,
                        max_concurrency=selected_profile.max_concurrency,
                        expires_at=expires_at,
                        token_hash=hash_consultation_token(token),
                        request_hash=request_hash,
                    )
                except Exception:
                    admin.managed_git_admin.release_snapshot_pin(
                        snapshot["managed_repo_id"],
                        source_session.directory_path,
                        consultation_id=consultation_id,
                        expected_commit_sha=snapshot["commit_sha"],
                    )
                    raise
                if not created:
                    admin.managed_git_admin.release_snapshot_pin(
                        snapshot["managed_repo_id"],
                        source_session.directory_path,
                        consultation_id=consultation_id,
                        expected_commit_sha=snapshot["commit_sha"],
                    )
            if record.status not in TERMINAL_CONSULTATION_STATUSES:
                try:
                    admin.managed_git_admin.pin_snapshot(
                        record.managed_repo_id,
                        record.directory_path,
                        consultation_id=record.consultation_id,
                        commit_sha=record.commit_sha,
                    )
                    _schedule_timeout(config, record.consultation_id, record.expires_at)
                    _ensure_dispatched(config, store, record)
                except Exception as setup_exc:
                    try:
                        _cleanup_failed_dispatch_setup(
                            config,
                            admin,
                            store,
                            record.consultation_id,
                            error=setup_exc,
                        )
                    except Exception as cleanup_exc:
                        raise RuntimeError(
                            f"{setup_exc}; consultation cleanup setup also failed: {cleanup_exc}"
                        ) from cleanup_exc
                    raise
            record = _wait_for_record(
                store,
                record.consultation_id,
                wait_seconds=wait,
            )
            if record is None:
                raise ToolError("consultation disappeared from the runtime store")
            result = {**record.public_payload(), "created": created}
            audit_success(
                resolved_session_id,
                ToolName.CONSULT_CODEBASE,
                args,
                {
                    "consultation_id": record.consultation_id,
                    "status": record.status,
                    "commit_sha": record.commit_sha,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.CONSULT_CODEBASE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.CONSULT_CODEBASE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            if isinstance(exc, ToolError):
                raise
            raise ToolError(str(exc)) from exc

    @server.tool()
    def get_codebase_consultation(
        consultation_id: str,
        wait_seconds: float = 0.0,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read or briefly wait for a consultation owned by the calling session."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "consultation_id": consultation_id,
            "wait_seconds": wait_seconds,
        }
        try:
            context.policy.require_tool(resolved_session_id, ToolName.GET_CODEBASE_CONSULTATION)
            config = context.resolve_provider_config_for_session(resolved_session_id)
            store = CodeConsultationStore(consultation_store_path(config.runtime_dir))
            record = store.get(str(consultation_id or "").strip())
            if record is None or record.source_session_id != resolved_session_id:
                raise ToolError(f"Unknown consultation_id: {consultation_id}")
            _ensure_dispatched(config, store, record)
            wait = min(
                max(0.0, float(wait_seconds)),
                float(config.agent_consultation_max_wait_seconds),
            )
            record = _wait_for_record(store, record.consultation_id, wait_seconds=wait)
            assert record is not None
            result = record.public_payload()
            audit_success(
                resolved_session_id,
                ToolName.GET_CODEBASE_CONSULTATION,
                args,
                {"consultation_id": record.consultation_id, "status": record.status},
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.GET_CODEBASE_CONSULTATION, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.GET_CODEBASE_CONSULTATION,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            if isinstance(exc, ToolError):
                raise
            raise ToolError(str(exc)) from exc

    @server.tool()
    def cancel_codebase_consultation(
        consultation_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Request cancellation of a consultation owned by the calling session."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id, "consultation_id": consultation_id}
        try:
            context.policy.require_tool(resolved_session_id, ToolName.CANCEL_CODEBASE_CONSULTATION)
            config = context.resolve_provider_config_for_session(resolved_session_id)
            store = CodeConsultationStore(consultation_store_path(config.runtime_dir))
            record = store.get(str(consultation_id or "").strip())
            if record is None or record.source_session_id != resolved_session_id:
                raise ToolError(f"Unknown consultation_id: {consultation_id}")
            requested = False
            if record.status == "queued":
                record = store.terminate(
                    record.consultation_id,
                    status="cancelled",
                    error="cancelled by the requesting session",
                )
            needs_cleanup_event = record.status not in TERMINAL_CONSULTATION_STATUSES or (
                record.status == "cancelled" and record.cleanup_status != "completed"
            )
            if needs_cleanup_event:
                InternalEventBus(internal_event_store_path(config.runtime_dir)).publish(
                    event_type=CONSULTATION_TERMINATE_EVENT_TYPE,
                    payload={
                        "consultation_id": record.consultation_id,
                        "reason": "cancelled by the requesting session",
                        "status": "cancelled",
                    },
                    correlation_key=record.consultation_id,
                    idempotency_key=f"code-consultation:cancel:{record.consultation_id}",
                    source=InternalEventSource(
                        source_type="mcp_tool",
                        source_name=ToolName.CANCEL_CODEBASE_CONSULTATION.value,
                    ),
                )
                requested = True
            result = {
                **record.public_payload(),
                "cancellation_requested": requested,
            }
            audit_success(
                resolved_session_id,
                ToolName.CANCEL_CODEBASE_CONSULTATION,
                args,
                {
                    "consultation_id": record.consultation_id,
                    "status": record.status,
                    "cancellation_requested": requested,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(
                resolved_session_id, ToolName.CANCEL_CODEBASE_CONSULTATION, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.CANCEL_CODEBASE_CONSULTATION,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            if isinstance(exc, ToolError):
                raise
            raise ToolError(str(exc)) from exc

    @server.tool()
    def submit_codebase_consultation_result(
        consultation_id: str,
        result_token: str,
        answer: str,
        evidence: list[dict[str, Any]],
        confidence: float,
        limitations: list[str] | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Submit one structured result from the consultation's bound worker session."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "consultation_id": consultation_id,
            "result_token": result_token,
            "answer": answer,
            "evidence": evidence,
            "confidence": confidence,
            "limitations": limitations or [],
        }
        try:
            context.policy.require_tool(
                resolved_session_id, ToolName.SUBMIT_CODEBASE_CONSULTATION_RESULT
            )
            config = context.resolve_provider_config_for_session(resolved_session_id)
            store = CodeConsultationStore(consultation_store_path(config.runtime_dir))
            record = store.get(str(consultation_id or "").strip())
            if record is None or record.session_id != resolved_session_id:
                raise ToolError(f"Unknown consultation_id: {consultation_id}")
            verify_consultation_token(
                result_token,
                secret=_consultation_secret(config),
                consultation_id=record.consultation_id,
                session_id=resolved_session_id,
            )
            result = CodeConsultationResult(
                answer=answer,
                evidence=evidence,
                confidence=confidence,
                limitations=limitations or [],
            )
            admin = deps.admin_runtime_factory()
            validated_evidence = admin.managed_git_admin.validate_snapshot_evidence(
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
            record = store.submit_result(
                record.consultation_id,
                session_id=resolved_session_id,
                token_hash=hash_consultation_token(result_token),
                result=result,
            )
            response = {
                "accepted": True,
                "consultation_id": record.consultation_id,
                "status": record.status,
                "commit_sha": record.commit_sha,
            }
            audit_success(
                resolved_session_id,
                ToolName.SUBMIT_CODEBASE_CONSULTATION_RESULT,
                args,
                response,
            )
            return response
        except PolicyError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SUBMIT_CODEBASE_CONSULTATION_RESULT,
                args,
                str(exc),
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SUBMIT_CODEBASE_CONSULTATION_RESULT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            if isinstance(exc, ToolError):
                raise
            raise ToolError(str(exc)) from exc


__all__ = ["register_consultation_tools"]
