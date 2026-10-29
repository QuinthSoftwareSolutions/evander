from __future__ import annotations

from typing import Protocol

from .models import (
    ExecutionBackendCapabilities,
    ExecutionBackendKind,
    ExecutionHandle,
    ExecutionSpec,
    ExecutionStatus,
    PreparedExecution,
)


ExecutionTarget = str | PreparedExecution | ExecutionHandle


class ExecutionBackend(Protocol):
    @property
    def kind(self) -> ExecutionBackendKind: ...

    @property
    def capabilities(self) -> ExecutionBackendCapabilities: ...

    def prepare(self, spec: ExecutionSpec) -> PreparedExecution: ...

    def start(self, target: ExecutionTarget): ...

    def stop(self, target: ExecutionTarget, *, timeout_seconds: int = 60): ...

    def inspect(self, target: ExecutionTarget) -> ExecutionStatus: ...

    def destroy(self, target: ExecutionTarget): ...

    def get_log_snapshot(
        self,
        target: ExecutionTarget,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str: ...
