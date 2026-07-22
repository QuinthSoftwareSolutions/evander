"""Request-scoped codebase consultation tools for the MCP gateway."""

from __future__ import annotations

import os
from typing import Any, Callable, NoReturn

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from evander.code_consultation_service import CodeConsultationService
from evander.config import GatewayConfig
from evander.events.internal import InternalEventSource
from evander.gateway.dependencies import GatewayToolDependencies
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import ToolName
from evander.policy import PolicyError
from evander.runtime import RuntimeContext


def _consultation_service(
    config: GatewayConfig,
    deps: GatewayToolDependencies,
) -> CodeConsultationService:
    return deps.code_consultation_service_factory.create(
        config,
        environment=os.environ,
        requested_source=InternalEventSource(
            source_type="mcp_tool",
            source_name=ToolName.CONSULT_CODEBASE.value,
        ),
        cancellation_source=InternalEventSource(
            source_type="mcp_tool",
            source_name=ToolName.CANCEL_CODEBASE_CONSULTATION.value,
        ),
    )


def _raise_tool_error(exc: Exception) -> NoReturn:
    if isinstance(exc, ToolError):
        raise exc
    raise ToolError(str(exc)) from exc


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
            config = context.resolve_provider_config_for_session(resolved_session_id)
            result = _consultation_service(config, deps).create(
                source_session_id=resolved_session_id,
                directory_path=source_session.directory_path,
                managed_repo_id=managed_repo_id,
                query=query,
                source_ref=source_ref,
                commit_sha=commit_sha,
                profile=profile,
                wait_seconds=wait_seconds,
                idempotency_key=idempotency_key,
            )
            audit_success(
                resolved_session_id,
                ToolName.CONSULT_CODEBASE,
                args,
                {
                    "consultation_id": result["consultation_id"],
                    "status": result["status"],
                    "commit_sha": result["commit_sha"],
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
            _raise_tool_error(exc)

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
            result = _consultation_service(config, deps).get(
                source_session_id=resolved_session_id,
                consultation_id=consultation_id,
                wait_seconds=wait_seconds,
            )
            audit_success(
                resolved_session_id,
                ToolName.GET_CODEBASE_CONSULTATION,
                args,
                {
                    "consultation_id": result["consultation_id"],
                    "status": result["status"],
                },
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
            _raise_tool_error(exc)

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
            result = _consultation_service(config, deps).cancel(
                source_session_id=resolved_session_id,
                consultation_id=consultation_id,
            )
            audit_success(
                resolved_session_id,
                ToolName.CANCEL_CODEBASE_CONSULTATION,
                args,
                {
                    "consultation_id": result["consultation_id"],
                    "status": result["status"],
                    "cancellation_requested": result["cancellation_requested"],
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
            _raise_tool_error(exc)

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
            result = _consultation_service(config, deps).submit(
                worker_session_id=resolved_session_id,
                consultation_id=consultation_id,
                result_token=result_token,
                answer=answer,
                evidence=evidence,
                confidence=confidence,
                limitations=limitations,
            )
            audit_success(
                resolved_session_id,
                ToolName.SUBMIT_CODEBASE_CONSULTATION_RESULT,
                args,
                result,
            )
            return result
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
            _raise_tool_error(exc)


__all__ = ["register_consultation_tools"]
