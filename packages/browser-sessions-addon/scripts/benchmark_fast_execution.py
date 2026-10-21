"""Opt-in, paired end-to-end PA benchmark; requires an explicit operator driver."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import time
from pathlib import Path

# The driver resets the same synthetic application before every attempt and reads
# its actual persisted result. These expected results are independent of PA text.
CASES = [
    {
        "id": "create-account",
        "goal": "Create the account Example and save it",
        "values": {"name": "Example"},
        "expected_state": {"account_name": "Example", "saved": True},
    },
    {
        "id": "edit-matching-row",
        "goal": "Rename account B to Updated; keep account A unchanged",
        "values": {"name": "Updated"},
        "expected_state": {"accounts": {"A": "Account A", "B": "Updated"}},
    },
    {
        "id": "configure-account",
        "goal": "Set the region to South, enable the account, and save it",
        "values": {},
        "expected_state": {"region": "South", "enabled": True, "saved": True},
    },
]
STRATEGIES = ("direct", "jev", "openai_decisions")


def summarize(samples):
    groups = {}
    for sample in samples:
        groups.setdefault(sample["strategy"], []).append(sample)
    results = {}
    for strategy, rows in groups.items():
        times = sorted(r["elapsed_seconds"] for r in rows)
        results[strategy] = {
            "attempts": len(rows),
            "success_rate": sum(r["success"] for r in rows) / len(rows),
            "median_seconds": statistics.median(times),
            "p95_seconds": times[math.ceil(len(times) * 0.95) - 1],
            **{
                key: sum(r[key] for r in rows)
                for key in ("model_calls", "browser_calls", "cost_usd")
            },
        }
    return results


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Driver receives one JSON request on stdin and must emit one JSON result. See docs/fast-execution.md. No live calls occur without an explicit driver.",
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Operator-owned JSON report path outside the repository",
    )
    parser.add_argument(
        "driver",
        nargs=argparse.REMAINDER,
        help="Driver argv, after --; executed directly, without a shell",
    )
    args = parser.parse_args()
    driver = args.driver[1:] if args.driver[:1] == ["--"] else args.driver
    if not driver or args.repeats < 1 or not 0 < args.timeout <= 600:
        parser.error(
            "Supply an explicit PA driver, positive repeats, and a timeout up to 600 seconds"
        )
    samples = []
    for iteration in range(args.repeats):
        # Rotate order to reduce cache/warm-up bias while retaining exact cases.
        strategies = STRATEGIES[iteration % 3 :] + STRATEGIES[: iteration % 3]
        for case in CASES:
            for strategy in strategies:
                request = {
                    "case_id": case["id"],
                    "goal": case["goal"],
                    "values": case["values"],
                    "strategy": strategy,
                    "iteration": iteration,
                    "reset": True,
                    "limits": {"max_steps": 20, "timeout_seconds": 30},
                }
                started = time.monotonic()
                completed = subprocess.run(
                    driver,
                    input=json.dumps(request),
                    capture_output=True,
                    text=True,
                    timeout=args.timeout,
                    check=True,
                )
                receipt = json.loads(completed.stdout)
                telemetry = {
                    key: receipt[key] for key in ("model_calls", "browser_calls", "cost_usd")
                }
                if any(
                    type(v) not in (int, float) or not math.isfinite(v) or v < 0
                    for v in telemetry.values()
                ):
                    raise ValueError("Driver telemetry must be finite, non-negative numbers")
                samples.append(
                    {
                        "case_id": case["id"],
                        "iteration": iteration,
                        "strategy": strategy,
                        "elapsed_seconds": time.monotonic() - started,
                        "success": receipt.get("verified_state") == case["expected_state"],
                        **telemetry,
                    }
                )
    report = {"samples": samples, "summary": summarize(samples)}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
