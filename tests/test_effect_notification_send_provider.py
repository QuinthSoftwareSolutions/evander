from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig, MailboxConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.notification_send import NotificationSendEffectProvider
from agent_sandbox.events.provider_models import NotificationSendEffectDefinition
from agent_sandbox.runtime import build_runtime


def _write_targets(project_dir: Path, targets: list[dict[str, object]]) -> None:
    config_dir = project_dir / "config" / "communication-targets"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps({"targets": targets}),
        encoding="utf-8",
    )


def _runtime(project_dir: Path, **kwargs: object):
    return build_runtime(GatewayConfig(runtime_dir=project_dir / "runtime", **kwargs))


def _execute(
    *,
    project_dir: Path,
    runtime: object,
    target_id: str,
    message: str = "Agent completed work.",
    subject: str | None = None,
    thread_id: str | None = None,
    severity: str = "info",
) -> dict[str, object]:
    effect = NotificationSendEffectDefinition.model_validate(
        {
            "id": "notify-1",
            "display_name": "Notify",
            "type": "effect:notification:send",
            "communication_target_id": target_id,
            "message": message,
            "severity": severity,
            "subject": subject,
            "thread_id": thread_id,
        }
    )
    provider = NotificationSendEffectProvider()
    provider.initialize(
        effect_definition=effect,
        runtime_context=EffectRuntimeContext(
            admin_service=SimpleNamespace(project_dir=project_dir, runtime=runtime),
            metadata={"directory_path": "root"},
        ),
    )
    return provider.execute()


def test_notification_send_posts_to_teams_target(tmp_path: Path) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "teams-primary",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["notify"]},
            }
        ],
    )

    output = _execute(
        project_dir=tmp_path,
        runtime=_runtime(tmp_path),
        target_id="teams-primary",
        message="Done.",
        thread_id="thread-1",
        severity="success",
    )

    assert output["success"] is True
    assert output["provider"] == "teams"
    assert output["communication_target_id"] == "teams-primary"
    assert output["severity"] == "success"
    assert output["channel_target"] == "team-a/channel-1"
    assert output["thread_id"] == "thread-1"


def test_notification_send_posts_to_teams_user_target(tmp_path: Path) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "teams-operator",
                "provider": "teams",
                "kind": "teams",
                "address": "user:operator@example.com",
                "policy": {"capabilities": ["notify"]},
            }
        ],
    )

    output = _execute(
        project_dir=tmp_path,
        runtime=_runtime(tmp_path),
        target_id="teams-operator",
        message="Done.",
        severity="success",
    )

    assert output["success"] is True
    assert output["provider"] == "teams"
    assert output["communication_target_id"] == "teams-operator"
    assert output["channel_target"] == "chat:stub-user-operator_at_example.com"


def test_notification_send_posts_to_discord_channel(tmp_path: Path) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678/234567890123456789",
                "policy": {"capabilities": ["notify"]},
            }
        ],
    )

    output = _execute(project_dir=tmp_path, runtime=_runtime(tmp_path), target_id="discord-ops")

    assert output["success"] is True
    assert output["provider"] == "discord"
    assert output["channel_id"] == "234567890123456789"


def test_notification_send_creates_email_draft(tmp_path: Path) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "email-ops",
                "provider": "email",
                "kind": "email",
                "address": "operator@example.com",
                "policy": {
                    "capabilities": ["notify"],
                    "auth_profile_ref": "email.ops",
                },
            }
        ],
    )
    runtime = _runtime(
        tmp_path,
        email_mailboxes=[
            MailboxConfig(mailbox_id="ops", provider="json_store", account_identifier="ops")
        ],
    )

    output = _execute(
        project_dir=tmp_path,
        runtime=runtime,
        target_id="email-ops",
        message="Review is ready.",
        subject="Review ready",
    )

    assert output["success"] is True
    assert output["provider"] == "email"
    assert output["status"] == "draft_created"
    assert output["mailbox_id"] == "ops"
    assert output["recipients"] == ["operator@example.com"]
    assert output["subject"] == "Review ready"
    assert str(output["draft_id"]).startswith("draft_")


def test_notification_send_requires_notify_capability(tmp_path: Path) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "teams-primary",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["read", "reply"]},
            }
        ],
    )

    with pytest.raises(ValueError, match="does not allow notify capability"):
        _execute(project_dir=tmp_path, runtime=_runtime(tmp_path), target_id="teams-primary")


def test_notification_send_rejects_unsupported_target_provider(tmp_path: Path) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "ticket-ops",
                "provider": "ado",
                "kind": "ticket",
                "address": "12345",
                "policy": {"capabilities": ["notify"]},
            }
        ],
    )

    with pytest.raises(ValueError, match="does not support communication target provider 'ado'"):
        _execute(project_dir=tmp_path, runtime=_runtime(tmp_path), target_id="ticket-ops")
