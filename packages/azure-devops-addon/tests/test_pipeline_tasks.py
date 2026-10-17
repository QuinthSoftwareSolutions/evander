from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from evander.admin.services.container_worker_assignment_service import (
    ContainerWorkerAssignmentError,
)
from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.worker.models import WorkerJobRecord
from evander.worker.service import WorkerDaemon
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
        snapshot=None,
        modes=[],
        backend_error=None,
        task_id=TASK,
    )
    config = GatewayConfig(
        runtime_dir=tmp_path,
        ado_organization="example",
        azure_devops_project="Project",
        azure_devops_pat_env="BUILD_PAT",
    )
    session = SimpleNamespace(
        directory_path="root/test", vm_provider="container_worker_vm", metadata={}
    )
    state["manifest"] = session

    def snapshot(*args, mode, **kwargs):
        state["modes"].append(mode)
        return state["snapshot"](mode) if state["snapshot"] else state["text"]

    backend = SimpleNamespace(
        repository=SimpleNamespace(
            list_by_session=lambda _: [SimpleNamespace(execution_id="execution-1")]
        ),
        get_log_snapshot=snapshot,
    )

    def resolve_backend(**kwargs):
        if state["backend_error"] is not None:
            raise state["backend_error"]
        return backend

    admin = SimpleNamespace(
        runtime=SimpleNamespace(
            policy=SimpleNamespace(get_session=lambda _: session if state["session"] else None)
        ),
        vm_agent_admin=SimpleNamespace(container_worker_backend=resolve_backend),
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
            assert path.endswith(f"/timelines/{TIMELINE}/records/{JOB}/feed")
            feed = json.loads(request.content)
            assert feed["stepId"] == state["task_id"]
            state["feeds"].append(feed)
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
    result = run(complete=True, result="failed")
    assert not result["done"] and result["status"] == "log_retry"
    assert state["events"][-1] == dict(
        name="TaskCompleted", taskId=TASK, jobId=JOB, result="failed"
    )
    state["failure"] = None
    state["active"] = False
    assert run()["done"]
    assert "tool: sign-in failed" in "".join(state["logs"])
    assert len(state["events"]) == 1


@pytest.mark.parametrize("failure", ["/logs/8", "/feed"])
def test_final_diagnostics_retry_after_completion_without_repeating_callback(environment, failure):
    state, run, _ = environment
    run(claim_start=True)
    state["text"] += "Final failure diagnostics\n"
    state["failure"] = failure
    assert run(complete=True, result="failed")["status"] == "log_retry"
    assert len(state["events"]) == 1
    feed_count = len(state["feeds"])
    state["active"] = False
    state["failure"] = None
    assert run()["done"]
    assert "Final failure diagnostics" in "".join(state["logs"])
    assert "[Evander] failed:" in "".join(state["logs"])
    assert len(state["feeds"]) == feed_count
    assert len(state["events"]) == 1


@pytest.mark.parametrize("failure", ["/logs/8", "/builds/42"])
def test_log_retries_after_callback_are_bounded_and_survive_read_outages(environment, failure):
    state, run, _ = environment
    run(claim_start=True)
    state["failure"] = "/logs/8"  # Fail the final footer, while completing the task.
    assert run(complete=True, result="succeeded")["status"] == "log_retry"
    state["failure"] = failure
    assert run()["retry_delay_seconds"] == 15
    exhausted = run()
    assert exhausted["done"] and exhausted["status"] == "log_exhausted"
    state["failure"] = None
    assert run()["status"] == "log_exhausted"
    assert len(state["events"]) == 1


def test_live_tool_completion_does_not_replay_rendered_session_history(environment, tmp_path):
    state, run, _ = environment
    thread = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    home = tmp_path / "agent-home"
    sessions = home / ".codex" / "sessions"
    sessions.mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    transcript = sessions / f"rollout-{thread}.jsonl"
    events = [
        {
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "Checking deployment"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-1",
                "arguments": json.dumps({"cmd": "dotnet test"}),
            },
        },
    ]
    transcript.write_text("".join(json.dumps(event) + "\n" for event in events))
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "worker-runtime",
        worker_root=tmp_path / "worker-root",
        token="test-token",
        container_runtime=SimpleNamespace(get_logs=lambda *a, **kw: f"session id: {thread}\n"),
    )
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="test-agent",
        image="runner:latest",
        command=["true"],
        container_name="container-1",
        status="running",
        agent_home_path=str(home),
        repo_root_path=str(home / "repos"),
        artifacts_path=str(tmp_path / "artifacts"),
        logs_path=str(tmp_path / "logs"),
    )
    assert "active tool call:" in daemon.get_logs(record, mode="session")
    state["snapshot"] = lambda mode: daemon.get_logs(record, mode=mode)
    assert run(claim_start=True)["synced_lines"] == 2
    with transcript.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": "passed",
                    },
                }
            )
            + "\n"
        )
    run()
    uploaded = "".join(state["logs"])
    assert uploaded.count("Checking deployment") == 1
    assert uploaded.count("tool started:") == 1
    assert "tool completed:" in uploaded
    assert "dotnet test" in uploaded
    assert "active tool call:" not in uploaded
    assert "changed or restarted" not in uploaded
    assert set(state["modes"]) == {"session-history"}


def test_libvirt_session_can_complete_without_container_log_configuration(environment):
    state, run, _ = environment
    state["manifest"].vm_provider = "libvirt"
    assert run(complete=True, result="failed")["done"]
    assert state["events"][0]["result"] == "failed"
    assert state["modes"] == []


def test_worker_assignment_failure_keeps_callback_and_log_retries_bounded(environment):
    state, run, _ = environment
    run(claim_start=True)
    state["backend_error"] = ContainerWorkerAssignmentError("No worker assignment")
    result = run(complete=True, result="failed")
    assert result["status"] == "log_retry" and not result["done"]
    assert state["events"][-1]["result"] == "failed"
    run()
    assert run()["status"] == "log_exhausted"
    assert len(state["events"]) == 1


def test_session_directory_is_checked_before_reading_or_skipping_logs(environment):
    state, run, _ = environment
    state["manifest"].directory_path = "root/other"
    state["manifest"].vm_provider = "libvirt"
    with pytest.raises(ValueError, match="outside the workflow directory"):
        run(complete=True, result="failed")
    assert state["events"] == []


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
        fields = {f.key: f for f in entry.fields}
        assert {key: f.kind for key, f in fields.items()} == {
            "task": "json",
            "session_id": "string",
            "claim_start": "boolean",
            "timeout_seconds": "number",
            "result": "enum",
            "summary": "string",
        }
        assert fields["claim_start"].default_value is False
        assert fields["timeout_seconds"].default_value == 7200
        assert fields["result"].default_value == "failed"
        assert {option.value for option in fields["result"].options} == {"succeeded", "failed"}
