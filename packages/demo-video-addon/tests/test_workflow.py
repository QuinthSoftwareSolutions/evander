"""Exercise the example with the real workflow engine; replace only external I/O boundaries."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.effects.providers.base import EffectProviderExecutionError
from evander.events.models import TriggerEvent, validate_provider_node_definition
from evander.queries.providers.text_regex import TextRegexQueryProvider
from evander.workflow.packs import WorkflowPack, compose_workflow_ir
from evander.workflow.validation import validate_workflow_graph
from evander_demo_video_addon.workflow import build_workflow


class Factory:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []
        self.pr = {
            "status": "active",
            "source_commit": "1" * 40,
            "source_branch": "refs/heads/feature/example",
            "title": "Explain a useful change",
            "raw": {
                "repository": {"id": "repository-1"},
                "lastMergeCommit": {"commitId": "2" * 40},
            },
        }

    def build(self, definition, **kwargs):
        if definition.type == "query:text:regex":
            provider = TextRegexQueryProvider()
            provider.initialize(definition, None)
            return provider
        outer = self

        class Provider:
            def execute(self, *, resolved_config=None, execution_context=None):
                outer.calls.append((definition.id, deepcopy(resolved_config)))
                output = outer.overrides.get(definition.id)
                if isinstance(output, Exception):
                    raise output
                if output is not None:
                    return deepcopy(output)
                return deepcopy(
                    {
                        "explain-login": {"ready": True, "profile_id": "demo-admin"},
                        "explain-sessions": {"count": 1, "sessions": [{"session_id": "review-42"}]},
                        "explain-pr": outer.pr,
                        "explain-current-pr": outer.pr,
                        "explain-builds": {
                            "count": 1,
                            "builds": [
                                {
                                    "success": True,
                                    "status": "completed",
                                    "run_id": "123",
                                    "build_number": "2026.10.27.1",
                                    "source_version": "2" * 40,
                                    "run_url": "https://build.example.test/123",
                                }
                            ],
                        },
                        "explain-read-ready": {"content": "ready:sample-app:42:7:9:123\n"},
                        "explain-session": {"metadata": {"agent_thread_id": "existing-thread"}},
                        "explain-deploy": {
                            "run_url": "https://build.example.test/456",
                            "success": True,
                        },
                    }.get(definition.id, {"success": True})
                )

        return Provider()


def run_workflow(
    tmp_path, *, body="!EXPLAIN demo1", author="Alex", overrides=None, duplicate=False
):
    pack = build_workflow()
    composed = compose_workflow_ir(
        project_dir=tmp_path, workflow_packs=[], built_in_workflow_packs=[], inline_ir=pack["ir"]
    )
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": tmp_path,
            "poll_interval_seconds": 0.1,
            "jitter_seconds": 0,
            "max_graph_depth": 64,
            "workflow_graphs": pack["graphs"],
            **{
                name: getattr(composed, name)
                for name in ("triggers", "gates", "effects", "queries", "edges")
            },
        }
    )
    factory = Factory(overrides)
    worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=tmp_path), worker_config=config, runtime=None
    )
    worker.provider_factory = factory
    worker.query_provider_factory = factory
    finished_native = set()

    def publish(number):
        worker.event_queue.publish(
            TriggerEvent(
                event_id=f"feedback-event-{number}",
                trigger_id="explain-feedback",
                trigger_type="trigger:pull-request:new-feedback",
                occurred_at=datetime.now(UTC),
                payload={
                    "feedback": {
                        "body": body,
                        "author": author,
                        "repo": "sample-app",
                        "pr_id": "42",
                        "thread_id": "7",
                        "comment_id": "9",
                        "feedback_id": "feedback-1",
                        "metadata": {"agent_directed_author_self": author == "Review Bot"},
                    }
                },
            )
        )

    try:
        publish(1)
        for phase in range(2 if duplicate else 1):
            if phase:
                publish(2)
            for _ in range(130):
                worker.run_once()
                # Native agent completion is an external boundary, just like remote ADO and
                # worker RPCs above. Inject its normal completion event, not gate decisions.
                for record in worker.execution_store.read_all().values():
                    if (
                        record.effect_type == "effect:agent:enqueue-prompt"
                        and record.status == "succeeded"
                        and record.execution_id not in finished_native
                    ):
                        worker._publish_completion_event_from_record(
                            record=record, outputs=record.outputs, branch_port="completed"
                        )
                        finished_native.add(record.execution_id)
        records = {r.effect_id: r for r in worker.execution_store.read_all().values()}
        return factory.calls, records
    finally:
        worker.close()


def test_pack_is_canonical_and_uses_registered_definitions():
    payload = build_workflow()
    pack = WorkflowPack.model_validate(payload)
    assert not validate_workflow_graph(
        nodes=[n.node for n in pack.ir.nodes],
        edges=pack.ir.edges,
        trigger_node_ids=["explain-feedback"],
    )
    for item in pack.ir.nodes:
        validate_provider_node_definition(item.node)
    # This JSON is a published source artifact, so compare its parsed contract to its generator.
    assert (
        json.loads((Path(__file__).parents[1] / "examples/pr-explain.v1.json").read_text())
        == payload
    )


@pytest.mark.parametrize(
    "body",
    [
        "!EXPLAIN demo1",
        " !explain demo-1 ",
        "@<reviewer-id> !EXPLAIN demo1",
        '<a data-vss-mention="reviewer">Review Bot</a> !EXPLAIN demo1',
    ],
)
def test_valid_commands_deploy_pinned_build_and_resume_original_session(tmp_path, body):
    calls, records = run_workflow(tmp_path, body=body)
    by_id = dict(calls)
    assert "explain-finished" in by_id, [
        (key, record.status, record.error) for key, record in records.items()
    ]
    deployment = by_id["explain-deploy"]
    assert deployment["template_parameters"]["buildRunId"] == "123"
    assert deployment["template_parameters"]["databaseAction"] == "preserve"
    assert deployment["reject_running_parameters"]["resourceBaseName"] in {"demo1", "demo-1"}
    assert by_id["explain-prompt"]["target"] == "review-42"
    assert by_id["explain-prompt"]["resume_thread_id"] == "existing-thread"
    assert by_id["explain-preflight"]["resume_thread_id"] == "existing-thread"
    assert by_id["explain-preflight"]["resume_mode"] == "same_context"
    assert by_id["explain-prompt"]["resume_mode"] == "same_context"
    assert calls.index(next(c for c in calls if c[0] == "explain-read-ready")) < calls.index(
        next(c for c in calls if c[0] == "explain-deploy")
    )


@pytest.mark.parametrize(
    "body",
    ["!EXPLAIN", "!EXPLAIN Demo1", "!EXPLAIN demo1 extra", "!EXPLAIN ../demo1", "!EXPLAIN a-b"],
)
def test_malformed_commands_reply_without_deployment(tmp_path, body):
    calls, _ = run_workflow(tmp_path, body=body)
    assert any("body_text" in config for _, config in calls)
    assert "explain-deploy" not in dict(calls)


@pytest.mark.parametrize(
    "body,author",
    [
        ("!TEST demo1", "Alex"),
        ("Could you explain this?", "Alex"),
        ("The example is !EXPLAIN demo1", "Alex"),
        ("!EXPLAIN demo1", "Review Bot"),
    ],
)
def test_other_feedback_and_bot_comments_do_not_enter_explain(tmp_path, body, author):
    calls, _ = run_workflow(tmp_path, body=body, author=author)
    assert not any(
        name in dict(calls) for name in ("explain-sessions", "explain-deploy", "explain-prompt")
    )


@pytest.mark.parametrize(
    "name,output",
    [
        ("explain-login", {"ready": False}),
        ("explain-sessions", {"count": 0, "sessions": []}),
        ("explain-sessions", {"count": 2, "sessions": [{}, {}]}),
        ("explain-session", {"metadata": {}}),
        ("explain-pr", {"status": "completed", "source_commit": "1" * 40}),
        ("explain-builds", {"count": 0, "builds": []}),
        (
            "explain-builds",
            {"count": 1, "builds": [{"success": False, "status": "completed", "result": "failed"}]},
        ),
        ("explain-read-ready", {"content": "ready:other-request\n"}),
        ("explain-current-pr", {"status": "active", "source_commit": "3" * 40, "raw": {}}),
        ("explain-wait", RuntimeError("Build failed or timed out")),
    ],
)
def test_pr_build_and_readiness_rejections_stop_before_deployment(tmp_path, name, output):
    calls, _ = run_workflow(tmp_path, overrides={name: output})
    by_id = dict(calls)
    assert "explain-deploy" not in by_id
    assert any("body_text" in config for _, config in calls)


def test_duplicate_feedback_does_not_deploy_twice(tmp_path):
    calls, _ = run_workflow(tmp_path, duplicate=True)
    assert sum(name == "explain-deploy" for name, _ in calls) == 1


def test_deployment_failure_prevents_demo_and_queued_agent_keeps_retry_message(tmp_path):
    calls, _ = run_workflow(tmp_path / "deploy", overrides={"explain-deploy": RuntimeError("busy")})
    assert "explain-prompt" not in dict(calls)
    assert "partially updated" in dict(calls)["explain-deploy-error"]["body_text"]
    calls, _ = run_workflow(
        tmp_path / "queued",
        overrides={
            "explain-preflight": EffectProviderExecutionError("busy", outputs={"queued": True})
        },
    )
    assert "explain-deploy" not in dict(calls)
    assert "explain-preflight-failure-queued" in dict(calls)
