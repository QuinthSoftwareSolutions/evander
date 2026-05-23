from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

DASHBOARD_CACHE_PARAM = "_dashboard"
DASHBOARD_REFRESH_PARAM = "_dashboard_refresh"
DEFAULT_DASHBOARD_CACHE_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class _CacheEntry:
    payload: Any
    status_code: int
    expires_at: float


class AdminWebJsonResponseCache:
    """Short-lived opt-in cache for expensive dashboard JSON reads."""

    def __init__(self, *, default_ttl_seconds: float = DEFAULT_DASHBOARD_CACHE_TTL_SECONDS):
        self.default_ttl_seconds = max(0.0, float(default_ttl_seconds))
        self._entries: dict[tuple[Any, ...], _CacheEntry] = {}
        self._inflight: dict[tuple[Any, ...], asyncio.Future[_CacheEntry | None]] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._inflight.clear()

    def wrap(
        self,
        namespace: str,
        handler: Callable[[Request], Awaitable[Response]],
        *,
        ttl_seconds: float | None = None,
    ) -> Callable[[Request], Awaitable[Response]]:
        async def cached_handler(request: Request) -> Response:
            return await self.get_or_set_json(
                request,
                namespace,
                lambda: handler(request),
                ttl_seconds=ttl_seconds,
            )

        return cached_handler

    async def get_or_set_json(
        self,
        request: Request,
        namespace: str,
        factory: Callable[[], Awaitable[Response]],
        *,
        ttl_seconds: float | None = None,
    ) -> Response:
        ttl = self.default_ttl_seconds if ttl_seconds is None else max(0.0, float(ttl_seconds))
        if ttl <= 0 or not _dashboard_cache_enabled(request):
            return await factory()

        key = _cache_key(request, namespace)
        bypass = _request_bypasses_cache(request)
        now = time.monotonic()
        if not bypass:
            with self._lock:
                entry = self._entries.get(key)
                if entry is not None and entry.expires_at > now:
                    return _cached_json_response(entry, hit=True)

                future = self._inflight.get(key)
                if future is None:
                    future = asyncio.get_running_loop().create_future()
                    self._inflight[key] = future
                    owns_refresh = True
                else:
                    owns_refresh = False

            if not owns_refresh:
                entry = await future
                if entry is not None and entry.expires_at > time.monotonic():
                    return _cached_json_response(entry, hit=True)
                return await factory()
        else:
            future = None
            owns_refresh = False

        try:
            response = await factory()
            if not isinstance(response, JSONResponse) or response.status_code != 200:
                if future is not None and not future.done():
                    future.set_result(None)
                return response

            try:
                payload = json.loads(response.body.decode("utf-8"))
            except Exception:
                if future is not None and not future.done():
                    future.set_result(None)
                return response

            entry = _CacheEntry(
                payload=payload,
                status_code=response.status_code,
                expires_at=time.monotonic() + ttl,
            )
            with self._lock:
                self._entries[key] = entry
            if future is not None and not future.done():
                future.set_result(entry)
            response.headers["X-Admin-Web-Cache"] = "refresh" if bypass else "miss"
            response.headers.setdefault("Cache-Control", "no-store")
            return response
        except Exception:
            if future is not None and not future.done():
                future.set_result(None)
            raise
        finally:
            if owns_refresh:
                with self._lock:
                    if self._inflight.get(key) is future:
                        self._inflight.pop(key, None)


def _cached_json_response(entry: _CacheEntry, *, hit: bool) -> JSONResponse:
    return JSONResponse(
        entry.payload,
        status_code=entry.status_code,
        headers={
            "X-Admin-Web-Cache": "hit" if hit else "miss",
            "Cache-Control": "no-store",
        },
    )


def _dashboard_cache_enabled(request: Request) -> bool:
    return _truthy(request.query_params.get(DASHBOARD_CACHE_PARAM))


def _request_bypasses_cache(request: Request) -> bool:
    cache_control = request.headers.get("cache-control", "")
    if "no-cache" in cache_control.lower():
        return True
    refresh = request.query_params.get(DASHBOARD_REFRESH_PARAM)
    return refresh is not None and _truthy(refresh)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _cache_key(request: Request, namespace: str) -> tuple[Any, ...]:
    query_items = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key not in {DASHBOARD_CACHE_PARAM, DASHBOARD_REFRESH_PARAM}
    ]
    query_items.sort()
    return (namespace, request.method, request.url.path, tuple(query_items))


__all__ = [
    "DASHBOARD_CACHE_PARAM",
    "DASHBOARD_REFRESH_PARAM",
    "DEFAULT_DASHBOARD_CACHE_TTL_SECONDS",
    "AdminWebJsonResponseCache",
]
