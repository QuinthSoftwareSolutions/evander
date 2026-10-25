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

from evander.codex_auth import normalize_subscription_profile_id
from .coverage_file_controls import (
    blocked_paths,
    isolate_file_scope,
    refresh_blocked_followups,
    refresh_blocked_work,
    release_file_blockers,
    resolve_exclusions,
)
from .coverage_questions import (
    assign_questions,
    ensure_question_scopes,
    normalize_questions,
    record_deferred,
    resolve_for_scope,
    scope_closed,
)
from .coverage_review import apply_file_evidence, record_validation_candidates, reconcile_validation_candidates

VERSION = 3
SDK_VERSION = "0.1.31"
BATCHING_VERSION = 4
MAX_BATCH_FILES = 100
DEFAULT_BATCH_TEXT_BYTES = 1024 * 1024
MAX_BATCH_TEXT_BYTES = 2 * 1024 * 1024
DEFAULT_PARALLEL_REVIEWS = 2
MAX_PARALLEL_REVIEWS = 4
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


def batch_text_limit(state):
    """Read the frozen budget without widening an older active journal."""
    version = state.get("batching_version", 1)
    if type(version) is not int or version not in {1, 2, 3, BATCHING_VERSION}:
        raise ValueError("Source scan batching policy is incompatible")
    if version in {1, 2}:
        return 512 * 1024
    if version == 3:
        return DEFAULT_BATCH_TEXT_BYTES
    limit = state.get("max_batch_text_bytes")
    if type(limit) is not int or not DEFAULT_BATCH_TEXT_BYTES <= limit <= MAX_BATCH_TEXT_BYTES:
        raise ValueError("Source scan batch text limit is incompatible")
    return limit


def review_parallelism(state):
    """Keep historical scans at two and honor each new journal's frozen limit."""
    limit = state.get("max_parallel_reviews", DEFAULT_PARALLEL_REVIEWS)
    if type(limit) is not int or not 1 <= limit <= MAX_PARALLEL_REVIEWS:
        raise ValueError("Source scan review parallelism is incompatible")
    return limit


def batches(entries, *, max_text_bytes=DEFAULT_BATCH_TEXT_BYTES):
    """Pack adjacent paths across folders without omitting non-text entries."""
    batch, text_bytes = [], 0
    for entry in sorted(entries, key=lambda x: x["path"]):
        # Keep large or unavailable content from consuming an otherwise
        # reviewable scope. Other non-text entries still require individual
        # dispositions, but do not each need a separate SDK invocation.
        if entry["size"] > max_text_bytes or entry["kind"] == "unavailable":
            if batch:
                yield batch
                batch, text_bytes = [], 0
            yield [entry["path"]]
            continue
        added_bytes = entry["size"] if entry["kind"] == "text" else 0
        if batch and (
            len(batch) >= MAX_BATCH_FILES or text_bytes + added_bytes > max_text_bytes
        ):
            yield batch
            batch, text_bytes = [], 0
        batch.append(entry["path"])
        text_bytes += added_bytes
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
    counts = {key: 0 for key in ("reviewed", "not_applicable", "blocked", "pending", "excluded")}
    for entry in state["files"].values():
        counts[entry["disposition"]] += 1
    tasks = list(state["tasks"].values())
    validation_only = sum(
        (q.get("resolution") or {}).get("disposition") == "validation_pending"
        or (q.get("resolution") or {}).get("validation_status") == "unverified"
        for q in state["deferred"].values()
    )
    validation_pending = sum(
        (q.get("resolution") or {}).get("disposition") == "validation_pending"
        or (q.get("resolution") or {}).get("validation_status") == "unverified"
        or any(r.get("disposition") == "validation_pending" for r in q.get("scope_resolutions", []))
        for q in state["deferred"].values()
    )
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
        "max_batch_text_bytes": batch_text_limit(state),
        "max_parallel_reviews": review_parallelism(state),
        "exclude_paths": state.get("exclude_paths", []),
        "incomplete_review_policy": state.get("incomplete_review_policy", "split"),
        "exclude_patterns": state.get("exclude_patterns", []),
        "files_isolated": len(blocked_paths(state)),
        "isolated_files": [
            {"path": path, **state["file_blockers"][path]}
            for path in sorted(blocked_paths(state))
        ],
        "required_questions_total": len(state["deferred"]) - validation_only,
        "required_questions_pending": sum(not value.get("resolution") for value in state["deferred"].values()),
        "validation_questions_pending": validation_pending,
        "required_question_aliases": len(state.get("deferred_aliases", {})),
    }


def validate_resume_route(request, route, previous):
    """Only a named, subscription-to-subscription handoff may differ from the source."""
    destination = request.resume_subscription_profile_id
    source = previous["route"]
    if destination is None:
        if route != source:
            raise ValueError("Source scan configuration or snapshot is incompatible")
        return None
    if request.resume_from_scan_id != previous["scan_id"]:
        raise ValueError("Subscription handoff requires the selected source scan")
    if any(
        selected.get("auth_mode") != "chatgpt_subscription"
        or selected.get("model_provider_mode") != "openai_public"
        for selected in (source, route)
    ):
        raise ValueError("Subscription handoff requires two public ChatGPT subscription routes")
    source_profile = normalize_subscription_profile_id(
        source.get("subscription_profile_id"), allow_none=False
    )
    if route.get("subscription_profile_id") != destination:
        raise ValueError("Requested resume subscription must match the scanner session")
    if {k: v for k, v in source.items() if k != "subscription_profile_id"} != {
        k: v for k, v in route.items() if k != "subscription_profile_id"
    }:
        raise ValueError("Subscription handoff cannot change other scanner configuration")
    return {
        "source_scan_id": previous["scan_id"],
        "source_subscription_profile_id": source_profile,
        "destination_subscription_profile_id": destination,
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
    exact = list(request.exclude_paths or [])
    patterns = list(request.exclude_patterns or [])
    excluded, matches = resolve_exclusions(entries, exact, patterns)
    state = {
        "version": VERSION,
        "sdk_version": SDK_VERSION,
        "batching_version": BATCHING_VERSION,
        "max_batch_text_bytes": request.max_batch_text_bytes or DEFAULT_BATCH_TEXT_BYTES,
        "max_parallel_reviews": request.max_parallel_reviews or DEFAULT_PARALLEL_REVIEWS,
        "requested_exclude_paths": exact,
        "exclude_patterns": patterns,
        "exclusion_matches": matches,
        "exclude_paths": excluded,
        "incomplete_review_policy": request.incomplete_review_policy or "split",
        "file_blockers": {},
        "blocked_followups": {},
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
        "deferred_aliases": {},
        "deferred_task_ids": {},
        "question_policy_version": 1,
        "source_review_policy_version": 1,
        "open_questions": {},
        "architecture": None,
        "boundary_planned": False,
        "files": {
            x["path"]: {"disposition": "pending", "receipt": None, "reason": None} for x in entries
        },
        "tasks": {},
    }
    if previous:
        if type(previous["version"]) is not int or previous["version"] not in {1, 2, VERSION}:
            raise ValueError("Source coverage journal version is not supported")
        state["version"] = previous["version"]
        if not resumable(previous, at=at, until=previous_until):
            raise ValueError("Source scan is active, complete, expired, or awaiting cleanup")
        previous_limit = batch_text_limit(previous)
        previous_parallelism = review_parallelism(previous)
        if request.max_parallel_reviews is None:
            state["max_parallel_reviews"] = previous_parallelism
        if request.max_batch_text_bytes is None:
            state["max_batch_text_bytes"] = max(DEFAULT_BATCH_TEXT_BYTES, previous_limit)
        for key in (
            "version",
            "sdk_version",
            "directory_path",
            "managed_repo_id",
            "revision",
            "inventory_digest",
        ):
            if state[key] != previous[key]:
                raise ValueError("Source scan configuration or snapshot is incompatible")
        handoff = validate_resume_route(request, route, previous)
        if handoff:
            state["subscription_handoff"] = handoff
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
        state["file_blockers"] = deepcopy(previous.get("file_blockers", {}))
        state["deferred_aliases"] = deepcopy(previous.get("deferred_aliases", {}))
        state["deferred_task_ids"] = deepcopy(previous.get("deferred_task_ids", {}))
        normalize_questions(state)
        if request.exclude_paths is None:
            state["requested_exclude_paths"] = list(previous.get("requested_exclude_paths", previous.get("exclude_paths", [])))
        if request.exclude_patterns is None:
            state["exclude_patterns"] = list(previous.get("exclude_patterns", []))
        state["exclude_paths"], state["exclusion_matches"] = resolve_exclusions(
            entries, state["requested_exclude_paths"], state["exclude_patterns"]
        )
        if not set(previous.get("exclude_paths", [])) <= set(state["exclude_paths"]):
            raise ValueError("A continuation cannot reintroduce explicitly excluded paths")
        if request.incomplete_review_policy is None:
            state["incomplete_review_policy"] = previous.get("incomplete_review_policy", "split")
        # Older cancellation paths could mark a just-accepted scope blocked.
        # Restore scheduling from host-owned credit without granting new credit;
        # the controller verifies all retained native proofs before journal creation.
        for item in state["tasks"].values():
            if item["kind"] == "architecture" and state["architecture"] is not None:
                item["status"] = "completed"
            elif (
                item["kind"] == "files"
                and item["status"] != "split"
                and item["paths"]
                and all(
                    state["files"][path]["disposition"] in {"reviewed", "not_applicable"}
                    for path in item["paths"]
                )
            ):
                item["status"] = "completed"
        if previous_limit != state["max_batch_text_bytes"]:
            _regroup_unstarted_scopes(state)
        for item in state["tasks"].values():
            if item.get("quarantined") and set(item.get("blocked_by_files", item["paths"])) & blocked_paths(state):
                item["status"] = "blocked"
            elif item["status"] in {"running", "blocked", "excluded"}:
                item["status"] = "pending"
            item["attempts"] = []
            item["next_at"] = None
        for entry in state["files"].values():
            if entry["disposition"] in {"blocked", "excluded"}:
                entry.update(disposition="pending", reason=None)
        state["phase"] = "reviewing" if state["architecture"] else "mapping"
        if set(request.retry_blocked_paths or []) & set(state["exclude_paths"]):
            raise ValueError("An excluded file cannot be retried as blocked work")
        release_file_blockers(state, request.retry_blocked_paths or [])
        refresh_blocked_work(state)
        apply_exclusions(state)
        refresh_blocked_work(state)
        if request.rebuild_pending_batches:
            rebuild_file_batches(state, previous["scan_id"])
        ensure_question_scopes(state)
    else:
        if request.resume_subscription_profile_id is not None:
            raise ValueError("Subscription handoff requires a retained source scan")
        apply_exclusions(state)
        included = [entry for entry in entries if entry["path"] not in state["exclude_paths"]]
        if not included:
            state.update(
                architecture={"paths": [], "reason": "No tracked files remain in the selected review scope"},
                boundary_planned=True,
                phase="finalizing",
            )
        else:
            item = task("architecture", [x["path"] for x in included])
            state["tasks"][item["id"]] = item
            for paths in batches(included, max_text_bytes=batch_text_limit(state)):
                item = task("files", paths)
                state["tasks"][item["id"]] = item
    state["version"] = VERSION
    return state


def _referenced_files(references, files, matches):
    """Resolve supported repository references; unknown references stay required."""
    if not isinstance(references, list):
        return None
    result = set()
    for reference in references:
        if not isinstance(reference, str) or not reference or "\x00" in reference:
            return None
        path = PurePosixPath(reference)
        if path.is_absolute() or ".." in path.parts:
            return None
        normalized = path.as_posix()
        if normalized not in matches:
            if normalized == ".":
                matches[normalized] = set(files)
            elif normalized in files:
                matches[normalized] = {normalized}
            else:
                matches[normalized] = {
                    candidate for candidate in files
                    if candidate.startswith(normalized + "/")
                }
        if not matches[normalized]:
            return None
        result.update(matches[normalized])
    return result


def apply_exclusions(state, native_scopes=None):
    """Remove explicitly excluded work without granting review or changing proofs."""
    excluded = set(state.get("exclude_paths", []))
    if not excluded:
        return
    if not excluded <= state["files"].keys():
        raise ValueError("An excluded path is absent from the pinned inventory")
    pending_boundary_keys = {
        key for item in state["tasks"].values()
        if item["kind"] == "boundary" and item["status"] == "pending"
        for key in item["deferred_keys"]
    }
    for path in excluded:
        if state["files"][path]["disposition"] not in {"reviewed", "not_applicable"}:
            state["files"][path].update(
                disposition="excluded", receipt=None,
                reason="Explicitly excluded by scan request; no review credit granted",
            )
    for item in list(state["tasks"].values()):
        if item["status"] != "pending" or (item["kind"] == "architecture" and state["architecture"] is not None):
            continue
        paths = [path for path in item["paths"] if path not in excluded]
        if paths == item["paths"]:
            continue
        if item["attempts"]:
            raise ValueError("An active or attempted scope cannot be excluded in place")
        if not paths:
            item["status"] = "excluded"
        else:
            item["status"] = "split"
            child = task(item["kind"], paths, parent=item["id"])
            existing = state["tasks"].get(child["id"])
            if existing and existing["status"] not in {"pending", "completed", "split"}:
                raise ValueError("Excluded scope conflicts with retained review work")
            state["tasks"].setdefault(child["id"], child)
    if state["architecture"] is None and excluded == state["files"].keys():
        state.update(
            architecture={"paths": [], "reason": "No tracked files remain in the selected review scope"},
            boundary_planned=True, phase="finalizing",
        )
    native_scopes = native_scopes or {}
    matches = {}
    for key, value in state["deferred"].items():
        resolution = value.get("resolution") or {}
        previous_exclusion = resolution if resolution.get("explicit_exclusion") else None
        if resolution and previous_exclusion is None:
            continue
        references = value.get("paths", [])
        if references == []:
            # Scope provenance is a fallback for truly pathless questions only.
            # Previously verified pathless exclusions survive later calls that
            # carry only the newest review's native scope map.
            references = native_scopes.get(key.split(":", 1)[0])
            if references is None and previous_exclusion:
                references = previous_exclusion.get("paths")
        omitted = _referenced_files(references, state["files"], matches)
        if omitted and omitted <= excluded:
            if previous_exclusion and set(previous_exclusion.get("paths", [])) == omitted:
                continue
            value["resolution"] = {
                "disposition": "excluded", "explicit_exclusion": True,
                "reason": "Originating review scope was explicitly excluded by scan request",
                "paths": sorted(omitted), "scan_id": state["scan_id"],
            }
        elif previous_exclusion:
            value["resolution"] = None
    scopes = [x for x in state["tasks"].values() if x["kind"] == "boundary" and x["status"] == "pending"]
    if scopes:
        assign_deferred(state, scopes, pending_boundary_keys)


def rebuild_file_batches(state, source_scan_id):
    """Replan only unfinished file work in a new, explicitly requested continuation."""
    old = [x for x in state["tasks"].values() if x["kind"] == "files" and x["status"] != "completed" and not x.get("quarantined")]
    if any(x["attempts"] or x["status"] == "running" for x in old):
        raise ValueError("Cannot rebuild active or attempted review work")
    state["pending_batch_rebuild"] = {
        "source_scan_id": source_scan_id, "previous_plan_sha256": digest(old),
        "replaced_scope_count": len(old),
    }
    for item in old:
        del state["tasks"][item["id"]]
    entries = [x for x in state["inventory"] if state["files"][x["path"]]["disposition"] == "pending"]
    for paths in batches(entries, max_text_bytes=batch_text_limit(state)):
        item = task("files", paths)
        if item["id"] in state["tasks"]:
            raise ValueError("Rebuilt scope conflicts with accepted review work")
        state["tasks"][item["id"]] = item


def _regroup_unstarted_scopes(state):
    """Resize only untouched work in the new continuation's journal.

    Attempted scopes and split children preserve their learned boundaries.
    Retained retry errors fence prior attempts whose per-run counters were reset.
    Accepted file credit, immutable proofs and the source journal are unchanged.
    """
    inventory = {entry["path"]: entry for entry in state["inventory"]}
    for kind in ("files", "boundary"):
        eligible = {
            identity: item
            for identity, item in state["tasks"].items()
            if item["kind"] == kind
            and item["status"] == "pending"
            and not item["attempts"]
            and not item.get("error")
            and item.get("parent") is None
        }
        if not eligible:
            continue
        paths = {
            path
            for item in eligible.values()
            for path in item["paths"]
            if kind != "files" or state["files"][path]["disposition"] in {"pending", "blocked"}
        }
        scopes = [
            task(kind, paths)
            for paths in batches(
                [inventory[path] for path in paths], max_text_bytes=batch_text_limit(state)
            )
        ]
        if any(item["id"] in state["tasks"] and item["id"] not in eligible for item in scopes):
            raise ValueError("Regrouped scope conflicts with retained review work")
        for identity in eligible:
            del state["tasks"][identity]
        for item in scopes:
            state["tasks"][item["id"]] = item
        if kind == "boundary":
            assign_deferred(
                state,
                scopes,
                sorted({key for item in eligible.values() for key in item["deferred_keys"]}),
            )


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


def required_work_complete(state):
    return (
        not state.get("migration_error")
        and state.get("architecture") is not None
        and state["boundary_planned"]
        and all(x["disposition"] in {"reviewed", "not_applicable", "excluded"} for x in state["files"].values())
        and all(x["status"] in {"completed", "split", "excluded"} for x in state["tasks"].values())
        and all(x.get("resolution") for x in state["deferred"].values())
    )


def complete(state):
    return not state.get("exclude_paths") and required_work_complete(state)


def scope_complete(state):
    return bool(state["finished_at"] and not state["stop_reason"] and required_work_complete(state))


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
        and not x.get("quarantined")
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
    slots = (1 if phase == "architecture" else review_parallelism(state)) - occupied
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
    record_deferred(state, item, receipt.get("deferred", []), proof)
    record_validation_candidates(state, proof, receipt.get("validation_candidates", []))
    # The native schema permits openQuestions on a complete static review.
    # Required work is expressed by partial coverage, deferred work or follow-up
    # surfaces; advisory limitations must not create new review obligations.
    record_open_questions(state, receipt.get("open_questions", []), proof)
    record_open_questions(state, receipt.get("validation_notes", []), proof)
    credited = apply_file_evidence(
        state, receipt.get("source_dispositions", {}) if item["kind"] == "files" else {},
        proof, receipt.get("coverage"),
    )
    reconcile_validation_candidates(state)
    if credited:
        state["last_progress_at"] = at.isoformat()
    if state.get("exclude_paths"):
        apply_exclusions(state, {proof["provider_scan_id"]: item["paths"]})
    if state.get("file_blockers"):
        refresh_blocked_followups(state)
    closed = (
        (receipt.get("coverage") == "complete" or receipt.get("source_review_complete"))
        and not receipt.get("review_deferred", receipt.get("deferred"))
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
            resolve_for_scope(state, item, key, value, proof)
        for key, value in receipt.get("validation_resolutions", {}).items():
            resolve_for_scope(state, item, key, value, proof, allow_validation=True)
        # Other parallel scopes own their outstanding work. Global closure is
        # checked by complete(), without spending retries on another review.
        closed = closed and scope_closed(state, item)
    missing_dispositions = {}
    dispositions = receipt.get("dispositions", {})
    for path in item["paths"] if item["kind"] == "files" else []:
        original = next(x for x in state["inventory"] if x["path"] == path)
        value = dispositions.get(path)
        if original["kind"] == "unavailable":
            missing_dispositions[path] = "Tracked content is unavailable in the pinned snapshot"
        elif original["kind"] == "text" and value and value.get("disposition") != "reviewed":
            missing_dispositions[path] = "Native file disposition does not confirm a complete text review"
        elif original["kind"] != "text" and not (
            value and value.get("reason")
            and value.get("disposition") in {"reviewed", "not_applicable"}
        ):
            missing_dispositions[path] = "Native report lacks an explicit reviewed or not-applicable disposition for this non-text file"
    if closed and not missing_dispositions:
        item["status"] = "completed"
        if item["kind"] == "files":
            for path in item["paths"]:
                value = dispositions.get(
                    path,
                    {"disposition": "reviewed", "reason": "Complete exact-scope static review"},
                )
                state["files"][path] = {**value, "receipt": proof["archive_key"]}
                if path in credited:
                    state["files"][path].update(
                        review_basis="sealed_file_surface", native_coverage=receipt["coverage"]
                    )
            reconcile_validation_candidates(state)
        state["last_progress_at"] = at.isoformat()
        return True
    if item["kind"] == "files" and state.get("incomplete_review_policy") == "isolate_blockers":
        isolate_file_scope(state, item, {**missing_dispositions, **receipt.get("file_blockers", {})}, proof)
        return True
    if item["kind"] == "files" and credited:
        from .coverage_file_controls import _new_scope

        remaining = [p for p in item["paths"] if state["files"][p]["disposition"] not in {"reviewed", "not_applicable", "excluded"}]
        if remaining:
            item["status"] = "split"
            _new_scope(state, "files", remaining, item["id"])
            return True
    if item["kind"] == "boundary":
        blocked = blocked_paths(state)
        owners = set(receipt.get("followup_files", {})) & blocked
        for question in receipt.get("deferred", []):
            referenced = _referenced_files(question.get("paths", []), state["files"], {})
            if referenced and referenced <= blocked:
                owners.update(referenced)
        if owners:
            item.update(
                status="blocked", quarantined=True, blocked_by_files=sorted(owners),
                error="Boundary review depends on isolated files; automatic retries are disabled",
            )
            return True
    retry(
        state, item, "Review coverage remains incomplete",
        split=state.get("incomplete_review_policy", "split") == "split", at=at,
    )
    return True


def retry(state, item, reason, *, split=False, at=None):
    at = at or now()
    item["error"] = reason
    if split and len(item["paths"]) > 1:
        from .coverage_file_controls import _new_scope

        midpoint = len(item["paths"]) // 2
        children = []
        for paths in (item["paths"][:midpoint], item["paths"][midpoint:]):
            if item["kind"] == "boundary":
                children.append(_new_scope(state, "boundary", paths, item["id"], obligation=True))
            else:
                child = task(item["kind"], paths, parent=item["id"])
                children.append(state["tasks"].setdefault(child["id"], child))
        item["status"] = "split"
        if item["kind"] == "boundary":
            assign_deferred(state, children, item["deferred_keys"])
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
    """Assign each required path to a component that is authorized to review it."""
    assign_questions(state, scopes, keys)


def plan_boundaries(state):
    if state["boundary_planned"] or state["architecture"] is None:
        return
    if any(
        x["kind"] == "files" and x["status"] in {"pending", "running"}
        for x in state["tasks"].values()
    ):
        return
    allowed = set(state["files"]) - set(state.get("exclude_paths", [])) - blocked_paths(state)
    anchors = {path for path in state["architecture"].get("paths", []) if path in allowed}
    for value in state["deferred"].values():
        for pattern in value.get("paths", []):
            anchors.update(
                path
                for path in allowed
                if path == pattern or path.startswith(pattern.rstrip("/") + "/")
            )
    if not anchors:
        anchors = allowed
    if anchors:
        scopes = []
        max_text_bytes = batch_text_limit(state)
        for paths in batches(
            [x for x in state["inventory"] if x["path"] in anchors], max_text_bytes=max_text_bytes
        ):
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
            if proof.get("source_projection"):
                # Preserve native locations/line numbers while making their
                # derived review revision visible to downstream summarizers.
                source["source_projection"] = {
                    k: proof["source_projection"][k]
                    for k in ("source_revision", "revision", "paths")
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
            if c.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise RuntimeError("Coverage journal requires write-ahead logging")
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

    def _retention_metadata(self, repository=None):
        """Keep historical findings out of Python and out of long-lived read cursors."""
        query = (
            "SELECT scan_id,directory,active,"
            "json_extract(payload,'$.finished_at'),"
            "json_extract(payload,'$.cleanup_confirmed'),"
            "json_extract(payload,'$.cleanup_finished_at'),"
            "json_extract(payload,'$.deadline_at'),"
            "json_extract(payload,'$.lineage') FROM campaigns"
        )
        params = ()
        if repository is not None:
            query += " WHERE repository=?"
            params = (repository,)
        with self.connect() as c:
            rows = c.execute(query, params).fetchall()
        metadata = []
        for scan_id, directory, active, finished, cleaned, cleanup_at, deadline, parents in rows:
            lineage = json.loads(parents)
            if not isinstance(lineage, list) or any(not isinstance(p, str) for p in lineage):
                raise ValueError("Invalid retained coverage lineage")
            metadata.append({
                "scan_id": scan_id, "directory_path": directory, "active": bool(active),
                "finished_at": finished, "cleanup_confirmed": bool(cleaned),
                "cleanup_finished_at": cleanup_at, "deadline_at": deadline, "lineage": lineage,
            })
        return metadata

    def expired(self, *, at=None):
        at = at or now()
        all_states = self._retention_metadata()
        referenced = {
            parent
            for s in all_states
            if not s["finished_at"]
            or not s["cleanup_confirmed"]
            or at < retention_anchor(s) + RETENTION
            for parent in s["lineage"]
        }
        return [
            self.get(s["scan_id"], s["directory_path"])
            for s in all_states
            if not s["active"]
            and s["scan_id"] not in referenced
            and s["finished_at"]
            and at >= retention_anchor(s) + RETENTION
        ]

    def retained_until(self, state):
        """Continuations protect their source journal without mutating its report."""
        related = self._retention_metadata(state["managed_repo_id"])
        times = [
            (retention_anchor(s) if s["finished_at"] else datetime.fromisoformat(s["deadline_at"]))
            + RETENTION
            for s in related
            if s["scan_id"] == state["scan_id"] or state["scan_id"] in s["lineage"]
        ]
        return max(times)
