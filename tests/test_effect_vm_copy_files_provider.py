from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.vm_copy_files import VmCopyFilesEffectProvider
from evander.models import VmSummary


class _AdminServiceStub:
    def __init__(
        self, summaries: list[VmSummary], vm_copy_files_result: dict[str, object] | None = None
    ) -> None:
        self.summaries = summaries
        self.vm_copy_files_result = vm_copy_files_result
        self.calls: list[dict[str, object]] = []

    def list_vms(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.summaries)

    def vm_copy_files(self, **kwargs):
        self.calls.append(kwargs)
        if self.vm_copy_files_result is None:
            raise RuntimeError("vm_copy_files_result not configured")
        return dict(self.vm_copy_files_result)


def _build_context(tmp_path: Path, admin_service: _AdminServiceStub) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path),
        admin_service=admin_service,
        env={},
        metadata={"directory_path": "root/team-a"},
    )


def test_vm_copy_files_provider_copies_inline_and_host_files(tmp_path: Path) -> None:
    source_path = tmp_path / "source.txt"
    source_path.write_text("host-file", encoding="utf-8")

    admin_service = _AdminServiceStub(
        [
            VmSummary(
                vm_name="lab-vm-1",
                session_ids=["session-1"],
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.15",
                agent_state="omitted",
            )
        ],
        vm_copy_files_result={
            "success": True,
            "vm_name": "lab-vm-1",
            "action": "vm_copy_files",
            "message": "Copied 2 path(s) into lab-vm-1",
            "copied_count": 2,
            "copied_paths": ["/home/agent/inline.txt", "/home/agent/source.txt"],
        },
    )
    context = _build_context(tmp_path, admin_service)
    provider = VmCopyFilesEffectProvider()
    provider.initialize(effect_definition=SimpleNamespace(id="copy-files"), runtime_context=context)

    result = provider.execute(
        resolved_config={
            "target": "lab-vm-1",
            "vm_user": "agent",
            "files": [
                {
                    "destination_path": "/home/agent/inline.txt",
                    "content": "inline payload",
                    "mode": "0644",
                },
                {
                    "destination_path": "/home/agent/source.txt",
                    "source_path": str(source_path),
                    "overwrite": False,
                },
            ],
        }
    )

    assert result["success"] is True
    assert result["action"] == "vm_copy_files"
    assert result["message"] == "Copied 2 path(s) into lab-vm-1"
    assert result["vm_name"] == "lab-vm-1"
    assert result["copied_count"] == 2
    assert result["copied_paths"] == ["/home/agent/inline.txt", "/home/agent/source.txt"]
    assert admin_service.calls == [
        {
            "target": "lab-vm-1",
            "files": [
                {
                    "source_path": None,
                    "destination_path": "/home/agent/inline.txt",
                    "content": "inline payload",
                    "content_format": None,
                    "mode": "0644",
                    "overwrite": True,
                },
                {
                    "source_path": str(source_path),
                    "destination_path": "/home/agent/source.txt",
                    "content": None,
                    "content_format": None,
                    "mode": None,
                    "overwrite": False,
                },
            ],
            "vm_user": "agent",
        }
    ]


def test_vm_copy_files_provider_resolves_vm_by_session_id(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        [
            VmSummary(
                vm_name="lab-vm-2",
                session_ids=["session-abc"],
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.22",
                agent_state="omitted",
            )
        ],
        vm_copy_files_result={
            "success": True,
            "vm_name": "lab-vm-2",
            "action": "vm_copy_files",
            "message": "Copied 1 path(s) into lab-vm-2",
            "copied_count": 1,
            "copied_paths": ["/home/agent/hello.txt"],
        },
    )
    context = _build_context(tmp_path, admin_service)
    provider = VmCopyFilesEffectProvider()
    provider.initialize(effect_definition=SimpleNamespace(id="copy-files"), runtime_context=context)

    result = provider.execute(
        resolved_config={
            "session_id": "session-abc",
            "files": [
                {
                    "destination_path": "/home/agent/hello.txt",
                    "content": "hello",
                }
            ],
        }
    )

    assert result["vm_name"] == "lab-vm-2"
    assert result["target"] == "session-abc"
    assert result["copied_count"] == 1
    assert result["copied_paths"] == ["/home/agent/hello.txt"]
    assert admin_service.calls == [
        {
            "target": "session-abc",
            "files": [
                {
                    "source_path": None,
                    "destination_path": "/home/agent/hello.txt",
                    "content": "hello",
                    "content_format": None,
                    "mode": None,
                    "overwrite": True,
                }
            ],
            "vm_user": "agent",
        }
    ]


def test_vm_copy_files_provider_accepts_managed_section_entries(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        [
            VmSummary(
                vm_name="lab-vm-2",
                session_ids=["session-abc"],
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.22",
                agent_state="omitted",
            )
        ],
        vm_copy_files_result={
            "success": True,
            "vm_name": "lab-vm-2",
            "action": "vm_copy_files",
            "message": "Copied 1 path(s) into lab-vm-2",
            "copied_count": 1,
            "copied_paths": ["/home/agent/AGENTS.md"],
        },
    )
    context = _build_context(tmp_path, admin_service)
    provider = VmCopyFilesEffectProvider()
    provider.initialize(effect_definition=SimpleNamespace(id="copy-files"), runtime_context=context)

    result = provider.execute(
        resolved_config={
            "session_id": "session-abc",
            "files": [
                {
                    "destination_path": "/home/agent/AGENTS.md",
                    "content": "Team guidance",
                    "merge_strategy": "managed_section",
                    "section_id": "evander-team-addon",
                }
            ],
        }
    )

    assert result["copied_paths"] == ["/home/agent/AGENTS.md"]
    assert admin_service.calls == [
        {
            "target": "session-abc",
            "files": [
                {
                    "source_path": None,
                    "destination_path": "/home/agent/AGENTS.md",
                    "content": "Team guidance",
                    "content_format": None,
                    "mode": None,
                    "overwrite": True,
                    "merge_strategy": "managed_section",
                    "section_id": "evander-team-addon",
                }
            ],
            "vm_user": "agent",
        }
    ]


def test_vm_copy_files_provider_serializes_object_content_as_yaml(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        [
            VmSummary(
                vm_name="lab-vm-2",
                session_ids=["session-abc"],
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.22",
                agent_state="omitted",
            )
        ],
        vm_copy_files_result={
            "success": True,
            "vm_name": "lab-vm-2",
            "action": "vm_copy_files",
            "message": "Copied 1 path(s) into lab-vm-2",
            "copied_count": 1,
            "copied_paths": ["/home/agent/repo/AGENT_TASK.yaml"],
        },
    )
    context = _build_context(tmp_path, admin_service)
    provider = VmCopyFilesEffectProvider()
    provider.initialize(effect_definition=SimpleNamespace(id="copy-files"), runtime_context=context)

    provider.execute(
        resolved_config={
            "session_id": "session-abc",
            "files": [
                {
                    "destination_path": "/home/agent/repo/AGENT_TASK.yaml",
                    "content": {
                        "issue_number": "14084",
                        "name": "Sample issue",
                        "tags": ["bug", "urgent"],
                    },
                    "content_format": "yaml",
                }
            ],
        }
    )

    assert admin_service.calls == [
        {
            "target": "session-abc",
            "files": [
                {
                    "source_path": None,
                    "destination_path": "/home/agent/repo/AGENT_TASK.yaml",
                    "content": "issue_number: '14084'\nname: Sample issue\ntags:\n- bug\n- urgent\n",
                    "content_format": "yaml",
                    "mode": None,
                    "overwrite": True,
                }
            ],
            "vm_user": "agent",
        }
    ]
