from __future__ import annotations

import asyncio
import copy
import json
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from config_helpers import canonical_worker_config, effect_execution_payload, write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.admin.services.classification_admin_service import ClassificationRevisionConflict
from evander.admin.web.app import create_app
from evander.classification.jev import JevProvider
from evander.classification.service import ClassificationService
from evander.classification.models import ClassificationError
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.directories import DirectoryService, DirectoryStore
from evander.directory_config import DirectoryConfigStore
from evander.events.models import TriggerEvent
from evander.gateway import create_server
from evander.model_usage.models import ModelUsageCorrelationContext
from evander.model_usage.runtime import model_usage_store
from evander.models import SessionManifest, ToolName
from evander.queries.providers.schemas import ClassificationEvaluateQueryDefinition
from evander.queries.registry import QueryProviderFactory


QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": "Choose a team",
        "criteria": {"support": "Issues", "sales": "Purchases"},
    }
}
CONFIG = {"enabled": True, "profiles": {"triage": {"questions": QUESTIONS}}}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    runtime_dir = write_runtime_config(
        tmp_path, enable_directory_scoped_config=True, classification=CONFIG
    )
    admin = build_admin_runtime(runtime_dir)
    store = DirectoryStore(admin.config.directories_store_path)
    directories = DirectoryService(store)
    directories.create(parent="root", name="team")
    directories.create(parent="root", name="other")
    configs = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "usage": {"input_tokens": 10, "output_tokens": 2},
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": "support",
                        "confidence": 0.5,
                        "probabilities": {"support": 0.85, "sales": 0.15},
                    }
                },
            },
        )

    classification = ClassificationService(
        runtime_dir,
        providers={"jev": JevProvider(transport=httpx.MockTransport(handler))},
        secret_resolver=lambda _: "fake-classification-key",
    )
    admin.classification_admin.service = classification
    monkeypatch.setattr(
        "evander.gateway.dependencies.service_for_runtime", lambda _: classification
    )
    monkeypatch.setattr(
        "evander.queries.providers.classification_evaluate.service_for_runtime",
        lambda _: classification,
    )
    return admin, configs, calls


def test_profile_inheritance_is_atomic_and_disable_revert_do_not_resurrect(environment):
    admin, configs, _ = environment
    child_profile = {
        "questions": {
            "route": {**QUESTIONS["route"], "criteria": {"sales": "Buying", "finance": "Invoices"}}
        }
    }
    configs.write(
        "root/team",
        domain="providers",
        payload={"classification": {"profiles": {"triage": child_profile}}},
    )
    settings = admin.runtime.provider_config.resolve_for_directory("root/team").classification
    assert set(settings.profiles["triage"].questions["route"].criteria) == {"sales", "finance"}
    configs.write(
        "root/team",
        domain="providers",
        payload={"classification": {"profiles": {"triage": {"enabled": False}}}},
    )
    settings = admin.runtime.provider_config.resolve_for_directory("root/team").classification
    assert admin.classification_admin.service.list_profiles(settings) == {"profiles": []}
    configs.write("root/team", domain="providers", payload={})
    assert (
        admin.runtime.provider_config.resolve_for_directory("root/team")
        .classification.profiles["triage"]
        .enabled
    )
    # The config loaded at boot contains the old root definition; removing it must win.
    configs.write("root", domain="providers", payload={})
    settings = admin.runtime.provider_config.resolve_for_directory("root/team").classification
    assert settings.enabled is False and settings.profiles == {}


def test_save_detects_masked_parent_edit_and_rejects_malformed_drafts(environment):
    admin, configs, _ = environment
    configs.write("root/team", domain="providers", payload={"classification": CONFIG})
    before = admin.classification_admin.snapshot("root/team")
    updated = copy.deepcopy(CONFIG)
    updated["profiles"]["triage"]["label"] = "Changed in parent"
    configs.write("root", domain="providers", payload={"classification": updated})
    after = admin.classification_admin.snapshot("root/team")
    assert before["effective_config"] == after["effective_config"]
    assert before["revision"] != after["revision"]
    with pytest.raises(ClassificationRevisionConflict):
        admin.classification_admin.save(
            "root/team", expected_revision=before["revision"], config_payload={}
        )
    with pytest.raises(ClassificationError, match="Invalid classification"):
        admin.classification_admin.save(
            "root/team", expected_revision=after["revision"], config_payload={"profiles": []}
        )
    assert admin.classification_admin.snapshot("root/team")["revision"] == after["revision"]


def test_disabled_workflow_uses_normal_failure_record_without_calling_provider(environment):
    admin, configs, calls = environment
    configs.write("root/team", domain="providers", payload={"classification": {"enabled": False}})
    worker_config = canonical_worker_config(
        {
            "runtime_dir": admin.config.runtime_dir,
            "triggers": [
                {
                    "id": "tick",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "queries": [
                {
                    "id": "classify",
                    "display_name": "Classify",
                    "type": "query:classification:evaluate",
                    "state": "sample",
                    "profile_id": "triage",
                }
            ],
            "edges": [{"source_id": "tick", "target_id": "classify"}],
            "workflow_graphs": [
                {
                    "id": "graph",
                    "name": "Classification",
                    "directory_path": "root/team",
                    "node_ids": ["tick", "classify"],
                }
            ],
        }
    )
    worker = worker_for(admin, worker_config)
    worker.event_queue.publish(
        TriggerEvent(
            event_id="disabled",
            trigger_id="tick",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(UTC),
            payload={},
        )
    )
    for _ in range(3):
        worker.run_once()
    record = next(iter(effect_execution_payload(worker_config).values()))
    assert record["status"] == "failed" and "disabled" in record["error"]
    assert calls == []


def test_admin_revision_lock_and_draft_sample_evaluation(environment):
    admin, configs, calls = environment
    with TestClient(create_app(admin)) as client:
        url = "/api/classification?directory_path=root/team"
        initial = client.get(url)
        assert initial.status_code == 200, initial.text
        assert initial.headers["cache-control"] == "no-store"
        assert "fake-classification-key" not in initial.text
        revision = initial.json()["revision"]
        payload = {"expected_revision": revision, "config": {"enabled": False}}
        assert client.put(url, json=payload).status_code == 403
        client.post("/api/admin/privileged/unlock")
        # A parent edit also invalidates the revision.
        updated = copy.deepcopy(CONFIG)
        updated["profiles"]["triage"]["label"] = "New label"
        configs.write("root", domain="providers", payload={"classification": updated})
        assert client.put(url, json=payload).status_code == 409
        payload["expected_revision"] = client.get(url).json()["revision"]
        saved = client.put(url, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["effective_config"]["enabled"] is False
        assert client.put(url, json=payload).status_code == 409
        assert client.put(url, json={"config": {}}).status_code == 428
        # Revert local overrides, then evaluate draft questions without persisting them.
        restored = client.put(
            url, json={"expected_revision": saved.json()["revision"], "config": {}}
        )
        assert restored.status_code == 200, restored.text
        sample = client.post(
            "/api/classification/evaluate?directory_path=root/team",
            json={"state": "private sample input", "questions": QUESTIONS},
        )
        assert sample.status_code == 200, sample.text
        assert sample.json()["answers"]["route"]["choice"] == "support"
        assert client.get(url).json()["revision"] == restored.json()["revision"]
        assert len(calls) == 1
        assert "private sample input" not in admin.config.audit_log_path.read_text()


def call_tool(server, name, arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    return result[1] if isinstance(result, tuple) else result


def test_agent_grants_directory_scope_and_usage_correlation(environment):
    admin, configs, calls = environment
    runtime = admin.runtime
    now = datetime.now(UTC)
    for session_id, scope, tools in [
        (
            "agent",
            "root/team",
            [ToolName.CLASSIFICATION_EVALUATE, ToolName.CLASSIFICATION_LIST_PROFILES],
        ),
        ("denied", "root/team", []),
        ("other", "root/other", [ToolName.CLASSIFICATION_EVALUATE]),
    ]:
        runtime.session_repository.upsert_session(
            SessionManifest(
                session_id=session_id,
                directory_path=scope,
                allowed_tools=tools,
                expires_at=now + timedelta(hours=1),
            )
        )
    configs.write(
        "root/other",
        domain="providers",
        payload={"classification": {"profiles": {"triage": {"enabled": False}}}},
    )
    usage = model_usage_store(admin.config.runtime_dir)
    usage.upsert_correlation_context(
        ModelUsageCorrelationContext(
            session_id="agent",
            dispatch_id="dispatch",
            workflow_run_id="run",
            agent_harness="codex",
            expires_at=now + timedelta(hours=1),
        )
    )
    server = create_server(runtime)
    listed = call_tool(server, "classification_list_profiles", {"session_id": "agent"})
    assert listed["profiles"][0]["id"] == "triage"
    result = call_tool(
        server,
        "classification_evaluate",
        {"session_id": "agent", "profile_id": "triage", "state": "private agent input"},
    )
    assert result["answers"]["route"]["choice"] == "support"
    for arguments in [
        {"session_id": "denied", "profile_id": "triage", "state": "input"},
        {"session_id": "other", "profile_id": "triage", "state": "input"},
        {"session_id": "agent", "profile_id": "triage", "model": "override", "state": "input"},
    ]:
        with pytest.raises(ToolError):
            call_tool(server, "classification_evaluate", arguments)
    assert len(calls) == 1
    event = usage.list_events()[0]
    assert event.directory_path == "root/team" and event.session_id == "agent"
    assert event.dispatch_id == "dispatch" and event.workflow_run_id == "run"
    assert "private agent input" not in admin.config.audit_log_path.read_text()


class ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future


class PendingExecutor:
    def submit(self, *args, **kwargs):
        return Future()


def worker_for(admin, worker_config, *, pending=False):
    worker = EffectOrchestratorWorker(
        config=admin.config, worker_config=worker_config, runtime=None
    )
    worker._executor = PendingExecutor() if pending else ImmediateExecutor()
    worker.query_provider_factory = QueryProviderFactory(admin.runtime.provider_config)
    return worker


def test_workflow_restart_uses_snapshot_and_reuses_completed_answers(environment):
    admin, configs, calls = environment
    worker_config = canonical_worker_config(
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
            "queries": [
                {
                    "id": "classify",
                    "display_name": "Classify",
                    "type": "query:classification:evaluate",
                    "state": "${{tick.message}}",
                    "profile_id": "triage",
                }
            ],
            "edges": [{"source_id": "tick", "target_id": "classify"}],
            "workflow_graphs": [
                {
                    "id": "graph",
                    "name": "Classification",
                    "directory_path": "root/team",
                    "node_ids": ["tick", "classify"],
                }
            ],
        }
    )
    first = worker_for(admin, worker_config, pending=True)
    first.event_queue.publish(
        TriggerEvent(
            event_id="event",
            trigger_id="tick",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(UTC),
            payload={"message": "Broken export"},
        )
    )
    first.run_once()
    record = next(iter(effect_execution_payload(worker_config).values()))
    snapshot = record["resolved_config"]["_classification_snapshot"]
    assert record["status"] == "running" and calls == []
    assert snapshot["state"] == "Broken export" and snapshot["model"] == "jev-1.13.0"
    changed = copy.deepcopy(CONFIG)
    changed["profiles"]["triage"]["model"] = "new-model"
    changed["profiles"]["triage"]["questions"]["route"]["instructions"] = "New instructions"
    configs.write("root", domain="providers", payload={"classification": changed})
    restarted = worker_for(admin, worker_config)
    for _ in range(3):
        restarted.run_once()
    record = next(iter(effect_execution_payload(worker_config).values()))
    assert record["status"] == "succeeded", record.get("error")
    assert calls[0]["model"] == "jev-1.13.0"
    assert calls[0]["questions"]["route"]["instructions"] == "Choose a team"
    assert record["outputs"]["profile_revision"] == snapshot["profile_revision"]
    worker_for(admin, worker_config).run_once()
    assert len(calls) == 1
    usage = model_usage_store(admin.config.runtime_dir).list_events()[0]
    assert usage.directory_path == "root/team" and usage.node_id == "classify"
    assert usage.workflow_run_id and usage.session_id is None


@pytest.mark.parametrize(
    "questions",
    [
        {},
        {"bad": {"type": "choice", "instructions": "Question", "criteria": {"one": "Only choice"}}},
    ],
)
def test_workflow_static_validation_rejects_invalid_questions(questions):
    with pytest.raises(ValueError):
        ClassificationEvaluateQueryDefinition(
            id="classify",
            display_name="Classify",
            type="query:classification:evaluate",
            state="${{tick.message}}",
            questions=questions,
        )
