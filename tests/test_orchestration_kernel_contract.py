from __future__ import annotations

import ast
from pathlib import Path

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
        "agent_sandbox.admin.service",
        "agent_sandbox.platform_integrations",
        "agent_sandbox.provider_config",
    )
    assert all(
        not any(name.startswith(prefix) for prefix in forbidden_prefixes) for name in imported
    )
