"""MCP tools for attachments."""

from __future__ import annotations
from collections.abc import Callable
from typing import Any
import httpx
from mcp.server.mcpserver.exceptions import ToolError
from evander.email.http_utils import ProviderRequestError
from evander.models import (
    CommunicationAttachmentRef,
    CommunicationDownloadAttachmentResponse,
    CommunicationListAttachmentsResponse,
    CommunicationDownloadAttachmentRequest,
    CommunicationListAttachmentsRequest,
    DownloadInlineMediaRequest,
    ListInlineMediaRequest,
    DownloadInlineMediaResponse,
    ListInlineMediaResponse,
    ToolName,
)
from evander.inline_media import normalize_inline_media_provider
from evander.policy import PolicyError

from .communication_support import CommunicationToolHelpers


def create_attachments_tools(
    context: Any, helpers: CommunicationToolHelpers
) -> dict[str, Callable[..., Any]]:
    _audit_inline_media_operation = helpers.audit_inline_media_operation
    _communication_target_audit_fields = helpers.communication_target_audit_fields
    _download_inline_media_bytes = helpers.download_inline_media_bytes
    _inline_media_ref_audit_payload = helpers.inline_media_ref_audit_payload
    _normalize_inline_media_source_kind = helpers.normalize_inline_media_source_kind
    _provider_download_attachment = helpers.provider_download_attachment
    _resolve_communication_attachments = helpers.resolve_communication_attachments
    _resolve_inline_media = helpers.resolve_inline_media
    _stage_communication_artifact = helpers.stage_communication_artifact
    _stage_inline_media_artifact = helpers.stage_inline_media_artifact
    audit_failure = helpers.audit_failure
    audit_success = helpers.audit_success

    def list_inline_media(
        session_id: str,
        source_kind: str,
        provider: str,
        resource_id: str,
        comment_id: str | None = None,
        project: str | None = None,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        """List image references from ADO work items, PR feedback or communications.

        For work items use source_kind=ado_backlog, provider=azure_devops,
        resource_id=<work item ID> and project. Requires file reception to be enabled.
        Download a returned media_id with download_inline_media to inspect its contents.
        """
        request = ListInlineMediaRequest(
            session_id=session_id,
            source_kind=source_kind,
            provider=provider,
            resource_id=resource_id,
            comment_id=comment_id,
            project=project,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        try:
            media = [ref for ref, _raw in _resolve_inline_media(request)]
            response = ListInlineMediaResponse(
                source_kind=_normalize_inline_media_source_kind(source_kind),
                provider=normalize_inline_media_provider(provider),
                resource_id=resource_id,
                media=media,
            )
            result = response.model_dump(mode="json")
            list_audit_result = {
                "source_kind": response.source_kind,
                "provider": response.provider,
                "resource_id": resource_id,
                "count": len(media),
                "comment_ids": sorted({item.comment_id for item in media if item.comment_id}),
                "mime_types": sorted({item.mime_type for item in media if item.mime_type}),
                "byte_count": sum(item.byte_count or 0 for item in media),
            }
            audit_success(
                session_id,
                ToolName.LIST_INLINE_MEDIA,
                args,
                list_audit_result,
            )
            _audit_inline_media_operation(
                event_type="inline_media_list",
                session_id=session_id,
                request=request,
                media=media,
                result=list_audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_INLINE_MEDIA, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.LIST_INLINE_MEDIA,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_INLINE_MEDIA,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def download_inline_media(
        session_id: str,
        source_kind: str,
        provider: str,
        resource_id: str,
        media_id: str,
        comment_id: str | None = None,
        project: str | None = None,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        """Download one referenced image into the host artifact store.

        Requires this tool grant and communication_file_receive_enabled. This returns
        artifact metadata, not visible image content. Copy artifact.artifact_id with
        communication_copy_artifact_to_workspace, choosing a unique destination_filename
        for each image, then open its destination_path with your local image viewer.
        """
        request = DownloadInlineMediaRequest(
            session_id=session_id,
            source_kind=source_kind,
            provider=provider,
            resource_id=resource_id,
            media_id=media_id,
            comment_id=comment_id,
            project=project,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        audit_args = dict(args)
        try:
            list_request = ListInlineMediaRequest(
                session_id=session_id,
                source_kind=source_kind,
                provider=provider,
                resource_id=resource_id,
                comment_id=comment_id,
                project=project,
                mailbox_id=mailbox_id,
                channel_id=channel_id,
                channel_target=channel_target,
            )
            matches = _resolve_inline_media(list_request, tool_name=ToolName.DOWNLOAD_INLINE_MEDIA)
            selected = next((item for item in matches if item[0].media_id == media_id), None)
            if selected is None:
                raise ToolError(f"Unknown inline media_id for resource: {media_id}")
            ref, raw = selected
            audit_args.update(_inline_media_ref_audit_payload(ref))
            session = context.policy.get_session(session_id)
            context.policy.require_inline_media_receive_enabled(
                session, source_kind=ref.source_kind
            )
            if ref.source_kind == "communication" and ref.provider == "teams":
                content, filename, mime_type = _download_inline_media_bytes(request, ref, raw)
            elif ref.source_kind == "communication":
                attachment_request = CommunicationDownloadAttachmentRequest(
                    session_id=session_id,
                    provider=provider,
                    message_id=resource_id,
                    attachment_id=media_id,
                    mailbox_id=mailbox_id,
                    channel_id=channel_id,
                    channel_target=channel_target,
                )
                attachment_ref = CommunicationAttachmentRef(
                    provider=ref.provider,
                    message_id=ref.resource_id,
                    attachment_id=ref.media_id,
                    filename=ref.filename,
                    mime_type=ref.mime_type,
                    byte_count=ref.byte_count,
                    mailbox_id=mailbox_id,
                    channel_id=channel_id,
                    channel_target=channel_target,
                    metadata=ref.metadata,
                )
                content, filename, mime_type = _provider_download_attachment(
                    attachment_request, attachment_ref
                )
            else:
                content, filename, mime_type = _download_inline_media_bytes(request, ref, raw)
            audit_args.update(
                {
                    "filename": filename,
                    "mime_type": mime_type,
                    "byte_count": len(content),
                    "comment_id": ref.comment_id,
                }
            )
            context.policy.require_inline_media_content(
                session,
                source_kind=ref.source_kind,
                filename=filename,
                mime_type=mime_type,
                byte_count=len(content),
            )
            artifact = _stage_inline_media_artifact(
                request,
                ref,
                content,
                filename=filename,
                mime_type=mime_type,
            )
            response = DownloadInlineMediaResponse(artifact=artifact)
            result = response.model_dump(mode="json")
            download_audit_result = {
                "source_kind": ref.source_kind,
                "provider": ref.provider,
                "resource_id": ref.resource_id,
                "media_id": ref.media_id,
                "comment_id": ref.comment_id,
                "artifact_id": artifact.artifact_id,
                "byte_count": artifact.byte_count,
                "mime_type": artifact.mime_type,
            }
            audit_success(
                session_id,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                audit_args,
                download_audit_result,
            )
            _audit_inline_media_operation(
                event_type="inline_media_download",
                session_id=session_id,
                request=request,
                media=[
                    ref.model_copy(
                        update={
                            "filename": filename,
                            "mime_type": mime_type,
                            "byte_count": len(content),
                        }
                    )
                ],
                result=download_audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DOWNLOAD_INLINE_MEDIA, audit_args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                audit_args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except ValueError as exc:
            audit_failure(
                session_id,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                audit_args,
                str(exc),
                decision="error",
                error_class="invalid_inline_media",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                audit_args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def communication_list_attachments(
        session_id: str,
        provider: str,
        message_id: str,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        request = CommunicationListAttachmentsRequest(
            session_id=session_id,
            provider=provider,
            message_id=message_id,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        try:
            attachments = _resolve_communication_attachments(request)
            response = CommunicationListAttachmentsResponse(
                provider=request.provider.strip().lower(),
                message_id=request.message_id,
                attachments=attachments,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_LIST_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider=response.provider,
                        mailbox_id=mailbox_id,
                        channel_id=channel_id,
                        channel_target=channel_target,
                    ),
                    "message_id": response.message_id,
                    "count": len(response.attachments),
                    "attachments": [
                        {
                            "attachment_id": item.attachment_id,
                            "filename": item.filename,
                            "mime_type": item.mime_type,
                            "byte_count": item.byte_count,
                            "size": item.byte_count,
                        }
                        for item in response.attachments
                    ],
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.COMMUNICATION_LIST_ATTACHMENTS, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_LIST_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_LIST_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def communication_download_attachment(
        session_id: str,
        provider: str,
        message_id: str,
        attachment_id: str,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        request = CommunicationDownloadAttachmentRequest(
            session_id=session_id,
            provider=provider,
            message_id=message_id,
            attachment_id=attachment_id,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        try:
            list_request = CommunicationListAttachmentsRequest(
                session_id=session_id,
                provider=provider,
                message_id=message_id,
                mailbox_id=mailbox_id,
                channel_id=channel_id,
                channel_target=channel_target,
            )
            attachments = _resolve_communication_attachments(
                list_request,
                tool_name=ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
            )
            ref = next((item for item in attachments if item.attachment_id == attachment_id), None)
            if ref is None:
                raise ToolError(f"Unknown attachment_id for message: {attachment_id}")
            content, filename, mime_type = _provider_download_attachment(request, ref)
            args.update(
                {
                    "filename": filename,
                    "mime_type": mime_type,
                    "byte_count": len(content),
                    "size": len(content),
                }
            )
            context.policy.require_communication_file_content(
                context.policy.get_session(session_id),
                filename=filename,
                mime_type=mime_type,
                byte_count=len(content),
            )
            artifact = _stage_communication_artifact(
                request,
                ref,
                content,
                filename=filename,
                mime_type=mime_type,
            )
            response = CommunicationDownloadAttachmentResponse(artifact=artifact)
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
                args,
                {
                    **_communication_target_audit_fields(
                        provider=artifact.provider,
                        mailbox_id=mailbox_id,
                        channel_id=channel_id,
                        channel_target=channel_target,
                    ),
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "artifact_id": artifact.artifact_id,
                    "byte_count": artifact.byte_count,
                    "size": artifact.byte_count,
                    "mime_type": artifact.mime_type,
                    "filename": artifact.filename,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    return {
        "list_inline_media": list_inline_media,
        "download_inline_media": download_inline_media,
        "communication_list_attachments": communication_list_attachments,
        "communication_download_attachment": communication_download_attachment,
    }
