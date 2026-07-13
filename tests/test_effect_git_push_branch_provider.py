from __future__ import annotations

from types import SimpleNamespace

import pytest

from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.git_push_branch import GitPushBranchEffectProvider
from evander.events.provider_models import GitPushBranchEffectDefinition


class _AdminServiceStub:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def push_branch(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if self.result is None:
            raise RuntimeError("result not configured")
        return dict(self.result)


def _definition(**overrides: object) -> GitPushBranchEffectDefinition:
    payload: dict[str, object] = {
        "id": "effect-push",
        "display_name": "Push branch",
        "type": "effect:git:push-branch",
        "managed_repo_id": "mr_0123456789abcdefabcd",
        "source_ref": "refs/evander/operations/op-1/source",
        "expected_commit_sha": "a" * 40,
        "destination_branch": "feature/topic",
    }
    payload.update(overrides)
    return GitPushBranchEffectDefinition.model_validate(payload)


def test_git_push_branch_provider_calls_admin_service_with_pinned_contract() -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": True,
            "published_commit_sha": "a" * 40,
        }
    )
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        _definition(
            managed_repo_id=" mr_0123456789abcdefabcd ",
            source_ref=" refs/evander/operations/op-1/source ",
            expected_commit_sha=f" {'a' * 40} ",
            destination_branch=" feature/topic ",
            directory_path=" root/team-a ",
        ),
        EffectRuntimeContext(
            admin_service=admin_service,
            env={},
            metadata={"directory_path": "root"},
        ),
    )

    result = provider.execute()

    assert admin_service.calls == [
        {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "source_ref": "refs/evander/operations/op-1/source",
            "expected_commit_sha": "a" * 40,
            "destination_branch": "feature/topic",
            "directory_path": "root/team-a",
        }
    ]
    assert result["success"] is True
    assert result["destination_branch"] == "feature/topic"


def test_git_push_branch_provider_uses_failure_message_as_error() -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": False,
            "reason": "destination_moved",
            "message": "Canonical destination changed before publication.",
        }
    )
    provider = GitPushBranchEffectProvider()
    provider.initialize(_definition(), EffectRuntimeContext(admin_service=admin_service, env={}))

    result = provider.execute()

    assert result["success"] is False
    assert result["reason"] == "destination_moved"
    assert result["error"] == "Canonical destination changed before publication."


@pytest.mark.parametrize(
    ("field_name", "message"),
    [
        ("managed_repo_id", "requires managed_repo_id"),
        ("source_ref", "requires source_ref"),
        ("expected_commit_sha", "requires expected_commit_sha"),
        ("destination_branch", "requires destination_branch"),
    ],
)
def test_git_push_branch_provider_requires_each_pinned_field(field_name: str, message: str) -> None:
    definition = _definition()
    setattr(definition, field_name, None)
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        definition,
        EffectRuntimeContext(admin_service=_AdminServiceStub({"success": True}), env={}),
    )

    with pytest.raises(ValueError, match=message):
        provider.execute()


def test_git_push_branch_provider_requires_admin_service_method() -> None:
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        _definition(),
        EffectRuntimeContext(admin_service=SimpleNamespace(), env={}),
    )

    with pytest.raises(RuntimeError, match="Admin runtime push_branch is not available"):
        provider.execute()
