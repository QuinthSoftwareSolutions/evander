"""Session-scoped managed reference-repository tools for the MCP gateway."""

from __future__ import annotations

from typing import Any, Callable, NoReturn

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from evander.effects.providers.agent_prepare_reference_repositories import (
    AgentPrepareReferenceRepositoriesEffectProvider,
)
from evander.effects.providers.base import EffectRuntimeContext
from evander.events.provider_models import AgentPrepareReferenceRepositoriesEffectDefinition
from evander.gateway.dependencies import GatewayToolDependencies
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import ToolName
from evander.policy import PolicyError
from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter
from evander.runtime import RuntimeContext


def _raise_tool_error(exc: Exception) -> NoReturn:
    if isinstance(exc, ToolError):
        raise exc
    raise ToolError(str(exc)) from exc


def register_reference_repository_tools(
    server: FastMCP,
    context: RuntimeContext,
    deps: GatewayToolDependencies,
    *,
    transport_session_map: dict[str, str],
    audit_success: Callable[..., None],
    audit_failure: Callable[..., None],
) -> None:
    """Register on-demand read-only managed repository preparation."""

    @server.tool()
    def prepare_reference_repository(
        managed_repo_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Refresh and prepare one policy-allowed managed repository for this session."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id,
            ctx,
            transport_session_map=transport_session_map,
        )
        normalized_repo_id = str(managed_repo_id or "").strip()
        args = {
            "session_id": resolved_session_id,
            "managed_repo_id": normalized_repo_id,
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id,
                ToolName.PREPARE_REFERENCE_REPOSITORY,
            )
            if not normalized_repo_id:
                raise ValueError("managed_repo_id is required")
            resolved_config = context.resolve_provider_config_for_session(resolved_session_id)
            provider = AgentPrepareReferenceRepositoriesEffectProvider()
            provider.initialize(
                effect_definition=AgentPrepareReferenceRepositoriesEffectDefinition(
                    id="mcp-prepare-reference-repository",
                    display_name="Prepare reference repository",
                    type="effect:agent:prepare-reference-repositories",
                    session_id=resolved_session_id,
                    directory_path=session.directory_path,
                    repositories=[{"managed_repo_id": normalized_repo_id}],
                    refresh_upstream=True,
                ),
                runtime_context=EffectRuntimeContext(
                    provider_config=resolved_config.model_dump(mode="json"),
                    provider_config_resolver=context.provider_config,
                    admin_service=AdminRuntimeProviderAdapter(deps.admin_runtime_factory()),
                    metadata={"directory_path": session.directory_path},
                ),
            )
            result = provider.execute()
            repositories = result.get("repositories")
            if not isinstance(repositories, list) or len(repositories) != 1:
                raise RuntimeError(
                    "reference repository preparation returned an unexpected result"
                )
            prepared = repositories[0]
            if not isinstance(prepared, dict):
                raise RuntimeError(
                    "reference repository preparation returned an invalid repository result"
                )
            response = {
                "success": bool(result.get("success", True)),
                "managed_repo_id": str(prepared.get("managed_repo_id") or normalized_repo_id),
                "repo_name": str(prepared.get("repo_name") or ""),
                "source_ref": str(prepared.get("source_ref") or ""),
                "commit_sha": str(prepared.get("commit_sha") or ""),
                "target_dir": str(prepared.get("target_dir") or ""),
                "refresh_performed": bool(prepared.get("refresh_performed")),
                "checkout_updated": bool(prepared.get("checkout_updated")),
                "access_reused": bool(prepared.get("access_reused")),
                "trust_reconciled": bool(prepared.get("trust_reconciled")),
            }
            audit_success(
                resolved_session_id,
                ToolName.PREPARE_REFERENCE_REPOSITORY,
                args,
                response,
            )
            return response
        except PolicyError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.PREPARE_REFERENCE_REPOSITORY,
                args,
                str(exc),
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.PREPARE_REFERENCE_REPOSITORY,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            _raise_tool_error(exc)


__all__ = ["register_reference_repository_tools"]
