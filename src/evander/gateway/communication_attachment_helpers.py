"""Communication attachment resolution helpers for gateway tools."""

from __future__ import annotations

from evander.conversation_context import ConversationContextStore
from evander.integration_services.teams import teams_conversation_key

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp.exceptions import ToolError

from evander.gateway.communication_artifact_helpers import CommunicationArtifactHelpers
from evander.inline_media import normalize_inline_media_provider
from evander.models import (
    CommunicationAttachmentRef,
    CommunicationDownloadAttachmentRequest,
    CommunicationFileArtifact,
    CommunicationListAttachmentsRequest,
    DiscordCommandEnvelope,
    TeamsMessageEnvelope,
    ToolName,
)
from evander.policy import PolicyError
from evander.runtime import RuntimeContext
from evander.services import (
    normalize_teams_target,
    parse_teams_target,
    teams_target_from_message,
)


def _filter_discord_commands_for_session(
    context: RuntimeContext,
    session_id: str,
    session: Any,
    raw_commands: list[DiscordCommandEnvelope],
    args: dict[str, Any],
    audit_failure_cb: Any,
) -> tuple[list[dict[str, Any]], int, str | None]:
    commands: list[dict[str, Any]] = []
    denied_count = 0
    next_cursor = raw_commands[-1].command_id if raw_commands else args.get("since_cursor")
    for command in raw_commands:
        try:
            context.policy.require_discord_command_source(session, command)
        except PolicyError as exc:
            denied_count += 1
            audit_failure_cb(
                session_id,
                ToolName.DISCORD_GET_COMMANDS,
                {
                    **args,
                    "command_id": command.command_id,
                    "source_metadata": command.metadata,
                },
                str(exc),
            )
            continue
        commands.append(command.model_dump(mode="json"))
    return commands, denied_count, next_cursor


def _filter_teams_messages_for_session(
    context: RuntimeContext,
    session_id: str,
    session: Any,
    raw_messages: list[TeamsMessageEnvelope],
    args: dict[str, Any],
    audit_failure_cb: Any,
) -> tuple[list[dict[str, Any]], int, str | None]:
    messages: list[dict[str, Any]] = []
    denied_count = 0
    next_cursor = raw_messages[-1].cursor if raw_messages else args.get("since_cursor")
    if args.get("channel_target"):
        channel_target = normalize_teams_target(str(args["channel_target"]).strip())
        raw_messages = ConversationContextStore(context.config.runtime_dir).filter_history(
            raw_messages, session_id=session_id, conversation_key=teams_conversation_key(channel_target)
        )
    for message in raw_messages:
        try:
            context.policy.require_teams_message_source(session, message)
        except PolicyError as exc:
            denied_count += 1
            audit_failure_cb(
                session_id,
                ToolName.TEAMS_GET_MESSAGES,
                {
                    **args,
                    "cursor": message.cursor,
                    "source_metadata": message.metadata,
                },
                str(exc),
            )
            continue
        messages.append(
            _teams_message_response(message, include_metadata=bool(args.get("include_metadata")))
        )
    return messages, denied_count, next_cursor


def _teams_message_response(
    message: TeamsMessageEnvelope, *, include_metadata: bool = False
) -> dict[str, Any]:
    if include_metadata:
        return message.model_dump(mode="json")

    # Authorize against the original envelope above. Raw Graph payloads remain
    # in storage for attachment resolution and explicit full-metadata reads.
    result = message.model_dump(mode="json", exclude={"metadata"})
    source = message.metadata
    raw = source.get("message")
    raw = raw if isinstance(raw, dict) else {}
    metadata = {
        key: source[key]
        for key in ("source", "session_id", "author_name", "reply_to_id", "web_url", "content_type")
        if source.get(key) is not None
    }
    for key, graph_key in (("reply_to_id", "replyToId"), ("web_url", "webUrl")):
        if key not in metadata and raw.get(graph_key):
            metadata[key] = raw[graph_key]
    body = raw.get("body")
    if "content_type" not in metadata and isinstance(body, dict) and body.get("contentType"):
        metadata["content_type"] = body["contentType"]
    sender = raw.get("from")
    if "author_name" not in metadata and isinstance(sender, dict):
        for kind in ("user", "application", "device"):
            identity = sender.get(kind)
            if isinstance(identity, dict) and identity.get("displayName"):
                metadata["author_name"] = identity["displayName"]
                break
    for key in ("subject", "mentions", "reactions", "lastEditedDateTime", "deletedDateTime"):
        if raw.get(key):
            metadata[key] = raw[key]
    attachments = source.get("attachments", raw.get("attachments"))
    if isinstance(attachments, list) and attachments:
        metadata["attachments"] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            summary = {
                key: attachment[key]
                for key in ("id", "name", "contentType", "size", "attachment_kind")
                if attachment.get(key) is not None
            }
            for key, alias in (
                ("id", "attachment_id"),
                ("name", "filename"),
                ("contentType", "mime_type"),
            ):
                if key not in summary and attachment.get(alias) is not None:
                    summary[key] = attachment[alias]
            # A card's content is the message itself, unlike a file's binary data.
            if str(summary.get("contentType", "")).startswith("application/vnd.microsoft.card."):
                if attachment.get("content") is not None:
                    summary["content"] = attachment["content"]
            metadata["attachments"].append(summary)
    result["metadata"] = metadata
    return result


def _teams_cursor_for_target(
    cursor: str | None,
    *,
    channel_target: str,
    field_name: str,
) -> str | None:
    if cursor is None:
        return None
    normalized_cursor = str(cursor or "").strip()
    if not normalized_cursor:
        raise ToolError(f"{field_name} must be non-empty when provided")
    if "/" not in normalized_cursor:
        return f"{channel_target}/{normalized_cursor}"
    cursor_target, message_id = normalized_cursor.rsplit("/", 1)
    if not cursor_target or not message_id:
        raise ToolError(
            f"{field_name} must be a Teams cursor or message id scoped by channel_target"
        )
    try:
        normalized_cursor_target = normalize_teams_target(cursor_target)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if normalized_cursor_target != channel_target:
        raise ToolError(f"{field_name} target must match channel_target")
    return f"{channel_target}/{message_id}"


@dataclass(frozen=True)
class CommunicationAttachmentHelpers:
    context: RuntimeContext
    artifact_helpers: CommunicationArtifactHelpers

    def coerce_attachment_refs(
        self,
        provider: str,
        message_id: str,
        raw_attachments: Any,
        *,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> list[CommunicationAttachmentRef]:
        if raw_attachments is None:
            return []
        refs: list[CommunicationAttachmentRef] = []
        for index, raw in enumerate(raw_attachments if isinstance(raw_attachments, list) else []):
            if isinstance(raw, CommunicationAttachmentRef):
                refs.append(raw)
                continue
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if not isinstance(raw, dict):
                continue
            attachment_id = str(
                raw.get("attachment_id")
                or raw.get("id")
                or raw.get("content_id")
                or raw.get("name")
                or index
            ).strip()
            filename = str(raw.get("filename") or raw.get("name") or attachment_id).strip()
            if not attachment_id or not filename:
                continue
            byte_count_raw = raw.get("byte_count", raw.get("size", raw.get("contentSize")))
            byte_count = None
            if byte_count_raw is not None:
                try:
                    byte_count = int(byte_count_raw)
                except (TypeError, ValueError):
                    byte_count = None
            mime_type = raw.get("mime_type") or raw.get("content_type") or raw.get("contentType")
            safe_metadata = {
                key: value
                for key, value in raw.items()
                if key
                not in {
                    "url",
                    "proxy_url",
                    "download_url",
                    "web_url",
                    "content",
                    "content_bytes",
                    "content_base64",
                    "data",
                    "authorization",
                    "token",
                    "access_token",
                }
            }
            refs.append(
                CommunicationAttachmentRef(
                    provider=normalize_inline_media_provider(provider),
                    message_id=message_id,
                    attachment_id=attachment_id,
                    filename=filename,
                    mime_type=str(mime_type).strip() if mime_type else None,
                    byte_count=byte_count,
                    mailbox_id=mailbox_id,
                    channel_id=channel_id,
                    channel_target=channel_target,
                    metadata=safe_metadata,
                )
            )
        return refs

    def attachments_from_metadata(
        self,
        provider: str,
        message_id: str,
        metadata: dict[str, Any],
        *,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> list[CommunicationAttachmentRef]:
        raw_attachments = metadata.get("attachments")
        if raw_attachments is None and isinstance(metadata.get("message"), dict):
            raw_attachments = metadata["message"].get("attachments")
        return self.coerce_attachment_refs(
            provider,
            message_id,
            raw_attachments,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )

    def clean_teams_channel_input(self, value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if "://" in raw:
            parsed = urlparse(raw)
            path_parts = [part for part in parsed.path.split("/") if part]
            if "channel" in path_parts:
                index = path_parts.index("channel")
                if index + 1 < len(path_parts):
                    return unquote(path_parts[index + 1]).strip()
        cleaned = unquote(raw).strip().strip("/")
        if cleaned.startswith("chat:"):
            return cleaned
        if "/" in cleaned:
            _team_id, _separator, channel_id = cleaned.partition("/")
            return channel_id.strip()
        return cleaned

    def teams_message_records_for_id(self, message_id: str) -> list[TeamsMessageEnvelope]:
        records: list[TeamsMessageEnvelope] = []
        direct = self.context.teams_messages.get(message_id)
        if direct is not None:
            records.append(direct)
        seen_cursors = {record.cursor for record in records}
        for record in self.context.teams_messages.read_all().values():
            if record.message_id != message_id or record.cursor in seen_cursors:
                continue
            records.append(record)
            seen_cursors.add(record.cursor)
        return records

    def teams_target_for_message_record(self, record: TeamsMessageEnvelope) -> str:
        return teams_target_from_message(
            team_id=record.team_id,
            channel_id=record.channel_id,
            chat_id=record.chat_id,
            metadata=record.metadata,
        )

    def teams_target_channel_id(self, target: str) -> str | None:
        parsed = parse_teams_target(target)
        return parsed.channel_id if parsed.kind == "channel" else None

    def select_allowlisted_teams_target(
        self,
        session: Any,
        candidates: list[str],
        *,
        message_id: str,
    ) -> str:
        normalized: list[str] = []
        for candidate in candidates:
            try:
                normalized.append(
                    self.context.policy.require_teams_channel_target(session, candidate)
                )
            except PolicyError:
                continue
            except ValueError:
                continue
        unique = sorted(set(normalized))
        if len(unique) == 1:
            return unique[0]
        if not unique:
            raise PolicyError(f"Teams target is not allowlisted for session {session.session_id}")
        raise PolicyError(
            f"Teams message {message_id} matches multiple allowlisted targets; pass channel_target"
        )

    def resolve_teams_target_from_message(
        self,
        session: Any,
        *,
        message_id: str,
        channel_id: str | None = None,
    ) -> str:
        requested_channel_id = self.clean_teams_channel_input(channel_id)
        candidates: list[str] = []
        for record in self.teams_message_records_for_id(message_id):
            try:
                target = self.teams_target_for_message_record(record)
            except ValueError:
                continue
            if (
                requested_channel_id
                and self.teams_target_channel_id(target) != requested_channel_id
            ):
                continue
            candidates.append(target)
        if candidates:
            return self.select_allowlisted_teams_target(
                session,
                candidates,
                message_id=message_id,
            )

        if requested_channel_id:
            for (
                normalized
            ) in self.context.policy.policy_config.resolved_teams_channel_targets_for_session(
                session,
                capability="read",
            ):
                if self.teams_target_channel_id(normalized) == requested_channel_id:
                    candidates.append(normalized)
            if candidates:
                return self.select_allowlisted_teams_target(
                    session,
                    candidates,
                    message_id=message_id,
                )

        raise PolicyError(
            "Teams channel_target or an allowlisted stored Teams message is required "
            f"for message {message_id}"
        )

    def resolve_teams_attachment_target(
        self,
        session: Any,
        request: CommunicationListAttachmentsRequest,
    ) -> str:
        raw_target = str(request.channel_target or "").strip()
        if raw_target:
            cleaned_target = unquote(raw_target).strip().strip("/")
            try:
                target = self.context.policy.require_teams_channel_target(session, cleaned_target)
                requested_channel_id = self.clean_teams_channel_input(request.channel_id)
                if (
                    requested_channel_id
                    and self.teams_target_channel_id(target) != requested_channel_id
                ):
                    raise PolicyError(
                        "Teams channel_id does not match the requested channel_target"
                    )
                return target
            except PolicyError as exc:
                channel_id = self.clean_teams_channel_input(raw_target)
                if channel_id and (
                    channel_id != cleaned_target
                    or ("/" not in cleaned_target and not cleaned_target.startswith("chat:"))
                ):
                    return self.resolve_teams_target_from_message(
                        session,
                        message_id=request.message_id,
                        channel_id=channel_id,
                    )
                raise exc

        requested_channel_id = self.clean_teams_channel_input(request.channel_id)
        if requested_channel_id:
            return self.resolve_teams_target_from_message(
                session,
                message_id=request.message_id,
                channel_id=requested_channel_id,
            )
        return self.resolve_teams_target_from_message(session, message_id=request.message_id)

    def resolve_communication_attachments(
        self,
        request: CommunicationListAttachmentsRequest,
        *,
        tool_name: ToolName = ToolName.COMMUNICATION_LIST_ATTACHMENTS,
    ) -> list[CommunicationAttachmentRef]:
        provider = request.provider.strip().lower()
        if provider == "discord":
            session = self.context.policy.require_tool(request.session_id, tool_name)
            self.context.policy.require_communication_file_receive_enabled(session)
            channel_id = str(request.channel_id or "").strip()
            service = self.context.discord_service_for_session(request.session_id)
            if hasattr(service, "list_attachments"):
                resolved_channel_id = self.context.policy.require_communication_file_scope(
                    session, provider=provider, channel_id=channel_id
                )
                return self.coerce_attachment_refs(
                    provider,
                    request.message_id,
                    service.list_attachments(request.message_id, channel_id=resolved_channel_id),
                    channel_id=resolved_channel_id,
                )
            record = self.context.discord_commands.get(request.message_id)
            if record is None:
                raise ToolError(f"Unknown Discord message_id: {request.message_id}")
            resolved_channel_id = self.context.policy.require_communication_file_scope(
                session, provider=provider, channel_id=channel_id or record.channel_id
            )
            return self.attachments_from_metadata(
                provider,
                request.message_id,
                record.metadata,
                channel_id=resolved_channel_id,
            )
        if provider == "teams":
            session = self.context.policy.require_tool(request.session_id, tool_name)
            self.context.policy.require_communication_file_receive_enabled(session)
            target = self.resolve_teams_attachment_target(session, request)
            service = self.context.teams_service_for_session(request.session_id)
            if hasattr(service, "list_attachments"):
                return self.coerce_attachment_refs(
                    provider,
                    request.message_id,
                    service.list_attachments(request.message_id, channel_target=target),
                    channel_id=self.teams_target_channel_id(target),
                    channel_target=target,
                )
            record = self.context.teams_messages.get(request.message_id)
            if record is None:
                record = next(
                    (
                        message
                        for message in self.context.teams_messages.read_all().values()
                        if message.message_id == request.message_id
                    ),
                    None,
                )
            if record is None:
                raise ToolError(f"Unknown Teams message_id: {request.message_id}")
            return self.attachments_from_metadata(
                provider,
                request.message_id,
                record.metadata,
                channel_id=self.teams_target_channel_id(target),
                channel_target=target,
            )
        if provider == "email":
            session = self.context.policy.require_tool(request.session_id, tool_name)
            self.context.policy.require_communication_file_receive_enabled(session)
            mailbox_id = str(request.mailbox_id or "").strip()
            self.context.policy.require_communication_file_scope(
                session, provider=provider, mailbox_id=mailbox_id
            )
            service = self.context.email_service_for_session(request.session_id)
            message = service.get_message(
                mailbox_id=mailbox_id,
                message_id=request.message_id,
            )
            self.context.policy.require_email_folder_for_mailbox(
                session, mailbox_id, message.folder
            )
            if hasattr(service, "list_attachments"):
                return self.coerce_attachment_refs(
                    provider,
                    request.message_id,
                    service.list_attachments(mailbox_id=mailbox_id, message_id=request.message_id),
                    mailbox_id=mailbox_id,
                )
            if getattr(message, "attachments", None):
                return self.coerce_attachment_refs(
                    provider,
                    request.message_id,
                    message.attachments,
                    mailbox_id=mailbox_id,
                )
            return self.attachments_from_metadata(
                provider,
                request.message_id,
                message.metadata,
                mailbox_id=mailbox_id,
            )
        raise ToolError("provider must be one of: discord, teams, email")

    def provider_download_attachment(
        self,
        request: CommunicationDownloadAttachmentRequest,
        ref: CommunicationAttachmentRef,
    ) -> tuple[bytes, str, str | None]:
        provider = request.provider.strip().lower()
        service: Any
        kwargs: dict[str, Any] = {
            "message_id": request.message_id,
            "attachment_id": request.attachment_id,
        }
        if provider == "discord":
            service = self.context.discord_service_for_session(request.session_id)
            kwargs["channel_id"] = ref.channel_id or request.channel_id
        elif provider == "teams":
            service = self.context.teams_service_for_session(request.session_id)
            kwargs["channel_target"] = ref.channel_target or request.channel_target
        elif provider == "email":
            service = self.context.email_service_for_session(request.session_id)
            kwargs["mailbox_id"] = request.mailbox_id
        else:
            raise ToolError("provider must be one of: discord, teams, email")
        if hasattr(service, "download_attachment"):
            raw = service.download_attachment(**kwargs)
            if isinstance(raw, bytes):
                return raw, ref.filename, ref.mime_type
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if isinstance(raw, dict):
                data = raw.get("content") or raw.get("content_bytes") or raw.get("bytes")
                if isinstance(data, str):
                    if raw.get("encoding") == "base64" or raw.get("content_base64"):
                        data_bytes = base64.b64decode(raw.get("content_base64") or data)
                    else:
                        data_bytes = data.encode("utf-8")
                elif isinstance(data, bytes):
                    data_bytes = data
                else:
                    data_bytes = base64.b64decode(str(raw.get("content_base64") or ""))
                return (
                    data_bytes,
                    str(raw.get("filename") or ref.filename),
                    str(raw.get("mime_type") or raw.get("content_type") or ref.mime_type or "")
                    or None,
                )
        raw_payload = ref.metadata.get("content_base64") or ref.metadata.get("content")
        if isinstance(raw_payload, str) and raw_payload:
            try:
                return base64.b64decode(raw_payload), ref.filename, ref.mime_type
            except Exception:
                return raw_payload.encode("utf-8"), ref.filename, ref.mime_type
        raise ToolError("Attachment content is not available from the configured provider adapter")

    def stage_communication_artifact(
        self,
        request: CommunicationDownloadAttachmentRequest,
        ref: CommunicationAttachmentRef,
        content: bytes,
        *,
        filename: str,
        mime_type: str | None,
    ) -> CommunicationFileArtifact:
        store = self.artifact_helpers.communication_artifact_store()
        source_channel = (
            ref.channel_target or ref.channel_id or request.channel_id or request.channel_target
        )
        record = store.stage(
            provider=request.provider,
            session_id=request.session_id,
            content=content,
            original_filename=filename,
            mime_type=mime_type,
            source_message_id=request.message_id,
            source_attachment_id=request.attachment_id,
            source_channel=source_channel,
            source_mailbox=request.mailbox_id,
            metadata={
                "attachment_ref": ref.model_dump(mode="json"),
            },
        )
        return self.artifact_helpers.artifact_to_model(record)
