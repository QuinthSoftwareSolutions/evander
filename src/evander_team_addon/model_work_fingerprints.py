"""Team work fingerprints models."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from .model_inbox_memory import (
    AgentInboxItem,
)
from .model_reviews import (
    ReviewRequest,
)


def wake_work_fingerprint(
    *,
    inbox_items: Sequence[AgentInboxItem],
    reviews: Sequence[ReviewRequest],
) -> str | None:
    """Return a stable snapshot of the inbox and review work offered on a wake."""

    if not inbox_items and not reviews:
        return None
    payload = wake_work_record_fingerprints(
        inbox_items=inbox_items,
        reviews=reviews,
    )
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def wake_work_record_fingerprints(
    *,
    inbox_items: Sequence[AgentInboxItem],
    reviews: Sequence[ReviewRequest],
) -> dict[str, str]:
    records: dict[str, str] = {}
    for kind, rows in (("inbox", inbox_items), ("review", reviews)):
        for row in rows:
            serialized = json.dumps(
                row.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            records[f"{kind}:{row.id}"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return records
