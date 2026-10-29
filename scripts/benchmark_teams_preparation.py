#!/usr/bin/env python3
"""Measure synthetic Teams response size and an 11-step durable workflow.

Run the same script/interpreter with each checkout's src on PYTHONPATH. All state
is temporary; no deployed database, provider or model is used. Provider bodies
return immediately, so timings cover scheduler/storage overhead, not VM startup.
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import Future
from datetime import UTC, datetime
import json
from pathlib import Path
import statistics
import tempfile
import time
from types import SimpleNamespace
from typing import Any

from mcp.server.mcpserver import MCPServer
from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.events.models import TriggerEvent
from evander.gateway.communication_attachment_helpers import _filter_teams_messages_for_session
from evander.models import TeamsMessageEnvelope


class ImmediateExecutor:
    def submit(self, function, *args, **kwargs):
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, **kwargs):
        pass


class Factory:
    def build(self, effect, **kwargs):
        output = (
            {"context": {f"field-{i}": "Synthetic context value. " * 4 for i in range(400)}}
            if effect.id == "step-3"
            else {"success": True}
        )
        return SimpleNamespace(execute=lambda **kwargs: output)


def workflow_sample():
    with tempfile.TemporaryDirectory(prefix="evander-preparation-") as directory:
        path = Path(directory)
        effects = [
            {
                "id": f"step-{i}",
                "display_name": f"Step {i}",
                "type": "effect:vm:create",
                "config": {"vm_name": f"vm-{i}", "description": "Sample task description. " * 35},
            }
            for i in range(990)
        ]
        config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "runtime_dir": path,
                "poll_interval_seconds": 13,
                "jitter_seconds": 0,
                "triggers": [
                    {
                        "id": "start",
                        "display_name": "Start",
                        "type": "trigger:cron",
                        "config": {"expression": "* * * * *"},
                    }
                ],
                "effects": effects,
                "edges": [
                    {"source_id": "start" if i == 0 else f"step-{i - 1}", "target_id": f"step-{i}"}
                    for i in range(990)
                    if i == 0 or i % 11 != 0
                ],
                "workflow_graphs": [
                    {
                        "id": f"graph-{i}",
                        "name": f"Graph {i}",
                        "directory_path": "root/sample",
                        "node_ids": (["start"] if i == 0 else [])
                        + [f"step-{j}" for j in range(i * 11, (i + 1) * 11)],
                    }
                    for i in range(90)
                ],
            }
        )
        worker = EffectOrchestratorWorker(
            config=GatewayConfig(runtime_dir=path),
            worker_config=config,
            runtime=None,
        )
        worker._executor.shutdown(wait=True)
        worker._executor = ImmediateExecutor()
        worker.provider_factory = Factory()
        worker._completion_dispatch_hooks = ()
        worker._lifecycle_reconciliation_hooks = ()
        try:
            worker.run_once()  # Schema/config setup precedes a message on a running daemon.
            worker.event_queue.publish(
                TriggerEvent(
                    event_id="sample",
                    trigger_id="start",
                    trigger_type="trigger:cron",
                    occurred_at=datetime.now(UTC),
                    trace_id="sample",
                    payload={
                        "message": {
                            "content": "A sample message. " * 20,
                            "metadata": {
                                f"field-{i}": "Provider context value. " * 3 for i in range(90)
                            },
                        }
                    },
                )
            )
            started = time.perf_counter()
            for _ in range(100):
                if worker.run_once().next_sleep_seconds:
                    break
            duration_ms = (time.perf_counter() - started) * 1000
            records = worker.execution_store.list_executions()
            assert len(records) == 11 and all(r.status == "succeeded" for r in records)
            assert {r.effect_id for r in records} == {f"step-{i}" for i in range(11)}
            return duration_ms, max(len(json.dumps(r.input_payload)) for r in records)
        finally:
            worker.close()


def teams_size():
    messages = []
    for i in range(5):
        content = "Please review the changes and send the result. " * 8
        messages.append(
            TeamsMessageEnvelope(
                cursor=f"chat:sample-chat/message-{i}",
                message_id=f"message-{i}",
                author_id="sample-user",
                chat_id="sample-chat",
                content=content,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                metadata={
                    "source": "teams_graph_api",
                    "target": "chat:sample-chat",
                    "target_type": "chat",
                    "author_id": "sample-user",
                    "chat_id": "sample-chat",
                    "content_type": "html",
                    "attachments": [],
                    "reply_to_id": None,
                    "message": {
                        "id": f"message-{i}",
                        "body": {"contentType": "html", "content": content},
                        "from": {"user": {"id": "sample-user", "displayName": "Alex"}},
                        "attachments": [],
                        "reactions": [],
                        "mentions": [],
                        "provider_diagnostics": "Provider bookkeeping. " * 45,
                    },
                },
            )
        )
    context = SimpleNamespace(
        policy=SimpleNamespace(require_teams_message_source=lambda *args: None)
    )
    server = MCPServer("size-benchmark")

    @server.tool()
    def history() -> dict[str, Any]:
        result, denied, cursor = _filter_teams_messages_for_session(
            context,
            "sample-session",
            None,
            messages,
            {},
            lambda *args: None,
        )
        assert denied == 0
        assert [m["content"] for m in result] == [m.content for m in messages]
        return {"messages": result, "next_cursor": cursor}

    result = asyncio.run(server.call_tool("history", {}))
    return {
        "content_characters": sum(len(m.content) for m in messages),
        "structured_characters": len(json.dumps(result.structured_content, ensure_ascii=False)),
        "text_characters": sum(len(item.text) for item in result.content),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=9)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    values = [workflow_sample() for _ in range(args.samples)]
    print(
        json.dumps(
            {
                "workflow_ms": [round(value[0], 3) for value in values],
                "median_ms": round(statistics.median(value[0] for value in values), 3),
                "max_input_characters": max(value[1] for value in values),
                "teams": teams_size(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
