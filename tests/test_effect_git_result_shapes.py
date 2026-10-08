from __future__ import annotations

from types import SimpleNamespace

import pytest

from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.git_promote_agent_branch import GitPromoteAgentBranchEffectProvider
from evander.effects.providers.git_push_branch import GitPushBranchEffectProvider


class _MappingResult(dict):
    def model_dump(self):
        raise AssertionError("Mappings must take precedence over model_dump")


class _ModelResult:
    success = False

    def __init__(self) -> None:
        self.origin = "attributes"

    def model_dump(self):
        return {"success": True, "origin": "model"}


class _AttributeResult:
    def __init__(self) -> None:
        self.success = True
        self.origin = "attributes"

    def model_dump(self):
        return []


@pytest.mark.parametrize(
    "provider_type,method_name",
    [
        (GitPushBranchEffectProvider, "push_branch"),
        (GitPromoteAgentBranchEffectProvider, "promote_agent_branch"),
    ],
)
@pytest.mark.parametrize(
    "raw_result,expected",
    [
        (_MappingResult(success=True, origin="mapping"), {"success": True, "origin": "mapping"}),
        (_ModelResult(), {"success": True, "origin": "model"}),
        (_AttributeResult(), {"success": True, "origin": "attributes"}),
        (None, {"success": False}),
        (7, {"success": False, "result": 7}),
    ],
)
def test_git_effects_preserve_runtime_result_shapes(
    provider_type, method_name, raw_result, expected
):
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return raw_result

    provider = provider_type()
    provider.initialize(
        SimpleNamespace(
            managed_repo_id="managed-repo",
            source_ref="refs/heads/source",
            destination_branch="destination",
            agent_branch="agents/session/topic",
            target_branch="review/session/topic",
            expected_commit_sha="a" * 40,
        ),
        EffectRuntimeContext(admin_service=SimpleNamespace(**{method_name: run}), env={}),
    )

    result = provider.execute()

    assert len(calls) == 1
    assert {key: result[key] for key in expected} == expected
    assert result["managed_repo_id"] == "managed-repo"
    assert result["expected_commit_sha"] == "a" * 40
    if isinstance(raw_result, dict):
        assert raw_result == expected
    elif hasattr(raw_result, "__dict__"):
        assert "managed_repo_id" not in vars(raw_result)
