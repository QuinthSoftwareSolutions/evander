from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.vm_exec import VmExecEffectProvider
from agent_sandbox.models import SessionManifest


@dataclass
class _SessionStore:
    sessions: dict[str, SessionManifest]

    def get(self, session_id: str) -> SessionManifest | None:
        return self.sessions.get(session_id)

    def read_all(self) -> dict[str, SessionManifest]:
        return dict(self.sessions)


@dataclass
class _AdminServiceStub:
    sessions: dict[str, SessionManifest]
    vm_exec_result: dict[str, object]
    calls: list[dict[str, object]]

    def __post_init__(self) -> None:
        self.runtime = SimpleNamespace(sessions=_SessionStore(self.sessions))

    def vm_exec(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.vm_exec_result)


def _runtime_context(admin_service: _AdminServiceStub, tmp_path: Path) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=admin_service,
        env={},
    )


def _effect_definition() -> SimpleNamespace:
    return SimpleNamespace(id="effect-exec", display_name="Exec", type="effect:vm:exec")


def test_vm_exec_provider_calls_admin_service_with_resolved_session_target(tmp_path) -> None:
    session = SessionManifest(
        session_id="session-1",
        vm_id="lab-vm-1",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    admin_service = _AdminServiceStub(
        sessions={"session-1": session},
        vm_exec_result={
            "success": True,
            "vm_name": "lab-vm-1",
            "action": "vm_exec",
            "message": "ok",
            "exit_code": 0,
            "stdout": "hello\n",
            "stderr": "",
        },
        calls=[],
    )
    provider = VmExecEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(admin_service, tmp_path))

    result = provider.execute(
        resolved_config={
            "session_id": "session-1",
            "vm_user": "builder",
            "command": "echo hello",
            "working_directory": "/work",
            "environment": {"FOO": "bar"},
            "stdin": "payload",
            "timeout_seconds": 12,
            "check": True,
        }
    )

    assert admin_service.calls == [
        {
            "target": "lab-vm-1",
            "vm_user": "builder",
            "command": "echo hello",
            "working_directory": "/work",
            "environment": {"FOO": "bar"},
            "stdin": "payload",
            "timeout_seconds": 12.0,
            "check": True,
        }
    ]
    assert result["success"] is True
    assert result["vm_name"] == "lab-vm-1"
    assert result["session_id"] == "session-1"
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello\n"


def test_vm_exec_provider_raises_when_checked_command_fails(tmp_path) -> None:
    session = SessionManifest(
        session_id="session-2",
        vm_id="lab-vm-2",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    admin_service = _AdminServiceStub(
        sessions={"session-2": session},
        vm_exec_result={
            "success": False,
            "vm_name": "lab-vm-2",
            "action": "vm_exec",
            "message": "command failed",
            "exit_code": 3,
            "stdout": "",
            "stderr": "boom",
        },
        calls=[],
    )
    provider = VmExecEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(admin_service, tmp_path))

    with pytest.raises(RuntimeError, match="boom"):
        provider.execute(
            resolved_config={
                "session_id": "session-2",
                "command": "false",
                "check": True,
            }
        )

    assert admin_service.calls == [
        {
            "target": "lab-vm-2",
            "vm_user": "agent",
            "command": "false",
            "working_directory": None,
            "environment": {},
            "stdin": None,
            "timeout_seconds": 3600.0,
            "check": True,
        }
    ]


def test_vm_exec_provider_returns_failure_result_when_check_is_disabled(tmp_path) -> None:
    session = SessionManifest(
        session_id="session-3",
        vm_id="lab-vm-3",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    admin_service = _AdminServiceStub(
        sessions={"session-3": session},
        vm_exec_result={
            "success": False,
            "vm_name": "lab-vm-3",
            "action": "vm_exec",
            "message": "command failed",
            "exit_code": 7,
            "stdout": "out",
            "stderr": "err",
        },
        calls=[],
    )
    provider = VmExecEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(admin_service, tmp_path))

    result = provider.execute(
        resolved_config={
            "session_id": "session-3",
            "command": "false",
            "check": False,
        }
    )

    assert result["success"] is False
    assert result["succeeded"] is False
    assert result["exit_code"] == 7
    assert result["stdout"] == "out"
    assert result["stderr"] == "err"
