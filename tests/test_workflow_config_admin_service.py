from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from evander.admin.services.process_helpers import resolve_path_setting
from evander.admin.services.workflow_config_service import (
    WorkflowConfigAdminDependencies,
    WorkflowConfigAdminService,
)


class EmptySkillLibrary:
    def list_skills(self) -> list[object]:
        return []


class RecordingWorkflowReportsAdmin:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _read_workflow_run_snapshot(self) -> dict[str, object]:
        self.calls.append(("_read_workflow_run_snapshot", (), {}))
        return {"snapshot": True}

    def _read_workflow_run_detail(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("_read_workflow_run_detail", (), kwargs))
        return {"detail": kwargs}

    def _read_workflow_run_search(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("_read_workflow_run_search", (), kwargs))
        return {"search": kwargs}


def _make_service(
    tmp_path: Path,
    *,
    workflow_reports_admin: object | None = None,
    admin_config_mutation_lock=None,
) -> WorkflowConfigAdminService:
    return WorkflowConfigAdminService(
        WorkflowConfigAdminDependencies(
            project_dir=tmp_path,
            config=SimpleNamespace(
                runtime_dir=tmp_path / "runtime",
                model_dump=lambda **_kwargs: {},
            ),
            skill_library=EmptySkillLibrary(),
            directory_admin=SimpleNamespace(),
            admin_config_mutation_lock=admin_config_mutation_lock or _null_lock,
            normalize_directory_reference=lambda value: value or "root",
            workflow_reports_admin=workflow_reports_admin,
        )
    )


@contextmanager
def _null_lock():
    yield


def test_workflow_config_service_delegates_report_reads(tmp_path: Path) -> None:
    reports_admin = RecordingWorkflowReportsAdmin()
    service = _make_service(tmp_path, workflow_reports_admin=reports_admin)

    assert service.read_workflow_run_snapshot() == {"snapshot": True}
    assert service.read_workflow_run_detail(run_id="run", limit=5) == {
        "detail": {"run_id": "run", "limit": 5}
    }
    assert service.read_workflow_run_search(node_id="node", include_completed=False) == {
        "search": {"node_id": "node", "include_completed": False}
    }
    assert reports_admin.calls == [
        ("_read_workflow_run_snapshot", (), {}),
        ("_read_workflow_run_detail", (), {"run_id": "run", "limit": 5}),
        ("_read_workflow_run_search", (), {"node_id": "node", "include_completed": False}),
    ]


def test_workflow_config_service_uses_injected_mutation_lock(tmp_path: Path) -> None:
    events: list[str] = []

    @contextmanager
    def recording_lock():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    service = _make_service(tmp_path, admin_config_mutation_lock=recording_lock)

    with service.admin_config_mutation_lock():
        events.append("body")

    assert events == ["enter", "body", "exit"]


def test_resolve_path_setting_preserves_home_and_relative_expansion(
    tmp_path: Path, monkeypatch
) -> None:
    home_dir = tmp_path / "home"
    project_dir = tmp_path / "project"
    fallback = tmp_path / "fallback-runtime"
    monkeypatch.setenv("HOME", str(home_dir))

    assert (
        resolve_path_setting(
            base_dir=project_dir,
            value="~/agent-runtime",
            fallback=fallback,
        )
        == (home_dir / "agent-runtime").resolve()
    )
    assert (
        resolve_path_setting(
            base_dir=project_dir,
            value="custom-workflow-runtime",
            fallback=fallback,
        )
        == (project_dir / "custom-workflow-runtime").resolve()
    )
    assert (
        resolve_path_setting(
            base_dir=project_dir,
            value="",
            fallback=fallback,
        )
        == fallback.resolve()
    )
