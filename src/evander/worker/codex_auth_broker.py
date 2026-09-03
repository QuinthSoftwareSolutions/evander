from __future__ import annotations

import base64
import fcntl
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any, Callable, Protocol

from .codex_app_server import CodexAppServerError, CodexAppServerProcess


class CodexAuthBrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexExternalAuthTokens:
    access_token: str
    account_id: str
    plan_type: str | None

    def app_server_login_params(self) -> dict[str, str]:
        params = {
            "type": "chatgptAuthTokens",
            "accessToken": self.access_token,
            "chatgptAccountId": self.account_id,
        }
        if self.plan_type:
            params["chatgptPlanType"] = self.plan_type
        return params

    def refresh_response(self) -> dict[str, str]:
        response = {
            "accessToken": self.access_token,
            "chatgptAccountId": self.account_id,
        }
        if self.plan_type:
            response["chatgptPlanType"] = self.plan_type
        return response


class CodexManagedAuthClient(Protocol):
    @property
    def running(self) -> bool: ...

    def start(self) -> None: ...

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


class CodexAuthBroker:
    """Owns the worker's single durable Codex ``auth.json`` and refresh flow."""

    def __init__(
        self,
        *,
        auth_home: Path,
        app_server_command: list[str] | tuple[str, ...] = ("codex", "app-server", "--stdio"),
        app_server_env: dict[str, str] | None = None,
        client_factory: Callable[..., CodexManagedAuthClient] = CodexAppServerProcess,
        refresh_skew_seconds: int = 300,
    ) -> None:
        self.auth_home = Path(auth_home)
        self.auth_home.mkdir(parents=True, exist_ok=True)
        self.auth_home.chmod(0o700)
        self.auth_path = self.auth_home / "auth.json"
        self._command = tuple(app_server_command)
        self._env = dict(app_server_env or os.environ)
        self._env["CODEX_HOME"] = str(self.auth_home)
        self._client_factory = client_factory
        self._refresh_skew_seconds = max(0, int(refresh_skew_seconds))
        self._lock = threading.RLock()
        self._managed_client: CodexManagedAuthClient | None = None
        self._device_logins: dict[str, dict[str, Any]] = {}
        self._login_notifications: SimpleQueue[dict[str, Any]] = SimpleQueue()
        self._lifecycle_file = (self.auth_home / ".broker.lock").open("a+")
        try:
            fcntl.flock(self._lifecycle_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lifecycle_file.close()
            raise CodexAuthBrokerError(
                f"another Codex auth broker already owns {self.auth_home}"
            ) from exc

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._drain_login_notifications_locked()
            account: dict[str, Any] | None = None
            error: str | None = None
            try:
                account = self._account_read(refresh=False)
            except CodexAuthBrokerError as exc:
                error = str(exc)
            auth_payload = self._read_auth_payload(required=False)
            tokens = auth_payload.get("tokens") if isinstance(auth_payload, dict) else None
            token_payload = tokens if isinstance(tokens, dict) else {}
            access_token = token_payload.get("access_token")
            expires_at = _jwt_expiration(access_token) if isinstance(access_token, str) else None
            account_value = account.get("account") if isinstance(account, dict) else None
            account_dict = account_value if isinstance(account_value, dict) else {}
            signed_in = bool(token_payload.get("refresh_token") and token_payload.get("account_id"))
            rate_limits: dict[str, Any] | None = None
            usage: dict[str, Any] | None = None
            if signed_in and error is None:
                try:
                    rate_limits = _safe_rate_limits_payload(
                        self._ensure_client().request(
                            "account/rateLimits/read", {}, timeout_seconds=30.0
                        )
                    )
                except (CodexAppServerError, CodexAuthBrokerError) as exc:
                    error = str(exc)
                if error is None:
                    try:
                        usage = _safe_usage_payload(
                            self._ensure_client().request(
                                "account/usage/read", {}, timeout_seconds=30.0
                            )
                        )
                    except (CodexAppServerError, CodexAuthBrokerError):
                        # Usage is display-only telemetry. An unsupported or transiently
                        # unavailable usage RPC must not prevent authenticated turns.
                        usage = None
            return {
                "available": error is None,
                "signed_in": signed_in,
                "account_id": (
                    str(token_payload.get("account_id") or "").strip() or None
                    if signed_in
                    else None
                ),
                "account_type": account_dict.get("type") if signed_in else None,
                "email": account_dict.get("email") if signed_in else None,
                "plan_type": account_dict.get("planType") if signed_in else None,
                "last_refresh": auth_payload.get("last_refresh") if signed_in else None,
                "access_token_expires_at": (
                    datetime.fromtimestamp(expires_at, tz=UTC).isoformat()
                    if expires_at is not None
                    else None
                ),
                "rate_limits": rate_limits,
                "usage": usage,
                "error": error,
            }

    def start_device_login(self) -> dict[str, Any]:
        with self._lock:
            self._drain_login_notifications_locked()
            client = self._ensure_client()
            result = client.request("account/login/start", {"type": "chatgptDeviceCode"})
            login_id = str(result.get("loginId") or "").strip()
            verification_url = str(result.get("verificationUrl") or "").strip()
            user_code = str(result.get("userCode") or "").strip()
            if not login_id or not verification_url or not user_code:
                raise CodexAuthBrokerError("Codex returned an incomplete device-login response")
            record = {
                "login_id": login_id,
                "status": "pending",
                "verification_url": verification_url,
                "user_code": user_code,
                "error": None,
            }
            self._device_logins[login_id] = record
            self._drain_login_notifications_locked()
            return dict(record)

    def device_login_status(self, login_id: str) -> dict[str, Any]:
        with self._lock:
            self._drain_login_notifications_locked()
            record = self._device_logins.get(str(login_id).strip())
            if record is None:
                raise CodexAuthBrokerError("device login not found")
            return dict(record)

    def cancel_device_login(self, login_id: str) -> dict[str, Any]:
        with self._lock:
            self._drain_login_notifications_locked()
            record = self._device_logins.get(str(login_id).strip())
            if record is None:
                raise CodexAuthBrokerError("device login not found")
            self._ensure_client().request("account/login/cancel", {"loginId": login_id})
            record["status"] = "cancelled"
            # Completion may race cancellation. Apply any notification that the
            # app-server reader queued while the request was in flight last.
            self._drain_login_notifications_locked()
            return dict(record)

    def logout(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_client().request("account/logout", {})
            return self.status()

    def external_tokens(
        self,
        *,
        previous_access_token: str | None = None,
        previous_account_id: str | None = None,
    ) -> CodexExternalAuthTokens:
        """Return an in-memory token snapshot, refreshing once under the broker lock.

        Jobs call this initially and again only after the app-server reports a 401.
        A stale 401 cannot trigger a second refresh after another job has already
        rotated the shared token.
        """

        with self._lock:
            payload = self._read_auth_payload(required=True)
            current = self._tokens_from_payload(payload)
            expired_soon = _jwt_expires_within(current.access_token, self._refresh_skew_seconds)
            same_failed_token = bool(
                previous_access_token and previous_access_token == current.access_token
            )
            same_account = not previous_account_id or previous_account_id == current.account_id
            if expired_soon or (same_failed_token and same_account):
                self._account_read(refresh=True)
                payload = self._read_auth_payload(required=True)
                refreshed = self._tokens_from_payload(payload)
                if same_failed_token and refreshed.access_token == previous_access_token:
                    raise CodexAuthBrokerError(
                        "Codex refreshed the account but did not rotate the rejected access token"
                    )
                current = refreshed
            return current

    def close(self) -> None:
        with self._lock:
            if self._managed_client is not None:
                self._managed_client.close()
                self._managed_client = None
            try:
                fcntl.flock(self._lifecycle_file.fileno(), fcntl.LOCK_UN)
            finally:
                self._lifecycle_file.close()

    def _ensure_client(self) -> CodexManagedAuthClient:
        client = self._managed_client
        if client is not None and client.running:
            return client
        try:
            client = self._client_factory(
                self._command,
                env=self._env,
                request_timeout_seconds=30.0,
                notification_handler=self._handle_notification,
            )
            client.start()
        except (CodexAppServerError, OSError) as exc:
            raise CodexAuthBrokerError(str(exc)) from exc
        self._managed_client = client
        return client

    def _account_read(self, *, refresh: bool) -> dict[str, Any]:
        try:
            return self._ensure_client().request(
                "account/read", {"refreshToken": refresh}, timeout_seconds=30.0
            )
        except CodexAppServerError as exc:
            raise CodexAuthBrokerError(str(exc)) from exc

    def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if method != "account/login/completed":
            return
        login_id = str(params.get("loginId") or "").strip()
        if not login_id:
            return
        # This callback runs on the sole app-server reader. Never make that reader
        # wait for a broker method that may itself be waiting for a protocol response.
        self._login_notifications.put(dict(params))

    def _drain_login_notifications_locked(self) -> None:
        while True:
            try:
                params = self._login_notifications.get_nowait()
            except Empty:
                return
            login_id = str(params.get("loginId") or "").strip()
            if not login_id:
                continue
            record = self._device_logins.setdefault(
                login_id,
                {
                    "login_id": login_id,
                    "verification_url": None,
                    "user_code": None,
                },
            )
            success = bool(params.get("success"))
            record["status"] = "completed" if success else "failed"
            record["error"] = None if success else str(params.get("error") or "login failed")

    def _read_auth_payload(self, *, required: bool) -> dict[str, Any]:
        try:
            raw = self.auth_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if required:
                raise CodexAuthBrokerError("ChatGPT subscription account is not signed in")
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CodexAuthBrokerError("Codex auth.json is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise CodexAuthBrokerError("Codex auth.json must contain a JSON object")
        return payload

    @staticmethod
    def _tokens_from_payload(payload: dict[str, Any]) -> CodexExternalAuthTokens:
        raw_tokens = payload.get("tokens")
        tokens = raw_tokens if isinstance(raw_tokens, dict) else {}
        access_token = str(tokens.get("access_token") or "").strip()
        account_id = str(tokens.get("account_id") or "").strip()
        if not access_token or not account_id:
            raise CodexAuthBrokerError("Codex auth.json is missing ChatGPT account tokens")
        plan_type = _jwt_plan_type(access_token)
        return CodexExternalAuthTokens(
            access_token=access_token,
            account_id=account_id,
            plan_type=plan_type,
        )


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    encoded = parts[1] + ("=" * (-len(parts[1]) % 4))
    try:
        decoded = base64.urlsafe_b64decode(encoded.encode("ascii"))
        payload = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _jwt_expiration(token: str) -> int | None:
    value = _jwt_payload(token).get("exp")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _jwt_expires_within(token: str, skew_seconds: int) -> bool:
    expiration = _jwt_expiration(token)
    return expiration is not None and expiration <= int(time.time()) + max(0, skew_seconds)


def _jwt_plan_type(token: str) -> str | None:
    payload = _jwt_payload(token)
    auth_claims = payload.get("https://api.openai.com/auth")
    if isinstance(auth_claims, dict):
        value = auth_claims.get("chatgpt_plan_type")
        if value:
            return str(value)
    value = payload.get("chatgpt_plan_type")
    return str(value) if value else None


def _safe_rate_limit_window(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    resets_at = value.get("resetsAt")
    try:
        resets_epoch = int(resets_at) if resets_at is not None else None
    except (TypeError, ValueError):
        resets_epoch = None
    return {
        "used_percent": value.get("usedPercent"),
        "window_duration_minutes": value.get("windowDurationMins"),
        "resets_at": (
            datetime.fromtimestamp(resets_epoch, tz=UTC).isoformat()
            if resets_epoch is not None
            else None
        ),
    }


def _safe_rate_limit_bucket(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "limit_id": value.get("limitId"),
        "limit_name": value.get("limitName"),
        "plan_type": value.get("planType"),
        "primary": _safe_rate_limit_window(value.get("primary")),
        "secondary": _safe_rate_limit_window(value.get("secondary")),
        "rate_limit_reached_type": value.get("rateLimitReachedType"),
    }


def _safe_rate_limits_payload(value: object) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    by_id = payload.get("rateLimitsByLimitId")
    safe_by_id: dict[str, Any] = {}
    if isinstance(by_id, dict):
        for raw_id, raw_bucket in by_id.items():
            limit_id = str(raw_id or "").strip()
            bucket = _safe_rate_limit_bucket(raw_bucket)
            if limit_id and bucket is not None:
                safe_by_id[limit_id] = bucket
    reset_credits = payload.get("rateLimitResetCredits")
    safe_reset_credits: dict[str, Any] | None = None
    if isinstance(reset_credits, dict):
        safe_credits: list[dict[str, Any]] = []
        credits = reset_credits.get("credits")
        if isinstance(credits, list):
            for credit in credits:
                if not isinstance(credit, dict):
                    continue
                safe_credits.append(
                    {
                        key: credit.get(key)
                        for key in (
                            "id",
                            "resetType",
                            "status",
                            "grantedAt",
                            "expiresAt",
                            "title",
                            "description",
                        )
                    }
                )
        safe_reset_credits = {
            "available_count": reset_credits.get("availableCount"),
            "credits": safe_credits if isinstance(credits, list) else None,
        }
    return {
        "default": _safe_rate_limit_bucket(payload.get("rateLimits")),
        "by_limit_id": safe_by_id,
        "reset_credits": safe_reset_credits,
    }


def _safe_usage_payload(value: object) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    raw_summary = payload.get("summary")
    summary = raw_summary if isinstance(raw_summary, dict) else {}
    safe_summary = {
        "lifetime_tokens": summary.get("lifetimeTokens"),
        "peak_daily_tokens": summary.get("peakDailyTokens"),
        "longest_running_turn_seconds": summary.get("longestRunningTurnSec"),
        "current_streak_days": summary.get("currentStreakDays"),
        "longest_streak_days": summary.get("longestStreakDays"),
    }
    raw_buckets = payload.get("dailyUsageBuckets")
    buckets: list[dict[str, Any]] | None = None
    if isinstance(raw_buckets, list):
        buckets = [
            {"start_date": bucket.get("startDate"), "tokens": bucket.get("tokens")}
            for bucket in raw_buckets
            if isinstance(bucket, dict)
        ]
    return {"summary": safe_summary, "daily_usage_buckets": buckets}
