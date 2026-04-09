from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Protocol

import httpx
from pydantic import AnyHttpUrl, BaseModel, Field, TypeAdapter, field_validator

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_DEFAULT_AUTHORITY_BASE_URL = "https://login.microsoftonline.com"
_DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class TeamsAuthError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        error_description: str | None = None,
        response_status_code: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.error_description = error_description
        self.response_status_code = response_status_code
        self.payload = payload or {}


class TeamsAuthenticationRequiredError(TeamsAuthError):
    pass


class TeamsAuthProtocolError(TeamsAuthError):
    pass


class TeamsAuthTransportError(TeamsAuthError):
    pass


class AuthorizationPendingError(TeamsAuthError):
    pass


class SlowDownError(TeamsAuthError):
    pass


class ConsentDeniedError(TeamsAuthError):
    pass


class DeviceCodeExpiredError(TeamsAuthError):
    pass


class DeviceCodePollingTimeoutError(TeamsAuthError):
    pass


class InvalidClientConfigurationError(TeamsAuthError):
    pass


class RefreshTokenRevokedError(TeamsAuthError):
    pass


class InteractionRequiredError(TeamsAuthError):
    pass


class TemporaryAuthFailureError(TeamsAuthError):
    pass


class MicrosoftDeviceCodeAuthConfig(BaseModel):
    client_id: str = Field(min_length=1)
    tenant_id: str = Field(default="organizations", min_length=1)
    scopes: list[str] = Field(default_factory=list)
    authority_base_url: str = _DEFAULT_AUTHORITY_BASE_URL
    timeout_seconds: float = Field(default=15.0, gt=0)
    expiry_skew_seconds: int = Field(default=300, ge=0)

    @field_validator("client_id", "tenant_id", mode="before")
    @classmethod
    def _normalize_required_strings(cls, value: object, info) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} cannot be empty")
        return normalized

    @field_validator("authority_base_url", mode="before")
    @classmethod
    def _normalize_authority_base_url(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("authority_base_url cannot be empty")
        return normalized

    @field_validator("authority_base_url")
    @classmethod
    def _validate_authority_base_url(cls, value: str) -> str:
        try:
            _HTTP_URL_ADAPTER.validate_python(value)
        except Exception as exc:  # pragma: no cover - TypeAdapter error shape is not stable
            raise ValueError(f"authority_base_url must be a valid http(s) URL, got {value!r}") from exc
        return value

    @field_validator("scopes", mode="before")
    @classmethod
    def _normalize_scopes(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, str):
            return [scope for scope in value.split(" ") if scope.strip()]
        return value

    @field_validator("scopes")
    @classmethod
    def _validate_scopes(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for scope in value:
            normalized_scope = scope.strip()
            if not normalized_scope:
                continue
            if normalized_scope not in seen:
                seen.add(normalized_scope)
                normalized.append(normalized_scope)
        if not normalized:
            raise ValueError("scopes must contain at least one scope")
        return normalized

    @property
    def scope_string(self) -> str:
        return " ".join(self.scopes)

    @property
    def device_code_url(self) -> str:
        return f"{self.authority_base_url}/{self.tenant_id}/oauth2/v2.0/devicecode"

    @property
    def token_url(self) -> str:
        return f"{self.authority_base_url}/{self.tenant_id}/oauth2/v2.0/token"


class TeamsAuthCredentials(BaseModel):
    client_id: str
    tenant_id: str
    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    token_type: str = "Bearer"
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime
    issued_at: datetime = Field(default_factory=_utc_now)
    account: dict[str, Any] = Field(default_factory=dict)

    @field_validator("token_type", mode="before")
    @classmethod
    def _normalize_token_type(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or "Bearer"

    @field_validator("expires_at", "issued_at", mode="before")
    @classmethod
    def _coerce_datetimes(cls, value: object) -> object:
        if isinstance(value, datetime):
            return _ensure_utc(value)
        return value

    def expires_within(self, within: timedelta, *, now: datetime | None = None) -> bool:
        return self.expires_at <= _ensure_utc(now or _utc_now()) + within


class DeviceCodeLoginSession(BaseModel):
    client_id: str
    tenant_id: str
    scopes: list[str]
    device_code: str = Field(min_length=1)
    user_code: str = Field(min_length=1)
    verification_uri: str = Field(min_length=1)
    verification_uri_complete: str | None = None
    message: str = Field(min_length=1)
    interval_seconds: int = Field(default=5, ge=1)
    expires_at: datetime
    created_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expires_at", "created_at", mode="before")
    @classmethod
    def _coerce_datetimes(cls, value: object) -> object:
        if isinstance(value, datetime):
            return _ensure_utc(value)
        return value


class TeamsCredentialStore(Protocol):
    def load(self) -> TeamsAuthCredentials | None:
        ...

    def save(self, credentials: TeamsAuthCredentials) -> None:
        ...

    def clear(self) -> None:
        ...


class JsonTeamsCredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> TeamsAuthCredentials | None:
        with self._lock:
            if not self.path.exists():
                return None
            raw = self.path.read_text(encoding="utf-8").strip()
            if not raw:
                return None
            return TeamsAuthCredentials.model_validate(json.loads(raw))

    def save(self, credentials: TeamsAuthCredentials) -> None:
        payload = credentials.model_dump(mode="json")
        with self._lock:
            self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()


class JsonTeamsIdentityCredentialStore:
    def __init__(self, path: Path, identity_id: str) -> None:
        self.path = path
        self.identity_id = identity_id.strip()
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> TeamsAuthCredentials | None:
        return self._read_all().get(self.identity_id)

    def save(self, credentials: TeamsAuthCredentials) -> None:
        with self._lock:
            payload = self._read_raw_payload()
            payload[self.identity_id] = credentials.model_dump(mode="json")
            self._write_raw_payload(payload)

    def clear(self) -> None:
        with self._lock:
            payload = self._read_raw_payload()
            payload.pop(self.identity_id, None)
            if payload:
                self._write_raw_payload(payload)
            elif self.path.exists():
                self.path.unlink()

    def list_all(self) -> dict[str, TeamsAuthCredentials]:
        return self._read_all()

    def _read_all(self) -> dict[str, TeamsAuthCredentials]:
        payload = self._read_raw_payload()
        return {
            key: TeamsAuthCredentials.model_validate(value)
            for key, value in payload.items()
            if isinstance(value, dict)
        }

    def _read_raw_payload(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            raw = self.path.read_text(encoding="utf-8").strip()
            if not raw:
                return {}
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TeamsAuthProtocolError(
                    f"Teams credential store at {self.path} must contain a JSON object.",
                    payload={"path": str(self.path)},
                )
            return payload

    def _write_raw_payload(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class TeamsDeviceCodeTokenProvider:
    def __init__(
        self,
        config: MicrosoftDeviceCodeAuthConfig,
        credential_store: TeamsCredentialStore,
        *,
        now_provider: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        client_factory: Callable[[float], httpx.Client] | None = None,
    ) -> None:
        self.config = config
        self.credential_store = credential_store
        self._now_provider = now_provider or _utc_now
        self._sleep = sleep or time.sleep
        self._client_factory = client_factory or (lambda timeout: httpx.Client(timeout=timeout))
        self._cached_credentials: TeamsAuthCredentials | None = None

    def load_credentials(self) -> TeamsAuthCredentials | None:
        if self._cached_credentials is not None:
            return self._cached_credentials
        self._cached_credentials = self.credential_store.load()
        return self._cached_credentials

    def save_credentials(self, credentials: TeamsAuthCredentials) -> TeamsAuthCredentials:
        self.credential_store.save(credentials)
        self._cached_credentials = credentials
        return credentials

    def clear_credentials(self) -> None:
        self.credential_store.clear()
        self._cached_credentials = None

    def credentials_expiring(
        self,
        credentials: TeamsAuthCredentials | None = None,
        *,
        within: timedelta | None = None,
    ) -> bool:
        resolved = credentials or self.load_credentials()
        if resolved is None:
            return True
        return resolved.expires_within(
            within or timedelta(seconds=self.config.expiry_skew_seconds),
            now=self._now_provider(),
        )

    def begin_device_code_login(self) -> DeviceCodeLoginSession:
        payload = self._post_form(
            self.config.device_code_url,
            {
                "client_id": self.config.client_id,
                "scope": self.config.scope_string,
            },
            operation="device_code_start",
        )
        now = self._now_provider()
        expires_in = self._int_field(payload, "expires_in", minimum=1)
        interval_seconds = self._int_field(payload, "interval", minimum=1, default=5)
        device_code = self._required_str(payload, "device_code")
        user_code = self._required_str(payload, "user_code")
        verification_uri = self._required_str(payload, "verification_uri", fallback_keys=("verification_url",))
        message = self._required_str(payload, "message")
        metadata = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "device_code",
                "user_code",
                "verification_uri",
                "verification_url",
                "verification_uri_complete",
                "message",
                "interval",
                "expires_in",
            }
        }
        return DeviceCodeLoginSession(
            client_id=self.config.client_id,
            tenant_id=self.config.tenant_id,
            scopes=list(self.config.scopes),
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            verification_uri_complete=self._optional_str(payload, "verification_uri_complete"),
            message=message,
            interval_seconds=interval_seconds,
            expires_at=now + timedelta(seconds=expires_in),
            created_at=now,
            metadata=metadata,
        )

    def poll_device_code_login_once(self, session: DeviceCodeLoginSession) -> TeamsAuthCredentials:
        payload = self._post_form(
            self.config.token_url,
            {
                "grant_type": _DEVICE_CODE_GRANT_TYPE,
                "client_id": self.config.client_id,
                "device_code": session.device_code,
            },
            operation="device_code_poll",
        )
        credentials = self._credentials_from_token_payload(payload)
        return self.save_credentials(credentials)

    def complete_device_code_login(
        self,
        session: DeviceCodeLoginSession,
        *,
        timeout_seconds: float | None = None,
    ) -> TeamsAuthCredentials:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive when provided")

        poll_interval = max(1, session.interval_seconds)
        deadline = session.expires_at
        deadline_error_type: type[TeamsAuthError] = DeviceCodeExpiredError
        deadline_message = (
            f"Device code expired before authorization completed at {session.expires_at.isoformat()}."
        )
        if timeout_seconds is not None:
            timeout_deadline = self._now_provider() + timedelta(seconds=timeout_seconds)
            if timeout_deadline < deadline:
                deadline = timeout_deadline
                deadline_error_type = DeviceCodePollingTimeoutError
                deadline_message = (
                    f"Timed out waiting for device-code authorization before {timeout_deadline.isoformat()}."
                )

        while True:
            try:
                return self.poll_device_code_login_once(session)
            except AuthorizationPendingError:
                self._sleep_until_next_poll(
                    poll_interval,
                    deadline,
                    error_type=deadline_error_type,
                    error_message=deadline_message,
                )
            except SlowDownError:
                poll_interval += max(1, session.interval_seconds)
                self._sleep_until_next_poll(
                    poll_interval,
                    deadline,
                    error_type=deadline_error_type,
                    error_message=deadline_message,
                )

    def refresh_access_token(
        self,
        credentials: TeamsAuthCredentials | None = None,
    ) -> TeamsAuthCredentials:
        resolved = credentials or self.load_credentials()
        if resolved is None:
            raise TeamsAuthenticationRequiredError("No persisted Teams credentials are available.")
        refresh_token = (resolved.refresh_token or "").strip()
        if not refresh_token:
            raise TeamsAuthenticationRequiredError("Persisted Teams credentials do not include a refresh token.")

        try:
            payload = self._post_form(
                self.config.token_url,
                {
                    "grant_type": "refresh_token",
                    "client_id": self.config.client_id,
                    "refresh_token": refresh_token,
                    "scope": self.config.scope_string,
                },
                operation="refresh",
            )
        except RefreshTokenRevokedError:
            self.clear_credentials()
            raise

        refreshed = self._credentials_from_token_payload(payload, prior_credentials=resolved)
        return self.save_credentials(refreshed)

    def get_bearer_token(self, *, force_refresh: bool = False) -> str:
        credentials = self.load_credentials()
        if credentials is None:
            raise TeamsAuthenticationRequiredError(
                "No Teams delegated credentials are available. Start device-code login first."
            )
        if force_refresh or self.credentials_expiring(credentials):
            credentials = self.refresh_access_token(credentials)
        return credentials.access_token

    def _sleep_until_next_poll(
        self,
        delay_seconds: int,
        deadline: datetime,
        *,
        error_type: type[TeamsAuthError],
        error_message: str,
    ) -> None:
        now = self._now_provider()
        if now >= deadline:
            raise error_type(error_message)
        remaining_seconds = (deadline - now).total_seconds()
        sleep_for = min(float(delay_seconds), remaining_seconds)
        self._sleep(max(0.0, sleep_for))
        if self._now_provider() >= deadline:
            raise error_type(error_message)

    def _post_form(self, url: str, form_data: dict[str, str], *, operation: str) -> dict[str, Any]:
        try:
            with self._client_factory(self.config.timeout_seconds) as client:
                response = client.post(
                    url,
                    data=form_data,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise TeamsAuthTransportError(f"Microsoft auth {operation} request failed: {exc}") from exc

        payload = self._json_payload(response, operation=operation)
        if response.is_error:
            raise self._normalize_oauth_error(
                operation=operation,
                payload=payload,
                response_status_code=response.status_code,
            )
        return payload

    def _json_payload(self, response: httpx.Response, *, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise TeamsAuthProtocolError(
                f"Microsoft auth {operation} returned a non-JSON response.",
                response_status_code=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise TeamsAuthProtocolError(
                f"Microsoft auth {operation} returned an unexpected JSON payload.",
                response_status_code=response.status_code,
                payload={"received_type": type(payload).__name__},
            )
        return payload

    def _credentials_from_token_payload(
        self,
        payload: dict[str, Any],
        *,
        prior_credentials: TeamsAuthCredentials | None = None,
    ) -> TeamsAuthCredentials:
        issued_at = self._now_provider()
        expires_in = self._int_field(payload, "expires_in", minimum=1)
        access_token = self._required_str(payload, "access_token")
        scope_value = self._optional_str(payload, "scope")
        scopes = scope_value.split(" ") if scope_value else list(self.config.scopes)
        refresh_token = self._optional_str(payload, "refresh_token")
        if refresh_token is None and prior_credentials is not None:
            refresh_token = prior_credentials.refresh_token
        return TeamsAuthCredentials(
            client_id=self.config.client_id,
            tenant_id=self.config.tenant_id,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=self._optional_str(payload, "token_type") or "Bearer",
            scopes=[scope for scope in scopes if scope.strip()],
            expires_at=issued_at + timedelta(seconds=expires_in),
            issued_at=issued_at,
            account={
                key: value
                for key, value in payload.items()
                if key
                in {"id_token", "ext_expires_in", "foci", "client_info", "scope", "resource", "tenant"}
            },
        )

    def _normalize_oauth_error(
        self,
        *,
        operation: str,
        payload: dict[str, Any],
        response_status_code: int,
    ) -> TeamsAuthError:
        error_code = self._optional_str(payload, "error")
        description = self._optional_str(payload, "error_description") or self._optional_str(payload, "message")
        message = description or error_code or f"Microsoft auth {operation} failed."

        error_map: dict[str, type[TeamsAuthError]] = {
            "authorization_pending": AuthorizationPendingError,
            "slow_down": SlowDownError,
            "authorization_declined": ConsentDeniedError,
            "access_denied": ConsentDeniedError,
            "consent_required": ConsentDeniedError,
            "expired_token": DeviceCodeExpiredError,
            "code_expired": DeviceCodeExpiredError,
            "invalid_client": InvalidClientConfigurationError,
            "unauthorized_client": InvalidClientConfigurationError,
            "interaction_required": InteractionRequiredError,
            "login_required": InteractionRequiredError,
        }
        error_type = error_map.get(error_code or "", TeamsAuthError)

        if operation == "refresh" and error_code in {"invalid_grant", "bad_token"}:
            error_type = RefreshTokenRevokedError
        elif operation == "device_code_poll" and error_code in {"invalid_grant", "bad_verification_code"}:
            error_type = DeviceCodeExpiredError
        elif error_code == "temporarily_unavailable" or response_status_code >= 500:
            error_type = TemporaryAuthFailureError
        elif error_code == "invalid_request" and operation in {"device_code_start", "device_code_poll", "refresh"}:
            error_type = InvalidClientConfigurationError

        return error_type(
            message,
            error_code=error_code,
            error_description=description,
            response_status_code=response_status_code,
            payload=payload,
        )

    @staticmethod
    def _required_str(
        payload: dict[str, Any],
        key: str,
        *,
        fallback_keys: tuple[str, ...] = (),
    ) -> str:
        value = TeamsDeviceCodeTokenProvider._optional_str(payload, key)
        if value:
            return value
        for fallback_key in fallback_keys:
            fallback_value = TeamsDeviceCodeTokenProvider._optional_str(payload, fallback_key)
            if fallback_value:
                return fallback_value
        raise TeamsAuthProtocolError(f"Microsoft auth response is missing required field {key!r}.", payload=payload)

    @staticmethod
    def _optional_str(payload: dict[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _int_field(payload: dict[str, Any], key: str, *, minimum: int, default: int | None = None) -> int:
        raw_value = payload.get(key, default)
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise TeamsAuthProtocolError(
                f"Microsoft auth response field {key!r} must be an integer.",
                payload=payload,
            ) from exc
        if value < minimum:
            raise TeamsAuthProtocolError(
                f"Microsoft auth response field {key!r} must be >= {minimum}.",
                payload=payload,
            )
        return value
