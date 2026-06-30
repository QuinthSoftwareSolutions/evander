from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.vm_start import VmStartEffectProvider
from evander.effects.providers.vm_stop import VmStopEffectProvider
from evander.models import VmActionResult


class _AdminServiceStub:
    def __init__(self) -> None:
        self.start_calls: list[str] = []
        self.stop_calls: list[tuple[str, int]] = []

    def start_vm(self, target: str) -> VmActionResult:
        self.start_calls.append(target)
        return VmActionResult(
            success=True, vm_name="vm-1", action="start", message="Started VM vm-1"
        )

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> VmActionResult:
        self.stop_calls.append((target, timeout_seconds))
        return VmActionResult(
            success=True, vm_name="vm-1", action="shutdown", message="Shut down VM vm-1"
        )


def _context(admin_service: _AdminServiceStub, tmp_path: Path) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=admin_service,
        env={},
    )


def test_vm_start_provider_uses_session_id_target(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub()
    provider = VmStartEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-start", display_name="Start", type="effect:vm:start"
        ),
        runtime_context=_context(admin_service, tmp_path),
    )

    result = provider.execute(resolved_config={"session_id": "session-1"})

    assert admin_service.start_calls == ["session-1"]
    assert result["success"] is True
    assert result["action"] == "start"
    assert result["vm_name"] == "vm-1"


def test_vm_stop_provider_uses_default_timeout(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub()
    provider = VmStopEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-stop", display_name="Stop", type="effect:vm:stop"
        ),
        runtime_context=_context(admin_service, tmp_path),
    )

    result = provider.execute(resolved_config={"vm_name": "vm-1"})

    assert admin_service.stop_calls == [("vm-1", 60)]
    assert result["success"] is True
    assert result["action"] == "shutdown"
    assert result["vm_name"] == "vm-1"


def test_vm_stop_provider_accepts_timeout_override(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub()
    provider = VmStopEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-stop", display_name="Stop", type="effect:vm:stop"
        ),
        runtime_context=_context(admin_service, tmp_path),
    )

    provider.execute(resolved_config={"target": "session-2", "timeout_seconds": "15"})

    assert admin_service.stop_calls == [("session-2", 15)]
