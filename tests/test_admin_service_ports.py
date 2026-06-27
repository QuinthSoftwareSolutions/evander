from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_sandbox.admin.facades import WorkerAdminDependencies, WorkerAdminFacade
from agent_sandbox.admin.services.operations_service import OperationsAdminService


class FakeDirectoryOwnership:
    def __init__(self) -> None:
        self.normalized: list[str | None] = []
        self.owned: list[tuple[str, str]] = []

    def normalize_directory_path(self, directory_path: str | None) -> str:
        self.normalized.append(directory_path)
        return directory_path or "root"

    def direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> set[str]:
        self.owned.append((directory_path, kind))
        return {"vm-1"}


class FakeWorkflowConfig:
    def __init__(self) -> None:
        self.saved_payload: dict[str, Any] | None = None

    def read_worker_global_config(self, worker_id: str) -> dict[str, str]:
        return {"worker_id": worker_id}

    def write_worker_global_config(self, worker_id: str, payload: dict[str, object], *, expected_revision: str | None = None) -> dict[str, object | None]:
        return {"worker_id": worker_id, "payload": payload, "expected_revision": expected_revision}

    def read_workflow_editor_document(self, workflow_pack: str | None = None) -> dict[str, object]:
        return {"workflow_pack": workflow_pack}

    def read_composed_workflow_editor_document(self) -> dict[str, bool]:
        return {"composed": True}

    def read_workflow_editor_catalog(self, directory_path: str | None = None) -> dict[str, object]:
        return {"directory_path": directory_path}

    def validate_workflow_editor_document(self, **kwargs: Any) -> dict[str, Any]:
        return {"validated": kwargs}

    def save_workflow_editor_document(self, **kwargs: Any) -> dict[str, Any]:
        self.saved_payload = kwargs
        return {"saved": kwargs}

    def read_workflow_run_snapshot(self) -> dict[str, bool]:
        return {"snapshot": True}

    def read_workflow_run_detail(self, **kwargs: Any) -> dict[str, Any]:
        return {"detail": kwargs}

    def read_workflow_run_search(self, **kwargs: Any) -> dict[str, Any]:
        return {"search": kwargs}

    def clear_workflow_run(self, event_id: str) -> dict[str, str]:
        return {"cleared": event_id}

    def dismiss_workflow_node_failures(self, node_id: str) -> dict[str, str]:
        return {"dismissed": node_id}

    def resume_workflow_run(self, event_id: str) -> dict[str, str]:
        return {"resumed": event_id}

    def workflow_event_journal(self) -> object:
        return object()

    def upsert_trigger_config(self, trigger_id: str, payload: dict[str, object]) -> dict[str, object]:
        return {"trigger_id": trigger_id, "payload": payload}

    def delete_trigger_config(self, trigger_id: str) -> dict[str, str]:
        return {"trigger_id": trigger_id}

    def upsert_effect_config(self, effect_id: str, payload: dict[str, object]) -> dict[str, object]:
        return {"effect_id": effect_id, "payload": payload}

    def delete_effect_config(self, effect_id: str) -> dict[str, str]:
        return {"effect_id": effect_id}


class FakePrivilegedHelperPort:
    def __init__(self) -> None:
        self.helper: Any | None = None
        self.required = True

    def get_privileged_helper(self) -> Any | None:
        return self.helper

    def get_use_privileged_helper(self) -> bool:
        return self.required

    def set_privileged_helper(self, helper: Any | None) -> None:
        self.helper = helper


class FakeHelper:
    def __init__(self) -> None:
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True

    def is_running(self) -> bool:
        return self.started and not self.closed


class FakeHelperFactory:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create_privileged_helper(self, **kwargs: Any) -> FakeHelper:
        self.created.append(kwargs)
        return FakeHelper()


def test_worker_admin_facade_uses_directory_and_workflow_ports() -> None:
    directory_ownership = FakeDirectoryOwnership()
    workflow_config = FakeWorkflowConfig()
    facade = WorkerAdminFacade(
        WorkerAdminDependencies(
            runtime=SimpleNamespace(),
            config=SimpleNamespace(),
            agent_control_store=SimpleNamespace(status_for_vm=lambda _vm_name: "enabled"),
            directory_ownership=directory_ownership,
            workflow_config=workflow_config,
        )
    )

    assert facade.read_worker_global_config("worker-1") == {"worker_id": "worker-1"}
    assert facade.read_workflow_run_search(node_id="node-1") == {
        "search": {
            "node_id": "node-1",
            "graph_id": None,
            "without_session": False,
            "include_completed": True,
            "limit": None,
        }
    }


def test_operations_service_uses_privileged_helper_port(tmp_path: Path) -> None:
    privileged_helper = FakePrivilegedHelperPort()
    factory = FakeHelperFactory()
    service = OperationsAdminService(
        project_dir=tmp_path,
        privileged_helper=privileged_helper,
        teams_admin=SimpleNamespace(),
        session_admin=SimpleNamespace(),
        worker_admin=SimpleNamespace(),
        directory_admin=SimpleNamespace(),
        privileged_helper_factory=factory,
    )

    assert service.privileged_helper_required() is True
    assert service.unlock_privileged_operations() == "Privileged operations unlocked."
    assert isinstance(privileged_helper.helper, FakeHelper)
    assert privileged_helper.helper.is_running()
    assert factory.created == [{"python_executable": __import__("sys").executable, "project_dir": tmp_path}]

    service.lock_privileged_operations()
    assert privileged_helper.helper is None
