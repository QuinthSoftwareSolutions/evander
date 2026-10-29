from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

DASHBOARD_CACHE_PARAM = "_dashboard"
DASHBOARD_REFRESH_PARAM = "_dashboard_refresh"
DEFAULT_DASHBOARD_CACHE_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class _CacheEntry:
    body: bytes
    status_code: int
    expires_at: float
    etag: str


class AdminWebJsonResponseCache:
    """Short-lived opt-in cache for expensive dashboard JSON reads."""

    def __init__(
        self,
        *,
        default_ttl_seconds: float = DEFAULT_DASHBOARD_CACHE_TTL_SECONDS,
        max_entries: int = 128,
        max_bytes: int = 32 * 1024 * 1024,
    ):
        self.default_ttl_seconds = max(0.0, float(default_ttl_seconds))
        self._entries: OrderedDict[tuple[Any, ...], _CacheEntry] = OrderedDict()
        self._max_entries = max(0, max_entries)
        self._max_bytes = max(0, max_bytes)
        self._cached_bytes = 0
        self._inflight: dict[tuple[Any, ...], asyncio.Future[_CacheEntry | None]] = {}
        self._refresh_tokens: dict[tuple[Any, ...], object] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._cached_bytes = 0
            self._inflight.clear()
            self._refresh_tokens.clear()

    def _prune_locked(self, now: float) -> None:
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= now:
                self._cached_bytes -= len(self._entries.pop(key).body)
        while len(self._entries) > self._max_entries or self._cached_bytes > self._max_bytes:
            _, entry = self._entries.popitem(last=False)
            self._cached_bytes -= len(entry.body)

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
        refresh_token = object()
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
        if not bypass:
            with self._lock:
                entry = self._entries.get(key)
                if entry is not None and entry.expires_at > now:
                    self._entries.move_to_end(key)
                    return _cached_json_response(entry, hit=True, request=request)

                future = self._inflight.get(key)
                if future is None:
                    future = asyncio.get_running_loop().create_future()
                    self._inflight[key] = future
                    self._refresh_tokens[key] = refresh_token
                    owns_refresh = True
                else:
                    owns_refresh = False

            if not owns_refresh:
                entry = await asyncio.shield(future)
                if entry is not None and entry.expires_at > time.monotonic():
                    return _cached_json_response(entry, hit=True, request=request)
                return await factory()
        else:
            future = None
            owns_refresh = False
            with self._lock:
                self._refresh_tokens[key] = refresh_token

        try:
            response = await factory()
            if not isinstance(response, JSONResponse) or response.status_code != 200:
                if future is not None and not future.done():
                    future.set_result(None)
                return response

            try:
                etag = await run_in_threadpool(_body_etag, response.body)
            except Exception:
                if future is not None and not future.done():
                    future.set_result(None)
                return response

            entry = _CacheEntry(
                body=response.body,
                status_code=response.status_code,
                expires_at=time.monotonic() + ttl,
                etag=etag,
            )
            with self._lock:
                # A newer explicit refresh or clear can finish while this
                # producer builds the response or hashes it off the loop.
                # Return its own snapshot to this caller without replacing
                # the newer cached result.
                if self._refresh_tokens.get(key) is refresh_token:
                    old = self._entries.pop(key, None)
                    if old is not None:
                        self._cached_bytes -= len(old.body)
                    if self._max_entries and len(entry.body) <= self._max_bytes:
                        self._entries[key] = entry
                        self._cached_bytes += len(entry.body)
                self._prune_locked(time.monotonic())
            if future is not None and not future.done():
                future.set_result(entry)
            if not bypass and _request_etag_matches(
                request.headers.get("if-none-match"), entry.etag
            ):
                return _not_modified_json_response(entry, cache_state="miss")
            response.headers["X-Admin-Web-Cache"] = "refresh" if bypass else "miss"
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers["ETag"] = entry.etag
            return response
        except BaseException:
            if future is not None and not future.done():
                future.set_result(None)
            raise
        finally:
            with self._lock:
                if self._refresh_tokens.get(key) is refresh_token:
                    self._refresh_tokens.pop(key, None)
                if owns_refresh and self._inflight.get(key) is future:
                    self._inflight.pop(key, None)


def _cached_json_response(entry: _CacheEntry, *, hit: bool, request: Request) -> Response:
    cache_state = "hit" if hit else "miss"
    if _request_etag_matches(request.headers.get("if-none-match"), entry.etag):
        return _not_modified_json_response(entry, cache_state=cache_state)
    return Response(
        entry.body,
        media_type="application/json",
        status_code=entry.status_code,
        headers={
            "X-Admin-Web-Cache": cache_state,
            "Cache-Control": "no-store",
            "ETag": entry.etag,
        },
    )


def _not_modified_json_response(entry: _CacheEntry, *, cache_state: str) -> Response:
    return Response(
        status_code=304,
        headers={
            "X-Admin-Web-Cache": cache_state,
            "Cache-Control": "no-store",
            "ETag": entry.etag,
        },
    )


def _json_etag(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return f'W/"{hashlib.sha256(body).hexdigest()}"'


def _body_etag(body: bytes) -> str:
    return _json_etag(json.loads(body))


def _request_etag_matches(header_value: str | None, etag: str) -> bool:
    if not header_value:
        return False
    etag_value = etag.strip()
    strong_value = etag_value.removeprefix("W/")
    for candidate in header_value.split(","):
        normalized = candidate.strip()
        if normalized == "*":
            return True
        if normalized == etag_value or normalized == strong_value:
            return True
        if normalized.removeprefix("W/") == strong_value:
            return True
    return False


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
    session_token = request.cookies.get("evander_admin_session", "")
    return (
        namespace,
        request.method,
        request.url.path,
        session_token,
        tuple(query_items),
    )


__all__ = [
    "DASHBOARD_CACHE_PARAM",
    "DASHBOARD_REFRESH_PARAM",
    "DEFAULT_DASHBOARD_CACHE_TTL_SECONDS",
    "AdminWebJsonResponseCache",
]
