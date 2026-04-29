from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.vm_delete_files import VmDeleteFilesEffectProvider


class _SessionStoreStub:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def get(self, session_id: str):
        return self.payload.get(session_id)

    def read_all(self):
        return dict(self.payload)


class _RuntimeStub:
    def __init__(self, sessions: dict[str, object]) -> None:
        self.session_repository = _SessionStoreStub(sessions)


class _AdminServiceStub:
    def __init__(
        self, sessions: dict[str, object], vm_delete_files_result: dict[str, object] | None = None
    ) -> None:
        self.runtime = _RuntimeStub(sessions)
        self.vm_delete_files_result = vm_delete_files_result
        self.calls: list[dict[str, object]] = []

    def vm_delete_files(self, **kwargs):
        self.calls.append(kwargs)
        if self.vm_delete_files_result is None:
            raise RuntimeError("vm_delete_files_result not configured")
        return dict(self.vm_delete_files_result)


def _build_context(tmp_path: Path, admin_service: _AdminServiceStub) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path),
        admin_service=admin_service,
        env={},
        metadata={"directory_path": "root/team-a"},
    )


def test_vm_delete_files_provider_deletes_paths_by_target(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        sessions={},
        vm_delete_files_result={
            "success": True,
            "vm_name": "lab-vm-1",
            "action": "vm_delete_files",
            "message": "Deleted 2 path(s) from lab-vm-1",
            "deleted_count": 2,
            "deleted_paths": ["/home/agent/a.txt", "/home/agent/b.txt"],
        },
    )
    context = _build_context(tmp_path, admin_service)
    provider = VmDeleteFilesEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(id="delete-files"), runtime_context=context
    )

    result = provider.execute(
        resolved_config={
            "target": "lab-vm-1",
            "vm_user": "agent",
            "paths": ["/home/agent/a.txt", "/home/agent/b.txt"],
            "missing_ok": False,
        }
    )

    assert result["success"] is True
    assert result["deleted_paths"] == ["/home/agent/a.txt", "/home/agent/b.txt"]
    assert admin_service.calls == [
        {
            "target": "lab-vm-1",
            "vm_user": "agent",
            "paths": ["/home/agent/a.txt", "/home/agent/b.txt"],
            "recursive": False,
            "missing_ok": False,
        }
    ]


def test_vm_delete_files_provider_resolves_vm_by_session_id(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        sessions={
            "session-abc": SimpleNamespace(vm_id="lab-vm-2"),
        },
        vm_delete_files_result={
            "success": True,
            "vm_name": "lab-vm-2",
            "action": "vm_delete_files",
            "message": "Deleted 1 path(s) from lab-vm-2",
            "deleted_count": 1,
            "deleted_paths": ["/home/agent/hello.txt"],
        },
    )
    context = _build_context(tmp_path, admin_service)
    provider = VmDeleteFilesEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(id="delete-files"), runtime_context=context
    )

    result = provider.execute(
        resolved_config={
            "session_id": "session-abc",
            "paths": ["/home/agent/hello.txt"],
            "recursive": True,
        }
    )

    assert result["vm_name"] == "lab-vm-2"
    assert result["target"] == "session-abc"
    assert admin_service.calls == [
        {
            "target": "session-abc",
            "vm_user": "agent",
            "paths": ["/home/agent/hello.txt"],
            "recursive": True,
            "missing_ok": True,
        }
    ]
