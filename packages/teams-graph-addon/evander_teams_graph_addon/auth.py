from __future__ import annotations

from contextlib import nullcontext
from functools import wraps

import base64
import json
import os
import stat
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Protocol

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import AnyHttpUrl, BaseModel, Field, TypeAdapter, field_validator

from evander.storage import JsonObjectStore

from evander.messaging.teams_auth_errors import (
    TeamsAuthError,
    TeamsAuthenticationRequiredError,
    TeamsAuthProtocolError,
    TeamsAuthTransportError,
    AuthorizationPendingError,
    SlowDownError,
    ConsentDeniedError,
    DeviceCodeExpiredError,
    DeviceCodePollingTimeoutError,
    InvalidClientConfigurationError,
    RefreshTokenRevokedError,
    InteractionRequiredError,
    TemporaryAuthFailureError,
)

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_DEFAULT_AUTHORITY_BASE_URL = "https://login.microsoftonline.com"
_DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_AADSTS_CONFIDENTIAL_CLIENT_REQUIRES_SECRET = "AADSTS7000218"
_IDENTITY_METADATA_SCOPES = ("openid", "profile")
_NON_CONCRETE_TENANT_IDS = {"organizations", "common", "consumers"}
TEAMS_CREDENTIAL_ENVELOPE_FORMAT = "evander.teams-credentials.fernet.v1"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_concrete_tenant_id(value: str | None) -> bool:
    normalized = str(value or "").strip()
    return bool(normalized) and normalized.casefold() not in _NON_CONCRETE_TENANT_IDS


def _base64url_json(value: object) -> dict[str, Any]:
    encoded = str(value or "").strip()
    if not encoded:
        return {}
    try:
        padded = f"{encoded}{'=' * (-len(encoded) % 4)}"
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tenant_id_from_account_metadata(account: dict[str, Any]) -> str | None:
    direct_tenant = str(account.get("tenant") or "").strip()
    if _is_concrete_tenant_id(direct_tenant):
        return direct_tenant

    client_info = _base64url_json(account.get("client_info"))
    client_tenant = str(client_info.get("utid") or "").strip()
    if _is_concrete_tenant_id(client_tenant):
        return client_tenant

    id_token = str(account.get("id_token") or "").strip()
    token_parts = id_token.split(".")
    id_token_claims = _base64url_json(token_parts[1]) if len(token_parts) >= 2 else {}
    token_tenant = str(id_token_claims.get("tid") or "").strip()
    if _is_concrete_tenant_id(token_tenant):
        return token_tenant
    return None


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
            raise ValueError(
                f"authority_base_url must be a valid http(s) URL, got {value!r}"
            ) from exc
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
    def device_code_scope_string(self) -> str:
        return " ".join(dict.fromkeys([*self.scopes, *_IDENTITY_METADATA_SCOPES]))

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
    def load(self) -> TeamsAuthCredentials | None: ...

    def save(self, credentials: TeamsAuthCredentials) -> None: ...

    def clear(self) -> None: ...


def legacy_teams_credential_key_path(path: Path) -> Path:
    path = Path(path)
    return path.parent / "secrets" / f"{path.name}.key"


def decode_legacy_teams_credential_envelope(
    envelope: object,
    *,
    key_path: Path,
) -> tuple[str, dict[str, Any]] | None:
    """Decode the transitional privacy envelope without rewriting its source file."""

    if not isinstance(envelope, dict) or not ({"format", "ciphertext"} & set(envelope)):
        return None
    if (
        set(envelope) != {"format", "ciphertext"}
        or envelope.get("format") != TEAMS_CREDENTIAL_ENVELOPE_FORMAT
        or not isinstance(envelope.get("ciphertext"), str)
    ):
        raise TeamsAuthProtocolError("Unsupported Teams credential storage format.")
    try:
        fd = os.open(Path(key_path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("unsafe key file")
            cleartext = Fernet(stream.read()).decrypt(envelope["ciphertext"].encode())
        document = json.loads(cleartext)
        if (
            not isinstance(document, dict)
            or set(document) != {"kind", "credentials"}
            or document.get("kind") not in {"single", "identities"}
            or not isinstance(document.get("credentials"), dict)
        ):
            raise ValueError("invalid encrypted payload")
        return document["kind"], document["credentials"]
    except (InvalidToken, OSError, TypeError, ValueError):
        raise TeamsAuthProtocolError(
            "Teams credential store could not be decrypted or validated."
        ) from None


class _EncryptedTeamsCredentialStore(JsonObjectStore):
    """Locked credential storage with one-way, atomic plaintext migration.

    The public credential APIs remain unchanged. Older binaries cannot read the
    versioned envelope; restore its matching host key when restoring this file.
    A missing key for encrypted data is an error, never a request to generate one.
    """

    _format = TEAMS_CREDENTIAL_ENVELOPE_FORMAT
    _kind = "single"

    def __init__(self, path: Path) -> None:
        path = Path(path)
        if path.is_symlink():
            raise TeamsAuthProtocolError("Teams credential store must not be a symbolic link.")
        self.key_path = legacy_teams_credential_key_path(path)
        super().__init__(path, {}, file_mode=0o600, empty_file_factory=dict)

    def _cipher(self, *, create: bool) -> Fernet:
        self.key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.key_path.parent.chmod(0o700)
        if create and not self.key_path.exists():
            fd, temporary = tempfile.mkstemp(dir=self.key_path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, self.key_path)
                except FileExistsError:
                    pass
                directory_fd = os.open(self.key_path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                os.unlink(temporary)
        try:
            fd = os.open(self.key_path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                return Fernet(stream.read())
        except (OSError, ValueError):
            raise TeamsAuthProtocolError(
                "Teams credential encryption key is unavailable or invalid; restore the matching key."
            ) from None

    def _validate_payload(self, payload: object) -> dict[str, Any]:
        try:
            if not isinstance(payload, dict):
                raise ValueError("expected object")
            if payload:
                if self._kind == "single":
                    TeamsAuthCredentials.model_validate(payload)
                else:
                    for identity_id, credentials in payload.items():
                        if not isinstance(identity_id, str) or not identity_id.strip():
                            raise ValueError("expected identity")
                        TeamsAuthCredentials.model_validate(credentials)
            return payload
        except (TypeError, ValueError):
            # Pydantic errors can include token values: never expose their details.
            raise TeamsAuthProtocolError(
                "Teams credential store contains invalid credentials."
            ) from None

    def _read_object_unlocked(self) -> dict[str, Any]:
        try:
            envelope = super()._read_object_unlocked()
        except (UnicodeError, ValueError):
            raise TeamsAuthProtocolError("Teams credential store contains invalid JSON.") from None
        decoded = decode_legacy_teams_credential_envelope(envelope, key_path=self.key_path)
        if decoded is not None:
            kind, credentials = decoded
            if kind != self._kind:
                raise TeamsAuthProtocolError(
                    "Teams credential store could not be decrypted or validated."
                )
            return self._validate_payload(credentials)
        payload = self._validate_payload(envelope)
        if payload:
            # Validation and the durable encrypted replacement share the file lock.
            # Failure leaves the original intact; no plaintext backup is created.
            self._write_object_unlocked(payload)
        return payload

    def _write_object_unlocked(self, payload: dict[str, Any]) -> None:
        self._validate_payload(payload)
        # Read paths already checked existing encrypted data before mutations.
        # Direct write_object() is also fail-closed when the existing key is lost.
        existing = super()._read_object_unlocked()
        cipher = self._cipher(create="format" not in existing and "ciphertext" not in existing)
        ciphertext = cipher.encrypt(
            json.dumps({"kind": self._kind, "credentials": payload}).encode("utf-8")
        ).decode("ascii")
        super()._write_object_unlocked({"format": self._format, "ciphertext": ciphertext})


class JsonTeamsCredentialStore(_EncryptedTeamsCredentialStore):
    def load(self) -> TeamsAuthCredentials | None:
        payload = self.read_object()
        return TeamsAuthCredentials.model_validate(payload) if payload else None

    def save(self, credentials: TeamsAuthCredentials) -> None:
        def update(payload: dict[str, Any]) -> None:
            payload.clear()
            payload.update(credentials.model_dump(mode="json"))

        self.update_object(update)

    def clear(self) -> None:
        self.update_object(lambda payload: payload.clear())


class JsonTeamsIdentityCredentialStore(_EncryptedTeamsCredentialStore):
    _kind = "identities"

    def __init__(self, path: Path, identity_id: str) -> None:
        self.identity_id = identity_id.strip()
        if not self.identity_id:
            raise ValueError("identity_id must not be empty")
        super().__init__(path)

    def load(self) -> TeamsAuthCredentials | None:
        return self.list_all().get(self.identity_id)

    def save(self, credentials: TeamsAuthCredentials) -> None:
        def update(payload: dict[str, Any]) -> None:
            payload[self.identity_id] = credentials.model_dump(mode="json")

        self.update_object(update)

    def clear(self) -> None:
        def update(payload: dict[str, Any]) -> None:
            payload.pop(self.identity_id, None)

        self.update_object(update)

    def list_all(self) -> dict[str, TeamsAuthCredentials]:
        return {
            key: TeamsAuthCredentials.model_validate(value)
            for key, value in self.read_object().items()
        }


def _managed_auth_errors(method):
    """Provider error descriptions and payloads may reflect submitted tokens."""

    @wraps(method)
    def safe(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except TeamsAuthError as exc:
            if not getattr(self.credential_store, "fresh_reads", False):
                raise
            raise type(exc)(
                f"Managed Teams authentication failed ({type(exc).__name__}).",
                response_status_code=exc.response_status_code,
            ) from None
        except (ValueError, TypeError, OverflowError):
            if not getattr(self.credential_store, "fresh_reads", False):
                raise
            raise TeamsAuthProtocolError(
                "Managed Teams authentication returned invalid data."
            ) from None

    return safe


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
        self._credentials_lock = RLock()

    def load_credentials(self) -> TeamsAuthCredentials | None:
        if self._cached_credentials is not None and not getattr(
            self.credential_store, "fresh_reads", False
        ):
            return self._cached_credentials
        self._cached_credentials = self.credential_store.load()
        return self._cached_credentials

    def save_credentials(
        self, credentials: TeamsAuthCredentials, *, refresh=False
    ) -> TeamsAuthCredentials:
        if getattr(self.credential_store, "fresh_reads", False):
            self.credential_store.save(credentials, refresh=refresh)
        else:
            self.credential_store.save(credentials)
        self._cached_credentials = credentials
        return credentials

    def clear_credentials(self, *, revoked=False) -> None:
        if getattr(self.credential_store, "fresh_reads", False):
            self.credential_store.clear(revoked=revoked)
        else:
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

    @_managed_auth_errors
    def begin_device_code_login(self) -> DeviceCodeLoginSession:
        revision = None
        if getattr(self.credential_store, "fresh_reads", False):
            with self._credentials_lock:
                self.credential_store.require_owner()
                self.load_credentials()
                revision = self.credential_store.revision
        payload = self._post_form(
            self.config.device_code_url,
            {
                "client_id": self.config.client_id,
                "scope": self.config.device_code_scope_string,
            },
            operation="device_code_start",
        )
        now = self._now_provider()
        expires_in = self._int_field(payload, "expires_in", minimum=1)
        interval_seconds = self._int_field(payload, "interval", minimum=1, default=5)
        device_code = self._required_str(payload, "device_code")
        user_code = self._required_str(payload, "user_code")
        verification_uri = self._required_str(
            payload, "verification_uri", fallback_keys=("verification_url",)
        )
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
        if getattr(self.credential_store, "fresh_reads", False):
            metadata = {
                "credential_revision": revision,
                "credential_ref": self.credential_store.ref,
                "credential_directory": self.credential_store.directory,
                "credential_binding": self.credential_store.binding,
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

    @_managed_auth_errors
    def poll_device_code_login_once(self, session: DeviceCodeLoginSession) -> TeamsAuthCredentials:
        with self._credentials_lock:
            return self._poll_device_code_login_once(session)

    def _poll_device_code_login_once(self, session: DeviceCodeLoginSession) -> TeamsAuthCredentials:
        managed = getattr(self.credential_store, "fresh_reads", False)
        if (
            managed
            or "credential_ref" in session.metadata
            or "credential_revision" in session.metadata
        ):
            revision = session.metadata.get("credential_revision")
            if (
                not managed
                or session.metadata.get("credential_ref") != self.credential_store.ref
                or session.metadata.get("credential_directory") != self.credential_store.directory
                or session.metadata.get("credential_binding") != self.credential_store.binding
                or not isinstance(revision, str)
                or not revision
            ):
                raise TeamsAuthenticationRequiredError("Start a new managed Teams login")
            self.credential_store.require_owner()
            self.load_credentials()
            if self.credential_store.revision != revision:
                raise TeamsAuthenticationRequiredError(
                    "Teams credentials changed; start a new login"
                )
        payload = self._post_form(
            self.config.token_url,
            {
                "grant_type": _DEVICE_CODE_GRANT_TYPE,
                "client_id": self.config.client_id,
                "device_code": session.device_code,
                "client_info": "1",
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
        deadline_message = f"Device code expired before authorization completed at {session.expires_at.isoformat()}."
        if timeout_seconds is not None:
            timeout_deadline = self._now_provider() + timedelta(seconds=timeout_seconds)
            if timeout_deadline < deadline:
                deadline = timeout_deadline
                deadline_error_type = DeviceCodePollingTimeoutError
                deadline_message = f"Timed out waiting for device-code authorization before {timeout_deadline.isoformat()}."

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

    @_managed_auth_errors
    def refresh_access_token(
        self,
        credentials: TeamsAuthCredentials | None = None,
    ) -> TeamsAuthCredentials:
        lock = getattr(self.credential_store, "refresh_lock", nullcontext)
        with self._credentials_lock, lock():
            if getattr(self.credential_store, "fresh_reads", False):
                credentials = self.load_credentials()
            return self._refresh_access_token(credentials)

    def _refresh_access_token(self, credentials=None) -> TeamsAuthCredentials:
        resolved = credentials or self.load_credentials()
        if resolved is None:
            raise TeamsAuthenticationRequiredError("No persisted Teams credentials are available.")
        refresh_token = (resolved.refresh_token or "").strip()
        if not refresh_token:
            raise TeamsAuthenticationRequiredError(
                "Persisted Teams credentials do not include a refresh token."
            )

        form_data = {
            "grant_type": "refresh_token",
            "client_id": self.config.client_id,
            "refresh_token": refresh_token,
            "client_info": "1",
        }
        granted_scope_string = " ".join(scope.strip() for scope in resolved.scopes if scope.strip())
        if granted_scope_string:
            form_data["scope"] = granted_scope_string

        try:
            payload = self._post_form(
                self.config.token_url,
                form_data,
                operation="refresh",
            )
        except RefreshTokenRevokedError:
            self.clear_credentials(revoked=True)
            raise

        refreshed = self._credentials_from_token_payload(payload, prior_credentials=resolved)
        return self.save_credentials(refreshed, refresh=True)

    @_managed_auth_errors
    def get_bearer_token(self, *, force_refresh: bool = False) -> str:
        # A Teams ingest cycle can poll several Graph targets at once. Keep the
        # read-check-refresh sequence atomic so an expiring credential produces one
        # refresh request rather than a concurrent refresh-token stampede.
        lock = getattr(self.credential_store, "refresh_lock", nullcontext)
        with self._credentials_lock, lock():
            credentials = self.load_credentials()
            if credentials is None:
                raise TeamsAuthenticationRequiredError(
                    "No Teams delegated credentials are available. Start device-code login first."
                )
            if force_refresh or self.credentials_expiring(credentials):
                credentials = self._refresh_access_token(credentials)
            return credentials.access_token

    def get_tenant_id(self) -> str | None:
        credentials = self.load_credentials()
        if credentials is not None and _is_concrete_tenant_id(credentials.tenant_id):
            return credentials.tenant_id.strip()
        if _is_concrete_tenant_id(self.config.tenant_id):
            return self.config.tenant_id.strip()
        return None

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
            raise TeamsAuthTransportError(
                f"Microsoft auth {operation} request failed: {exc}"
            ) from exc

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
        if scope_value:
            scopes = scope_value.split(" ")
        elif prior_credentials is not None:
            scopes = list(prior_credentials.scopes)
        else:
            scopes = list(self.config.scopes)
        refresh_token = self._optional_str(payload, "refresh_token")
        if refresh_token is None and prior_credentials is not None:
            refresh_token = prior_credentials.refresh_token
        account = dict(prior_credentials.account) if prior_credentials is not None else {}
        account.update(
            {
                key: value
                for key, value in payload.items()
                if key
                in {
                    "id_token",
                    "ext_expires_in",
                    "foci",
                    "client_info",
                    "scope",
                    "resource",
                    "tenant",
                }
            }
        )
        tenant_id = _tenant_id_from_account_metadata(account)
        if tenant_id is None and prior_credentials is not None:
            tenant_id = prior_credentials.tenant_id
        if tenant_id is None:
            tenant_id = self.config.tenant_id
        return TeamsAuthCredentials(
            client_id=self.config.client_id,
            tenant_id=tenant_id,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=self._optional_str(payload, "token_type") or "Bearer",
            scopes=[scope for scope in scopes if scope.strip()],
            expires_at=issued_at + timedelta(seconds=expires_in),
            issued_at=issued_at,
            account=account,
        )

    def _normalize_oauth_error(
        self,
        *,
        operation: str,
        payload: dict[str, Any],
        response_status_code: int,
    ) -> TeamsAuthError:
        error_code = self._optional_str(payload, "error")
        description = self._optional_str(payload, "error_description") or self._optional_str(
            payload, "message"
        )
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
        elif operation == "device_code_poll" and error_code in {
            "invalid_grant",
            "bad_verification_code",
        }:
            error_type = DeviceCodeExpiredError
        elif error_code == "temporarily_unavailable" or response_status_code >= 500:
            error_type = TemporaryAuthFailureError
        elif error_code == "invalid_request" and operation in {
            "device_code_start",
            "device_code_poll",
            "refresh",
        }:
            error_type = InvalidClientConfigurationError

        if (
            error_type is InvalidClientConfigurationError
            and description
            and _AADSTS_CONFIDENTIAL_CLIENT_REQUIRES_SECRET in description
        ):
            message = (
                "Microsoft Entra rejected the device-code flow because the configured app registration "
                "is not enabled as a public client. Enable device-code/public client flows for "
                f"client_id {self.config.client_id!r}, or use an app registration intended for "
                "delegated device-code auth."
            )

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
        raise TeamsAuthProtocolError(
            f"Microsoft auth response is missing required field {key!r}.", payload=payload
        )

    @staticmethod
    def _optional_str(payload: dict[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _int_field(
        payload: dict[str, Any], key: str, *, minimum: int, default: int | None = None
    ) -> int:
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
