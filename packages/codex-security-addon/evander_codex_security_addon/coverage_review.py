"""Separate sealed source-review evidence from unverified vulnerability candidates."""

from copy import deepcopy

from .coverage_file_controls import _exact_reference, matches_pattern

REVIEWED = {"reported", "no_issue_found", "not_applicable"}


def candidate(value):
    item = value.get("candidate")
    return isinstance(item, dict) and all(
        isinstance(item.get(key), str) and item[key].strip() for key in ("title", "source")
    )


def file_outcomes(coverage, assigned):
    assigned = set(assigned)
    reviewed, blocked = {}, {}
    for surface in coverage.get("surfaces", []):
        label, reason = surface.get("label", ""), surface.get("notes")
        if not isinstance(label, str) or not label.startswith("file:"):
            continue
        path = _exact_reference(label[5:], assigned)
        if not path:
            continue
        if surface.get("disposition") == "needs_follow_up":
            blocked[path] = reason or "Assigned file review remains unfinished"
        elif surface.get("disposition") in REVIEWED and isinstance(reason, str) and reason.strip():
            reviewed[path] = {
                "disposition": "not_applicable" if surface["disposition"] == "not_applicable" else "reviewed",
                "reason": reason,
            }
    for path in blocked:
        reviewed.pop(path, None)
    return reviewed, blocked


def review_facts(coverage, assigned, *, kind="files", known_candidates=None, reviewed_paths=(), checkpoint_keys=(), exclusions=None):
    """Called only after native identity, snapshot, scope and seals are verified.

    A partial native scan can establish individual reviewed files. It cannot
    establish source review merely by naming a candidate or an open question.
    Unknown source work and contradictory file surfaces remain blocking.
    """
    assigned = set(assigned)
    reviewed, blocked = file_outcomes(coverage, assigned)
    surfaces = coverage.get("surfaces", [])
    marker = any(
        s.get("label") == "review:" + kind and s.get("disposition") in REVIEWED and s.get("notes")
        for s in surfaces
    )
    source_proven = bool(assigned) and (
        assigned <= reviewed.keys() if kind == "files"
        else marker and assigned <= set(reviewed_paths)
    )
    validation_resolutions = {
        s["label"][11:]: {"disposition": "validation_pending", "reason": s["notes"]}
        for s in surfaces
        if source_proven and isinstance(s.get("label"), str)
        and s["label"].startswith("validation:") and s["label"][11:] in checkpoint_keys
        and s.get("disposition") == "needs_follow_up" and s.get("notes")
    }
    validations, required, candidates = [], [], []
    for question in coverage.get("deferred", []):
        value = deepcopy(question)
        if not candidate(value) and str(value.get("id")) in (known_candidates or {}):
            value["candidate"] = deepcopy(known_candidates[str(value["id"])])
        if candidate(value):
            candidates.append(value)
        references = value.get("paths") or []
        paths = [_exact_reference(p, assigned) for p in references]
        if (
            candidate(value) and paths and all(paths)
        ) or str(value.get("id")) in validation_resolutions:
            validations.append(value)
        else:
            required.append(question)
    advisory_surfaces, source_followups = [], []
    for surface in surfaces:
        if surface.get("disposition") != "needs_follow_up":
            continue
        label = surface.get("label", "")
        validation = (
            isinstance(label, str) and label.startswith("candidate:")
        ) or (
            isinstance(label, str) and label.startswith("validation:")
            and label[11:] in validation_resolutions
        ) or (
            isinstance(label, str) and label.startswith("deferred:")
            and label[9:] in validation_resolutions
        )
        if validation:
            advisory_surfaces.append({"question": surface.get("notes") or label, "validation_status": "unverified", "surface": deepcopy(surface)})
        else:
            source_followups.append(surface)
    eligible = deepcopy(reviewed)
    for question in required:
        references = question.get("paths") or []
        paths = [_exact_reference(p, assigned) for p in references]
        if not paths or not all(paths):
            eligible.clear()
        else:
            for path in paths:
                eligible.pop(path, None)
                if isinstance(question.get("reason"), str) and question["reason"].strip():
                    blocked.setdefault(path, question["reason"][:2000])
    for surface in source_followups:
        label = surface.get("label", "")
        if not isinstance(label, str) or not label.startswith("file:"):
            eligible.clear()
    omissions = coverage.get("explicitExclusions", []) if exclusions is None else exclusions
    for value in omissions:
        path = value if isinstance(value, str) else value.get("path")
        pattern = value.get("pattern") if isinstance(value, dict) else None
        if path in assigned:
            eligible.pop(path, None)
        elif pattern:
            for path in list(eligible):
                if matches_pattern(path, pattern):
                    eligible.pop(path)
        else:
            eligible.clear()
    # An unexplained partial status cannot be upgraded through file summaries.
    explained = bool(required or source_followups or omissions or validations or advisory_surfaces)
    completeness = coverage.get("completeness")
    if completeness not in {"complete", "partial"} or completeness == "partial" and not explained:
        eligible.clear()
    complete = (
        completeness == "complete"
        or completeness == "partial" and source_proven and bool(validations or advisory_surfaces)
    ) and not (required or source_followups or omissions)
    if not complete:
        validation_resolutions = {}
    return {
        "source_dispositions": eligible,
        "source_review_complete": bool(complete),
        "review_deferred": required,
        "needs_follow_up": bool(source_followups),
        "validation_questions": validations,
        "validation_candidates": candidates,
        "validation_notes": advisory_surfaces,
        "validation_resolutions": validation_resolutions,
        "file_blockers": blocked,
    }


def record_validation_candidates(state, proof, questions):
    from .coverage_questions import canonical_key

    for incoming in questions:
        if not candidate(incoming):
            continue
        identifier = str(incoming["id"])
        native_key = proof["provider_scan_id"] + ":" + identifier
        key = canonical_key(state, native_key)
        if key not in state["deferred"] and identifier in state["deferred"]:
            key = canonical_key(state, identifier)
        question = state["deferred"].get(key)
        if question is None or question.get("candidate") != incoming["candidate"]:
            continue
        receipts = question.setdefault("validation_candidate_receipts", [])
        if proof["archive_key"] not in receipts:
            receipts.append(proof["archive_key"])


def candidate_context(state):
    from .coverage_questions import canonical_key

    result = {key: q["candidate"] for key, q in state["deferred"].items() if candidate(q)}
    for key in state.get("deferred_aliases", {}):
        target = canonical_key(state, key)
        if target in result:
            result[key] = result[target]
    return result


def apply_file_evidence(state, dispositions, proof, native_coverage):
    inventory = {entry["path"]: entry for entry in state["inventory"]}
    credited = []
    for path, value in dispositions.items():
        if path not in state["files"] or state["files"][path]["disposition"] in {"reviewed", "not_applicable", "excluded"}:
            continue
        if inventory[path]["kind"] == "unavailable" or (
            inventory[path]["kind"] == "text" and value["disposition"] != "reviewed"
        ):
            continue
        blocker = state.get("file_blockers", {}).get(path)
        if blocker and blocker["receipt"] != proof["archive_key"]:
            continue
        if blocker:
            del state["file_blockers"][path]
            state.setdefault("review_classification_corrections", []).append({
                "path": path, "receipt": proof["archive_key"],
                "reason": "Sealed file review is complete; candidate validation does not make the source unreadable",
            })
        state["files"][path] = {
            **deepcopy(value), "receipt": proof["archive_key"],
            "review_basis": "sealed_file_surface", "native_coverage": native_coverage,
        }
        credited.append(path)
    return credited


def reconcile_validation_candidates(state):
    """Close source-review obligations, never claim a candidate was validated."""
    for key, question in state["deferred"].items():
        if not question.get("validation_candidate_receipts") or not candidate(question):
            continue
        references = question.get("paths") or []
        paths = [_exact_reference(p, state["files"]) for p in references]
        if not paths or not all(paths) or not all(
            state["files"][p]["disposition"] in {"reviewed", "not_applicable", "excluded"} for p in paths
        ):
            if (question.get("resolution") or {}).get("disposition") == "validation_pending":
                question["resolution"] = None
            continue
        if question.get("resolution") and question["resolution"].get("disposition") != "validation_pending":
            continue
        receipts = sorted(question["validation_candidate_receipts"])
        question["resolution"] = {
            "disposition": "validation_pending", "validation_status": "unverified",
            "reason": "Referenced source review is complete or explicitly excluded; candidate validity is not established",
            "receipts": receipts,
        }
        state.setdefault("open_questions", {})["validation:" + key] = {
            "question": question.get("reason") or question["candidate"]["title"],
            "candidate": deepcopy(question["candidate"]), "paths": list(references),
            "validation_status": "unverified", "receipt": receipts[0], "receipts": receipts,
        }
