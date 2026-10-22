"""Host-owned coverage journal. No model can mount or update this database."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3

VERSION = 1
SDK_VERSION = "0.1.31"
RETENTION = timedelta(days=7)
RESERVE_SECONDS = 300
SEVERITIES = ("critical", "high", "medium", "low", "informational")


def now():
    return datetime.now(UTC)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def valid_path(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Invalid inventory path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(x in {"", ".", ".."} for x in value.split("/")):
        raise ValueError("Invalid inventory path")
    return value


def inventory_digest(entries):
    seen = set()
    for entry in entries:
        path = valid_path(entry["path"])
        if path in seen:
            raise ValueError("Duplicate inventory path")
        seen.add(path)
        if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", entry["oid"]):
            raise ValueError("Invalid inventory object")
        if entry["mode"] not in {"100644", "100755", "120000", "160000"}:
            raise ValueError("Unsupported Git entry mode")
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise ValueError("Invalid inventory size")
        if entry["kind"] not in {"text", "binary", "symlink", "gitlink", "unavailable"}:
            raise ValueError("Invalid inventory kind")
        allowed_kinds = {"120000": {"symlink"}, "160000": {"gitlink"}}.get(
            entry["mode"], {"text", "binary", "unavailable"}
        )
        if entry["kind"] not in allowed_kinds:
            raise ValueError("Inventory kind does not match its Git mode")
    return digest(sorted(entries, key=lambda x: x["path"]))


def batches(entries):
    """Stable exact-file scopes; never infer an ignore policy from a suffix."""
    batch, size, directory = [], 0, None
    for entry in sorted(entries, key=lambda x: x["path"]):
        if entry["kind"] != "text":
            if batch:
                yield batch
                batch, size = [], 0
            yield [entry["path"]]
            continue
        parent = str(PurePosixPath(entry["path"]).parent)
        if batch and (parent != directory or len(batch) >= 100 or size + entry["size"] > 524288):
            yield batch
            batch, size = [], 0
        batch.append(entry["path"])
        size += entry["size"]
        directory = parent
    if batch:
        yield batch


def task(kind, paths, *, parent=None):
    paths = sorted(set(paths))
    return {
        "id": digest([kind, paths])[:24],
        "kind": kind,
        "paths": paths,
        "status": "pending",
        "attempts": [],
        "parent": parent,
        "next_at": None,
        "deferred_keys": [],
    }


def progress(state):
    counts = {key: 0 for key in ("reviewed", "not_applicable", "blocked", "pending")}
    for entry in state["files"].values():
        counts[entry["disposition"]] += 1
    tasks = list(state["tasks"].values())
    return {
        "phase": state["phase"],
        "files_total": len(state["files"]),
        **{"files_" + key: value for key, value in counts.items()},
        "active_reviews": sum(not a.get("cleanup_confirmed") for x in tasks for a in x["attempts"]),
        "completed_reviews": sum(x["status"] == "completed" for x in tasks),
        "attempts": sum(len(x["attempts"]) for x in tasks),
        "cleanup_condition": (
            "Worker cleanup is unconfirmed; repository reservation remains held"
            if state["finished_at"] and not state["cleanup_confirmed"]
            else None
        ),
        "deadline_at": state["deadline_at"],
        "last_progress_at": state["last_progress_at"],
    }


def make_state(
    scan_id, request, directory, inventory, route, *, previous=None, previous_until=None, at=None
):
    at = at or now()
    entries = inventory["entries"]
    checksum = inventory_digest(entries)
    revision = inventory["revision"]
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision):
        raise ValueError("Invalid inventory revision")
    state = {
        "version": VERSION,
        "sdk_version": SDK_VERSION,
        "scan_id": scan_id,
        "directory_path": directory,
        "managed_repo_id": request.managed_repo_id,
        "revision": revision,
        "inventory_digest": checksum,
        "inventory": entries,
        "route": route,
        "request": request.model_dump(mode="json"),
        "created_at": at.isoformat(),
        "deadline_at": (at + timedelta(seconds=request.timeout_seconds)).isoformat(),
        "last_progress_at": at.isoformat(),
        "finished_at": None,
        "phase": "mapping",
        "stop_reason": None,
        "cleanup_confirmed": True,
        "cleanup_finished_at": None,
        "archive_key": None,
        "lineage": [],
        "proofs": [],
        "uncredited": [],
        "deferred": {},
        "open_questions": {},
        "architecture": None,
        "boundary_planned": False,
        "files": {
            x["path"]: {"disposition": "pending", "receipt": None, "reason": None} for x in entries
        },
        "tasks": {},
    }
    if previous:
        if not resumable(previous, at=at, until=previous_until):
            raise ValueError("Source scan is active, complete, expired, or awaiting cleanup")
        for key in (
            "version",
            "sdk_version",
            "directory_path",
            "managed_repo_id",
            "revision",
            "inventory_digest",
            "route",
        ):
            if state[key] != previous[key]:
                raise ValueError("Source scan configuration or snapshot is incompatible")
        state.update(
            {
                key: deepcopy(previous[key])
                for key in (
                    "files",
                    "tasks",
                    "proofs",
                    "uncredited",
                    "deferred",
                    "architecture",
                    "boundary_planned",
                )
            }
        )
        state["lineage"] = [*previous["lineage"], previous["scan_id"]]
        state["open_questions"] = deepcopy(previous.get("open_questions", {}))
        for item in state["tasks"].values():
            if item["status"] in {"running", "blocked"}:
                item["status"] = "pending"
            item["attempts"] = []
            item["next_at"] = None
        for entry in state["files"].values():
            if entry["disposition"] == "blocked":
                entry.update(disposition="pending", reason=None)
        state["phase"] = "reviewing" if state["architecture"] else "mapping"
    else:
        if not entries:
            state.update(
                architecture={"paths": [], "reason": "The pinned Git tree has no tracked files"},
                boundary_planned=True,
                phase="finalizing",
            )
            return state
        item = task("architecture", [x["path"] for x in entries])
        state["tasks"][item["id"]] = item
        for paths in batches(entries):
            item = task("files", paths)
            state["tasks"][item["id"]] = item
    return state


def resumable(state, *, at=None, until=None):
    at = at or now()
    finished = state.get("finished_at")
    return bool(
        finished
        and state.get("cleanup_confirmed")
        and not complete(state)
        and at < (until or retention_anchor(state) + RETENTION)
    )


def retention_anchor(state):
    return datetime.fromisoformat(state.get("cleanup_finished_at") or state["finished_at"])


def complete(state):
    return (
        state.get("architecture") is not None
        and state["boundary_planned"]
        and all(x["disposition"] in {"reviewed", "not_applicable"} for x in state["files"].values())
        and all(x["status"] in {"completed", "split"} for x in state["tasks"].values())
        and all(x.get("resolution") for x in state["deferred"].values())
    )


def pending(state, *, at=None):
    at = at or now()
    if state.get("not_before") and at < datetime.fromisoformat(state["not_before"]):
        return []
    if state["stop_reason"] or at >= datetime.fromisoformat(state["deadline_at"]) - timedelta(
        seconds=RESERVE_SECONDS
    ):
        return []
    candidates = [
        x
        for x in state["tasks"].values()
        if x["status"] == "pending"
        and (not x["next_at"] or at >= datetime.fromisoformat(x["next_at"]))
    ]
    phase = "architecture" if state["architecture"] is None else "files"
    if phase == "files" and not any(
        x["kind"] == "files" and x["status"] in {"pending", "running"}
        for x in state["tasks"].values()
    ):
        phase = "boundary"
    # An uncertain launch/stop occupies a slot even after its result was saved.
    # Never overlap generations of a scope, including across orchestrator restarts.
    candidates = [
        x
        for x in candidates
        if x["kind"] == phase and all(a.get("cleanup_confirmed") for a in x["attempts"])
    ]
    occupied = sum(
        not a.get("cleanup_confirmed") for x in state["tasks"].values() for a in x["attempts"]
    )
    slots = (1 if phase == "architecture" else 2) - occupied
    return candidates[: max(0, slots)]


def begin(state, item, *, at=None):
    at = at or now()
    if item["status"] != "pending":
        raise ValueError("Review is not pending")
    number = len(item["attempts"]) + 1
    attempt = {
        "number": number,
        "scan_id": digest([state["scan_id"], item["id"], number])[:32],
        "started_at": at.isoformat(),
        "handle": None,
        "finished_at": None,
        "cleanup_confirmed": False,
    }
    item["attempts"].append(attempt)
    item["status"] = "running"
    state["phase"] = {"architecture": "mapping", "files": "reviewing", "boundary": "boundaries"}[
        item["kind"]
    ]
    state["cleanup_confirmed"] = False
    return attempt


def record_open_questions(state, questions, proof):
    """Retain native advisory questions with their immutable source receipt."""
    recorded = state.setdefault("open_questions", {})
    for question in questions:
        key = proof["provider_scan_id"] + ":question:" + digest(question)[:24]
        recorded.setdefault(key, {**deepcopy(question), "receipt": proof["archive_key"]})


def accept(state, item, attempt_id, receipt, proof, *, at=None):
    """Fence late completions and bind every accepted result to its input scope."""
    at = at or now()
    if item["status"] != "running" or item["attempts"][-1]["scan_id"] != attempt_id:
        return False
    if (
        receipt.get("revision") != state["revision"]
        or sorted(receipt.get("paths", [])) != item["paths"]
    ):
        raise ValueError("Review result does not match the pinned scope")
    if receipt.get("sdk_version") != state["sdk_version"] or receipt.get("kind") != item["kind"]:
        raise ValueError("Review result has an incompatible producer")
    item["attempts"][-1].update(finished_at=at.isoformat(), archive_key=proof["archive_key"])
    # Immutable proofs retain findings even if this scope later gets split or retried.
    state["proofs"].append(proof)
    for deferred in receipt.get("deferred", []):
        key = proof["provider_scan_id"] + ":" + str(deferred["id"])
        state["deferred"].setdefault(key, {**deepcopy(deferred), "resolution": None})
        if item["kind"] == "boundary" and key not in item["deferred_keys"]:
            item["deferred_keys"].append(key)
    # The native schema permits openQuestions on a complete static review.
    # Required work is expressed by partial coverage, deferred work or follow-up
    # surfaces; advisory limitations must not create new review obligations.
    record_open_questions(state, receipt.get("open_questions", []), proof)
    closed = (
        receipt.get("coverage") == "complete"
        and not receipt.get("deferred")
        and not receipt.get("exclusions")
        and not receipt.get("needs_follow_up")
    )
    if item["kind"] == "architecture":
        architecture = receipt.get("architecture")
        if architecture:
            state["architecture"] = architecture
            item["status"] = "completed"
            state["last_progress_at"] = at.isoformat()
        else:
            retry(state, item, "Architecture map was not produced", at=at)
        return True
    if item["kind"] == "boundary":
        for key, value in receipt.get("resolutions", {}).items():
            if (
                key in item["deferred_keys"]
                and value.get("disposition")
                in {"reported", "rejected", "not_applicable", "no_issue_found"}
                and value.get("reason")
            ):
                state["deferred"][key]["resolution"] = {**value, "receipt": proof["archive_key"]}
        # Other parallel scopes own their outstanding work. Global closure is
        # checked by complete(), without spending retries on another review.
        closed = closed and all(
            state["deferred"][key].get("resolution") for key in item["deferred_keys"]
        )
    if closed:
        dispositions = receipt.get("dispositions", {})
        for path in item["paths"] if item["kind"] == "files" else []:
            original = next(x for x in state["inventory"] if x["path"] == path)
            value = dispositions.get(path)
            if original["kind"] == "unavailable":
                closed = False
            elif original["kind"] == "text" and value and value.get("disposition") != "reviewed":
                closed = False
            elif original["kind"] != "text" and not (
                value
                and value.get("reason")
                and value.get("disposition") in {"reviewed", "not_applicable"}
            ):
                closed = False
        if closed:
            item["status"] = "completed"
            if item["kind"] == "files":
                for path in item["paths"]:
                    value = dispositions.get(
                        path,
                        {"disposition": "reviewed", "reason": "Complete exact-scope static review"},
                    )
                    state["files"][path] = {**value, "receipt": proof["archive_key"]}
            state["last_progress_at"] = at.isoformat()
            return True
    retry(state, item, "Review coverage remains incomplete", split=True, at=at)
    return True


def retry(state, item, reason, *, split=False, at=None):
    at = at or now()
    item["error"] = reason
    if split and len(item["paths"]) > 1:
        midpoint = len(item["paths"]) // 2
        children = []
        for paths in (item["paths"][:midpoint], item["paths"][midpoint:]):
            child = task(item["kind"], paths, parent=item["id"])
            state["tasks"].setdefault(child["id"], child)
            children.append(state["tasks"][child["id"]])
        if item["kind"] == "boundary":
            assign_deferred(state, children, item["deferred_keys"])
        item["status"] = "split"
    elif len(item["attempts"]) < 3:
        item["status"] = "pending"
        item["next_at"] = (
            at + timedelta(seconds=5 if len(item["attempts"]) == 1 else 30)
        ).isoformat()
    else:
        item["status"] = "blocked"
        if item["kind"] == "files":
            for path in item["paths"]:
                if state["files"][path]["disposition"] == "pending":
                    state["files"][path].update(disposition="blocked", reason=reason)


def assign_deferred(state, scopes, keys):
    """Give each outstanding item one durable owner, including pathless questions."""
    for scope in scopes:
        scope["deferred_keys"] = []
    for key in sorted(keys):
        if state["deferred"][key].get("resolution"):
            continue
        paths = state["deferred"][key].get("paths", [])
        owner = next(
            (
                scope
                for scope in scopes
                if any(
                    path == pattern or path.startswith(pattern.rstrip("/") + "/")
                    for path in scope["paths"]
                    for pattern in paths
                )
            ),
            scopes[0],
        )
        owner["deferred_keys"].append(key)


def plan_boundaries(state):
    if state["boundary_planned"] or state["architecture"] is None:
        return
    if any(
        x["kind"] == "files" and x["status"] in {"pending", "running"}
        for x in state["tasks"].values()
    ):
        return
    anchors = {path for path in state["architecture"].get("paths", []) if path in state["files"]}
    for value in state["deferred"].values():
        for pattern in value.get("paths", []):
            anchors.update(
                path
                for path in state["files"]
                if path == pattern or path.startswith(pattern.rstrip("/") + "/")
            )
    if not anchors:
        anchors = set(state["files"])
    if anchors:
        scopes = []
        for paths in batches([x for x in state["inventory"] if x["path"] in anchors]):
            item = task("boundary", paths)
            state["tasks"][item["id"]] = item
            scopes.append(item)
        assign_deferred(state, scopes, state["deferred"])
    state["boundary_planned"] = True


def aggregate(state):
    grouped = {}
    for proof in state["proofs"]:
        for finding in proof.get("findings", []):
            # A native primary fingerprint is useful only with the same target.
            fingerprint = finding.get("fingerprints", {}).get("primary")
            identity = finding.get("identity", {})
            roots = tuple(
                sorted(
                    (x.get("path"), x.get("startLine"), x.get("endLine"))
                    for x in finding.get("locations", [])
                    if x.get("role") == "root_control"
                )
            )
            if identity.get("anchor") and finding.get("ruleId") and roots:
                key = (
                    state["managed_repo_id"],
                    state["revision"],
                    finding["ruleId"],
                    identity["anchor"],
                    identity.get("instance"),
                    roots,
                )
            else:
                key = (
                    (proof.get("target_id"), fingerprint)
                    if fingerprint
                    else (proof["archive_key"], finding["findingId"])
                )
            record = grouped.setdefault(key, {"finding": finding, "sources": []})
            if SEVERITIES.index(finding["severity"]["level"]) < SEVERITIES.index(
                record["finding"]["severity"]["level"]
            ):
                record["finding"] = finding
            source = {
                "archive_key": proof["archive_key"],
                "provider_scan_id": proof["provider_scan_id"],
                "finding_id": finding["findingId"],
                "occurrence_id": finding.get("occurrenceId"),
            }
            if source not in record["sources"]:
                record["sources"].append(source)
    counts = dict.fromkeys(SEVERITIES, 0)
    findings = []
    for value in grouped.values():
        f = value["finding"]
        severity = f["severity"]["level"]
        if severity not in counts:
            raise ValueError("Unknown finding severity")
        counts[severity] += 1
        findings.append(
            {
                "id": f["findingId"],
                "occurrence_id": f.get("occurrenceId"),
                "title": f["title"],
                "severity": severity,
                "locations": f.get("locations", []),
                "sources": value["sources"],
            }
        )
    return findings, counts, list(grouped.values())


class CoverageStore:
    def __init__(self, runtime_dir):
        self.root = Path(runtime_dir) / "codex-security-coverage"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.path = self.root / "journal.sqlite3"
        with self.connect() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS campaigns (scan_id TEXT PRIMARY KEY, directory TEXT NOT NULL, repository TEXT NOT NULL, active INTEGER NOT NULL, payload TEXT NOT NULL)"
            )
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.execute("PRAGMA busy_timeout=10000")
        try:
            with c:
                yield c
        finally:
            c.close()

    def get(self, scan_id, directory):
        with self.connect() as c:
            row = c.execute(
                "SELECT payload FROM campaigns WHERE scan_id=? AND directory=?",
                (scan_id, directory),
            ).fetchone()
        if row is None:
            raise ValueError(
                "No tracked-coverage journal in this directory; start a new tracked scan"
            )
        return json.loads(row[0])

    def create(self, state):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT payload FROM campaigns WHERE scan_id=?", (state["scan_id"],)
            ).fetchone()
            if existing:
                saved = json.loads(existing[0])
                if (
                    saved["request"] != state["request"]
                    or saved["directory_path"] != state["directory_path"]
                ):
                    raise ValueError("Coverage identity reused with different inputs")
                return saved
            if c.execute(
                "SELECT 1 FROM campaigns WHERE repository=? AND active=1",
                (state["managed_repo_id"],),
            ).fetchone():
                raise ValueError(
                    "This repository already has an active scan or unconfirmed cleanup"
                )
            c.execute(
                "INSERT INTO campaigns VALUES(?,?,?,?,?)",
                (
                    state["scan_id"],
                    state["directory_path"],
                    state["managed_repo_id"],
                    1,
                    json.dumps(state),
                ),
            )
        return state

    def save(self, state):
        active = not state["finished_at"] or not state["cleanup_confirmed"]
        with self.connect() as c:
            c.execute(
                "UPDATE campaigns SET active=?,payload=? WHERE scan_id=? AND directory=?",
                (int(active), json.dumps(state), state["scan_id"], state["directory_path"]),
            )

    @contextmanager
    def locked(self, scan_id):
        import fcntl

        if not re.fullmatch(r"[a-f0-9]{32}", scan_id):
            raise ValueError("Invalid scan identity")
        with (self.root / (scan_id + ".lock")).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def expired(self, *, at=None):
        at = at or now()
        with self.connect() as c:
            rows = c.execute("SELECT payload FROM campaigns WHERE active=0").fetchall()
        states = [json.loads(row[0]) for row in rows]
        with self.connect() as c:
            all_states = [json.loads(row[0]) for row in c.execute("SELECT payload FROM campaigns")]
        referenced = {
            parent
            for s in all_states
            if not s["finished_at"]
            or not s["cleanup_confirmed"]
            or at < retention_anchor(s) + RETENTION
            for parent in s["lineage"]
        }
        return [
            s
            for s in states
            if s["scan_id"] not in referenced
            and s["finished_at"]
            and at >= retention_anchor(s) + RETENTION
        ]

    def retained_until(self, state):
        """Continuations protect their source journal without mutating its report."""
        with self.connect() as c:
            related = [
                json.loads(row[0])
                for row in c.execute(
                    "SELECT payload FROM campaigns WHERE repository=?", (state["managed_repo_id"],)
                )
            ]
        times = [
            (retention_anchor(s) if s["finished_at"] else datetime.fromisoformat(s["deadline_at"]))
            + RETENTION
            for s in related
            if s["scan_id"] == state["scan_id"] or state["scan_id"] in s["lineage"]
        ]
        return max(times)
