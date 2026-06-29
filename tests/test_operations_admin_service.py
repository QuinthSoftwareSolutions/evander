from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_sandbox.admin.services.operations_service import OperationsAdminService


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
    worker_admin: Any | None = None,
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
        worker_admin=worker_admin or domain,
        directory_admin=directory_admin or domain,
        workflow_config_admin=domain,
        trigger_repository=domain,
        privileged_helper_factory=privileged_helper_factory or FakePrivilegedHelperFactory(),
    )


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs", "domain_method", "expected_args", "expected_kwargs"),
    [
        ("teams_auth_login", (), {"directory": "eng"}, "login", (), {"directory": "eng"}),
        (
            "teams_auth_complete_login",
            ("login-session",),
            {"directory": "eng", "timeout_seconds": 1.5},
            "complete_login",
            ("login-session",),
            {"directory": "eng", "timeout_seconds": 1.5},
        ),
        ("teams_auth_status", (), {"directory": "eng"}, "status", (), {"directory": "eng"}),
        ("teams_auth_logout", (), {"directory": "eng"}, "logout", (), {"directory": "eng"}),
        ("teams_auth_list", (), {}, "list", (), {}),
        (
            "teams_auth_list_channels",
            (),
            {"directory": "eng", "team_id": "team", "channel_name": "ops", "include_all": True},
            "list_channels",
            (),
            {"directory": "eng", "team_id": "team", "channel_name": "ops", "include_all": True},
        ),
    ],
)
def test_operations_admin_service_delegates_teams_methods(
    method_name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    domain_method: str,
    expected_args: tuple[object, ...],
    expected_kwargs: dict[str, object],
) -> None:
    teams = RecordingDomain()
    service = _service(teams_admin=teams)

    assert getattr(service, method_name)(*args, **kwargs) == {
        "method": domain_method,
        "args": expected_args,
        "kwargs": expected_kwargs,
    }
    assert teams.calls == [(domain_method, expected_args, expected_kwargs)]


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs", "domain_method", "expected_args", "expected_kwargs"),
    [
        (
            "list_sessions",
            (),
            {"include_expired": True, "expired_only": True, "directory_path": "root/team", "direct_only": True},
            "list_sessions",
            (),
            {"include_expired": True, "expired_only": True, "directory_path": "root/team", "direct_only": True},
        ),
        ("list_wake_schedules", (), {"session_id": "s1", "include_disabled": False}, "list_wake_schedules", (), {"session_id": "s1", "include_disabled": False}),
        ("create_wake_schedule", (), {"session_id": "s1", "prompt": "wake", "cron": "0 * * * *", "metadata": {"k": "v"}}, "create_wake_schedule", (), {"session_id": "s1", "prompt": "wake", "resume_at": None, "cron": "0 * * * *", "resume_mode": "same_context", "enabled": True, "request_id": None, "source": "admin_cli", "metadata": {"k": "v"}}),
        ("edit_wake_schedule", ("req-1",), {"prompt": "new", "enabled": False}, "edit_wake_schedule", ("req-1",), {"resume_at": None, "cron": None, "prompt": "new", "resume_mode": None, "enabled": False}),
        ("delete_wake_schedule", ("req-1",), {}, "delete_wake_schedule", ("req-1",), {}),
        ("renew_session", ("s1",), {"extension": "2d"}, "renew_session", ("s1",), {"extension": "2d"}),
        ("revoke_session", ("s1",), {}, "revoke_session", ("s1",), {}),
        ("delete_session", ("s1",), {}, "delete_session", ("s1",), {}),
        ("set_session_allowed_tools", ("s1", ["shell"]), {}, "set_session_allowed_tools", ("s1", ["shell"]), {}),
        ("set_session_allowed_script_names", ("s1", ["build"]), {}, "set_session_allowed_script_names", ("s1", ["build"]), {}),
        ("update_session_policy", ("s1", {"model": "gpt"}), {}, "update_session_policy", ("s1", {"model": "gpt"}), {}),
        ("set_session_model", ("s1", "gpt"), {}, "set_session_model", ("s1", "gpt"), {}),
    ],
)
def test_operations_admin_service_delegates_session_methods(method_name, args, kwargs, domain_method, expected_args, expected_kwargs) -> None:
    session = RecordingDomain()
    service = _service(session_admin=session)

    assert getattr(service, method_name)(*args, **kwargs) == {"method": domain_method, "args": expected_args, "kwargs": expected_kwargs}
    assert session.calls == [(domain_method, expected_args, expected_kwargs)]


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs", "domain_method", "expected_args", "expected_kwargs"),
    [
        ("list_background_workers", (), {}, "list_background_workers", (), {}),
        ("get_background_worker_log_snapshot", ("worker",), {"lines": 50}, "get_background_worker_log_snapshot", ("worker",), {"lines": 50}),
        ("start_background_worker", ("worker",), {}, "start_background_worker", ("worker",), {}),
        ("stop_background_worker", ("worker",), {}, "stop_background_worker", ("worker",), {}),
    ],
)
def test_operations_admin_service_delegates_worker_methods(method_name, args, kwargs, domain_method, expected_args, expected_kwargs) -> None:
    worker = RecordingDomain()
    service = _service(worker_admin=worker)

    assert getattr(service, method_name)(*args, **kwargs) == {"method": domain_method, "args": expected_args, "kwargs": expected_kwargs}
    assert worker.calls == [(domain_method, expected_args, expected_kwargs)]


def test_operations_admin_service_delegates_directory_methods_with_normalized_paths() -> None:
    directory = RecordingDomain()
    service = _service(directory_admin=directory)

    assert service.list_directory_tree() == {"method": "tree", "args": ("root",), "kwargs": {}}
    directory_result = service.get_directory("team")
    assert directory_result.ok is True
    assert directory_result.data.details.directory.path == "canonical/team"
    assert service.create_directory_child("team", "child") == {"method": "create_child", "args": (), "kwargs": {"parent": "canonical/team", "name": "child"}}
    assert service.read_directory_domain_config("team", "providers") == {"method": "read_domain_config", "args": ("canonical/team",), "kwargs": {"domain": "providers"}}
    assert service.write_directory_domain_config("team", "providers", {"mode": "test"}) == {"method": "write_domain_config", "args": ("canonical/team",), "kwargs": {"domain": "providers", "payload": {"mode": "test"}}}
    assert service.compute_directory_effective_config("team", "providers") == {"method": "compute_effective_config", "args": ("canonical/team",), "kwargs": {"domain": "providers"}}


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
