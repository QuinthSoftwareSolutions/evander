from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "evander_test_runner",
    Path(__file__).resolve().parents[1] / "scripts" / "run_tests.py",
)
assert _spec and _spec.loader
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _ledger(selected, deselected=(), *, outcome="passed"):
    return {
        "collected": list(selected),
        "deselected": list(deselected),
        "tests": {nodeid: [{"phase": "call", "outcome": outcome}] for nodeid in selected},
    }


def test_complete_runner_includes_parallel_and_performance_cases():
    assert runner.phases(4) == [
        ("parallel", ["-n", "4", "--dist=worksteal", "-m", "not performance"]),
        ("performance", ["-n", "0", "-m", "performance"]),
    ]
    assert runner.phases(0) == [("serial", [])]
    assert runner.check_execution(
        [
            _ledger(["normal"], ["timed"]),
            _ledger(["timed"], ["normal"]),
        ]
    ) == {"collected": 2, "passed": 2}


@pytest.mark.parametrize(
    ("ledgers", "message"),
    [
        ([_ledger([])], "No tests were collected"),
        ([_ledger(["one", "one"])], "collected a test more than once"),
        ([_ledger(["one"], ["two"]), _ledger(["two"], ["three"])], "collection changed"),
        ([_ledger(["one"]), _ledger(["one"])], "multiple lanes"),
        ([_ledger(["one"], ["missing"])], "omitted by lane selection"),
        ([{"collected": ["one"], "deselected": [], "tests": {}}], "without terminal results"),
        (
            [
                {
                    "collected": ["one"],
                    "deselected": [],
                    "tests": {
                        "one": [{"phase": "setup", "outcome": "passed"}],
                    },
                }
            ],
            "without terminal results",
        ),
        (
            [
                {
                    "collected": [],
                    "deselected": [],
                    "tests": {
                        "extra": [{"phase": "call", "outcome": "passed"}],
                    },
                }
            ],
            "unexpected results",
        ),
    ],
)
def test_execution_ledger_rejects_missing_or_repeated_work(ledgers, message):
    with pytest.raises(ValueError, match=message):
        runner.check_execution(ledgers)


def test_execution_ledger_includes_setup_and_teardown_failures():
    ledger = _ledger(["setup", "teardown", "skipped"])
    ledger["tests"]["setup"] = [{"phase": "setup", "outcome": "failed"}]
    ledger["tests"]["teardown"].append({"phase": "teardown", "outcome": "failed"})
    ledger["tests"]["skipped"] = [{"phase": "setup", "outcome": "skipped"}]
    assert runner.check_execution([ledger]) == {"collected": 3, "failed": 2, "skipped": 1}


def test_execution_ledger_accepts_empty_performance_lane():
    assert runner.check_execution([_ledger(["one"]), _ledger([], ["one"])]) == {
        "collected": 1,
        "passed": 1,
    }


@pytest.mark.parametrize("cpus, expected", [(1, 1), (2, 2), (48, 4)])
def test_default_worker_count_is_bounded(monkeypatch, cpus, expected):
    monkeypatch.setattr(runner.os, "sched_getaffinity", lambda _: set(range(cpus)))
    assert runner.default_workers() == expected


def test_deadline_stops_worker_group_and_remaining_descendants(monkeypatch):
    signals = []
    waits = []
    monkeypatch.setattr(runner.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    process = SimpleNamespace(pid=123, wait=lambda **kwargs: waits.append(kwargs))
    runner.stop_process_group(process)
    assert signals == [(123, runner.signal.SIGTERM), (123, runner.signal.SIGKILL)]
    assert waits == [{"timeout": 5}]


def test_deadline_kills_worker_that_ignores_termination(monkeypatch):
    signals = []
    calls = []
    monkeypatch.setattr(runner.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    def wait(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise runner.subprocess.TimeoutExpired("worker", 5)

    runner.stop_process_group(SimpleNamespace(pid=123, wait=wait))
    assert signals == [
        (123, runner.signal.SIGTERM),
        (123, runner.signal.SIGKILL),
        (123, runner.signal.SIGKILL),
    ]
    assert calls == [{"timeout": 5}, {}]


def test_repeated_interrupt_cannot_abort_descendant_cleanup(monkeypatch):
    previous = {signal.SIGTERM: object(), signal.SIGINT: object()}
    handlers = dict(previous)

    def set_handler(signum, handler):
        old = handlers[signum]
        handlers[signum] = handler
        return old

    def interrupted_wait(**_):
        raise KeyboardInterrupt

    process = SimpleNamespace(wait=interrupted_wait)
    stopped = []

    def stop(received):
        assert handlers == {signal.SIGTERM: signal.SIG_IGN, signal.SIGINT: signal.SIG_IGN}
        stopped.append(received)

    monkeypatch.setattr(runner.signal, "signal", set_handler)
    monkeypatch.setattr(runner.signal, "getsignal", handlers.__getitem__)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runner, "stop_process_group", stop)
    with pytest.raises(KeyboardInterrupt):
        runner.run_command(["pytest"], {}, 30)
    assert stopped == [process]
    assert handlers == previous


def test_sigterm_reaps_descendant_that_ignores_termination(tmp_path):
    pid_path = tmp_path / "descendant.pid"
    child = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(pid_path)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    worker = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
    )
    parent = (
        "import importlib.util,os,sys; "
        f"s=importlib.util.spec_from_file_location('runner', {str(Path(runner.__file__))!r}); "
        "r=importlib.util.module_from_spec(s); s.loader.exec_module(r); "
        f"r.run_command([sys.executable, '-c', {worker!r}], os.environ.copy(), 30)"
    )
    process = subprocess.Popen([sys.executable, "-c", parent], stderr=subprocess.DEVNULL)
    descendant = None
    try:
        deadline = time.monotonic() + 5
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_path.exists(), "descendant did not start"
        descendant = int(pid_path.read_text())
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=10)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            stat = Path(f"/proc/{descendant}/stat")
            if not stat.exists() or stat.read_text().split(")", 1)[1].split()[0] == "Z":
                break
            time.sleep(0.01)
        else:
            pytest.fail("cancelled runner left its descendant alive")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if descendant is not None:
            try:
                os.kill(descendant, signal.SIGKILL)
            except ProcessLookupError:
                pass
