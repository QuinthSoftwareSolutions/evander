"""Shared semantics for agent tools, workflows and operator inspection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from copy import deepcopy
import re
import time
from typing import Any
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from .client import COMMENT_PAGE_SIZE, MiroClient, comment_page, svg_from_result, unwrap
from .models import (
    Connection,
    MAX_CONTENT_BYTES,
    MiroError,
    OPERATIONS,
    OperationInput,
    WRITE_OPERATIONS,
    board_url,
    identifier,
    in_directory,
    require,
)
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


class MiroService:
    def __init__(self, store: MiroStore, access: Access, client_factory=MiroClient):
        self.store, self.access, self.client_factory = store, access, client_factory

    def list_connections(self) -> dict:
        connections = []
        for record in self.store.effective_connections(self.access.directory_path):
            config = Connection.model_validate(record["config"])
            if not config.enabled:
                continue
            if not self.access.admin and not self.access.session_id:
                if not self.access.workflow or not config.allow_workflows:
                    continue
            connections.append(
                {
                    "connection_id": config.id,
                    "name": config.name,
                    "directory_path": config.directory_path,
                    "inherited": config.directory_path != self.access.directory_path,
                    "allow_create_boards": config.allow_create_boards,
                    "approved_board_count": len(config.boards),
                    "writable_board_count": sum(board.writable for board in config.boards),
                }
            )
        return {"connections": connections}

    def authorize(self, connection_id: str, board_id: str | None = None, *, write=False) -> dict:
        record = self.store.resolve_connection(self.access.directory_path, connection_id)
        config = Connection.model_validate(record["config"])
        if not config.enabled:
            raise MiroError("forbidden", "Miro connection is disabled")
        # Agent calls arrive through the session's normal MCP tool policy.
        if not self.access.admin and not self.access.session_id:
            if not self.access.workflow or not config.allow_workflows:
                raise MiroError("forbidden", "Workflow access to this Miro connection is disabled")
        if board_id is not None:
            binding = next((board for board in config.boards if board.board_id == board_id), None)
            if binding is None or (write and not binding.writable):
                raise MiroError("forbidden", "Board is outside the granted Miro access")
        return record

    async def execute(self, operation: str, arguments: dict) -> dict:
        if operation not in OPERATIONS:
            raise MiroError("invalid_input", "Unknown Miro operation")
        args = OperationInput.model_validate(arguments)
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
                    {**b, "url": board_url(b["board_id"])} for b in record["config"]["boards"]
                ]
            }
        if operation == "get_snapshot":
            return self.saved_snapshot(record, require(args.snapshot_id, "snapshot_id"))
        if operation == "get_operation":
            return self.saved_operation(record, require(args.operation_id, "operation_id"))
        if operation in WRITE_OPERATIONS:
            return await self.write(record, operation, args)
        async with self.client_factory(self.store, record).open() as client:
            if operation == "get_canvas_format":
                data = await client.call(operation)
                specific = (
                    await client.call("load_format", format_name=args.format_name)
                    if args.format_name
                    else None
                )
                return {"format": data, "format_specific": specific}
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
        if operation.get("board_id") and not self.access.admin:
            self.authorize(record["config"]["id"], operation["board_id"])
        return {
            key: value
            for key, value in operation.items()
            if key not in {"request", "connection_key"}
        }

    @staticmethod
    def public_snapshot(snapshot: dict) -> dict:
        return {key: value for key, value in snapshot.items() if key != "connection_key"} | {
            "url": board_url(snapshot["board_id"], snapshot["scope"].get("frame_id")),
            "external_content": True,
        }

    async def read(self, client, record: dict, operation: str, args: OperationInput) -> dict:
        scope = {"frame_id": args.frame_id, "item_ids": sorted(args.item_ids)}
        values = {"board_id": args.board_id, "frame_id": args.frame_id, "item_ids": args.item_ids}
        if operation == "read_canvas":
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
        if saved["scope"] != {"frame_id": args.frame_id, "item_ids": sorted(args.item_ids)}:
            raise MiroError("conflict", "Update selection must match the source snapshot")
        return saved

    async def write(self, record: dict, operation: str, args: OperationInput) -> dict:
        require(args.operation_key, "operation_key")
        request = args.model_dump(mode="json")
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
            if operation == "create_board" and len(record["config"]["boards"]) >= 200:
                raise MiroError(
                    "invalid_input", "Connection board limit reached; remove unused bindings first"
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
            async with self.client_factory(self.store, record).open() as client:
                baseline = None
                values: dict[str, Any] = {"board_id": args.board_id, "frame_id": args.frame_id}
                if operation in {"create_canvas", "update_canvas"}:
                    svg, ids = canvas(require(args.svg, "svg"))
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
                entry = {
                    "operation_id": op_id,
                    "connection_key": record["key"],
                    "operation": operation,
                    "board_id": args.board_id,
                    "actor": self.access.actor,
                    "directory_path": self.access.directory_path,
                    "status": "running",
                    "fingerprint": fingerprint,
                    "request": request,
                    "source_refs": args.source_refs,
                    "started_at": time.time(),
                    "baseline_snapshot_id": baseline["snapshot_id"] if baseline else None,
                }
                self.store.put("operation", op_id, entry)
                try:
                    result = await client.call(operation, **values)
                    entry["upstream_acknowledged"] = True
                    if operation == "create_board":
                        entry["board_id"] = self._created_board_id(result)
                        current_config = Connection.model_validate(record["config"])
                        from .models import BoardBinding

                        if entry["board_id"] not in {b.board_id for b in current_config.boards}:
                            current_config.boards.append(
                                BoardBinding(
                                    board_id=entry["board_id"], name=args.title, writable=True
                                )
                            )
                            record = self.store.save_connection(current_config, record["revision"])
                            client.record = record
                        args = args.model_copy(update={"board_id": entry["board_id"]})
                    read_op = (
                        "read_comments"
                        if operation in {"reply_comment", "resolve_comment"}
                        else "read_canvas"
                    )
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
                except Exception as exc:
                    # Even a remote tool error can follow a partial multi-item mutation.
                    pre_send = (
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
                        error=exc.code if isinstance(exc, MiroError) else "upstream_unavailable",
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
                url = board.get("url") or board.get("board_url") or board.get("boardUrl")
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
        args = OperationInput.model_validate(entry["request"] | {"board_id": entry["board_id"]})
        read_op = (
            "read_comments"
            if entry["operation"] in {"reply_comment", "resolve_comment"}
            else "read_canvas"
        )
        async with self.store.lock("board:" + args.board_id):
            # A live writer or another reconciliation may have completed while
            # this request waited. Preserve its durable outcome and evidence.
            entry = self.store.get("operation", operation_id)
            async with self.client_factory(self.store, record).open() as client:
                observed = await self.read(client, record, read_op, args)
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
            if entry.get("board_id") and not entry.get("reconciliation_snapshot_id"):
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
        require(args.board_id, "board_id")
        self.authorize(args.connection_id, args.board_id)
        key = digest(
            [
                self.access.directory_path,
                subscription_id,
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
                    "directory_path": self.access.directory_path,
                    "connection_key": record["key"],
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
                    async with self.client_factory(self.store, record).open() as client:
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
            state["pending"] = [
                event for event in state["pending"] if event["event_id"] != event_id
            ]
            self.store.put("subscription", subscription_key, state)
