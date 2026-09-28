#!/usr/bin/env python3
"""Compare scheduler storage under identical synthetic concurrent workloads.

Run this same file with each checkout's Python and --worktree. Providers have
no external side effects. Every retry contributes to elapsed time; incomplete
chains and duplicate provider executions fail the run. Runtime data is temporary.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path


def busy(error):
    return isinstance(error, sqlite3.OperationalError) and (
        getattr(error, "sqlite_errorcode", 0) & 255 in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
        or "locked" in str(error).lower()
        or "deadline exceeded" in str(error).lower()
    )


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--chains", type=int, default=30)
    parser.add_argument("--payload-kib", type=int, default=256)
    parser.add_argument("--lock-hold-ms", type=float, default=120)
    parser.add_argument("--lock-gap-ms", type=float, default=40)
    parser.add_argument("--duration-limit", type=float, default=180)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--max-regression-ratio", type=float, default=1.1)
    args = parser.parse_args()
    sys.path[:0] = [str(args.worktree / "src"), str(args.worktree / "tests")]

    from evander.events.models import TriggerEvent
    from evander.orchestration.isolated_maintenance import IsolatedMaintenanceHook
    from evander.orchestration.providers.agent_defaults import (
        _ProviderCompletionDispatchHook,
        _ProviderLifecycleReconciliationHook,
    )
    from evander_team_addon.providers import TeamAgentPromptCompletionFinalizeHook
    from test_orchestration_handoff_latency import _seed_latency_history, _worker

    log_counts = Counter()

    class LogCounts(logging.Handler):
        def emit(self, record):
            message = record.getMessage().lower()
            if "busy" in message or "locked" in message or "deadline exceeded" in message:
                log_counts[record.name] += 1

    logging.getLogger().handlers[:] = [LogCounts()]
    logging.getLogger().setLevel(logging.WARNING)
    counts = Counter()
    starts, returns = {}, {}
    count_lock = threading.Lock()
    shared_output = {"success": True, "body": "x" * (args.payload_kib * 1024)}

    class Provider:
        def execute(self, *, resolved_config=None, execution_context=None):
            context = execution_context or {}
            key = (context["workflow_trace_id"], context["node_id"])
            with count_lock:
                counts[key] += 1
                starts[key] = time.monotonic()
                returns[key] = time.monotonic()
            return dict(shared_output)

    class Factory:
        def build(self, effect, **kwargs):
            return Provider()

    with tempfile.TemporaryDirectory(prefix="orchestration-contention-") as directory:
        runtime = Path(directory)
        worker = _worker(runtime)
        _seed_latency_history(worker, runtime)
        # The focused latency fixture intentionally leaves unrelated rows
        # unhydratable. This workload also exercises maintenance, so give those
        # rows valid legacy execution records for both implementations.
        with sqlite3.connect(worker.execution_store.path) as connection:
            recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
            connection.execute("""
                UPDATE effect_executions SET started_at=?,finished_at=?,payload_json=json_set(payload_json,
                    '$.execution_id',execution_id,'$.idempotency_key',idempotency_key,
                    '$.event_id',event_id,'$.trace_id',trace_id,
                    '$.source_node_id',source_node_id,'$.source_trigger_type',source_trigger_type,
                    '$.effect_id',effect_id,'$.effect_type',effect_type,'$.status',status,
                    '$.started_at',?,
                    '$.finished_at',?,'$.resolved_config_hash','synthetic')
                WHERE trace_id='history'
            """, (recent, recent, recent, recent))
        worker.provider_factory = Factory()
        team = IsolatedMaintenanceHook(TeamAgentPromptCompletionFinalizeHook())
        worker._lifecycle_reconciliation_hooks = (_ProviderLifecycleReconciliationHook(team),)
        worker._completion_dispatch_hooks = (_ProviderCompletionDispatchHook(team),)
        worker._initialize_lifecycle_hooks()
        database = worker.execution_store.path
        stop, writer_ready = threading.Event(), threading.Event()
        writer_stats = {"transactions": 0, "busy": 0, "lock_wait_ms": 0.0, "wal_peak_bytes": 0}
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE benchmark_competing_writer (id INTEGER PRIMARY KEY, value INTEGER)")
            connection.execute("INSERT INTO benchmark_competing_writer VALUES (1, 0)")

        def competing_writer():
            connection = sqlite3.connect(database, timeout=0.05)
            try:
                while not stop.is_set():
                    lock_started = time.monotonic()
                    try:
                        connection.execute("BEGIN IMMEDIATE")
                        writer_stats["lock_wait_ms"] += (time.monotonic() - lock_started) * 1000
                        connection.execute("UPDATE benchmark_competing_writer SET value=value+1 WHERE id=1")
                        writer_ready.set()
                        stop.wait(args.lock_hold_ms / 1000)
                        connection.commit()
                        writer_stats["transactions"] += 1
                    except sqlite3.OperationalError as error:
                        connection.rollback()
                        if not busy(error):
                            raise
                        writer_stats["busy"] += 1
                    wal = Path(str(database) + "-wal")
                    if wal.exists():
                        writer_stats["wal_peak_bytes"] = max(writer_stats["wal_peak_bytes"], wal.stat().st_size)
                    stop.wait(args.lock_gap_ms / 1000)
            finally:
                connection.close()

        # Publish the same backlog before starting the competing writer, so
        # both versions begin measurement with identical durable inputs.
        for number in range(args.chains):
            trace = f"synthetic-{number:03d}"
            worker.event_queue.publish(TriggerEvent(
                event_id=trace, trigger_id="start", trigger_type="trigger:cron",
                trace_id=trace, occurred_at=datetime.now(UTC), payload={"message": trace},
            ))
        thread = threading.Thread(target=competing_writer, daemon=True)
        thread.start()
        assert writer_ready.wait(2), "competing writer did not start"
        started = time.monotonic()
        deadline = started + args.duration_limit
        cycles, outer_busy, max_cycle = 0, 0, 0.0
        completed, failed = 0, 0
        observer = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)
        try:
            while time.monotonic() < deadline:
                cycle_started = time.monotonic()
                try:
                    result = worker.run_once()
                except sqlite3.OperationalError as error:
                    if not busy(error):
                        raise
                    outer_busy += 1
                    time.sleep(0.01)
                    continue
                cycles += 1
                max_cycle = max(max_cycle, time.monotonic() - cycle_started)
                rows = observer.execute(
                    "SELECT status,COUNT(*) FROM effect_executions WHERE trace_id LIKE 'synthetic-%' GROUP BY status"
                ).fetchall()
                statuses = dict(rows)
                completed, failed = statuses.get("succeeded", 0), statuses.get("failed", 0)
                if completed == args.chains * 6 or failed:
                    break
                if result.next_sleep_seconds and not result.events_processed and not result.completion_events_published:
                    time.sleep(min(result.next_sleep_seconds, 0.05, max(0, deadline-time.monotonic())))
            elapsed = time.monotonic() - started
        finally:
            observer.close()
            stop.set()
            thread.join(timeout=3)
            worker.close()
            worker._executor.shutdown(wait=True)
        handoffs, response_times = [], []
        for number in range(args.chains):
            trace = f"synthetic-{number:03d}"
            if all((trace, f"step-{stage}") in starts for stage in range(6)):
                handoffs.append(sum(starts[(trace, f"step-{stage}")] - returns[(trace, f"step-{stage-1}")]
                                    for stage in range(1, 6)))
                response_times.append(returns[(trace, "step-5")] - started)
        expected = args.chains * 6
        duplicate_calls = sum(max(0, count - 1) for count in counts.values())
        report = {
            "worktree": str(args.worktree), "chains": args.chains, "stages": 6,
            "payload_kib_per_stage": args.payload_kib,
            "seeded_execution_history": 10000, "seeded_team_dispatches": 5000,
            "writer_hold_ms": args.lock_hold_ms, "writer_gap_ms": args.lock_gap_ms,
            "elapsed_seconds": elapsed, "cycles": cycles, "max_cycle_seconds": max_cycle,
            "handoff_p95_seconds": percentile(handoffs, .95),
            "response_p95_seconds": percentile(response_times, .95),
            "completed_executions": completed, "expected_executions": expected,
            "failed_executions": failed, "missing_provider_calls": expected-len(counts),
            "duplicate_provider_calls": duplicate_calls, "worker_restarts": 0,
            "outer_busy_retries": outer_busy, "busy_log_counts": dict(log_counts),
            "writer": writer_stats,
            "integrity_passed": completed == expected and not failed and not duplicate_calls,
            "latency_gate": {"evaluated": False},
        }
        if args.baseline_report:
            baseline = json.loads(args.baseline_report.read_text())
            for key in (
                "chains", "stages", "payload_kib_per_stage", "seeded_execution_history",
                "seeded_team_dispatches", "writer_hold_ms", "writer_gap_ms",
            ):
                if baseline[key] != report[key]:
                    raise ValueError(f"Baseline workload differs at {key}")
            ratios = {
                key: report[key] / baseline[key] if report[key] is not None else None
                for key in ("handoff_p95_seconds", "response_p95_seconds")
            }
            report["latency_gate"] = {
                "evaluated": True, "maximum_ratio": args.max_regression_ratio,
                "ratios": ratios,
                "passed": all(ratio is not None and ratio <= args.max_regression_ratio
                              for ratio in ratios.values()),
            }
            report["passed"] = report["integrity_passed"] and report["latency_gate"]["passed"]
        rendered = json.dumps(report, indent=2)
        print(rendered)
        if args.output:
            args.output.write_text(rendered + "\n")
        return 0 if report["integrity_passed"] and report.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
