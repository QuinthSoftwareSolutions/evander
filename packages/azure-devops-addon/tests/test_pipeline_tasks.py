from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander_azure_devops_addon import pipeline_tasks as tasks
from evander_azure_devops_addon.registration import register_workflow_providers

PROJECT, PLAN, JOB, TIMELINE, TASK = [f"{n:08d}-1111-4111-8111-111111111111" for n in range(1, 6)]
REFERENCE = dict(
    pipeline_id=7,
    run_id=42,
    project_id=PROJECT,
    plan_id=PLAN,
    job_id=JOB,
    timeline_id=TIMELINE,
    task_id=TASK,
)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    state = dict(
        text="reasoning: Checking readiness\n",
        logs=[],
        feeds=[],
        events=[],
        failure=None,
        active=True,
        wrong=None,
        session=True,
        task_id=TASK,
    )
    config = GatewayConfig(
        runtime_dir=tmp_path,
        ado_organization="example",
        azure_devops_project="Project",
        azure_devops_pat_env="BUILD_PAT",
    )
    backend = SimpleNamespace(
        repository=SimpleNamespace(
            list_by_session=lambda _: [SimpleNamespace(execution_id="execution-1")]
        ),
        get_log_snapshot=lambda *a, **kw: state["text"],
    )
    admin = SimpleNamespace(
        runtime=SimpleNamespace(
            policy=SimpleNamespace(
                get_session=lambda _: (
                    SimpleNamespace(directory_path="root/test") if state["session"] else None
                )
            )
        ),
        vm_agent_admin=SimpleNamespace(container_worker_backend=lambda **kw: backend),
    )
    context = EffectRuntimeContext(
        resolved_config=config,
        provider_config_resolver=SimpleNamespace(resolve_for_directory=lambda _: config),
        admin_service=admin,
        env={"BUILD_PAT": "host-only-build-token", "TEST_PASSWORD": "very-secret-password"},
        metadata={"directory_path": "root/test", "graph_id": "test-run"},
    )
    original_client = httpx.Client

    def handle(request):
        assert request.url.host == "dev.azure.com"
        assert (
            request.headers["authorization"]
            == "Basic " + base64.b64encode(b":host-only-build-token").decode()
        )
        path = request.url.path
        if state["failure"] and state["failure"] in path:
            return httpx.Response(503, text="sensitive-provider-response host-only-build-token")
        if request.method == "GET" and path.endswith("/builds/42"):
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "status": "inProgress" if state["active"] else "completed",
                    "project": {"id": PROJECT},
                    "definition": {"id": state["wrong"] or 7},
                    "orchestrationPlan": {"planId": PLAN},
                },
            )
        if request.method == "GET" and "/timeline/" in path:
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "id": state["task_id"],
                            "parentId": JOB,
                            "type": "Task",
                            "state": "inProgress",
                            "log": {"id": 8},
                        },
                        {"id": JOB, "type": "Job", "state": "inProgress"},
                    ]
                },
            )
        if path.endswith("/logs/8"):
            state["logs"].append(request.content.decode())
        elif path.endswith("/feed"):
            state["feeds"].append(json.loads(request.content))
        elif path.endswith("/events"):
            state["events"].append(json.loads(request.content))
        else:
            raise AssertionError((request.method, path))
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        tasks.httpx,
        "Client",
        lambda **kw: original_client(**kw, transport=httpx.MockTransport(handle)),
    )

    def run(*, complete=False, reference=None, owner="request-1", graph="test-run", **overrides):
        definition = tasks.PipelineTaskDefinition(
            id="callback" if complete else "logs",
            display_name="Azure task",
            type="effect:ado:task-complete" if complete else "effect:ado:task-log-sync",
            task=reference or REFERENCE,
            session_id="test-agent",
            **overrides,
        )
        provider = tasks.TaskEffect()
        provider.initialize(
            definition, replace(context, metadata={**context.metadata, "graph_id": graph})
        )
        return provider.execute(execution_context={"idempotency_key": owner})

    return state, run, tmp_path


def test_incremental_logs_survive_provider_recreation_and_redact_secrets(environment):
    state, run, root = environment
    assert run(claim_start=True)["start_required"]
    assert run()["synced_lines"] == 0
    state["text"] += 'tool: password="very-secret-password"\nBearer host-only-build-token\n'
    assert run(graph="scheduled-log-loop")["synced_lines"] == 2
    assert state["feeds"][1]["startLine"] == 2
    assert "Checking readiness" in "".join(state["logs"])
    saved = (root / "workflow-state.json").read_text()
    for secret in ("very-secret-password", "host-only-build-token", "Checking readiness"):
        assert secret not in saved
    assert "very-secret-password" not in "".join(state["logs"])
    assert "host-only-build-token" not in json.dumps(state["feeds"])


def test_log_upload_failure_does_not_advance_cursor_or_block_failed_completion(environment):
    state, run, _ = environment
    run(claim_start=True)
    state["text"] += "tool: sign-in failed\n"
    state["failure"] = "/logs/8"
    assert run()["success"]
    state["failure"] = None
    assert run()["synced_lines"] == 1
    state["failure"] = "/logs/8"
    assert run(complete=True, result="failed")["done"]
    assert state["events"][-1] == dict(
        name="TaskCompleted", taskId=TASK, jobId=JOB, result="failed"
    )


def test_completion_flushes_pending_output_and_is_idempotent(environment):
    state, run, _ = environment
    run(claim_start=True)
    state["text"] += "Tests passed\n"
    assert run(complete=True, result="succeeded", summary="Evidence published")["done"]
    assert "Tests passed" in "".join(state["logs"])
    run(complete=True, result="failed")
    assert len(state["events"]) == 1
    assert state["events"][0]["result"] == "succeeded"
    state["failure"] = "/builds/42"
    assert run()["done"]


def test_retried_wait_job_attaches_without_restarting_test_and_gets_saved_result(environment):
    state, run, _ = environment
    assert run(claim_start=True)["start_required"]
    assert run(claim_start=True)["start_required"]  # replay the same effect after a crash
    assert not run(claim_start=True, owner="retry-job")["start_required"]
    run(complete=True, result="succeeded")
    state["task_id"] = "00000006-1111-4111-8111-111111111111"
    result = run(
        claim_start=True,
        owner="new-wait-task",
        reference={**REFERENCE, "task_id": state["task_id"]},
    )
    assert result["done"] and not result["start_required"]
    assert state["events"][-1]["taskId"] == state["task_id"]
    assert state["events"][-1]["result"] == "succeeded"


def test_callback_retries_are_bounded_and_keep_original_result(environment):
    state, run, _ = environment
    run(claim_start=True)
    state["failure"] = "/events"
    first = run(complete=True, result="failed")
    assert first["retry_delay_seconds"] == 15
    assert first["status"] == "callback_retry"
    run(complete=True, result="succeeded")
    assert run()["status"] == "callback_exhausted"
    state["failure"] = None
    assert run()["done"]
    assert state["events"] == []


def test_timeout_ends_wait_without_success(environment, monkeypatch):
    state, run, _ = environment
    run(claim_start=True, timeout_seconds=1)
    later = datetime.now(UTC) + timedelta(seconds=2)
    monkeypatch.setattr(
        tasks,
        "datetime",
        SimpleNamespace(now=lambda _: later, fromisoformat=datetime.fromisoformat),
    )
    assert run(complete=True, result="succeeded")["status"] == "failed"
    assert state["events"][0]["result"] == "failed"


def test_cancellation_stops_forwarding_without_completing_cancelled_task(environment):
    state, run, _ = environment
    run(claim_start=True)
    state["active"] = False
    state["text"] += "Should not be forwarded\n"
    assert run()["done"]
    assert not state["events"]
    assert "Should not be forwarded" not in "".join(state["logs"])


def test_wrong_pipeline_and_untrusted_destination_are_rejected_before_writes(environment):
    state, run, _ = environment
    state["wrong"] = 99
    with pytest.raises(ValueError, match="does not belong"):
        run(claim_start=True)
    assert state["logs"] == state["events"] == []
    with pytest.raises(ValidationError):
        run(reference={**REFERENCE, "callback_url": "https://untrusted.example/"})


def test_setup_failure_can_complete_without_an_agent(environment):
    state, run, _ = environment
    state["session"] = False
    assert run(complete=True, result="failed", summary="Environment readiness failed")["done"]
    assert state["events"][0]["result"] == "failed"


def test_setup_callback_retries_azure_read_outages_without_losing_failure(environment):
    state, run, root = environment
    state["session"] = False
    state["failure"] = "/builds/42"
    assert run(complete=True, result="failed")["status"] == "callback_retry"
    state["failure"] = None
    assert run()["status"] == "failed"
    assert state["events"][-1]["result"] == "failed"
    assert "sensitive-provider-response" not in (root / "workflow-state.json").read_text()


def test_large_final_log_is_drained_before_task_completion_and_reset_is_visible(environment):
    state, run, _ = environment
    run(claim_start=True)
    state["text"] = "\n".join(f"Progress {n}" for n in range(1500)) + "\n"
    assert not run(complete=True, result="succeeded")["done"]
    assert not state["events"]
    assert run()["done"]
    assert "Progress 1499" in "".join(state["logs"])
    assert "changed or restarted" in "".join(state["logs"])


def test_new_effects_are_in_addon_catalog_with_bindable_task_contract():
    registration = register_workflow_providers()
    for kind in ("effect:ado:task-log-sync", "effect:ado:task-complete"):
        assert kind in registration.effect_providers
        entry = next(e for e in registration.workflow_editor_catalog_entries if e.type == kind)
        assert {f.key for f in entry.fields if f.required} == {"task", "session_id"}
