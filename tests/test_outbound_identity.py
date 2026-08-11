from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.communication_artifact_links import (
    CommunicationArtifactLinkError,
    issue_communication_persona_avatar_link,
    verify_communication_persona_avatar_token,
)
from evander.messaging.targets import CommunicationTargetDefinition
from evander.outbound_identity import OutboundIdentity, OutboundIdentityRegistry
from evander_team_addon import outbound_identity as team_outbound_identity


def test_outbound_identity_registry_rejects_ambiguous_resolvers() -> None:
    registry = OutboundIdentityRegistry()
    registry.register(
        "one",
        lambda **_: OutboundIdentity(display_name="One", source="one"),
    )
    registry.register(
        "two",
        lambda **_: OutboundIdentity(display_name="Two", source="two"),
    )

    resolution = registry.resolve(
        session_id="session-1",
        provider="discord",
        destination="123",
    )

    assert resolution.status == "conflict"
    assert resolution.identity is None
    assert resolution.matched_sources == ("one", "two")


def test_discord_sender_identity_is_explicitly_opted_in_per_target() -> None:
    default_target = CommunicationTargetDefinition.model_validate(
        {
            "id": "default",
            "provider": "discord",
            "kind": "discord",
            "address": "123456789012345678",
            "policy": {"capabilities": ["write"]},
        }
    )
    persona_target = CommunicationTargetDefinition.model_validate(
        {
            "id": "persona",
            "provider": "discord",
            "kind": "discord",
            "address": "123456789012345679",
            "policy": {
                "capabilities": ["write"],
                "sender_identity": "resolved",
            },
        }
    )

    assert default_target.policy.sender_identity == "platform"
    assert persona_target.policy.sender_identity == "resolved"
    assert persona_target.model_dump(mode="json")["policy"]["sender_identity"] == "resolved"


def test_persona_avatar_links_are_signed_and_expire(monkeypatch) -> None:
    monkeypatch.setenv("PERSONA_SECRET", "persona-secret")
    now = datetime(2026, 8, 10, tzinfo=UTC)
    digest = hashlib.sha256(b"avatar").hexdigest()
    link = issue_communication_persona_avatar_link(
        sha256=digest,
        public_base_url="https://admin.example.test",
        secret_env="PERSONA_SECRET",
        ttl_seconds=60,
        now=now,
    )
    token = link.url.split("token=", 1)[1]

    assert verify_communication_persona_avatar_token(
        sha256=digest,
        token=token,
        secret_env="PERSONA_SECRET",
        now=now + timedelta(seconds=30),
    ) == digest
    with pytest.raises(CommunicationArtifactLinkError, match="expired"):
        verify_communication_persona_avatar_token(
            sha256=digest,
            token=token,
            secret_env="PERSONA_SECRET",
            now=now + timedelta(seconds=61),
        )


def test_team_addon_resolver_uses_persistent_agent_name_and_avatar(
    tmp_path: Path,
    monkeypatch,
) -> None:
    avatar_content = b"team-agent-avatar"
    avatar_path = tmp_path / "avatar.webp"
    avatar_path.write_bytes(avatar_content)
    agent = SimpleNamespace(
        id="builder",
        display_name="Builder Agent",
        session_id="session-builder",
        avatar=SimpleNamespace(
            asset_id="avatar-1",
            mime_type="image/webp",
            sha256=hashlib.sha256(avatar_content).hexdigest(),
        ),
    )

    class FakeRepository:
        def __init__(self, runtime_dir: Path) -> None:
            assert runtime_dir == tmp_path

        def read(self):
            return SimpleNamespace(agents={agent.id: agent})

    class FakeAvatarStorage:
        def __init__(self, runtime_dir: Path) -> None:
            assert runtime_dir == tmp_path

        def asset_path(self, asset_id: str) -> Path:
            assert asset_id == "avatar-1"
            return avatar_path

    monkeypatch.setattr(team_outbound_identity, "TeamStateRepository", FakeRepository)
    monkeypatch.setattr(team_outbound_identity, "AvatarStorage", FakeAvatarStorage)
    resolver = team_outbound_identity.build_outbound_identity_resolver(
        SimpleNamespace(runtime_dir=tmp_path)
    )

    identity = resolver(
        session_id="session-builder",
        provider="discord",
        destination="123",
    )

    assert identity is not None
    assert identity.display_name == "Builder Agent"
    assert identity.source == "team-addon"
    assert identity.avatar is not None
    assert identity.avatar.content == avatar_content
    assert resolver(session_id="other", provider="discord", destination="123") is None
