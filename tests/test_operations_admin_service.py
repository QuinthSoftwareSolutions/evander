from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any


from evander.admin.services.operations_service import OperationsAdminService


class RecordingDomain:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def recorder(*args: object, **kwargs: object) -> dict[str, object]:
            self.calls.append((name, args, kwargs))
            return {"method": name, "args": args, "kwargs": kwargs}

        return recorder

    def get(self, directory_path: str):
        self.calls.append(("get", (directory_path,), {}))
        normalized_path = (
            directory_path
            if directory_path.startswith("canonical/")
            else f"canonical/{directory_path}"
        )
        directory = SimpleNamespace(path=normalized_path)
        details = SimpleNamespace(directory=directory)
        return SimpleNamespace(ok=True, data=SimpleNamespace(details=details))


class FakePrivilegedHelperPort:
    def __init__(self, *, required: bool = False) -> None:
        self.required = required
        self.helper: Any | None = None

    def get_use_privileged_helper(self) -> bool:
        return self.required

    def get_privileged_helper(self) -> Any | None:
        return self.helper

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


class FakePrivilegedHelperFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.helper = FakeHelper()

    def create_privileged_helper(self, **kwargs: object) -> FakeHelper:
        self.calls.append(kwargs)
        return self.helper


def _service(
    *,
    teams_admin: Any | None = None,
    session_admin: Any | None = None,
    wake_schedule_admin: Any | None = None,
    worker_control_admin: Any | None = None,
    directory_admin: Any | None = None,
    privileged_helper: FakePrivilegedHelperPort | None = None,
    privileged_helper_factory: FakePrivilegedHelperFactory | None = None,
) -> OperationsAdminService:
    domain = RecordingDomain()
    return OperationsAdminService(
        config=SimpleNamespace(),
        project_dir=Path("/tmp/project"),
        privileged_helper=privileged_helper or FakePrivilegedHelperPort(),
        teams_admin=teams_admin or domain,
        session_admin=session_admin or domain,
        wake_schedule_admin=wake_schedule_admin or domain,
        worker_control_admin=worker_control_admin or domain,
        directory_admin=directory_admin or domain,
        workflow_config_admin=domain,
        trigger_repository=domain,
        privileged_helper_factory=privileged_helper_factory or FakePrivilegedHelperFactory(),
    )


def test_operations_admin_service_delegates_directory_methods_with_normalized_paths() -> None:
    directory = RecordingDomain()
    service = _service(directory_admin=directory)

    assert service.list_directory_tree() == {"method": "tree", "args": ("root",), "kwargs": {}}
    directory_result = service.get_directory("team")
    assert directory_result.ok is True
    assert directory_result.data.details.directory.path == "canonical/team"
    assert service.create_directory_child("team", "child") == {
        "method": "create_child",
        "args": (),
        "kwargs": {"parent": "canonical/team", "name": "child"},
    }
    assert service.read_directory_domain_config("team", "providers") == {
        "method": "read_domain_config",
        "args": ("canonical/team",),
        "kwargs": {"domain": "providers"},
    }
    assert service.write_directory_domain_config("team", "providers", {"mode": "test"}) == {
        "method": "write_domain_config",
        "args": ("canonical/team",),
        "kwargs": {"domain": "providers", "payload": {"mode": "test"}},
    }
    assert service.compute_directory_effective_config("team", "providers") == {
        "method": "compute_effective_config",
        "args": ("canonical/team",),
        "kwargs": {"domain": "providers"},
    }


def test_operations_admin_service_manages_privileged_helper_lifecycle() -> None:
    helper_port = FakePrivilegedHelperPort(required=True)
    factory = FakePrivilegedHelperFactory()
    service = _service(privileged_helper=helper_port, privileged_helper_factory=factory)

    assert service.privileged_helper_required() is True
    assert service.privileged_operations_unlocked() is False
    assert service.unlock_privileged_operations() == "Privileged operations unlocked."
    assert factory.helper.started is True
    assert service.privileged_operations_unlocked() is True

    service.lock_privileged_operations()
    assert factory.helper.closed is True
    assert helper_port.helper is None
