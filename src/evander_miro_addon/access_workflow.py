"""Workflow-only board approval with authenticated message provenance."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

from pydantic import BaseModel, ConfigDict

from evander.effects.providers.base import EffectProviderBase
from evander.events.models import ConfigValue, WorkflowNodeEnvelope
from evander.queries.providers.base import QueryProviderBase
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.state_store import WorkflowStateStore

from .models import MiroError, board_url
from .permissions import board_reference, key_for, session_instance
from .runtime import provider_service

NAMESPACE = "miro-board-approval"
URL = re.compile(r"https://miro\.com/[^\s<>\"']+")


class MessageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.quoted = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"blockquote", "q", "script", "style"}:
            self.quoted += 1
        if not self.quoted:
            if tag in {"p", "br", "div"}:
                self.parts.append("\n")
            if tag == "a":
                href = dict(attrs).get("href", "")
                if URL.fullmatch(href):
                    self.links.append(href)

    def handle_endtag(self, tag):
        if tag in {"blockquote", "q", "script", "style"} and self.quoted:
            self.quoted -= 1

    def handle_data(self, data):
        if not self.quoted:
            self.parts.append(data)


def message_source(service, cursor, approver_user_id, *, require_fresh=True):
    if not cursor or not approver_user_id:
        raise MiroError(
            "invalid_input", "Chat approval requires a stored message and configured approver"
        )
    path = service.store.runtime_dir / "teams-inbox-store.db"
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
            row = db.execute(
                "SELECT payload_json FROM teams_inbox_messages WHERE cursor=?", (cursor,)
            ).fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        raise MiroError("forbidden", "Approval source message is unavailable")
    message = json.loads(row[0])
    expected = str(approver_user_id).removeprefix("user:")
    if (
        message.get("author_id") != expected
        or not message.get("chat_id")
        or message.get("metadata", {}).get("chat_type") != "oneOnOne"
    ):
        raise MiroError("forbidden", "Approval must come from the mapped user in their direct chat")
    session = service.session()
    ttl = 3600
    if service.policy is not None:
        ttl = service.policy.effective_policy_for_session(session).approval_ttl_seconds
    created = datetime.fromisoformat(str(message["created_at"]).replace("Z", "+00:00"))
    now = datetime.now(UTC)
    if require_fresh and (
        created < session.created_at
        or created > now + timedelta(minutes=1)
        or now - created > timedelta(seconds=ttl)
    ):
        raise MiroError("forbidden", "Approval source is expired or predates this session")
    parser = MessageText()
    parser.feed(message.get("content", ""))
    text = "".join(parser.parts).strip()
    boards = sorted(
        {board_reference(url.rstrip(".,;!?)]}")) for url in [*parser.links, *URL.findall(text)]}
    )
    return message, text, boards, ttl


class AccessDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    connection_id: ConfigValue
    session_id: ConfigValue
    board_id: ConfigValue = ""
    message_cursor: ConfigValue = ""
    approver_user_id: ConfigValue = ""
    language: ConfigValue = "en"
    outputs: dict | None = None


class GrantDefinition(AccessDefinition):
    message_cursor: ConfigValue
    approver_user_id: ConfigValue
    access: ConfigValue
    operation_key: ConfigValue


def materialize(node):
    model = GrantDefinition if node.type == "effect:miro:grant-board-access" else AccessDefinition
    return model.model_validate(
        {**node.config, "id": node.id, "type": node.type, "outputs": node.outputs}
    )


def scoped_state(context, service):
    graph = str(context.metadata.get("graph_id") or "").strip()
    if not graph:
        raise MiroError("invalid_input", "Chat board approval requires workflow graph identity")
    return WorkflowStateStore(service.store.runtime_dir), service.access.directory_path, graph


def current_pending(value, message, ready_at):
    try:
        expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
        requested = datetime.fromisoformat(value["requested_at"].replace("Z", "+00:00"))
        received = datetime.fromisoformat(message["created_at"].replace("Z", "+00:00"))
        return expires > datetime.now(UTC) and requested <= ready_at <= received
    except (KeyError, TypeError, ValueError):
        return False


def clear_pending(context, service, receipt):
    pending = receipt.get("pending")
    if pending:
        state, directory, graph = scoped_state(context, service)
        # Retain the consumed record so its CAS version keeps increasing. A
        # delete/recreate cycle would reuse versions and let an old receipt
        # accidentally consume a later question for this conversation.
        state.patch(
            directory,
            graph,
            NAMESPACE,
            pending["key"],
            patch={"ready": False},
            expected_version=pending["version"],
        )


def access_context(context, service, payload, *, require_fresh=True):
    session = service.session()
    record = service.authorize(payload["connection_id"])
    board = board_reference(payload["board_id"]) if payload.get("board_id") else None
    message = None
    text = ""
    pending = None
    pending_key = None
    state_version = None
    boards = []
    ttl = 3600
    if payload.get("message_cursor"):
        message, text, boards, ttl = message_source(
            service,
            payload["message_cursor"],
            payload.get("approver_user_id"),
            require_fresh=require_fresh,
        )
        pending_key = key_for(
            [
                record["key"],
                session.session_id,
                session_instance(session),
                message["author_id"],
                message["chat_id"],
            ]
        )
        store, directory, graph = scoped_state(context, service)
        result = store.get(directory, graph, NAMESPACE, pending_key)
        if result.record is not None:
            state_version = result.record.version
            value = result.record.value
            if (
                isinstance(value, dict)
                and value.get("ready") is True
                and value.get("session_created_at") == session_instance(session)
                and value.get("author_id") == message["author_id"]
                and value.get("chat_id") == message["chat_id"]
                and value.get("connection_key") == record["key"]
                and value.get("session_id") == session.session_id
                and current_pending(value, message, result.record.updated_at)
            ):
                pending = value
        # An explicit reference in this message always takes precedence over a
        # prior question. Pending state can resolve a board only for a linkless reply.
        if board and (
            (boards and boards != [board])
            or (not boards and (pending is None or pending.get("board_id") != board))
        ):
            raise MiroError(
                "forbidden", "Board does not match the source message or pending request"
            )
        if not board:
            if len(boards) == 1:
                board = boards[0]
            elif not boards and pending:
                board = pending["board_id"]
    grant = service.store.board_grant(record, session, board) if board else None
    return (
        session,
        record,
        board,
        grant,
        message,
        text,
        boards,
        pending,
        pending_key,
        state_version,
        ttl,
    )


def inspect_access(context, service, payload):
    session, record, board, grant, message, text, boards, pending, pending_key, version, ttl = (
        access_context(context, service, payload, require_fresh=False)
    )
    access = grant["access"] if grant else "none"
    result = {
        "session_id": session.session_id,
        "session_created_at": session_instance(session),
        "connection_id": record["config"]["id"],
        "board_id": board,
        "access": access,
        "writable": access == "edit",
        "has_board": bool(board),
        "ambiguous": len(boards) > 1,
        "url": board_url(board) if board else None,
        "message_text": text,
        "pending": pending,
        "pending_key": pending_key,
        "pending_version": version or 0,
        "pending_read": None,
        "pending_edit": None,
    }
    if message and board:
        for mode in ("read", "edit"):
            result["pending_" + mode] = {
                "connection_key": record["key"],
                "connection_id": record["config"]["id"],
                "session_id": session.session_id,
                "session_created_at": session_instance(session),
                "board_id": board,
                "access": mode,
                "author_id": message["author_id"],
                "chat_id": message["chat_id"],
                "source_message_cursor": payload["message_cursor"],
                "original_request": message.get("content", text),
                "requested_at": message["created_at"],
                "ready": False,
                "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat(),
            }
    swedish = str(payload.get("language", "")).lower() in {"sv", "sv-se", "svenska", "swedish"}
    link = result["url"] or ""
    result["question_read"] = (
        f"Får jag läsa den här Miro-tavlan? {link}"
        if swedish
        else f"May I read this Miro board? {link}"
    )
    result["question_edit"] = (
        f"Får jag läsa och redigera den här Miro-tavlan? {link}"
        if swedish
        else f"May I read and edit this Miro board? {link}"
    )
    return result


async def grant_access(context, service, payload):
    # The configured workflow controls the mapped user and target session. Only
    # immutable inbox data supplies the actual sender, chat and approved URL.
    session = service.session()
    record = service.authorize(payload["connection_id"])
    if (
        not payload.get("operation_key")
        or not payload.get("message_cursor")
        or not payload.get("approver_user_id")
    ):
        raise MiroError(
            "invalid_input", "Grant requires an operation key, stored message and mapped user"
        )
    receipt_key = key_for(
        [record["key"], session.session_id, session_instance(session), payload["message_cursor"]]
    )
    fingerprint = key_for(
        [
            service.access.directory_path,
            context.metadata.get("graph_id"),
            session.session_id,
            session_instance(session),
            payload["connection_id"],
            payload.get("board_id"),
            payload["access"],
            payload["message_cursor"],
            payload["approver_user_id"],
        ]
    )
    async with service.store.lock("board-approval-operation:" + receipt_key):
        receipt = service.store.get("chat-grant-outcome", receipt_key)
        if receipt:
            if receipt["fingerprint"] != fingerprint:
                raise MiroError(
                    "conflict", "Grant operation key was already used with different arguments"
                )
            result = dict(receipt["result"])
            current = service.store.board_grant(record, session, result["board_id"])
            if not current or (payload["access"] == "edit" and current["access"] != "edit"):
                result.update(status="revoked", resume_request="")
            clear_pending(context, service, receipt)
            return result
        session, record, board, old, message, text, boards, pending, pending_key, version, ttl = (
            access_context(context, service, payload)
        )
        if message is None or not text or not board or len(boards) > 1:
            raise MiroError(
                "forbidden", "Approval needs one exact board and an authenticated source message"
            )
        access = payload["access"]
        if not boards and (not pending or pending["access"] != access):
            raise MiroError(
                "forbidden", "Confirmation does not match a current pending board request"
            )
        source = {
            "provider": "teams",
            "message_cursor": payload["message_cursor"],
            "message_id": message["message_id"],
            "author_id": message["author_id"],
            "chat_id": message["chat_id"],
            "workflow_node_id": context.metadata.get("node_id"),
        }
        grant = service.store.save_board_grant(
            record,
            session,
            board,
            access,
            actor="user:" + message["author_id"],
            source=source,
            operation_key="chat:" + receipt_key,
            merge=True,
        )
        current = service.store.board_grant(record, service.session(), board)
        status = (
            "applied"
            if current and (current["access"] == "edit" or access == "read")
            else "revoked"
        )
        swedish = str(payload.get("language", "")).lower() in {"sv", "sv-se", "svenska", "swedish"}
        confirmation = (
            ("Du har godkänt redigering" if access == "edit" else "Du har godkänt läsåtkomst")
            if swedish
            else ("Edit access approved" if access == "edit" else "Read access approved")
        )
        result = {
            "status": status,
            "grant_id": grant["grant_id"],
            "session_id": session.session_id,
            "board_id": board,
            "access": grant["access"],
            "url": board_url(board),
            "confirmation": confirmation + ": " + board_url(board),
            "resume_request": pending.get("original_request", "")
            if pending and pending["board_id"] == board
            else "",
        }
        receipt = {
            "fingerprint": fingerprint,
            "result": result,
            "pending": {"key": pending_key, "version": version}
            if pending and pending["board_id"] == board
            else None,
        }
        # Save the durable result before consuming the question. A retry after a
        # crash can then acknowledge the existing grant and finish consumption.
        service.store.put("chat-grant-outcome", receipt_key, receipt)
        clear_pending(context, service, receipt)
        return result


class AccessProviderMixin:
    @classmethod
    def validate_definition(cls, node):
        return materialize(node) if type(node) is WorkflowNodeEnvelope else node

    def execute(self, *, resolved_config=None):
        definition = getattr(self, "query_definition", None) or self.effect_definition
        payload = definition.model_dump(exclude={"id", "type", "outputs"})
        payload.update(resolved_config or {})
        service = provider_service(
            self.runtime_context, node_id=definition.id, session_id=payload["session_id"]
        )
        if definition.type == "query:miro:board-access":
            return inspect_access(self.runtime_context, service, payload)
        return asyncio.run(grant_access(self.runtime_context, service, payload))


class BoardAccessProvider(AccessProviderMixin, QueryProviderBase):
    pass


class GrantBoardAccessProvider(AccessProviderMixin, EffectProviderBase):
    pass


def register_access_nodes(queries, effects, catalog, resolvers):
    for kind, node, provider in (
        ("query", "query:miro:board-access", BoardAccessProvider),
        ("effect", "effect:miro:grant-board-access", GrantBoardAccessProvider),
    ):
        (queries if kind == "query" else effects)[node] = provider
        resolvers[node] = materialize
        fields = [
            WorkflowEditorFieldDefinition(
                key=k,
                label=label,
                kind="string",
                required=required,
                bindable=True,
                description=description,
            )
            for k, label, required, description in (
                (
                    "connection_id",
                    "Connection",
                    True,
                    "Miro connection available to the target session.",
                ),
                (
                    "session_id",
                    "Agent session",
                    True,
                    "Exact live agent session receiving board access.",
                ),
                (
                    "board_id",
                    "Board ID or URL",
                    False,
                    "Exact board reference; chat approvals must match the original message or pending request.",
                ),
                (
                    "message_cursor",
                    "Source Teams message",
                    kind == "effect",
                    "Immutable inbox cursor from the original trigger, never model-generated.",
                ),
                (
                    "approver_user_id",
                    "Mapped user ID",
                    kind == "effect",
                    "Trusted user-to-assistant mapping; checked against the stored message author.",
                ),
                (
                    "language",
                    "Reply language",
                    False,
                    "User language for approval prompts and confirmations; default English.",
                ),
            )
        ]
        if kind == "effect":
            fields.extend(
                [
                    WorkflowEditorFieldDefinition(
                        key="access",
                        label="Access",
                        kind="string",
                        required=True,
                        bindable=True,
                        description="read or edit; edit includes read.",
                    ),
                    WorkflowEditorFieldDefinition(
                        key="operation_key",
                        label="Operation key",
                        kind="string",
                        required=True,
                        bindable=True,
                        description="Stable key derived from the source message; retries never restore revoked access.",
                    ),
                ]
            )
        catalog.append(
            WorkflowEditorNodeCatalogEntry(
                kind=kind,
                type=node,
                label="Miro check board access"
                if kind == "query"
                else "Miro grant session board access",
                description="Session board permissions with authenticated approval provenance; no Miro network call.",
                fields=fields,
            )
        )
        outputs = {
            "session_id": {"type": "string"},
            "session_created_at": {"type": "string"},
            "connection_id": {"type": "string"},
            "board_id": {"type": ["string", "null"]},
            "access": {"type": "string"},
            "writable": {"type": "boolean"},
            "has_board": {"type": "boolean"},
            "ambiguous": {"type": "boolean"},
            "url": {"type": ["string", "null"]},
            "message_text": {"type": "string"},
            "pending": {"type": ["object", "null"]},
            "pending_key": {"type": ["string", "null"]},
            "pending_version": {"type": "integer"},
            "pending_read": {"type": ["object", "null"]},
            "pending_edit": {"type": ["object", "null"]},
            "question_read": {"type": "string"},
            "question_edit": {"type": "string"},
            "status": {"type": "string"},
            "grant_id": {"type": "string"},
            "confirmation": {"type": "string"},
            "resume_request": {"type": "string"},
        }
        grant_fields = {
            "status",
            "grant_id",
            "session_id",
            "board_id",
            "access",
            "url",
            "confirmation",
            "resume_request",
        }
        outputs = {
            k: v
            for k, v in outputs.items()
            if (
                k in grant_fields
                if kind == "effect"
                else k not in {"status", "grant_id", "confirmation", "resume_request"}
            )
        }
        register_node_output_schema(node_type=node, outputs=outputs)
