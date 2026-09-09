"""Pinned HTTPS transport shared by host-owned outbound HTTP clients."""
from __future__ import annotations

import ipaddress
from collections.abc import Iterable

import httpcore
import httpx

NODE_TYPE = "host HTTP request"


class _PinnedAddressNetworkBackend(httpcore.SyncBackend):
    def __init__(
        self,
        *,
        allowed_host: str,
        approved_addresses: Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> None:
        self.allowed_host = allowed_host.rstrip(".").casefold()
        self.approved_addresses = tuple(approved_addresses)
        if not self.approved_addresses:
            raise ValueError("approved_addresses cannot be empty")
        self.approved_address = self.approved_addresses[0]

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        normalized_host = host.rstrip(".").casefold()
        if normalized_host != self.allowed_host:
            raise httpcore.ConnectError(
                f"{NODE_TYPE} attempted to connect to unapproved host: {host}"
            )
        last_error: httpcore.ConnectError | httpcore.ConnectTimeout | None = None
        for approved_address in self.approved_addresses:
            try:
                return super().connect_tcp(
                    str(approved_address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise httpcore.ConnectError(f"{NODE_TYPE} has no approved addresses for host: {host}")


class _PinnedAddressHTTPTransport(httpx.HTTPTransport):
    def __init__(
        self,
        *,
        allowed_host: str,
        approved_addresses: Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> None:
        super().__init__(limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
        self.pinned_backend = _PinnedAddressNetworkBackend(
            allowed_host=allowed_host,
            approved_addresses=approved_addresses,
        )
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpcore.default_ssl_context(),
            max_connections=1,
            max_keepalive_connections=0,
            keepalive_expiry=None,
            http1=True,
            http2=False,
            retries=0,
            network_backend=self.pinned_backend,
        )


class _PinnedAddressAsyncNetworkBackend(httpcore.AnyIOBackend):
    def __init__(self, *, allowed_host: str, approved_addresses: Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address]) -> None:
        self.allowed_host = allowed_host.rstrip(".").casefold()
        self.approved_addresses = tuple(approved_addresses)
        if not self.approved_addresses:
            raise ValueError("approved_addresses cannot be empty")

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if host.rstrip(".").casefold() != self.allowed_host:
            raise httpcore.ConnectError("Attempted to connect to unapproved host")
        last_error = None
        for address in self.approved_addresses:
            try:
                return await super().connect_tcp(
                    str(address), port, timeout=timeout,
                    local_address=local_address, socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


class _PinnedAddressAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, *, allowed_host: str, approved_addresses: Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address]) -> None:
        super().__init__(limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
        self.pinned_backend = _PinnedAddressAsyncNetworkBackend(
            allowed_host=allowed_host, approved_addresses=approved_addresses,
        )
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpcore.default_ssl_context(),
            max_connections=1, max_keepalive_connections=0,
            keepalive_expiry=None, http1=True, http2=False, retries=0,
            network_backend=self.pinned_backend,
        )
