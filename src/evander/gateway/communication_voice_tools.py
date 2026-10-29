"""MCP tools for voice."""

from __future__ import annotations
import base64
import json
from collections.abc import Callable
from typing import Any
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent
from evander.email.http_utils import ProviderRequestError
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import (
    GetPhoneCallResultRequest,
    ListContactsRequest,
    MakePhoneCallRequest,
    ContactSummary,
    ListContactsResponse,
    MakePhoneCallResponse,
    SubmitVoiceQueryResponseRequest,
    SubmitVoiceQueryResponseResponse,
    ToolName,
)
from evander.policy import PolicyError
from evander.retell_phone import sanitize_retell_metadata
from evander.voice_sessions import disable_pending_voice_query_wake

from .communication_support import CommunicationToolHelpers


def create_voice_tools(
    context: Any, helpers: CommunicationToolHelpers, *, transport_session_map: dict[str, str]
) -> dict[str, Callable[..., Any]]:
    audit_failure = helpers.audit_failure
    audit_success = helpers.audit_success
    transport_session_map = helpers.transport_session_map

    def list_contacts(
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List directory contacts visible to this session without exposing addresses or numbers."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id}
        try:
            request = ListContactsRequest(session_id=resolved_session_id)
            args = request.model_dump(mode="json")
            session = context.policy.require_tool(
                resolved_session_id,
                ToolName.LIST_CONTACTS,
            )
            contacts_config = context.provider_config.resolve_effective_contacts_for_session_id(
                resolved_session_id
            )
            contacts = []
            for contact_id, contact in sorted(contacts_config.contacts.items()):
                primary_phone_labels = [
                    phone.label for phone in contact.phone_numbers if phone.primary
                ]
                contacts.append(
                    ContactSummary(
                        id=contact_id,
                        name=contact.name,
                        description=contact.description,
                        email_address_count=len(contact.email_addresses),
                        phone_number_count=len(contact.phone_numbers),
                        primary_phone_label=(
                            primary_phone_labels[0] if len(primary_phone_labels) == 1 else None
                        ),
                        callable=len(primary_phone_labels) == 1,
                    )
                )
            result = ListContactsResponse(
                directory_path=session.directory_path,
                contacts=contacts,
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.LIST_CONTACTS,
                args,
                {
                    "directory_path": result["directory_path"],
                    "contact_count": len(result["contacts"]),
                    "callable_contact_count": sum(
                        1 for contact in result["contacts"] if contact.get("callable")
                    ),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.LIST_CONTACTS, args, str(exc))
            raise
        except ValueError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.LIST_CONTACTS,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.LIST_CONTACTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def make_phone_call(
        contact: str,
        prompt: str,
        session_id: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Start a host-side Retell phone call to a directory contact id."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        safe_metadata = sanitize_retell_metadata(metadata or {})
        args = {
            "session_id": resolved_session_id,
            "contact": contact,
            "prompt": prompt,
            "request_id": request_id,
            "metadata": safe_metadata,
        }
        try:
            request = MakePhoneCallRequest(
                session_id=resolved_session_id,
                contact=contact,
                prompt=prompt,
                request_id=request_id.strip() if isinstance(request_id, str) else None,
                metadata=safe_metadata,
            )
            args = request.model_dump(mode="json")
            session = context.policy.require_tool(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
            )
            result = context.phone_call_service_for_session(resolved_session_id).make_phone_call(
                session=session,
                contact=request.contact,
                prompt=request.prompt,
                request_id=request.request_id,
                metadata=request.metadata,
            )
            result = MakePhoneCallResponse.model_validate(result).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
                args,
                {
                    "request_id": result["request_id"],
                    "call_id": result.get("call_id"),
                    "contact": result["contact"],
                    "status": result["status"],
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.MAKE_PHONE_CALL, args, str(exc))
            raise
        except (ValueError, ProviderRequestError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def get_phone_call_result(
        session_id: str | None = None,
        request_id: str | None = None,
        call_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read a Retell phone call result owned by this session."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "request_id": request_id,
            "call_id": call_id,
        }
        try:
            request = GetPhoneCallResultRequest(
                session_id=resolved_session_id,
                request_id=request_id,
                call_id=call_id,
            )
            args = request.model_dump(mode="json")
            session = context.policy.require_tool(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
            )
            result = context.phone_call_service_for_session(
                resolved_session_id
            ).get_phone_call_result(
                session=session,
                request_id=request.request_id,
                call_id=request.call_id,
            )
            audit_success(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
                args,
                {
                    "request_id": result.get("request_id"),
                    "call_id": result.get("call_id"),
                    "contact": result.get("contact"),
                    "status": result.get("status"),
                    "has_transcript": bool(result.get("transcript")),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.GET_PHONE_CALL_RESULT, args, str(exc))
            raise
        except (ValueError, ProviderRequestError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def get_voice_query_visual_context(
        query_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> CallToolResult:
        """Inspect the frozen camera/screen images for this session's delegated voice query."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map,
        )
        args = {"session_id": resolved_session_id, "query_id": query_id}
        try:
            images = context.voice_visual_service.read_visual_context(
                query_id=query_id, origin_session_id=resolved_session_id,
            )
            metadata = [reference.model_dump(mode="json") for reference, _ in images]
            audit_success(resolved_session_id, ToolName.GET_VOICE_QUERY_VISUAL_CONTEXT, args,
                          {"query_id": query_id, "snapshots": metadata})
            content = []
            for reference, image in images:
                content.append(TextContent(type="text", text=json.dumps(reference.model_dump(mode="json"))))
                content.append(ImageContent(type="image", data=base64.b64encode(image).decode(), mimeType="image/jpeg"))
            return CallToolResult(content=content)
        except (PolicyError, ValueError, FileNotFoundError) as exc:
            audit_failure(resolved_session_id, ToolName.GET_VOICE_QUERY_VISUAL_CONTEXT, args, str(exc))
            raise ToolError(str(exc)) from exc

    def submit_voice_query_response(
        query_id: str,
        answer: str,
        session_id: str | None = None,
        status: str = "answered",
        citations: list[str] | None = None,
        confidence: str | None = None,
        metadata: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Answer a delegated live voice query for this originating session."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        safe_metadata = sanitize_retell_metadata(metadata or {})
        args = {
            "session_id": resolved_session_id,
            "query_id": query_id,
            "answer": answer,
            "status": status,
            "citations": citations or [],
            "confidence": confidence,
            "metadata": safe_metadata,
        }
        try:
            request = SubmitVoiceQueryResponseRequest(
                session_id=resolved_session_id,
                query_id=query_id,
                answer=answer,
                status=status,
                citations=citations or [],
                confidence=confidence,
                metadata=safe_metadata,
            )
            args = request.model_dump(mode="json")
            context.policy.require_tool(
                resolved_session_id,
                ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
            )
            query = context.voice_session_store.complete_query(
                request.query_id,
                origin_session_id=resolved_session_id,
                answer=request.answer,
                status="failed" if request.status == "failed" else "answered",
                citations=request.citations,
                confidence=request.confidence,
                metadata=request.metadata,
            )
            wake_request_disabled = disable_pending_voice_query_wake(
                query=query,
                wake_store=context.wake_store,
            )
            visual_service = getattr(context, "voice_visual_service", None)
            if visual_service is not None:
                visual_service.cleanup_visual_context()
            result = SubmitVoiceQueryResponseResponse(
                query_id=query.query_id,
                voice_session_id=query.voice_session_id,
                status=query.status,
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
                args,
                {
                    "query_id": result["query_id"],
                    "voice_session_id": result["voice_session_id"],
                    "status": result["status"],
                    "wake_request_disabled": wake_request_disabled,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
                args,
                str(exc),
            )
            raise
        except ValueError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    return {
        "list_contacts": list_contacts,
        "make_phone_call": make_phone_call,
        "get_phone_call_result": get_phone_call_result,
        "submit_voice_query_response": submit_voice_query_response,
        "get_voice_query_visual_context": get_voice_query_visual_context,
    }
