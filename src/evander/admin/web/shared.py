from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import quote, unquote, urlencode

import httpx
import jwt
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from evander.events.internal import EXTERNAL_WORKFLOW_EVENT_PREFIX
from evander.admin.web.user_store import (
    AdminWebUserRecord,
    AdminWebUserStore,
    normalize_admin_web_login,
)
from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
    PERMISSION_CATALOG,
    USERS_MANAGE_PERMISSION,
)

AdminAuthMode = Literal["none", "local_password", "azure_sso"]
AdminWebHeaderLogoSize = Literal["compact", "wordmark"]

AZURE_SSO_START_PATH = "/api/auth/azure-sso/start"
AZURE_SSO_CALLBACK_PATH = "/api/auth/azure-sso/callback"
AZURE_SSO_COOKIE_NAME = "evander_admin_oidc"
AZURE_SSO_SCOPES = ("openid", "profile", "email")
WORKFLOW_EVENT_API_TOKEN_KIND = "workflow_event_api"
WORKFLOW_EVENT_API_ALLOWED_PREFIX = EXTERNAL_WORKFLOW_EVENT_PREFIX
WORKFLOW_EVENT_API_TOKEN_TTL_SECONDS = 3600
ADMIN_WEB_BRANDING_ASSET_BASE_PATH = "/api/admin/branding/assets"
DEFAULT_ADMIN_WEB_PAGE_TITLE = "Evander Admin"
DEFAULT_ADMIN_WEB_HEADER_TITLE = "EVANDER"
DEFAULT_ADMIN_WEB_HEADER_TITLE_VISIBLE = False
DEFAULT_ADMIN_WEB_HEADER_LOGO_SIZE: AdminWebHeaderLogoSize = "wordmark"
DEFAULT_ADMIN_WEB_BRANDING_ASSET_DIR = (
    Path(__file__).resolve().parents[4] / "frontend" / "admin-web" / "branding"
)
DEFAULT_ADMIN_WEB_PRODUCT_LOGO_LIGHT_URL = "evander-logo-light.png"
DEFAULT_ADMIN_WEB_PRODUCT_LOGO_DARK_URL = "evander-logo-dark.png"
DEFAULT_ADMIN_WEB_PRODUCT_LOGO_ALT = "Evander Logo"
DEFAULT_ADMIN_WEB_COMPANY_LOGO_LIGHT_URL = "qss-light.png"
DEFAULT_ADMIN_WEB_COMPANY_LOGO_DARK_URL = "qss-dark.png"
DEFAULT_ADMIN_WEB_COMPANY_LOGO_ALT = "Quinth Software Solutions"
DEFAULT_ADMIN_WEB_SLOW_REQUEST_THRESHOLD_MS = 500


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    values = [item.strip() for item in raw.split(",")]
    return tuple(item for item in values if item)


def _env_text(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip() or default


def _env_optional_text(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    normalized = raw.strip()
    return normalized or None


def _env_non_negative_int(name: str, default: int) -> int:
    raw = _env_optional_text(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _branding_asset_reference(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    lowered = normalized.lower()
    if (
        normalized.startswith("/")
        or lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("data:image/")
    ):
        return normalized
    return f"{ADMIN_WEB_BRANDING_ASSET_BASE_PATH}/{quote(normalized.lstrip('/'), safe='/._-~')}"


def _env_branding_asset_reference(name: str) -> str | None:
    return _branding_asset_reference(_env_optional_text(name))


def _env_workflow_event_machine_tokens() -> tuple["WorkflowEventMachineToken", ...]:
    values = _env_csv("EVANDER_WORKFLOW_EVENT_MACHINE_TOKENS")
    records: list[WorkflowEventMachineToken] = []
    seen_tokens: set[str] = set()

    for value in values:
        if value.count(":") != 1:
            raise ValueError(
                "EVANDER_WORKFLOW_EVENT_MACHINE_TOKENS entries must use source-name:token format"
            )

        source_name, token = (part.strip() for part in value.split(":", 1))
        if not source_name or not token:
            raise ValueError(
                "EVANDER_WORKFLOW_EVENT_MACHINE_TOKENS entries must include "
                "both source name and token"
            )
        if token in seen_tokens:
            raise ValueError(
                "EVANDER_WORKFLOW_EVENT_MACHINE_TOKENS contains duplicate token values"
            )

        seen_tokens.add(token)
        records.append(WorkflowEventMachineToken(source_name=source_name, token=token))

    return tuple(records)


def _normalize_user_identifier(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


@dataclass(frozen=True)
class WorkflowEventMachineToken:
    source_name: str
    token: str


@dataclass(frozen=True)
class AdminWebLogoConfig:
    light_url: str | None
    dark_url: str | None
    alt: str

    def to_payload(self) -> dict[str, str | None]:
        return {
            "light_url": self.light_url,
            "dark_url": self.dark_url,
            "alt": self.alt,
        }


@dataclass(frozen=True)
class AdminWebBrandingConfig:
    page_title: str
    header_title: str
    header_title_visible: bool
    header_logo_size: AdminWebHeaderLogoSize
    header_logo: AdminWebLogoConfig
    product_logo: AdminWebLogoConfig
    company_logo: AdminWebLogoConfig

    def to_payload(self) -> dict[str, Any]:
        return {
            "page_title": self.page_title,
            "header_title": self.header_title,
            "header_title_visible": self.header_title_visible,
            "header_logo_size": self.header_logo_size,
            "header_logo": self.header_logo.to_payload(),
            "product_logo": self.product_logo.to_payload(),
            "company_logo": self.company_logo.to_payload(),
        }

    @classmethod
    def from_env(cls) -> "AdminWebBrandingConfig":
        page_title = _env_text("EVANDER_ADMIN_WEB_PAGE_TITLE", DEFAULT_ADMIN_WEB_PAGE_TITLE)
        header_title = _env_text("EVANDER_ADMIN_WEB_HEADER_TITLE", DEFAULT_ADMIN_WEB_HEADER_TITLE)
        header_title_visible = _env_flag(
            "EVANDER_ADMIN_WEB_HEADER_TITLE_VISIBLE",
            default=DEFAULT_ADMIN_WEB_HEADER_TITLE_VISIBLE,
        )
        header_logo_size = _env_header_logo_size(default=DEFAULT_ADMIN_WEB_HEADER_LOGO_SIZE)
        product_logo = _logo_config_from_env(
            "EVANDER_ADMIN_WEB_PRODUCT_LOGO",
            default_alt=DEFAULT_ADMIN_WEB_PRODUCT_LOGO_ALT,
            default_light_url=DEFAULT_ADMIN_WEB_PRODUCT_LOGO_LIGHT_URL,
            default_dark_url=DEFAULT_ADMIN_WEB_PRODUCT_LOGO_DARK_URL,
        )
        header_logo = _logo_config_from_env(
            "EVANDER_ADMIN_WEB_HEADER_LOGO",
            default_alt=product_logo.alt,
            fallback=product_logo,
        )
        company_logo = _logo_config_from_env(
            "EVANDER_ADMIN_WEB_COMPANY_LOGO",
            default_alt=DEFAULT_ADMIN_WEB_COMPANY_LOGO_ALT,
            default_light_url=DEFAULT_ADMIN_WEB_COMPANY_LOGO_LIGHT_URL,
            default_dark_url=DEFAULT_ADMIN_WEB_COMPANY_LOGO_DARK_URL,
        )
        return cls(
            page_title=page_title,
            header_title=header_title,
            header_title_visible=header_title_visible,
            header_logo_size=header_logo_size,
            header_logo=header_logo,
            product_logo=product_logo,
            company_logo=company_logo,
        )


def _env_header_logo_size(*, default: AdminWebHeaderLogoSize) -> AdminWebHeaderLogoSize:
    raw = os.environ.get("EVANDER_ADMIN_WEB_HEADER_LOGO_SIZE", "").strip().lower()
    if not raw:
        return default
    if raw == "compact":
        return "compact"
    if raw == "wordmark":
        return "wordmark"
    raise ValueError("EVANDER_ADMIN_WEB_HEADER_LOGO_SIZE must be compact or wordmark")


def _logo_config_from_env(
    prefix: str,
    *,
    default_alt: str,
    default_light_url: str | None = None,
    default_dark_url: str | None = None,
    fallback: AdminWebLogoConfig | None = None,
) -> AdminWebLogoConfig:
    light_url = _env_branding_asset_reference(f"{prefix}_LIGHT_URL")
    dark_url = _env_branding_asset_reference(f"{prefix}_DARK_URL")
    alt = _env_text(f"{prefix}_ALT", fallback.alt if fallback else default_alt)
    if fallback is not None:
        light_url = light_url if light_url is not None else fallback.light_url
        dark_url = dark_url if dark_url is not None else fallback.dark_url
    if light_url is not None and dark_url is None:
        dark_url = light_url
    if dark_url is not None and light_url is None:
        light_url = dark_url
    if light_url is None and dark_url is None:
        light_url = _branding_asset_reference(default_light_url)
        dark_url = _branding_asset_reference(default_dark_url)
    if light_url is not None and dark_url is None:
        dark_url = light_url
    if dark_url is not None and light_url is None:
        light_url = dark_url
    return AdminWebLogoConfig(light_url=light_url, dark_url=dark_url, alt=alt)


@dataclass(frozen=True)
class AzureSsoConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    redirect_uri: str
    authority_base_url: str
    bootstrap_users: frozenset[str]
    post_login_redirect: str

    @property
    def authority_url(self) -> str:
        return f"{self.authority_base_url.rstrip('/')}/{self.tenant_id}"

    @property
    def authorization_url(self) -> str:
        return f"{self.authority_url}/oauth2/v2.0/authorize"

    @property
    def token_url(self) -> str:
        return f"{self.authority_url}/oauth2/v2.0/token"

    @property
    def discovery_url(self) -> str:
        return f"{self.authority_url}/v2.0/.well-known/openid-configuration"


@dataclass(frozen=True)
class AdminWebConfig:
    auth_mode: AdminAuthMode
    username: str
    password: str | None
    session_secret: str
    session_ttl_seconds: int
    cookie_name: str
    cookie_secure: bool
    workflow_event_machine_tokens: tuple[WorkflowEventMachineToken, ...]
    azure_sso: AzureSsoConfig | None
    branding: AdminWebBrandingConfig
    branding_asset_dir: Path | None
    slow_request_threshold_ms: int = DEFAULT_ADMIN_WEB_SLOW_REQUEST_THRESHOLD_MS

    @property
    def auth_required(self) -> bool:
        return self.auth_mode != "none"

    @classmethod
    def from_env(cls) -> "AdminWebConfig":
        username = _env_text("EVANDER_ADMIN_WEB_USERNAME", "admin")
        password = _env_optional_text("EVANDER_ADMIN_WEB_PASSWORD")
        raw_mode = (_env_optional_text("EVANDER_ADMIN_WEB_AUTH_MODE") or "").lower()
        if raw_mode:
            if raw_mode not in {"none", "local_password", "azure_sso"}:
                raise ValueError(
                    "EVANDER_ADMIN_WEB_AUTH_MODE must be none, local_password, or azure_sso"
                )
            auth_mode: AdminAuthMode = raw_mode  # type: ignore[assignment]
        else:
            auth_mode = "local_password" if password else "none"
        if auth_mode == "local_password" and not password:
            raise ValueError(
                "EVANDER_ADMIN_WEB_PASSWORD is required when auth mode is local_password"
            )
        azure_sso = cls._azure_sso_from_env(auth_mode)
        branding_asset_dir = _env_optional_text("EVANDER_ADMIN_WEB_BRANDING_ASSET_DIR")
        resolved_branding_asset_dir = (
            Path(branding_asset_dir).expanduser()
            if branding_asset_dir is not None
            else DEFAULT_ADMIN_WEB_BRANDING_ASSET_DIR
        )
        return cls(
            auth_mode=auth_mode,
            username=username,
            password=password,
            session_secret=_env_text("EVANDER_ADMIN_WEB_SESSION_SECRET", "")
            or secrets.token_urlsafe(32),
            session_ttl_seconds=max(
                60,
                int(_env_text("EVANDER_ADMIN_WEB_SESSION_TTL_SECONDS", "43200")),
            ),
            cookie_name="evander_admin_session",
            cookie_secure=_env_flag("EVANDER_ADMIN_WEB_COOKIE_SECURE", default=False),
            workflow_event_machine_tokens=_env_workflow_event_machine_tokens(),
            azure_sso=azure_sso,
            branding=AdminWebBrandingConfig.from_env(),
            branding_asset_dir=resolved_branding_asset_dir,
            slow_request_threshold_ms=_env_non_negative_int(
                "EVANDER_ADMIN_WEB_SLOW_REQUEST_THRESHOLD_MS",
                DEFAULT_ADMIN_WEB_SLOW_REQUEST_THRESHOLD_MS,
            ),
        )

    @classmethod
    def _azure_sso_from_env(cls, auth_mode: AdminAuthMode) -> AzureSsoConfig | None:
        if auth_mode != "azure_sso":
            return None
        tenant_id = _env_optional_text("EVANDER_ADMIN_WEB_AZURE_TENANT_ID") or ""
        client_id = _env_optional_text("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID") or ""
        client_secret = _env_optional_text("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET") or ""
        authority_base_url = _env_text(
            "EVANDER_ADMIN_WEB_AZURE_AUTHORITY_BASE_URL",
            "https://login.microsoftonline.com",
        ).rstrip("/")
        public_base_url = (_env_optional_text("EVANDER_ADMIN_WEB_PUBLIC_BASE_URL") or "").rstrip(
            "/"
        )
        redirect_uri = _env_optional_text("EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI") or ""
        if not redirect_uri and public_base_url:
            redirect_uri = f"{public_base_url}{AZURE_SSO_CALLBACK_PATH}"
        post_login_redirect = _env_text("EVANDER_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT", "/")
        if not post_login_redirect.startswith("/") or post_login_redirect.startswith("//"):
            raise ValueError(
                "EVANDER_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT must be an absolute in-app path"
            )
        bootstrap_users = frozenset(
            item
            for item in (
                _normalize_user_identifier(value)
                for value in _env_csv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS")
            )
            if item
        )
        missing = []
        if not tenant_id:
            missing.append("EVANDER_ADMIN_WEB_AZURE_TENANT_ID")
        if not client_id:
            missing.append("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID")
        if not client_secret:
            missing.append("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET")
        if not redirect_uri:
            missing.append(
                "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI or EVANDER_ADMIN_WEB_PUBLIC_BASE_URL"
            )
        if missing:
            raise ValueError(f"Azure SSO admin auth is incomplete; missing {', '.join(missing)}")
        return AzureSsoConfig(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            authority_base_url=authority_base_url,
            bootstrap_users=bootstrap_users,
            post_login_redirect=post_login_redirect,
        )


class AdminWebAuth:
    def __init__(self, config: AdminWebConfig, user_store: AdminWebUserStore | None = None) -> None:
        self.config = config
        self.user_store = user_store
        self._privileged_session_ids: set[str] = set()
        self._azure_metadata_cache: dict[str, Any] | None = None
        if self.config.auth_mode == "azure_sso":
            if self.user_store is None:
                raise ValueError("Azure SSO admin auth requires an admin web user store")
            azure = self._require_azure_sso_config()
            self.user_store.bootstrap_admins(azure.bootstrap_users)
            users = self.user_store.list_users()
            if not users:
                raise ValueError(
                    "Azure SSO admin auth has no configured users; set "
                    "EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS for first startup "
                    "or populate runtime/admin-web.db"
                )
            if not any(self.user_store.can(user.login, ADMIN_OWNER_PERMISSION) for user in users):
                raise ValueError("Azure SSO admin auth requires at least one effective owner")
        if (
            self.config.auth_mode == "local_password"
            and self.user_store is not None
            and self.config.password is not None
        ):
            self.user_store.bootstrap_local_password_admin(
                login=self.config.username,
                password=self.config.password,
            )

    def authentication_payload(self, request: Request) -> dict[str, Any]:
        session = self.read_session(request)
        session_id = str(session.get("sid") or "").strip() if session else None
        default_identity = self.config.username if self.config.auth_mode == "none" else None
        identity = session.get("login") if session else default_identity
        permissions = list(self.effective_permissions(identity)) if identity else []
        return {
            "auth_mode": self.config.auth_mode,
            "auth_required": self.config.auth_required,
            "authenticated": session is not None or not self.config.auth_required,
            "username": session.get("login") if session else default_identity,
            "display_name": session.get("name") if session else default_identity,
            "can_manage_users": self._can_manage_users_for_permissions(permissions),
            "permissions": permissions,
            "effective_permissions": permissions,
            "session_id": session_id,
            "available_login_methods": (
                []
                if self.config.auth_mode == "none"
                else ["password"]
                if self.config.auth_mode == "local_password"
                else ["azure_sso"]
            ),
            "azure_sso_login_url": AZURE_SSO_START_PATH
            if self.config.auth_mode == "azure_sso"
            else None,
            "privileged_operations": {
                "unlocked": bool(session_id and session_id in self._privileged_session_ids),
            },
            "branding": self.config.branding.to_payload(),
        }

    def issue_workflow_event_api_token(
        self,
        request: Request,
        *,
        ttl_seconds: int = WORKFLOW_EVENT_API_TOKEN_TTL_SECONDS,
    ) -> dict[str, Any]:
        session = self.require(request)
        issued_at = int(time.time())
        normalized_ttl = max(1, int(ttl_seconds))
        expires_at = issued_at + normalized_ttl
        payload = {
            "kind": WORKFLOW_EVENT_API_TOKEN_KIND,
            "sub": str(session.get("sub") or ""),
            "login": str(session.get("login") or ""),
            "name": str(session.get("name") or ""),
            "sid": str(session.get("sid") or ""),
            "allowed_event_prefix": WORKFLOW_EVENT_API_ALLOWED_PREFIX,
            "iat": issued_at,
            "exp": expires_at,
        }
        return {
            "access_token": self._encode_signed_payload(payload),
            "token_type": "Bearer",
            "expires_in_seconds": normalized_ttl,
            "expires_at_epoch": expires_at,
            "allowed_event_prefix": WORKFLOW_EVENT_API_ALLOWED_PREFIX,
        }

    def verify_workflow_event_api_token(self, token: str) -> dict[str, Any] | None:
        payload = self._decode_signed_payload(str(token or "").strip())
        if payload is None:
            return None
        if payload.get("kind") != WORKFLOW_EVENT_API_TOKEN_KIND:
            return None
        if payload.get("allowed_event_prefix") != WORKFLOW_EVENT_API_ALLOWED_PREFIX:
            return None
        return payload

    def verify_workflow_event_machine_token(self, token: str) -> dict[str, Any] | None:
        normalized_token = str(token or "").strip()
        if not normalized_token:
            return None

        for configured_token in self.config.workflow_event_machine_tokens:
            if hmac.compare_digest(normalized_token, configured_token.token):
                return {
                    "kind": "workflow_event_api_machine",
                    "source_name": configured_token.source_name,
                    "allowed_event_prefix": WORKFLOW_EVENT_API_ALLOWED_PREFIX,
                }

        return None

    def login(self, username: str, password: str) -> str:
        if self.config.auth_mode == "none":
            return self._encode_session(
                subject=self.config.username,
                login=self.config.username,
                display_name=self.config.username,
            )
        if self.config.auth_mode != "local_password":
            raise ValueError("Password sign-in is unavailable for the configured auth mode")
        normalized_username = normalize_admin_web_login(username)
        if self.user_store is not None:
            user = self.user_store.verify_local_password(login=username, password=password)
            if user is not None:
                return self._encode_local_password_session(user)
        if normalized_username != normalize_admin_web_login(self.config.username):
            raise ValueError("Invalid username or password")
        expected = (self.config.password or "").encode("utf-8")
        if not hmac.compare_digest(password.encode("utf-8"), expected):
            raise ValueError("Invalid username or password")
        display_name = self.config.username
        if self.user_store is not None:
            user = self.user_store.record_login(
                login=self.config.username,
                display_name=self.config.username,
            )
            if user is not None:
                display_name = user.display_name or self.config.username
        return self._encode_session(
            subject=self.config.username,
            login=self.config.username,
            display_name=display_name,
            extras={"auth_mode": "local_password"},
        )

    def logout(self, request: Request) -> None:
        session = self.read_session(request)
        session_id = str(session.get("sid") or "").strip() if session else ""
        if session_id:
            self._privileged_session_ids.discard(session_id)
            if self.user_store is not None:
                self.user_store.revoke_session(session_id, float(session.get("exp") or 0))

    def mark_privileged_for_request(self, request: Request) -> None:
        session = self.require(request)
        session_id = str(session.get("sid") or "").strip()
        if session_id:
            self._privileged_session_ids.add(session_id)

    def clear_privileged_for_request(self, request: Request) -> None:
        session = self.read_session(request)
        session_id = str(session.get("sid") or "").strip() if session else ""
        if session_id:
            self._privileged_session_ids.discard(session_id)

    def privileged_for_request(self, request: Request) -> bool:
        session = self.read_session(request)
        session_id = str(session.get("sid") or "").strip() if session else ""
        return bool(session_id) and session_id in self._privileged_session_ids

    def require(self, request: Request) -> dict[str, Any]:
        session = self.read_session(request)
        if session is None and self.config.auth_required:
            raise PermissionError("Authentication required")
        if session is None:
            session = {
                "sub": self.config.username,
                "sid": "local-dev",
                "login": self.config.username,
                "name": self.config.username,
                "can_manage_users": True,
                "permissions": sorted(PERMISSION_CATALOG),
                "effective_permissions": sorted(PERMISSION_CATALOG),
            }
        return session

    def read_session(self, request: Request) -> dict[str, Any] | None:
        if self.config.auth_mode == "none":
            return self._annotate_session_permissions(
                {
                    "sub": self.config.username,
                    "sid": "local-dev",
                    "login": self.config.username,
                    "name": self.config.username,
                    "can_manage_users": True,
                }
            )
        token = request.cookies.get(self.config.cookie_name, "")
        if not token:
            return None
        payload = self._decode_signed_payload(token)
        if payload is None:
            return None
        return self.session_from_token_payload(payload)

    def session_from_token(self, token: str) -> dict[str, Any] | None:
        payload = self._decode_signed_payload(token)
        if payload is None:
            return None
        return self.session_from_token_payload(payload)

    def session_from_token_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if payload.get("kind") != "session":
            return None
        if self.user_store is not None and self.user_store.session_is_revoked(
            str(payload.get("sid") or "")
        ):
            return None
        if self.config.auth_mode == "azure_sso":
            user = self._user_for_session_payload(payload)
            if user is None:
                return None
            return self._enrich_azure_session(payload, user)
        if self.config.auth_mode == "local_password":
            return self._enrich_local_password_session(payload)
        return self._annotate_session_permissions(payload)

    def effective_permissions(
        self,
        request_or_login: Request | str | Mapping[str, Any] | None,
        directory_path: object | None = None,
    ) -> tuple[str, ...]:
        login = self._login_from_request_or_login(request_or_login)
        if login is None:
            return ()
        return self._effective_permissions_for_login(login, directory_path=directory_path)

    def can(
        self,
        request_or_login: Request | str | Mapping[str, Any] | None,
        permission: object,
        directory_path: object | None = None,
    ) -> bool:
        normalized_permission = str(permission or "").strip().lower()
        if not normalized_permission:
            return False
        return normalized_permission in self.effective_permissions(
            request_or_login, directory_path=directory_path
        )

    def require_permission(
        self,
        request_or_login: Request | str | Mapping[str, Any] | None,
        permission: object,
        directory_path: object | None = None,
    ) -> None:
        if self.can(request_or_login, permission, directory_path=directory_path):
            return
        raise PermissionError(f"Permission required: {str(permission or '').strip()}")

    def can_manage_users(
        self,
        request_or_login: Request | str | Mapping[str, Any] | None,
        directory_path: object | None = None,
    ) -> bool:
        permissions = self.effective_permissions(request_or_login, directory_path=directory_path)
        return self._can_manage_users_for_permissions(permissions)

    def _login_from_request_or_login(
        self, request_or_login: Request | str | Mapping[str, Any] | None
    ) -> str | None:
        if isinstance(request_or_login, Request):
            session = self.read_session(request_or_login)
            if session is None:
                return None
            return normalize_admin_web_login(session.get("login"))
        if isinstance(request_or_login, Mapping):
            return normalize_admin_web_login(request_or_login.get("login"))
        return normalize_admin_web_login(request_or_login)

    def _effective_permissions_for_login(
        self, login: object | None, directory_path: object | None = None
    ) -> tuple[str, ...]:
        normalized_login = normalize_admin_web_login(login)
        if normalized_login is None:
            return ()
        if self.config.auth_mode in {
            "none",
            "local_password",
        } and normalized_login == normalize_admin_web_login(self.config.username):
            return tuple(sorted(PERMISSION_CATALOG))
        if self.user_store is None:
            return ()
        return tuple(
            sorted(
                self.user_store.effective_permissions(
                    normalized_login, directory_path=directory_path
                )
            )
        )

    def _can_manage_users_for_permissions(self, permissions: list[str] | tuple[str, ...]) -> bool:
        return any(
            permission in permissions
            for permission in (
                ADMIN_OWNER_PERMISSION,
                USERS_MANAGE_PERMISSION,
            )
        )

    def _annotate_session_permissions(
        self, payload: dict[str, Any], *, directory_path: object | None = None
    ) -> dict[str, Any]:
        enriched = dict(payload)
        login = normalize_admin_web_login(enriched.get("login"))
        permissions = list(self._effective_permissions_for_login(login, directory_path))
        enriched["can_manage_users"] = self._can_manage_users_for_permissions(permissions)
        enriched["permissions"] = permissions
        enriched["effective_permissions"] = permissions
        return enriched

    def attach_session_cookie(self, response: Response, token: str) -> None:
        response.set_cookie(
            self.config.cookie_name,
            token,
            max_age=self.config.session_ttl_seconds,
            httponly=True,
            secure=self.config.cookie_secure,
            samesite="lax",
            path="/",
        )

    def clear_session_cookie(self, response: Response) -> None:
        response.delete_cookie(self.config.cookie_name, path="/")

    def attach_pending_azure_login(
        self, response: Response, *, state: str, code_verifier: str, next_path: str
    ) -> None:
        token = self._encode_signed_payload(
            {
                "kind": "azure_pending",
                "state": state,
                "code_verifier": code_verifier,
                "next": next_path,
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
            }
        )
        response.set_cookie(
            AZURE_SSO_COOKIE_NAME,
            token,
            max_age=600,
            httponly=True,
            secure=self.config.cookie_secure,
            samesite="lax",
            path=AZURE_SSO_CALLBACK_PATH,
        )

    def clear_pending_azure_login(self, response: Response) -> None:
        response.delete_cookie(AZURE_SSO_COOKIE_NAME, path=AZURE_SSO_CALLBACK_PATH)

    def read_pending_azure_login(self, request: Request) -> dict[str, Any] | None:
        token = request.cookies.get(AZURE_SSO_COOKIE_NAME, "")
        if not token:
            return None
        payload = self._decode_signed_payload(token)
        if payload is None or payload.get("kind") != "azure_pending":
            return None
        return payload

    def build_azure_login_redirect(self, next_path: str | None = None) -> tuple[str, str, str, str]:
        azure = self._require_azure_sso_config()
        state = secrets.token_urlsafe(24)
        code_verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        target = self._sanitize_post_login_target(next_path) or azure.post_login_redirect
        query = urlencode(
            {
                "client_id": azure.client_id,
                "response_type": "code",
                "redirect_uri": azure.redirect_uri,
                "response_mode": "query",
                "scope": " ".join(AZURE_SSO_SCOPES),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{azure.authorization_url}?{query}", state, code_verifier, target

    async def exchange_azure_callback(
        self, request: Request, code: str, state: str
    ) -> tuple[str, str]:
        azure = self._require_azure_sso_config()
        pending = self.read_pending_azure_login(request)
        if pending is None:
            raise ValueError("Missing Azure sign-in state")
        if not hmac.compare_digest(str(pending.get("state") or ""), state):
            raise ValueError("Invalid Azure sign-in state")
        token_response = await self._exchange_azure_code_for_token(
            code=code, code_verifier=str(pending["code_verifier"])
        )
        claims = await self._validate_azure_id_token(str(token_response.get("id_token") or ""))
        if str(claims.get("tid") or "") != azure.tenant_id:
            raise ValueError("Azure sign-in tenant is not allowed")
        login = self._extract_azure_login(claims)
        display_name = str(claims.get("name") or login)
        user = await run_in_threadpool(self._require_azure_user, login, display_name=display_name)
        token = self._encode_session(
            subject=str(claims.get("sub") or ""),
            login=user.login,
            display_name=user.display_name or display_name,
            extras={
                "auth_mode": "azure_sso",
                "tid": str(claims.get("tid") or ""),
                "oid": str(claims.get("oid") or ""),
                "iss": str(claims.get("iss") or ""),
            },
        )
        return token, self._sanitize_post_login_target(
            str(pending.get("next") or "")
        ) or azure.post_login_redirect

    async def _exchange_azure_code_for_token(
        self, *, code: str, code_verifier: str
    ) -> dict[str, Any]:
        azure = self._require_azure_sso_config()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                azure.token_url,
                data={
                    "grant_type": "authorization_code",
                    "client_id": azure.client_id,
                    "client_secret": azure.client_secret,
                    "code": code,
                    "redirect_uri": azure.redirect_uri,
                    "code_verifier": code_verifier,
                    "scope": " ".join(AZURE_SSO_SCOPES),
                },
                headers={"Accept": "application/json"},
            )
        if response.status_code >= 400:
            try:
                details = response.json()
            except ValueError:
                details = {"error": response.text.strip() or "token_exchange_failed"}
            raise ValueError(
                f"Azure token exchange failed: {details.get('error_description') or details.get('error') or response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict) or not payload.get("id_token"):
            raise ValueError("Azure token exchange did not return an ID token")
        return payload

    async def _validate_azure_id_token(self, id_token: str) -> dict[str, Any]:
        azure = self._require_azure_sso_config()
        metadata = await self._azure_metadata()
        jwks_client = jwt.PyJWKClient(str(metadata["jwks_uri"]))
        signing_key = await run_in_threadpool(jwks_client.get_signing_key_from_jwt, id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=azure.client_id,
            issuer=str(metadata["issuer"]),
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub"]},
        )
        if str(claims.get("tid") or "") != azure.tenant_id:
            raise ValueError("Azure sign-in tenant is not allowed")
        return claims

    async def _azure_metadata(self) -> dict[str, Any]:
        if self._azure_metadata_cache is not None:
            return self._azure_metadata_cache
        azure = self._require_azure_sso_config()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(azure.discovery_url, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "issuer" not in payload or "jwks_uri" not in payload:
            raise ValueError("Azure discovery document is incomplete")
        self._azure_metadata_cache = payload
        return payload

    def _extract_azure_login(self, claims: dict[str, Any]) -> str:
        login = (
            _normalize_user_identifier(claims.get("preferred_username"))
            or _normalize_user_identifier(claims.get("upn"))
            or _normalize_user_identifier(claims.get("email"))
        )
        if not login:
            raise ValueError("Azure sign-in token did not include a supported user identifier")
        return login

    def _require_azure_user(
        self, login: str, *, display_name: str | None = None
    ) -> AdminWebUserRecord:
        if self.user_store is None:
            raise ValueError("Azure SSO admin auth requires an admin web user store")
        user = self.user_store.record_login(login=login, display_name=display_name)
        if user is None:
            raise ValueError("Azure sign-in user is not allowed")
        return user

    def _user_for_session_payload(self, payload: dict[str, Any]) -> AdminWebUserRecord | None:
        if self.user_store is None:
            return None
        login = normalize_admin_web_login(payload.get("login"))
        if login is None:
            return None
        return self.user_store.get_user(login)

    def _enrich_azure_session(
        self, payload: dict[str, Any], user: AdminWebUserRecord
    ) -> dict[str, Any]:
        enriched = dict(payload)
        enriched["login"] = user.login
        enriched["name"] = user.display_name or str(payload.get("name") or user.login)
        return self._annotate_session_permissions(enriched)

    def _encode_local_password_session(self, user: AdminWebUserRecord) -> str:
        configured_login = normalize_admin_web_login(self.config.username)
        login = self.config.username if user.login == configured_login else user.login
        return self._encode_session(
            subject=user.login,
            login=login,
            display_name=user.display_name or login,
            extras={"auth_mode": "local_password"},
        )

    def _enrich_local_password_session(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        login = normalize_admin_web_login(payload.get("login"))
        configured_login = normalize_admin_web_login(self.config.username)
        if login is None:
            return None
        if login == configured_login:
            return self._annotate_session_permissions(payload)
        user = self._user_for_session_payload(payload)
        if user is None:
            return None
        enriched = dict(payload)
        enriched["login"] = user.login
        enriched["name"] = user.display_name or str(payload.get("name") or user.login)
        return self._annotate_session_permissions(enriched)

    def _require_azure_sso_config(self) -> AzureSsoConfig:
        if self.config.auth_mode != "azure_sso" or self.config.azure_sso is None:
            raise ValueError("Azure SSO is not enabled")
        return self.config.azure_sso

    def _sanitize_post_login_target(self, value: str | None) -> str | None:
        target = str(value or "").strip()
        if not target:
            return None
        if not target.startswith("/") or target.startswith("//"):
            return None
        decoded = target
        for _ in range(3):
            decoded = unquote(decoded)
        if decoded.startswith("//") or "\\" in decoded or any(
            ord(char) < 32 or ord(char) == 127 for char in decoded
        ):
            return None
        return target

    def _encode_session(
        self,
        *,
        subject: str,
        login: str,
        display_name: str,
        extras: dict[str, Any] | None = None,
    ) -> str:
        issued_at = int(time.time())
        payload: dict[str, Any] = {
            "kind": "session",
            "sub": subject,
            "login": login,
            "name": display_name,
            "sid": secrets.token_urlsafe(16),
            "iat": issued_at,
            "exp": issued_at + self.config.session_ttl_seconds,
        }
        if extras:
            payload.update(extras)
        return self._encode_signed_payload(payload)

    def _encode_signed_payload(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        signature = hmac.new(
            self.config.session_secret.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return f"{encoded}.{signature}"

    def _decode_signed_payload(self, token: str) -> dict[str, Any] | None:
        encoded, separator, signature = token.partition(".")
        if not separator:
            return None
        expected = hmac.new(
            self.config.session_secret.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padding = "=" * (-len(encoded) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        expires_at = int(payload.get("exp") or 0)
        if expires_at <= int(time.time()):
            return None
        return payload


def request_has_privileged_operations(
    *,
    auth: AdminWebAuth,
    service: Any,
    request: Request,
    required_permission: object | None = None,
    directory_path: object | None = None,
) -> bool:
    try:
        if required_permission is None:
            auth.require(request)
        else:
            require_permission = getattr(auth, "require_permission", None)
            if not callable(require_permission):
                return False
            require_permission(request, required_permission, directory_path=directory_path)
    except PermissionError:
        return False
    privileged_for_request = getattr(auth, "privileged_for_request", None)
    if not callable(privileged_for_request):
        return False
    return bool(privileged_for_request(request))


def json_error(
    message: str, *, status_code: int = 400, details: dict[str, Any] | None = None
) -> JSONResponse:
    payload: dict[str, Any] = {"error": message}
    if details:
        payload["details"] = details
    return JSONResponse(payload, status_code=status_code)


def json_ok(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)
