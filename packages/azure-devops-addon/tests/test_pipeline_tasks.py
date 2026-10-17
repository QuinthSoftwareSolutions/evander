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
from evander.execution.container_worker_repository import ContainerWorkerExecutionRepository
from evander.policy import PolicyEngine
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
        accepted_results={},
        callback_failure=None,
        callback_crash=False,
        callback_calls=0,
        log_crash=False,
        log_calls=0,
        task_id=TASK,
    )
    config = GatewayConfig(
        runtime_dir=tmp_path,
        ado_organization="example",
        azure_devops_project="Project",
        azure_devops_pat_env="BUILD_PAT",
    )
    session = SimpleNamespace(
        directory_path="root/test",
        vm_provider="container_worker_vm",
        metadata={},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
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
    state["backend"] = backend

    def resolve_backend(**kwargs):
        if state["backend_error"] is not None:
            raise state["backend_error"]
        return backend

    policy = SimpleNamespace(
        session_store=SimpleNamespace(get_session=lambda _: session if state["session"] else None)
    )
    policy.get_session = lambda session_id: PolicyEngine.get_session(policy, session_id)
    admin = SimpleNamespace(
        runtime=SimpleNamespace(policy=policy),
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
        if request.method == "GET":
            assert "/Project/_apis/build/" in path
        else:
            assert f"/{PROJECT}/_apis/distributedtask/" in path
        if path.endswith("/logs/8"):
            state["log_calls"] += 1
            if state["log_crash"]:
                raise SystemExit("Process stopped during final log delivery")
        if path.endswith("/events"):
            state["callback_calls"] += 1
            if state["callback_crash"]:
                raise SystemExit("Process stopped before Azure accepted completion")
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
                            "state": "completed"
                            if not state["active"] or state["task_id"] in state["accepted_results"]
                            else "inProgress",
                            "result": state["accepted_results"].get(state["task_id"])
                            or ("canceled" if not state["active"] else None),
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
            event = json.loads(request.content)
            state["events"].append(event)
            state["accepted_results"][event["taskId"]] = event["result"]
            failure, state["callback_failure"] = state["callback_failure"], None
            if failure:
                state["active"] = False
                if failure == "crash":
                    raise SystemExit("Process stopped after Azure accepted completion")
                raise httpx.ReadError("Callback acknowledgement lost", request=request)
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


@pytest.mark.parametrize("failure", ["transport", "crash"])
def test_lost_callback_acknowledgement_keeps_final_log_recovery(environment, failure):
    state, run, _ = environment
    run(claim_start=True)
    state["text"] += "Diagnostics pending when completion was accepted\n"
    state["failure"] = "/logs/8"
    state["callback_failure"] = failure
    if failure == "crash":
        with pytest.raises(SystemExit):
            run(complete=True, result="failed")
    else:
        assert run(complete=True, result="failed")["status"] == "callback_retry"
    assert len(state["events"]) == 1
    assert "Diagnostics pending" not in "".join(state["logs"])
    state["failure"] = None
    recovered = run()
    assert recovered["done"] and recovered["status"] == "failed"
    assert "Diagnostics pending" in "".join(state["logs"])
    assert len(state["events"]) == 1


def test_last_transport_failure_reconciles_receipt_without_a_fourth_callback(environment):
    state, run, _ = environment
    run(claim_start=True)
    state["failure"] = "/events"
    for _ in range(2):
        assert run(complete=True, result="failed")["status"] == "callback_retry"
    state["text"] += "Diagnostics pending on the last callback\n"
    state["failure"] = "/logs/8"
    state["callback_failure"] = "transport"
    assert run()["status"] == "callback_retry"
    assert state["callback_calls"] == 3
    state["failure"] = None
    recovered = run()
    assert recovered["done"] and recovered["status"] == "failed"
    assert "Diagnostics pending on the last callback" in "".join(state["logs"])
    assert state["callback_calls"] == 3
    assert len(state["events"]) == 1


@pytest.mark.parametrize("result", ["canceled", "succeeded"])
def test_unmatched_remote_result_does_not_resume_logs_after_callback_failure(environment, result):
    state, run, _ = environment
    run(claim_start=True)
    state["failure"] = "/events"
    assert run(complete=True, result="failed")["status"] == "callback_retry"
    state["failure"] = None
    state["active"] = False
    state["accepted_results"][TASK] = result
    state["text"] += "Should not be forwarded after external completion\n"
    assert run()["status"] == "inactive"
    assert "Should not be forwarded" not in "".join(state["logs"])
    assert state["events"] == []


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


def test_corrupt_worker_repository_does_not_block_callback_and_log_retries_are_bounded(
    environment, tmp_path
):
    state, run, root = environment
    database = tmp_path / "unavailable-worker.db"
    database.write_bytes(b"not a SQLite database")
    state["backend"].repository = ContainerWorkerExecutionRepository(database)
    first = run(complete=True, result="failed")
    assert first["status"] == "log_retry" and not first["done"]
    assert state["events"][-1]["result"] == "failed"
    assert run()["status"] == "log_retry"
    exhausted = run()
    assert exhausted["done"] and exhausted["status"] == "log_exhausted"
    assert state["callback_calls"] == 1
    saved = (root / "workflow-state.json").read_text()
    assert str(database) not in saved
    assert "file is not a database" not in saved


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


@pytest.mark.parametrize("expired", [False, True])
def test_session_directory_is_checked_before_reading_or_skipping_logs(environment, expired):
    state, run, _ = environment
    state["manifest"].directory_path = "root/other"
    state["manifest"].vm_provider = "libvirt"
    if expired:
        state["manifest"].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    for _ in range(4):
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


@pytest.mark.parametrize("failure", ["/events", "/_apis/distributedtask/"])
def test_callback_retries_are_bounded_and_keep_original_result(environment, failure):
    state, run, _ = environment
    run(claim_start=True)
    state["failure"] = failure
    first = run(complete=True, result="failed")
    assert first["retry_delay_seconds"] == 15
    assert first["status"] == "callback_retry"
    run(complete=True, result="succeeded")
    assert run()["status"] == "callback_retry"
    assert run()["status"] == "callback_exhausted"
    state["failure"] = None
    assert run()["status"] == "callback_exhausted"
    assert state["events"] == []


@pytest.mark.parametrize("last_accepted", [False, True])
def test_callback_budget_survives_process_stops_and_reconciles_last_receipt(
    environment, last_accepted
):
    state, run, _ = environment
    run(claim_start=True)
    state["callback_crash"] = True
    for _ in range(2):
        with pytest.raises(SystemExit):
            run(complete=True, result="failed")
    state["callback_crash"] = not last_accepted
    state["callback_failure"] = "crash"
    with pytest.raises(SystemExit):
        run(complete=True, result="failed")
    expected = "failed" if last_accepted else "callback_exhausted"
    recovered = run()
    assert recovered["done"] and recovered["status"] == expected
    assert run()["status"] == expected
    assert state["callback_calls"] == 3
    assert len(state["events"]) == (1 if last_accepted else 0)


@pytest.mark.parametrize("already_completed", [False, True])
def test_terminal_log_budget_survives_process_stops_without_blocking_callback(
    environment, already_completed
):
    state, run, _ = environment
    run(claim_start=True)
    state["text"] += "Pending terminal diagnostics\n"
    if already_completed:
        state["failure"] = "/logs/8"
        assert run(complete=True, result="failed")["status"] == "log_retry"
        state["failure"] = None
    remaining_attempts = 2 if already_completed else 3
    previous_calls = state["log_calls"]
    state["log_crash"] = True
    for _ in range(remaining_attempts):
        with pytest.raises(SystemExit):
            run(complete=True, result="failed")
    recovered = run()
    assert recovered["done"] and recovered["status"] == "log_exhausted"
    assert run()["status"] == "log_exhausted"
    assert state["log_calls"] - previous_calls == remaining_attempts
    assert len(state["events"]) == 1


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


def test_expired_session_can_complete_without_reading_worker_logs(environment):
    state, run, _ = environment
    state["manifest"].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert run(complete=True, result="failed")["done"]
    assert state["events"][0]["result"] == "failed"
    assert state["modes"] == []


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
    state["text"] = "\n".join(f"Progress {n}" for n in range(4500)) + "\n"
    assert not run(complete=True, result="succeeded")["done"]
    for _ in range(3):
        assert not run()["done"]
    assert not state["events"]
    assert run()["done"]
    assert "Progress 4499" in "".join(state["logs"])
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
