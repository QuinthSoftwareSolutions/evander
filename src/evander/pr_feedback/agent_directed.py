from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from evander.models import PrFeedbackItem


def annotate_agent_directed_feedback(
    item: PrFeedbackItem,
    *,
    agent_author_names: Iterable[object],
    agent_mention_names: Iterable[object],
    agent_author_ids: Iterable[object] = (),
    agent_mention_ids: Iterable[object] = (),
) -> PrFeedbackItem:
    author_names = _normalize_identity_set(agent_author_names)
    author_ids = _normalize_identity_id_set((*agent_author_ids, *agent_author_names))
    mention_names = _normalize_identity_set(agent_mention_names) or author_names
    mention_ids = _normalize_identity_id_set((*agent_mention_ids, *agent_mention_names))
    if not mention_ids:
        mention_ids = author_ids
    if not author_names and not author_ids and not mention_names and not mention_ids:
        return item

    metadata = dict(item.metadata or {})
    self_authored = (
        _normalize_identity(item.author) in author_names
        or _metadata_contains_identity(
            metadata,
            keys=(
                "comment_author_names",
                "comment_author_unique_names",
                "author_names",
                "author_unique_names",
            ),
            identities=author_names,
        )
        or _metadata_contains_identity_id(
            metadata,
            keys=("comment_author_ids", "author_ids"),
            identities=author_ids,
        )
    )
    if self_authored:
        metadata.update(
            {
                "agent_directed": False,
                "agent_directed_same_thread": False,
                "agent_directed_mentioned": False,
                "agent_directed_author_self": True,
                "agent_directed_reason": "self_authored",
            }
        )
        return item.model_copy(update={"metadata": metadata})

    same_thread = (
        _metadata_contains_identity(
            metadata,
            keys=("prior_thread_comment_author_names", "prior_thread_comment_author_unique_names"),
            identities=author_names,
        )
        or _metadata_contains_identity_id(
            metadata,
            keys=("prior_thread_comment_author_ids",),
            identities=author_ids,
        )
    )
    mentioned = (
        _metadata_contains_identity(
            metadata,
            keys=("mentioned_names",),
            identities=mention_names,
        )
        or _metadata_contains_identity_id(
            metadata,
            keys=("mentioned_ids",),
            identities=mention_ids,
        )
        or _body_mentions_identity(item.body, mention_names)
        or _body_mentions_identity_id(item.body, mention_ids)
    )
    agent_directed = same_thread or mentioned
    metadata.update(
        {
            "agent_directed": agent_directed,
            "agent_directed_same_thread": same_thread,
            "agent_directed_mentioned": mentioned,
            "agent_directed_author_self": False,
            "agent_directed_reason": (
                "same_thread" if same_thread else "mention" if mentioned else "not_agent_directed"
            ),
        }
    )
    return item.model_copy(update={"metadata": metadata})


def _normalize_identity(value: object) -> str:
    text = str(value or "").strip().removeprefix("@")
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def _normalize_identity_id(value: object) -> str:
    text = str(value or "").strip().removeprefix("@").strip()
    if (text.startswith("<") and text.endswith(">")) or (
        text.startswith("{") and text.endswith("}")
    ):
        text = text[1:-1].strip()
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def _normalize_text(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def _normalize_identity_set(values: Iterable[object]) -> set[str]:
    return {normalized for value in values if (normalized := _normalize_identity(value))}


def _normalize_identity_id_set(values: Iterable[object]) -> set[str]:
    return {normalized for value in values if (normalized := _normalize_identity_id(value))}


def _metadata_values(metadata: dict[str, Any], key: str) -> list[object]:
    value = metadata.get(key)
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _metadata_contains_identity(
    metadata: dict[str, Any],
    *,
    keys: Iterable[str],
    identities: set[str],
) -> bool:
    if not identities:
        return False
    return any(
        _normalize_identity(value) in identities
        for key in keys
        for value in _metadata_values(metadata, key)
    )


def _metadata_contains_identity_id(
    metadata: dict[str, Any],
    *,
    keys: Iterable[str],
    identities: set[str],
) -> bool:
    if not identities:
        return False
    return any(
        _normalize_identity_id(value) in identities
        for key in keys
        for value in _metadata_values(metadata, key)
    )


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value or "")


def _body_mentions_identity(body: str, identities: set[str]) -> bool:
    if not identities:
        return False
    visible = _strip_html(body)
    normalized_visible = _normalize_text(visible)
    normalized_raw = _normalize_text(body)
    for identity in identities:
        mention_token = f"@{identity}"
        if normalized_visible == mention_token or normalized_visible.startswith(
            f"{mention_token} "
        ):
            return True
        if f" {mention_token}" in normalized_visible:
            return True
        if "data-vss-mention" in normalized_raw and identity in normalized_raw:
            return True
    return False


def _body_mentions_identity_id(body: str, identities: set[str]) -> bool:
    if not identities:
        return False
    for match in re.finditer(r"@<\s*([^>]+?)\s*>", body or ""):
        if _normalize_identity_id(match.group(1)) in identities:
            return True
    return False
