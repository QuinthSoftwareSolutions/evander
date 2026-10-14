from __future__ import annotations


from pathlib import Path


from types import MethodType


from starlette.testclient import TestClient


from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime


from evander.admin.web.app import create_app as _create_app


from evander.models import (
    VmActionResult,
)


from config_helpers import (
    canonicalize_workflow_files,
    write_runtime_config,
)


def write_config(
    tmp_path: Path,
    *,
    enable_directory_scoped_config: bool = False,
    execution_backend: str = "libvirt_vm",
) -> Path:
    return write_runtime_config(
        tmp_path,
        enable_directory_scoped_config=enable_directory_scoped_config,
        execution_backend=execution_backend,
    )


def create_app(service: AdminRuntimeComponents, *args, **kwargs):
    canonicalize_workflow_files(service.project_dir)
    return _create_app(service, *args, **kwargs)


def _unlock_privileged_operations(client: TestClient) -> None:
    response = client.post("/api/admin/privileged/unlock")
    assert response.status_code == 200


def test_admin_web_agents_create_vm_forwards_supplied_teams_chat_allowlist(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    captured: dict[str, object] = {}

    def create_vm(self, **payload):
        captured.update(payload)
        return VmActionResult(
            success=True,
            vm_name=payload["vm_name"],
            action="create_vm",
            message="created",
            provider="libvirt",
        )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_vm",
        MethodType(create_vm, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        "/api/admin/agents",
        json={
            "backend": "libvirt_vm",
            "vm_name": "lab-vm",
            "teams_chat_allowlist": [" chat:chat-1 ", "chat:chat-2"],
        },
    )

    assert response.status_code == 201
    assert captured["teams_chat_allowlist"] == ["chat:chat-1", "chat:chat-2"]
