from __future__ import annotations

import pytest

from agent_sandbox.admin.services.workflow_config_service import WorkflowConfigAdminService


class RecordingWorkflowAdmin:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def recorder(*args: object, **kwargs: object) -> dict[str, object]:
            self.calls.append((name, args, kwargs))
            return {"method": name, "args": args, "kwargs": kwargs}

        return recorder


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs", "worker_method", "expected_args", "expected_kwargs"),
    [
        ("read_worker_global_config", ("ado-worker",), {}, "read_worker_global_config", ("ado-worker",), {}),
        ("write_worker_global_config", ("ado-worker", {"enabled": True}), {"expected_revision": "rev-1"}, "write_worker_global_config", ("ado-worker", {"enabled": True}), {"expected_revision": "rev-1"}),
        ("read_workflow_editor_document", (), {}, "read_workflow_editor_document", (), {}),
        ("read_workflow_editor_document", (), {"workflow_pack": "ops"}, "read_workflow_editor_document", (), {"workflow_pack": "ops"}),
        ("read_composed_workflow_editor_document", (), {}, "read_composed_workflow_editor_document", (), {}),
        ("read_workflow_editor_catalog", (), {"directory_path": "root/team"}, "read_workflow_editor_catalog", (), {"directory_path": "root/team"}),
        ("validate_workflow_editor_document", (), {"config": {"nodes": []}, "layout": {"nodes": {}}, "graphs": [{"id": "g"}]}, "validate_workflow_editor_document", (), {"config": {"nodes": []}, "layout": {"nodes": {}}, "graphs": [{"id": "g"}]}),
        ("validate_workflow_editor_document", (), {"config": {"nodes": []}, "layout": None, "graphs": None, "workflow_pack": "ops"}, "validate_workflow_editor_document", (), {"config": {"nodes": []}, "layout": None, "graphs": None, "workflow_pack": "ops"}),
        ("save_workflow_editor_document", (), {"config": {"nodes": []}, "layout": {"nodes": {}}, "graphs": [{"id": "g"}], "expected_revision": "rev-2"}, "save_workflow_editor_document", (), {"config": {"nodes": []}, "layout": {"nodes": {}}, "graphs": [{"id": "g"}], "expected_revision": "rev-2"}),
        ("save_workflow_editor_document", (), {"config": {"nodes": []}, "layout": None, "graphs": None, "expected_revision": "rev-3", "workflow_pack": "ops"}, "save_workflow_editor_document", (), {"config": {"nodes": []}, "layout": None, "graphs": None, "expected_revision": "rev-3", "workflow_pack": "ops"}),
        ("read_workflow_run_snapshot", (), {}, "read_workflow_run_snapshot", (), {}),
        ("read_workflow_run_detail", (), {"run_id": "run", "event_id": "event", "trigger_event_id": "trigger", "trace_id": "trace", "session_id": "session", "artifact_id": "artifact"}, "read_workflow_run_detail", (), {"run_id": "run", "event_id": "event", "trigger_event_id": "trigger", "trace_id": "trace", "session_id": "session", "artifact_id": "artifact"}),
        ("read_workflow_run_search", (), {"node_id": "node", "graph_id": "graph", "without_session": True, "include_completed": False, "limit": 25}, "read_workflow_run_search", (), {"node_id": "node", "graph_id": "graph", "without_session": True, "include_completed": False, "limit": 25}),
        ("clear_workflow_run", ("event",), {}, "clear_workflow_run", ("event",), {}),
        ("dismiss_workflow_node_failures", ("node",), {}, "dismiss_workflow_node_failures", ("node",), {}),
        ("resume_workflow_run", ("event",), {}, "resume_workflow_run", ("event",), {}),
        ("workflow_event_journal", (), {}, "workflow_event_journal", (), {}),
        ("upsert_trigger_config", ("trigger", {"type": "manual"}), {}, "upsert_trigger_config", ("trigger", {"type": "manual"}), {}),
        ("delete_trigger_config", ("trigger",), {}, "delete_trigger_config", ("trigger",), {}),
        ("upsert_effect_config", ("effect", {"type": "notify"}), {}, "upsert_effect_config", ("effect", {"type": "notify"}), {}),
        ("delete_effect_config", ("effect",), {}, "delete_effect_config", ("effect",), {}),
    ],
)
def test_workflow_config_service_delegates_every_public_operation(
    method_name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    worker_method: str,
    expected_args: tuple[object, ...],
    expected_kwargs: dict[str, object],
) -> None:
    worker_admin = RecordingWorkflowAdmin()
    service = WorkflowConfigAdminService(worker_admin)

    assert getattr(service, method_name)(*args, **kwargs) == {
        "method": worker_method,
        "args": expected_args,
        "kwargs": expected_kwargs,
    }
    assert worker_admin.calls == [(worker_method, expected_args, expected_kwargs)]
