from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evander.config import TriggerOrchestratorWorkerGlobalConfig
from evander.repositories.orchestration_db import (
    OrchestrationWalCheckpointResult,
    maybe_checkpoint_orchestration_wal,
)


def sqlite_orchestration_store_enabled(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> bool:
    return (
        worker_config.orchestration_store_backend == "sqlite"
        and "orchestration_store_backend" in worker_config.model_fields_set
    )


@dataclass
class OrchestrationWalCheckpointMaintenance:
    clock: Callable[[], float] = time.monotonic
    _next_check_at: float = 0.0
    _last_settings: tuple[Any, ...] | None = None

    def run_if_due(
        self,
        worker_config: TriggerOrchestratorWorkerGlobalConfig,
    ) -> OrchestrationWalCheckpointResult | None:
        if not sqlite_orchestration_store_enabled(worker_config):
            self._last_settings = None
            self._next_check_at = 0.0
            return None

        settings = (
            Path(worker_config.orchestration_store_path),
            worker_config.orchestration_wal_checkpoint_mode,
            int(worker_config.orchestration_wal_checkpoint_min_bytes),
            int(worker_config.orchestration_wal_checkpoint_interval_seconds),
            int(worker_config.orchestration_wal_checkpoint_busy_timeout_ms),
        )
        now = self.clock()
        if settings != self._last_settings:
            self._last_settings = settings
            self._next_check_at = 0.0
        if now < self._next_check_at:
            return None

        interval_seconds = settings[3]
        self._next_check_at = now + interval_seconds if interval_seconds > 0 else now
        return maybe_checkpoint_orchestration_wal(
            settings[0],
            mode=settings[1],
            min_bytes=settings[2],
            interval_seconds=interval_seconds,
            busy_timeout_ms=settings[4],
        )
