from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.vm_delete_files import VmDeleteFilesEffectProvider


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


class _NoLookupAdminService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def vm_delete_files(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "success": True,
            "vm_name": kwargs["target"],
            "action": "vm_delete_files",
            "message": "Deleted 1 path(s)",
            "deleted_count": 1,
            "deleted_paths": list(kwargs["paths"]),
        }


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
    assert result["target"] == "lab-vm-1"
    assert result["session_id"] == "session-lab-vm-1"
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
    assert result["session_id"] == "session-abc"
    assert admin_service.calls == [
        {
            "target": "session-abc",
            "vm_user": "agent",
            "paths": ["/home/agent/hello.txt"],
            "recursive": True,
            "missing_ok": True,
        }
    ]


def test_vm_delete_files_provider_uses_fallback_session_id_for_vm_name(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        sessions={},
        vm_delete_files_result={
            "success": True,
            "vm_name": "lab-vm-3",
            "action": "vm_delete_files",
            "message": "Deleted 1 path(s) from lab-vm-3",
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
            "vm_name": "lab-vm-3",
            "paths": ["/home/agent/hello.txt"],
        }
    )

    assert result["session_id"] == "session-lab-vm-3"
    assert admin_service.calls == [
        {
            "target": "lab-vm-3",
            "vm_user": "agent",
            "paths": ["/home/agent/hello.txt"],
            "recursive": False,
            "missing_ok": True,
        }
    ]


def test_vm_delete_files_provider_uses_fallback_session_id_for_unresolved_target(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(
        sessions={},
        vm_delete_files_result={
            "success": True,
            "vm_name": "lab-vm-4",
            "action": "vm_delete_files",
            "message": "Deleted 1 path(s) from lab-vm-4",
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
            "target": "lab-vm-4",
            "paths": ["/home/agent/hello.txt"],
        }
    )

    assert result["target"] == "lab-vm-4"
    assert result["session_id"] == "session-lab-vm-4"
    assert admin_service.calls == [
        {
            "target": "lab-vm-4",
            "vm_user": "agent",
            "paths": ["/home/agent/hello.txt"],
            "recursive": False,
            "missing_ok": True,
        }
    ]


def test_vm_delete_files_provider_uses_matching_session_id_for_vm_name(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(
        sessions={
            "session-4": SimpleNamespace(vm_id="lab-vm-4"),
        },
        vm_delete_files_result={
            "success": True,
            "vm_name": "lab-vm-4",
            "action": "vm_delete_files",
            "message": "Deleted 1 path(s) from lab-vm-4",
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
            "vm_name": "lab-vm-4",
            "paths": ["/home/agent/hello.txt"],
        }
    )

    assert result["session_id"] == "session-4"
    assert admin_service.calls == [
        {
            "target": "lab-vm-4",
            "vm_user": "agent",
            "paths": ["/home/agent/hello.txt"],
            "recursive": False,
            "missing_ok": True,
        }
    ]


def test_vm_delete_files_provider_raises_for_ambiguous_vm_name_matches(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(
        sessions={
            "session-a": SimpleNamespace(vm_id="shared-vm"),
            "session-b": SimpleNamespace(vm_id="shared-vm"),
        },
        vm_delete_files_result={},
    )
    context = _build_context(tmp_path, admin_service)
    provider = VmDeleteFilesEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(id="delete-files"), runtime_context=context
    )

    with pytest.raises(RuntimeError, match=r"VM 'shared-vm' is associated with multiple sessions"):
        provider.execute(
            resolved_config={
                "vm_name": "shared-vm",
                "paths": ["/home/agent/hello.txt"],
            }
        )

    assert admin_service.calls == []


def test_vm_delete_files_provider_requires_session_lookup(tmp_path: Path) -> None:
    admin_service = _NoLookupAdminService()
    provider = VmDeleteFilesEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(id="delete-files"),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path),
            admin_service=admin_service,
            env={},
        ),
    )

    with pytest.raises(RuntimeError, match="effect:vm:delete-files requires session_lookup"):
        provider.execute(
            resolved_config={
                "vm_name": "lab-vm-5",
                "paths": ["/home/agent/hello.txt"],
            }
        )

    assert admin_service.calls == []
