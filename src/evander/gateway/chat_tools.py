"""Chat and agent-prompt tool registration for the MCP gateway."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from evander.config import GatewayConfig
from evander.messaging.agent_prompt_store import AgentPromptRecord
from evander.messaging.agent_prompt_tokens import (
    encode_agent_prompt_response_token_from_claims,
    hash_agent_prompt_response_token,
    issue_agent_prompt_response_token,
    verify_agent_prompt_response_token,
)
from evander.models import (
    ListPendingAgentPromptsRequest,
    ListPendingAgentPromptsResponse,
    PendingAgentPrompt,
    PromptAgentRequest,
    PromptAgentResponse,
    RespondToAgentRequest,
    RespondToAgentResponse,
    ToolName,
)
from evander.policy import PolicyError
from evander.runtime import RuntimeContext
from evander.gateway.dependencies import GatewayToolDependencies
from evander.gateway.session_context import _resolve_session_id_from_context


def _load_agent_prompt_token_secret(config: GatewayConfig) -> str:
    env_var_name = str(config.agent_prompt_token_secret_env or "").strip()
    if not env_var_name:
        raise ToolError("agent_prompt_token_secret_env must be configured")
    secret = os.environ.get(env_var_name, "").strip()
    if not secret:
        raise ToolError(
            f"Agent prompt token secret is missing in environment variable {env_var_name!r}"
        )
    return secret


def _issue_agent_prompt_response_token_for_session(
    context: RuntimeContext,
    *,
    sender_session_id: str,
    target_session_id: str,
    message_id: str,
    issued_at: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    resolved_config = context.resolve_provider_config_for_session(target_session_id)
    return issue_agent_prompt_response_token(
        secret=_load_agent_prompt_token_secret(resolved_config),
        message_id=message_id,
        sender_session_id=sender_session_id,
        target_session_id=target_session_id,
        issued_at=issued_at,
        ttl_seconds=resolved_config.agent_prompt_token_ttl_seconds,
    )


def _get_or_refresh_agent_prompt_response_token(
    context: RuntimeContext,
    *,
    message: AgentPromptRecord,
    refresh_expired: bool,
) -> tuple[AgentPromptRecord, str, dict[str, Any]]:
    resolved_config = context.resolve_provider_config_for_session(message.target_session_id)
    secret = _load_agent_prompt_token_secret(resolved_config)
    allow_refresh = refresh_expired and message.status == "unread" and message.token_used_at is None
    stored_claims = dict(message.issued_token_claims or {})
    if stored_claims:
        try:
            issued_response_token = encode_agent_prompt_response_token_from_claims(
                secret=secret,
                claims=stored_claims,
            )
            issued_token_hash = hash_agent_prompt_response_token(issued_response_token)
            if issued_token_hash == message.issued_token_hash:
                expires_at = int(stored_claims["expires_at"])
                if datetime.now(timezone.utc).timestamp() < float(expires_at) or not allow_refresh:
                    return message, issued_response_token, stored_claims
        except (ValueError, KeyError, TypeError):
            pass
    if not allow_refresh:
        raise ToolError(
            f"No active response token is available for message_id: {message.message_id}"
        )

    issued_response_token, issued_claims = _issue_agent_prompt_response_token_for_session(
        context,
        sender_session_id=message.sender_session_id,
        target_session_id=message.target_session_id,
        message_id=message.message_id,
    )
    issued_token_hash = hash_agent_prompt_response_token(issued_response_token)
    refreshed = context.agent_prompt_store.replace_issued_token(
        message.message_id,
        issued_token_hash=issued_token_hash,
        issued_token_claims=issued_claims,
    )
    if refreshed is None:
        raise ToolError(f"Unknown message_id: {message.message_id}")
    return refreshed, issued_response_token, issued_claims


def _assistant_allowed_chat_thread(session: Any, thread_id: str) -> bool:
    metadata = getattr(session, "metadata", {}) if session is not None else {}
    if not isinstance(metadata, dict):
        return False
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_thread_id:
        return False
    chat_threads = metadata.get("admin_chat_threads")
    if not isinstance(chat_threads, dict):
        return False
    for raw_mapping in chat_threads.values():
        if not isinstance(raw_mapping, dict):
            continue
        if str(raw_mapping.get("thread_id") or "").strip() == normalized_thread_id:
            return True
    return False


def register_chat_tools(
    server: MCPServer,
    context: RuntimeContext,
    deps: GatewayToolDependencies,
    *,
    transport_session_map: dict[str, str],
    audit_success: Callable[..., None],
    audit_failure: Callable[..., None],
    require_vm_visible_in_session_directory: Callable[..., Any],
    require_target_session_visible_within_directory: Callable[..., Any],
) -> None:
    """Register chat tools with the MCP gateway server."""

    @server.tool()
    def assistant_post_chat_reply(
        thread_id: str,
        content: str,
        user_message_id: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "thread_id": thread_id,
            "user_message_id": user_message_id,
            "content": content,
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_POST_CHAT_REPLY
            )
            if not _assistant_allowed_chat_thread(session, thread_id):
                raise PolicyError(
                    f"Chat thread {thread_id!r} is not mapped to assistant session {resolved_session_id}"
                )
            normalized_content = str(content or "").strip()
            if not normalized_content:
                raise ToolError("content is required")
            store = deps.chat_store_factory()
            message = store.append_assistant_reply(
                thread_id=thread_id,
                content=normalized_content,
                user_message_id=user_message_id,
            )
            result = {"thread_id": thread_id, "message": message}
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_POST_CHAT_REPLY,
                args,
                {"thread_id": thread_id, "message_id": message.get("id")},
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_POST_CHAT_REPLY, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_POST_CHAT_REPLY,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def prompt_agent(
        session_id: str | None = None,
        target_vm_name: str | None = None,
        target_session_id: str | None = None,
        prompt: str = "",
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        vm_user: str = "agent",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "target_vm_name": target_vm_name,
            "target_session_id": target_session_id,
            "prompt": prompt,
            "metadata": metadata or {},
            "idempotency_key": idempotency_key,
            "vm_user": vm_user,
        }
        try:
            request = PromptAgentRequest(
                session_id=resolved_session_id,
                target_vm_name=target_vm_name,
                target_session_id=target_session_id,
                prompt=prompt,
                metadata=metadata or {},
                idempotency_key=idempotency_key.strip()
                if isinstance(idempotency_key, str)
                else None,
            )
            args = request.model_dump(mode="json")
            args["vm_user"] = vm_user
            source_session = context.policy.require_tool(request.session_id, ToolName.PROMPT_AGENT)

            resolved_target_session_id = ""
            if request.target_vm_name:
                summary = require_vm_visible_in_session_directory(
                    context,
                    source_session,
                    vm_name=request.target_vm_name,
                    vm_user=vm_user,
                )
                if not summary.session_ids:
                    raise ToolError(
                        f"VM '{request.target_vm_name}' has no visible target session bound for prompt dispatch."
                    )
                if len(summary.session_ids) > 1:
                    raise ToolError(
                        f"VM '{request.target_vm_name}' has multiple visible sessions; use target_session_id instead."
                    )
                resolved_target_session_id = summary.session_ids[0]
            else:
                resolved_target_session_id = str(request.target_session_id or "").strip()
                if not resolved_target_session_id:
                    raise ToolError("target_session_id is required")
                require_target_session_visible_within_directory(
                    context,
                    source_session,
                    target_session_id=resolved_target_session_id,
                    vm_user=vm_user,
                )
            context.policy.require_tool(resolved_target_session_id, ToolName.RESPOND_TO_AGENT)

            if request.idempotency_key:
                existing = context.agent_prompt_store.find_prompt_by_idempotency_key(
                    sender_session_id=request.session_id,
                    idempotency_key=request.idempotency_key,
                )
                if existing is not None:
                    if existing.target_session_id != resolved_target_session_id:
                        raise ToolError(
                            "idempotency_key is already bound to a different target_session_id"
                        )
                    existing_payload = dict(existing.prompt_payload or {})
                    if str(existing_payload.get("prompt") or "").strip() != request.prompt:
                        raise ToolError("idempotency_key is already bound to a different prompt")
                    if dict(existing_payload.get("metadata") or {}) != request.metadata:
                        raise ToolError("idempotency_key is already bound to different metadata")
                    existing, issued_response_token, _issued_claims = (
                        _get_or_refresh_agent_prompt_response_token(
                            context,
                            message=existing,
                            refresh_expired=True,
                        )
                    )
                    response = PromptAgentResponse(
                        message_id=existing.message_id,
                        target_session_id=existing.target_session_id,
                        status=existing.status,
                        response_token=issued_response_token,
                    ).model_dump(mode="json")
                    audit_success(
                        resolved_session_id,
                        ToolName.PROMPT_AGENT,
                        args,
                        {
                            "message_id": response["message_id"],
                            "target_session_id": response["target_session_id"],
                            "status": response["status"],
                            "response_token": "<redacted>",
                        },
                    )
                    return response

            now = datetime.now(timezone.utc)
            message_id = f"agent-prompt-{uuid4().hex}"
            issued_response_token, issued_claims = _issue_agent_prompt_response_token_for_session(
                context,
                message_id=message_id,
                sender_session_id=request.session_id,
                target_session_id=resolved_target_session_id,
                issued_at=now,
            )
            issued_token_hash = hash_agent_prompt_response_token(issued_response_token)
            created = context.agent_prompt_store.create_prompt_message(
                AgentPromptRecord(
                    message_id=message_id,
                    sender_session_id=request.session_id,
                    sender_vm_id=source_session.vm_id,
                    target_session_id=resolved_target_session_id,
                    target_vm_id=None,
                    prompt_payload={
                        "prompt": request.prompt,
                        "metadata": request.metadata,
                        "idempotency_key": request.idempotency_key,
                    },
                    issued_token_hash=issued_token_hash,
                    issued_token_claims=issued_claims,
                    status="unread",
                    created_at=now,
                    updated_at=now,
                )
            )
            if not created:
                raise ToolError("Failed to persist agent prompt message")

            response = PromptAgentResponse(
                message_id=message_id,
                target_session_id=resolved_target_session_id,
                status="unread",
                response_token=issued_response_token,
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.PROMPT_AGENT,
                args,
                {
                    "message_id": response["message_id"],
                    "target_session_id": response["target_session_id"],
                    "status": response["status"],
                    "response_token": "<redacted>",
                },
            )
            return response
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.PROMPT_AGENT, args, str(exc))
            raise
        except ValueError as exc:
            message = str(exc)
            audit_failure(
                resolved_session_id,
                ToolName.PROMPT_AGENT,
                args,
                message,
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(message) from exc
        except ToolError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.PROMPT_AGENT,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.PROMPT_AGENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_pending_agent_prompts(
        session_id: str | None = None,
        limit: int | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "limit": limit,
        }
        try:
            request = ListPendingAgentPromptsRequest(session_id=resolved_session_id, limit=limit)
            args = request.model_dump(mode="json")
            context.policy.require_tool(request.session_id, ToolName.LIST_PENDING_AGENT_PROMPTS)
            messages = context.agent_prompt_store.list_unread_prompts(
                target_session_id=request.session_id,
                limit=request.limit,
            )

            prompts: list[PendingAgentPrompt] = []
            for message in messages:
                message, issued_response_token, issued_claims = (
                    _get_or_refresh_agent_prompt_response_token(
                        context,
                        message=message,
                        refresh_expired=False,
                    )
                )
                prompt_payload = dict(message.prompt_payload or {})
                prompts.append(
                    PendingAgentPrompt(
                        message_id=message.message_id,
                        sender_session_id=message.sender_session_id,
                        target_session_id=message.target_session_id,
                        prompt=str(prompt_payload.get("prompt") or "").strip(),
                        metadata=dict(prompt_payload.get("metadata") or {}),
                        status=message.status,
                        created_at=message.created_at,
                        expires_at=datetime.fromtimestamp(
                            int(issued_claims["expires_at"]),
                            tz=timezone.utc,
                        ),
                        response_token=issued_response_token,
                        response_token_claims=issued_claims,
                    )
                )

            result = ListPendingAgentPromptsResponse(prompts=prompts).model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.LIST_PENDING_AGENT_PROMPTS,
                args,
                {"count": len(prompts)},
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.LIST_PENDING_AGENT_PROMPTS, args, str(exc))
            raise
        except ValueError as exc:
            message = str(exc)
            audit_failure(
                resolved_session_id,
                ToolName.LIST_PENDING_AGENT_PROMPTS,
                args,
                message,
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(message) from exc
        except ToolError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.LIST_PENDING_AGENT_PROMPTS,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.LIST_PENDING_AGENT_PROMPTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def respond_to_agent(
        session_id: str | None = None,
        message_id: str = "",
        response_token: str = "",
        response_text: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "message_id": message_id,
            "response_token": response_token,
            "response_text": response_text,
        }
        try:
            request = RespondToAgentRequest(
                session_id=resolved_session_id,
                message_id=message_id,
                response_token=response_token,
                response_text=response_text,
            )
            args = request.model_dump(mode="json")
            context.policy.require_tool(request.session_id, ToolName.RESPOND_TO_AGENT)
            message = context.agent_prompt_store.get_prompt(request.message_id)
            if message is None:
                raise ToolError(f"Unknown message_id: {request.message_id}")
            resolved_config = context.resolve_provider_config_for_session(request.session_id)
            claims = verify_agent_prompt_response_token(
                token=request.response_token,
                secret=_load_agent_prompt_token_secret(resolved_config),
                expected_message_id=request.message_id,
                expected_target_session_id=request.session_id,
            )
            if claims["sender_session_id"] != message.sender_session_id:
                raise PolicyError(
                    "response_token sender_session_id claim does not match original message"
                )

            token_hash = hash_agent_prompt_response_token(request.response_token)
            consumed = context.agent_prompt_store.validate_and_consume_token(
                token_hash,
                expected_message_id=request.message_id,
            )
            if consumed is None:
                raise PolicyError(
                    "response_token is invalid, already consumed, or does not match message_id"
                )
            if consumed.target_session_id != request.session_id:
                raise PolicyError("response token is not valid for this session")

            updated = context.agent_prompt_store.record_response(
                request.message_id,
                response_text=request.response_text,
                responder_session_id=request.session_id,
            )
            if updated is None:
                raise ToolError(f"Unknown message_id: {request.message_id}")

            result = RespondToAgentResponse(
                status="responded",
                message_id=request.message_id,
                delivered_at=updated.updated_at,
            ).model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.RESPOND_TO_AGENT,
                args,
                {
                    "status": result["status"],
                    "message_id": result["message_id"],
                    "delivered_at": result["delivered_at"],
                    "response_token": "<redacted>",
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.RESPOND_TO_AGENT, args, str(exc))
            raise
        except ValueError as exc:
            message = str(exc)
            audit_failure(
                resolved_session_id,
                ToolName.RESPOND_TO_AGENT,
                args,
                message,
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(message) from exc
        except ToolError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.RESPOND_TO_AGENT,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.RESPOND_TO_AGENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise
