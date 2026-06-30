from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from evander.admin.services import vm_helpers as admin_vm_helpers
from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.vm_exec import VmExecEffectProvider
from evander.models import SessionManifest


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
        self.runtime = SimpleNamespace(session_repository=_SessionStore(self.sessions))

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


def test_vm_exec_provider_uses_vm_name_fallback_session_id(tmp_path) -> None:
    admin_service = _AdminServiceStub(
        sessions={},
        vm_exec_result={
            "success": True,
            "vm_name": "lab-vm-2",
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
            "vm_name": "lab-vm-2",
            "command": "echo hello",
        }
    )

    assert admin_service.calls == [
        {
            "target": "lab-vm-2",
            "vm_user": "agent",
            "command": "echo hello",
            "working_directory": None,
            "environment": {},
            "stdin": None,
            "timeout_seconds": 3600.0,
            "check": True,
        }
    ]
    assert result["session_id"] == "session-lab-vm-2"


def test_vm_exec_provider_uses_target_fallback_session_id(tmp_path) -> None:
    admin_service = _AdminServiceStub(
        sessions={},
        vm_exec_result={
            "success": True,
            "vm_name": "lab-vm-3",
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
            "target": "lab-vm-3",
            "command": "echo hello",
        }
    )

    assert admin_service.calls == [
        {
            "target": "lab-vm-3",
            "vm_user": "agent",
            "command": "echo hello",
            "working_directory": None,
            "environment": {},
            "stdin": None,
            "timeout_seconds": 3600.0,
            "check": True,
        }
    ]
    assert result["session_id"] == "session-lab-vm-3"


def test_vm_exec_provider_uses_matching_session_id_for_vm_name(tmp_path) -> None:
    session = SessionManifest(
        session_id="session-4",
        vm_id="lab-vm-4",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    admin_service = _AdminServiceStub(
        sessions={"session-4": session},
        vm_exec_result={
            "success": True,
            "vm_name": "lab-vm-4",
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
            "vm_name": "lab-vm-4",
            "command": "echo hello",
        }
    )

    assert admin_service.calls == [
        {
            "target": "lab-vm-4",
            "vm_user": "agent",
            "command": "echo hello",
            "working_directory": None,
            "environment": {},
            "stdin": None,
            "timeout_seconds": 3600.0,
            "check": True,
        }
    ]
    assert result["session_id"] == "session-4"


def test_vm_exec_provider_uses_ssh_fallback_session_id(tmp_path, monkeypatch) -> None:
    provider = VmExecEffectProvider()
    runtime_context = EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=SimpleNamespace(),
        env={},
        session_lookup=SimpleNamespace(read_all_sessions=lambda: {}),
    )
    provider.initialize(_effect_definition(), runtime_context)

    monkeypatch.setattr(admin_vm_helpers, "resolve_vm_ip", lambda vm_name: "10.0.0.2")
    monkeypatch.setattr(admin_vm_helpers, "resolve_private_key_path", lambda: Path("/tmp/key"))
    monkeypatch.setattr(admin_vm_helpers, "ssh_options", lambda *_args: [])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0] if args else [],
            returncode=0,
            stdout="ssh ok\n",
            stderr="",
        ),
    )

    result = provider.execute(
        resolved_config={
            "vm_name": "lab-vm-ssh",
            "command": "echo hello",
        }
    )

    assert result["success"] is True
    assert result["vm_name"] == "lab-vm-ssh"
    assert result["session_id"] == "session-lab-vm-ssh"


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


def test_vm_exec_provider_raises_for_ambiguous_vm_name_matches(tmp_path) -> None:
    session_a = SessionManifest(
        session_id="session-a",
        vm_id="shared-vm",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session_b = SessionManifest(
        session_id="session-b",
        vm_id="shared-vm",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    admin_service = _AdminServiceStub(
        sessions={"session-a": session_a, "session-b": session_b},
        vm_exec_result={},
        calls=[],
    )
    provider = VmExecEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(admin_service, tmp_path))

    with pytest.raises(RuntimeError, match=r"VM 'shared-vm' is associated with multiple sessions"):
        provider.execute(
            resolved_config={
                "vm_name": "shared-vm",
                "command": "echo hello",
            }
        )

    assert admin_service.calls == []


def test_vm_exec_provider_requires_session_lookup(tmp_path) -> None:
    @dataclass
    class _NoLookupAdminService:
        calls: list[dict[str, object]]

        def vm_exec(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "success": True,
                "vm_name": kwargs["target"],
                "action": "vm_exec",
                "message": "ok",
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
            }

    admin_service = _NoLookupAdminService(calls=[])
    provider = VmExecEffectProvider()
    provider.initialize(
        _effect_definition(),
        EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            admin_service=admin_service,
            env={},
        ),
    )

    with pytest.raises(RuntimeError, match="effect:vm:exec requires session_lookup"):
        provider.execute(
            resolved_config={
                "vm_name": "lab-vm-5",
                "command": "echo hello",
            }
        )

    assert admin_service.calls == []


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
    assert result["exit_code"] == 7
    assert result["stdout"] == "out"
    assert result["stderr"] == "err"
