"""Host-owned OAuth authorization code + PKCE, with durable single-use callbacks."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx

from .models import MCP_URL, MiroError
from .state import MiroStore, digest


def miro_endpoint(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or parts.netloc != "mcp.miro.com"
        or parts.username
        or parts.password
        or parts.fragment
        or parts.query
    ):
        raise MiroError("invalid_provider", "Miro OAuth discovery returned an unapproved endpoint")
    return value


class MiroOAuth:
    def __init__(self, store: MiroStore, transport=None):
        self.store = store
        self.transport = transport

    async def request(self, method: str, url: str, **kwargs) -> dict:
        miro_endpoint(url)
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=20, follow_redirects=False
            ) as client:
                async with client.stream(method, url, **kwargs) as response:
                    if response.status_code >= 300:
                        raise MiroError(
                            "authentication_failed",
                            "Miro authorization failed; reconnect in Admin Web",
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 65536:
                            raise MiroError(
                                "invalid_provider", "Miro OAuth response exceeds the size limit"
                            )
                    import json

                    result = json.loads(content)
                    if not isinstance(result, dict):
                        raise ValueError()
                    return result
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, MiroError):
                raise
            raise MiroError("authentication_failed", "Miro authorization is unavailable") from None

    async def begin(self, record: dict, principal: str) -> dict:
        async with self.store.lock("oauth:" + record["key"]):
            self.store.assert_generation(record)
            metadata = await self.request("GET", MCP_URL + ".well-known/oauth-authorization-server")
            if metadata.get("issuer", "").rstrip("/") != MCP_URL.rstrip("/"):
                raise MiroError("invalid_provider", "Miro OAuth issuer does not match")
            for name in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
                miro_endpoint(metadata.get(name, ""))
            if "S256" not in metadata.get("code_challenge_methods_supported", []):
                raise MiroError("invalid_provider", "Miro OAuth requires PKCE S256 support")
            registration = await self.request(
                "POST",
                metadata["registration_endpoint"],
                json={
                    "client_name": "Evander Miro Add-on",
                    "redirect_uris": [record["config"]["redirect_uri"]],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "client_secret_post",
                    "scope": "boards:read boards:write",
                },
            )
            if not registration.get("client_id") or not registration.get("client_secret"):
                raise MiroError(
                    "invalid_provider", "Miro registration did not return client credentials"
                )
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            pending = {
                "record": record,
                "principal": principal,
                "verifier": verifier,
                "registration": registration,
                "token_endpoint": metadata["token_endpoint"],
            }
            self.store.put(
                "oauth_pending",
                digest(state),
                {
                    "expires_at": time.time() + 600,
                    "encrypted": self.store.seal(pending),
                },
            )
            params = {
                "response_type": "code",
                "client_id": registration["client_id"],
                "redirect_uri": record["config"]["redirect_uri"],
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "boards:read boards:write",
                "resource": MCP_URL,
            }
            return {
                "authorization_url": metadata["authorization_endpoint"] + "?" + urlencode(params)
            }

    async def complete(self, state: str, code: str, principal: str) -> dict:
        key = digest(state)
        async with self.store.lock("callback:" + key):
            pending = self.store.get("oauth_pending", key)
            if not pending or pending["expires_at"] <= time.time():
                raise MiroError(
                    "invalid_callback", "Miro authorization expired or was already used"
                )
            saved = self.store.unseal(pending["encrypted"])
            if saved["principal"] != principal:
                raise MiroError(
                    "forbidden", "Complete Miro authorization with the operator who started it"
                )
            record = saved["record"]
            self.store.assert_generation(record)
            # Consume before network I/O: an uncertain token exchange must not be replayed.
            self.store.delete("oauth_pending", key)
            async with self.store.lock("oauth:" + record["key"]):
                registration = saved["registration"]
                tokens = await self.request(
                    "POST",
                    saved["token_endpoint"],
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "code_verifier": saved["verifier"],
                        "redirect_uri": record["config"]["redirect_uri"],
                        "resource": MCP_URL,
                        "client_id": registration["client_id"],
                        "client_secret": registration["client_secret"],
                    },
                )
                credentials = {
                    "registration": registration,
                    "token_endpoint": saved["token_endpoint"],
                    "tokens": self._tokens(tokens),
                }
                self.store.save_credentials(record, credentials)
                return {
                    "connection_id": record["config"]["id"],
                    "directory_path": record["config"]["directory_path"],
                    "status": "connected",
                }

    @staticmethod
    def _tokens(value: dict) -> dict:
        if not isinstance(value.get("access_token"), str) or not value["access_token"]:
            raise MiroError("authentication_failed", "Miro did not return an access token")
        if str(value.get("token_type", "Bearer")).lower() != "bearer":
            raise MiroError("authentication_failed", "Unsupported Miro token type")
        try:
            expiry = int(value.get("expires_in", 3600))
        except (ValueError, TypeError):
            raise MiroError(
                "authentication_failed", "Miro returned an invalid token expiry"
            ) from None
        if expiry <= 0:
            raise MiroError("authentication_failed", "Miro returned an expired token")
        return {**value, "expires_at": time.time() + expiry}

    async def access_token(self, record: dict) -> str:
        async with self.store.lock("oauth:" + record["key"]):
            credentials = self.store.credentials(record)
            if not credentials:
                raise MiroError("not_connected", "Connect this Miro account in Admin Web first")
            tokens = credentials["tokens"]
            if tokens["expires_at"] > time.time() + 90:
                return tokens["access_token"]
            if not tokens.get("refresh_token"):
                raise MiroError(
                    "not_connected", "Miro authorization expired; reconnect in Admin Web"
                )
            registration = credentials["registration"]
            try:
                refreshed = await self.request(
                    "POST",
                    credentials["token_endpoint"],
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": tokens["refresh_token"],
                        "client_id": registration["client_id"],
                        "client_secret": registration["client_secret"],
                        "resource": MCP_URL,
                    },
                )
            except MiroError:
                # Refresh-token rotation can succeed upstream even if the response is lost.
                self.store.delete("credentials", record["key"])
                raise
            refreshed.setdefault("refresh_token", tokens["refresh_token"])
            credentials["tokens"] = self._tokens(refreshed)
            self.store.save_credentials(record, credentials)
            return credentials["tokens"]["access_token"]
