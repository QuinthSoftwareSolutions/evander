from __future__ import annotations

from dataclasses import dataclass
import json
import ipaddress
import socket
import time
from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import httpcore

from .base import EffectProviderBase, EffectProviderExecutionError

NODE_TYPE = "effect:http:request"
_METADATA_HOSTS = {
    "169.254.169.254",
    "fd00:ec2::254",
    "metadata",
    "metadata.google.internal",
    "metadata.azure.internal",
    "100.100.100.200",
}
_LOCALHOST_NAMES = {"localhost", "ip6-localhost", "ip6-loopback"}


@dataclass(frozen=True)
class _ApprovedDestination:
    transport: httpx.BaseTransport | None = None


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


class HttpRequestEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        resolved_config_map = dict(resolved_config or {})
        payload = self._definition_payload()
        if resolved_config_map:
            payload.update(resolved_config_map)

        method = self._normalize_method(payload.get("method", "GET"))
        url = self._normalize_url(payload.get("url"))
        allowed_url_patterns = self._normalize_allowed_url_patterns(
            payload.get("allowed_url_patterns", ["*"])
        )
        if self._url_requires_explicit_allowlist(resolved_config_map):
            self._enforce_explicit_bound_url_allowlist(
                allowed_url_patterns=allowed_url_patterns,
                explicit_allowlist=self._allowed_url_patterns_was_explicit(
                    resolved_config_map
                ),
            )
        self._enforce_allowed_url(url, allowed_url_patterns)
        approved_destination = self._enforce_destination_policy(
            url,
            allow_private_networks=self._normalize_bool(
                payload.get("allow_private_networks", False),
                field_name="allow_private_networks",
            ),
        )
        headers = self._normalize_headers(payload.get("headers"))
        timeout_seconds = self._resolve_timeout_seconds(payload.get("timeout_seconds"))
        check = self._normalize_bool(payload.get("check", False), field_name="check")
        body = payload.get("payload")

        start = time.monotonic()
        try:
            with httpx.Client(
                timeout=timeout_seconds,
                follow_redirects=False,
                transport=approved_destination.transport,
            ) as client:
                request_kwargs: dict[str, Any] = {"headers": headers or None}
                if body is not None:
                    request_kwargs["json"] = body
                response = client.request(method, url, **request_kwargs)
        except httpx.RequestError as exc:
            message = f"{NODE_TYPE} {method} {url} request failed: {exc}"
            raise RuntimeError(message) from exc
        elapsed_ms = max(0, int((time.monotonic() - start) * 1000))

        body_text = response.text
        body_json = self._parse_json(response)
        status_code = int(response.status_code)
        success = 200 <= status_code < 300
        content_type = str(response.headers.get("content-type") or "")
        result = {
            "success": success,
            "status_code": status_code,
            "method": method,
            "url": str(response.url),
            "headers": dict(response.headers),
            "body_text": body_text,
            "body_json": body_json,
            "content_type": content_type,
            "elapsed_ms": elapsed_ms,
        }
        if check and not success:
            excerpt = self._body_excerpt(body_text)
            raise EffectProviderExecutionError(
                f"{NODE_TYPE} {method} {url} failed with status {status_code}: {excerpt}",
                outputs=result,
            )
        return result

    def _definition_payload(self) -> dict[str, Any]:
        definition = self.effect_definition
        payload: dict[str, Any] = {}
        if definition is None:
            return payload
        for field_name in (
            "method",
            "url",
            "allowed_url_patterns",
            "payload",
            "headers",
            "timeout_seconds",
            "check",
            "allow_private_networks",
        ):
            if hasattr(definition, field_name):
                payload[field_name] = getattr(definition, field_name)
        return payload

    @staticmethod
    def _normalize_method(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{NODE_TYPE} method must be a string")
        method = value.strip()
        if not method:
            raise ValueError(f"{NODE_TYPE} method must be GET, POST, or PUT")
        method = method.upper()
        if method not in {"GET", "POST", "PUT"}:
            raise ValueError(f"{NODE_TYPE} method must be GET, POST, or PUT")
        return method

    @staticmethod
    def _normalize_url(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{NODE_TYPE} url must be a string")
        url = value.strip()
        if not url:
            raise ValueError(f"{NODE_TYPE} url must be a non-empty http or https URL")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{NODE_TYPE} url must be a non-empty http or https URL")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError(f"{NODE_TYPE} url must include a valid port") from exc
        return url

    @classmethod
    def _normalize_allowed_url_patterns(cls, value: Any) -> list[str]:
        if value is None:
            return ["*"]
        if isinstance(value, str):
            pattern = value.strip()
            if not pattern:
                raise ValueError(f"{NODE_TYPE} allowed_url_patterns must include a pattern")
            return [pattern]
        if not isinstance(value, list):
            raise ValueError(f"{NODE_TYPE} allowed_url_patterns must be a string or string list")
        patterns: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"{NODE_TYPE} allowed_url_patterns must be a string or string list"
                )
            pattern = item.strip()
            if pattern:
                patterns.append(pattern)
        if not patterns:
            raise ValueError(f"{NODE_TYPE} allowed_url_patterns must include a pattern")
        return patterns

    @classmethod
    def _enforce_allowed_url(cls, url: str, allowed_url_patterns: list[str]) -> None:
        candidates = cls._url_match_candidates(url)
        for raw_pattern in allowed_url_patterns:
            pattern = cls._normalize_allowed_url_pattern(raw_pattern)
            if pattern == "*":
                return
            if "://" in pattern:
                scoped_candidates = (candidates["url"], candidates["origin"])
            else:
                scoped_candidates = (candidates["host"], candidates["netloc"])
            if any(fnmatchcase(candidate, pattern) for candidate in scoped_candidates):
                return
        raise ValueError(f"{NODE_TYPE} url is not allowed by allowed_url_patterns: {url}")

    def _url_requires_explicit_allowlist(self, resolved_config: Mapping[str, Any]) -> bool:
        definition_url = self._definition_config_value("url")
        if isinstance(definition_url, str) and "${{" in definition_url:
            return True
        if "url" not in resolved_config:
            return False
        if definition_url is None:
            return True
        if not isinstance(definition_url, str):
            return True
        if "${{" in definition_url:
            return True
        return str(resolved_config.get("url") or "").strip() != definition_url.strip()

    def _allowed_url_patterns_was_explicit(self, resolved_config: Mapping[str, Any]) -> bool:
        if "allowed_url_patterns" in resolved_config:
            return True
        definition = self.effect_definition
        config = getattr(definition, "config", None)
        if isinstance(config, Mapping) and "allowed_url_patterns" in config:
            return True
        fields_set = getattr(definition, "model_fields_set", set())
        return "allowed_url_patterns" in fields_set

    def _definition_config_value(self, field_name: str) -> Any:
        definition = self.effect_definition
        if definition is None:
            return None
        config = getattr(definition, "config", None)
        if isinstance(config, Mapping) and field_name in config:
            return config[field_name]
        if hasattr(definition, field_name):
            return getattr(definition, field_name)
        return None

    @classmethod
    def _enforce_explicit_bound_url_allowlist(
        cls,
        *,
        allowed_url_patterns: list[str],
        explicit_allowlist: bool,
    ) -> None:
        if not explicit_allowlist:
            raise ValueError(
                f"{NODE_TYPE} bound urls require explicit allowed_url_patterns"
            )
        global_patterns = [
            pattern
            for pattern in allowed_url_patterns
            if cls._pattern_allows_any_host(pattern)
        ]
        if global_patterns:
            raise ValueError(
                f"{NODE_TYPE} bound urls require host-restricted allowed_url_patterns"
            )

    @staticmethod
    def _pattern_allows_any_host(pattern: str) -> bool:
        normalized = pattern.strip().casefold()
        if not normalized or normalized == "*":
            return True
        if "://" in normalized:
            parsed = urlparse(normalized)
            host = (parsed.hostname or "").rstrip(".")
            return host in {"", "*"}
        if normalized.startswith("["):
            closing = normalized.find("]")
            host = normalized[1:closing] if closing != -1 else normalized
        else:
            host = normalized.rsplit(":", 1)[0] if ":" in normalized else normalized
        return host.rstrip(".") in {"", "*"}

    @classmethod
    def _enforce_destination_policy(
        cls,
        url: str,
        *,
        allow_private_networks: bool,
    ) -> _ApprovedDestination:
        parsed = urlparse(url)
        host = parsed.hostname
        if host is None:
            raise ValueError(f"{NODE_TYPE} url must be a non-empty http or https URL")
        normalized_host = host.rstrip(".").casefold()
        if cls._is_metadata_destination(normalized_host):
            raise ValueError(f"{NODE_TYPE} url targets a blocked metadata destination")
        address = cls._parse_ip_address(normalized_host)
        if address is not None:
            if cls._is_metadata_destination(str(address)):
                raise ValueError(f"{NODE_TYPE} url targets a blocked metadata destination")
            if not allow_private_networks and cls._is_private_destination(address):
                raise ValueError(
                    f"{NODE_TYPE} url targets a blocked private, loopback, "
                    "or link-local destination"
                )
            return _ApprovedDestination()
        if not allow_private_networks and cls._is_localhost_name(normalized_host):
            raise ValueError(
                f"{NODE_TYPE} url targets a blocked private, loopback, "
                "or link-local destination"
            )
        resolved_addresses = cls._resolve_host_addresses(
            normalized_host, parsed.port or cls._default_port(parsed.scheme)
        )
        if not resolved_addresses:
            raise ValueError(f"{NODE_TYPE} url hostname could not be resolved")
        for resolved_address in resolved_addresses:
            if cls._is_metadata_destination(str(resolved_address)):
                raise ValueError(f"{NODE_TYPE} url targets a blocked metadata destination")
            if not allow_private_networks and cls._is_private_destination(resolved_address):
                raise ValueError(
                    f"{NODE_TYPE} url targets a blocked private, loopback, "
                    "or link-local destination"
                )
        return _ApprovedDestination(
            transport=_PinnedAddressHTTPTransport(
                allowed_host=normalized_host,
                approved_addresses=resolved_addresses,
            )
        )

    @staticmethod
    def _parse_ip_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        try:
            return ipaddress.ip_address(host)
        except ValueError:
            return None

    @staticmethod
    def _default_port(scheme: str) -> int:
        return 443 if scheme == "https" else 80

    @classmethod
    def _resolve_host_addresses(
        cls, host: str, port: int
    ) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            address_info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return []
        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        seen: set[str] = set()
        for item in address_info:
            sockaddr = item[4]
            if not sockaddr:
                continue
            address = cls._parse_ip_address(str(sockaddr[0]))
            if address is None:
                continue
            key = str(address)
            if key in seen:
                continue
            seen.add(key)
            addresses.append(address)
        return addresses

    @staticmethod
    def _canonical_address_for_policy(
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            return address.ipv4_mapped
        return address

    @classmethod
    def _is_private_destination(
        cls,
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        policy_address = cls._canonical_address_for_policy(address)
        return (
            policy_address.is_private
            or policy_address.is_loopback
            or policy_address.is_link_local
            or policy_address.is_unspecified
            or policy_address.is_multicast
            or policy_address.is_reserved
        )

    @staticmethod
    def _is_localhost_name(host: str) -> bool:
        return host in _LOCALHOST_NAMES or host.endswith(".localhost")

    @classmethod
    def _is_metadata_destination(cls, host: str) -> bool:
        normalized = host.strip("[]").rstrip(".").casefold()
        address = cls._parse_ip_address(normalized)
        if address is not None:
            normalized = str(cls._canonical_address_for_policy(address))
        return normalized in _METADATA_HOSTS or normalized.endswith(
            ".metadata.google.internal"
        )

    @staticmethod
    def _url_match_candidates(url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = parsed.hostname
        if host is None:
            raise ValueError(f"{NODE_TYPE} url must be a non-empty http or https URL")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"{NODE_TYPE} url must include a valid port") from exc
        normalized_host = host.rstrip(".").casefold()
        host_for_netloc = (
            f"[{normalized_host}]"
            if ":" in normalized_host and not normalized_host.startswith("[")
            else normalized_host
        )
        netloc = f"{host_for_netloc}:{port}" if port is not None else host_for_netloc
        scheme = parsed.scheme.casefold()
        path = parsed.path or "/"
        normalized_url = urlunparse((scheme, netloc, path, "", parsed.query, ""))
        return {
            "scheme": scheme,
            "host": normalized_host,
            "netloc": netloc,
            "origin": f"{scheme}://{netloc}",
            "url": normalized_url,
        }

    @staticmethod
    def _normalize_allowed_url_pattern(pattern: str) -> str:
        normalized = pattern.strip()
        if not normalized or normalized == "*":
            return normalized
        if "://" not in normalized:
            return normalized.rstrip(".").casefold()
        parsed = urlparse(normalized)
        if not parsed.scheme or not parsed.netloc:
            return normalized
        netloc = parsed.netloc.rsplit("@", 1)[-1].rstrip(".").casefold()
        return urlunparse((parsed.scheme.casefold(), netloc, parsed.path, "", parsed.query, ""))

    @staticmethod
    def _normalize_headers(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(f"{NODE_TYPE} headers must be a mapping")
        headers: dict[str, str] = {}
        for key, raw_value in value.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            headers[normalized_key] = "" if raw_value is None else str(raw_value)
        return headers

    def _resolve_timeout_seconds(self, value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{NODE_TYPE} timeout_seconds must be a number")
        if value is not None and value != "":
            return float(value)
        context = self._context()
        provider_timeout = context.provider_config.get("openai_relay_timeout_seconds")
        if isinstance(provider_timeout, bool):
            raise ValueError(f"{NODE_TYPE} timeout_seconds must be a number")
        if provider_timeout is not None and provider_timeout != "":
            return float(provider_timeout)
        return 30.0

    @staticmethod
    def _normalize_bool(value: Any, *, default: bool = False, field_name: str) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off"}:
                return False
        raise ValueError(f"{NODE_TYPE} {field_name} must be a boolean")

    @staticmethod
    def _parse_json(response: httpx.Response) -> Any | None:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _body_excerpt(text: str, *, limit: int = 240) -> str:
        excerpt = " ".join(text.strip().split())
        if not excerpt:
            return "<empty body>"
        if len(excerpt) <= limit:
            return excerpt
        return f"{excerpt[: limit - 1]}…"

    def _context(self) -> Any:
        if self.runtime_context is None:
            raise RuntimeError("HttpRequestEffectProvider has no runtime context")
        return self.runtime_context
