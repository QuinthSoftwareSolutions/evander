from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

from config_helpers import canonical_worker_config, gate_execution_payload, trigger_queue_payload
from test_classification_integration import environment as environment, worker_for
from evander.admin.web.app import create_app
from evander.events.models import TriggerEvent
from evander.gates.registry import GateProviderFactory
from evander.model_usage.runtime import model_usage_store


CONFIG = {
    "state": "${{tick.message}}",
    "instructions": "Choose a team",
    "cases": [{"port": "support", "description": "Issues"}],
    "default_port": "sales",
    "default_description": "Purchases",
}


def workflow(admin, **patch):
    return canonical_worker_config(
        {
            "runtime_dir": admin.config.runtime_dir,
            "triggers": [
                {
                    "id": "tick",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"message": {"type": "string"}},
                }
            ],
            "gates": [
                {
                    "id": "smart",
                    "display_name": "Smart switch",
                    "type": "gate:classifier:switch",
                    **CONFIG,
                    **patch,
                },
                *[
                    {
                        "id": name,
                        "display_name": name,
                        "type": "gate:map",
                        "template": {"arrived": name},
                    }
                    for name in ["support", "sales", "error", "fast"]
                ],
            ],
            "edges": [
                {"source_id": "tick", "target_id": "smart"},
                {"source_id": "tick", "target_id": "fast"},
                *[
                    {"source_id": "smart", "source_port": p, "target_id": p}
                    for p in ["support", "sales", "error"]
                ],
            ],
            "workflow_graphs": [
                {
                    "id": "graph",
                    "name": "Smart switch",
                    "directory_path": "root/team",
                    "node_ids": ["tick", "smart", "support", "sales", "error", "fast"],
                }
            ],
        }
    )


def worker(admin, config, *, pending=False):
    instance = worker_for(admin, config, pending=pending)
    instance.gate_provider_factory = GateProviderFactory(admin.runtime.provider_config)
    return instance


def publish(instance, *, extra_payload=None):
    instance.event_queue.publish(
        TriggerEvent(
            event_id="input",
            trigger_id="tick",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(UTC),
            payload={
                "message": "Broken export",
                "directory_path": "root/other",
                **(extra_payload or {}),
            },
        )
    )


def records(config):
    return {r["gate_id"]: r for r in gate_execution_payload(config).values()}


@pytest.fixture
def setup(environment, monkeypatch):
    admin, configs, calls = environment
    monkeypatch.setattr(
        "evander.gates.providers.classifier_switch.service_for_runtime",
        lambda _: admin.classification_admin.service,
    )
    return admin, configs, calls


@pytest.mark.parametrize("cutoff,port", [(None, "support"), (0.85, "support"), (0.9, "sales")])
def test_end_to_end_routing_payload_and_attribution(setup, cutoff, port):
    admin, _, calls = setup
    config = workflow(admin, min_probability=cutoff)
    instance = worker(admin, config)
    publish(instance)
    for _ in range(4):
        instance.run_once()
    result = records(config)
    assert result["smart"]["status"] == "succeeded", result["smart"]
    assert result["smart"]["completion_event_branch_port"] == port
    assert result["smart"]["outputs"] == {
        "message": "Broken export",
        "directory_path": "root/other",
    }
    assert set(result) == {"smart", "fast", port}
    assert result["smart"]["resolved_config"]["decision"]["selected_choice"] == "support"
    assert len(calls) == 1
    usage = model_usage_store(admin.config.runtime_dir).list_events()[0]
    assert usage.directory_path == "root/team" and usage.node_id == "smart"
    assert usage.workflow_run_id and usage.session_id is None
    worker(admin, config).run_once()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "category,expected", [("  Routing  ", "Routing"), ("${{tick.category}}", "Reference")]
)
def test_switch_category_reaches_usage_without_reaching_provider_input(setup, category, expected):
    admin, _, calls = setup
    config = workflow(admin, cost_category=category)
    instance = worker(admin, config)
    publish(instance, extra_payload={"category": "Reference"})
    for _ in range(4):
        instance.run_once()
    result = records(config)["smart"]
    assert result["status"] == "succeeded"
    assert result["completion_event_branch_port"] == "support"
    usage = model_usage_store(admin.config.runtime_dir).list_events()[0]
    assert usage.cost_category == expected
    assert usage.directory_path == "root/team" and usage.node_id == "smart"
    assert usage.billing_kind == "api" and usage.cost_attribution_recorded
    assert usage.input_tokens == 10 and usage.output_tokens == 2
    assert usage.session_id is None
    assert "cost_category" not in calls[0] and calls[0]["state"] == "Broken export"


@pytest.mark.parametrize(
    "override,expected",
    [({}, "Parent"), ({"cost_category": None}, "Parent"), ({"cost_category": ""}, None)],
)
def test_switch_category_inheritance_and_explicit_uncategorized(setup, override, expected):
    from evander.classification.switch import PreparedSwitch, execute_switch, prepare_switch

    admin, _, calls = setup
    service = admin.classification_admin.service
    settings = admin.runtime.provider_config.resolve_for_directory("root/team").classification
    prepared = prepare_switch(service, settings, {**CONFIG, "state": "Broken export", **override})
    snapshot = prepared.model_dump(mode="json")
    if not override:
        # Durable snapshots recorded before this field existed remain valid.
        snapshot["config"].pop("cost_category")
    attribution = {"cost_category": "Parent", "directory_path": "root/team", "node_id": "smart"}
    result = execute_switch(
        service, settings, PreparedSwitch.model_validate(snapshot), attribution=attribution
    )
    assert result["selected_port"] == "support"
    assert model_usage_store(admin.config.runtime_dir).list_events()[0].cost_category == expected
    assert attribution["cost_category"] == "Parent"
    assert len(calls) == 1


def test_pending_call_does_not_block_other_gates_and_restart_pins_definition(setup):
    admin, configs, calls = setup
    config = workflow(admin, cost_category="Routing")
    first = worker(admin, config, pending=True)
    first._executor.submit = Mock(wraps=first._executor.submit)
    publish(first)
    first.run_once()
    first.run_once()
    result = records(config)
    assert result["smart"]["status"] == "running" and result["fast"]["status"] == "succeeded"
    assert len(first._inflight) == 1 and calls == []
    assert first._executor.submit.call_count == 1
    saved = result["smart"]["resolved_config"]
    assert saved["prepared"]["evaluation"]["state"] == "Broken export"
    configs.write(
        "root/team",
        domain="providers",
        payload={
            "classification": {
                "providers": {"jev": {"default_model": "changed-model"}},
            }
        },
    )
    config.gates[0].config["instructions"] = "New instructions"
    config.gates[0].config["cost_category"] = "Changed category"
    config.gates[0].config["min_probability"] = 1.0
    config.workflow_graphs[0].directory_path = "root/other"
    configs.write("root/other", domain="providers", payload={"classification": {"enabled": False}})
    restarted = worker(admin, config)
    for _ in range(4):
        restarted.run_once()
    result = records(config)["smart"]
    assert result["status"] == "succeeded", result
    assert result["completion_event_branch_port"] == "support"
    assert calls[0]["model"] == "jev-1.13.0"
    assert calls[0]["questions"]["route"]["instructions"] == "Choose a team"
    assert model_usage_store(admin.config.runtime_dir).list_events()[0].cost_category == "Routing"
    assert len(calls) == 1


@pytest.mark.parametrize("pending", [False, True])
def test_disabled_directory_uses_error_branch_without_provider_call(setup, pending):
    admin, configs, calls = setup
    config = workflow(admin)
    instance = worker(admin, config, pending=pending)
    publish(instance)
    if pending:
        instance.run_once()
        assert records(config)["smart"]["status"] == "running"
    configs.write("root/team", domain="providers", payload={"classification": {"enabled": False}})
    if pending:
        instance = worker(admin, config)
    for _ in range(3):
        instance.run_once()
    result = records(config)
    assert result["smart"]["status"] == "error_routed", result["smart"]
    assert set(result) == {"smart", "fast", "error"}
    assert calls == []


def test_pending_publication_reuses_decision_after_restart(setup, monkeypatch):
    admin, _, calls = setup
    config = workflow(admin)
    first = worker(admin, config)
    publish(first)
    original = first.event_queue.publish

    def failing_publish(event, **kwargs):
        if event.trigger_id == "smart":
            raise RuntimeError("publication unavailable")
        return original(event, **kwargs)

    first.event_queue.supports_atomic_publication = False
    monkeypatch.setattr(first.event_queue, "publish", failing_publish)
    first.run_once()
    assert records(config)["smart"]["status"] == "completion_pending"
    assert len(calls) == 1
    restarted = worker(admin, config)
    for _ in range(4):
        restarted.run_once()
    result = records(config)
    assert result["smart"]["status"] == "succeeded" and "support" in result
    assert len(calls) == 1
    completions = [e for e in trigger_queue_payload(config).values() if e["trigger_id"] == "smart"]
    assert len(completions) == 1


def test_busy_atomic_gate_completion_reuses_finished_classifier_result(setup, monkeypatch):
    admin, _, calls = setup
    config = workflow(admin)
    instance = worker(admin, config)
    original = instance.gate_execution_store.record_completion_and_publish
    blocked = [True]

    def publish_completion(**kwargs):
        if kwargs["record"].gate_id == "smart" and blocked[0]:
            raise sqlite3.OperationalError("database is locked")
        return original(**kwargs)

    monkeypatch.setattr(instance.gate_execution_store, "record_completion_and_publish", publish_completion)
    publish(instance)
    instance.run_once()
    assert len(calls) == 1
    assert records(config)["smart"]["status"] == "running"
    assert "input::smart" in instance._inflight
    blocked[0] = False
    instance._completion_storage_retry_at = 0
    for retry in instance._completion_retries.values():
        retry.retry_at = 0
    for _ in range(4):
        instance.run_once()
    result = records(config)
    assert result["smart"]["status"] == "succeeded" and "support" in result
    assert len(calls) == 1
    completions = [e for e in trigger_queue_payload(config).values() if e["trigger_id"] == "smart"]
    assert len(completions) == 1


def test_sample_api_permissions_and_routing(setup):
    admin, configs, calls = setup
    url = "/api/classification/switch/evaluate?directory_path=root/team"
    payload = {"config": {**CONFIG, "state": "Private sample", "min_probability": 0.9}}
    with TestClient(create_app(admin)) as client:
        assert client.post(url, json=payload).status_code == 403
        client.post("/api/admin/privileged/unlock")
        response = client.post(url, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["selected_port"] == "sales"
        assert response.json()["routing_reason"] == "below_probability"
        assert response.headers["cache-control"] == "no-store"
        assert (
            client.post(
                url, json={"config": {**payload["config"], "default_port": "support"}}
            ).status_code
            == 400
        )
        configs.write(
            "root/other", domain="providers", payload={"classification": {"enabled": False}}
        )
        assert client.post(url.replace("root/team", "root/other"), json=payload).status_code == 400
        assert len(calls) == 1
        assert "Private sample" not in admin.config.audit_log_path.read_text()


@pytest.mark.parametrize("error_connected", [False, True])
def test_failed_async_call_uses_failure_contract_and_never_default(
    setup, monkeypatch, error_connected
):
    from evander.classification.models import ClassificationError

    admin, _, calls = setup
    config = workflow(admin)
    if not error_connected:
        config.edges = [edge for edge in config.edges if edge.source_port != "error"]

    def fail(*args):
        raise ClassificationError("Jev returned HTTP 429")

    monkeypatch.setattr(
        "evander.gates.providers.classifier_switch.ClassifierSwitchGateProvider.evaluate_prepared",
        fail,
    )
    instance = worker(admin, config)
    publish(instance)
    for _ in range(3):
        instance.run_once()
    result = records(config)
    record = result["smart"]
    assert record["status"] == ("error_routed" if error_connected else "failed")
    assert record["outputs"] == {
        "success": False,
        "error_message": "Jev returned HTTP 429",
        "error_class": "ClassificationError",
    }
    assert "support" not in result and "sales" not in result and calls == []
    assert trigger_queue_payload(config)["input"]["delivery_state"] == "delivered"
