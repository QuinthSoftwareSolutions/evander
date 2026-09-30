"""Operator-configured JSON logins that issue temporary, session-scoped browser state.

Passwords are resolved only on the host. No guest-supplied URL, login payload, or
storage mapping is accepted. The application remains responsible for token validity.
"""
from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import inspect
import ipaddress
import json
import logging
import math
import os
import re
import socket
import time
from typing import Any, Literal, TYPE_CHECKING
from urllib.parse import urlsplit
from weakref import WeakValueDictionary

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from evander.http_transport import _PinnedAddressAsyncHTTPTransport
from evander.models import DirectoryPath
from evander.session_auth import verify_mcp_session_proof_token
from evander.website_credentials import WebsiteCredentialStore, credential_binding

logger = logging.getLogger(__name__)

WEBSITE_CREDENTIAL_ENV_ALLOWLIST = "EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST"

if TYPE_CHECKING:
    from evander.provider_config import ProviderConfigResolver


def website_origin(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment
            or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url):
        raise ValueError("managed websites require an HTTPS URL without userinfo or fragment")
    host = parsed.hostname.rstrip(".").lower()
    if "%" in host:
        raise ValueError("scoped addresses are not supported")
    host = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
    port = parsed.port or 443
    return f"https://{host}" + (f":{port}" if port != 443 else "")


class WebsiteStorageBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_pointer: str = ""
    encoding: Literal["json", "string"] = "json"

    @field_validator("response_pointer")
    @classmethod
    def validate_pointer(cls, value: str) -> str:
        if (value and not value.startswith("/")) or re.search(r"~(?![01])", value):
            raise ValueError("use an RFC 6901 JSON pointer")
        return value


class ManagedWebsiteSessionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    adapter: str = Field(default="json", pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    adapter_config: dict[str, JsonValue] = Field(default_factory=dict, max_length=32)
    browser_origin: str
    login_url: str
    credential_source: Literal["environment", "stored"] = "environment"
    credential_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    credential_fields: dict[str, str] = Field(default_factory=dict, max_length=32)
    public_credential_fields: set[str] = Field(default_factory=set)
    static_fields: dict[str, JsonValue] = Field(default_factory=dict, max_length=32)
    local_storage: dict[str, WebsiteStorageBinding] = Field(default_factory=dict, max_length=64)
    session_storage: dict[str, WebsiteStorageBinding] = Field(default_factory=dict, max_length=64)
    cookies_response_pointer: str | None = None
    jwt_response_pointer: str | None = None
    expires_in_response_pointer: str | None = None
    max_age_seconds: int = Field(default=3600, ge=60, le=86400)
    renew_before_seconds: int = Field(default=60, ge=5, le=3600)
    timeout_seconds: float = Field(default=20, gt=0, le=60)
    max_response_bytes: int = Field(default=262144, ge=1024, le=1048576)

    @model_validator(mode="after")
    def validate_profile(self) -> ManagedWebsiteSessionProfile:
        origin = website_origin(self.browser_origin)
        if urlsplit(self.browser_origin).path not in {"", "/"} or urlsplit(self.browser_origin).query:
            raise ValueError("browser_origin must be an origin without a path or query")
        self.browser_origin = origin
        website_origin(self.login_url)
        if self.adapter == "json" and urlsplit(self.login_url).query:
            raise ValueError("login_url must not contain query parameters")
        if self.adapter == "json" and not self.credential_fields:
            raise ValueError("JSON logins require credential fields")
        if self.adapter == "json" and not self.local_storage and not self.session_storage and self.cookies_response_pointer is None:
            raise ValueError("at least one storage binding is required")
        if self.credential_fields.keys() & self.static_fields.keys():
            raise ValueError("static fields cannot replace credentials")
        if not self.public_credential_fields <= self.credential_fields.keys():
            raise ValueError("public credential fields must name configured credentials")
        if self.credential_source == "environment" and any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env) for env in self.credential_fields.values()):
            raise ValueError("credentials must reference environment variable names")
        if self.jwt_response_pointer is not None and self.expires_in_response_pointer is not None:
            raise ValueError("choose one response expiry source")
        for pointer in (self.jwt_response_pointer, self.expires_in_response_pointer, self.cookies_response_pointer):
            if pointer is not None:
                WebsiteStorageBinding.validate_pointer(pointer)
        if self.renew_before_seconds * 2 >= self.max_age_seconds:
            raise ValueError("max_age_seconds must exceed twice the renewal margin")
        return self


@dataclass(frozen=True)
class WebsiteEnvironmentCredentialGrant:
    directory_path: str
    profile_id: str
    binding: str


def environment_credential_grants() -> frozenset[WebsiteEnvironmentCredentialGrant]:
    """Return host-approved, scope-bound environment credential grants."""
    raw = os.environ.get(WEBSITE_CREDENTIAL_ENV_ALLOWLIST, "").strip()
    if not raw:
        return frozenset()
    if len(raw) > 65536:
        raise ValueError("website credential environment grant configuration is invalid")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("website credential environment grant configuration is invalid") from None
    if not isinstance(payload, list) or len(payload) > 256:
        raise ValueError("website credential environment grant configuration is invalid")

    required = {"directory_path", "profile_id", "browser_origin", "login_url", "credential_fields"}
    optional = {"public_credential_fields", "static_fields"}
    grants = set()
    try:
        for item in payload:
            if (not isinstance(item, dict) or not required <= set(item)
                    or set(item) - required - optional):
                raise ValueError
            raw_directory_path = item["directory_path"]
            if not isinstance(raw_directory_path, str):
                raise ValueError
            directory_path = DirectoryPath(segments=raw_directory_path).as_posix()
            profile_id = item["profile_id"]
            if (not isinstance(profile_id, str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", profile_id)):
                raise ValueError
            binding_profile = ManagedWebsiteSessionProfile(
                browser_origin=item["browser_origin"],
                login_url=item["login_url"],
                credential_source="environment",
                credential_fields=item["credential_fields"],
                public_credential_fields=item.get("public_credential_fields", []),
                static_fields=item.get("static_fields", {}),
                local_storage={"_grant_validation": WebsiteStorageBinding()},
            )
            grants.add(WebsiteEnvironmentCredentialGrant(
                directory_path=directory_path,
                profile_id=profile_id,
                binding=credential_binding(binding_profile),
            ))
    except (KeyError, TypeError, ValueError):
        raise ValueError("website credential environment grant configuration is invalid") from None
    return frozenset(grants)


class WebsiteSessionError(Exception):
    """Safe, fixed error codes only: never include upstream data or credentials."""

    def __init__(self, code: str, status: int = 503) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def validate_environment_credentials(
    directory_path: str,
    profile_id: str,
    profile: ManagedWebsiteSessionProfile,
) -> None:
    if profile.credential_source != "environment":
        return
    scope = DirectoryPath(segments=directory_path).as_posix()
    binding = credential_binding(profile)
    if not any(
        grant.profile_id == profile_id
        and grant.binding == binding
        and (scope == grant.directory_path or scope.startswith(grant.directory_path + "/"))
        for grant in environment_credential_grants()
    ):
        raise ValueError(
            "website credential environment grant is not host-approved for this profile"
        )


def _pointer(value: Any, pointer: str) -> Any:
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", part):
                raise ValueError("invalid array index")
            value = value[int(part)]
        else:
            value = value[part]
    return value


async def _public_transport(url: str) -> httpx.AsyncBaseTransport:
    """Resolve once, reject nonpublic addresses, and pin the approved TLS connection."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    resolved = await asyncio.get_running_loop().getaddrinfo(
        host, parsed.port or 443, type=socket.SOCK_STREAM,
    )
    addresses = [ipaddress.ip_address(item[4][0]) for item in resolved]
    policy_addresses = [a.ipv4_mapped if isinstance(a, ipaddress.IPv6Address)
                        and a.ipv4_mapped else a for a in addresses]
    if not addresses or any(not a.is_global or a.is_multicast for a in policy_addresses):
        raise WebsiteSessionError("website_destination_blocked")
    return _PinnedAddressAsyncHTTPTransport(allowed_host=host, approved_addresses=addresses)


class ManagedWebsiteSessionService:
    def __init__(
        self, resolver: ProviderConfigResolver, *,
        transport_factory: Callable[[str], httpx.AsyncBaseTransport | Awaitable[httpx.AsyncBaseTransport]] = _public_transport,
        clock: Callable[[], float] = time.time,
        adapters: dict[str, Any] | None = None,
    ) -> None:
        self.resolver = resolver
        self.transport_factory = transport_factory
        self.clock = clock
        self._adapters = adapters
        # Duplicate callers share a login without blocking other session/target pairs.
        # Weak entries disappear when the last caller releases its lock.
        self._locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = WeakValueDictionary()
        self._cache: OrderedDict[tuple[str, str], tuple[str, dict[str, Any]]] = OrderedDict()
        self._failures: OrderedDict[str, float] = OrderedDict()

    @property
    def adapters(self) -> dict[str, Any]:
        # Ordinary JSON logins do not depend on optional add-on imports.
        if self._adapters is None:
            self._adapters = website_session_adapters(self.resolver)
        return self._adapters

    def _grant(self, sid: str, alias: str, proof: str | None):
        try:
            session = self.resolver.get_session_manifest(sid)
            if not verify_mcp_session_proof_token(session, proof):
                raise WebsiteSessionError("website_session_denied", 403)
            if session.expires_at.timestamp() <= self.clock():
                raise WebsiteSessionError("website_session_expired", 403)
            target = session.http_target(alias)
            profile_id = target.managed_website_session
            profile = self.resolver.resolve_for_session_manifest(session).managed_website_sessions.get(profile_id)
            if not profile_id or profile is None or not profile.enabled:
                raise WebsiteSessionError("website_session_not_granted", 403)
            if website_origin(target.base_url) != profile.browser_origin:
                raise WebsiteSessionError("website_session_origin_mismatch", 403)
            targets = session.http_targets_metadata().targets.values()
            allowed = set()
            for candidate in targets:
                try:
                    allowed.add(website_origin(candidate.base_url))
                except ValueError:
                    continue
            if website_origin(profile.login_url) not in allowed:
                raise WebsiteSessionError("website_login_origin_not_granted", 403)
            if profile.adapter == "json":
                credentials = self.credentials(session.directory_path, profile_id, profile)
            else:
                adapter = self.adapters.get(profile.adapter)
                if adapter is None:
                    raise WebsiteSessionError("website_login_adapter_unavailable", 403)
                credentials = adapter.grant_context(session, profile)
            fingerprint = hashlib.sha256(json.dumps([
                session.directory_path, profile_id, profile.model_dump(mode="json"), credentials,
                session.expires_at.isoformat(), session.created_at.isoformat(),
            ], sort_keys=True).encode()).hexdigest()
            return profile, credentials, fingerprint, session.expires_at.timestamp()
        except WebsiteSessionError:
            raise
        except (ValueError, KeyError, TypeError):
            raise WebsiteSessionError("website_session_denied", 403) from None

    def credentials(self, scope: str, profile_id: str, profile) -> dict[str, str]:
        try:
            if profile.credential_source == "stored":
                values = WebsiteCredentialStore(self.resolver.config.runtime_dir).read(
                    profile.credential_ref, scope, profile_id, profile)
            else:
                validate_environment_credentials(scope, profile_id, profile)
                values = {field: os.environ.get(env, "") for field, env in profile.credential_fields.items()}
            if set(values) != set(profile.credential_fields) or not all(values.values()):
                raise ValueError("missing credentials")
            return values
        except Exception:
            raise WebsiteSessionError("website_credentials_unavailable") from None

    async def acquire(self, sid: str, alias: str, proof: str | None) -> dict[str, Any]:
        self._grant(sid, alias, proof)
        key = (sid, alias)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            profile, credentials, fingerprint, session_expiry = self._grant(sid, alias, proof)
            cached = self._cache.get(key)
            if cached and cached[0] == fingerprint and cached[1]["expiresAt"] > self.clock() + profile.renew_before_seconds:
                self._cache.move_to_end(key)
                return cached[1]
            self._cache.pop(key, None)
            if self._failures.get(fingerprint, 0) > self.clock():
                raise WebsiteSessionError("website_login_retry_later", 503)
            try:
                state = (await self._login(profile, credentials) if profile.adapter == "json" else
                         await self.adapters[profile.adapter].login(sid, profile, credentials, session_expiry))
                if profile.adapter != "json" or profile.cookies_response_pointer is not None:
                    state = validate_browser_state(state, profile.browser_origin, self.clock(),
                        min(session_expiry, self.clock() + profile.max_age_seconds))
                # A revocation or credential/config change during login must prevent delivery.
                if self._grant(sid, alias, proof)[2] != fingerprint:
                    raise WebsiteSessionError("website_session_changed", 409)
                state["expiresAt"] = min(state["expiresAt"], session_expiry)
                if state["expiresAt"] <= self.clock() + profile.renew_before_seconds:
                    raise WebsiteSessionError("website_login_expiry_too_short")
            except WebsiteSessionError as exc:
                self._failures[fingerprint] = self.clock() + 60
                self._trim(self._failures)
                logger.warning("managed_website_session_failed session=%r target=%r code=%s", sid, alias, exc.code)
                raise
            self._failures.pop(fingerprint, None)
            self._cache[key] = (fingerprint, state)
            self._trim(self._cache)
            logger.info("managed_website_session_renewed session=%r target=%r expires_at=%.0f", sid, alias, state["expiresAt"])
            return state

    @staticmethod
    def _trim(cache: OrderedDict) -> None:
        while len(cache) > 128:
            cache.popitem(last=False)

    async def _login(self, profile: ManagedWebsiteSessionProfile, credentials: dict[str, str]) -> dict[str, Any]:
        started = self.clock()
        try:
            # This deadline includes DNS, connection/TLS, headers and the entire body.
            # Async cancellation interrupts a stalled read and closes the connection.
            async with asyncio.timeout(profile.timeout_seconds):
                transport = self.transport_factory(profile.login_url)
                if inspect.isawaitable(transport):
                    transport = await transport
                async with httpx.AsyncClient(transport=transport,
                                  timeout=profile.timeout_seconds, follow_redirects=False, trust_env=False) as client:
                    async with client.stream("POST", profile.login_url, json={**profile.static_fields, **credentials},
                                       headers={"accept": "application/json", "accept-encoding": "identity"}) as response:
                        if not 200 <= response.status_code < 300:
                            raise WebsiteSessionError("website_login_rejected")
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise WebsiteSessionError("website_login_response_encoding")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > profile.max_response_bytes:
                                raise WebsiteSessionError("website_login_response_limit")
                            body.extend(chunk)
            data = json.loads(body)
            expiry = started + profile.max_age_seconds
            if profile.jwt_response_pointer is not None:
                token = _pointer(data, profile.jwt_response_pointer)
                payload = token.split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                # exp is only a cache hint, not JWT signature validation or authorization.
                claim_expiry = float(claims["exp"])
                if not math.isfinite(claim_expiry):
                    raise ValueError("invalid expiry")
                expiry = min(expiry, claim_expiry)
            if profile.expires_in_response_pointer is not None:
                lifetime = float(_pointer(data, profile.expires_in_response_pointer))
                if not math.isfinite(lifetime):
                    raise ValueError("invalid lifetime")
                expiry = min(expiry, started + lifetime)
            if not math.isfinite(expiry) or expiry <= self.clock() + profile.renew_before_seconds:
                raise WebsiteSessionError("website_login_expiry_too_short")

            def check_secret(candidate, *, include_public=False):
                if isinstance(candidate, str):
                    for field, secret in credentials.items():
                        if (include_public or field not in profile.public_credential_fields) and secret in candidate:
                            raise WebsiteSessionError("website_login_unsafe_response")
                elif isinstance(candidate, dict):
                    for key, value in candidate.items():
                        check_secret(key, include_public=include_public)
                        check_secret(value, include_public=include_public)
                elif isinstance(candidate, list):
                    for item in candidate:
                        check_secret(item, include_public=include_public)

            def render(bindings):
                result = {}
                for key, binding in bindings.items():
                    value = _pointer(data, binding.response_pointer)
                    check_secret(value)
                    if binding.encoding == "string" and not isinstance(value, str):
                        raise ValueError("string storage binding requires a string")
                    result[key] = (json.dumps(value, separators=(",", ":"), ensure_ascii=False)
                                   if binding.encoding == "json" else value)
                return result

            local = render(profile.local_storage)
            temporary = render(profile.session_storage)
            # A login endpoint must never reflect a host password into exported state.
            rendered = json.dumps([local, temporary], ensure_ascii=False)
            if len(rendered.encode("utf-8")) > 1048576:
                raise WebsiteSessionError("website_login_response_limit")
            for field, value in credentials.items():
                if field not in profile.public_credential_fields and (
                    value in rendered or json.dumps(value, ensure_ascii=False)[1:-1] in rendered
                    or any(value in text for text in [*local.values(), *temporary.values()])
                ):
                    raise WebsiteSessionError("website_login_unsafe_response")
            cookies = _pointer(data, profile.cookies_response_pointer) if profile.cookies_response_pointer is not None else []
            check_secret(cookies, include_public=True)
            rendered_cookies = json.dumps(cookies, ensure_ascii=False)
            if any(secret and secret in rendered_cookies for secret in credentials.values()):
                raise WebsiteSessionError("website_login_unsafe_response")
            return {"origin": profile.browser_origin, "expiresAt": expiry,
                    "storageState": {"cookies": cookies, "origins": [{"origin": profile.browser_origin,
                        "localStorage": [{"name": k, "value": v} for k, v in local.items()]}]},
                    "sessionStorage": temporary}
        except WebsiteSessionError:
            raise
        except TimeoutError:
            raise WebsiteSessionError("website_login_timeout") from None
        except Exception:
            # Deliberately discard upstream exception chains (URLs, bodies, credentials).
            raise WebsiteSessionError("website_login_failed") from None


def website_session_adapters(resolver):
    """Load only enabled add-on login adapters; no domain imports in the relay."""
    from evander.addons import load_addons
    if getattr(getattr(resolver, "config", None), "addon_loader", None) is None:
        return {}
    catalog = load_addons(resolver.config.addon_loader, base_dir=resolver.config.runtime_dir.parent)
    adapters = {}
    for registration in catalog.registrations:
        for name, factory in registration.website_session_adapters.items():
            if name == "json" or name in adapters:
                raise ValueError("Duplicate managed website login adapter")
            adapters[name] = factory(resolver)
    return adapters


def validate_browser_state(state, origin, now, expiry_limit):
    """Restrict temporary cookies/storage to the granted HTTPS origin and TTL."""
    try:
        if state["origin"] != origin:
            raise ValueError
        expiry = min(float(state["expiresAt"]), expiry_limit)
        if not math.isfinite(expiry) or expiry <= now:
            raise ValueError
        storage = state["storageState"]
        host = urlsplit(origin).hostname
        cookies = storage.get("cookies", [])
        if not isinstance(cookies, list) or len(cookies) > 64:
            raise ValueError
        for cookie in cookies:
            if (cookie.get("domain", "").lstrip(".") != host or not cookie.get("secure")
                    or not isinstance(cookie.get("name"), str) or not isinstance(cookie.get("value"), str)
                    or not cookie.get("path", "").startswith("/")
                    or cookie.get("sameSite") not in {"Strict", "Lax", "None"}):
                raise ValueError
            raw_expiry = float(cookie.get("expires", expiry))
            if not math.isfinite(raw_expiry):
                raise ValueError
            cookie["expires"] = min(raw_expiry, expiry)
            if not math.isfinite(cookie["expires"]) or cookie["expires"] <= now:
                raise ValueError
        if any(item.get("origin") != origin for item in storage.get("origins", [])):
            raise ValueError
        if len(json.dumps(state, ensure_ascii=False).encode()) > 1048576:
            raise ValueError
        state["expiresAt"] = expiry
        return state
    except (KeyError, TypeError, ValueError, AttributeError):
        raise WebsiteSessionError("website_login_unsafe_response") from None
