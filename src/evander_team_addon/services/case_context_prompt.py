"""An absolute wake-payload ceiling, independent of configurable view budgets."""

from collections import Counter
from copy import deepcopy
import json

MAX_CASE_CONTEXT_BRIEF_CHARS = 6000


def compact_case_contexts(contexts):
    """Bound the whole pretty-printed JSON field, including escaping and metadata."""

    def size(rows):
        return len(json.dumps({"case_contexts": rows}, sort_keys=True, indent=2))

    rows = deepcopy(contexts)
    if size(rows) <= MAX_CASE_CONTEXT_BRIEF_CHARS:
        return rows
    # The runtime creates one shared snapshot per dispatch, not one per case.
    # Defensive projection also prevents legacy/foreign lease payloads bypassing the cap.
    first = rows[0] if rows else {}
    row = {
        key: first[key]
        for key in (
            "snapshot_id",
            "content_digest",
            "read",
            "mode",
            "baseline_snapshot_id",
            "changed_record_count",
        )
        if key in first
    }
    row.update(
        complete=False,
        case_count=len(first.get("case_ids", [])),
        omitted_brief_count=max(0, len(rows) - 1),
        omissions=[{"kind": "wake_budget", "section": "case_context"}],
        omission_counts=dict(
            Counter(str(w.get("kind", "unknown")) for w in first.get("omissions", []))
        ),
    )
    # IDs and tool references come from the host. Still handle malformed imported payloads.
    if size([row]) > MAX_CASE_CONTEXT_BRIEF_CHARS:
        return [
            {
                "complete": False,
                "text": "Case context exceeds the wake budget; use team_get_case_context.",
            }
        ]
    text = str(first.get("text", ""))
    lower, upper = 0, len(text)
    while lower < upper:
        middle = (lower + upper + 1) // 2
        row["text"] = text[:middle]
        if size([row]) <= MAX_CASE_CONTEXT_BRIEF_CHARS:
            lower = middle
        else:
            upper = middle - 1
    row["text"] = text[:lower]
    return [row]
