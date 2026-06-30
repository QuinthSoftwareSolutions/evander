from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.admin.services.session_helpers import deep_merge_mapping as _deep_merge_mapping
from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.session_update_metadata import (
    SessionUpdateMetadataEffectProvider,
)


class _AdminServiceStub:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def session_update_metadata(self, **kwargs):
        self.calls.append(kwargs)
        if self.result is None:
            raise RuntimeError("result not configured")
        return dict(self.result)


def _build_context(tmp_path: Path, admin_service: _AdminServiceStub) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=admin_service,
        env={},
    )


def test_session_update_metadata_provider_calls_admin_service(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": True,
            "session_id": "session-1",
            "vm_name": "vm-1",
            "directory_path": "root/team-a",
            "metadata": {"issue_number": "14084"},
        }
    )
    provider = SessionUpdateMetadataEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-session-meta",
            type="effect:session:update-metadata",
            session_id=" session-1 ",
            metadata={"issue_number": "14084"},
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    output = provider.execute()

    assert admin_service.calls == [{"target": "session-1", "metadata": {"issue_number": "14084"}}]
    assert output["success"] is True
    assert output["metadata"]["issue_number"] == "14084"


def test_session_update_metadata_provider_requires_admin_service_method(tmp_path: Path) -> None:
    provider = SessionUpdateMetadataEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-session-meta", type="effect:session:update-metadata"
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            admin_service=SimpleNamespace(),
            env={},
        ),
    )

    with pytest.raises(
        RuntimeError, match="Admin runtime session_update_metadata is not available"
    ):
        provider.execute(
            resolved_config={"session_id": "session-1", "metadata": {"issue_number": "14084"}}
        )


def test_deep_merge_mapping_merges_nested_session_metadata() -> None:
    merged = _deep_merge_mapping(
        {"example_app": {"environments": {"alpha": {"base_url": "https://alpha.example"}}}},
        {"example_app": {"environments": {"beta": {"base_url": "https://beta.example"}}}},
    )

    assert merged == {
        "example_app": {
            "environments": {
                "alpha": {"base_url": "https://alpha.example"},
                "beta": {"base_url": "https://beta.example"},
            }
        }
    }


def test_deep_merge_mapping_replaces_nested_metadata_with_null() -> None:
    merged = _deep_merge_mapping(
        {"agent_thread_ids": {"teams:chat-1": "thread-1"}, "keep": "value"},
        {"agent_thread_ids": None},
    )

    assert merged == {"agent_thread_ids": None, "keep": "value"}
