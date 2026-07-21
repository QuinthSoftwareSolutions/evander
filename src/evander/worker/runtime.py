from __future__ import annotations

from typing import Any, Protocol, Sequence

from evander.codex_auth import CodexTurnSpec

from .models import WorkerJobRecord, WorkerJobRequest, WorkerSessionPaths


class ContainerRuntime(Protocol):
    def start_job(
        self,
        request: WorkerJobRequest,
        record: WorkerJobRecord,
        paths: WorkerSessionPaths,
    ) -> str: ...

    def inspect_job(self, record: WorkerJobRecord) -> dict[str, Any]: ...

    def stop_job(self, record: WorkerJobRecord) -> None: ...

    def remove_job(self, record: WorkerJobRecord) -> None: ...

    def get_logs(self, record: WorkerJobRecord, *, tail_lines: int | None = None) -> str: ...

    def codex_app_server_command(
        self, record: WorkerJobRecord, turn: CodexTurnSpec
    ) -> Sequence[str]: ...
