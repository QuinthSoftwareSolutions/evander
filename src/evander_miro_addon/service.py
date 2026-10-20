"""Shared semantics for agent tools, workflows and operator inspection."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlsplit

from evander.policy import PolicyError
from evander.repositories.session_manifests import JsonSessionManifestRepository

from .canvas_authoring import AUTHORING_CHECKS, validate_connectors
from .client import COMMENT_PAGE_SIZE, MiroClient, comment_page, svg_from_result, unwrap
from .layout_review import review_layout
from .models import (
    MAX_CONTENT_BYTES,
    OPERATIONS,
    WRITE_OPERATIONS,
    CanvasScope,
    Connection,
    MiroError,
    OperationInput,
    board_url,
    identifier,
    in_directory,
    require,
)
from .permissions import session_instance
from .state import MiroStore, digest, encoded


def canvas(svg: str) -> tuple[str, list[str]]:
    if len(svg.encode()) > MAX_CONTENT_BYTES or re.search(r"<!\s*(DOCTYPE|ENTITY)", svg, re.I):
        raise MiroError(
            "invalid_canvas", "Canvas exceeds the size limit or contains XML declarations"
        )
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        raise MiroError("invalid_canvas", "Canvas must be a valid SVG document") from None
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise MiroError("invalid_canvas", "Canvas root must be SVG")
    ids = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() in {"script", "iframe", "object", "embed"}:
            raise MiroError("invalid_canvas", "Active content is not supported in a canvas")
        for name, value in element.attrib.items():
            local = name.rsplit("}", 1)[-1].lower()
            if local.startswith("on") or re.match(r"\s*(javascript|data|file):", value, re.I):
                raise MiroError("invalid_canvas", "Active content is not supported in a canvas")
        if item_id := element.get("data-miro-id"):
            ids.append(identifier(item_id))
        deletion = element.get("data-deleted")
        if deletion not in {None, "true", "false"} or (
            deletion == "true" and not element.get("data-miro-id")
        ):
            raise MiroError(
                "invalid_canvas", "Deletion requires a stable item ID and a boolean marker"
            )
    if len(ids) != len(set(ids)):
        raise MiroError("invalid_canvas", "Canvas contains duplicate Miro item IDs")
    return ET.canonicalize(svg), sorted(ids)


def item_states(snapshot: dict | None) -> dict[str, str]:
    """Compare content across selections without treating snapshot scope as an edit.

    Keep unidentified SVG content too: when a change cannot be attributed to stable
    items we conservatively emit a change instead of hiding possible human input.
    """
    if not snapshot:
        return {}
    if snapshot["kind"] == "comments":
        return {str(item["id"]): digest(item) for item in snapshot["data"]["comments"]}
    root = ET.fromstring(snapshot["data"]["svg"])

    def signature(element):
        element = deepcopy(element)
        for parent in element.iter():
            for child in list(parent):
                if child.get("data-miro-id"):
                    parent.remove(child)
        return digest(ET.canonicalize(ET.tostring(element, encoding="unicode")))

    return {"$canvas": signature(root)} | {
        element.get("data-miro-id"): signature(element)
        for element in root.iter()
        if element.get("data-miro-id")
    }


def changed_items(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}


def authored_item_states(
    operation: str, args: OperationInput, baseline: dict | None, observed: dict, result: Any
) -> dict[str, str | None]:
    """Retain only requested transitions confirmed by the observation.

    The readback can contain concurrent human edits, even on a requested item.
    New IDs also need the mutation's SVG receipt; a board-wide readback alone
    cannot establish which items this call created.
    """
    if not baseline:
        return {}
    before, after = item_states(baseline), item_states(observed)
    expected: dict[str, str | None] = {}
    if observed["kind"] == "canvas":
        requested = item_states({"kind": "canvas", "data": {"svg": args.svg}})
        root = ET.fromstring(args.svg)
        for element in root.iter():
            item_id = element.get("data-miro-id")
            if item_id and item_id in before:
                value = None if element.get("data-deleted") == "true" else requested[item_id]
                if value != before[item_id]:
                    expected[item_id] = value

        def without_ids(element):
            element = deepcopy(element)
            # A nested text span's tail belongs to its parent, not this element.
            element.tail = None
            for child in element.iter():
                child.attrib.pop("data-miro-id", None)
            return ET.canonicalize(ET.tostring(element, encoding="unicode"))

        additions = Counter(
            without_ids(element)
            for element in root.iter()
            if element is not root and not element.get("data-miro-id")
        )
        if additions:
            try:
                receipt_svg, _ = canvas(svg_from_result(result))
            except MiroError:
                # Missing or unrecognized mutation evidence must not hide human input.
                receipt_svg = "<svg/>"
            receipt = item_states({"kind": "canvas", "data": {"svg": receipt_svg}})
            for element in ET.fromstring(receipt_svg).iter():
                item_id = element.get("data-miro-id")
                if item_id and item_id not in before:
                    content = without_ids(element)
                    if additions[content]:
                        additions[content] -= 1
                        expected[item_id] = receipt[item_id]
    elif operation == "resolve_comment":
        for comment in baseline["data"]["comments"]:
            if str(comment["id"]) == args.comment_id:
                expected[args.comment_id] = digest(comment | {"resolved": args.resolved})
    # Reply responses without a documented, verifiable message receipt remain
    # external_or_unknown instead of attributing a whole changed thread to us.
    return {
        key: value
        for key, value in expected.items()
        if before.get(key) != value and after.get(key) == value
    }


@dataclass(frozen=True)
class Access:
    directory_path: str
    actor: str
    session_id: str | None = None
    workflow: bool = False
    admin: bool = False
    session_created_at: str | None = None


class MiroService:
    def __init__(
        self,
        store: MiroStore,
        access: Access,
        client_factory=MiroClient,
        *,
        session_repository=None,
        policy=None,
    ):
        self.store, self.access, self.client_factory = store, access, client_factory
        self.sessions = session_repository or JsonSessionManifestRepository(
            store.runtime_dir / "sessions.json"
        )
        self.policy = policy
        self._operation_tool = None
        self._operation_arguments = {}
        self._session_instance = access.session_created_at

    def session(self):
        if not self.access.session_id:
            raise MiroError("forbidden", "Miro board operations require an agent session")
        session = self.sessions.get_session(self.access.session_id)
        if session is None or session.expires_at <= datetime.now(UTC):
            raise MiroError("forbidden", "Miro session is missing or expired")
        if not in_directory(self.access.directory_path, session.directory_path) or (
            not self.access.workflow and session.directory_path != self.access.directory_path
        ):
            raise MiroError("forbidden", "Session is outside this workflow directory")
        instance = session_instance(session)
        if self._session_instance is not None and self._session_instance != instance:
            raise MiroError("forbidden", "Miro session was replaced; obtain a new board grant")
        self._session_instance = instance
        return session

    def require_tool_arguments(self, tool, arguments):
        if self.access.admin:
            return
        session = self.session()
        if self.policy is not None:
            session = self.policy.require_tool(session.session_id, tool)
            if session_instance(session) != self._session_instance:
                raise MiroError("forbidden", "Miro session was replaced; obtain a new board grant")
            self.policy.require_tool_arguments(session, tool, arguments)
        elif tool not in {str(getattr(t, "value", t)) for t in session.allowed_tools}:
            raise MiroError("forbidden", "Miro tool is not allowed for this session")

    @asynccontextmanager
    async def open_client(self, record):
        service = self
        async with self.client_factory(self.store, record).open() as client:

            class GuardedClient:
                mutation_dispatched = False

                def prepare(self, operation, **values):
                    return client.prepare(operation, **values)

                async def call(self, operation, **values):
                    current = service.authorize(
                        record["config"]["id"],
                        values.get("board_id"),
                        write=operation in WRITE_OPERATIONS,
                    )
                    if current["key"] != record["key"]:
                        raise MiroError(
                            "connection_changed", "Connection binding changed; retry the operation"
                        )
                    tool = service._operation_tool or "miro_" + (
                        "get_canvas_format" if operation == "load_format" else operation
                    )
                    service.require_tool_arguments(tool, service._operation_arguments)
                    # A failure before this point cannot have sent a mutation.
                    # Keep this true through readback so a later denial remains uncertain.
                    if operation in WRITE_OPERATIONS:
                        self.mutation_dispatched = True
                    return await client.call(operation, **values)

            yield GuardedClient()

    def list_connections(self) -> dict:
        session = None if self.access.admin else self.session()
        scope = session.directory_path if session is not None else self.access.directory_path
        connections = []
        for record in self.store.effective_connections(scope):
            config = Connection.model_validate(record["config"])
            if not config.enabled or (self.access.workflow and not config.allow_workflows):
                continue
            grants = self.store.session_grants(record, session) if session is not None else []
            connections.append(
                {
                    "connection_id": config.id,
                    "name": config.name,
                    "directory_path": config.directory_path,
                    "inherited": config.directory_path != scope,
                    "allow_create_boards": config.allow_create_boards,
                    "approved_board_count": len(grants),
                    "writable_board_count": sum(g["access"] == "edit" for g in grants),
                }
            )
        return {"connections": connections}

    def authorize(self, connection_id: str, board_id: str | None = None, *, write=False) -> dict:
        session = None if self.access.admin else self.session()
        scope = session.directory_path if session is not None else self.access.directory_path
        record = self.store.resolve_connection(scope, connection_id)
        config = Connection.model_validate(record["config"])
        if not config.enabled:
            raise MiroError("forbidden", "Miro connection is disabled")
        if self.access.workflow and not config.allow_workflows:
            raise MiroError("forbidden", "Workflow access to this Miro connection is disabled")
        if board_id is not None and not self.access.admin:
            grant = self.store.board_grant(record, session, board_id)
            if grant is None:
                raise MiroError(
                    "board_not_approved", "This board is not approved for this agent session"
                )
            if write and grant["access"] != "edit":
                raise MiroError(
                    "board_read_only",
                    "This session has read-only access; edit access needs approval",
                )
        return record

    async def execute(self, operation: str, arguments: dict) -> dict:
        if operation not in OPERATIONS:
            raise MiroError("invalid_input", "Unknown Miro operation")
        args = OperationInput.model_validate(arguments)
        self._operation_tool = "miro_" + operation
        self._operation_arguments = args.model_dump(mode="json")
        self.require_tool_arguments(self._operation_tool, self._operation_arguments)
        if operation not in {
            "list_boards",
            "get_canvas_format",
            "create_board",
            "get_operation",
            "get_snapshot",
        }:
            require(args.board_id, "board_id")
        record = self.authorize(
            args.connection_id, args.board_id, write=operation in WRITE_OPERATIONS
        )
        if operation == "list_boards":
            return {
                "boards": [
                    {
                        "board_id": g["board_id"],
                        "name": g.get("name", ""),
                        "access": g["access"],
                        "writable": g["access"] == "edit",
                        "url": board_url(g["board_id"]),
                    }
                    for g in self.store.session_grants(record, self.session())
                ]
            }
        if operation == "get_snapshot":
            return self.saved_snapshot(record, require(args.snapshot_id, "snapshot_id"))
        if operation == "get_operation":
            return self.saved_operation(record, require(args.operation_id, "operation_id"))
        if operation in WRITE_OPERATIONS:
            return await self.write(record, operation, args)
        async with self.open_client(record) as client:
            if operation == "get_canvas_format":
                composer = {"step": args.step, "is_repository": args.is_repository}
                format_args = {
                    "format_name": args.format_name,
                    "notation": args.notation,
                    "is_repository": args.is_repository,
                }
                # Validate both calls before spending a call on the composer.
                client.prepare(operation, **composer)
                if args.format_name:
                    client.prepare("load_format", **format_args)
                data = await client.call(operation, **composer)
                specific = (
                    await client.call("load_format", **format_args) if args.format_name else None
                )
                return {
                    "authoring_checks": list(AUTHORING_CHECKS),
                    "format": data,
                    "format_specific": specific,
                }
            if operation == "search_canvas":
                return await self.search(client, args)
            return await self.read(client, record, operation, args)

    def saved_snapshot(self, record: dict, snapshot_id: str) -> dict:
        snapshot = self.store.get("snapshot", snapshot_id)
        if not snapshot or snapshot["connection_key"] != record["key"]:
            raise MiroError("forbidden", "Snapshot is unavailable in this connection")
        self.authorize(record["config"]["id"], snapshot["board_id"])
        return self.public_snapshot(snapshot)

    def saved_operation(self, record: dict, operation_id: str) -> dict:
        operation = self.store.get("operation", operation_id)
        if not operation or operation["connection_key"] != record["key"]:
            raise MiroError("forbidden", "Operation is unavailable in this connection")
        operation_directory = operation.get("directory_path")
        if self.access.admin and (
            not isinstance(operation_directory, str)
            or not in_directory(self.access.directory_path, operation_directory)
        ):
            raise MiroError("forbidden", "Operation is outside this operator directory")
        if not self.access.admin:
            session = self.session()
            if operation.get("session_id") != session.session_id or operation.get(
                "session_created_at"
            ) != session_instance(session):
                raise MiroError("forbidden", "Operation belongs to a different session instance")
            if operation.get("board_id"):
                self.authorize(record["config"]["id"], operation["board_id"])
        return {
            key: value
            for key, value in operation.items()
            if key not in {"request", "connection_key"}
        }

    @staticmethod
    def public_snapshot(snapshot: dict) -> dict:
        result = {key: value for key, value in snapshot.items() if key != "connection_key"} | {
            "url": board_url(snapshot["board_id"], snapshot["scope"].get("frame_id")),
            "external_content": True,
        }
        if snapshot["kind"] == "canvas":
            # Derived diagnostics must not change stored snapshot identities or
            # stale-write checks. They are advisory, not write success criteria.
            result["layout_review"] = review_layout(snapshot["data"]["svg"]) | {
                "snapshot_id": snapshot["snapshot_id"],
            }
        return result

    async def search(self, client, args: OperationInput) -> dict:
        if args.result_mode != "overview" and not args.patterns:
            raise MiroError("invalid_input", "Search areas and matches require patterns")
        if args.result_mode == "overview" and (
            args.target_id is not None or args.cursor is not None
        ):
            raise MiroError("invalid_input", "Search target_id and cursor require areas or matches")
        data = unwrap(
            await client.call(
                "search_canvas",
                board_id=args.board_id,
                result_mode=args.result_mode,
                patterns=args.patterns,
                target_id=args.target_id,
                cursor=args.cursor,
            )
        )
        if not isinstance(data, dict) or any(
            type(data.get(key)) is not int or data[key] < 0
            for key in ("total_items", "total_results")
        ):
            raise MiroError(
                "unsupported_response", "Miro did not return readable canvas search results"
            )
        # Search pages and overview samples are navigation aids, not complete snapshots.
        return {
            "connection_id": args.connection_id,
            "board_id": args.board_id,
            "url": board_url(args.board_id),
            "data": data,
            "external_content": True,
        }

    async def canvas_selection(self, client, args: OperationInput) -> OperationInput:
        if args.canvas_scope is not None or args.frame_id or args.item_ids:
            return args
        overview = await self.search(
            client, OperationInput(connection_id=args.connection_id, board_id=args.board_id)
        )
        if overview["data"]["total_items"] != 0:
            raise MiroError(
                "scope_required",
                "Use miro_search_canvas (query:miro:search-canvas in workflows), then read with "
                "canvas_scope {x, y, width, height}, frame_id, or item_ids. "
                "An overview is not a complete board read.",
            )
        # Only a confirmed empty board gets an initial area. Record it explicitly;
        # neither readback nor polling may later reinterpret it as the whole board.
        return args.model_copy(
            update={"canvas_scope": CanvasScope(x=-500, y=-500, width=1000, height=1000)}
        )

    @staticmethod
    def selection(args: OperationInput) -> dict:
        scope = {"frame_id": args.frame_id, "item_ids": sorted(args.item_ids)}
        if args.canvas_scope is not None:
            scope["canvas_scope"] = args.canvas_scope.model_dump()
        return scope

    async def read(self, client, record: dict, operation: str, args: OperationInput) -> dict:
        if operation == "read_canvas":
            args = await self.canvas_selection(client, args)
        elif args.canvas_scope is not None:
            raise MiroError("invalid_input", "canvas_scope is only supported for canvas operations")
        scope = self.selection(args)
        values = {"board_id": args.board_id, "frame_id": args.frame_id, "item_ids": args.item_ids}
        if operation == "read_canvas":
            if args.canvas_scope is not None:
                values.update(
                    {"scope_" + key: value for key, value in scope["canvas_scope"].items()}
                )
            elif args.frame_id and not args.item_ids:
                # A frame URL alone is not a scoped read; selecting the frame pulls
                # in its descendants under the current Miro canvas contract.
                values["item_ids"] = [args.frame_id]
            response = await client.call(operation, **values)
            svg, ids = canvas(svg_from_result(response))
            data = {"svg": svg, "item_ids": ids}
            kind = "canvas"
        else:
            comments, offset = [], 0
            for _ in range(20):
                page, next_offset = comment_page(
                    await client.call(operation, **values, limit=COMMENT_PAGE_SIZE, offset=offset),
                    offset=offset,
                )
                comments.extend(page)
                if len(encoded(comments).encode()) > MAX_CONTENT_BYTES:
                    raise MiroError(
                        "response_too_large",
                        "Comment selection is too large; select a smaller frame",
                    )
                if next_offset is None:
                    break
                offset = next_offset
            else:
                raise MiroError(
                    "incomplete_response",
                    "Comment pagination limit reached; checkpoint was not advanced",
                )
            ids = [str(comment["id"]) for comment in comments]
            if len(ids) != len(set(ids)):
                raise MiroError(
                    "incomplete_response", "Miro returned duplicate comment IDs; retry the read"
                )
            data = {"comments": sorted(comments, key=lambda item: str(item["id"]))}
            kind = "comments"
        snapshot = self.store.snapshot(record, args.board_id, scope, kind, data)
        return self.public_snapshot(snapshot)

    def _baseline(self, record: dict, args: OperationInput, kind: str) -> dict:
        saved = self.store.get("snapshot", require(args.snapshot_id, "snapshot_id"))
        if (
            not saved
            or saved["connection_key"] != record["key"]
            or saved["board_id"] != args.board_id
            or saved["kind"] != kind
        ):
            raise MiroError("conflict", "Snapshot does not belong to this board and operation")
        if saved["scope"] != self.selection(args):
            raise MiroError("conflict", "Update selection must match the source snapshot")
        return saved

    async def write(self, record: dict, operation: str, args: OperationInput) -> dict:
        require(args.operation_key, "operation_key")
        request = args.model_dump(mode="json")
        # Preserve fingerprints of durable writes created before scoped reads and
        # search were added. New, explicitly supplied values still identify a write.
        defaults = OperationInput(connection_id=args.connection_id).model_dump(mode="json")
        for key in (
            "canvas_scope",
            "result_mode",
            "patterns",
            "target_id",
            "cursor",
            "step",
            "notation",
            "is_repository",
        ):
            if request[key] == defaults[key]:
                request.pop(key)
        op_id = digest([record["key"], args.operation_key])
        fingerprint = digest([operation, request, self.access.actor])
        lock = "board:" + args.board_id if args.board_id else "create-board:" + record["key"]
        # The operation key is connection-wide, including writes on different boards.
        async with self.store.lock("operation:" + op_id), self.store.lock(lock):
            current_record = self.authorize(args.connection_id, args.board_id, write=True)
            if current_record["key"] != record["key"]:
                raise MiroError(
                    "connection_changed", "Connection binding changed; retry the operation"
                )
            record = current_record
            if operation == "create_board" and not record["config"]["allow_create_boards"]:
                raise MiroError("forbidden", "Board creation is not granted on this connection")
            previous = self.store.get("operation", op_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise MiroError(
                        "conflict",
                        "Operation key was already used with different arguments or actor",
                    )
                if previous["status"] == "running":
                    previous.update(
                        status="uncertain", error="Interrupted operation requires reconciliation"
                    )
                    self.store.put("operation", op_id, previous)
                return self.saved_operation(record, op_id)
            if (
                operation == "create_board"
                and len(self.store.session_grants(record, self.session())) >= 200
            ):
                raise MiroError(
                    "invalid_input", "Session board limit reached; revoke unused grants first"
                )
            unsettled = [
                item
                for item in self.store.list("operation")
                if item["status"] in {"running", "uncertain"}
                and (
                    (args.board_id and item.get("board_id") == args.board_id)
                    or (not args.board_id and item["connection_key"] == record["key"])
                )
            ]
            if unsettled:
                raise MiroError(
                    "uncertain_write",
                    "An earlier write needs reconciliation before this board can change",
                )
            if operation in {"create_canvas", "update_canvas"}:
                svg, ids = canvas(require(args.svg, "svg"))
                validate_connectors(svg)
            async with self.open_client(record) as client:
                baseline = None
                values: dict[str, Any] = {"board_id": args.board_id, "frame_id": args.frame_id}
                if operation in {"create_canvas", "update_canvas"}:
                    values["svg"] = svg
                    if operation == "create_canvas" and ids:
                        raise MiroError(
                            "invalid_canvas",
                            "New canvas content must not reference existing Miro IDs",
                        )
                    if operation == "update_canvas":
                        baseline = self._baseline(record, args, "canvas")
                        old_ids = set(baseline["data"]["item_ids"])
                        if not old_ids:
                            raise MiroError(
                                "conflict", "Update requires a snapshot with stable Miro item IDs"
                            )
                        if set(ids) - old_ids:
                            raise MiroError(
                                "forbidden",
                                "Updated canvas references items outside the source snapshot",
                            )
                        deletes = any(
                            element.get("data-deleted") == "true"
                            for element in ET.fromstring(svg).iter()
                        )
                        if deletes and not args.allow_delete:
                            raise MiroError(
                                "conflict",
                                "Update would delete marked items; explicitly allow deletion",
                            )
                elif operation == "create_board":
                    values = {"title": require(args.title, "title")}
                else:
                    baseline = self._baseline(record, args, "comments")
                    comment_id = require(args.comment_id, "comment_id")
                    if comment_id not in {str(item["id"]) for item in baseline["data"]["comments"]}:
                        raise MiroError("forbidden", "Comment is outside the source snapshot")
                    values["comment_id"] = comment_id
                    if operation == "reply_comment":
                        values["text"] = require(args.text, "text")
                    else:
                        values["resolved"] = args.resolved
                # Negotiate schema and read the same selection BEFORE recording a sent write.
                client.prepare(operation, **values)
                if baseline:
                    current = await self.read(client, record, "read_" + baseline["kind"], args)
                    if current["snapshot_id"] != baseline["snapshot_id"]:
                        raise MiroError(
                            "conflict",
                            "Miro content changed since the snapshot; read it again before revising",
                        )
                elif operation == "create_canvas":
                    baseline = await self.read(client, record, "read_canvas", args)
                    args = OperationInput.model_validate(args.model_dump() | baseline["scope"])
                entry = {
                    "operation_id": op_id,
                    "connection_key": record["key"],
                    "operation": operation,
                    "board_id": args.board_id,
                    "actor": self.access.actor,
                    "session_id": self.access.session_id,
                    "session_created_at": session_instance(self.session())
                    if not self.access.admin
                    else None,
                    "directory_path": self.access.directory_path,
                    "status": "running",
                    "fingerprint": fingerprint,
                    "request": request,
                    "source_refs": args.source_refs,
                    "started_at": time.time(),
                    "baseline_snapshot_id": baseline["snapshot_id"] if baseline else None,
                }
                if baseline and baseline["kind"] == "canvas":
                    entry["readback_scope"] = baseline["scope"]
                self.store.put("operation", op_id, entry)
                try:
                    result = await client.call(operation, **values)
                    entry["upstream_acknowledged"] = True
                    if operation == "create_board":
                        entry["board_id"] = self._created_board_id(result)
                        self.store.save_board_grant(
                            record,
                            self.session(),
                            entry["board_id"],
                            "edit",
                            actor=self.access.actor,
                            source={"kind": "board_creation", "operation_id": op_id},
                            operation_key="created-board:" + op_id,
                            merge=True,
                            name=args.title,
                        )
                        args = args.model_copy(update={"board_id": entry["board_id"]})
                    # Persist acknowledgement and any created board reference before
                    # readback, including if the process is interrupted during search.
                    self.store.put("operation", op_id, entry)
                    read_op = (
                        "read_comments"
                        if operation in {"reply_comment", "resolve_comment"}
                        else "read_canvas"
                    )
                    if read_op == "read_canvas":
                        args = await self.canvas_selection(client, args)
                        entry["readback_scope"] = self.selection(args)
                        self.store.put("operation", op_id, entry)
                    observed = await self.read(client, record, read_op, args)
                    authored = authored_item_states(operation, args, baseline, observed, result)
                    entry.update(
                        status="applied",
                        result_snapshot_id=observed["snapshot_id"],
                        url=observed["url"],
                        affected_item_ids=sorted(authored),
                        authored_item_states=authored,
                        completed_at=time.time(),
                    )
                    if "layout_review" in observed:
                        entry["layout_review"] = observed["layout_review"]
                except Exception as exc:
                    # Even a remote tool error can follow a partial multi-item mutation.
                    pre_send = not client.mutation_dispatched or (
                        isinstance(exc, MiroError)
                        and exc.code
                        in {
                            "quota_exhausted",
                            "connection_changed",
                            "unsupported_capability",
                        }
                        and not entry.get("upstream_acknowledged")
                    )
                    entry.update(
                        status="not_applied" if pre_send else "uncertain",
                        error=exc.code
                        if isinstance(exc, MiroError)
                        else "forbidden"
                        if isinstance(exc, PolicyError)
                        else "upstream_unavailable",
                    )
                self.store.put("operation", op_id, entry)
                return self.saved_operation(record, op_id)

    @staticmethod
    def _created_board_id(result: Any) -> str:
        result = unwrap(result)
        if isinstance(result, dict):
            board = result.get("board", result)
            if isinstance(board, dict):
                value = board.get("id") or board.get("board_id") or board.get("boardId")
                if value:
                    return identifier(value)
                url = (
                    board.get("miro_url")
                    or board.get("url")
                    or board.get("board_url")
                    or board.get("boardUrl")
                )
                if isinstance(url, str):
                    parts = urlsplit(url)
                    if (
                        parts.scheme == "https"
                        and parts.netloc == "miro.com"
                        and parts.path.startswith("/app/board/")
                    ):
                        return identifier(unquote(parts.path.split("/")[3]))
        raise MiroError(
            "unsupported_response", "Board creation returned no recognized board reference"
        )

    async def reconcile(self, connection_id: str, operation_id: str) -> dict:
        record = self.authorize(connection_id)
        self.saved_operation(record, operation_id)
        entry = self.store.get("operation", operation_id)
        if not entry.get("board_id"):
            return {
                "operation": self.saved_operation(record, operation_id),
                "guidance": "Inspect the Miro team for the created board; record its ID when resolving the operation.",
            }
        read_op = (
            "read_comments"
            if entry["operation"] in {"reply_comment", "resolve_comment"}
            else "read_canvas"
        )
        async with self.store.lock("board:" + entry["board_id"]):
            # A live writer or another reconciliation may have completed while
            # this request waited. Preserve its durable outcome and evidence.
            entry = self.store.get("operation", operation_id)
            args = OperationInput.model_validate(
                entry["request"] | {"board_id": entry["board_id"]} | entry.get("readback_scope", {})
            )
            async with self.open_client(record) as client:
                observed = await self.read(client, record, read_op, args)
            if read_op == "read_canvas":
                entry["readback_scope"] = observed["scope"]
            entry["reconciliation_snapshot_id"] = observed["snapshot_id"]
            entry["reconciled_at"] = time.time()
            if entry["status"] == "running":
                entry["status"] = "uncertain"
            self.store.put("operation", operation_id, entry)
            return {"operation": self.saved_operation(record, operation_id), "observed": observed}

    async def resolve_operation(
        self, connection_id: str, operation_id: str, outcome: str, evidence: str
    ) -> dict:
        if (
            not self.access.admin
            or outcome not in {"applied", "not_applied"}
            or not evidence.strip()
        ):
            raise MiroError(
                "forbidden", "Operator resolution requires an outcome and readback evidence"
            )
        record = self.authorize(connection_id)
        self.saved_operation(record, operation_id)
        entry = self.store.get("operation", operation_id)
        lock = (
            "board:" + entry["board_id"]
            if entry.get("board_id")
            else "create-board:" + record["key"]
        )
        async with self.store.lock(lock):
            entry = self.store.get("operation", operation_id)
            if entry["status"] not in {"running", "uncertain"}:
                raise MiroError("conflict", "Only uncertain operations can be resolved")
            acknowledged_creation = (
                entry["operation"] == "create_board"
                and entry.get("board_id")
                and entry.get("upstream_acknowledged")
                and outcome == "applied"
            )
            # Operators can confirm a known, acknowledged creation even after the
            # board is deleted or its sharing changes. Canvas/comment mutations
            # still need readback to establish what was actually applied.
            if (
                entry.get("board_id")
                and not entry.get("reconciliation_snapshot_id")
                and not acknowledged_creation
            ):
                raise MiroError("conflict", "Read back the board before resolving this operation")
            entry.update(
                status=outcome,
                resolution_evidence=evidence[:2000],
                resolved_by=self.access.actor,
                completed_at=time.time(),
            )
            self.store.put("operation", operation_id, entry)
            return self.saved_operation(record, operation_id)

    def own_changes(self, previous_id: str | None, observed: dict, since: float) -> bool:
        previous = self.store.get("snapshot", previous_id) if previous_id else None
        if not previous:
            return False
        before, after = item_states(previous), item_states(observed)
        changed = changed_items(before, after)
        if not changed:
            return False
        tracked = dict(before)
        operations = sorted(self.store.list("operation"), key=lambda op: op.get("completed_at", 0))
        for op in operations:
            if (
                op.get("status") != "applied"
                or op.get("board_id") != observed["board_id"]
                or op.get("completed_at", 0) < since
                or not op.get("baseline_snapshot_id")
                or not op.get("result_snapshot_id")
            ):
                continue
            source = self.store.get("snapshot", op["baseline_snapshot_id"])
            result = self.store.get("snapshot", op["result_snapshot_id"])
            if not source or not result or result["kind"] != observed["kind"]:
                continue
            op_before = item_states(source)
            authored = op.get("authored_item_states", {})
            for key in changed & authored.keys():
                # A human edit between observations breaks the chain. Do not hide
                # that edit merely because a later Evander write read it back.
                if tracked.get(key) == op_before.get(key):
                    if authored[key] is not None:
                        tracked[key] = authored[key]
                    else:
                        tracked.pop(key, None)
        return all(tracked.get(key) == after.get(key) for key in changed)

    async def poll(
        self,
        subscription_id: str,
        arguments: dict,
        *,
        kind: str,
        interval: int = 900,
        include_self: bool = False,
        emit_initial: bool = False,
    ) -> list[dict]:
        if kind not in {"canvas", "comments"} or interval < 60:
            raise MiroError(
                "invalid_input",
                "Polling requires canvas/comments and an interval of at least 60 seconds",
            )
        args = OperationInput.model_validate(arguments)
        self._operation_tool = "miro_read_" + kind
        self._operation_arguments = args.model_dump(mode="json")
        self.require_tool_arguments(self._operation_tool, self._operation_arguments)
        require(args.board_id, "board_id")
        self.authorize(args.connection_id, args.board_id)
        key = digest(
            [
                self.access.directory_path,
                subscription_id,
                self.access.session_id,
                session_instance(self.session()) if not self.access.admin else None,
                arguments,
                kind,
                include_self,
                emit_initial,
            ]
        )
        async with self.store.lock("subscription:" + key):
            record = self.authorize(args.connection_id, args.board_id)
            state = self.store.get("subscription", key)
            if not state or state["connection_key"] != record["key"]:
                # A shadowing connection cannot read the old connection's snapshots.
                # Keep event IDs monotonic so delayed acknowledgments remain harmless.
                state = {
                    "key": key,
                    "subscription_id": subscription_id,
                    "session_id": self.access.session_id,
                    "session_created_at": session_instance(self.session())
                    if not self.access.admin
                    else None,
                    "directory_path": self.access.directory_path,
                    "connection_key": record["key"],
                    "connection_id": args.connection_id,
                    "board_id": args.board_id,
                    "sequence": state["sequence"] if state else 0,
                    "snapshot_id": None,
                    "checked_at": 0,
                    "pending": [],
                }
            if state["pending"]:
                return state["pending"]
            if time.time() < state["checked_at"] + interval:
                return []
            if time.time() < state.get("retry_after", 0):
                return []
            try:
                async with self.store.lock("board:" + args.board_id):
                    current_record = self.authorize(args.connection_id, args.board_id)
                    if current_record["key"] != record["key"]:
                        raise MiroError(
                            "connection_changed",
                            "Connection binding changed; retry the observation",
                        )
                    record = current_record
                    if kind == "canvas" and state.get("read_scope"):
                        args = OperationInput.model_validate(
                            args.model_dump() | state["read_scope"]
                        )
                    async with self.open_client(record) as client:
                        observed = await self.read(client, record, "read_" + kind, args)
                    if self.authorize(args.connection_id, args.board_id)["key"] != record["key"]:
                        raise MiroError(
                            "connection_changed",
                            "Connection binding changed; retry the observation",
                        )
            except MiroError as exc:
                state.update(last_error=exc.code, last_attempt_at=time.time())
                # Backoff attempts without advancing the observation checkpoint.
                state["retry_after"] = time.time() + interval
                self.store.put("subscription", key, state)
                raise
            changed = state["snapshot_id"] != observed["snapshot_id"]
            own = changed and self.own_changes(state["snapshot_id"], observed, state["checked_at"])
            if changed and (state["snapshot_id"] or emit_initial) and (include_self or not own):
                state["sequence"] += 1
                event = {
                    "event_id": f"miro:{key}:{state['sequence']}",
                    "subscription_key": key,
                    "connection_id": args.connection_id,
                    "board_id": args.board_id,
                    "frame_id": args.frame_id,
                    "scope": observed["scope"],
                    "kind": kind,
                    "snapshot_id": observed["snapshot_id"],
                    "previous_snapshot_id": state["snapshot_id"],
                    "url": observed["url"],
                    "origin": "evander" if own else "external_or_unknown",
                    "observed_at": time.time(),
                    "external_content": True,
                }
                state["pending"] = [event]
            state.update(
                snapshot_id=observed["snapshot_id"],
                read_scope=observed["scope"],
                checked_at=observed["observed_at"],
                last_error=None,
                retry_after=0,
            )
            self.store.put("subscription", key, state)
            return state["pending"]

    async def acknowledge(self, subscription_key: str, event_id: str) -> None:
        async with self.store.lock("subscription:" + subscription_key):
            state = self.store.get("subscription", subscription_key)
            if not state or state["directory_path"] != self.access.directory_path:
                raise MiroError("forbidden", "Subscription is outside this workflow directory")
            if not self.access.admin:
                session = self.session()
                if state.get("session_id") != session.session_id or state.get(
                    "session_created_at"
                ) != session_instance(session):
                    raise MiroError(
                        "forbidden", "Subscription belongs to a different session instance"
                    )
                self.authorize(state.get("connection_id", ""), state["board_id"])
            state["pending"] = [
                event for event in state["pending"] if event["event_id"] != event_id
            ]
            self.store.put("subscription", subscription_key, state)
