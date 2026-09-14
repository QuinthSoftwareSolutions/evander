from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
import re
from time import monotonic
from typing import Any

import httpx

from evander.communication_artifacts import normalize_safe_filename
from evander.connectors.drivers import ConnectorInvocationError
from evander.connectors.models import (
    CONNECTOR_MAX_INLINE_BYTES,
    ConnectorArtifactResult,
    ConnectorDefinition,
    ConnectorInvocationResult,
    ConnectorOperationDefinition,
)
from evander.connectors.registry import ConnectorCallContext

from .client import (
    FreshserviceClient,
    validate_api_base_url,
    validate_attachment_hosts,
    validate_fixed_headers,
)


_ACTIONS = {
    "download_ticket_attachment",
    "get_ticket",
    "list_ticket_conversations",
    "search_tickets_by_subject",
}
_EXPECTED_RESULT_MODES = {
    "download_ticket_attachment": "artifact",
    "get_ticket": "json",
    "list_ticket_conversations": "json",
    "search_tickets_by_subject": "json",
}
_OPERATION_CONFIG_KEYS = {
    "download_ticket_attachment": {
        "action",
        "allowed_extensions",
        "allowed_mime_types",
        "conversation_page_limit",
    },
    "get_ticket": {"action"},
    "list_ticket_conversations": {"action"},
    "search_tickets_by_subject": {"action"},
}


class FreshserviceConnectorDriver:
    """Narrow read-only Freshservice API and attachment access.

    Attachment callers provide ticket and attachment identifiers, never a URL. The driver resolves
    the URL from Freshservice, validates its exact host against operator configuration, and never
    forwards Freshservice API or authentication headers to a different origin.
    """

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    def validate_definition(self, definition: ConnectorDefinition) -> None:
        _validated_base_url(definition)
        _fixed_headers(definition)
        if definition.auth_profile is None:
            raise ValueError("Freshservice connectors require a host authentication profile")
        _attachment_hosts(definition)
        for operation_name, operation in definition.operations.items():
            if operation.disabled:
                continue
            action = _operation_action(operation_name, operation)
            unknown_config_keys = set(operation.config) - _OPERATION_CONFIG_KEYS[action]
            if unknown_config_keys:
                raise ValueError(
                    f"Freshservice action {action!r} has unsupported config keys: "
                    f"{', '.join(sorted(unknown_config_keys))}"
                )
            if operation.access != "read":
                raise ValueError("Freshservice connector operations must use access='read'")
            expected_mode = _EXPECTED_RESULT_MODES[action]
            if operation.result_mode != expected_mode:
                raise ValueError(
                    f"Freshservice action {action!r} must use result_mode={expected_mode!r}"
                )
            if action == "download_ticket_attachment":
                configured_hosts = definition.config.get("attachment_host_allowlist")
                if not isinstance(configured_hosts, list) or not configured_hosts:
                    raise ValueError(
                        "Freshservice attachment downloads require config.attachment_host_allowlist"
                    )
                _download_policy(operation)

    async def invoke(
        self,
        *,
        context: ConnectorCallContext,
        connector_id: str,
        definition: ConnectorDefinition,
        operation_name: str,
        operation: ConnectorOperationDefinition,
        arguments: dict[str, Any],
    ) -> ConnectorInvocationResult:
        self.validate_definition(definition)
        action = _operation_action(operation_name, operation)
        started = monotonic()
        headers = {**_fixed_headers(definition), **context.auth_headers}
        timeout = operation.timeout_seconds
        async with httpx.AsyncClient(
            timeout=timeout,
            transport=self.transport,
            follow_redirects=False,
        ) as http_client:
            client = FreshserviceClient(
                http_client,
                base_url=_validated_base_url(definition),
                api_headers=headers,
                attachment_hosts=_attachment_hosts(definition),
            )
            if action == "get_ticket":
                ticket_id = _positive_int(arguments.get("ticket_id"), "ticket_id")
                value, status = await client.get_json(
                    f"/tickets/{ticket_id}",
                    max_bytes=int(operation.max_response_bytes or 0),
                )
                return _json_result(connector_id, operation_name, value, status, started=started)
            if action == "list_ticket_conversations":
                ticket_id = _positive_int(arguments.get("ticket_id"), "ticket_id")
                page = _bounded_int(arguments.get("page", 1), "page", minimum=1, maximum=10_000)
                per_page = _bounded_int(
                    arguments.get("per_page", 100),
                    "per_page",
                    minimum=1,
                    maximum=100,
                )
                value, status = await client.get_json(
                    f"/tickets/{ticket_id}/conversations",
                    query={"page": page, "per_page": per_page},
                    max_bytes=int(operation.max_response_bytes or 0),
                )
                return _json_result(connector_id, operation_name, value, status, started=started)
            if action == "search_tickets_by_subject":
                return await self._search_tickets_by_subject(
                    client=client,
                    connector_id=connector_id,
                    operation_name=operation_name,
                    operation=operation,
                    arguments=arguments,
                    started=started,
                )
            return await self._download_ticket_attachment(
                client=client,
                context=context,
                connector_id=connector_id,
                operation_name=operation_name,
                operation=operation,
                arguments=arguments,
                started=started,
            )

    async def _search_tickets_by_subject(
        self,
        *,
        client: FreshserviceClient,
        connector_id: str,
        operation_name: str,
        operation: ConnectorOperationDefinition,
        arguments: dict[str, Any],
        started: float,
    ) -> ConnectorInvocationResult:
        subject = _required_text(arguments.get("subject"), "subject", maximum=500)
        page = _bounded_int(arguments.get("page", 1), "page", minimum=1, maximum=10_000)
        per_page = _bounded_int(arguments.get("per_page", 100), "per_page", minimum=1, maximum=100)
        query: dict[str, object] = {
            "order_type": "desc",
            "page": page,
            "per_page": per_page,
        }
        if arguments.get("updated_since") is not None:
            query["updated_since"] = _freshservice_timestamp(arguments["updated_since"])
        if arguments.get("workspace_id") is not None:
            query["workspace_id"] = _bounded_int(
                arguments["workspace_id"], "workspace_id", minimum=0, maximum=2_147_483_647
            )
        value, status = await client.get_json(
            "/tickets",
            query=query,
            max_bytes=int(operation.max_response_bytes or 0),
        )
        rows = value.get("tickets") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            raise ConnectorInvocationError("Freshservice ticket search returned an invalid payload")
        matches = [
            row
            for row in rows
            if isinstance(row, dict)
            and str(row.get("subject") or "").strip().casefold() == subject.casefold()
        ]
        result = {
            "tickets": matches,
            "match_count": len(matches),
            "scanned_count": len(rows),
            "page": page,
            "per_page": per_page,
            "has_more": len(rows) == per_page,
        }
        return _json_result(connector_id, operation_name, result, status, started=started)

    async def _download_ticket_attachment(
        self,
        *,
        client: FreshserviceClient,
        context: ConnectorCallContext,
        connector_id: str,
        operation_name: str,
        operation: ConnectorOperationDefinition,
        arguments: dict[str, Any],
        started: float,
    ) -> ConnectorInvocationResult:
        ticket_id = _positive_int(arguments.get("ticket_id"), "ticket_id")
        attachment_id = _positive_int(arguments.get("attachment_id"), "attachment_id")
        attachment = await self._find_attachment(
            client=client,
            ticket_id=ticket_id,
            attachment_id=attachment_id,
            conversation_page_limit=_bounded_int(
                operation.config.get("conversation_page_limit", 10),
                "conversation_page_limit",
                minimum=1,
                maximum=100,
            ),
        )
        filename = normalize_safe_filename(
            _required_text(attachment.get("name"), "attachment name", maximum=255)
        )
        attachment_url = _required_text(
            attachment.get("attachment_url"), "attachment URL", maximum=8192
        )
        allowed_extensions, allowed_mime_types = _download_policy(operation)
        extension = PurePosixPath(filename).suffix.casefold()
        if extension not in allowed_extensions:
            raise ConnectorInvocationError(
                f"Freshservice attachment extension is not allowed: {extension or '<none>'}"
            )
        declared_mime = str(attachment.get("content_type") or "").split(";", 1)[0].strip().lower()
        if allowed_mime_types and declared_mime and declared_mime not in allowed_mime_types:
            raise ConnectorInvocationError(
                f"Freshservice attachment content type is not allowed: {declared_mime}"
            )
        content, response_mime, status = await client.download_attachment(
            attachment_url,
            max_bytes=int(operation.max_response_bytes or 0),
        )
        effective_mime = declared_mime or response_mime
        if allowed_mime_types and effective_mime not in allowed_mime_types:
            raise ConnectorInvocationError(
                "Freshservice attachment response content type is not allowed"
            )
        metadata = context.artifact_store.stage(
            provider=f"connector:{connector_id}",
            session_id=context.session_id,
            content=content,
            original_filename=filename,
            mime_type=effective_mime or None,
            metadata={
                "connector_id": connector_id,
                "operation": operation_name,
                "ticket_id": ticket_id,
                "attachment_id": attachment_id,
            },
        )
        return ConnectorInvocationResult(
            connector_id=connector_id,
            operation=operation_name,
            result_mode="artifact",
            artifact=ConnectorArtifactResult(
                artifact_id=metadata.artifact_id,
                filename=metadata.filename,
                mime_type=metadata.mime_type,
                byte_count=metadata.byte_count,
                sha256=metadata.sha256,
                expires_at=metadata.expires_at.isoformat(),
            ),
            upstream_status=status,
            duration_ms=int((monotonic() - started) * 1000),
        )

    async def _find_attachment(
        self,
        *,
        client: FreshserviceClient,
        ticket_id: int,
        attachment_id: int,
        conversation_page_limit: int,
    ) -> dict[str, Any]:
        ticket, _status = await client.get_json(
            f"/tickets/{ticket_id}",
            max_bytes=CONNECTOR_MAX_INLINE_BYTES,
        )
        ticket_payload = ticket.get("ticket") if isinstance(ticket, dict) else None
        match = _find_attachment_in(ticket_payload, attachment_id)
        if match is not None:
            return match
        for page in range(1, conversation_page_limit + 1):
            conversations, _status = await client.get_json(
                f"/tickets/{ticket_id}/conversations",
                query={"page": page, "per_page": 100},
                max_bytes=CONNECTOR_MAX_INLINE_BYTES,
            )
            rows = conversations.get("conversations") if isinstance(conversations, dict) else None
            if not isinstance(rows, list):
                raise ConnectorInvocationError(
                    "Freshservice conversations returned an invalid payload"
                )
            for conversation in rows:
                match = _find_attachment_in(conversation, attachment_id)
                if match is not None:
                    return match
            if len(rows) < 100:
                break
        raise ConnectorInvocationError(
            f"Freshservice attachment {attachment_id} was not found on ticket {ticket_id}"
        )


def _operation_action(operation_name: str, operation: ConnectorOperationDefinition) -> str:
    action = str(operation.config.get("action") or operation_name).strip().lower()
    if action not in _ACTIONS:
        raise ValueError(f"Unsupported Freshservice connector action: {action!r}")
    return action


def _validated_base_url(definition: ConnectorDefinition) -> str:
    return validate_api_base_url(definition.config.get("base_url"))


def _fixed_headers(definition: ConnectorDefinition) -> dict[str, str]:
    return validate_fixed_headers(definition.config.get("headers", {}))


def _attachment_hosts(definition: ConnectorDefinition) -> set[str]:
    return validate_attachment_hosts(
        _validated_base_url(definition),
        definition.config.get("attachment_host_allowlist", []),
    )


def _download_policy(operation: ConnectorOperationDefinition) -> tuple[set[str], set[str]]:
    raw_extensions = operation.config.get("allowed_extensions")
    if not isinstance(raw_extensions, list) or not raw_extensions:
        raise ValueError(
            "Freshservice attachment downloads require non-empty config.allowed_extensions"
        )
    extensions: set[str] = set()
    for value in raw_extensions:
        extension = str(value or "").strip().casefold()
        if not re.fullmatch(r"\.[a-z0-9]{1,16}", extension):
            raise ValueError(
                "Freshservice attachment allowed_extensions must contain values like '.pdf'"
            )
        extensions.add(extension)
    raw_mime_types = operation.config.get("allowed_mime_types", [])
    if not isinstance(raw_mime_types, list):
        raise ValueError("Freshservice attachment allowed_mime_types must be a list")
    mime_types = {str(value or "").strip().lower() for value in raw_mime_types}
    if any(not value or "/" not in value for value in mime_types):
        raise ValueError("Freshservice attachment allowed_mime_types contains an invalid value")
    return extensions, mime_types


def _find_attachment_in(payload: object, attachment_id: int) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    attachments = payload.get("attachments")
    if not isinstance(attachments, list):
        return None
    for attachment in attachments:
        if isinstance(attachment, dict) and str(attachment.get("id")) == str(attachment_id):
            return attachment
    return None


def _required_text(value: object, field_name: str, *, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConnectorInvocationError(f"Freshservice {field_name} is required")
    if len(text) > maximum or any(ord(char) < 32 for char in text):
        raise ConnectorInvocationError(f"Freshservice {field_name} is invalid")
    return text


def _positive_int(value: object, field_name: str) -> int:
    return _bounded_int(value, field_name, minimum=1, maximum=2_147_483_647)


def _bounded_int(value: object, field_name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ConnectorInvocationError(f"Freshservice {field_name} must be an integer")
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConnectorInvocationError(f"Freshservice {field_name} must be an integer") from exc
    if result < minimum or result > maximum:
        raise ConnectorInvocationError(
            f"Freshservice {field_name} must be between {minimum} and {maximum}"
        )
    return result


def _freshservice_timestamp(value: object) -> str:
    text = _required_text(value, "updated_since", maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConnectorInvocationError(
            "Freshservice updated_since must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ConnectorInvocationError("Freshservice updated_since must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_result(
    connector_id: str,
    operation_name: str,
    value: Any,
    status: int,
    *,
    started: float,
) -> ConnectorInvocationResult:
    return ConnectorInvocationResult(
        connector_id=connector_id,
        operation=operation_name,
        result_mode="json",
        value=value,
        upstream_status=status,
        duration_ms=int((monotonic() - started) * 1000),
    )
