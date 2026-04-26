from __future__ import annotations

import json

import pytest

from agent_sandbox.communication_targets import (
    CommunicationTargetsConfig,
    load_communication_targets_config,
)


def test_communication_targets_reject_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="must be unique: alpha"):
        CommunicationTargetsConfig.model_validate(
            {
                "targets": [
                    {"id": "alpha", "provider": "teams", "kind": "teams", "address": "team/channel"},
                    {"id": "alpha", "provider": "discord", "kind": "discord", "address": "123456789012345678"},
                ]
            }
        )


def test_communication_targets_reject_incompatible_provider_kind() -> None:
    with pytest.raises(ValueError, match="not compatible"):
        CommunicationTargetsConfig.model_validate(
            {
                "targets": [
                    {"id": "bad", "provider": "teams", "kind": "email", "address": "team/channel"},
                ]
            }
        )


def test_communication_targets_apply_provider_address_validation() -> None:
    with pytest.raises(ValueError, match="expected 'team-id/channel-id' or 'chat:<chat-id>'"):
        CommunicationTargetsConfig.model_validate(
            {
                "targets": [
                    {"id": "bad-teams", "provider": "teams", "kind": "teams", "address": "nope"},
                ]
            }
        )

    with pytest.raises(ValueError, match="discord address must be"):
        CommunicationTargetsConfig.model_validate(
            {
                "targets": [
                    {"id": "bad-discord", "provider": "discord", "kind": "discord", "address": "channel-name"},
                ]
            }
        )


def test_communication_targets_reject_secret_like_address(tmp_path) -> None:
    path = tmp_path / "config" / "communication-targets"
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "id": "mail",
                        "provider": "email",
                        "kind": "email",
                        "address": "token=super-secret",
                        "policy": {"auth_profile_ref": "email.default"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="appears to contain a secret"):
        load_communication_targets_config(tmp_path)
