from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evander.codex_auth import (
    DEFAULT_SUBSCRIPTION_PROFILE_ID,
    CodexSubscriptionDeferral,
    CodexSubscriptionDeferredError,
    normalize_subscription_profile_id,
)
from evander.storage import JsonStore

from .codex_auth_broker import CodexAuthBroker, CodexAuthBrokerError, CodexExternalAuthTokens


def _now_utc() -> datetime:
    return datetime.now(UTC)


class CodexAuthProfileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    display_name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    pinned_account_id: str | None = None
    created_at: datetime = Field(default_factory=_now_utc)
    updated_at: datetime = Field(default_factory=_now_utc)

    @field_validator("profile_id", mode="before")
    @classmethod
    def normalize_profile_id(cls, value: object) -> str:
        return str(normalize_subscription_profile_id(value, allow_none=False))

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("display_name is required")
        return text

    @field_validator("pinned_account_id", mode="before")
    @classmethod
    def normalize_account_id(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class CodexAuthProfileStore:
    """Worker-owned profile metadata. Token material stays in per-profile auth homes."""

    def __init__(self, path: Path) -> None:
        self.store = JsonStore(Path(path), CodexAuthProfileRecord, file_mode=0o600)
        self._ensure_default()

    def _ensure_default(self) -> None:
        now = _now_utc()

        def updater(records: dict[str, CodexAuthProfileRecord]) -> None:
            if DEFAULT_SUBSCRIPTION_PROFILE_ID not in records:
                records[DEFAULT_SUBSCRIPTION_PROFILE_ID] = CodexAuthProfileRecord(
                    profile_id=DEFAULT_SUBSCRIPTION_PROFILE_ID,
                    display_name="Default",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )

        self.store.update_all(updater)

    def list(self) -> list[CodexAuthProfileRecord]:
        return sorted(self.store.read_all().values(), key=lambda item: item.profile_id)

    def require(self, profile_id: object) -> CodexAuthProfileRecord:
        normalized = str(normalize_subscription_profile_id(profile_id, allow_none=False))
        record = self.store.get(normalized)
        if record is None:
            raise CodexAuthBrokerError(f"Codex subscription profile not found: {normalized}")
        return record

    def create(
        self,
        *,
        profile_id: object,
        display_name: object,
        enabled: bool = True,
    ) -> CodexAuthProfileRecord:
        normalized = str(normalize_subscription_profile_id(profile_id, allow_none=False))
        now = _now_utc()
        record = CodexAuthProfileRecord(
            profile_id=normalized,
            display_name=display_name,
            enabled=bool(enabled),
            created_at=now,
            updated_at=now,
        )

        def updater(records: dict[str, CodexAuthProfileRecord]) -> CodexAuthProfileRecord:
            if normalized in records:
                raise CodexAuthBrokerError(
                    f"Codex subscription profile already exists: {normalized}"
                )
            records[normalized] = record
            return record

        return self.store.update_all(updater)

    def patch(
        self,
        profile_id: object,
        *,
        display_name: object | None = None,
        enabled: bool | None = None,
    ) -> CodexAuthProfileRecord:
        normalized = str(normalize_subscription_profile_id(profile_id, allow_none=False))

        def updater(records: dict[str, CodexAuthProfileRecord]) -> CodexAuthProfileRecord:
            current = records.get(normalized)
            if current is None:
                raise CodexAuthBrokerError(f"Codex subscription profile not found: {normalized}")
            update: dict[str, Any] = {"updated_at": _now_utc()}
            if display_name is not None:
                update["display_name"] = CodexAuthProfileRecord.model_validate(
                    {**current.model_dump(), "display_name": display_name}
                ).display_name
            if enabled is not None:
                update["enabled"] = bool(enabled)
            changed = current.model_copy(update=update)
            records[normalized] = changed
            return changed

        return self.store.update_all(updater)

    def pin_account(self, profile_id: object, account_id: object) -> CodexAuthProfileRecord:
        normalized = str(normalize_subscription_profile_id(profile_id, allow_none=False))
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            raise CodexAuthBrokerError("Codex login did not provide an account id")

        def updater(records: dict[str, CodexAuthProfileRecord]) -> CodexAuthProfileRecord:
            current = records.get(normalized)
            if current is None:
                raise CodexAuthBrokerError(f"Codex subscription profile not found: {normalized}")
            if (
                current.pinned_account_id is not None
                and current.pinned_account_id != normalized_account_id
            ):
                raise CodexAuthBrokerError(
                    f"Codex subscription profile {normalized!r} is pinned to a different "
                    "OpenAI account; create a new profile for this account"
                )
            if current.pinned_account_id == normalized_account_id:
                return current
            changed = current.model_copy(
                update={
                    "pinned_account_id": normalized_account_id,
                    "updated_at": _now_utc(),
                }
            )
            records[normalized] = changed
            return changed

        return self.store.update_all(updater)


class CodexAuthBrokerRegistry:
    """Lazy profile-to-broker registry with independent process and refresh locks."""

    def __init__(
        self,
        *,
        worker_root: Path,
        app_server_command: Sequence[str] = ("codex", "app-server", "--stdio"),
        app_server_env: dict[str, str] | None = None,
        command_factory: Callable[[str, Path], Sequence[str]] | None = None,
        broker_factory: Callable[..., CodexAuthBroker] = CodexAuthBroker,
        initial_brokers: dict[str, CodexAuthBroker] | None = None,
    ) -> None:
        self.worker_root = Path(worker_root)
        self.auth_root = self.worker_root / "codex-auth"
        self.auth_root.mkdir(parents=True, exist_ok=True)
        self.auth_root.chmod(0o700)
        self.profiles = CodexAuthProfileStore(self.auth_root / "profiles.json")
        self._command = tuple(app_server_command)
        self._env = dict(app_server_env or {})
        self._command_factory = command_factory
        self._broker_factory = broker_factory
        self._brokers: dict[str, CodexAuthBroker] = dict(initial_brokers or {})
        self._lock = threading.RLock()

    def auth_home(self, profile_id: object) -> Path:
        normalized = str(normalize_subscription_profile_id(profile_id, allow_none=False))
        if normalized == DEFAULT_SUBSCRIPTION_PROFILE_ID:
            return self.auth_root
        return self.auth_root / "profiles" / normalized

    def broker(self, profile_id: object) -> CodexAuthBroker:
        record = self.profiles.require(profile_id)
        normalized = record.profile_id
        with self._lock:
            broker = self._brokers.get(normalized)
            if broker is not None:
                return broker
            auth_home = self.auth_home(normalized)
            command = (
                tuple(self._command_factory(normalized, auth_home))
                if self._command_factory is not None
                else self._command
            )
            broker = self._broker_factory(
                auth_home=auth_home,
                app_server_command=command,
                app_server_env=self._env,
            )
            self._brokers[normalized] = broker
            return broker

    def create_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        unsupported = sorted(set(payload) - {"profile_id", "display_name", "enabled"})
        if unsupported:
            raise ValueError("Unsupported profile fields: " + ", ".join(unsupported))
        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        record = self.profiles.create(
            profile_id=payload.get("profile_id"),
            display_name=payload.get("display_name"),
            enabled=enabled,
        )
        return self.profile_status(record.profile_id, include_account=False)

    def patch_profile(self, profile_id: object, payload: dict[str, Any]) -> dict[str, Any]:
        unsupported = sorted(set(payload) - {"display_name", "enabled"})
        if unsupported:
            raise ValueError("Unsupported profile fields: " + ", ".join(unsupported))
        if "display_name" in payload and payload["display_name"] is None:
            raise ValueError("display_name is required")
        if "enabled" in payload and not isinstance(payload["enabled"], bool):
            raise ValueError("enabled must be a boolean")
        record = self.profiles.patch(
            profile_id,
            display_name=payload.get("display_name") if "display_name" in payload else None,
            enabled=payload.get("enabled") if "enabled" in payload else None,
        )
        return self.profile_status(record.profile_id)

    def list_profiles(self) -> dict[str, Any]:
        return {"profiles": [self.profile_status(item.profile_id) for item in self.profiles.list()]}

    def profile_status(
        self,
        profile_id: object,
        *,
        include_account: bool = True,
    ) -> dict[str, Any]:
        record = self.profiles.require(profile_id)
        status = self._empty_status()
        if include_account:
            try:
                broker = self.broker(record.profile_id)
                status = broker.status()
                self._pin_status_account(record, status, broker=broker)
                record = self.profiles.require(record.profile_id)
            except CodexAuthBrokerError as exc:
                status = self._empty_status(error=str(exc), available=False)
        return {
            **record.model_dump(mode="json"),
            **status,
        }

    def start_device_login(self, profile_id: object) -> dict[str, Any]:
        record = self._require_enabled(profile_id)
        return self.broker(record.profile_id).start_device_login()

    def device_login_status(self, profile_id: object, login_id: str) -> dict[str, Any]:
        record = self.profiles.require(profile_id)
        broker = self.broker(record.profile_id)
        login = broker.device_login_status(login_id)
        if login.get("status") == "completed":
            try:
                status = broker.status()
                self._pin_status_account(record, status, broker=broker)
            except CodexAuthBrokerError as exc:
                return {**login, "status": "failed", "error": str(exc)}
        return login

    def cancel_device_login(self, profile_id: object, login_id: str) -> dict[str, Any]:
        return self.broker(profile_id).cancel_device_login(login_id)

    def logout(self, profile_id: object) -> dict[str, Any]:
        self.broker(profile_id).logout()
        return self.profile_status(profile_id)

    def preflight(self, profile_id: object) -> CodexExternalAuthTokens:
        normalized = str(normalize_subscription_profile_id(profile_id, allow_none=False))
        try:
            record = self._require_enabled(normalized)
        except CodexSubscriptionDeferredError:
            raise
        except CodexAuthBrokerError as exc:
            raise CodexSubscriptionDeferredError(
                CodexSubscriptionDeferral(profile_id=normalized, reason="unavailable"),
                str(exc),
            ) from exc
        broker = self.broker(record.profile_id)
        try:
            status = broker.status()
        except CodexAuthBrokerError as exc:
            raise CodexSubscriptionDeferredError(
                CodexSubscriptionDeferral(
                    profile_id=record.profile_id,
                    reason="unavailable",
                ),
                str(exc),
            ) from exc
        try:
            self._pin_status_account(record, status, broker=broker)
        except CodexAuthBrokerError as exc:
            raise CodexSubscriptionDeferredError(
                CodexSubscriptionDeferral(
                    profile_id=record.profile_id,
                    reason="unavailable",
                ),
                str(exc),
            ) from exc
        if not status.get("signed_in"):
            raise CodexSubscriptionDeferredError(
                CodexSubscriptionDeferral(profile_id=record.profile_id, reason="signed_out")
            )
        if not status.get("available"):
            raise CodexSubscriptionDeferredError(
                CodexSubscriptionDeferral(profile_id=record.profile_id, reason="unavailable"),
                str(status.get("error") or "subscription profile is unavailable"),
            )
        rate_limited, retry_at = _rate_limit_state(status.get("rate_limits"))
        if rate_limited:
            raise CodexSubscriptionDeferredError(
                CodexSubscriptionDeferral(
                    profile_id=record.profile_id,
                    reason="rate_limited",
                    retry_at=retry_at,
                )
            )
        try:
            tokens = broker.external_tokens()
            self.profiles.pin_account(record.profile_id, tokens.account_id)
            return tokens
        except CodexAuthBrokerError as exc:
            raise CodexSubscriptionDeferredError(
                CodexSubscriptionDeferral(profile_id=record.profile_id, reason="unavailable"),
                str(exc),
            ) from exc

    def close(self) -> None:
        with self._lock:
            brokers = list(self._brokers.values())
            self._brokers.clear()
        for broker in brokers:
            broker.close()

    def _require_enabled(self, profile_id: object) -> CodexAuthProfileRecord:
        record = self.profiles.require(profile_id)
        if not record.enabled:
            raise CodexSubscriptionDeferredError(
                CodexSubscriptionDeferral(profile_id=record.profile_id, reason="disabled")
            )
        return record

    def _pin_status_account(
        self,
        record: CodexAuthProfileRecord,
        status: dict[str, Any],
        *,
        broker: CodexAuthBroker,
    ) -> None:
        if not status.get("signed_in"):
            return
        account_id = str(status.get("account_id") or "").strip()
        if not account_id:
            raise CodexAuthBrokerError("Codex login did not provide an account id")
        try:
            self.profiles.pin_account(record.profile_id, account_id)
        except CodexAuthBrokerError:
            # Never leave a profile authenticated as an identity other than the pin.
            try:
                broker.logout()
            except CodexAuthBrokerError:
                pass
            raise

    @staticmethod
    def _empty_status(*, error: str | None = None, available: bool = True) -> dict[str, Any]:
        return {
            "available": available,
            "signed_in": False,
            "account_id": None,
            "account_type": None,
            "email": None,
            "plan_type": None,
            "last_refresh": None,
            "access_token_expires_at": None,
            "rate_limits": None,
            "usage": None,
            "error": error,
        }


def _rate_limit_state(value: object) -> tuple[bool, datetime | None]:
    if not isinstance(value, dict):
        return False, None
    buckets: list[dict[str, Any]] = []
    default = value.get("default")
    if isinstance(default, dict):
        buckets.append(default)
    by_id = value.get("by_limit_id")
    if isinstance(by_id, dict):
        buckets.extend(bucket for bucket in by_id.values() if isinstance(bucket, dict))
    reset_times: list[datetime] = []
    reached = False
    for bucket in buckets:
        bucket_reached = bool(bucket.get("rate_limit_reached_type"))
        reached = reached or bucket_reached
        for window_name in ("primary", "secondary"):
            window = bucket.get(window_name)
            if not isinstance(window, dict):
                continue
            try:
                window_reached = float(window.get("used_percent")) >= 100
            except (TypeError, ValueError):
                window_reached = False
            if not window_reached:
                continue
            reached = True
            reset_text = str(window.get("resets_at") or "").strip()
            if not reset_text:
                continue
            try:
                parsed = datetime.fromisoformat(reset_text.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            reset_times.append(parsed.astimezone(UTC))
    if not reached:
        return False, None
    future = [value for value in reset_times if value > _now_utc()]
    # Capacity is unavailable while any exhausted window remains closed.  If
    # more than one window is exhausted, waking at the earliest reset only
    # produces another guaranteed deferral.
    return True, max(future) if future else None
