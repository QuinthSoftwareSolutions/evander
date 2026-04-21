from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AdminWebConfig:
    username: str
    password: str | None
    session_secret: str
    session_ttl_seconds: int
    cookie_name: str
    cookie_secure: bool

    @property
    def auth_required(self) -> bool:
        return bool((self.password or "").strip())

    @classmethod
    def from_env(cls) -> "AdminWebConfig":
        return cls(
            username=os.environ.get("AGENT_SANDBOX_ADMIN_WEB_USERNAME", "admin").strip() or "admin",
            password=os.environ.get("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "").strip() or None,
            session_secret=os.environ.get("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "").strip()
            or secrets.token_urlsafe(32),
            session_ttl_seconds=max(
                60,
                int(os.environ.get("AGENT_SANDBOX_ADMIN_WEB_SESSION_TTL_SECONDS", "43200").strip() or "43200"),
            ),
            cookie_name="agent_sandbox_admin_session",
            cookie_secure=_env_flag("AGENT_SANDBOX_ADMIN_WEB_COOKIE_SECURE", default=False),
        )


class AdminWebAuth:
    def __init__(self, config: AdminWebConfig) -> None:
        self.config = config
        self._privileged_session_id: str | None = None

    def authentication_payload(self, request: Request) -> dict[str, Any]:
        session = self.read_session(request)
        session_id = str(session.get("sid") or "").strip() if session else None
        return {
            "auth_required": self.config.auth_required,
            "authenticated": session is not None or not self.config.auth_required,
            "username": session.get("sub") if session else (self.config.username if not self.config.auth_required else None),
            "session_id": session_id,
            "privileged_operations": {
                "unlocked": bool(session_id and session_id == self._privileged_session_id),
            },
        }

    def login(self, username: str, password: str) -> str:
        if not self.config.auth_required:
            return self._encode_session(subject=self.config.username)
        if username.strip() != self.config.username:
            raise ValueError("Invalid username or password")
        expected = (self.config.password or "").encode("utf-8")
        if not hmac.compare_digest(password.encode("utf-8"), expected):
            raise ValueError("Invalid username or password")
        return self._encode_session(subject=self.config.username)

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
        if session is None:
            return not self.config.auth_required
        return str(session.get("sid") or "").strip() == self._privileged_session_id

    def require(self, request: Request) -> dict[str, Any]:
        session = self.read_session(request)
        if session is None and self.config.auth_required:
            raise PermissionError("Authentication required")
        if session is None:
            return {"sub": self.config.username, "sid": "local-dev"}
        return session

    def read_session(self, request: Request) -> dict[str, Any] | None:
        if not self.config.auth_required:
            return {"sub": self.config.username, "sid": "local-dev"}
        token = request.cookies.get(self.config.cookie_name, "")
        if not token:
            return None
        return self._decode_session(token)

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

    def _encode_session(self, *, subject: str) -> str:
        issued_at = int(time.time())
        payload = {
            "sub": subject,
            "sid": secrets.token_urlsafe(16),
            "iat": issued_at,
            "exp": issued_at + self.config.session_ttl_seconds,
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        signature = hmac.new(
            self.config.session_secret.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return f"{encoded}.{signature}"

    def _decode_session(self, token: str) -> dict[str, Any] | None:
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


def json_error(message: str, *, status_code: int = 400, details: dict[str, Any] | None = None) -> JSONResponse:
    payload: dict[str, Any] = {"error": message}
    if details:
        payload["details"] = details
    return JSONResponse(payload, status_code=status_code)


def json_ok(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)
