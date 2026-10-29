#!/usr/bin/env python3
"""Compare ten read paths using temporary synthetic stores and deterministic work counts.

Run this same script with each checkout's src on PYTHONPATH and the same interpreter.
Fixtures and instrumentation are shared with the regression tests; dev dependencies
are required. This measures read work, not production latency or worker capacity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import evander
from evander.worker.service import WorkerDaemon
from evander.workflow.validation import (
    derive_workflow_output_schemas,
    validate_workflow_binding_diagnostics,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from read_scaling_probe import LineProbe, ReadProbe  # noqa: E402
from test_lookup_read_scaling import (  # noqa: E402
    NOW,
    context,
    message,
    teams_store,
    usage_store,
)
from test_wake_read_scaling import (  # noqa: E402
    FixedClock,
    dispatch,
    request,
    seed as wake_seed,
)
from test_worker_read_scaling import job, seed as worker_seed, service  # noqa: E402
from test_workflow_validation_scaling import (  # noqa: E402
    filter_chain,
    independent_pairs,
    reverse_chain,
)


def digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def median_read_ms(function, repeat):
    # Work counters and tracing are disabled for these warm read timings.
    durations = []
    for _ in range(repeat):
        started = time.perf_counter()
        function()
        durations.append((time.perf_counter() - started) * 1000)
    return statistics.median(durations)


def read_sample(function, repeat):
    with ReadProbe() as probe:
        output = function()
    report = {**probe.report(), "output_digest": digest(output)}
    if repeat:
        report["median_warm_read_ms"] = median_read_ms(function, repeat)
    return report


def run(root: Path, history: int, nodes: int, repeat: int) -> dict:
    def sample(function):
        return read_sample(function, repeat)

    samples = {}
    store = worker_seed(root / "telemetry", history)
    store.put(job(root / "telemetry", "running", cleanup="cleanup_failed"))
    store.put(job(root / "telemetry", "failed", status="failed"))
    samples["worker_telemetry"] = sample(
        lambda: service(store)._job_telemetry().model_dump(mode="json")
    )

    store = worker_seed(root / "startup", history)
    for status in ("created", "preparing", "starting", "running"):
        store.put(
            job(
                root / "startup",
                f"sub-{status}",
                status=status,
                metadata={"codex_auth_mode": "chatgpt_subscription"},
            )
        )
    store.put(job(root / "startup", "metered"))

    def startup():
        daemon = WorkerDaemon(
            runtime_dir=root / "startup" / "runtime",
            worker_root=root / "startup" / "worker",
            container_runtime=SimpleNamespace(),
            token="synthetic",
        )
        daemon.close()
        return True

    with patch("evander.worker.service.now_utc", lambda: NOW):
        # Startup mutates jobs; repeated calls would measure a different workload.
        samples["worker_restart"] = read_sample(startup, repeat=0)
    samples["worker_restart"]["output_digest"] = digest(
        {
            job_id: store.get(job_id).status
            for job_id in (
                "sub-created",
                "sub-preparing",
                "sub-starting",
                "sub-running",
                "metered",
                "old-0",
            )
        }
    )

    store = worker_seed(root / "profiles", history)
    for index in range(8):
        store.put(
            job(
                root / "profiles",
                f"active-{index}",
                metadata={
                    "codex_auth_mode": "chatgpt_subscription",
                    "subscription_profile_id": f"profile-{index % 4}",
                },
            )
        )
    daemon = service(store)
    daemon._codex_auth_registry = SimpleNamespace(
        list_profiles=lambda: {"profiles": [{"profile_id": f"profile-{i}"} for i in range(20)]}
    )
    samples["subscription_profiles"] = sample(daemon.list_codex_auth_profiles)

    store = wake_seed(root / "dispatches", dispatches=history)
    wake = request("wanted")
    with store._connect() as db:
        db.execute(
            "INSERT INTO wake_dispatches VALUES (?,?,?,?,?,?,?)",
            store._dispatch_row(dispatch("wanted", "wanted")),
        )
    with patch("evander.messaging.wake_store.datetime", FixedClock):
        samples["wake_dispatch_lookup"] = sample(lambda: store.ledger_entry_for_request(wake))

    store = wake_seed(root / "ledger", requests=history)
    with patch("evander.messaging.wake_store.datetime", FixedClock):
        samples["wake_ledger_page"] = sample(lambda: store.list_ledger_entries(limit=10))

    store = teams_store(root / "teams", history)
    store.add_message_if_new(message("wanted", "wanted"))
    samples["teams_message_lookup"] = sample(
        lambda: store.find_message("wanted", channel_target="team/channel").model_dump(mode="json")
    )

    store = usage_store(root / "usage", history)
    store.upsert_correlation_context(context("wanted"))
    samples["model_usage_context"] = sample(
        lambda: store.get_active_correlation_context(
            session_id="session-one", now_utc=NOW
        ).model_dump(mode="json")
    )

    config, reads = independent_pairs(nodes)
    diagnostics = validate_workflow_binding_diagnostics(config)
    samples["binding_ancestors"] = {
        "edge_field_reads": dict(reads),
        "output_digest": digest([item.model_dump() for item in diagnostics]),
    }
    plain_config = {**config, "edges": [dict(edge) for edge in config["edges"]]}
    samples["binding_ancestors"]["median_warm_read_ms"] = median_read_ms(
        lambda: validate_workflow_binding_diagnostics(plain_config), repeat
    )

    config, reads = filter_chain(nodes)
    schemas = derive_workflow_output_schemas(config)
    samples["output_schema_derivation"] = {
        "edge_field_reads": dict(reads),
        "output_digest": digest(schemas),
    }
    plain_config = {**config, "edges": [dict(edge) for edge in config["edges"]]}
    samples["output_schema_derivation"]["median_warm_read_ms"] = median_read_ms(
        lambda: derive_workflow_output_schemas(plain_config), repeat
    )

    with LineProbe("_compute_dominators") as probe:
        diagnostics = validate_workflow_binding_diagnostics(reverse_chain(nodes))
    samples["binding_dominators"] = {
        "python_line_events": probe.line_events,
        "output_digest": digest([item.model_dump() for item in diagnostics]),
    }
    config = reverse_chain(nodes)
    samples["binding_dominators"]["median_warm_read_ms"] = median_read_ms(
        lambda: validate_workflow_binding_diagnostics(config), repeat
    )
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=int, default=5000)
    parser.add_argument("--nodes", type=int, default=150)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.history < 1 or not 1 <= args.nodes <= 500 or args.repeat < 1:
        parser.error("history/repeat must be positive; nodes must be between 1 and 500")
    with tempfile.TemporaryDirectory(prefix="evander-read-benchmark-") as directory:
        report = {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "source": str(Path(evander.__file__).resolve().parent),
            "history": args.history,
            "nodes": args.nodes,
            "timing_repeats": args.repeat,
            "samples": run(Path(directory), args.history, args.nodes, args.repeat),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
