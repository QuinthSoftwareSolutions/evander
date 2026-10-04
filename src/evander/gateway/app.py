from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from evander.gateway.server import *  # noqa: F403
from evander.gateway.server import _MCP_REQUEST_CONTEXT, create_server
from evander.config import GatewayConfig, _normalize_phone_number
from evander.gateway.request_context import (
    _string_from_headers,
    _string_from_query_params,
)
from evander.gateway.session_context import (
    _SESSION_ID_CONTEXT_CONFLICT_MESSAGE,
    _ambient_session_id_from_request,
    _mcp_session_token_from_request,
    _resolve_session_id_from_request,
)
from evander.models import ToolName, tool_name_value
from evander.policy import PolicyError
from evander.retell_phone import (
    RetellWebhookSignatureError,
    retell_api_key_from_config,
    verify_retell_webhook_signature,
)
from evander.runtime import RuntimeContext
from evander.session_auth import (
    redact_mcp_session_token_query,
    verify_mcp_session_proof_token,
)
from evander.voice_sessions import VoiceProjectionService, VoiceQueryDelegator
from evander.voice_sessions import (
    realtime_session_config,
    sanitize_voice_metadata,
    voice_query_payload,
)

logger = logging.getLogger(__name__)


def _install_mcp_http_logging(
    app: Starlette,
    *,
    runtime_context: RuntimeContext,
    transport_session_map: dict[str, str] | None = None,
    transport_binding_store: Any | None = None,
) -> None:
    class McpHttpLoggingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):  # type: ignore[override]
            if request.url.path != "/mcp":
                return await call_next(request)

            token = _MCP_REQUEST_CONTEXT.set(request)
            request_started_at = time.perf_counter()
            declared_session_id = _ambient_session_id_from_request(
                request,
                transport_session_map=None,
            )
            workflow_run_id = _string_from_query_params(
                getattr(request, "query_params", None),
                "workflow_run_id",
            )
            if workflow_run_id is None:
                workflow_run_id = _string_from_headers(
                    request.headers,
                    "x-evander-workflow-run-id",
                    "x-workflow-run-id",
                )
            mcp_tool_approval_lease_id = _string_from_query_params(
                getattr(request, "query_params", None),
                "mcp_tool_approval_lease_id",
                "tool_approval_lease_id",
            )
            if mcp_tool_approval_lease_id is None:
                mcp_tool_approval_lease_id = _string_from_headers(
                    request.headers,
                    "x-evander-mcp-tool-approval-lease-id",
                    "x-mcp-tool-approval-lease-id",
                )
            protocol_version = _string_from_headers(
                request.headers,
                "mcp-protocol-version",
            )
            request_transport_session_id = _string_from_headers(
                request.headers,
                "mcp-session-id",
            )
            request_method_name = ""
            try:
                raw_body = await request.body()
                if raw_body:
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    if isinstance(payload, dict):
                        request_method_name = str(payload.get("method") or "").strip()
            except Exception:
                request_method_name = ""
            redacted_query = redact_mcp_session_token_query(request.url.query)
            logger.info(
                "gateway_mcp_request method=%s path=%s query=%s accept=%s content_type=%s mcp_session_id=%s protocol_version=%s",
                request.method,
                request.url.path,
                redacted_query,
                request.headers.get("accept", ""),
                request.headers.get("content-type", ""),
                request.headers.get("mcp-session-id", ""),
                request.headers.get("mcp-protocol-version", ""),
            )

            async def conflict_response(bound_session_id: str) -> JSONResponse:
                if transport_binding_store is not None:
                    await run_in_threadpool(
                        transport_binding_store.record_conflict,
                        transport_session_id=request_transport_session_id,
                        declared_session_id=declared_session_id,
                        reason=_SESSION_ID_CONTEXT_CONFLICT_MESSAGE,
                        bound_session_id=bound_session_id,
                        last_protocol_version=protocol_version,
                        last_workflow_run_id=workflow_run_id,
                        last_mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                    )
                return JSONResponse(
                    {"error": _SESSION_ID_CONTEXT_CONFLICT_MESSAGE}, status_code=409
                )

            try:
                bound_session_id = ""
                if transport_session_map is not None and request_transport_session_id:
                    bound_session_id = str(
                        transport_session_map.get(request_transport_session_id) or ""
                    ).strip()
                    if bound_session_id and declared_session_id:
                        if bound_session_id != declared_session_id:
                            return await conflict_response(bound_session_id)
                auth_session_id = (declared_session_id or bound_session_id or "").strip()
                if auth_session_id:
                    session = await run_in_threadpool(
                        runtime_context.session_repository.get_session, auth_session_id
                    )
                    if session is not None and not verify_mcp_session_proof_token(
                        session,
                        _mcp_session_token_from_request(request),
                    ):
                        return JSONResponse(
                            {"error": "MCP session token is required for this session"},
                            status_code=401,
                        )
                if transport_session_map is not None and request_transport_session_id:
                    # Authentication yielded to storage workers. Recheck and
                    # reserve the identity on the event loop before yielding
                    # again, so simultaneous first requests cannot rebind it.
                    current_binding = transport_session_map.get(request_transport_session_id)
                    if (
                        current_binding
                        and declared_session_id
                        and current_binding != declared_session_id
                    ):
                        return await conflict_response(current_binding)
                    if declared_session_id:
                        transport_session_map[request_transport_session_id] = declared_session_id
                    if transport_binding_store is not None:
                        effective_bound_session_id = declared_session_id or bound_session_id or None
                        if effective_bound_session_id:
                            await run_in_threadpool(
                                transport_binding_store.bind_transport_session,
                                transport_session_id=request_transport_session_id,
                                bound_session_id=effective_bound_session_id,
                                last_protocol_version=protocol_version,
                                last_workflow_run_id=workflow_run_id,
                                last_mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                            )
                response = await call_next(request)
                response_transport_session_id = str(
                    response.headers.get("mcp-session-id") or ""
                ).strip()
                if (
                    transport_session_map is not None
                    and declared_session_id
                    and response_transport_session_id
                ):
                    bound_session_id = str(
                        transport_session_map.get(response_transport_session_id) or ""
                    ).strip()
                    if not bound_session_id:
                        transport_session_map[response_transport_session_id] = declared_session_id
                    if transport_binding_store is not None:
                        await run_in_threadpool(
                            transport_binding_store.bind_transport_session,
                            transport_session_id=response_transport_session_id,
                            bound_session_id=declared_session_id,
                            last_protocol_version=protocol_version,
                            last_workflow_run_id=workflow_run_id,
                            last_mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                        )
                if (
                    request_method_name == "tools/list"
                    and transport_binding_store is not None
                    and request_transport_session_id
                ):
                    resolved_session_id = (
                        declared_session_id
                        or str(
                            transport_session_map.get(request_transport_session_id) or ""
                        ).strip()
                    )
                    if resolved_session_id:

                        def record_visible_tools():
                            try:
                                session = runtime_context.policy.get_session(resolved_session_id)
                            except PolicyError:
                                session = None
                            if session is not None:
                                visible_tool_names = sorted(
                                    {
                                        tool_name_value(visibility.tool)
                                        for visibility in runtime_context.policy.visible_tools_for_session(
                                            session,
                                            workflow_run_id=workflow_run_id,
                                            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                                        )
                                    }
                                )
                                transport_binding_store.record_visible_tools(
                                    transport_session_id=request_transport_session_id,
                                    bound_session_id=resolved_session_id,
                                    visible_tool_names=visible_tool_names,
                                    last_protocol_version=protocol_version,
                                    last_workflow_run_id=workflow_run_id,
                                    last_mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                                )

                        await run_in_threadpool(record_visible_tools)
                        transport_session_map[request_transport_session_id] = resolved_session_id
                logger.info(
                    "gateway_mcp_response method=%s path=%s status=%s content_type=%s mcp_session_id=%s duration_ms=%d",
                    request.method,
                    request.url.path,
                    response.status_code,
                    response.headers.get("content-type", ""),
                    response.headers.get("mcp-session-id", ""),
                    max(0, int((time.perf_counter() - request_started_at) * 1000)),
                )
                return response
            finally:
                _MCP_REQUEST_CONTEXT.reset(token)

    app.add_middleware(McpHttpLoggingMiddleware)


def create_app(context: RuntimeContext) -> Starlette:
    server = create_server(context)
    mcp_app = server.streamable_http_app()

    async def retell_webhook(request: Request) -> PlainTextResponse | JSONResponse:
        raw_body = await request.body()
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Retell webhook payload must be an object")
            call = payload.get("call")
            if call is not None and not isinstance(call, dict):
                raise ValueError("Retell webhook call payload must be an object")
            call_id = str((call or {}).get("call_id") or "").strip()
            record = context.retell_phone_call_store.get_by_call_id(call_id) if call_id else None
            signature_config = (
                context.provider_config.resolve_for_directory(record.directory_path)
                if record is not None
                else context.provider_config.resolve_for_directory("root")
            )
            verify_retell_webhook_signature(
                raw_body=raw_body,
                signature_header=request.headers.get("x-retell-signature"),
                api_key=retell_api_key_from_config(signature_config),
            )
        except RetellWebhookSignatureError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        except json.JSONDecodeError:
            return JSONResponse(
                {"error": "Retell webhook payload must be valid JSON"},
                status_code=400,
            )
        except ValueError as exc:
            message = str(exc)
            status_code = 503 if "API key is missing" in message else 400
            return JSONResponse({"error": message}, status_code=status_code)

        event = str(payload.get("event") or "").strip()
        if event in {"call_ended", "call_analyzed", "transcript_updated"} and call_id and call:
            context.retell_phone_call_store.update_from_retell_call(
                call_id,
                call,
                event=event,
            )
        return PlainTextResponse("", status_code=204)

    async def copilot_auth_token(request: Request) -> PlainTextResponse | JSONResponse:
        session_id: str | None = None
        vm_id: str | None = None
        try:
            session_id = _resolve_session_id_from_request(request)
            session = context.policy.require_tool(session_id, ToolName.COPILOT_HOST_AUTH)
            vm_id = session.vm_id
            if not verify_mcp_session_proof_token(
                session,
                _mcp_session_token_from_request(request),
            ):
                context.audit.log_decision(
                    event_type="copilot_auth_request",
                    session_id=session_id,
                    vm_id=vm_id,
                    decision="denied",
                    reason="MCP session token is required for this session",
                    args={},
                    result={},
                )
                return JSONResponse(
                    {"error": "MCP session token is required for this session"},
                    status_code=401,
                )
            resolved_config = context.resolve_provider_config_for_session(session_id)
            env_var_name = str(resolved_config.github_token_env or "").strip()
            if not env_var_name:
                raise RuntimeError("github_token_env is not configured for Copilot host auth")
            token = os.environ.get(env_var_name, "").strip()
            if not token:
                context.audit.log_decision(
                    event_type="copilot_auth_request",
                    session_id=session_id,
                    vm_id=vm_id,
                    decision="error",
                    reason=f"missing host token env {env_var_name}",
                    args={"env_var": env_var_name},
                    result={},
                )
                return JSONResponse(
                    {"error": f"Host environment variable {env_var_name} is not set."},
                    status_code=503,
                )
            context.audit.log_decision(
                event_type="copilot_auth_request",
                session_id=session_id,
                vm_id=vm_id,
                decision="allowed",
                args={"env_var": env_var_name},
                result={"status": "issued"},
            )
            return PlainTextResponse(token, headers={"Cache-Control": "no-store"})
        except PolicyError as exc:
            context.audit.log_decision(
                event_type="copilot_auth_request",
                session_id=session_id,
                vm_id=vm_id,
                decision="denied",
                reason=str(exc),
                args={},
                result={},
            )
            return JSONResponse({"error": str(exc)}, status_code=403)
        except Exception as exc:
            context.audit.log_decision(
                event_type="copilot_auth_request",
                session_id=session_id,
                vm_id=vm_id,
                decision="error",
                reason=str(exc),
                args={},
                result={},
            )
            return JSONResponse({"error": str(exc)}, status_code=500)

    def _authorization_bearer_token(request: Request) -> str | None:
        auth_header = str(request.headers.get("authorization") or "").strip()
        if not auth_header.lower().startswith("bearer "):
            return None
        token = auth_header.split(None, 1)[1].strip()
        return token or None

    def _controller_token(config: GatewayConfig) -> tuple[str | None, str | None]:
        env_var = str(config.voice_media_controller_token_env or "").strip()
        if not env_var:
            return None, None
        token = os.environ.get(env_var, "").strip()
        return (token or None), env_var

    def _normalize_inbound_phone_number(value: object) -> str | None:
        return _normalize_phone_number(value, field_name="inbound_phone_calls")

    def _inbound_phone_match_key(value: str | None) -> str | None:
        normalized = _normalize_inbound_phone_number(value)
        if normalized is None:
            return None
        return normalized.removeprefix("+")

    def _hash_identity(value: str | None) -> str | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        return sha256(normalized.encode("utf-8")).hexdigest()

    def _identity_payload(value: str | None) -> dict[str, str] | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        return {"redacted": "<redacted>", "sha256": _hash_identity(normalized) or ""}

    async def create_inbound_phone_call(request: Request) -> JSONResponse:
        root_token, root_env = _controller_token(context.config)
        request_token = _authorization_bearer_token(request)
        root_authorized = bool(root_token and request_token == root_token)
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("inbound phone call payload must be an object")

            raw_did = payload.get("did")
            raw_called_number = payload.get("called_number")
            raw_caller_number = payload.get("caller_number")
            raw_asterisk_channel_id = payload.get("asterisk_channel_id")
            raw_bridge_call_id = payload.get("bridge_call_id")
            raw_route_id = payload.get("route_id")
            raw_sip_headers = payload.get("sip_headers")

            did = _normalize_inbound_phone_number(raw_did)
            called_number = _normalize_inbound_phone_number(raw_called_number)
            if (
                did is not None
                and called_number is not None
                and _inbound_phone_match_key(did) != _inbound_phone_match_key(called_number)
            ):
                raise ValueError("did and called_number must refer to the same number")
            request_number = did or called_number
            if request_number is None:
                raise ValueError("did or called_number is required")

            caller_number = _normalize_inbound_phone_number(raw_caller_number)
            if caller_number is None:
                raise ValueError("caller_number is required")

            asterisk_channel_id = str(raw_asterisk_channel_id or "").strip()
            if not asterisk_channel_id:
                raise ValueError("asterisk_channel_id is required")

            bridge_call_id = str(raw_bridge_call_id or "").strip()
            if not bridge_call_id:
                raise ValueError("bridge_call_id is required")

            route_id = str(raw_route_id or "").strip() or None
            if raw_sip_headers is not None and not isinstance(raw_sip_headers, dict):
                raise ValueError("sip_headers must be an object when provided")
            sip_headers = sanitize_voice_metadata(raw_sip_headers or {})

            binding = context.inbound_phone_routing_service.resolve(
                route_id=route_id,
                did=request_number,
                caller_number=caller_number,
            )
            route = binding.route
            route_directory_path = binding.directory_path
            route_config = context.provider_config.resolve_for_directory(route_directory_path)
            route_token, route_env = _controller_token(route_config)

            if not root_authorized:
                usable_tokens = [token for token in (root_token, route_token) if token]
                if usable_tokens:
                    if request_token not in usable_tokens:
                        return JSONResponse({"error": "Unauthorized"}, status_code=401)
                else:
                    missing_env = root_env or route_env or "VOICE_MEDIA_CONTROLLER_TOKEN"
                    return JSONResponse(
                        {
                            "error": (
                                f"Voice media controller token env var {missing_env!r} is not set"
                            )
                        },
                        status_code=503,
                    )

            route_session_id = binding.session_id
            if not route_session_id:
                raise ValueError("inbound phone route is missing a session_id")
            route_session = context.session_repository.get_session(route_session_id)
            if route_session is None:
                context.audit.log_decision(
                    event_type="inbound_phone_call_provisioning",
                    decision="denied",
                    reason="route session is unavailable",
                    args={
                        "route_id": route.id,
                        "route_directory_path": route_directory_path,
                        "route_session_id": route_session_id,
                        "did_sha256": _hash_identity(request_number),
                        "caller_sha256": _hash_identity(caller_number),
                        "asterisk_channel_id": asterisk_channel_id,
                        "bridge_call_id": bridge_call_id,
                    },
                    result={},
                )
                return JSONResponse(
                    {"error": "Configured inbound route session is unavailable"},
                    status_code=404,
                )
            if route_session.expires_at <= datetime.now(timezone.utc):
                context.audit.log_decision(
                    event_type="inbound_phone_call_provisioning",
                    decision="denied",
                    reason="route session is inactive",
                    args={
                        "route_id": route.id,
                        "route_directory_path": route_directory_path,
                        "route_session_id": route_session_id,
                        "did_sha256": _hash_identity(request_number),
                        "caller_sha256": _hash_identity(caller_number),
                        "asterisk_channel_id": asterisk_channel_id,
                        "bridge_call_id": bridge_call_id,
                    },
                    result={},
                )
                return JSONResponse(
                    {"error": "Configured inbound route session is inactive"},
                    status_code=403,
                )
            if str(route_session.directory_path or "").strip() != route_directory_path:
                context.audit.log_decision(
                    event_type="inbound_phone_call_provisioning",
                    decision="denied",
                    reason="route session directory mismatch",
                    args={
                        "route_id": route.id,
                        "route_directory_path": route_directory_path,
                        "route_session_id": route_session_id,
                        "route_session_directory_path": route_session.directory_path,
                        "did_sha256": _hash_identity(request_number),
                        "caller_sha256": _hash_identity(caller_number),
                        "asterisk_channel_id": asterisk_channel_id,
                        "bridge_call_id": bridge_call_id,
                    },
                    result={},
                )
                return JSONResponse(
                    {"error": "Configured inbound route session directory mismatch"},
                    status_code=403,
                )

            policy_error = context.inbound_phone_routing_service.caller_policy_error(
                route,
                caller_number=caller_number,
            )
            if policy_error is not None:
                context.audit.log_decision(
                    event_type="inbound_phone_call_provisioning",
                    decision="denied",
                    reason=policy_error,
                    args={
                        "route_id": route.id,
                        "route_directory_path": route_directory_path,
                        "route_session_id": route_session_id,
                        "did_sha256": _hash_identity(request_number),
                        "caller_sha256": _hash_identity(caller_number),
                        "asterisk_channel_id": asterisk_channel_id,
                        "bridge_call_id": bridge_call_id,
                    },
                    result={},
                )
                return JSONResponse({"error": policy_error}, status_code=403)

            prompt_metadata = {
                "route_id": route.id,
                "route_directory_path": route_directory_path,
                "request_number_sha256": _hash_identity(request_number),
                "caller_sha256": _hash_identity(caller_number),
                "asterisk_channel_id": asterisk_channel_id,
                "bridge_call_id": bridge_call_id,
                "route_prompt_sha256": _hash_identity(route.route_prompt),
                "startup_text_sha256": _hash_identity(route.startup_text),
            }
            projection = VoiceProjectionService().build_projection(
                session=route_session,
                prompt=route.route_prompt,
                channel="phone",
                metadata={
                    "direction": "inbound",
                    "route_id": route.id,
                    "route_directory_path": route_directory_path,
                    "did_redacted": "<redacted>",
                    "did_sha256": _hash_identity(request_number),
                    "caller_redacted": "<redacted>",
                    "caller_sha256": _hash_identity(caller_number),
                    "asterisk_channel_id": asterisk_channel_id,
                    "bridge_call_id": bridge_call_id,
                    "startup_text": route.startup_text,
                    "sip_headers": sip_headers,
                },
            )
            record, created = context.voice_session_store.prepare_session(
                origin_session_id=route_session.session_id,
                directory_path=route_session.directory_path,
                channel="phone",
                request_id=f"inbound-phone:{route.id}:{asterisk_channel_id}",
                prompt_metadata=prompt_metadata,
                projection=projection,
                caller_metadata={
                    "direction": "inbound",
                    "route_id": route.id,
                    "route_directory_path": route_directory_path,
                    "did_redacted": "<redacted>",
                    "did_sha256": _hash_identity(request_number),
                    "caller_redacted": "<redacted>",
                    "caller_sha256": _hash_identity(caller_number),
                    "asterisk_channel_id": asterisk_channel_id,
                    "bridge_call_id": bridge_call_id,
                    "startup_text": route.startup_text,
                    "sip_headers": sip_headers,
                },
                contact_id=route.id,
                contact_display_name=route.id,
            )
            response = {
                "voice_session_id": record.voice_session_id,
                "request_id": record.request_id,
                "projection": projection,
                "realtime_session_config": realtime_session_config(route_config),
                "route": {
                    "route_id": route.id,
                    "directory_path": route_directory_path,
                    "session_id": route_session_id,
                    "did": _identity_payload(request_number),
                    "caller": _identity_payload(caller_number),
                    "asterisk_channel_id": asterisk_channel_id,
                    "bridge_call_id": bridge_call_id,
                    "sip_headers": sip_headers,
                },
                "inbound_instructions": {
                    "route_prompt": route.route_prompt,
                    "startup_text": route.startup_text,
                },
            }
            context.audit.log_decision(
                event_type="inbound_phone_call_provisioning",
                decision="allowed",
                args={
                    "route_id": route.id,
                    "route_directory_path": route_directory_path,
                    "route_session_id": route_session_id,
                    "did_sha256": _hash_identity(request_number),
                    "caller_sha256": _hash_identity(caller_number),
                    "asterisk_channel_id": asterisk_channel_id,
                    "bridge_call_id": bridge_call_id,
                },
                result={
                    "voice_session_id": record.voice_session_id,
                    "request_id": record.request_id,
                    "created": created,
                },
            )
            return JSONResponse(response, status_code=201 if created else 200)
        except PermissionError as exc:
            context.audit.log_decision(
                event_type="inbound_phone_call_provisioning",
                decision="denied",
                reason=str(exc),
                args={},
                result={},
            )
            return JSONResponse({"error": str(exc)}, status_code=403)
        except LookupError as exc:
            context.audit.log_decision(
                event_type="inbound_phone_call_provisioning",
                decision="denied",
                reason=str(exc),
                args={},
                result={},
            )
            status_code = 409 if "ambiguous" in str(exc).lower() else 404
            return JSONResponse({"error": str(exc)}, status_code=status_code)
        except ValueError as exc:
            context.audit.log_decision(
                event_type="inbound_phone_call_provisioning",
                decision="error",
                reason=str(exc),
                args={},
                result={},
            )
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            context.audit.log_decision(
                event_type="inbound_phone_call_provisioning",
                decision="error",
                reason=str(exc),
                args={},
                result={},
            )
            return JSONResponse({"error": str(exc)}, status_code=500)

    def voice_controller_auth_error(request: Request, directory_path: str) -> JSONResponse | None:
        resolved_config = context.provider_config.resolve_for_directory(directory_path)
        token, token_env = _controller_token(resolved_config)
        if not token:
            return JSONResponse(
                {"error": f"Voice media controller token env var {token_env!r} is not set"},
                status_code=503,
            )
        if request.headers.get("authorization", "") != f"Bearer {token}":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return None

    async def create_voice_query(request: Request) -> JSONResponse:
        voice_session_id = str(request.path_params.get("voice_session_id") or "").strip()
        record = context.voice_session_store.get_by_voice_session_id(voice_session_id)
        if record is None:
            return JSONResponse({"error": "Unknown voice session"}, status_code=404)
        auth_error = voice_controller_auth_error(request, record.directory_path)
        if auth_error is not None:
            return auth_error
        try:
            from evander.gateway.voice_visual_uploads import read_voice_query_request

            payload, images = await read_voice_query_request(request)
            if not isinstance(payload, dict):
                raise ValueError("voice query payload must be an object")
            if images is not None:
                query = await run_in_threadpool(
                    context.teams_voice_service.create_visual_query, record, payload, images,
                )
                return JSONResponse(voice_query_payload(query), status_code=201)
            query = VoiceQueryDelegator(
                store=context.voice_session_store,
                wake_store=context.wake_store,
            ).create_query_and_wake_origin(
                voice_session_id,
                question=str(payload.get("question") or ""),
                request_id=str(payload.get("request_id") or "").strip() or None,
                transcript_excerpt=str(payload.get("transcript_excerpt") or "").strip() or None,
                metadata=payload.get("metadata")
                if isinstance(payload.get("metadata"), dict)
                else {},
                deadline_seconds=(
                    int(payload["deadline_seconds"])
                    if payload.get("deadline_seconds") is not None
                    else None
                ),
            )
            return JSONResponse(voice_query_payload(query), status_code=201)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    async def sync_voice_session_from_bridge(request: Request) -> JSONResponse:
        voice_session_id = str(request.path_params.get("voice_session_id") or "").strip()
        record = context.voice_session_store.get_by_voice_session_id(voice_session_id)
        if record is None:
            return JSONResponse({"error": "Unknown voice session"}, status_code=404)
        auth_error = voice_controller_auth_error(request, record.directory_path)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("voice session bridge result payload must be an object")
            updated = context.voice_session_store.update_from_bridge_payload(
                voice_session_id,
                payload,
            )
            if updated.channel == "teams_call" and updated.status in {"ended", "failed"}:
                updated = context.voice_session_reconciler.schedule_reconciliation(voice_session_id)
            return JSONResponse(
                {
                    "voice_session_id": updated.voice_session_id,
                    "status": updated.status,
                    "reconciliation_status": updated.reconciliation_status,
                    "reconciliation_request_id": updated.reconciliation_request_id,
                }
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    async def get_voice_query(request: Request) -> JSONResponse:
        voice_session_id = str(request.path_params.get("voice_session_id") or "").strip()
        record = context.voice_session_store.get_by_voice_session_id(voice_session_id)
        if record is None:
            return JSONResponse({"error": "Unknown voice session"}, status_code=404)
        auth_error = voice_controller_auth_error(request, record.directory_path)
        if auth_error is not None:
            return auth_error
        query_id = str(request.path_params.get("query_id") or "").strip()
        query = context.voice_session_store.get_query_for_voice_session(
            voice_session_id,
            query_id,
        )
        if query is None:
            return JSONResponse({"error": "Unknown voice query"}, status_code=404)
        return JSONResponse(voice_query_payload(query))

    async def inbound_teams_call(request: Request) -> JSONResponse:
        auth_error = voice_controller_auth_error(request, "root")
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Teams call payload must be an object")
            result = await run_in_threadpool(context.teams_voice_service.admit, payload)
            return JSONResponse(result)
        except (ValueError, PolicyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def timeout_voice_query(request: Request) -> JSONResponse:
        voice_session_id = str(request.path_params.get("voice_session_id") or "").strip()
        record = context.voice_session_store.get_by_voice_session_id(voice_session_id)
        if record is None:
            return JSONResponse({"error": "Unknown voice session"}, status_code=404)
        auth_error = voice_controller_auth_error(request, record.directory_path)
        if auth_error is not None:
            return auth_error
        query_id = str(request.path_params.get("query_id") or "").strip()
        query = context.voice_session_store.get_query_for_voice_session(
            voice_session_id,
            query_id,
        )
        if query is None:
            return JSONResponse({"error": "Unknown voice query"}, status_code=404)
        query = context.voice_session_store.mark_query_timeout(query_id)
        return JSONResponse(voice_query_payload(query))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        # Starlette does not automatically run lifespan handlers for mounted sub-apps.
        # The FastMCP streamable HTTP transport requires its lifespan to initialize the
        # session manager task group before any /mcp request is served.
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = Starlette(
        debug=False,
        routes=[
            Route("/retell/webhook", retell_webhook, methods=["POST"]),
            Route("/copilot-auth/token", copilot_auth_token, methods=["GET"]),
            Route(
                "/voice-sessions/inbound-phone-calls",
                create_inbound_phone_call,
                methods=["POST"],
            ),
            Route("/voice-sessions/inbound-teams-calls", inbound_teams_call, methods=["POST"]),
            Route(
                "/voice-sessions/{voice_session_id}/queries",
                create_voice_query,
                methods=["POST"],
            ),
            Route(
                "/voice-sessions/{voice_session_id}/bridge-result",
                sync_voice_session_from_bridge,
                methods=["POST"],
            ),
            Route(
                "/voice-sessions/{voice_session_id}/queries/{query_id}",
                get_voice_query,
                methods=["GET"],
            ),
            Route(
                "/voice-sessions/{voice_session_id}/queries/{query_id}/timeout",
                timeout_voice_query,
                methods=["POST"],
            ),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    _install_mcp_http_logging(
        app,
        runtime_context=context,
        transport_session_map=getattr(server, "_evander_transport_session_map", None),
        transport_binding_store=context.mcp_transport_bindings,
    )
    return app
