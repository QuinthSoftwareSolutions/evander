from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

AdminAuthMode = Literal["none", "local_password", "azure_sso"]

AZURE_SSO_START_PATH = "/api/auth/azure-sso/start"
AZURE_SSO_CALLBACK_PATH = "/api/auth/azure-sso/callback"
AZURE_SSO_COOKIE_NAME = "agent_sandbox_admin_oidc"
AZURE_SSO_SCOPES = ("openid", "profile", "email")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    values = [item.strip() for item in raw.split(",")]
    return tuple(item for item in values if item)


def _normalize_user_identifier(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


@dataclass(frozen=True)
class AzureSsoConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    redirect_uri: str
    authority_base_url: str
    allowed_users: frozenset[str]
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
    azure_sso: AzureSsoConfig | None

    @property
    def auth_required(self) -> bool:
        return self.auth_mode != "none"

    @classmethod
    def from_env(cls) -> "AdminWebConfig":
        username = os.environ.get("AGENT_SANDBOX_ADMIN_WEB_USERNAME", "admin").strip() or "admin"
        password = os.environ.get("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "").strip() or None
        raw_mode = os.environ.get("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "").strip().lower()
        if raw_mode:
            if raw_mode not in {"none", "local_password", "azure_sso"}:
                raise ValueError(
                    "AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE must be none, local_password, or azure_sso"
                )
            auth_mode: AdminAuthMode = raw_mode  # type: ignore[assignment]
        else:
            auth_mode = "local_password" if password else "none"
        if auth_mode == "local_password" and not password:
            raise ValueError(
                "AGENT_SANDBOX_ADMIN_WEB_PASSWORD is required when auth mode is local_password"
            )
        azure_sso = cls._azure_sso_from_env(auth_mode)
        return cls(
            auth_mode=auth_mode,
            username=username,
            password=password,
            session_secret=os.environ.get("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "").strip()
            or secrets.token_urlsafe(32),
            session_ttl_seconds=max(
                60,
                int(
                    os.environ.get("AGENT_SANDBOX_ADMIN_WEB_SESSION_TTL_SECONDS", "43200").strip()
                    or "43200"
                ),
            ),
            cookie_name="agent_sandbox_admin_session",
            cookie_secure=_env_flag("AGENT_SANDBOX_ADMIN_WEB_COOKIE_SECURE", default=False),
            azure_sso=azure_sso,
        )

    @classmethod
    def _azure_sso_from_env(cls, auth_mode: AdminAuthMode) -> AzureSsoConfig | None:
        if auth_mode != "azure_sso":
            return None
        tenant_id = os.environ.get("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", "").strip()
        client_id = os.environ.get("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", "").strip()
        client_secret = os.environ.get("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", "").strip()
        authority_base_url = (
            os.environ.get(
                "AGENT_SANDBOX_ADMIN_WEB_AZURE_AUTHORITY_BASE_URL",
                "https://login.microsoftonline.com",
            )
            .strip()
            .rstrip("/")
        )
        public_base_url = (
            os.environ.get("AGENT_SANDBOX_ADMIN_WEB_PUBLIC_BASE_URL", "").strip().rstrip("/")
        )
        redirect_uri = os.environ.get("AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI", "").strip()
        if not redirect_uri and public_base_url:
            redirect_uri = f"{public_base_url}{AZURE_SSO_CALLBACK_PATH}"
        post_login_redirect = (
            os.environ.get("AGENT_SANDBOX_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT", "/").strip() or "/"
        )
        if not post_login_redirect.startswith("/") or post_login_redirect.startswith("//"):
            raise ValueError(
                "AGENT_SANDBOX_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT must be an absolute in-app path"
            )
        allowed_users = frozenset(
            item
            for item in (
                _normalize_user_identifier(value)
                for value in _env_csv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS")
            )
            if item
        )
        missing = []
        if not tenant_id:
            missing.append("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID")
        if not client_id:
            missing.append("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID")
        if not client_secret:
            missing.append("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET")
        if not redirect_uri:
            missing.append(
                "AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI or AGENT_SANDBOX_ADMIN_WEB_PUBLIC_BASE_URL"
            )
        if not allowed_users:
            missing.append("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS")
        if missing:
            raise ValueError(f"Azure SSO admin auth is incomplete; missing {', '.join(missing)}")
        return AzureSsoConfig(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            authority_base_url=authority_base_url,
            allowed_users=allowed_users,
            post_login_redirect=post_login_redirect,
        )


class AdminWebAuth:
    def __init__(self, config: AdminWebConfig) -> None:
        self.config = config
        self._privileged_session_id: str | None = None
        self._azure_metadata_cache: dict[str, Any] | None = None

    def authentication_payload(self, request: Request) -> dict[str, Any]:
        session = self.read_session(request)
        session_id = str(session.get("sid") or "").strip() if session else None
        default_identity = self.config.username if self.config.auth_mode == "none" else None
        return {
            "auth_mode": self.config.auth_mode,
            "auth_required": self.config.auth_required,
            "authenticated": session is not None or not self.config.auth_required,
            "username": session.get("login") if session else default_identity,
            "display_name": session.get("name") if session else default_identity,
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
                "unlocked": bool(session_id and session_id == self._privileged_session_id),
            },
        }

    def login(self, username: str, password: str) -> str:
        if self.config.auth_mode == "none":
            return self._encode_session(
                subject=self.config.username,
                login=self.config.username,
                display_name=self.config.username,
            )
        if self.config.auth_mode != "local_password":
            raise ValueError("Password sign-in is unavailable for the configured auth mode")
        if username.strip() != self.config.username:
            raise ValueError("Invalid username or password")
        expected = (self.config.password or "").encode("utf-8")
        if not hmac.compare_digest(password.encode("utf-8"), expected):
            raise ValueError("Invalid username or password")
        return self._encode_session(
            subject=self.config.username,
            login=self.config.username,
            display_name=self.config.username,
        )

    def logout(self, request: Request) -> None:
        session = self.read_session(request)
        session_id = str(session.get("sid") or "").strip() if session else ""
        if session_id and session_id == self._privileged_session_id:
            self._privileged_session_id = None

    def mark_privileged_for_request(self, request: Request) -> None:
        session = self.require(request)
        self._privileged_session_id = str(session.get("sid") or "").strip() or None

    def clear_privileged_for_request(self, request: Request) -> None:
        session = self.read_session(request)
        session_id = str(session.get("sid") or "").strip() if session else ""
        if session_id and session_id == self._privileged_session_id:
            self._privileged_session_id = None

    def privileged_for_request(self, request: Request) -> bool:
        session = self.read_session(request)
        session_id = str(session.get("sid") or "").strip() if session else ""
        return bool(session_id) and session_id == self._privileged_session_id

    def require(self, request: Request) -> dict[str, Any]:
        session = self.read_session(request)
        if session is None and self.config.auth_required:
            raise PermissionError("Authentication required")
        if session is None:
            return {
                "sub": self.config.username,
                "sid": "local-dev",
                "login": self.config.username,
                "name": self.config.username,
            }
        return session

    def read_session(self, request: Request) -> dict[str, Any] | None:
        if self.config.auth_mode == "none":
            return {
                "sub": self.config.username,
                "sid": "local-dev",
                "login": self.config.username,
                "name": self.config.username,
            }
        token = request.cookies.get(self.config.cookie_name, "")
        if not token:
            return None
        payload = self._decode_signed_payload(token)
        if payload is None:
            return None
        if payload.get("kind") != "session":
            return None
        return payload

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
        login = self._extract_allowed_azure_login(claims)
        token = self._encode_session(
            subject=str(claims.get("sub") or ""),
            login=login,
            display_name=str(claims.get("name") or login),
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
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
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

    def _extract_allowed_azure_login(self, claims: dict[str, Any]) -> str:
        azure = self._require_azure_sso_config()
        login = (
            _normalize_user_identifier(claims.get("preferred_username"))
            or _normalize_user_identifier(claims.get("upn"))
            or _normalize_user_identifier(claims.get("email"))
        )
        if not login:
            raise ValueError("Azure sign-in token did not include a supported user identifier")
        if login not in azure.allowed_users:
            raise ValueError("Azure sign-in user is not allowed")
        return login

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


def json_error(
    message: str, *, status_code: int = 400, details: dict[str, Any] | None = None
) -> JSONResponse:
    payload: dict[str, Any] = {"error": message}
    if details:
        payload["details"] = details
    return JSONResponse(payload, status_code=status_code)


def json_ok(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)
