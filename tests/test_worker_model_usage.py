import asyncio
from types import MethodType, SimpleNamespace

import httpx

from evander.worker.api import build_worker_app
from evander.worker.model_usage import SubscriptionUsageRecorder, WorkerUsageJournal
from evander.worker.service import WorkerDaemon


def recorder(journal, job="job-1", **context):
    return SubscriptionUsageRecorder(
        job_id=job,
        session_id="session-1",
        sink=journal.append,
        context={"cost_category": "Review", "directory_path": "root/a", **context},
    )


def update(writer, total, last, *, thread="parent", turn="turn-1"):
    writer.notification(
        "thread/tokenUsage/updated",
        {
            "threadId": thread,
            "turnId": turn,
            "tokenUsage": {
                "total": {"totalTokens": total},
                "last": {
                    "totalTokens": last,
                    "cachedInputTokens": 9999,
                    "reasoningOutputTokens": 9999,
                },
            },
            "accessToken": "must-not-be-persisted",
        },
    )


def test_live_counters_deduplicate_resumed_threads_count_children_and_survive_restart(tmp_path):
    path = tmp_path / "receipts.db"
    journal = WorkerUsageJournal(path)
    writer = recorder(journal)
    update(writer, 1000, 500)  # A resumed thread's old snapshot, before a new turn starts.
    writer.expect_turn("parent")
    update(writer, 1020, 20)
    update(writer, 1020, 20)
    update(writer, 1040, 20)  # Same last count, distinct inference.
    update(writer, 9000, 9000, thread="unrelated")
    writer.notification("turn/started", {"threadId": "child", "turn": {"id": "child-turn"}})
    update(writer, 5, 5, thread="child", turn="child-turn")
    writer.notification(
        "turn/completed", {"threadId": "child", "turn": {"id": "child-turn", "status": "completed"}}
    )
    writer.notification(
        "turn/completed", {"threadId": "parent", "turn": {"id": "turn-1", "status": "completed"}}
    )
    update(writer, 1040, 20)  # Duplicate after completion is still ignored.
    writer.expect_turn("parent")
    update(writer, 1050, 10, turn="turn-2")
    writer.notification(
        "turn/completed",
        {
            "threadId": "parent",
            "turn": {"id": "turn-2", "status": "completed"},
        },
    )
    writer.finish()
    writer.finish()
    reloaded = WorkerUsageJournal(path)
    first = reloaded.page("job-1", limit=2)
    second = reloaded.page("job-1", cursor=first.next_cursor, limit=2)
    assert first.has_more and not second.has_more
    events = first.events + second.events
    assert [event.total_tokens for event in events] == [20, 20, 5, 10]
    assert len({event.event_id for event in events}) == 4
    assert all(
        event.cost_category == "Review" and event.billing_kind == "subscription" for event in events
    )
    assert "must-not-be-persisted" not in "".join(event.model_dump_json() for event in events)
    # Redelivery is harmless and receipts outlive the connection that produced them.
    for event in events:
        reloaded.append("job-1", event)
    assert len(reloaded.page("job-1").events) == 4
    assert reloaded.page("other-job").events == []


def test_cancelled_turns_and_counter_resets_report_unknown_counts_instead_of_zero(tmp_path):
    journal = WorkerUsageJournal(tmp_path / "receipts.db")
    writer = recorder(journal)
    writer.notification("turn/started", {"threadId": "child", "turn": {"id": "cancelled"}})
    writer.expect_turn("parent")
    update(writer, 100, 10)
    update(writer, 90, 10)  # An unresolvable native counter reset.
    writer.notification(
        "turn/completed",
        {
            "threadId": "parent",
            "turn": {"id": "turn-1", "status": "completed"},
        },
    )
    writer.finish()
    events = journal.page("job-1").events
    assert [event.total_tokens for event in events] == [10, None, None]
    assert events[-1].request_status == "cancelled"
    assert all(event.normalized_usage_status == "usage_unavailable" for event in events[1:])


def test_interruption_keeps_observed_counts_and_marks_unfinished_inference_partial(tmp_path):
    journal = WorkerUsageJournal(tmp_path / "receipts.db")
    writer = recorder(journal)
    writer.expect_turn("parent")
    update(writer, 100, 10)
    writer.finish()
    writer.finish()
    events = journal.page("job-1").events
    assert [event.total_tokens for event in events] == [10, None]
    assert events[-1].request_status == "cancelled"
    assert events[-1].normalized_usage_status == "usage_unavailable"


def test_missing_malformed_and_zero_usage_are_distinct(tmp_path):
    journal = WorkerUsageJournal(tmp_path / "receipts.db")
    writer = recorder(journal)
    writer.expect_turn("parent")
    update(writer, 0, 0)
    writer.notification(
        "turn/completed", {"threadId": "parent", "turn": {"id": "turn-1", "status": "completed"}}
    )
    writer.expect_turn("parent")
    writer.notification(
        "thread/tokenUsage/updated",
        {
            "threadId": "parent",
            "turnId": "bad",
            "tokenUsage": {
                "total": {"totalTokens": True},
                "last": {"totalTokens": -10},
            },
        },
    )
    writer.finish()
    events = journal.page("job-1").events
    assert [event.total_tokens for event in events] == [0, None]
    assert [event.normalized_usage_status for event in events] == ["available", "usage_unavailable"]


def test_worker_export_is_authenticated_bounded_and_does_not_import_history(tmp_path):
    journal = WorkerUsageJournal(tmp_path / "receipts.db")
    writer = recorder(journal)
    writer.expect_turn("parent")
    update(writer, 10, 10)
    daemon = SimpleNamespace(
        token="worker-secret",
        usage_journal=journal,
        store=SimpleNamespace(
            get=lambda job: (
                SimpleNamespace(job_id=job, status="exited")
                if job in {"job-1", "old-job"}
                else None
            )
        ),
        _codex_subscription_runners={},
        _codex_runtime_bridges={},
    )
    daemon.model_usage_page = MethodType(WorkerDaemon.model_usage_page, daemon)
    app = build_worker_app(daemon)

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://worker"
        ) as client:
            assert (await client.get("/v1/jobs/job-1/model-usage")).status_code == 401
            client.headers["Authorization"] = "Bearer worker-secret"
            assert (await client.get("/v1/jobs/absent/model-usage")).status_code == 404
            for query in ["cursor=-1", "limit=0", "limit=501", "cursor=bad"]:
                assert (await client.get(f"/v1/jobs/job-1/model-usage?{query}")).status_code == 400
            result = (await client.get("/v1/jobs/job-1/model-usage?limit=1")).json()
            assert result["complete"] is True
            daemon._codex_runtime_bridges["job-1"] = object()
            draining = (await client.get("/v1/jobs/job-1/model-usage?limit=1")).json()
            assert draining["complete"] is False
            daemon._codex_runtime_bridges.clear()
            assert [event["total_tokens"] for event in result["events"]] == [10]
            old = (await client.get("/v1/jobs/old-job/model-usage")).json()
            assert old["events"] == []

    asyncio.run(check())
