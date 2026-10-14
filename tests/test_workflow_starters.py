from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from starlette.applications import Starlette

from config_helpers import write_runtime_config
from evander.addons import AddonRegistration, LoadedAddonCatalog
from evander.admin.composition import build_admin_runtime
from evander.admin.payloads import AdminRevisionConflict
from evander.admin.services.integration_admin_service import IntegrationAdminService
from evander.admin.web.workflow_starters import workflow_starter_routes
from evander.events.internal import InternalEventSource
from evander.integration_contracts import (
    AddonIntegration,
    IntegrationCheckResult,
    IntegrationReadinessCheck,
)
from evander.models import SessionManifest
from evander.daemons.effect_orchestrator import (
    EffectExecutionRecord,
    GateExecutionRecord,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from evander.events.models import TriggerEvent
from evander.triggers.queue import SqliteTriggerEventQueue


@pytest.fixture
def admin(tmp_path):
    service = build_admin_runtime(write_runtime_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant",
            vm_id="vm-assistant",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    return service


def inputs(**overrides):
    return {
        "name": "daily-check",
        "session_id": "assistant",
        "schedule": "weekdays",
        "time": "09:30",
        "timezone": "Europe/Stockholm",
        "prompt": "Report on the project's open tasks.",
        **overrides,
    }


def activate(admin, **overrides):
    values = inputs(**overrides)
    preview = admin.workflow_starter_admin.preview("core/scheduled-task", "root", values)
    return admin.workflow_starter_admin.activate(
        "core/scheduled-task", "root", values, preview_revision=preview["preview_revision"]
    )


def test_preview_is_inert_and_activation_installs_an_editable_scoped_workflow(admin):
    service = admin.workflow_starter_admin
    preview = service.preview("core/scheduled-task", "root", inputs())
    assert preview["ready"]
    assert not (admin.project_dir / "config/workflow-packs/starter-daily-check.v1.json").exists()
    assert not (admin.runtime_dir / "workflow-starter-instances.json").exists()
    receipt = activate(admin)
    document = admin.workflow_config_admin.read_workflow_editor_document(
        workflow_pack=receipt["workflow_pack"]
    )
    assert document.graphs[0].directory_path == "root"
    schedule = next(node for node in document.config["triggers"] if node["type"] == "trigger:cron")
    assert schedule["config"]["expression"] == "30 9 * * 1-5"
    assert schedule["config"]["timezone"] == "Europe/Stockholm"
    assert admin.runtime.session_repository.get_session("assistant").allowed_tools == []
    assert any(edge.get("source_port") == "completed" for edge in document.config["edges"])
    assert admin.workflow_starter_admin.catalog("root")["instances"][0]["id"] == receipt["id"]


def test_preview_rejects_stale_workflows_and_duplicate_name_without_overwrite(admin):
    preview = admin.workflow_starter_admin.preview("core/scheduled-task", "root", inputs())
    activate(admin, name="another-task")
    with pytest.raises(AdminRevisionConflict):
        admin.workflow_starter_admin.activate(
            "core/scheduled-task", "root", inputs(), preview_revision=preview["preview_revision"]
        )
    receipt = activate(admin)
    path = admin.project_dir / "config/workflow-packs" / receipt["workflow_pack"]
    original = path.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        admin.workflow_starter_admin.preview("core/scheduled-task", "root", inputs())
    assert path.read_bytes() == original


def test_activation_retry_preserves_subsequent_operator_customization(admin):
    preview = admin.workflow_starter_admin.preview("core/scheduled-task", "root", inputs())
    receipt = activate(admin)
    path = admin.project_dir / "config/workflow-packs" / receipt["workflow_pack"]
    payload = json.loads(path.read_text())
    task = next(
        node["node"]
        for node in payload["ir"]["nodes"]
        if node["node"]["type"] == "effect:agent:enqueue-prompt"
    )
    task["config"]["prompt"] = "Customized by the operator"
    path.write_text(json.dumps(payload))
    retried = admin.workflow_starter_admin.activate(
        "core/scheduled-task", "root", inputs(), preview_revision=preview["preview_revision"]
    )
    assert retried["id"] == receipt["id"]
    assert "Customized by the operator" in path.read_text()
    assert admin.workflow_starter_admin.verification(receipt["id"], "root")["state"] == "changed"


def test_starter_name_cannot_collide_with_another_pack_version(admin):
    receipt = activate(admin)
    document = admin.workflow_config_admin.read_workflow_editor_document(
        workflow_pack=receipt["workflow_pack"]
    )
    admin.workflow_config_admin.update_workflow_pack_metadata(
        workflow_pack=receipt["workflow_pack"],
        pack_id="starter-daily-check",
        pack_version="v2",
        expected_revision=document.selected_workflow_pack.revision,
    )
    with pytest.raises(ValueError, match="already exists"):
        admin.workflow_starter_admin.preview("core/scheduled-task", "root", inputs())


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": "missing"},
        {"timezone": "Unknown/Place"},
        {"name": "../unsafe"},
        {"time": "99:00"},
        {"prompt": "${{other.secret}}"},
    ],
)
def test_invalid_starter_inputs_do_not_install(admin, changes):
    with pytest.raises(ValueError):
        admin.workflow_starter_admin.preview("core/scheduled-task", "root", inputs(**changes))
    assert not (admin.runtime_dir / "workflow-starter-instances.json").exists()


def test_cross_directory_and_expired_sessions_are_rejected(admin):
    session = admin.runtime.session_repository.get_session("assistant")
    admin.runtime.session_repository.upsert_session(
        session.model_copy(update={"directory_path": "root/other"})
    )
    with pytest.raises(ValueError, match="selected directory"):
        admin.workflow_starter_admin.preview("core/scheduled-task", "root", inputs())
    admin.runtime.session_repository.upsert_session(
        session.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(hours=1)})
    )
    with pytest.raises(ValueError, match="active agent"):
        admin.workflow_starter_admin.preview("core/scheduled-task", "root", inputs())


def test_manual_verification_uses_real_queue_and_retries_do_not_duplicate(admin):
    receipt = activate(admin)
    args = dict(
        request_id="one-attempt",
        source=InternalEventSource(user_id="operator"),
        authorize_directory=lambda _: None,
    )
    first = admin.workflow_starter_admin.run_test(receipt["id"], "root", **args)
    second = admin.workflow_starter_admin.run_test(receipt["id"], "root", **args)
    assert second == first
    state = admin.workflow_starter_admin.verification(
        receipt["id"], "root", event_id=first["event_id"]
    )
    assert state["state"] == "running"


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "unrelated", "foreign_trace"])
def test_verification_follows_persisted_gate_and_effect_continuations(admin, outcome):
    receipt = activate(admin)
    started = admin.workflow_starter_admin.run_test(
        receipt["id"],
        "root",
        request_id="chain-test",
        source=InternalEventSource(user_id="operator"),
        authorize_directory=lambda _: None,
    )
    stores = admin.workflow_config_admin.workflow_run_store_paths()
    queue = SqliteTriggerEventQueue(stores["orchestration_store_path"])
    gate_store = SqliteGateExecutionStore(stores["orchestration_store_path"])
    effects = SqliteExecutionStore(stores["orchestration_store_path"])
    now = datetime.now(UTC)
    common = dict(
        trace_id=started["trace_id"],
        started_at=now,
        finished_at=now,
        resolved_config_hash="fixture-config",
    )
    gate_store.record_execution(
        GateExecutionRecord(
            **common,
            execution_id="merge-execution",
            idempotency_key="merge-execution",
            event_id=started["event_id"],
            source_node_id="starter-daily-check-test",
            source_trigger_type="trigger:event",
            gate_id="starter-daily-check-start",
            gate_type="gate:merge",
            status="succeeded",
            published_event_id="merge-completed",
        )
    )
    queue.publish(
        TriggerEvent(
            event_id="merge-completed",
            trigger_id="starter-daily-check-start",
            trigger_type="gate:merge",
            occurred_at=now,
            trace_id=started["trace_id"],
            payload={},
        )
    )
    effects.record_execution(
        EffectExecutionRecord(
            **common,
            execution_id="task-execution",
            idempotency_key="task-execution",
            event_id="merge-completed",
            source_node_id="starter-daily-check-start",
            source_trigger_type="gate:merge",
            effect_id="starter-daily-check-task",
            effect_type="effect:agent:enqueue-prompt",
            status="failed" if outcome == "failed" else "succeeded",
            error="Task failed" if outcome == "failed" else None,
            published_event_id="task-completed"
            if outcome in {"succeeded", "foreign_trace"}
            else None,
        )
    )
    queue.publish(
        TriggerEvent(
            event_id="task-completed",
            trigger_id="starter-daily-check-task",
            trigger_type="effect:agent:enqueue-prompt",
            occurred_at=now,
            trace_id="another-trace" if outcome == "foreign_trace" else started["trace_id"],
            payload={},
        )
    )
    effects.record_execution(
        EffectExecutionRecord(
            **{
                **common,
                "trace_id": "another-trace" if outcome == "foreign_trace" else started["trace_id"],
            },
            execution_id="marker-execution",
            idempotency_key="marker-execution",
            event_id="task-completed",
            source_node_id="starter-daily-check-task",
            source_trigger_type="effect:agent:enqueue-prompt",
            effect_id="starter-daily-check-completed",
            effect_type="effect:event:emit",
            status="succeeded",
        )
    )
    origin = admin.workflow_config_admin.read_workflow_run_detail(event_id=started["event_id"])
    assert all(node.node_id != "starter-daily-check-completed" for node in origin.nodes)
    result = admin.workflow_starter_admin.verification(
        receipt["id"], "root", event_id=started["event_id"]
    )
    assert (
        result["state"]
        == {
            "succeeded": "verified",
            "failed": "failed",
            "unrelated": "running",
            "foreign_trace": "needs_attention",
        }[outcome]
    )
    assert result["event_id"] == started["event_id"]


def test_verification_rejects_an_unrelated_originating_trigger(admin):
    receipt = activate(admin)
    stores = admin.workflow_config_admin.workflow_run_store_paths()
    queue = SqliteTriggerEventQueue(stores["orchestration_store_path"])
    queue.publish(
        TriggerEvent(
            event_id="unrelated-run",
            trigger_id="unrelated-trigger",
            trigger_type="trigger:event",
            occurred_at=datetime.now(UTC),
            payload={},
        )
    )
    with pytest.raises(ValueError, match="does not belong"):
        admin.workflow_starter_admin.verification(receipt["id"], "root", event_id="unrelated-run")


def test_unconfigured_addon_starter_exposes_setup_check_without_resolving_credentials(admin):
    from evander.workflow_starters import SCHEDULED_STARTER

    addon = AddonRegistration(
        "synthetic",
        "Synthetic integration",
        integration=AddonIntegration(
            workflow_starters=(SCHEDULED_STARTER,),
            readiness_checks=(
                IntegrationReadinessCheck(
                    "connection",
                    "Connection required",
                    lambda _: IntegrationCheckResult("blocked", "connection_missing"),
                ),
            ),
        ),
    )
    admin.workflow_starter_admin.integrations = IntegrationAdminService(
        catalog=LoadedAddonCatalog((addon,), ()),
        project_dir=admin.project_dir,
        runtime_dir=admin.runtime_dir,
        config=admin.config,
        services=admin.runtime,
        resolve_directory=lambda value: value,
    )
    preview = admin.workflow_starter_admin.preview("synthetic/scheduled-task", "root", inputs())
    assert not preview["ready"]
    assert preview["checks"][0]["code"] == "connection_missing"


def test_routes_enforce_write_run_and_global_pack_permissions(admin, monkeypatch):
    import evander.admin.web.workflow_starters as routes

    class Auth:
        denied = None

        def require(self, request):
            return {"login": "operator"}

        def require_permission(self, request, permission, *, directory_path):
            if permission == self.denied:
                raise PermissionError("denied")

    auth = Auth()
    monkeypatch.setattr(routes, "request_has_privileged_operations", lambda **_: True)
    app = Starlette(routes=workflow_starter_routes(admin, auth))

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            body = {
                "starter_id": "core/scheduled-task",
                "directory_path": "root",
                "values": inputs(),
            }
            preview = (await client.post("/api/admin/workflow-starters/preview", json=body)).json()
            auth.denied = "workflow_packs.manage"
            assert (
                await client.post(
                    "/api/admin/workflow-starters/activate",
                    json={**body, "preview_revision": preview["preview_revision"]},
                )
            ).status_code == 403
            auth.denied = None
            assert (
                await client.post("/api/admin/workflow-starters/activate", json=body)
            ).status_code == 428
            receipt = (
                await client.post(
                    "/api/admin/workflow-starters/activate",
                    json={**body, "preview_revision": preview["preview_revision"]},
                )
            ).json()
            auth.denied = "workflow_runs.control"
            assert (
                await client.post(
                    f"/api/admin/workflow-starters/{receipt['id']}/test",
                    json={"request_id": "test"},
                )
            ).status_code == 403
            auth.denied = "workflow_runs.read"
            assert (
                await client.get(f"/api/admin/workflow-starters/{receipt['id']}/verify")
            ).status_code == 403

    asyncio.run(run())
