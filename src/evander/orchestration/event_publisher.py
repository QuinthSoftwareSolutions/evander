"""Reuse lazy event stores across provider invocations without sharing connections."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from typing import Any

from evander.events.internal import InternalEventBus
from evander.events.scheduled import ScheduledInternalEventStore
from evander.orchestration.internal_events import (
    build_internal_event_bus_from_metadata,
    build_scheduled_internal_event_store_from_metadata,
)


class InternalEventPublisherRuntime:
    def __init__(self, *, runtime_dir: Path | str, metadata: Mapping[str, Any] | None = None):
        self.runtime_dir = Path(runtime_dir)
        self.metadata = dict(metadata or {})
        self._lock = Lock()
        self._bus: InternalEventBus | None = None
        self._scheduled: ScheduledInternalEventStore | None = None

    @staticmethod
    def configuration_key(*, runtime_dir: Path | str, metadata: Mapping[str, Any]) -> tuple:
        runtime = Path(runtime_dir).resolve()
        return (
            runtime,
            Path(
                metadata.get("orchestration_store_path") or runtime / "orchestration.db"
            ).resolve(),
            Path(
                metadata.get("trigger_lock_path") or runtime / "trigger-orchestrator.lock"
            ).resolve(),
            metadata.get("orchestration_retention_days"),
            metadata.get("orchestration_compact_history_retention_days"),
        )

    def publish(self, **kwargs: Any) -> Any:
        with self._lock:
            if self._bus is None:
                self._bus = build_internal_event_bus_from_metadata(
                    self.metadata, runtime_dir=self.runtime_dir
                )
            bus = self._bus
        return bus.publish(**kwargs)

    def schedule_event(self, **kwargs: Any) -> Any:
        with self._lock:
            if self._scheduled is None:
                self._scheduled = build_scheduled_internal_event_store_from_metadata(
                    self.metadata, runtime_dir=self.runtime_dir
                )
            scheduled = self._scheduled
        return scheduled.schedule_event(**kwargs)
