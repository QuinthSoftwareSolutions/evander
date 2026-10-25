"""Stable required checkpoints and scope-local closure over immutable reports."""

from copy import deepcopy
from pathlib import PurePosixPath

from .coverage_file_controls import blocked_paths, matches_pattern

OUTCOMES = {"reported", "rejected", "not_applicable", "no_issue_found", "resolved"}


def canonical_key(state, key):
    aliases = state.get("deferred_aliases", {})
    seen = set()
    while key in aliases:
        if key in seen:
            raise ValueError("Required checkpoint aliases contain a cycle")
        seen.add(key)
        key = aliases[key]
    return key


def referenced_paths(state, question):
    from .coverage import _referenced_files

    references = question.get("paths")
    if not references:
        return None
    return _referenced_files(references, state["files"], {})


def closed_paths(question):
    return {path for result in question.get("scope_resolutions", []) for path in result["paths"]}


def reconcile_question(state, key):
    question = state["deferred"][canonical_key(state, key)]
    resolution = question.get("resolution") or {}
    paths = referenced_paths(state, question)
    if question.get("paths") and paths is None:
        question["resolution"] = None
        return
    if (
        resolution
        and resolution.get("disposition") != "scope_resolved"
        and not resolution.get("explicit_exclusion")
    ):
        return
    question["resolution"] = None
    if paths and paths <= set(state.get("exclude_paths", [])):
        question["resolution"] = {
            "disposition": "excluded",
            "explicit_exclusion": True,
            "reason": "Every referenced file is explicitly excluded by the scan request",
            "paths": sorted(paths),
            "scan_id": state["scan_id"],
        }
        return
    if (
        paths is not None
        and paths
        and paths <= closed_paths(question) | set(state.get("exclude_paths", []))
    ):
        question["resolution"] = {
            "disposition": "scope_resolved",
            "reason": "Every required scope part has a verified resolution or an explicit scan exclusion",
            "scope_receipts": sorted(
                {result["receipt"] for result in question.get("scope_resolutions", [])}
            ),
            "excluded_paths": sorted(paths & set(state.get("exclude_paths", []))),
        }


def needed_paths(state, question):
    paths = referenced_paths(state, question)
    if paths is None:
        return None
    return paths - closed_paths(question) - set(state.get("exclude_paths", []))


def local_paths(state, item, key):
    key = canonical_key(state, key)
    paths = needed_paths(state, state["deferred"][key])
    if key in item.get("deferred_paths", {}):
        value = item["deferred_paths"][key]
        if value is not None:
            return (
                set(value)
                & set(item["paths"])
                & (referenced_paths(state, state["deferred"][key]) or set())
            )
    return None if paths is None else paths & set(item["paths"])


def scope_closed(state, item):
    for key in item["deferred_keys"]:
        question = state["deferred"][canonical_key(state, key)]
        if question.get("resolution"):
            continue
        if question.get("paths") and referenced_paths(state, question) is None:
            # This obligation is retained for operator scope clarification. An
            # old frozen component must not be required to review unknown paths.
            continue
        paths = local_paths(state, item, key)
        if paths is None or not paths <= closed_paths(question) | set(
            state.get("exclude_paths", [])
        ):
            return False
    return True


def resolve_for_scope(state, item, key, value, proof):
    key = canonical_key(state, key)
    if key not in {canonical_key(state, own) for own in item["deferred_keys"]}:
        return
    if value.get("disposition") not in OUTCOMES or not value.get("reason"):
        return
    question = state["deferred"][key]
    if question.get("paths") and referenced_paths(state, question) is None:
        return
    paths = local_paths(state, item, key)
    result = {**deepcopy(value), "receipt": proof["archive_key"]}
    if paths is None:
        question["resolution"] = result
    elif paths:
        result["paths"] = sorted(paths & set(item["paths"]))
        results = question.setdefault("scope_resolutions", [])
        if result not in results:
            results.append(result)
        reconcile_question(state, key)


def _retain_resolution(question, value, references):
    retained = {"value": deepcopy(value), "references": deepcopy(references)}
    if retained not in question.setdefault("retained_resolutions", []):
        question["retained_resolutions"].append(retained)


def _extend_references(question, incoming):
    if not incoming:
        return
    original = question.get("paths") or []
    combined = sorted(set(original) | set(incoming))
    if set(combined) != set(original):
        result = question.get("resolution") or {}
        if result.get("disposition") in OUTCOMES:
            _retain_resolution(question, result, question.get("paths"))
        question["resolution"] = None
        question["paths"] = combined


def normalize_questions(state, proof_scopes=None):
    """Fold explicit saved-ID aliases, preserving the original source journal.

    A matching local ID in two unrelated reports is not an alias. Old closures
    of a multi-component checkpoint are bounded by their original native scope.
    """
    aliases = state.setdefault("deferred_aliases", {})
    questions = state["deferred"]
    saved = {
        key: {
            name: deepcopy(value.get(name))
            for name in ("paths", "resolution", "scope_resolutions", "retained_resolutions")
        }
        for key, value in questions.items()
    }
    for key, question in questions.items():
        previous = question.get("id")
        if (
            isinstance(previous, str)
            and previous != key
            and (previous in questions or previous in aliases)
        ):
            aliases[key] = previous
    for key in list(aliases):
        target = canonical_key(state, key)
        if target not in questions:
            raise ValueError("Required checkpoint alias has no retained target")
        aliases[key] = target
    for key, question in list(questions.items()):
        target = canonical_key(state, key)
        root = questions[target]
        if key != target:
            # Preserve additional explicit references; pathless echoes must not
            # replace the original checkpoint's repository-wide target list.
            _extend_references(root, saved[key]["paths"])
            for result in saved[key]["scope_resolutions"] or []:
                if result not in root.setdefault("scope_resolutions", []):
                    root["scope_resolutions"].append(deepcopy(result))
            for retained in saved[key]["retained_resolutions"] or []:
                _retain_resolution(root, retained["value"], retained["references"])
        resolution = saved[key]["resolution"] or {}
        if resolution.get("disposition") in OUTCOMES:
            if key != target or saved[key]["paths"] or root.get("paths") or target in aliases.values():
                _retain_resolution(root, resolution, saved[key]["paths"])
                if proof_scopes and key == target:
                    root["resolution"] = None
        if key != target:
            del questions[key]
    for item in state["tasks"].values():
        item["deferred_keys"] = sorted({canonical_key(state, key) for key in item["deferred_keys"]})
        if item.get("deferred_paths"):
            combined = {}
            for key, paths in item["deferred_paths"].items():
                target = canonical_key(state, key)
                if paths is None:
                    combined[target] = None
                elif combined.get(target, []) is not None:
                    combined[target] = sorted(set(combined.get(target, [])) | set(paths))
            item["deferred_paths"] = combined
    for key in questions:
        question = questions[key]
        if proof_scopes:
            for retained in question.get("retained_resolutions", []):
                value = retained["value"]
                scope = proof_scopes.get(value.get("receipt"))
                paths = referenced_paths(state, {"paths": retained["references"]})
                if scope is not None and not retained["references"] and not question.get("paths"):
                    question["resolution"] = deepcopy(value)
                if scope is not None and paths is not None and value.get("disposition") in OUTCOMES:
                    subset = paths if scope == ["."] else paths & set(scope)
                    result = {**deepcopy(value), "paths": sorted(subset)}
                    if subset and result not in question.setdefault("scope_resolutions", []):
                        question["scope_resolutions"].append(result)
        reconcile_question(state, key)


def record_deferred(state, item, values, proof):
    aliases = state.setdefault("deferred_aliases", {})
    local_ids = state.setdefault("deferred_task_ids", {}).setdefault(item["id"], {})
    keys = []
    for value in values:
        identifier = str(value["id"])
        native_key = proof["provider_scan_id"] + ":" + identifier
        if identifier in state["deferred"] or identifier in aliases:
            key = canonical_key(state, identifier)
        else:
            key = canonical_key(state, local_ids.get(identifier, native_key))
        if key not in state["deferred"]:
            state["deferred"][key] = {**deepcopy(value), "resolution": None}
        question = state["deferred"][key]
        _extend_references(question, value.get("paths"))
        reconcile_question(state, key)
        question["last_observation"] = {"reason": value["reason"], "receipt": proof["archive_key"]}
        if native_key != key:
            aliases[native_key] = key
        local_ids[identifier] = key
        keys.append(key)
        if item["kind"] == "boundary" and key not in item["deferred_keys"]:
            paths = needed_paths(state, question)
            if paths is None or paths & set(item["paths"]):
                item["deferred_keys"].append(key)
                item.setdefault("deferred_paths", {})[key] = (
                    None if paths is None else sorted(paths & set(item["paths"]))
                )
    return keys


def assign_questions(state, scopes, keys):
    """Allocate each checkpoint's remaining paths without widening a component."""
    selected = {scope["id"] for scope in scopes}
    for scope in scopes:
        scope["deferred_keys"], scope["deferred_paths"] = [], {}
    for key in sorted({canonical_key(state, key) for key in keys}):
        question = state["deferred"][key]
        reconcile_question(state, key)
        if question.get("resolution"):
            continue
        paths = needed_paths(state, question)
        reserved = [
            item
            for item in state["tasks"].values()
            if item["kind"] == "boundary"
            and item["id"] not in selected
            and item["status"] in {"running", "blocked"}
            and key in item["deferred_keys"]
        ]
        if paths is None:
            if scopes and not reserved and not question.get("paths"):
                scopes[0]["deferred_keys"].append(key)
                scopes[0]["deferred_paths"][key] = None
            continue
        paths -= blocked_paths(state)
        for owner in reserved:
            paths -= local_paths(state, owner, key) or set()
        for scope in scopes:
            part = paths & set(scope["paths"])
            if part:
                scope["deferred_keys"].append(key)
                scope["deferred_paths"][key] = sorted(part)
                paths -= part


def scoped_questions(state, item):
    result = {}
    if item["kind"] != "boundary":
        return result
    for key in item["deferred_keys"]:
        key = canonical_key(state, key)
        question = state["deferred"][key]
        if question.get("resolution"):
            continue
        if question.get("paths") and referenced_paths(state, question) is None:
            continue
        paths = local_paths(state, item, key)
        if paths == set() or (paths is not None and paths <= closed_paths(question)):
            continue
        result[key] = {
            "id": key,
            "reason": "Resolve this checkpoint only for the assigned component paths below. The host retains and separately assigns all other paths. Original requirement: "
            + question.get("reason", "Required checkpoint"),
            "paths": deepcopy(question.get("paths", [])) if paths is None else sorted(paths),
            "host_scope": {
                "path_count": len(item["paths"]),
                "checkpoint_path_count": len(referenced_paths(state, question) or []),
            },
        }
    return result


def ensure_question_scopes(state):
    """Give new or imported cross-scope work an executable bounded owner."""
    from .coverage import batches, batch_text_limit
    from .coverage_file_controls import _new_scope

    if not state["boundary_planned"] or state["stop_reason"] or state["finished_at"]:
        return
    pending = [
        item
        for item in state["tasks"].values()
        if item["kind"] == "boundary" and item["status"] == "pending"
    ]
    assign_questions(state, pending, state["deferred"])
    added = False
    for key, question in state["deferred"].items():
        if question.get("resolution"):
            continue
        paths = needed_paths(state, question)
        if paths is None:
            continue
        paths -= blocked_paths(state)
        for item in state["tasks"].values():
            if item["kind"] != "boundary":
                continue
            if item["status"] == "running":
                paths -= set(item["paths"])  # Wait for its frozen result first.
            elif item["status"] in {"pending", "blocked"} and key in item["deferred_keys"]:
                paths -= local_paths(state, item, key) or set()
        for group in batches(
            [entry for entry in state["inventory"] if entry["path"] in paths],
            max_text_bytes=batch_text_limit(state),
        ):
            item = _new_scope(state, "boundary", group, "checkpoint:" + key, obligation=True)
            if item["status"] == "blocked":
                # The same bounded failed scope remains a blocker, not a fresh
                # retry generation disguised as a new checkpoint owner.
                item["deferred_keys"].append(key)
                item.setdefault("deferred_paths", {})[key] = group
            added = True
    if added:
        pending = [
            item
            for item in state["tasks"].values()
            if item["kind"] == "boundary" and item["status"] == "pending"
        ]
        assign_questions(state, pending, state["deferred"])


def blocking_exclusions(state, paths, exclusions):
    """Only approved omissions wholly outside this assignment are harmless."""
    excluded = set(state.get("exclude_paths", []))
    inventory = set(state["files"])
    blocking = []
    for value in exclusions:
        exact = (
            value
            if isinstance(value, str)
            else value.get("path")
            if isinstance(value, dict)
            else None
        )
        pattern = value.get("pattern") if isinstance(value, dict) else None
        matches = set()
        valid = True
        if exact is not None:
            path = PurePosixPath(exact) if isinstance(exact, str) and exact else None
            valid = bool(
                path
                and not path.is_absolute()
                and ".." not in path.parts
                and path.as_posix() in inventory
            )
            if valid:
                matches.add(path.as_posix())
        if pattern is not None:
            selected = {
                path
                for path in inventory
                if isinstance(pattern, str) and matches_pattern(path, pattern)
            }
            valid = valid and bool(selected)
            matches.update(selected)
        if not valid or not matches or not matches <= excluded or matches & set(paths):
            blocking.append(value)
    return blocking
