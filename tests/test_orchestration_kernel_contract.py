from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from agent_sandbox.orchestration import kernels
from agent_sandbox.orchestration.host_polling import (
    HostWakeNotifier,
    HostWakeSleeper,
    wake_socket_path_for_lock,
)
from agent_sandbox.orchestration.kernels import PollingKernelConfig, PollingOrchestratorKernel


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class _FakeLockManager:
    def __init__(self) -> None:
        self.paths: list[object] = []

    def lock(self, path: object):
        self.paths.append(path)
        return _FakeLock()


class _FakeSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        raise StopIteration


def test_polling_kernel_runs_cycle_with_lock() -> None:
    lock_manager = _FakeLockManager()
    sleeper = _FakeSleeper()
    calls: list[int] = []

    def _run_cycle() -> int:
        calls.append(1)
        return 5

    kernel = PollingOrchestratorKernel(
        config=PollingKernelConfig(
            poll_interval_seconds=1, jitter_seconds=0, lock_path="worker.lock"
        ),
        lock_manager=lock_manager,
        sleeper=sleeper,
        run_cycle=_run_cycle,
        get_sleep_seconds=lambda result: float(result),
    )

    assert kernel.run_once_with_lock() == 5
    assert lock_manager.paths == ["worker.lock"]

    try:
        kernel.run_forever()
    except StopIteration:
        pass
    assert calls
    assert sleeper.calls == [5.0]


def test_polling_kernel_backoffs_after_cycle_exception() -> None:
    lock_manager = _FakeLockManager()

    class _CycleState:
        def __init__(self) -> None:
            self.calls = 0
            self.exceptions: list[str] = []
            self.successes: list[int] = []
            self.starts = 0

        def run_cycle(self) -> int:
            self.calls += 1
            if self.calls == 1:
                raise ValueError("cycle failed")
            return 7

    state = _CycleState()

    class _RecordingSleeper:
        def __init__(self) -> None:
            self.calls: list[float] = []

        def sleep(self, seconds: float) -> None:
            self.calls.append(seconds)
            if len(self.calls) == 2:
                raise StopIteration

    sleeper = _RecordingSleeper()
    kernel = PollingOrchestratorKernel(
        config=PollingKernelConfig(
            poll_interval_seconds=1, jitter_seconds=0, lock_path="worker.lock"
        ),
        lock_manager=lock_manager,
        sleeper=sleeper,
        run_cycle=state.run_cycle,
        get_sleep_seconds=lambda result: float(result),
        on_start=lambda: setattr(state, "starts", state.starts + 1),
        on_cycle_success=state.successes.append,
        on_cycle_exception=lambda exc: state.exceptions.append(str(exc)),
        get_exception_sleep_seconds=lambda exc: 9.0,
    )

    with pytest.raises(StopIteration):
        kernel.run_forever()

    assert state.starts == 1
    assert state.exceptions == ["cycle failed"]
    assert state.successes == [7]
    assert sleeper.calls == [9.0, 7.0]


def test_polling_kernel_starts_only_after_lock_is_acquired() -> None:
    starts: list[str] = []

    class _FailingLockManager:
        def lock(self, path: object):
            raise RuntimeError("lock held")

    kernel = PollingOrchestratorKernel(
        config=PollingKernelConfig(
            poll_interval_seconds=1, jitter_seconds=0, lock_path="worker.lock"
        ),
        lock_manager=_FailingLockManager(),
        sleeper=_FakeSleeper(),
        run_cycle=lambda: 1,
        get_sleep_seconds=lambda result: float(result),
        on_start=lambda: starts.append("started"),
    )

    with pytest.raises(RuntimeError, match="lock held"):
        kernel.run_forever()

    assert starts == []


def test_polling_kernel_run_once_with_lock_propagates_exceptions() -> None:
    lock_manager = _FakeLockManager()

    def _run_cycle() -> int:
        raise RuntimeError("boom")

    kernel = PollingOrchestratorKernel(
        config=PollingKernelConfig(
            poll_interval_seconds=1, jitter_seconds=0, lock_path="worker.lock"
        ),
        lock_manager=lock_manager,
        sleeper=_FakeSleeper(),
        run_cycle=_run_cycle,
        get_sleep_seconds=lambda result: float(result),
    )

    with pytest.raises(RuntimeError, match="boom"):
        kernel.run_once_with_lock()


def test_compute_next_sleep_seconds_uses_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kernels.random, "uniform", lambda lower, upper: 0.25)

    assert kernels.compute_next_sleep_seconds(
        poll_interval_seconds=3.0, jitter_seconds=2.0
    ) == 3.25
    assert kernels.compute_next_sleep_seconds(
        poll_interval_seconds=3.0, jitter_seconds=0.0
    ) == 3.0


def test_wake_socket_path_is_derived_from_lock_path(tmp_path: Path) -> None:
    assert wake_socket_path_for_lock(tmp_path / "effect-orchestrator.lock") == (
        tmp_path / "effect-orchestrator.wake.sock"
    )


def test_host_wake_sleeper_returns_when_notified(tmp_path: Path) -> None:
    wake_path = tmp_path / "worker.wake.sock"
    sleeper = HostWakeSleeper(wake_path)
    sleeper.start()
    try:
        assert HostWakeNotifier(wake_path).notify() is True
        started = time.monotonic()
        sleeper.sleep(10.0)
    finally:
        sleeper.close()

    assert time.monotonic() - started < 0.5


def test_host_wake_notifier_noops_when_receiver_is_missing(tmp_path: Path) -> None:
    assert HostWakeNotifier(tmp_path / "missing.wake.sock").notify() is False


def test_kernel_module_has_no_platform_specific_imports() -> None:
    source = Path("src/agent_sandbox/orchestration/kernels.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden_prefixes = (
        "agent_sandbox.admin.composition",
        "agent_sandbox.platform_integrations",
        "agent_sandbox.provider_config",
    )
    assert all(
        not any(name.startswith(prefix) for prefix in forbidden_prefixes) for name in imported
    )
