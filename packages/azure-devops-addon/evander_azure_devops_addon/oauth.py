"""Microsoft Entra delegated ADO tokens. Credentials stay on the host."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import time
from urllib.parse import urlsplit

import httpx

CALLBACK_PATH = "/api/admin/me/ado-connections/oauth/callback"
ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
ADO_SCOPES = ("vso.code_write", "vso.profile", "vso.work_write")
SCOPES = "offline_access " + " ".join(f"{ADO_RESOURCE}/{scope}" for scope in ADO_SCOPES)


def has_required_scopes(scope: object) -> bool:
    if not isinstance(scope, str):
        return False
    granted = {item.rsplit("/", 1)[-1] for item in scope.split()}
    return set(ADO_SCOPES).issubset(granted)


class AdoOAuthError(ValueError):
    def __init__(self, *, reconnect: bool = False):
        super().__init__(
            "Microsoft connection needs reconnection"
            if reconnect
            else "Microsoft connection is unavailable"
        )
        self.reconnect = reconnect


@dataclass(frozen=True)
class AdoOAuthConfig:
    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str

    @classmethod
    def from_env(cls) -> AdoOAuthConfig:
        if os.environ.get("EVANDER_ADO_OAUTH_ENABLED", "").lower() not in {"1", "true", "yes"}:
            raise AdoOAuthError()

        def setting(name):
            return (
                os.environ.get(f"EVANDER_ADO_OAUTH_{name}")
                or os.environ.get(f"EVANDER_ADMIN_WEB_AZURE_{name}")
                or ""
            ).strip()

        tenant, client, secret = (
            setting(name) for name in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET")
        )
        public_url = os.environ.get("EVANDER_ADMIN_WEB_PUBLIC_BASE_URL", "").rstrip("/")
        redirect = os.environ.get("EVANDER_ADO_OAUTH_REDIRECT_URI", "") or (
            public_url + CALLBACK_PATH if public_url else ""
        )
        parsed = urlsplit(redirect)
        if (
            not tenant
            or not re.fullmatch(r"[A-Za-z0-9.-]+", tenant)
            or tenant.lower() in {"common", "organizations", "consumers"}
            or not client
            or not secret
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != CALLBACK_PATH
            or not (
                parsed.scheme == "https"
                or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})
            )
        ):
            raise AdoOAuthError()
        return cls(tenant, client, secret, redirect)

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0"

    @property
    def binding(self) -> list[str]:
        return [self.tenant_id, self.client_id, self.redirect_uri]


def exchange_token(
    config: AdoOAuthConfig,
    *,
    code: str | None = None,
    verifier: str | None = None,
    previous: dict | None = None,
) -> dict:
    if previous is not None and previous.get("client_binding") != config.binding:
        raise AdoOAuthError(reconnect=True)
    granted_scope = SCOPES if previous is None else previous.get("scope")
    data = {"client_id": config.client_id, "client_secret": config.client_secret}
    if granted_scope:
        data["scope"] = granted_scope
    if previous is None:
        data.update(
            grant_type="authorization_code",
            code=code,
            code_verifier=verifier,
            redirect_uri=config.redirect_uri,
        )
    else:
        data.update(grant_type="refresh_token", refresh_token=previous["refresh_token"])
    try:
        response = httpx.post(
            config.authority + "/token", data=data, timeout=15, follow_redirects=False
        )
        if response.status_code in {400, 401, 403}:
            error = response.json().get("error")
            raise AdoOAuthError(
                reconnect=error in {"invalid_grant", "interaction_required", "consent_required"}
            )
        response.raise_for_status()
        payload = response.json()
        access = payload["access_token"]
        refresh = payload.get("refresh_token") or (previous or {}).get("refresh_token")
        scope = str(payload.get("scope") or granted_scope or "")
        if (
            str(payload.get("token_type", "")).lower() != "bearer"
            or not isinstance(access, str)
            or not access
            or any(c.isspace() for c in access)
            or not isinstance(refresh, str)
            or not refresh
            or not has_required_scopes(scope)
        ):
            raise AdoOAuthError(reconnect=True)
        lifetime = int(payload["expires_in"])
        if lifetime <= 0:
            raise AdoOAuthError(reconnect=True)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": time.time() + min(lifetime, 86400),
            "scope": scope,
            "client_binding": config.binding,
        }
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
        if isinstance(exc, AdoOAuthError):
            raise
        raise AdoOAuthError() from None
