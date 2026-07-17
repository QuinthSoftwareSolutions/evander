from __future__ import annotations

from collections.abc import Iterable
import re

_SECTION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def normalize_managed_section_id(section_id: str) -> str:
    normalized = str(section_id or "").strip()
    if not normalized:
        raise ValueError("managed section_id is required")
    if not _SECTION_ID_RE.fullmatch(normalized):
        raise ValueError(
            "managed section_id must contain only letters, numbers, dots, underscores, colons, or dashes"
        )
    return normalized


def managed_section_markers(section_id: str) -> tuple[str, str]:
    normalized = normalize_managed_section_id(section_id)
    return (
        f"<!-- evander-managed-section:{normalized}:begin -->",
        f"<!-- evander-managed-section:{normalized}:end -->",
    )


def render_managed_section(section_id: str, content: str) -> str:
    begin_marker, end_marker = managed_section_markers(section_id)
    body = str(content or "").rstrip() + "\n"
    return f"{begin_marker}\n{body}{end_marker}\n"


def merge_managed_section(
    existing: str,
    *,
    section_id: str,
    content: str,
    remove_section_ids: Iterable[str] = (),
) -> str:
    existing_text = remove_managed_sections(existing, section_ids=remove_section_ids)
    return merge_managed_section_after_removals(
        existing_text, section_id=section_id, content=content
    )


def remove_managed_sections(existing: str, *, section_ids: Iterable[str]) -> str:
    existing_text = str(existing or "")
    for section_id in section_ids:
        begin_marker, end_marker = managed_section_markers(section_id)
        pattern = re.compile(
            rf"(?ms)^{re.escape(begin_marker)}\n.*?^{re.escape(end_marker)}\n{{0,2}}"
        )
        existing_text = pattern.sub("", existing_text)
    return existing_text


def merge_managed_section_after_removals(existing: str, *, section_id: str, content: str) -> str:
    begin_marker, end_marker = managed_section_markers(section_id)
    section = render_managed_section(section_id, content)
    pattern = re.compile(rf"(?ms)^{re.escape(begin_marker)}\n.*?^{re.escape(end_marker)}\n?")
    existing_text = str(existing or "")
    if pattern.search(existing_text):
        return pattern.sub(section, existing_text, count=1)
    if not existing_text:
        return section
    prefix = existing_text if existing_text.endswith("\n") else f"{existing_text}\n"
    if not prefix.endswith("\n\n"):
        prefix += "\n"
    return f"{prefix}{section}"
