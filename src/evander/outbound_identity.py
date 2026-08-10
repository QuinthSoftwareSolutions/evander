from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol


OutboundIdentityProvider = Literal["discord"]


@dataclass(frozen=True, slots=True)
class OutboundAvatar:
    content: bytes
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    sha256: str

    @classmethod
    def from_bytes(
        cls,
        content: bytes,
        *,
        mime_type: Literal["image/png", "image/jpeg", "image/webp"],
        expected_sha256: str | None = None,
    ) -> "OutboundAvatar":
        if not content:
            raise ValueError("outbound avatar content must not be empty")
        if len(content) > 5 * 1024 * 1024:
            raise ValueError("outbound avatar content exceeds 5 MiB")
        digest = hashlib.sha256(content).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256.strip().lower():
            raise ValueError("outbound avatar content does not match its recorded SHA-256")
        return cls(content=content, mime_type=mime_type, sha256=digest)


@dataclass(frozen=True, slots=True)
class OutboundIdentity:
    display_name: str
    source: str
    avatar: OutboundAvatar | None = None

    def __post_init__(self) -> None:
        display_name = str(self.display_name or "").strip()
        source = str(self.source or "").strip()
        if not display_name:
            raise ValueError("outbound identity display_name must not be empty")
        if len(display_name) > 80:
            raise ValueError("outbound identity display_name must be at most 80 characters")
        if not source:
            raise ValueError("outbound identity source must not be empty")
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "source", source)


class OutboundIdentityResolver(Protocol):
    def __call__(
        self,
        *,
        session_id: str,
        provider: OutboundIdentityProvider,
        destination: str,
    ) -> OutboundIdentity | None: ...


@dataclass(frozen=True, slots=True)
class OutboundIdentityResolution:
    identity: OutboundIdentity | None
    status: Literal["resolved", "not_found", "conflict", "error"]
    matched_sources: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(slots=True)
class OutboundIdentityRegistry:
    _resolvers: list[tuple[str, OutboundIdentityResolver]] = field(default_factory=list)

    def register(self, source: str, resolver: OutboundIdentityResolver) -> None:
        normalized = str(source or "").strip()
        if not normalized:
            raise ValueError("outbound identity resolver source must not be empty")
        for index, (current_source, _) in enumerate(self._resolvers):
            if current_source == normalized:
                self._resolvers[index] = (normalized, resolver)
                return
        self._resolvers.append((normalized, resolver))

    def resolve(
        self,
        *,
        session_id: str,
        provider: OutboundIdentityProvider,
        destination: str,
    ) -> OutboundIdentityResolution:
        matches: list[OutboundIdentity] = []
        matched_sources: list[str] = []
        errors: list[str] = []
        for source, resolver in self._resolvers:
            try:
                identity = resolver(
                    session_id=session_id,
                    provider=provider,
                    destination=destination,
                )
            except Exception as exc:
                errors.append(f"{source}: {type(exc).__name__}: {exc}")
                continue
            if identity is None:
                continue
            matches.append(identity)
            matched_sources.append(source)
        if len(matches) > 1:
            return OutboundIdentityResolution(
                identity=None,
                status="conflict",
                matched_sources=tuple(matched_sources),
                errors=tuple(errors),
            )
        if matches:
            return OutboundIdentityResolution(
                identity=matches[0],
                status="resolved",
                matched_sources=tuple(matched_sources),
                errors=tuple(errors),
            )
        return OutboundIdentityResolution(
            identity=None,
            status="error" if errors else "not_found",
            errors=tuple(errors),
        )


OutboundIdentityResolverFactory = Callable[[object], OutboundIdentityResolver]
