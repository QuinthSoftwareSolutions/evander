from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.session_set_image_generation_enabled import (
    SessionSetImageGenerationEnabledEffectProvider,
)


class _AdminServiceStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def session_set_image_generation_enabled(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "success": True,
            "session_id": kwargs["target"],
            "image_generation_enabled": kwargs["enabled"],
        }


def test_session_image_generation_provider_updates_target_session(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub()
    provider = SessionSetImageGenerationEnabledEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-image-policy",
            type="effect:session:set-image-generation-enabled",
            session_id=" session-1 ",
            enabled=True,
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            admin_service=admin_service,
            env={},
        ),
    )

    output = provider.execute()

    assert admin_service.calls == [{"target": "session-1", "enabled": True}]
    assert output == {
        "success": True,
        "session_id": "session-1",
        "image_generation_enabled": True,
    }


def test_session_image_generation_provider_requires_boolean_enabled(tmp_path: Path) -> None:
    provider = SessionSetImageGenerationEnabledEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-image-policy",
            type="effect:session:set-image-generation-enabled",
            target="session-1",
            enabled="true",
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            admin_service=_AdminServiceStub(),
            env={},
        ),
    )

    with pytest.raises(ValueError, match="enabled must resolve to a boolean"):
        provider.execute()
