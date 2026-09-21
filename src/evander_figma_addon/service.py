"""One authorization and recovery path for agent, workflow, and operator callers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import io
import json
from pathlib import Path
import tarfile
import time
from uuid import uuid4

from evander.artifact_spaces import ArtifactSpaceStore
from evander.communication_artifacts import CommunicationArtifactStore
from evander.models import DirectoryPath
from evander.published_artifacts import PublishedArtifactStore

from .auth import OAuth
from .client import FigmaClient
from .models import (
    DesignRequest,
    FigmaConflict,
    FigmaError,
    FigmaRejected,
    FigmaUnavailable,
    FilePermissions,
    digest,
)
from .state import Store

CANVAS_WRITES = {"create_area", "edit", "compose"}
READ_OPERATIONS = {
    "pages",
    "inspect",
    "variables",
    "comments",
    "history",
    "import_snapshot",
    "reconcile",
}


def now():
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Actor:
    directory: str
    session_id: str
    trace_id: str | None = None
    operator: bool = False


class FigmaService:
    def __init__(self, runtime_dir: Path, actor: Actor, *, client_factory=FigmaClient, oauth=None):
        self.store = Store(runtime_dir)
        self.actor = Actor(
            DirectoryPath(segments=actor.directory).as_posix(),
            actor.session_id,
            actor.trace_id,
            actor.operator,
        )
        self.oauth = oauth or OAuth(self.store)
        self.client_factory = client_factory

    def _target(self, target_id, access):
        _, settings = self.store.settings(self.actor.directory)
        target = settings.targets.get(target_id)
        if target is None:
            raise FigmaError("Unknown Figma target in this directory")
        permissions = (
            FilePermissions(access=["read", "comment", "edit"]) if self.actor.operator else target
        )
        if access not in permissions.access:
            raise FigmaError("This Figma file does not allow the requested capability")
        connection = settings.connections[target.connection_id]
        client = self.client_factory(
            self.oauth, self.actor.directory, target.connection_id, connection
        )
        return target, permissions, connection, client

    def list_targets(self):
        _, settings = self.store.settings(self.actor.directory)
        return [
            {
                "target_id": key,
                "name": target.name,
                "file_url": f"https://www.figma.com/design/{target.file_key}",
                "access": target.access,
                "edit_roots": target.edit_roots,
            }
            for key, target in settings.targets.items()
            if target.access
        ]

    def connection_status(self, connection_id):
        _, settings = self.store.settings(self.actor.directory)
        connection = settings.connections.get(connection_id)
        if connection is None:
            raise FigmaError("Unknown Figma connection")
        status = self.oauth.status(self.actor.directory, connection_id, connection)
        binding = digest([connection.binding(), status["generation"]])
        try:
            check = self.store.get(
                "check",
                digest([self.actor.directory, connection_id]),
                self.actor.directory,
                connection_id,
                "connection",
            )
        except FigmaConflict:
            check = {}
        return {
            "connected": status["connected"],
            "expires_at": status["expires_at"],
            "authoring_ready": bool(
                status["connected"]
                and check.get("binding") == binding
                and check.get("status") == "passed"
            ),
            "check": {k: v for k, v in check.items() if k != "binding"},
        }

    def execute(self, request: DesignRequest | dict):
        request = DesignRequest.model_validate(request)
        access = (
            "edit"
            if request.operation in CANVAS_WRITES
            else ("comment" if request.operation == "comment" else "read")
        )
        target, permissions, connection, client = self._target(request.target_id, access)
        # All canvas authors must be able to inspect their own output and expected base.
        if request.operation in CANVAS_WRITES and "read" not in permissions.access:
            raise FigmaError("Canvas editing also requires the file's read capability")
        scope = (self.actor.directory, request.target_id, target.file_key)
        if request.operation == "history":
            return {
                "operations": [
                    self._public_operation(r) for r in self.store.records("operation", *scope)
                ],
                "snapshots": self.store.records("snapshot", *scope),
            }
        if request.operation == "comments":
            return {"comments": client.comments(target.file_key)}
        if request.operation == "pages":
            return client.script(target.file_key, {"action": "pages"})
        if request.operation == "variables":
            return client.variables(target.file_key, request.node_id)
        if request.operation == "inspect":
            nodes = client.inspect(target.file_key, request.node_id)
            return {"node_id": request.node_id, "revision": digest(nodes), "nodes": nodes}
        if request.operation == "import_snapshot":
            return self._import_snapshot(request.snapshot_id, scope)
        if request.operation == "reconcile":
            return self._reconcile(request.operation_id, scope, client)
        if (
            request.operation in CANVAS_WRITES
            and not self.connection_status(target.connection_id)["authoring_ready"]
        ):
            raise FigmaUnavailable(
                "Authoring is disabled until the unattended connection test passes"
            )

        operation_id = digest([self.actor.directory, self.actor.session_id, request.request_id])
        fingerprint = digest([target.file_key, target.connection_id, request.model_dump()])
        with self.store.file_lock(target.file_key):
            refreshed_target, permissions, refreshed_connection, client = self._target(
                request.target_id, access
            )
            if refreshed_target != target or refreshed_connection != connection:
                raise FigmaConflict(
                    "Figma permissions or connection changed; retry with current settings"
                )
            if (
                request.operation in CANVAS_WRITES
                and not self.connection_status(target.connection_id)["authoring_ready"]
            ):
                raise FigmaUnavailable(
                    "Figma authoring authorization changed while waiting for the file"
                )
            try:
                prior = self.store.get("operation", operation_id, *scope)
            except FigmaConflict:
                prior = None
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise FigmaConflict("request_id was already used for different arguments")
                return self._public_operation(prior)
            if self.store.unresolved(target.file_key):
                raise FigmaConflict(
                    "This file has an uncertain operation; reconcile it before more writes"
                )
            before = (
                client.inspect(target.file_key, request.node_id)
                if request.node_id and request.operation != "comment"
                else None
            )
            if request.expected_revision and digest(before) != request.expected_revision:
                raise FigmaConflict("Design changed since inspection; inspect and revise the edit")
            if request.operation in CANVAS_WRITES and permissions.edit_roots:
                if not before or not set(permissions.edit_roots).intersection(
                    [before[0]["id"], *before[0].get("ancestors", [])]
                ):
                    raise FigmaError("Selected node is outside this file's editable roots")
            record = {
                "operation_id": operation_id,
                "request_id": request.request_id,
                "fingerprint": fingerprint,
                "status": "running",
                "actor": self.actor.session_id,
                "trace_id": self.actor.trace_id,
                "created_at": now(),
                "request": request.model_dump(mode="json"),
                "before": before,
            }
            if request.operation == "comment":
                # Pin the REST account before attempting the POST. A quoted marker or
                # an exact copy from another user is not evidence of our delivery.
                marker = f"[Evander {self.actor.session_id}; operation {operation_id}]"
                record["expected_comment"] = {
                    "message": request.message + "\n\n" + marker,
                    "user_id": client.user_id(),
                }
            self.store.put("operation", operation_id, *scope, record, immutable=True)
            try:
                if request.operation == "snapshot":
                    result = self._snapshot(operation_id, scope, request.node_id, before, client)
                elif request.operation == "comment":
                    result = client.comment(
                        target.file_key,
                        record["expected_comment"]["message"],
                        request.node_id,
                        request.comment_id,
                    )
                else:
                    payload = {
                        "action": request.operation,
                        "node_id": request.node_id,
                        "name": request.name,
                        "expected": before,
                        "allowed_roots": permissions.edit_roots,
                        "edits": [edit.model_dump(exclude_none=True) for edit in request.edits],
                    }
                    if request.operation == "compose":
                        payload["edits"], payload["expected_sources"] = [], {}
                        x = (
                            max(
                                [
                                    n.get("x", 0) + n.get("width", 0)
                                    for n in before[1:]
                                    if n.get("parent_id") == request.node_id
                                ]
                                or [0]
                            )
                            + 80
                        )
                        for snapshot_id in request.source_snapshot_ids:
                            snapshot = self.store.get("snapshot", snapshot_id, *scope)
                            source = self._bundle(snapshot)["design.json"]
                            source_nodes = json.loads(source)["nodes"]
                            node_id = snapshot["node_id"]
                            if (
                                digest(client.inspect(target.file_key, node_id))
                                != snapshot["revision"]
                            ):
                                raise FigmaConflict("A source proposal changed after its snapshot")
                            payload["expected_sources"][node_id] = source_nodes
                            payload["edits"].append(
                                {
                                    "action": "clone",
                                    "node_id": node_id,
                                    "parent_id": request.node_id,
                                    "properties": {"x": x, "y": 0},
                                }
                            )
                            x += source_nodes[0].get("width", 0) + 80
                    result = client.script(target.file_key, payload)
                    # Persist the upstream receipt before any additional request can fail.
                    record["receipt"] = result
                    self.store.put("operation", operation_id, *scope, record)
                    root_id = request.node_id or result["createdNodeIds"][0]
                    after = client.inspect(target.file_key, root_id)
                    result = {
                        **result,
                        "node_id": root_id,
                        "revision": digest(after),
                        "file_url": self._url(target.file_key, root_id),
                    }
                record.update(status="succeeded", result=result, completed_at=now())
                event = {
                    "kind": "design.changed"
                    if request.operation in CANVAS_WRITES
                    else (
                        "snapshot.created" if request.operation == "snapshot" else "comment.created"
                    ),
                    "origin": "evander",
                    "actor": self.actor.session_id,
                    "file_key": target.file_key,
                    "operation_id": operation_id,
                    "result": result,
                    "created_at": now(),
                }
                self.store.finish(record, *scope, event=event)
            except Exception as exc:
                # Even a rejected upstream script can have partially changed the canvas.
                record.update(
                    status="uncertain",
                    error="Operation interrupted; reconcile before retrying",
                    completed_at=now(),
                )
                if request.operation == "snapshot":
                    record.update(
                        status="failed",
                        error="Snapshot could not be captured consistently; request a new snapshot",
                    )
                if isinstance(exc, FigmaRejected):
                    record.update(status="rejected", error=str(exc))
                if (
                    isinstance(exc, FigmaConflict)
                    and request.operation == "compose"
                    and "receipt" not in record
                ):
                    record["status"] = "rejected"
                self.store.finish(record, *scope)
            return self._public_operation(record)

    @staticmethod
    def _url(file_key, node_id):
        return f"https://www.figma.com/design/{file_key}?node-id={node_id.replace(':', '-')}"

    @staticmethod
    def _public_operation(record):
        return {
            key: value
            for key, value in record.items()
            if key not in {"before", "fingerprint", "receipt"}
        }

    def _snapshot(self, snapshot_id, scope, node_id, nodes, client):
        png = client.screenshot(scope[2], node_id)
        if digest(client.inspect(scope[2], node_id)) != digest(nodes):
            raise FigmaConflict("Design changed while capturing the preview; take a new snapshot")
        revision = digest(nodes)
        manifest = {
            "snapshot_id": snapshot_id,
            "file_key": scope[2],
            "node_id": node_id,
            "revision": revision,
            "captured_at": now(),
            "nodes": nodes,
        }
        files = {
            "preview.png": png,
            "design.json": json.dumps(manifest, indent=2).encode(),
            "README.md": (
                f"# Figma design snapshot\n\nSnapshot: {snapshot_id}\n\n"
                f"Source: {self._url(scope[2], node_id)}\n\nRevision: {revision}\n"
            ).encode(),
        }
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as archive:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o644
                archive.addfile(info, io.BytesIO(data))
        key = f"figma/{digest(scope[0])[:20]}/{snapshot_id}"
        published = PublishedArtifactStore(self.store.runtime_dir)
        if not published.exists(key):
            published.publish_bytes(
                key=key,
                session_id=self.actor.session_id,
                content=buf.getvalue(),
                source_file_count=3,
                source_byte_count=sum(map(len, files.values())),
                metadata={
                    "directory_path": scope[0],
                    "target_id": scope[1],
                    "snapshot_id": snapshot_id,
                    "revision": revision,
                },
            )
        space = f"/figma/{digest(scope[0])[:20]}/{snapshot_id}"
        spaces, subject = ArtifactSpaceStore(self.store.runtime_dir), "figma-addon"
        spaces.ensure_space(path="/figma", subject=subject, grants={"manage_subjects": [subject]})
        spaces.ensure_space(path=space, subject=subject, grants={"manage_subjects": [subject]})
        if not spaces.read().versions_for_space(space):
            spaces.publish_version(
                space_path=space, artifact_id="bundle", artifact_key=key, created_by_subject=subject
            )
        record = {
            "snapshot_id": snapshot_id,
            "node_id": node_id,
            "revision": revision,
            "artifact_key": key,
            "artifact_space_ref": "artifact-space:" + space,
            "file_url": self._url(scope[2], node_id),
            "captured_at": manifest["captured_at"],
        }
        self.store.put("snapshot", snapshot_id, *scope, record, immutable=True)
        return record

    def _bundle(self, snapshot):
        artifact = PublishedArtifactStore(self.store.runtime_dir).get(snapshot["artifact_key"])
        import hashlib

        content = artifact.blob_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact.metadata.sha256:
            raise FigmaError("Snapshot archive failed its integrity check")
        result = {}
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            for entry in archive:
                if (
                    entry.name not in {"preview.png", "design.json", "README.md"}
                    or not entry.isfile()
                    or entry.size > 16 * 1024 * 1024
                ):
                    raise FigmaError("Invalid snapshot archive")
                result[entry.name] = archive.extractfile(entry).read()
        return result

    def _import_snapshot(self, id, scope):
        snapshot = self.store.get("snapshot", id, *scope)
        artifacts = CommunicationArtifactStore(self.store.runtime_dir)
        items = []
        for name, content in self._bundle(snapshot).items():
            item = artifacts.stage(
                provider="figma",
                session_id=self.actor.session_id,
                content=content,
                original_filename=name,
                metadata={"snapshot_id": id},
            )
            items.append(item.model_dump(mode="json"))
        return {
            "snapshot": snapshot,
            "artifacts": items,
            "next_step": "Use communication_copy_artifact_to_workspace for each artifact_id",
        }

    @staticmethod
    def _matches_comment(record, comment):
        expected = record.get("expected_comment")
        user = comment.get("user")
        return bool(
            expected
            and isinstance(user, dict)
            and expected.get("user_id")
            and user.get("id") == expected["user_id"]
            and comment.get("message") == expected["message"]
        )

    def _reconcile(self, operation_id, scope, client):
        with self.store.file_lock(scope[2]):
            record = self.store.get("operation", operation_id, *scope)
            if record["status"] not in {"running", "uncertain"}:
                return self._public_operation(record)
            request = record["request"]
            event = None
            if request["operation"] == "comment":
                comments = client.comments(scope[2])
                matches = [c for c in comments if self._matches_comment(record, c)]
                if len(matches) == 1:
                    record.update(status="succeeded", result=matches[0], completed_at=now())
                    event = {
                        "kind": "comment.created",
                        "origin": "evander",
                        "file_key": scope[2],
                        "actor": record["actor"],
                        "operation_id": operation_id,
                        "result": matches[0],
                        "created_at": now(),
                    }
                else:
                    record["reconciliation"] = {
                        "observed_at": now(),
                        "comments": comments,
                        "requires_operator_resolution": True,
                    }
            else:
                node_id = request.get("node_id") or next(
                    iter(record.get("receipt", {}).get("createdNodeIds", [])), None
                )
                exists = bool(
                    node_id
                    and client.script(scope[2], {"action": "exists", "node_id": node_id})["exists"]
                )
                record["reconciliation"] = {
                    "observed_at": now(),
                    "node_id": node_id,
                    "nodes": client.inspect(scope[2], node_id) if exists else None,
                    "pages": client.script(scope[2], {"action": "pages"})["pages"]
                    if not exists
                    else None,
                    "requires_operator_resolution": True,
                }
            self.store.finish(record, *scope, event=event)
            return self._public_operation(record)

    def resolve_uncertain(self, target_id, operation_id, expected_observation):
        if not self.actor.operator:
            raise FigmaError("An operator must resolve uncertain canvas writes")
        target, _, _, client = self._target(target_id, "edit")
        scope = (self.actor.directory, target_id, target.file_key)
        with self.store.file_lock(target.file_key):
            record = self.store.get("operation", operation_id, *scope)
            if record["status"] not in {"running", "uncertain"}:
                raise FigmaConflict("Operation no longer needs reconciliation")
            observed = record.get("reconciliation")
            if not observed or digest(observed) != expected_observation:
                raise FigmaConflict(
                    "Read reconciliation evidence before accepting the current state"
                )
            if "comments" in observed:
                if digest(client.comments(target.file_key)) != digest(observed["comments"]):
                    raise FigmaConflict("Comments changed after reconciliation")
            elif observed["nodes"] is not None:
                if digest(client.inspect(target.file_key, observed["node_id"])) != digest(
                    observed["nodes"]
                ):
                    raise FigmaConflict("Canvas changed after reconciliation")
            else:
                if (
                    observed["node_id"]
                    and client.script(
                        target.file_key, {"action": "exists", "node_id": observed["node_id"]}
                    )["exists"]
                ):
                    raise FigmaConflict("Design was restored after reconciliation")
                if (
                    client.script(target.file_key, {"action": "pages"})["pages"]
                    != observed["pages"]
                ):
                    raise FigmaConflict("File pages changed after reconciliation")
            record.update(
                status="reconciled", resolved_by=self.actor.session_id, completed_at=now()
            )
            self.store.finish(record, *scope)
            return self._public_operation(record)

    def poll_external(self, target_id, interval=300):
        """Poll comments and named versions; first observation establishes a baseline."""
        if not self.actor.operator:
            raise FigmaError("External polling is workflow-owned")
        target, _, _, client = self._target(target_id, "read")
        scope = (self.actor.directory, target_id, target.file_key)
        key = digest(scope)
        with self.store.file_lock("poll:" + key):
            try:
                cursor = self.store.get("poll", key, *scope)
            except FigmaConflict:
                cursor = {}
            if cursor.get("next_poll", 0) > time.time():
                return
            # Persist backoff before I/O, including on authentication/rate-limit failure.
            self.store.put("poll", key, *scope, {**cursor, "next_poll": time.time() + interval})
            comments, versions = client.comments(target.file_key), client.versions(target.file_key)
            named_versions = [v for v in versions if v.get("label")]
            seen = set(cursor.get("seen", []))
            items = [("comment.created", str(c["id"]), c) for c in comments] + [
                ("version.created", str(v["id"]), v) for v in named_versions
            ]
            current = set()
            for kind, id, item in items:
                event_id = digest([scope, kind, id])
                current.add(event_id)
                if not cursor.get("initialized") or event_id in seen:
                    continue
                if kind == "comment.created" and "[Evander " in item.get("message", ""):
                    marker = item["message"].rsplit("operation ", 1)[-1].rstrip("]")
                    operation = self.store.operation_for_file(marker, target.file_key)
                    if operation and self._matches_comment(operation, item):
                        continue
                self.store.event(
                    event_id,
                    scope[0],
                    target_id,
                    {
                        "kind": kind,
                        "origin": "external",
                        "file_key": target.file_key,
                        "item": item,
                        "created_at": now(),
                    },
                )
            self.store.put(
                "poll",
                key,
                *scope,
                {"initialized": True, "seen": sorted(current), "next_poll": time.time() + interval},
            )

    def test_authoring(self, target_id):
        """The only gate-opening path: a real create/edit/render/readback/cleanup cycle."""
        if not self.actor.operator:
            raise FigmaError("Only an operator can run the authoring connection test")
        target, _, connection, client = self._target(target_id, "edit")
        scope = (self.actor.directory, target_id, target.file_key)
        check_id = digest([self.actor.directory, target.connection_id])
        check = {"status": "failed", "tested_at": now(), "target_id": target_id}
        op_id, page_id = uuid4().hex, None
        test_name = "Evander connection test " + op_id
        record = {
            "operation_id": op_id,
            "status": "running",
            "actor": self.actor.session_id,
            "request": {"operation": "connection_test", "node_id": None},
            "created_at": now(),
        }
        with self.store.file_lock(target.file_key):
            if self.store.unresolved(target.file_key):
                raise FigmaConflict(
                    "Reconcile uncertain file operations before running the connection test"
                )
            self.store.put(
                "check", check_id, self.actor.directory, target.connection_id, "connection", check
            )
            self.store.put("operation", op_id, *scope, record, immutable=True)
            try:
                reply = client.script(target.file_key, {"action": "create_area", "name": test_name})
                page_id = reply["createdNodeIds"][0]
                record["request"]["node_id"] = page_id
                self.store.put("operation", op_id, *scope, record)

                def edit(node_id, edits):
                    return client.script(
                        target.file_key,
                        {
                            "action": "edit",
                            "node_id": node_id,
                            "expected": client.inspect(target.file_key, node_id),
                            "edits": edits,
                        },
                    )

                reply = edit(
                    page_id,
                    [
                        {
                            "action": "create",
                            "kind": "FRAME",
                            "parent_id": page_id,
                            "properties": {
                                "name": "Unattended smoke test",
                                "width": 400,
                                "height": 160,
                                "layout_mode": "VERTICAL",
                                "padding": 24,
                                "fill": "#ffffff",
                            },
                        }
                    ],
                )
                frame_id = reply["createdNodeIds"][0]
                reply = edit(
                    frame_id,
                    [
                        {
                            "action": "create",
                            "kind": "TEXT",
                            "parent_id": frame_id,
                            "properties": {"characters": "Before", "font_size": 24},
                        }
                    ],
                )
                text_id = reply["createdNodeIds"][0]
                edit(
                    frame_id,
                    [
                        {
                            "action": "update",
                            "node_id": text_id,
                            "properties": {"characters": "Evander unattended authoring works"},
                        }
                    ],
                )
                nodes = client.inspect(target.file_key, frame_id)
                if not any(
                    n.get("id") == text_id
                    and n.get("characters") == "Evander unattended authoring works"
                    for n in nodes
                ):
                    raise FigmaError("The authoring readback did not match")
                png = client.screenshot(target.file_key, frame_id)
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise FigmaError("Missing rendered preview")
                status = self.oauth.status(self.actor.directory, target.connection_id, connection)
                if not status["connected"]:
                    raise FigmaUnavailable("Connection changed during the test")
                check.update(
                    status="passed",
                    binding=digest([connection.binding(), status["generation"]]),
                    preview_sha256=__import__("hashlib").sha256(png).hexdigest(),
                    revision=digest(nodes),
                    created_node_ids=[page_id, frame_id, text_id],
                )
                record.update(
                    status="succeeded", result={k: v for k, v in check.items() if k != "binding"}
                )
            except Exception:
                check.update(
                    status="failed",
                    error="Unattended authoring test failed; check approval, credentials, and tool compatibility",
                )
                record.update(status="uncertain", error=check["error"])
            finally:
                if page_id:
                    try:
                        client.script(
                            target.file_key,
                            {
                                "action": "cleanup_test",
                                "node_id": page_id,
                                "expected_name": test_name,
                                "expected": client.inspect(target.file_key, page_id),
                            },
                        )
                        pages = client.script(target.file_key, {"action": "pages"})["pages"]
                        if any(page["id"] == page_id for page in pages):
                            raise FigmaError("Connection-test cleanup could not be verified")
                        check["cleanup_complete"] = True
                        if record["status"] == "uncertain":
                            record["status"] = "failed"
                    except Exception:
                        check.update(
                            status="failed", cleanup_complete=False, cleanup_page_id=page_id
                        )
                        record["status"] = "uncertain"
                self.store.finish(record, *scope)
                self.store.put(
                    "check",
                    check_id,
                    self.actor.directory,
                    target.connection_id,
                    "connection",
                    check,
                )
        return {k: v for k, v in check.items() if k != "binding"}
