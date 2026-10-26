import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from evander.resources.reaper import run_cleanup_loop


class ClockStop:
    def __init__(self):
        self.stopped = False
        self.now = 0
        self.waits = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, delay):
        self.waits.append(delay)
        self.now += delay
        return self.stopped


def test_serial_cleanup_retries_without_catchup_or_exposing_provider_error(caplog):
    stop = ClockStop()
    calls = []

    def tick():
        calls.append(stop.now)
        stop.now += 7  # A pass slower than the interval must not cause catch-up work.
        if len(calls) == 1:
            raise RuntimeError("synthetic provider password")
        stop.set()

    with caplog.at_level("INFO"):
        run_cleanup_loop(tick, stop=stop)
    assert calls == [0, 12]
    assert stop.waits == [5, 5]
    assert "failed" in caplog.text and "completed" in caplog.text
    assert "synthetic provider password" not in caplog.text


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_cleanup_signal_interrupts_wait_and_exits_cleanly(signum):
    script = """
from evander.resources.reaper import run_cleanup_loop
run_cleanup_loop(lambda: print('ready', flush=True), interval=60)
print('stopped', flush=True)
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    with subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True, env=env
    ) as child:
        try:
            assert child.stdout.readline().strip() == "ready"
            child.send_signal(signum)
            assert child.communicate(timeout=3)[0].strip() == "stopped"
            assert child.returncode == 0
        finally:
            if child.poll() is None:
                child.kill()


def test_cleanup_restores_signal_handlers():
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    stop = ClockStop()
    run_cleanup_loop(stop.set, stop=stop)
    assert {s: signal.getsignal(s) for s in previous} == previous


def test_shutdown_finishes_in_flight_pass_before_exit():
    completed = []

    def tick():
        os.kill(os.getpid(), signal.SIGTERM)
        completed.append(True)

    run_cleanup_loop(tick)
    assert completed == [True]


@pytest.mark.parametrize("mode,expected_ticks", [("--reap", 1), ("--reap-loop", 2)])
def test_resource_cli_constructs_service_once_for_cleanup(
    tmp_path, monkeypatch, mode, expected_ticks
):
    from evander.resources import reaper, worker

    config = tmp_path / "worker.json"
    config.write_text(
        json.dumps(
            {"worker_id": "worker-a", "runtime_dir": str(tmp_path), "token_env": "TEST_TOKEN"}
        )
    )
    constructed, ticks = [], []

    class Service:
        def __init__(self, *args):
            constructed.append(args)

        def tick(self):
            ticks.append(True)

    monkeypatch.setattr(worker, "WorkerService", Service)
    monkeypatch.setattr(reaper, "run_cleanup_loop", lambda tick: (tick(), tick()))
    monkeypatch.setattr(sys, "argv", ["worker", "--config", str(config), mode])
    worker.main("synthetic-addon", lambda settings: object(), 11900)
    assert len(constructed) == 1
    assert len(ticks) == expected_ticks


def test_resource_cli_rejects_conflicting_modes(monkeypatch):
    from evander.resources.worker import main

    monkeypatch.setattr(sys, "argv", ["worker", "--config", "unused", "--reap", "--reap-loop"])
    with pytest.raises(SystemExit, match="2"):
        main("synthetic-addon", lambda settings: object(), 11900)


def test_worker_store_import_does_not_load_server_components():
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    script = """
import sys
import evander.worker.store
for prefix in ('evander.worker.api', 'evander.worker.service', 'evander.admin', 'evander.gateway', 'mcp'):
    assert not any(n == prefix or n.startswith(prefix + '.') for n in sys.modules), prefix
"""
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "name,module",
    [
        ("WorkerJobRecord", "models"),
        ("WorkerJobStore", "store"),
        ("WorkerDaemon", "service"),
        ("create_app", "api"),
        ("ContainerRuntime", "runtime"),
        ("WorkerSandboxProfile", "sandbox_profile"),
    ],
)
def test_worker_public_exports_preserve_identity(name, module):
    import evander.worker as worker

    assert getattr(worker, name) is getattr(
        importlib.import_module("evander.worker." + module), name
    )
    assert name in dir(worker)


def test_persistent_service_sees_new_leases_and_retries_cleanup_under_contention(tmp_path):
    from types import SimpleNamespace

    from evander.resources.models import Lease, Owner, Profile, WorkerSettings
    from evander.resources.state import Store
    from evander.resources.worker import WorkerService

    stop = ClockStop()
    attempts = []

    def release(lease, save, credentials):
        attempts.append(lease.lease_id)
        if len(attempts) == 1:
            raise RuntimeError("provider temporarily unavailable")
        lease.resources.clear()
        save()

    service = WorkerService(
        "synthetic-addon",
        WorkerSettings(worker_id="worker-a", runtime_dir=str(tmp_path), token_env="TEST_TOKEN"),
        SimpleNamespace(release=release),
        jobs=SimpleNamespace(current=lambda owner: None, unbind=lambda lease, save: None),
        credentials=object(),
        now=lambda: 100,
    )
    other_process_store = Store(tmp_path)
    lease_id = "rs-" + "1" * 32
    states = []
    calls = 0

    def tick():
        nonlocal calls
        calls += 1
        if calls == 2:
            with other_process_store.locked(lease_id):
                service.tick()  # An API operation holding the lease cannot block this pass.
            assert attempts == []
        else:
            service.tick()
        if calls == 1:
            # The long-lived service must discover records written after it started.
            other_process_store.put(
                Lease(
                    lease_id=lease_id,
                    owner=Owner(key="owner-a", directory="root"),
                    profile_id="profile-a",
                    profile=Profile(worker_id="worker-a"),
                    created_at=0,
                    expires_at=50,
                    resources={"owned": True},
                )
            )
        else:
            states.append(other_process_store.get(lease_id).state)
        if calls == 4:
            stop.set()

    run_cleanup_loop(tick, stop=stop)
    assert states == ["provisioning", "cleanup_failed", "released"]
    assert attempts == [lease_id, lease_id]
    assert other_process_store.get(lease_id).resources == {}
