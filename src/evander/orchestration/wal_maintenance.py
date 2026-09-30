from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evander.config import TriggerOrchestratorWorkerGlobalConfig
from evander.orchestration.storage_maintenance import run_storage_maintenance
from evander.repositories.orchestration_db import OrchestrationWalCheckpointResult

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationWalCheckpointMaintenance:
    """Single-flight storage lane; the dispatch thread only submits/observes."""

    clock: Callable[[], float] = time.monotonic
    _next_check_at: float = 0.0
    _next_garbage_collection_at: float = 0.0
    _last_settings: tuple[Any, ...] | None = None
    _executor: ThreadPoolExecutor | None = field(default=None, init=False, repr=False)
    _future: Future | None = field(default=None, init=False, repr=False)
    _wake_callback: Callable[[], None] | None = field(default=None, init=False, repr=False)

    def set_wake_callback(self, callback: Callable[[], None]) -> None:
        self._wake_callback = callback

    def _notify(self, _future: Future) -> None:
        if self._wake_callback is not None:
            try:
                self._wake_callback()
            except Exception:
                logger.debug("Storage maintenance wake unavailable", exc_info=True)

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True)
        self._executor = None
        self._future = None
        self._next_check_at = 0.0
        self._next_garbage_collection_at = 0.0

    def run_if_due(
        self,
        worker_config: TriggerOrchestratorWorkerGlobalConfig,
    ) -> OrchestrationWalCheckpointResult | None:
        result = None
        if self._future is not None:
            if not self._future.done():
                return None
            try:
                result = self._future.result()
            except Exception as error:
                logger.warning("Storage maintenance deferred", exc_info=True)
                result = OrchestrationWalCheckpointResult(
                    error=str(error), skipped_reason="maintenance_failed"
                )
            finally:
                self._future = None
            if result.skipped_reason == "storage_busy":
                self._next_check_at = self.clock() + 1.0
            elif result.history_index_pending:
                self._next_check_at = 0.0
            if result.garbage_collection_pending is False:
                self._next_garbage_collection_at = self.clock() + 30.0
            elif result.garbage_collection_pending is True:
                self._next_garbage_collection_at = self.clock() + 1.0
        settings = (
            Path(worker_config.orchestration_store_path),
            worker_config.orchestration_wal_checkpoint_mode,
            int(worker_config.orchestration_wal_checkpoint_min_bytes),
            int(worker_config.orchestration_wal_checkpoint_interval_seconds),
            int(worker_config.orchestration_wal_checkpoint_busy_timeout_ms),
            int(worker_config.orchestration_retention_days),
            int(worker_config.orchestration_compact_history_retention_days),
        )
        now = self.clock()
        if settings != self._last_settings:
            self._last_settings = settings
            self._next_check_at = 0.0
            self._next_garbage_collection_at = 0.0
        if now < self._next_check_at:
            return result
        # History projections and retention progress independently of the
        # five-minute checkpoint interval and disabled checkpoint setting.
        self._next_check_at = now + 1.0
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="storage-maintenance"
            )
        self._future = self._executor.submit(
            run_storage_maintenance,
            copy.copy(worker_config),
            run_garbage_collection=now >= self._next_garbage_collection_at,
        )
        self._future.add_done_callback(self._notify)
        return result
