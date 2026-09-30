#!/usr/bin/env python3
"""Compare Team wake/completion memory with synthetic archived case evidence.

Run this script with the same Python environment and --source pointing at each
checkout. Seeding and measurement use separate, resource-capped subprocesses.
Only temporary synthetic databases are written; no configured runtime is opened.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time


def seed(runtime: Path, archive_mib: float) -> dict:
    from evander_team_addon.case_context_models import CaseContextSnapshot, CaseContextView
    from evander_team_addon.repository import TeamStateRepository
    from evander_team_addon.services.admin_service import TeamAdminService

    repo = TeamStateRepository(runtime)
    TeamAdminService(repo).create_team(
        team_id="benchmark-team",
        name="Benchmark team",
        manager_agent_id="manager",
        manager_session_id="benchmark-session",
    )
    record = {
        "entity_type": "note",
        "id": "synthetic-note",
        "content": {
            "body": "Synthetic evidence for bounded history memory measurement. " * 2,
            "tags": ["design", "verification"],
            "source": {"kind": "synthetic", "version": 1},
        },
    }
    snapshot = CaseContextSnapshot(
        id="template",
        request_id="template",
        request_digest="synthetic",
        directory_path="root",
        team_ids=["benchmark-team"],
        case_ids=["archived-case"],
        dispatch_id="archived-dispatch",
        source_revision=1,
        view=CaseContextView(
            id="view", view_id="task-brief", title="Brief", sections=[{"name": "request"}]
        ),
        content_digest="synthetic",
        context={"text": "Archived brief", "complete": True},
        evidence={"request": [record] * 750},
    ).model_dump(mode="json")
    total = 0
    count = 0
    with repo.store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        while total < archive_mib * 1024 * 1024:
            snapshot["id"] = snapshot["request_id"] = f"archive-{count:06}"
            serialized = json.dumps(snapshot)
            connection.execute(
                "INSERT INTO team_case_context_snapshots VALUES (?, ?)",
                (snapshot["id"], serialized),
            )
            total += len(serialized.encode())
            count += 1
    # Complete migration/indexing before the measurement subprocess starts.
    TeamStateRepository(runtime)
    return {"archive_bytes": total, "snapshot_count": count}


def measure(runtime: Path, iterations: int) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    from evander_team_addon.provider_completion import TeamAgentPromptCompletionFinalizeHook
    from evander_team_addon.repository import TeamStateRepository
    from evander_team_addon.services.chat_service import TeamChatService

    barrier = Barrier(2)
    worker = SimpleNamespace(worker_config=SimpleNamespace(runtime_dir=runtime))

    def run(kind: str) -> None:
        repo = TeamStateRepository(runtime)
        completion = TeamAgentPromptCompletionFinalizeHook()
        for _ in range(iterations):
            barrier.wait(timeout=120)
            if kind == "wake":
                _, claims = TeamChatService(repo).claim_pending_chat_message_dispatches(
                    agent_id="absent"
                )
                assert not claims
            else:
                assert (
                    completion._reconcile_selected_completion(
                        worker=worker, repo=repo, dispatch_ids=set(), records=[]
                    )
                    == 0
                )

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, ("wake", "completion")))
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    return {
        "peak_rss_mib": max(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, int(status["VmHWM"].split()[0])
        )
        / 1024,
        "retained_rss_mib": int(status["VmRSS"].split()[0]) / 1024,
        "elapsed_seconds": time.monotonic() - started,
        "iterations": iterations,
        "concurrent_workers": 2,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive-mib", type=float, default=86)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--memory-limit-mib", type=int, default=3072)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--phase", choices=("seed", "measure"), help=argparse.SUPPRESS)
    parser.add_argument("--runtime", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.archive_mib < 0 or args.iterations < 1:
        parser.error("archive size must be nonnegative and iterations must be positive")
    if args.phase:
        cap = args.memory_limit_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        resource.setrlimit(resource.RLIMIT_CPU, (args.timeout, args.timeout))
        sys.path.insert(0, str(args.source.resolve() / "src"))
        result = (
            seed(args.runtime, args.archive_mib)
            if args.phase == "seed"
            else measure(args.runtime, args.iterations)
        )
        print(json.dumps(result))
        return
    results = {"source": str(args.source.resolve())}
    with tempfile.TemporaryDirectory(prefix="team-history-benchmark-") as runtime:
        for phase in ("seed", "measure"):
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--source",
                str(args.source.resolve()),
                "--archive-mib",
                str(args.archive_mib),
                "--iterations",
                str(args.iterations),
                "--memory-limit-mib",
                str(args.memory_limit_mib),
                "--timeout",
                str(args.timeout),
                "--phase",
                phase,
                "--runtime",
                runtime,
            ]
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=args.timeout,
                env={**os.environ, "PYTHONHASHSEED": "0"},
            )
            results.update(json.loads(completed.stdout))
    print(json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    main()
