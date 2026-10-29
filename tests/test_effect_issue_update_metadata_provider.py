from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.issue_update_metadata import IssueUpdateMetadataEffectProvider


class _AdminServiceStub:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def update_issue_metadata(self, **kwargs):
        self.calls.append(kwargs)
        if self.result is None:
            raise RuntimeError("result not configured")
        return dict(self.result)


def _build_context(tmp_path: Path, admin_service: object) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=admin_service,
        env={},
    )


def test_issue_update_metadata_provider_calls_admin_service_with_dot_patch(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(
        result={"updated": True, "metadata": {"plan": {"status": "committed"}}}
    )
    provider = IssueUpdateMetadataEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-issue-meta",
            type="effect:issue:update-metadata",
            issue_id=" 14084 ",
            patch={"plan.status": "committed"},
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    output = provider.execute()

    assert admin_service.calls == [
        {
            "issue_id": "14084",
            "patch": {"plan.status": "committed"},
            "sync_proxy_cache": True,
        }
    ]
    assert output["success"] is True
    assert output["issue_number"] == "14084"


def test_issue_update_metadata_provider_accepts_merge_patch_and_concurrency_token(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(result={"updated": True})
    provider = IssueUpdateMetadataEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-issue-meta",
            type="effect:issue:update-metadata",
            issue_id="14084",
            merge_patch={"plan": {"status": "committed"}},
            expected_updated_at="2026-05-08T00:00:00+00:00",
            sync_proxy_cache="false",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    provider.execute()

    assert admin_service.calls == [
        {
            "issue_id": "14084",
            "merge_patch": {"plan": {"status": "committed"}},
            "expected_updated_at": "2026-05-08T00:00:00+00:00",
            "sync_proxy_cache": False,
        }
    ]


def test_issue_update_metadata_provider_validates_patch_shape(tmp_path: Path) -> None:
    provider = IssueUpdateMetadataEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-issue-meta",
            type="effect:issue:update-metadata",
            issue_id="14084",
        ),
        runtime_context=_build_context(tmp_path, _AdminServiceStub(result={"updated": True})),
    )

    with pytest.raises(ValueError, match="requires patch or merge_patch"):
        provider.execute()

    with pytest.raises(ValueError, match="mutually exclusive"):
        provider.execute(resolved_config={"patch": {}, "merge_patch": {}})

    with pytest.raises(ValueError, match="patch must resolve to an object"):
        provider.execute(resolved_config={"patch": "not-an-object"})


def test_issue_update_metadata_provider_requires_admin_service_method(tmp_path: Path) -> None:
    provider = IssueUpdateMetadataEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-issue-meta",
            type="effect:issue:update-metadata",
            issue_id="14084",
            patch={"plan.status": "committed"},
        ),
        runtime_context=_build_context(tmp_path, SimpleNamespace()),
    )

    with pytest.raises(RuntimeError, match=r"Admin runtime update_issue_metadata is not available"):
        provider.execute()
