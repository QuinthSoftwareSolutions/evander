from __future__ import annotations

from agent_sandbox.admin.services.session_helpers import (
    build_issue_session_manifest as build_admin_issue_session_manifest,
)
from agent_sandbox.cli import _build_issue_session_manifest as build_cli_issue_session_manifest
from agent_sandbox.models import ToolName
from agent_sandbox.policy_config import EffectivePolicyConfig


def test_admin_issue_session_manifest_only_adds_copilot_auth_for_copilot_provider(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    default_manifest = build_admin_issue_session_manifest(
        policy=EffectivePolicyConfig(),
        session_id="session-default",
        vm_id="vm-1",
        issue_number="14418",
        directory_path="root",
        ttl_hours=1,
        discord_channels=[],
        email_mailboxes=[],
        email_folders=[],
    )
    monkeypatch.setenv("AGENT_PROVIDER", "copilot")
    copilot_manifest = build_admin_issue_session_manifest(
        policy=EffectivePolicyConfig(),
        session_id="session-copilot",
        vm_id="vm-2",
        issue_number="14418",
        directory_path="root",
        ttl_hours=1,
        discord_channels=[],
        email_mailboxes=[],
        email_folders=[],
    )

    assert ToolName.COPILOT_HOST_AUTH not in default_manifest.allowed_tools
    assert ToolName.COPILOT_HOST_AUTH in copilot_manifest.allowed_tools
    assert ToolName.STAGE_ARTIFACT in default_manifest.allowed_tools
    assert ToolName.STAGE_ARTIFACT in copilot_manifest.allowed_tools
    assert copilot_manifest.metadata["agent_provider"] == "copilot"


def test_cli_issue_session_manifest_adds_copilot_auth_for_copilot_provider(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PROVIDER", "copilot")
    manifest = build_cli_issue_session_manifest(
        policy=EffectivePolicyConfig(),
        session_id="session-copilot",
        vm_id="vm-1",
        directory_path="root",
        ttl_hours=1,
        discord_channels=[],
        email_mailboxes=[],
        email_folders=[],
    )

    assert ToolName.COPILOT_HOST_AUTH in manifest.allowed_tools
    assert ToolName.STAGE_ARTIFACT in manifest.allowed_tools
    assert manifest.metadata["agent_provider"] == "copilot"
