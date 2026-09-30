from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from config_helpers import canonical_workflow_payload, write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.admin.web.app import create_app
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.daemons.trigger_orchestrator import TriggerOrchestratorWorker
from evander.events.internal import InternalEventBus
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config
from evander.workflow.state_store import WorkflowStateStore


@pytest.fixture
def manual_trigger(tmp_path, monkeypatch):
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "none")
    service = build_admin_runtime(write_runtime_config(tmp_path))
    config = canonical_workflow_payload({
        "runtime_dir": str(tmp_path / "runtime"),
        "triggers": [
            {"id": name, "type": "trigger:event", "display_name": name,
             "event_type": "custom.operator_event", "correlation_key": "ticket:demo"}
            for name in ("alpha", "beta")
        ] + [{"id": "schedule", "type": "trigger:cron", "display_name": "Schedule",
              "expression": "0 * * * *", "enabled": False}],
        "effects": [
            {"id": f"store-{name}", "type": "effect:state:update", "display_name": "Store",
             "namespace": "manual", "key": "result", "operation": "set",
             "value": "${{" + name + ".payload}}"}
            for name in ("alpha", "beta")
        ],
        "edges": [{"source_id": name, "target_id": f"store-{name}"} for name in ("alpha", "beta")],
    })
    config["workflow_graphs"] = [
        {"id": f"graph-{name}", "name": name, "directory_path": "root",
         "node_ids": [name, f"store-{name}"]} for name in ("alpha", "beta")
    ]
    service.workflow_config_admin.save_workflow_editor_document(
        config=config,
        layout={},
        graphs=config["workflow_graphs"],
    )
    app = create_app(service)
    with TestClient(app) as client:
        yield service, app, client


def _request(service, **changes):
    return {
        "expected_revision": service.workflow_config_admin.read_workflow_editor_document().revision,
        "payload": {"answer": "yes"},
        "request_id": "manual-request-1",
        **changes,
    }


def _queue(service):
    return SqliteTriggerEventQueue(service.config.runtime_dir / "orchestration.db")


def test_manual_event_runs_only_selected_node_with_normal_envelope(manual_trigger, monkeypatch):
    service, _app, client = manual_trigger
    config = load_trigger_orchestrator_worker_global_config(service.project_dir, base_config=service.config)
    trigger_worker = TriggerOrchestratorWorker(config=service.config, worker_config=config, runtime=None)
    assert trigger_worker.run_once().events_published == 0
    wake_paths: list[Path] = []
    monkeypatch.setattr(
        "evander.admin.services.workflow_config_service.notify_wake_socket_for_lock",
        lambda path: wake_paths.append(path),
    )
    response = client.post("/api/workflow-editor/nodes/alpha/trigger", json=_request(service))

    assert response.status_code == 202, response.text
    record = _queue(service).list_pending()[0]
    assert record.trigger_id == "alpha"
    assert record.trigger_type == "trigger:event"
    assert record.trace_id == response.json()["trace_id"]
    assert record.event_id == response.json()["event_id"]
    assert record.directory_path == "root"
    assert record.config_revision
    assert record.payload["event_type"] == "custom.operator_event"
    assert record.payload["payload"] == {"answer": "yes"}
    assert record.payload["correlation_key"] == "ticket:demo"
    assert record.payload["source"]["source_type"] == "workflow_editor_manual"
    assert record.payload["source"]["user_id"] == "admin"
    assert record.payload["source"]["node_id"] == "alpha"
    assert record.payload["created_at"].endswith("Z")
    assert record.payload["idempotency_key"] == record.idempotency_key
    assert wake_paths == [config.effect_lock_path]
    assert InternalEventBus(config.orchestration_store_path).list_events() == []
    assert trigger_worker.run_once().events_published == 0
    queued_detail = client.get("/api/workflow-editor/run-detail", params={"event_id": record.event_id})
    assert queued_detail.status_code == 200, queued_detail.text
    assert queued_detail.json()["source_node_id"] == "alpha"

    effect_worker = EffectOrchestratorWorker(config=service.config, worker_config=config, runtime=None)
    effect_worker.run_once()
    effect_worker._wait_for_inflight_completion(timeout_seconds=2)
    effect_worker.run_once()
    state = WorkflowStateStore(service.config.runtime_dir)
    assert state.get("root", "graph-alpha", "manual", "result").record.value == {"answer": "yes"}
    assert not state.get("root", "graph-beta", "manual", "result").found
    detail = client.get("/api/workflow-editor/run-detail", params={"event_id": record.event_id})
    assert detail.status_code == 200, detail.text
    assert detail.json()["source_node_id"] == "alpha"
    assert any(node["node_id"] == "store-alpha" for node in detail.json()["nodes"])


def test_manual_event_retries_are_idempotent_and_new_requests_run_again(manual_trigger):
    service, _app, client = manual_trigger
    body = _request(service)
    first = client.post("/api/workflow-editor/nodes/alpha/trigger", json=body)
    retry = client.post("/api/workflow-editor/nodes/alpha/trigger", json=body)
    assert first.status_code == retry.status_code == 202
    assert retry.json() == first.json()
    mismatch = client.post("/api/workflow-editor/nodes/alpha/trigger", json={**body, "payload": {"answer": "no"}})
    assert mismatch.status_code == 400
    assert len(_queue(service).list_pending()) == 1
    new = client.post("/api/workflow-editor/nodes/alpha/trigger", json={**body, "request_id": "manual-request-2"})
    other_node = client.post("/api/workflow-editor/nodes/beta/trigger", json=body)
    assert new.status_code == other_node.status_code == 202
    assert len({first.json()["event_id"], new.json()["event_id"], other_node.json()["event_id"]}) == 3


@pytest.mark.parametrize("change", [
    {"payload": []}, {"payload": None}, {"payload": "hello"},
    {"request_id": " "}, {"correlation_key": "wrong-filter"},
])
def test_manual_event_rejects_invalid_inputs_without_queueing(manual_trigger, change):
    service, _app, client = manual_trigger
    response = client.post("/api/workflow-editor/nodes/alpha/trigger", json=_request(service, **change))
    assert response.status_code == 400
    assert _queue(service).list_pending() == []


def test_manual_event_rejects_stale_missing_disabled_and_wrong_type_nodes(manual_trigger):
    service, _app, client = manual_trigger
    stale = client.post("/api/workflow-editor/nodes/alpha/trigger", json=_request(service, expected_revision="stale"))
    assert stale.status_code == 409
    missing_revision = _request(service)
    missing_revision.pop("expected_revision")
    assert client.post("/api/workflow-editor/nodes/alpha/trigger", json=missing_revision).status_code == 428
    for node_id in ("missing", "schedule", "store-alpha"):
        assert client.post(f"/api/workflow-editor/nodes/{node_id}/trigger", json=_request(service)).status_code == 400
    document = service.workflow_config_admin.read_workflow_editor_document()
    document.config["triggers"][0]["config"]["enabled"] = False
    service.workflow_config_admin.save_workflow_editor_document(
        config=document.config, layout=document.layout,
        graphs=[graph.model_dump(mode="json") for graph in document.graphs],
        expected_revision=document.revision,
    )
    disabled = client.post("/api/workflow-editor/nodes/alpha/trigger", json=_request(service))
    assert disabled.status_code == 400
    assert "enabled" in disabled.json()["error"]
    assert _queue(service).list_pending() == []


def test_manual_event_checks_authoritative_directory_permission_before_queueing(manual_trigger, monkeypatch):
    service, app, client = manual_trigger
    document = service.workflow_config_admin.read_workflow_editor_document()
    document.graphs[0].directory_path = "root/ops"
    service.workflow_config_admin.save_workflow_editor_document(
        config=document.config, layout=document.layout,
        graphs=[graph.model_dump(mode="json") for graph in document.graphs],
    )
    checked = []

    def require_permission(_request, permission, *, directory_path):
        checked.append((permission, directory_path))
        if directory_path == "root/ops":
            raise PermissionError("Permission required: workflow_runs.control")

    monkeypatch.setattr(app.state.auth, "require_permission", require_permission)
    response = client.post("/api/workflow-editor/nodes/alpha/trigger", json=_request(service))
    assert response.status_code == 403
    assert checked == [("workflow_runs.control", "root/ops")]
    assert _queue(service).list_pending() == []


def test_manual_event_requires_authenticated_directory_controller(manual_trigger, monkeypatch):
    service, _app, _client = manual_trigger
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    app = create_app(service)
    users = app.state.admin_user_store
    users.upsert_user(login="viewer", display_name="Viewer")
    users.set_local_password(login="viewer", password="viewer-password")
    users.set_user_group_assignments(
        login="viewer", assignments=[{"group_id": "workflow-viewer", "directory_path": "root"}],
    )
    with TestClient(app) as client:
        unauthenticated = client.post("/api/workflow-editor/nodes/alpha/trigger", json=_request(service))
        assert unauthenticated.status_code == 401
        assert client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-password"}).status_code == 200
        denied = client.post("/api/workflow-editor/nodes/alpha/trigger", json=_request(service))
        assert denied.status_code == 403
        assert _queue(service).list_pending() == []
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"}).status_code == 200
        assert client.post("/api/workflow-editor/nodes/alpha/trigger", json=_request(service)).status_code == 202


def test_manual_event_uses_selected_active_pack_revision(manual_trigger):
    service, _app, client = manual_trigger
    document = service.workflow_config_admin.read_workflow_editor_document()
    reference = "manual.v1.json"
    pack_path = service.project_dir / "config" / "workflow-packs" / reference
    pack_path.parent.mkdir(parents=True, exist_ok=True)
    pack_path.write_text(json.dumps({
        "pack_id": "manual", "pack_version": "v1",
        "graphs": [graph.model_dump(mode="json") for graph in document.graphs],
        "ir": {
            "version": "v1",
            "nodes": [{"node": node} for key in ("triggers", "effects") for node in document.config[key]],
            "edges": document.config["edges"],
        },
    }))
    service.workflow_config_admin.write_worker_global_config("trigger-orchestrator", {
        "runtime_dir": str(service.config.runtime_dir), "workflow_packs": [reference],
    })
    selected = service.workflow_config_admin.read_workflow_editor_document(workflow_pack=reference)
    response = client.post("/api/workflow-editor/nodes/alpha/trigger", json=_request(
        service, workflow_pack=reference, expected_revision=selected.revision,
    ))
    assert response.status_code == 202, response.text
    assert _queue(service).list_pending()[0].trigger_id == "alpha"
    service.workflow_config_admin.write_worker_global_config("trigger-orchestrator", {
        "runtime_dir": str(service.config.runtime_dir), "workflow_packs": [],
    })
    inactive = client.post("/api/workflow-editor/nodes/alpha/trigger", json={
        "workflow_pack": reference, "expected_revision": selected.revision,
        "payload": {}, "request_id": "inactive-request",
    })
    assert inactive.status_code == 400
    assert len(_queue(service).list_pending()) == 1
