from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.communication_send_message import (
    CommunicationSendMessageEffectProvider,
)
from evander.events.provider_models import CommunicationSendMessageEffectDefinition
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.outbound_identity import OutboundIdentity
from evander.runtime import build_runtime


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
    sender_session_id: str | None = None,
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
            "sender_session_id": sender_session_id,
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


def test_communication_send_message_uses_supported_workflow_sender_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def send_message(_self, **kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {"success": True, "message_id": "workflow-message-1"}

    monkeypatch.setattr(
        "evander.effects.providers.communication_send_message.CommunicationMessageSender.send_message",
        send_message,
    )

    output = _execute(
        project_dir=tmp_path,
        runtime=_runtime(tmp_path),
        target_id="unused-because-the-sender-is-mocked",
    )

    assert output["success"] is True
    assert calls == [
        {
            "communication_target_id": "unused-because-the-sender-is-mocked",
            "message": "Workflow completed.",
            "thread_id": None,
            "subject": None,
            "sender_session_id": "workflow",
        }
    ]


def test_workflow_discord_send_allows_only_its_declared_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    service_builds: list[dict[str, object]] = []

    class FakeDiscordService:
        def post_status(
            self,
            message: str,
            channel_id: str,
            session_id: str,
        ) -> dict[str, object]:
            return {
                "status": "posted",
                "message_id": "discord-workflow-1",
                "channel_id": channel_id,
                "session_id": session_id,
            }

    def build_discord_service(*_args: object, **kwargs: object) -> FakeDiscordService:
        service_builds.append(dict(kwargs))
        return FakeDiscordService()

    monkeypatch.setattr("evander.messaging.notifications._build_discord_service", build_discord_service)

    output = _execute(project_dir=tmp_path, runtime=_runtime(tmp_path), target_id="discord-ops")

    assert output["message_id"] == "discord-workflow-1"
    assert len(service_builds) == 1
    allowed_channel_ids_provider = service_builds[0]["allowed_channel_ids_provider"]
    assert callable(allowed_channel_ids_provider)
    assert allowed_channel_ids_provider() == ["234567890123456789"]


def test_communication_send_message_uses_resolved_discord_sender_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "discord-trudy",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678/234567890123456789",
                "policy": {
                    "capabilities": ["write"],
                    "sender_identity": "resolved",
                },
            }
        ],
    )
    calls: list[dict[str, object]] = []
    service_builds: list[dict[str, object]] = []

    class FakeDiscordService:
        def post_status_with_identity(
            self,
            message: str,
            channel_id: str,
            session_id: str,
            *,
            identity: OutboundIdentity | None,
            identity_status: str,
        ) -> dict[str, object]:
            calls.append(
                {
                    "message": message,
                    "channel_id": channel_id,
                    "session_id": session_id,
                    "identity": identity,
                    "identity_status": identity_status,
                }
            )
            return {
                "status": "posted",
                "message_id": "discord-persona-1",
                "channel_id": channel_id,
                "transport": "webhook",
            }

        def post_status(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("resolved targets must use the identity-aware Discord sender")

    def build_discord_service(*_args: object, **kwargs: object) -> FakeDiscordService:
        service_builds.append(dict(kwargs))
        return FakeDiscordService()

    monkeypatch.setattr("evander.messaging.notifications._build_discord_service", build_discord_service)
    runtime = _runtime(tmp_path)
    runtime.register_outbound_identity_resolver(
        "test",
        lambda **kwargs: OutboundIdentity(
            display_name="Trudy",
            source=f"test:{kwargs['session_id']}",
        ),
    )

    output = _execute(
        project_dir=tmp_path,
        runtime=runtime,
        target_id="discord-trudy",
        sender_session_id="session-trudy",
    )

    assert output["transport"] == "webhook"
    assert calls == [
        {
            "message": "Workflow completed.",
            "channel_id": "234567890123456789",
            "session_id": "session-trudy",
            "identity": OutboundIdentity(
                display_name="Trudy",
                source="test:session-trudy",
            ),
            "identity_status": "resolved",
        }
    ]
    assert service_builds == [{"session_id": "session-trudy", "capability": "write"}]


def test_communication_send_message_keeps_platform_discord_sender_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_targets(
        tmp_path,
        [
            {
                "id": "discord-platform",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678/234567890123456789",
                "policy": {
                    "capabilities": ["write"],
                    "sender_identity": "platform",
                },
            }
        ],
    )
    calls: list[tuple[str, str, str]] = []

    class FakeDiscordService:
        def post_status(
            self,
            message: str,
            channel_id: str,
            session_id: str,
        ) -> dict[str, object]:
            calls.append((message, channel_id, session_id))
            return {
                "status": "posted",
                "message_id": "discord-platform-1",
                "channel_id": channel_id,
                "transport": "bot_api",
            }

        def post_status_with_identity(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("platform targets must use the ordinary Discord sender")

    monkeypatch.setattr(
        "evander.messaging.notifications._build_discord_service",
        lambda *_args, **_kwargs: FakeDiscordService(),
    )
    runtime = _runtime(tmp_path)
    runtime.register_outbound_identity_resolver(
        "test",
        lambda **_: OutboundIdentity(display_name="Trudy", source="test"),
    )

    output = _execute(
        project_dir=tmp_path,
        runtime=runtime,
        target_id="discord-platform",
        sender_session_id="session-trudy",
    )

    assert output["transport"] == "bot_api"
    assert calls == [
        (
            "Workflow completed.",
            "234567890123456789",
            "session-trudy",
        )
    ]


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
