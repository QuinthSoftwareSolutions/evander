from __future__ import annotations

import shutil
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evander.codex_auth import (
    DEFAULT_SUBSCRIPTION_PROFILE_ID,
    CodexRateLimitResetRequest,
    CodexSubscriptionDeferral,
    CodexSubscriptionDeferredError,
    normalize_subscription_profile_id,
)
from evander.codex_capacity import subscription_rate_limit_state
from evander.storage import JsonStore

from .codex_auth_broker import CodexAuthBroker, CodexAuthBrokerError, CodexExternalAuthTokens


def _now_utc() -> datetime:
    return datetime.now(UTC)


class CodexAuthProfileInUseError(CodexAuthBrokerError):
    """A worker job still owns or is acquiring this profile."""


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
        migrate_legacy = not (self.auth_root / "profiles.json").exists() and (
            (self.auth_root / "auth.json").is_file()
            or DEFAULT_SUBSCRIPTION_PROFILE_ID in (initial_brokers or {})
        )
        self.profiles = CodexAuthProfileStore(self.auth_root / "profiles.json")
        if migrate_legacy:
            self.profiles.create(profile_id="default", display_name="Default")
        self._command = tuple(app_server_command)
        self._env = dict(app_server_env or {})
        self._command_factory = command_factory
        self._broker_factory = broker_factory
        self._brokers: dict[str, CodexAuthBroker] = dict(initial_brokers or {})
        self._lock = threading.RLock()
        self._profile_users: dict[str, int] = {}

    def auth_home(self, profile_id: object) -> Path:
        normalized = str(normalize_subscription_profile_id(profile_id, allow_none=False))
        if normalized == DEFAULT_SUBSCRIPTION_PROFILE_ID:
            return self.auth_root
        return self.auth_root / "profiles" / normalized

    def broker(self, profile_id: object) -> CodexAuthBroker:
        with self._lock:
            record = self.profiles.require(profile_id)
            normalized = record.profile_id
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

    @contextmanager
    def use_profile(self, profile_id: object) -> Iterator[None]:
        """Reserve a profile until an admitted job has a durable worker record."""
        normalized = str(normalize_subscription_profile_id(profile_id, allow_none=False))
        with self._lock:
            self._profile_users[normalized] = self._profile_users.get(normalized, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._profile_users[normalized] -= 1
                if not self._profile_users[normalized]:
                    del self._profile_users[normalized]

    def delete_profile(
        self, profile_id: object, *, has_active_jobs: Callable[[], bool]
    ) -> dict[str, Any]:
        normalized = str(normalize_subscription_profile_id(profile_id, allow_none=False))
        with self._lock:
            if self._profile_users.get(normalized) or has_active_jobs():
                raise CodexAuthProfileInUseError("Subscription profile has active jobs")
            if self.profiles.store.get(normalized) is None:
                return {"profile_id": normalized, "deleted": True}
            broker = self._brokers.get(normalized)
            if broker is not None:
                broker.close()
                del self._brokers[normalized]
            auth_home = self.auth_home(normalized)
            if normalized == DEFAULT_SUBSCRIPTION_PROFILE_ID:
                # The legacy home also contains the registry and all named homes.
                (auth_home / "auth.json").unlink(missing_ok=True)
            elif auth_home.exists():
                shutil.rmtree(auth_home)
            self.profiles.store.delete(normalized)
            return {"profile_id": normalized, "deleted": True}

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
        profiles = []
        for item in self.profiles.list():
            try:
                profiles.append(self.profile_status(item.profile_id))
            except CodexAuthBrokerError:
                # A concurrent delete may have removed this snapshot entry.
                if self.profiles.store.get(item.profile_id) is not None:
                    raise
        return {"profiles": profiles}

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

    def consume_reset_credit(
        self, profile_id: object, request: CodexRateLimitResetRequest
    ) -> dict[str, Any]:
        record = self._require_enabled(profile_id)
        broker = self.broker(record.profile_id)
        # Capacity exhaustion is precisely when redemption is useful. Check the
        # account pin without the turn preflight's rate-limit rejection.
        status = broker.status(include_usage=False)
        self._pin_status_account(record, status, broker=broker)
        if not status.get("signed_in") or not status.get("available"):
            raise CodexAuthBrokerError(
                str(status.get("error") or "Subscription profile must be signed in and available")
            )
        if (
            request.expected_account_id is not None
            and request.expected_account_id != status["account_id"]
        ):
            raise CodexAuthBrokerError("Subscription account changed before reset redemption")
        result = broker.consume_reset_credit(
            request, expected_account_id=request.expected_account_id or str(status["account_id"])
        )
        # Read authoritative windows and credit counts after every definitive
        # outcome, including an idempotent replay of a successful redemption.
        return {**result, "profile": self.profile_status(record.profile_id)}

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
            # Usage totals are dashboard telemetry, not a prerequisite for a
            # turn. Keep fresh account/capacity checks without that extra RPC.
            status = broker.status(include_usage=False)
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
        rate_limited, retry_at = subscription_rate_limit_state(
            status.get("rate_limits"), now=_now_utc()
        )
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

    def refresh_tokens(
        self,
        profile_id: object,
        *,
        previous_access_token: str | None = None,
        previous_account_id: str | None = None,
    ) -> CodexExternalAuthTokens:
        """Refresh a running turn without bypassing the profile account pin."""

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
            tokens = broker.external_tokens(
                previous_access_token=previous_access_token,
                previous_account_id=previous_account_id,
            )
            self.profiles.pin_account(record.profile_id, tokens.account_id)
        except CodexAuthBrokerError as exc:
            # A concurrent login may have replaced the worker-owned auth state.
            # Fail closed and remove that mismatched identity before it can reach
            # the already-bound turn.
            try:
                broker.logout()
            except CodexAuthBrokerError:
                pass
            raise CodexSubscriptionDeferredError(
                CodexSubscriptionDeferral(profile_id=record.profile_id, reason="unavailable"),
                str(exc),
            ) from exc
        return tokens

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
