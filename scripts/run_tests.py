#!/usr/bin/env python3
"""Run every selected Python case, isolating wall-clock contracts from parallel work."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_PATHS = tomllib.loads((ROOT / "pyproject.toml").read_text())[
    "tool"
]["pytest"]["ini_options"]["testpaths"]


class SuiteCancelled(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum


def default_workers() -> int:
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    return max(1, min(4, cpus or 1))


def phases(workers: int) -> list[tuple[str, list[str]]]:
    if workers == 0:
        return [("serial", [])]
    return [
        ("parallel", ["-n", str(workers), "--dist=worksteal", "-m", "not performance"]),
        ("performance", ["-n", "0", "-m", "performance"]),
    ]


def check_execution(ledgers: list[dict]) -> dict[str, int]:
    """Verify that the lanes partition collection and actually finish every case."""
    expected: set[str] | None = None
    selected: set[str] = set()
    completed: set[str] = set()
    outcomes: Counter[str] = Counter()
    for ledger in ledgers:
        collection = ledger["collected"]
        if len(collection) != len(set(collection)):
            raise ValueError("A lane collected a test more than once.")
        all_cases = set(collection) | set(ledger["deselected"])
        if expected is not None and all_cases != expected:
            raise ValueError("Test collection changed between lanes.")
        expected = all_cases
        overlap = selected & set(collection)
        if overlap:
            raise ValueError(f"Tests ran in multiple lanes: {sorted(overlap)[:5]}")
        selected.update(collection)
        for nodeid, reports in ledger["tests"].items():
            terminal = [
                report
                for report in reports
                if report["phase"] == "call" or report["outcome"] in {"failed", "skipped"}
            ]
            if terminal:
                completed.add(nodeid)
                if any(report["outcome"] == "failed" for report in reports):
                    outcomes["failed"] += 1
                elif any(report["outcome"] == "skipped" for report in reports):
                    outcomes["skipped"] += 1
                else:
                    outcomes["passed"] += 1
    missing = (expected or set()) - selected
    if missing:
        raise ValueError(f"Tests omitted by lane selection: {sorted(missing)[:5]}")
    if completed != selected:
        raise ValueError(
            f"Tests without terminal results: {sorted(selected - completed)[:5]}; "
            f"unexpected results: {sorted(completed - selected)[:5]}"
        )
    if not expected:
        raise ValueError("No tests were collected.")
    return {"collected": len(selected), **dict(outcomes)}


def stop_process_group(process: subprocess.Popen) -> None:
    """Also stop xdist, Node, and fixture descendants when a deadline expires."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait()
    # The group can outlive its leader if a fixture ignores SIGTERM.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_command(command: list[str], env: dict[str, str], timeout: float) -> int:
    def cancelled(signum, _frame):
        raise SuiteCancelled(signum)

    previous = signal.signal(signal.SIGTERM, cancelled)
    previous_interrupt = signal.getsignal(signal.SIGINT)
    process = None
    try:
        process = subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=True)
        return process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt, SuiteCancelled):
        # A second termination request must not interrupt descendant cleanup.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if process is not None:
            stop_process_group(process)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)
        signal.signal(signal.SIGINT, previous_interrupt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workers", type=int, default=default_workers(), help="0 runs all cases serially"
    )
    parser.add_argument("--timeout", type=float, default=900, help="total deadline in seconds")
    parser.add_argument(
        "--artifacts-dir", type=Path, help="preserve ledgers and conformance reports here"
    )
    parser.add_argument("paths", nargs="*", default=DEFAULT_TEST_PATHS, help="pytest paths or node IDs")
    args = parser.parse_args(argv)
    if args.workers < 0 or args.timeout <= 0:
        parser.error("workers must be nonnegative and timeout must be positive")
    artifacts = (
        args.artifacts_dir or ROOT / ".pytest_cache" / "test-runs" / uuid.uuid4().hex
    ).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "summary.json").unlink(missing_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    # A nested invocation should not inherit another worker's identity.
    for name in ("PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT", "PYTEST_XDIST_TESTRUNUID"):
        env.pop(name, None)
    started = time.monotonic()
    ledgers = []
    exitcode = 0
    full_selection = args.paths == ["tests"] or args.paths == DEFAULT_TEST_PATHS
    for name, options in phases(args.workers):
        ledger_path = artifacts / f"{name}.json"
        ledger_path.unlink(missing_ok=True)
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--strict-markers",
            "--durations=10",
            f"--execution-ledger={ledger_path}",
            f"--conformance-output-dir={artifacts / 'conformance'}",
            *options,
            *args.paths,
        ]
        if full_selection and name != "performance":
            command.extend(["--require-conformance=effect", "--require-conformance=trigger"])
        print(f"\n=== {name} lane ===", flush=True)
        remaining = args.timeout - (time.monotonic() - started)
        if remaining <= 0:
            print(f"Suite exceeded its {args.timeout:g}s deadline.", file=sys.stderr)
            return 124
        try:
            result = run_command(command, env, remaining)
        except subprocess.TimeoutExpired:
            print(f"Suite exceeded its {args.timeout:g}s deadline during {name}.", file=sys.stderr)
            return 124
        except KeyboardInterrupt:
            return 130
        except SuiteCancelled as error:
            return 128 + error.signum
        if not ledger_path.is_file():
            print(f"{name} did not produce its execution ledger.", file=sys.stderr)
            return result or 1
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledgers.append(ledger)
        if result == 5 and not ledger["collected"]:
            result = 0  # A focused selection may contain no performance cases.
        if result not in (0, 1):
            return result
        exitcode = max(exitcode, result)
    try:
        summary = check_execution(ledgers)
    except ValueError as error:
        print(f"Incomplete test execution: {error}", file=sys.stderr)
        return 1
    summary["seconds"] = round(time.monotonic() - started, 3)
    summary["workers"] = args.workers
    summary["exitstatus"] = exitcode
    (artifacts / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("\nComplete selection: " + json.dumps(summary), flush=True)
    print(f"Execution evidence: {artifacts}", flush=True)
    return exitcode


if __name__ == "__main__":
    raise SystemExit(main())
