"""Exact, evidence-backed file isolation and deterministic exclusion matching."""

from fnmatch import fnmatchcase
from pathlib import PurePosixPath


def matches_pattern(path, pattern):
    """Case-insensitive path glob; matched directories include descendants."""
    parts = tuple(path.casefold().split("/"))
    directory_only = pattern.endswith("/")
    pattern = pattern.rstrip("/").casefold()
    if "/" not in pattern:
        names = parts[:-1] if directory_only else parts
        return any(fnmatchcase(name, pattern) for name in names)
    patterns = tuple(pattern.split("/"))

    def closure(positions):
        # ** can consume zero segments. Iterate instead of recursing through
        # user-supplied patterns or deeply nested repository paths. A trailing
        # slash followed by ** requires a child, so bin/** cannot match a file
        # named bin in place of that directory.
        for index in range(len(patterns)):
            if index in positions and patterns[index] == "**" and index < len(patterns) - 1:
                positions.add(index + 1)
        return positions

    positions = closure({0})
    for index, part in enumerate(parts):
        following = set()
        for position in positions:
            if position == len(patterns):
                continue
            if patterns[position] == "**":
                following.add(position)
                if position == len(patterns) - 1:
                    following.add(position + 1)
            elif fnmatchcase(part, patterns[position]):
                following.add(position + 1)
        positions = closure(following)
        if len(patterns) in positions and (not directory_only or index < len(parts) - 1):
            return True
    return False


def resolve_exclusions(entries, exact, patterns):
    paths = {entry["path"] for entry in entries}
    if not set(exact) <= paths:
        raise ValueError("An excluded path is absent from the pinned inventory")
    matches = {
        pattern: sorted(path for path in paths if matches_pattern(path, pattern))
        for pattern in patterns
    }
    return sorted(set(exact).union(*(set(value) for value in matches.values()))), matches


def _exact_reference(value, assigned):
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    normalized = path.as_posix()
    return normalized if normalized in assigned else None


def native_file_blockers(coverage, assigned):
    """Use sealed structured references, never filenames guessed from prose."""
    assigned = set(assigned)
    result = {}
    for question in coverage.get("deferred", []):
        references = question.get("paths")
        reason = question.get("reason")
        if (
            not isinstance(references, list)
            or not references
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            continue
        paths = [_exact_reference(value, assigned) for value in references]
        if all(paths):
            for path in paths:
                result.setdefault(path, reason[:2000])
    for surface in coverage.get("surfaces", []):
        label, reason = surface.get("label", ""), surface.get("notes")
        if (
            surface.get("disposition") != "needs_follow_up"
            or not isinstance(label, str)
            or not label.startswith("file:")
        ):
            continue
        path = _exact_reference(label[5:], assigned)
        if path and isinstance(reason, str) and reason.strip():
            result[path] = reason[:2000]
    return result


def blocked_paths(state):
    return {
        path
        for path in state.get("file_blockers", {})
        if path not in state.get("exclude_paths", [])
        and state["files"][path]["disposition"] not in {"reviewed", "not_applicable"}
    }


def record_file_blockers(state, blockers, proof):
    recorded = state.setdefault("file_blockers", {})
    changed = set()
    for path, reason in blockers.items():
        if path not in state["files"] or state["files"][path]["disposition"] not in {
            "pending",
            "blocked",
        }:
            continue
        recorded.setdefault(
            path,
            {
                "reason": reason,
                "receipt": proof["archive_key"],
                "provider_scan_id": proof["provider_scan_id"],
            },
        )
        changed.add(path)
    return changed


def _new_scope(state, kind, paths, parent, *, obligation=False):
    from .coverage import digest, task

    child = task(kind, paths, parent=parent)
    existing = state["tasks"].get(child["id"])
    if existing and (existing["status"] == "completed" or (obligation and existing["status"] in {"split", "excluded"})):
        # Preserve accepted scopes; a new obligation may need the same paths.
        child["id"] = digest([child["id"], parent, state["scan_id"], "file_controls"])[:24]
        generation = 0
        while obligation and state["tasks"].get(child["id"], {}).get("status") in {"completed", "split", "excluded"}:
            generation += 1
            child["id"] = digest([kind, paths, parent, state["scan_id"], "obligation", generation])[:24]
    return state["tasks"].setdefault(child["id"], child)


def refresh_blocked_followups(state):
    """Keep dependent questions unresolved and outside ordinary boundary work."""
    from .coverage_questions import local_paths, needed_paths

    blocked = blocked_paths(state)
    state["blocked_followups"] = {}
    for key, question in state["deferred"].items():
        if question.get("resolution"):
            continue
        referenced = needed_paths(state, question)
        owners = sorted((referenced or set()) & blocked)
        if owners:
            state["blocked_followups"][key] = {"blocked_by": owners}
    for item in state["tasks"].values():
        if item["kind"] == "boundary":
            item["deferred_keys"] = [
                key for key in item["deferred_keys"]
                if key not in state["blocked_followups"]
                or (local_paths(state, item, key) or set()) - blocked
            ]


def refresh_blocked_work(state):
    """Park known blockers and their dependent questions without resolving them."""
    from .coverage import assign_deferred, task

    if not (
        state.get("file_blockers")
        or state.get("blocked_followups")
        or any(item.get("quarantined") for item in state["tasks"].values())
    ):
        return
    blocked = blocked_paths(state)
    for path in blocked:
        state["files"][path].update(
            disposition="blocked", receipt=None, reason=state["file_blockers"][path]["reason"]
        )
    for item in list(state["tasks"].values()):
        if item.get("quarantined"):
            if set(item.get("blocked_by_files", item["paths"])) & blocked:
                item["status"] = "blocked"
                continue
            item.pop("quarantined")
            item["status"] = "pending"
        if item["kind"] == "architecture" or item["status"] != "pending":
            continue
        remaining = [path for path in item["paths"] if path not in blocked]
        if remaining == item["paths"]:
            continue
        if remaining:
            item["status"] = "split"
            _new_scope(state, item["kind"], remaining, item["id"])
        elif len(item["paths"]) > 1:
            # Individual quarantines own every file. Retrying all of them must
            # not also reactivate an overlapping copy of the original batch.
            item["status"] = "split"
        else:
            item.update(status="blocked", quarantined=True)
    refresh_blocked_followups(state)
    for path in sorted(blocked):
        scope = task("files", [path])
        item = state["tasks"].setdefault(scope["id"], scope)
        if item["status"] == "completed":
            raise ValueError("A blocked file conflicts with an accepted file scope")
        item.update(
            status="blocked", quarantined=True, error=state["file_blockers"][path]["reason"]
        )
        item["deferred_keys"] = [
            key
            for key, value in state["blocked_followups"].items()
            if value["blocked_by"][0] == path
        ]
    scopes = [
        t for t in state["tasks"].values() if t["kind"] == "boundary" and t["status"] == "pending"
    ]
    if scopes:
        assign_deferred(state, scopes, state["deferred"])


def isolate_file_scope(state, item, blockers, proof):
    """A partial report earns no credit; only the remainder gets a new review."""
    eligible = {path: reason for path, reason in blockers.items() if path in item["paths"]}
    isolated = record_file_blockers(state, eligible, proof)
    if not isolated:
        item.update(
            status="blocked",
            error="Incomplete review did not identify exact blocked files; operator attention required",
        )
        for path in item["paths"]:
            if state["files"][path]["disposition"] == "pending":
                state["files"][path].update(disposition="blocked", reason=item["error"])
        return
    remaining = [path for path in item["paths"] if path not in blocked_paths(state)]
    item["status"] = "split" if remaining or len(item["paths"]) > 1 else "blocked"
    if remaining:
        _new_scope(state, "files", remaining, item["id"])
    refresh_blocked_work(state)


def release_file_blockers(state, paths):
    """Only an explicit continuation input may retry previously isolated files."""
    if not set(paths) <= state.get("file_blockers", {}).keys():
        raise ValueError("retry_blocked_paths must name previously isolated files")
    for path in paths:
        del state["file_blockers"][path]
        if state["files"][path]["disposition"] not in {"reviewed", "not_applicable", "excluded"}:
            state["files"][path].update(disposition="pending", receipt=None, reason=None)
    if paths and state["boundary_planned"]:
        from .coverage import batches, batch_text_limit

        covered = {
            path
            for item in state["tasks"].values()
            if item["kind"] == "boundary" and item["status"] == "pending"
            for path in item["paths"]
        }
        entries = [entry for entry in state["inventory"] if entry["path"] in set(paths) - covered]
        for group in batches(entries, max_text_bytes=batch_text_limit(state)):
            _new_scope(state, "boundary", group, "explicit_blocker_retry", obligation=True)
