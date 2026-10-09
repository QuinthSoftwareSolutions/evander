from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config_helpers import write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.admin.services.connector_admin_service import ConnectorConfigRevisionConflict
from evander.admin.services.host_directory_admin_service import HostDirectoryConfigRevisionConflict
from evander.admin.services.model_usage_service import ModelUsageAdminService
from evander.admin.services import model_usage_service
from evander.admin.services.workflow_run_search import WorkflowRunSearchBuilder
from evander.daemons.effect_orchestrator import EffectExecutionRecord
from evander.directories import DirectoryService


@pytest.mark.parametrize("domain", ["connectors", "host_directories"])
def test_directory_admin_save_preserves_inheritance_and_revision_contract(
    tmp_path: Path, domain: str
) -> None:
    runtime = build_admin_runtime(
        write_runtime_config(tmp_path, enable_directory_scoped_config=True)
    )
    DirectoryService(runtime.runtime.directories).create(parent="root", name="team")
    if domain == "connectors":
        admin = runtime.connector_admin
        conflict = ConnectorConfigRevisionConflict
        parent_definition = {
            "display_name": "Parent",
            "description": "Inherited description",
            "driver": "rest",
            "config": {"base_url": "https://synthetic.example.test"},
            "operations": {
                "read": {
                    "description": "Read synthetic data",
                    "input_schema": {"type": "object"},
                    "access": "read",
                    "requires_approval": False,
                    "result_mode": "json",
                    "config": {"method": "GET", "path": "/"},
                }
            },
        }
    else:
        admin = runtime.host_directory_admin
        conflict = HostDirectoryConfigRevisionConflict
        root = tmp_path / "shared"
        root.mkdir()
        parent_definition = {
            "display_name": "Parent",
            "description": "Inherited description",
            "root": str(root),
            "capabilities": ["list", "delete"],
        }
    assert runtime.operations_admin.write_directory_domain_config(
        "root", domain, {domain: {"shared": parent_definition}}
    ).ok
    before = admin.snapshot("root/team")
    assert before["revision"] == sha256(b"{}").hexdigest()
    overlay = {domain: {"shared": {"display_name": "Child"}}}
    saved = admin.save("root/team", expected_revision=before["revision"], config_payload=overlay)
    expected_json = '{"' + domain + '":{"shared":{"display_name":"Child"}}}'
    assert saved["revision"] == sha256(expected_json.encode()).hexdigest()
    effective = saved["effective_config"][domain]["shared"]
    assert effective["display_name"] == "Child"
    assert effective["description"] == "Inherited description"
    if domain == "connectors":
        assert effective["config"]["base_url"] == "https://synthetic.example.test"
        assert effective["operations"]["read"]["config"]["method"] == "GET"
    else:
        assert effective["root"] == str(root)
        assert effective["capabilities"] == ["list", "delete"]
    assert admin.snapshot("root")["effective_config"][domain]["shared"]["display_name"] == "Parent"
    # Revision conflicts must win over invalid configuration, before any write.
    with pytest.raises(conflict):
        admin.save("root/team", expected_revision=before["revision"], config_payload={domain: 42})
    assert admin.snapshot("root/team") == saved


def test_run_search_collects_effect_and_gate_payloads_in_order(tmp_path: Path) -> None:
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    effect = EffectExecutionRecord(
        execution_id="effect-execution",
        idempotency_key="event::effect",
        event_id="event",
        trace_id="trace",
        effect_id="effect",
        effect_type="effect:test",
        source_node_id="effect-source",
        source_trigger_type="trigger:effect",
        status="failed",
        started_at=now,
        finished_at=now + timedelta(seconds=1),
        resolved_config_hash="hash",
        resolved_config={"session_id": "session-direct"},
        input_payload={},
        outputs={},
        error="effect failure",
    )
    gate = {
        "event_id": "event",
        "trace_id": "trace",
        "gate_id": "gate",
        "source_node_id": "gate-source",
        "source_trigger_type": "trigger:gate",
        "status": "waiting",
        "started_at": now.isoformat(),
        "finished_at": (now + timedelta(seconds=2)).isoformat(),
        "inputs_by_source": {
            "upstream": {
                "approval_request_id": "approval",
                "artifact_id": "comm_00000000000000000000000000000001",
            }
        },
        "error": "gate failure",
    }
    artifact_store = Mock()
    artifact_store.payload.return_value = {"session_id": "session-artifact"}
    service = SimpleNamespace(
        _read_composed_workflow_editor_document=lambda: SimpleNamespace(
            graphs=[SimpleNamespace(id="graph", name="Graph", node_ids=["effect", "gate"])]
        ),
        _workflow_run_store_paths=lambda: {
            "legacy_await_agent_store_path": tmp_path / "legacy.json",
            "await_agent_store_path": tmp_path / "await.json",
        },
        _workflow_run_search_sqlite_event_ids=lambda *args, **kwargs: (["event"], 1, False),
        _workflow_run_sqlite_records_for_event_ids=lambda *args, **kwargs: (
            {},
            {
                "effect-execution": effect,
                "fallback-event": {
                    "effect_id": "effect",
                    "status": "succeeded",
                    "started_at": now.isoformat(),
                    "inputs_by_source": {"session_id": "ignored-effect-input"},
                },
                "invalid-effect": None,
            },
            {"gate-execution": gate, "invalid-gate": []},
        ),
        runtime=SimpleNamespace(
            approvals=SimpleNamespace(
                read_all=lambda: {"approval": SimpleNamespace(session_id="session-approval")}
            )
        ),
        _communication_artifact_store=lambda: artifact_store,
    )
    result = WorkflowRunSearchBuilder(
        service=service,
        node_id=None,
        graph_id="graph",
        limit=10,
        without_session=False,
        include_completed=True,
        clock=lambda: now,
    ).build()
    run = result.runs[0]
    assert result.matched == result.returned == 2
    assert [item.event_id for item in result.runs] == ["event", "fallback-event"]
    assert result.runs[1].session_ids == []
    assert result.runs[1].run_state == "completed"
    assert run.event_id == "event"
    assert run.run_state == "errored"
    assert run.source_node_id == "effect-source"
    assert run.source_trigger_type == "trigger:effect"
    assert run.error == "effect failure"
    assert run.status_counts == {"failed": 1, "waiting": 1}
    assert run.latest_activity_at == now + timedelta(seconds=2)
    assert run.session_ids == ["session-approval", "session-artifact", "session-direct"]
    assert [(node.node_id, node.source, node.status) for node in run.nodes] == [
        ("gate", "gate_execution", "waiting"),
        ("effect", "effect_execution", "failed"),
    ]
    artifact_store.payload.assert_called_once_with("comm_00000000000000000000000000000001")


@pytest.fixture
def steering_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    usage_store = Mock()
    usage_store.get_active_correlation_context.return_value = SimpleNamespace(
        execution_backend="libvirt_vm"
    )
    note_store = Mock()
    monkeypatch.setattr(model_usage_service, "model_usage_store", lambda path: usage_store)
    monkeypatch.setattr(
        model_usage_service, "operator_steering_note_store", lambda path: note_store
    )
    dependencies = SimpleNamespace(
        runtime_dir=tmp_path,
        session_repository=Mock(),
        is_container_session=Mock(return_value=False),
        interrupt_container_operator_steering_note=Mock(return_value={"mode": "interrupt"}),
    )
    dependencies.session_repository.get_session.return_value = SimpleNamespace(session_id="session")
    return ModelUsageAdminService(dependencies), dependencies, usage_store, note_store


@pytest.mark.parametrize(
    "method", ["create_operator_steering_note", "submit_operator_steering_note"]
)
@pytest.mark.parametrize(
    ("session_id", "dispatch_id", "message", "error"),
    [(" ", " ", " ", "session_id is required"), ("session", " ", " ", "dispatch_id is required")],
)
def test_steering_validates_identifiers_before_reading_context(
    steering_service, method: str, session_id: str, dispatch_id: str, message: str, error: str
) -> None:
    service, dependencies, usage_store, note_store = steering_service
    with pytest.raises(ValueError, match=f"^{error}$"):
        getattr(service, method)(session_id=session_id, dispatch_id=dispatch_id, message=message)
    usage_store.get_active_correlation_context.assert_not_called()
    dependencies.session_repository.get_session.assert_not_called()
    note_store.create_note.assert_not_called()


@pytest.mark.parametrize(
    ("context_backend", "session_present", "session_container", "classifier_calls", "error"),
    [
        ("container_worker_vm", True, False, 0, None),
        ("container_worker_vm", False, False, 0, "unknown session: session"),
        ("libvirt_vm", True, True, 1, None),
        ("libvirt_vm", True, False, 1, None),
        ("libvirt_vm", False, False, 0, None),
    ],
)
def test_steering_preserves_container_routing_and_queued_context_recheck(
    steering_service, context_backend, session_present, session_container, classifier_calls, error
) -> None:
    service, dependencies, usage_store, note_store = steering_service
    usage_store.get_active_correlation_context.return_value.execution_backend = context_backend
    dependencies.is_container_session.return_value = session_container
    if not session_present:
        dependencies.session_repository.get_session.return_value = None
    kwargs = {
        "session_id": " session ",
        "dispatch_id": " dispatch ",
        "message": " note ",
        "worker_execution_id": "worker",
        "created_by": "actor",
    }
    if error:
        with pytest.raises(ValueError, match=f"^{error}$"):
            service.submit_operator_steering_note(**kwargs)
        dependencies.interrupt_container_operator_steering_note.assert_not_called()
    else:
        result = service.submit_operator_steering_note(**kwargs)
        if context_backend == "container_worker_vm" or session_container:
            assert result == {"mode": "interrupt"}
            dependencies.interrupt_container_operator_steering_note.assert_called_once_with(
                session=dependencies.session_repository.get_session.return_value,
                context=usage_store.get_active_correlation_context.return_value,
                message="note",
                worker_execution_id="worker",
                created_by="actor",
            )
            assert usage_store.get_active_correlation_context.call_count == 1
            note_store.create_note.assert_not_called()
        else:
            assert result == {"mode": "queued_note", "note": note_store.create_note.return_value}
            assert usage_store.get_active_correlation_context.call_count == 2
            note_store.create_note.assert_called_once_with(
                session_id="session",
                dispatch_id="dispatch",
                message="note",
                worker_execution_id="worker",
                created_by="actor",
            )
    assert dependencies.is_container_session.call_count == classifier_calls
    usage_store.get_active_correlation_context.assert_called_with(
        session_id="session", dispatch_id="dispatch"
    )


def test_steering_message_validation_precedes_context_but_create_defers_it(
    steering_service,
) -> None:
    service, dependencies, usage_store, note_store = steering_service
    usage_store.get_active_correlation_context.return_value = None
    with pytest.raises(ValueError, match="^message is required$"):
        service.submit_operator_steering_note(
            session_id="session", dispatch_id="dispatch", message=" "
        )
    usage_store.get_active_correlation_context.assert_not_called()
    with pytest.raises(ValueError, match="No active model-correlation context"):
        service.create_operator_steering_note(
            session_id="session", dispatch_id="dispatch", message=" "
        )
    dependencies.session_repository.get_session.assert_not_called()
    note_store.create_note.assert_not_called()
