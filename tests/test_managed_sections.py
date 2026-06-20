from __future__ import annotations

import pytest

from agent_sandbox.managed_sections import merge_managed_section


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


def test_merge_managed_section_rejects_unsafe_section_id() -> None:
    with pytest.raises(ValueError, match="managed section_id"):
        merge_managed_section("", section_id="../team", content="new")
