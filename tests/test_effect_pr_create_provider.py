from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.pr_create import PrCreateEffectProvider
from evander.effects.providers.schemas import PrCreateEffectDefinition


class _AdminServiceStub:
    def __init__(self, pr_create_result: dict[str, object] | None = None) -> None:
        self.pr_create_result = pr_create_result
        self.calls: list[dict[str, object]] = []

    def pr_create(self, **kwargs):
        self.calls.append(kwargs)
        if self.pr_create_result is None:
            raise RuntimeError("pr_create_result not configured")
        return dict(self.pr_create_result)


def _build_context(tmp_path: Path, admin_service: _AdminServiceStub) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=admin_service,
        env={},
    )


def test_pr_create_provider_calls_admin_service_with_normalized_arguments(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        pr_create_result={
            "success": True,
            "action": "pr_create",
            "message": "Created PR",
            "pr_id": "123",
            "pr_url": "https://example.test/pr/123",
        }
    )
    provider = PrCreateEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-pr",
            display_name="Create PR",
            type="effect:pr:create",
            managed_repo_id="  mr_11111111111111111111  ",
            source_ref="refs/evander/review/default",
            expected_commit_sha="1111111111111111111111111111111111111111",
            destination_branch="review/default",
            base_branch="main",
            session_id="session-default",
            work_item_id="WI-1",
            title="Default title",
            body="Default body",
            draft=None,
            fill=True,
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute(
        resolved_config={
            "managed_repo_id": "  mr_22222222222222222222  ",
            "source_ref": "  refs/evander/review/session-1  ",
            "expected_commit_sha": "2222222222222222222222222222222222222222",
            "destination_branch": "  feature/issue-1  ",
            "title": "  Override title  ",
            "body": "  Override body  ",
            "draft": "false",
            "fill": "0",
        }
    )

    assert admin_service.calls == [
        {
            "managed_repo_id": "mr_22222222222222222222",
            "source_ref": "refs/evander/review/session-1",
            "expected_commit_sha": "2222222222222222222222222222222222222222",
            "destination_branch": "feature/issue-1",
            "directory_path": "root",
            "base_branch": "main",
            "session_id": "session-default",
            "work_item_id": "WI-1",
            "title": "Override title",
            "body": "Override body",
            "draft": False,
            "fill": False,
        }
    ]
    assert result["success"] is True
    assert result["action"] == "pr_create"
    assert result["message"] == "Created PR"
    assert result["pr_id"] == "123"
    assert result["pr_url"] == "https://example.test/pr/123"


def test_pr_create_schema_exposes_no_provider_or_file_path_overrides() -> None:
    assert {
        "project",
        "body_file",
        "body_artifact_ids",
        "host_repo_path",
        "trusted_repo_path",
    }.isdisjoint(PrCreateEffectDefinition.model_fields)


def test_pr_create_provider_passes_structured_preflight_failure(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        pr_create_result={
            "success": False,
            "action": "pr_create",
            "preflight_success": False,
            "reason": "destination_branch_ahead",
            "message": "Destination branch is ahead",
            "destination_branch": "feature/issue-1",
            "source_ref": "refs/evander/review/issue-1/work",
        }
    )
    provider = PrCreateEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-pr",
            display_name="Create PR",
            type="effect:pr:create",
            managed_repo_id="mr_11111111111111111111",
            source_ref="refs/evander/review/issue-1/work",
            expected_commit_sha="1111111111111111111111111111111111111111",
            destination_branch="feature/issue-1",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["preflight_success"] is False
    assert result["reason"] == "destination_branch_ahead"
    assert result["destination_branch"] == "feature/issue-1"


def test_pr_create_provider_uses_fallback_title_when_title_is_blank(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(pr_create_result={"success": True})
    provider = PrCreateEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-pr",
            display_name="Create PR",
            type="effect:pr:create",
            managed_repo_id="mr_11111111111111111111",
            source_ref="refs/evander/review/topic",
            expected_commit_sha="1111111111111111111111111111111111111111",
            destination_branch="feature/topic",
            title="",
            fallback_title="Fallback title",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute()

    assert result["success"] is True
    assert admin_service.calls[0]["title"] == "Fallback title"


def test_pr_create_provider_uses_fallback_destination_when_override_is_blank(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(pr_create_result={"success": True})
    provider = PrCreateEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-pr",
            display_name="Create PR",
            type="effect:pr:create",
            managed_repo_id="mr_11111111111111111111",
            source_ref="refs/evander/review/topic",
            expected_commit_sha="1" * 40,
            destination_branch=None,
            fallback_destination_branch="feature/session-1",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute()

    assert result["success"] is True
    assert admin_service.calls[0]["destination_branch"] == "feature/session-1"


def test_pr_create_provider_requires_admin_service_method(tmp_path: Path) -> None:
    admin_service = SimpleNamespace()
    provider = PrCreateEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(id="effect-pr", type="effect:pr:create"),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            admin_service=admin_service,
            env={},
        ),
    )

    with pytest.raises(RuntimeError, match="Admin runtime pr_create is not available"):
        provider.execute(
            resolved_config={
                "managed_repo_id": "mr_11111111111111111111",
                "source_ref": "refs/evander/review/topic",
                "expected_commit_sha": "1111111111111111111111111111111111111111",
                "destination_branch": "feature/topic",
            }
        )
