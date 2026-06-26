from __future__ import annotations

import pytest

from agent_sandbox.managed_sections import merge_managed_section, remove_managed_sections


def test_merge_managed_section_creates_missing_file_content() -> None:
    merged = merge_managed_section("", section_id="team", content="hello\n")

    assert merged == (
        "<!-- agent-sandbox-managed-section:team:begin -->\n"
        "hello\n"
        "<!-- agent-sandbox-managed-section:team:end -->\n"
    )


def test_merge_managed_section_replaces_only_matching_section() -> None:
    existing = (
        "intro\n\n"
        "<!-- agent-sandbox-managed-section:team:begin -->\n"
        "old\n"
        "<!-- agent-sandbox-managed-section:team:end -->\n\n"
        "tail\n"
    )

    merged = merge_managed_section(existing, section_id="team", content="new")

    assert merged == (
        "intro\n\n"
        "<!-- agent-sandbox-managed-section:team:begin -->\n"
        "new\n"
        "<!-- agent-sandbox-managed-section:team:end -->\n"
        "\n"
        "tail\n"
    )


def test_merge_managed_section_appends_after_existing_content() -> None:
    merged = merge_managed_section("intro\n", section_id="team", content="new")

    assert merged == (
        "intro\n\n"
        "<!-- agent-sandbox-managed-section:team:begin -->\n"
        "new\n"
        "<!-- agent-sandbox-managed-section:team:end -->\n"
    )


def test_merge_managed_section_removes_stale_sections_first() -> None:
    existing = (
        "intro\n\n"
        "<!-- agent-sandbox-managed-section:truman-team-addon:begin -->\n"
        "stale\n"
        "<!-- agent-sandbox-managed-section:truman-team-addon:end -->\n\n"
        "<!-- agent-sandbox-managed-section:evander-team-addon:begin -->\n"
        "old\n"
        "<!-- agent-sandbox-managed-section:evander-team-addon:end -->\n"
    )

    merged = merge_managed_section(
        existing,
        section_id="evander-team-addon",
        content="new",
        remove_section_ids=["truman-team-addon"],
    )

    assert "truman-team-addon" not in merged
    assert merged == (
        "intro\n\n"
        "<!-- agent-sandbox-managed-section:evander-team-addon:begin -->\n"
        "new\n"
        "<!-- agent-sandbox-managed-section:evander-team-addon:end -->\n"
    )


def test_remove_managed_sections_rejects_unsafe_section_id() -> None:
    with pytest.raises(ValueError, match="managed section_id"):
        remove_managed_sections("", section_ids=["../team"])


def test_merge_managed_section_rejects_unsafe_section_id() -> None:
    with pytest.raises(ValueError, match="managed section_id"):
        merge_managed_section("", section_id="../team", content="new")
