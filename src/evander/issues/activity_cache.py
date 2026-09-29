"""Bounded, gateway-owned reuse of completed ADO activity scans."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
import logging
import secrets
from threading import Lock
import time
from typing import Any

LOGGER = logging.getLogger(__name__)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return normalized.isoformat().replace("+00:00", "Z")
    raise TypeError(f"Unsupported activity value type: {type(value).__name__}")


def _encode(value: Any) -> bytes:
    return json.dumps(value, default=_json_default, separators=(",", ":")).encode("utf-8")


def _page(
    metadata: dict[str, Any], items: list[dict[str, Any]], *, top: int, skip: int
) -> dict[str, Any]:
    paging = metadata.pop("paging")
    total = paging["total_matches"]
    next_skip = skip + len(items) if skip + len(items) < total else None
    paging.update(
        skip=skip,
        top=top,
        returned_count=len(items),
        next_skip=next_skip,
        has_more=next_skip is not None or not paging["candidate_scan_complete"],
    )
    return {**metadata, "items": items, "count": len(items), "paging": paging}


@dataclass
class ActivityScanResult:
    items: list[dict[str, Any]]
    metadata: dict[str, Any]

    def page(self, *, top: int, skip: int) -> dict[str, Any]:
        return _page(
            deepcopy(self.metadata), deepcopy(self.items[skip : skip + top]), top=top, skip=skip
        )


@dataclass(frozen=True)
class _Snapshot:
    items: tuple[bytes, ...]
    metadata: bytes
    size_bytes: int

    @classmethod
    def from_scan(cls, scan: ActivityScanResult, budget: int) -> _Snapshot | None:
        metadata = _encode(scan.metadata)
        size = len(metadata)
        if size > budget:
            return None
        items = []
        for item in scan.items:
            encoded = _encode(item)
            size += len(encoded)
            if size > budget:
                return None
            items.append(encoded)
        return cls(tuple(items), metadata, size)

    def page(self, *, top: int, skip: int) -> dict[str, Any]:
        return _page(
            json.loads(self.metadata),
            [json.loads(item) for item in self.items[skip : skip + top]],
            top=top,
            skip=skip,
        )


@dataclass(frozen=True)
class _Entry:
    snapshot: _Snapshot
    completed_at: float


class AdoActivityScanCache:
    def __init__(
        self,
        *,
        ttl_seconds: float = 300,
        max_bytes: int = 64 * 1024 * 1024,
        max_entries: int = 128,
        max_inflight: int = 16,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.max_inflight = max_inflight
        self._clock = clock
        self._salt = secrets.token_bytes(32)
        self._lock = Lock()
        self._entries: OrderedDict[tuple[str, str, int], _Entry] = OrderedDict()
        self._inflight: dict[tuple[str, str, int], Future[_Snapshot | ActivityScanResult]] = {}
        self._generations: dict[str, int] = {}
        self._retained_bytes = 0

    def _cleanup_generations(self) -> None:
        active = {key[0] for key in self._entries} | {key[0] for key in self._inflight}
        self._generations = {
            scope: generation for scope, generation in self._generations.items() if scope in active
        }

    def _remove(self, key: tuple[str, str, int]) -> None:
        self._retained_bytes -= self._entries.pop(key).snapshot.size_bytes

    def _expire(self, now: float) -> None:
        for key, entry in list(self._entries.items()):
            if now - entry.completed_at >= self.ttl_seconds:
                self._remove(key)
        self._cleanup_generations()

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._expire(self._clock())
            return {
                "retained_bytes": self._retained_bytes,
                "entries": len(self._entries),
                "inflight": len(self._inflight),
            }

    def _log(self, outcome: str, *, age_seconds: float = 0) -> None:
        stats = self.stats()
        LOGGER.info(
            "ado_activity_cache outcome=%s age_seconds=%.3f retained_bytes=%d entries=%d inflight=%d",
            outcome,
            age_seconds,
            stats["retained_bytes"],
            stats["entries"],
            stats["inflight"],
        )

    def invalidate_directory(self, directory_path: str) -> None:
        with self._lock:
            self._generations[directory_path] = self._generations.get(directory_path, 0) + 1
            for key in list(self._entries):
                if key[0] == directory_path:
                    self._remove(key)
            self._cleanup_generations()
        self._log("invalidated")

    def query(
        self,
        *,
        directory_path: str,
        key_parts: Any,
        load: Callable[[], ActivityScanResult],
        top: int,
        skip: int,
    ) -> dict[str, Any]:
        # Credentials and caller filters participate in equality but are never retained or logged.
        digest = hmac.new(self._salt, _encode(key_parts), hashlib.sha256).hexdigest()
        now = self._clock()
        with self._lock:
            self._expire(now)
            key = (directory_path, digest, self._generations.get(directory_path, 0))
            entry = self._entries.get(key)
            future = self._inflight.get(key)
            leader = False
            if entry is not None:
                self._entries.move_to_end(key)
            elif future is None and len(self._inflight) < self.max_inflight:
                future = Future()
                self._inflight[key] = future
                leader = True
        if entry is not None:
            self._log("hit", age_seconds=now - entry.completed_at)
            return entry.snapshot.page(top=top, skip=skip)
        if future is None:
            self._log("inflight_limit_bypass")
            return load().page(top=top, skip=skip)
        if not leader:
            self._log("shared")
            # A caller abandoning its wait does not cancel the shared future.
            return future.result().page(top=top, skip=skip)

        self._log("miss")
        try:
            scan = load()
            snapshot = _Snapshot.from_scan(scan, self.max_bytes)
            completed_at = self._clock()
            stored = False
            with self._lock:
                self._expire(completed_at)
                if snapshot is not None and self._generations.get(directory_path, 0) == key[2]:
                    while self._entries and (
                        len(self._entries) >= self.max_entries
                        or self._retained_bytes + snapshot.size_bytes > self.max_bytes
                    ):
                        self._remove(next(iter(self._entries)))
                    self._entries[key] = _Entry(snapshot, completed_at)
                    self._retained_bytes += snapshot.size_bytes
                    stored = True
            result = snapshot if snapshot is not None else scan
        except BaseException as exc:
            future.set_exception(exc)
            self._log("failed")
            raise
        else:
            future.set_result(result)
            self._log(
                "stored"
                if stored
                else "oversized_bypass"
                if snapshot is None
                else "invalidated_bypass"
            )
        finally:
            with self._lock:
                self._inflight.pop(key, None)
                self._cleanup_generations()
        return result.page(top=top, skip=skip)
