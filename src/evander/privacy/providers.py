"""Host-owned outbound privacy approvals.

This is a dispatch gate, not a content classifier or a complete egress firewall.
Approvals bind the resolved credential reference and configured endpoint; operators
must verify which external account that reference represents when approving it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)
_dispatch_check: ContextVar[Callable[[], None] | None] = ContextVar(
    "privacy_dispatch_check", default=None
)


@contextmanager
def provider_dispatch_scope(check: Callable[[], None]):
    """Carry a host check into provider-internal retries, scoped to one call."""
    token = _dispatch_check.set(check)
    try:
        yield
    finally:
        _dispatch_check.reset(token)


def check_provider_dispatch() -> None:
    check = _dispatch_check.get()
    if check is not None:
        check()


# Keep this deliberately narrower than the product's complete outbound inventory.
INSTRUMENTED_PROVIDER_CAPABILITIES = frozenset(
    {"model_relay", "classification", "codex_subscription"}
)


def canonical_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "*" in parsed.netloc
        or any(character.isspace() for character in value)
    ):
        raise ValueError("provider endpoint must be an absolute HTTP(S) URL without credentials")
    host = parsed.hostname.rstrip(".").lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port and port != (443 if parsed.scheme == "https" else 80):
        host = f"{host}:{port}"
    # Compare configured endpoints exactly. No prefix or hostname-suffix grants.
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


class ProviderApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=160)
    connection_id: str = Field(min_length=1, max_length=320)
    endpoint: str
    allowed_categories: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    legal_review_reference: str = Field(default="", max_length=1000)
    approved_by: str = Field(default="", max_length=320)
    status: Literal["pending", "approved", "revoked"] = "pending"
    installation_wide: bool = False
    expires_at: datetime | None = None

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return canonical_endpoint(value)

    @field_validator("approval_id", "provider", "connection_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("provider identifiers must not contain surrounding whitespace")
        return value

    @field_validator("allowed_categories", "capabilities")
    @classmethod
    def validate_labels(cls, values: list[str]) -> list[str]:
        if any(not value or value != value.strip() for value in values):
            raise ValueError("provider categories and capabilities must be nonempty labels")
        return sorted(set(values))

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("approval expiry must include a timezone")
        return value


class ProviderPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approvals: list[ProviderApproval] = Field(default_factory=list)

    @field_validator("approvals")
    @classmethod
    def validate_unique_ids(cls, values: list[ProviderApproval]) -> list[ProviderApproval]:
        if len({value.approval_id for value in values}) != len(values):
            raise ValueError("provider approval IDs must be unique")
        return values


@dataclass(frozen=True)
class ProviderDispatchDecision:
    allowed: bool
    would_deny: bool
    reason: str
    enforced: bool = False


class PrivacyDispatchDenied(PermissionError):
    """Safe to surface without disclosing policy, account, or payload details."""

    def __init__(self) -> None:
        super().__init__("Outbound request is blocked by privacy policy")


def _value(settings: Any, name: str, default: Any = None) -> Any:
    return (
        settings.get(name, default)
        if isinstance(settings, dict)
        else getattr(settings, name, default)
    )


class ProviderDispatchGuard:
    def __init__(
        self,
        settings_loader: Callable[[str | None], Sequence[Any]],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings_loader = settings_loader
        self.clock = clock
        self._last_log: dict[tuple[bool, str, str], float] = {}

    def check(
        self,
        *,
        provider: str,
        endpoint: str,
        connection_id: str,
        directory_path: str | None,
        capability: str,
        installation_wide: bool = False,
    ) -> ProviderDispatchDecision:
        """Re-read policy on every call; never accept guest-provided categories.

        Loader order is root followed by effective directory settings. Every
        applicable enforcing layer must approve; a child cannot loosen root.
        Subscription dispatch checks the union of all returned directory labels.
        """
        try:
            layers = tuple(self.settings_loader(directory_path))
            if not layers:
                raise ValueError("privacy settings are unavailable")
            enforced = any(_value(layer, "mode", "report_only") == "enforce" for layer in layers)
            try:
                normalized_endpoint = canonical_endpoint(endpoint)
            except ValueError:
                normalized_endpoint = ""
            root_categories = set(_value(layers[0], "enabled_categories", ["unknown"]))
            categories = (
                set().union(
                    *(set(_value(layer, "enabled_categories", ["unknown"])) for layer in layers)
                )
                if installation_wide
                else set(_value(layers[-1], "enabled_categories", ["unknown"]))
            )
            reason = "approved"
            if capability not in INSTRUMENTED_PROVIDER_CAPABILITIES:
                reason = "unsupported_capability"
            elif not normalized_endpoint:
                reason = "unattributed_endpoint"
            elif not directory_path and not installation_wide:
                reason = "unattributed_directory"
            elif not categories or "unknown" in categories:
                reason = "unknown_data_categories"
            elif not categories.issubset(root_categories):
                reason = "categories_outside_installation_policy"
            else:
                # Root and effective directory must both approve even if a child
                # sets report_only beneath an enforcing root policy.
                for layer in layers:
                    policy = ProviderPolicy.model_validate(_value(layer, "provider_policy", {}))
                    matching = [
                        approval
                        for approval in policy.approvals
                        if approval.provider == provider
                        and approval.endpoint == normalized_endpoint
                        and approval.connection_id == connection_id
                        and capability in approval.capabilities
                        and (not installation_wide or approval.installation_wide)
                    ]
                    if not any(
                        approval.status == "approved"
                        and approval.approved_by.strip()
                        and approval.legal_review_reference.strip()
                        and (approval.expires_at is None or approval.expires_at > self.clock())
                        and categories.issubset(set(approval.allowed_categories))
                        for approval in matching
                    ):
                        reason = "missing_current_approval"
                        break
            decision = ProviderDispatchDecision(
                allowed=not enforced or reason == "approved",
                would_deny=reason != "approved",
                reason=reason,
                enforced=enforced,
            )
        except Exception:
            # A broken policy loader must never silently switch back to report-only.
            decision = ProviderDispatchDecision(False, True, "policy_unavailable", True)
        if decision.would_deny:
            # Streaming and CONNECT recheck every second. Keep report-only
            # migration diagnostics useful without logging every token/chunk.
            key = (decision.allowed, capability, decision.reason)
            timestamp = time.monotonic()
            if timestamp - self._last_log.get(key, float("-inf")) >= 60:
                self._last_log[key] = timestamp
                logger.info(
                    "privacy_provider_dispatch allowed=%s capability=%s reason=%s",
                    decision.allowed,
                    capability,
                    decision.reason,
                )
        return decision

    def require(self, **dispatch: Any) -> ProviderDispatchDecision:
        decision = self.check(**dispatch)
        if not decision.allowed:
            raise PrivacyDispatchDenied()
        return decision


def config_provider_guard(config: Any) -> ProviderDispatchGuard:
    """Read authoritative directory policy without creating files on dispatch."""

    def load(directory_path: str | None) -> Sequence[Any]:
        from evander.directories import DirectoryService, DirectoryStore
        from evander.directory_config import (
            DirectoryConfigResolver,
            DirectoryConfigStore,
            get_domain_base_config,
            validate_effective_domain_payload,
        )

        base = getattr(config, "privacy", None)
        # Tests and pre-bootstrap installations need no new privacy database or
        # directory registry merely to preserve their report-only defaults.
        if not Path(config.directories_store_path).exists():
            if _value(base, "mode", "report_only") == "enforce" and directory_path not in {
                None,
                "root",
            }:
                raise ValueError("unknown privacy directory")
            return [base or {"mode": "report_only", "enabled_categories": ["unknown"]}]
        directory_store = DirectoryStore(config.directories_store_path)
        service = DirectoryService(directory_store)
        resolver = DirectoryConfigResolver(
            service,
            base_gateway_config=config,
            config_store=DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir),
        )

        def resolve(path: str) -> Any:
            resolution = resolver.resolve_domain(
                path,
                domain="privacy",
                base_config=get_domain_base_config(config, "privacy"),
                validator=lambda payload: validate_effective_domain_payload("privacy", payload),
            )
            if resolution.validation_errors:
                raise ValueError("invalid privacy directory configuration")
            return resolution.effective_config["privacy"]

        root = resolve("root")
        if directory_path is None:
            # CONNECT cannot attribute a customer/session. Include every child,
            # including dormant ones, rather than guessing which traffic is live.
            return [root, *(resolve(item.path) for item in service.list_descendants("root"))]
        return [root] if directory_path == "root" else [root, resolve(directory_path)]

    return ProviderDispatchGuard(load)


def runtime_provider_guard(runtime_dir: Path) -> ProviderDispatchGuard:
    # GatewayConfig applies path defaults without bootstrapping any on-disk state.
    from evander.config import GatewayConfig
    from evander.host_config import HostConfig, build_runtime_gateway_config

    def load(directory_path: str | None) -> Sequence[Any]:
        path = runtime_dir / "host-config.json"
        config = (
            build_runtime_gateway_config(HostConfig.model_validate_json(path.read_text()))
            if path.exists()
            else GatewayConfig(runtime_dir=runtime_dir)
        )
        return config_provider_guard(config).settings_loader(directory_path)

    return ProviderDispatchGuard(load)
