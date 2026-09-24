"""Session-grantable classification tools; host credentials never enter tool arguments."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from starlette.concurrency import run_in_threadpool

from evander.classification.runtime import session_attribution
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import ToolName
from evander.policy import PolicyError


def register_classification_tools(
    server, context, deps, *, transport_session_map=None, audit_success, audit_failure
):
    def invoke(tool, session_id, ctx, payload):
        resolved = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        # Audit metadata, not potentially confidential model state or instructions.
        audit_args = {
            "profile_id": payload.get("profile_id"),
            "question_ids": sorted((payload.get("questions") or {}).keys()),
        }
        try:
            session = context.policy.require_tool(resolved, tool)
            settings = context.provider_config.resolve_for_session_manifest(session).classification
            service = deps.classification_service_factory()
            if tool == ToolName.CLASSIFICATION_LIST_PROFILES:
                result = service.list_profiles(settings)
            else:
                result = service.evaluate(
                    settings, payload, attribution=session_attribution(context, session)
                )
            audit_success(
                resolved,
                tool,
                audit_args,
                {
                    key: result[key]
                    for key in (
                        "provider",
                        "model",
                        "duration_ms",
                        "profile_id",
                        "profile_revision",
                    )
                    if key in result
                },
            )
            return result
        except (ValueError, PolicyError) as exc:
            audit_failure(resolved, tool, audit_args, str(exc))
            raise ToolError(str(exc)) from None
        except Exception:
            audit_failure(
                resolved,
                tool,
                audit_args,
                "Classification evaluation failed",
                decision="error",
                error_class="classification_error",
            )
            raise ToolError("Classification evaluation failed") from None

    @server.tool()
    async def classification_evaluate(
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Any] | None = None,
        profile_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Evaluate choice, score, or boolean questions against supplied state.

        State is text or JSON containing the input and context. Each independent question
        has type, instructions (text or JSON), and criteria: a map of option IDs to
        descriptions for choice, an ordered list of levels for score, or optional
        true/false descriptions for boolean. Choice and score require at least two options.
        Supply either questions (named type/instructions/criteria objects) or profile_id.
        Profile definitions are fixed. Boolean answers return probability_true, not an action.
        Results include named answers, provider/model, native usage, and duration_ms.
        """
        return await run_in_threadpool(
            invoke,
            ToolName.CLASSIFICATION_EVALUATE,
            session_id,
            ctx,
            {
                "state": state,
                "questions": questions,
                "profile_id": profile_id,
                "provider": provider,
                "model": model,
            },
        )

    @server.tool()
    async def classification_list_profiles(
        session_id: str | None = None, ctx: Context | None = None
    ) -> dict[str, Any]:
        """List enabled classification profiles available in the caller's directory."""
        return await run_in_threadpool(
            invoke, ToolName.CLASSIFICATION_LIST_PROFILES, session_id, ctx, {}
        )
