from __future__ import annotations

import json
from pathlib import Path

from evander.pr_feedback.store import PrFeedbackStore


def prune_pr_map(path: Path, *, session_ids: set[str]) -> tuple[int, set[tuple[str, str, str]]]:
    """Remove PR routing entries owned by deleted sessions."""

    if not path.exists():
        return 0, set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, set()
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return 0, set()

    removed_streams: set[tuple[str, str, str]] = set()
    kept_entries: dict[str, object] = {}
    removed_count = 0
    for entry_key, raw_entry in entries.items():
        if not isinstance(raw_entry, dict):
            kept_entries[entry_key] = raw_entry
            continue
        session_id = str(raw_entry.get("session_id", "")).strip()
        if session_id in session_ids:
            provider = str(raw_entry.get("provider", "")).strip()
            repo = str(raw_entry.get("repo", "")).strip()
            pr_id = str(raw_entry.get("pr_id", "")).strip()
            if provider and repo and pr_id:
                removed_streams.add((provider, repo, pr_id))
            removed_count += 1
            continue
        kept_entries[entry_key] = raw_entry

    if removed_count:
        payload["entries"] = kept_entries
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return removed_count, removed_streams


def prune_pr_feedback_store(
    path: Path,
    *,
    streams: set[tuple[str, str, str]],
) -> tuple[int, int]:
    """Remove feedback cursors and records for PR streams no longer routed."""

    if not streams or not path.exists():
        return 0, 0
    return PrFeedbackStore(path).prune_streams(streams)


_prune_pr_map = prune_pr_map
_prune_pr_feedback_store = prune_pr_feedback_store
