from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.communication_send_message import (
    CommunicationSendMessageEffectProvider,
)
from agent_sandbox.events.provider_models import CommunicationSendMessageEffectDefinition
from agent_sandbox.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from agent_sandbox.runtime import build_runtime


def _write_targets(project_dir: Path, targets: list[dict[str, object]]) -> None:
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(project_dir / "runtime")
    )
    repository.write_overlay("root", {"targets": targets})


def _runtime(project_dir: Path, **kwargs: object):
    return build_runtime(GatewayConfig(runtime_dir=project_dir / "runtime", **kwargs))


def _execute(
    *,
    project_dir: Path,
    runtime: object,
    target_id: str,
    message: str = "Workflow completed.",
    thread_id: str | None = None,
    subject: str | None = None,
) -> dict[str, object]:
    effect = CommunicationSendMessageEffectDefinition.model_validate(
        {
            "id": "send-message-1",
            "display_name": "Send message",
            "type": "effect:communication:send-message",
            "communication_target_id": target_id,
            "message": message,
            "thread_id": thread_id,
            "subject": subject,
        }
    )
    provider = CommunicationSendMessageEffectProvider()
    provider.initialize(
        effect_definition=effect,
        runtime_context=EffectRuntimeContext(
            admin_service=SimpleNamespace(project_dir=project_dir, runtime=runtime),
            metadata={"directory_path": "root"},
        ),
    )
    return provider.execute()


def test_communication_send_message_posts_to_teams_chat(tmp_path: Path) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "teams-chat",
                "provider": "teams",
                "kind": "teams",
                "address": "chat:chat-1",
                "policy": {"capabilities": ["write"]},
            }
        ],
    )

    output = _execute(
        project_dir=tmp_path,
        runtime=_runtime(tmp_path),
        target_id="teams-chat",
        message="Done.",
    )

    assert output["success"] is True
    assert output["provider"] == "teams"
    assert output["communication_target_id"] == "teams-chat"
    assert output["channel_target"] == "chat:chat-1"
    assert output["chat_id"] == "chat-1"


def test_communication_send_message_posts_to_teams_channel_thread(
    tmp_path: Path,
) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "teams-channel",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["reply"]},
            }
        ],
    )

    output = _execute(
        project_dir=tmp_path,
        runtime=_runtime(tmp_path),
        target_id="teams-channel",
        message="Reply posted.",
        thread_id="thread-1",
    )

    assert output["success"] is True
    assert output["provider"] == "teams"
    assert output["channel_target"] == "team-a/channel-1"
    assert output["thread_id"] == "thread-1"


def test_communication_send_message_posts_to_discord_channel(tmp_path: Path) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678/234567890123456789",
                "policy": {"capabilities": ["write"]},
            }
        ],
    )

    output = _execute(project_dir=tmp_path, runtime=_runtime(tmp_path), target_id="discord-ops")

    assert output["success"] is True
    assert output["provider"] == "discord"
    assert output["channel_id"] == "234567890123456789"


def test_communication_send_message_requires_write_capability(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="does not allow write capability"):
        _execute(project_dir=tmp_path, runtime=_runtime(tmp_path), target_id="teams-primary")


def test_communication_send_message_sends_email_when_enabled(tmp_path: Path) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "email-ops",
                "provider": "email",
                "kind": "email",
                "address": "operator@example.com",
                "policy": {"capabilities": ["write"], "auth_profile_ref": "email.default"},
            }
        ],
    )

    runtime = _runtime(tmp_path, email_send_enabled=True)
    output = _execute(
        project_dir=tmp_path,
        runtime=runtime,
        target_id="email-ops",
        subject="Workflow update",
    )

    assert output["success"] is True
    assert output["provider"] == "email"
    assert output["status"] == "local_sent"
    assert output["mailbox_id"] == "default"
    assert output["recipients"] == ["operator@example.com"]
    assert output["subject"] == "Workflow update"


def test_communication_send_message_blocks_email_when_send_disabled(tmp_path: Path) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "email-ops",
                "provider": "email",
                "kind": "email",
                "address": "operator@example.com",
                "policy": {"capabilities": ["write"], "auth_profile_ref": "email.default"},
            }
        ],
    )

    with pytest.raises(ValueError, match="email send is disabled"):
        _execute(project_dir=tmp_path, runtime=_runtime(tmp_path), target_id="email-ops")
