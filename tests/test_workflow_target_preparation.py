from __future__ import annotations

from types import SimpleNamespace

import pytest

from evander.admin.services.workflow_target_preparation_service import (
    WorkflowTargetPreparationCoordinator,
)


def _direct_chat_config() -> dict[str, object]:
    return {
        "triggers": [
            {
                "id": "direct-chat",
                "type": "trigger:teams:direct-message",
                "config": {
                    "chat_target_mode": "direct",
                    "direct_chat_user_upns": [" person@example.com "],
                    "targets": [],
                    "sender_user_ids": [],
                },
            }
        ]
    }


def test_save_preflights_drafts_before_resolving_scoped_targets() -> None:
    calls: list[object] = []
    config = _direct_chat_config()

    def preflight(candidate):
        calls.append("validated")
        assert candidate["triggers"][0]["config"]["targets"] == [
            {"target": "chat:pending-direct-chat"}
        ]

    def resolve(**kwargs):
        calls.append(kwargs)
        return [
            {
                "user_principal_name": "person@example.com",
                "user_id": "user-1",
                "chat_target": "chat:chat-1",
            }
        ]

    coordinator = WorkflowTargetPreparationCoordinator(
        teams_admin=lambda: SimpleNamespace(resolve_direct_chat_targets=resolve),
        normalize_graphs=lambda **_: [
            SimpleNamespace(directory_path="root/ops", node_ids=["direct-chat"])
        ],
    )

    prepared = coordinator.prepare_for_save(config=config, graphs=[], preflight=preflight)

    assert calls == ["validated", {"directory": "root/ops", "user_refs": ["person@example.com"]}]
    assert prepared["triggers"][0]["config"] == {
        "chat_target_mode": "direct",
        "direct_chat_user_upns": ["person@example.com"],
        "targets": [{"target": "chat:chat-1"}],
        "sender_user_ids": ["user-1"],
    }
    assert config == _direct_chat_config()


def test_invalid_workflow_never_resolves_external_targets() -> None:
    def unexpected_resolution(**_kwargs):
        pytest.fail("invalid workflows must not resolve or create chats")

    coordinator = WorkflowTargetPreparationCoordinator(
        teams_admin=lambda: SimpleNamespace(resolve_direct_chat_targets=unexpected_resolution),
        normalize_graphs=unexpected_resolution,
    )

    def reject_workflow(_candidate):
        raise ValueError("invalid graph")

    with pytest.raises(ValueError, match="invalid graph"):
        coordinator.prepare_for_save(
            config=_direct_chat_config(), graphs=[], preflight=reject_workflow
        )
