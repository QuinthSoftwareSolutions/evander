from __future__ import annotations

import json
import time
from collections.abc import Mapping
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from .base import EffectProviderBase, EffectProviderExecutionError

NODE_TYPE = "effect:http:request"


class HttpRequestEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._definition_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        method = self._normalize_method(payload.get("method", "GET"))
        url = self._normalize_url(payload.get("url"))
        allowed_url_patterns = self._normalize_allowed_url_patterns(
            payload.get("allowed_url_patterns", ["*"])
        )
        self._enforce_allowed_url(url, allowed_url_patterns)
        headers = self._normalize_headers(payload.get("headers"))
        timeout_seconds = self._resolve_timeout_seconds(payload.get("timeout_seconds"))
        check = self._normalize_bool(payload.get("check", False))
        body = payload.get("payload")

        start = time.monotonic()
        try:
            with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
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
    def _normalize_bool(value: Any, *, default: bool = False) -> bool:
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
        raise ValueError(f"{NODE_TYPE} check must be a boolean")

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
