"""Host-only OAuth custody for an approved Evander client, never client impersonation."""

from __future__ import annotations

import base64
import hashlib
import math
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

from evander.storage import JsonObjectStore

from .models import Connection, FigmaUnavailable, digest
from .state import Store

MCP_URL = "https://mcp.figma.com/mcp"
AUTHORIZE_URL = "https://www.figma.com/oauth/mcp"
TOKEN_URL = "https://api.figma.com/v1/oauth/token"


class OAuth:
    def __init__(self, store: Store, *, transport=None, secret_resolver=os.environ.get):
        self.store = store
        self.transport = transport
        self.secret_resolver = secret_resolver
        self.credentials = JsonObjectStore(
            store.root / "oauth.json", {"tokens": {}, "pending": {}}, file_mode=0o600
        )

    def _key(self, directory, connection_id):
        return digest([directory, connection_id])

    def start(self, directory, connection_id, config: Connection, login):
        if not self.secret_resolver(config.client_secret_env):
            raise FigmaUnavailable("Configure the approved OAuth client secret on the host first")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )

        def save(payload):
            payload["pending"] = {
                k: v for k, v in payload["pending"].items() if v["expires_at"] > time.time()
            }
            payload["pending"][digest(state)] = {
                "directory": directory,
                "connection_id": connection_id,
                "login": login,
                "binding": config.binding(),
                "verifier": verifier,
                "expires_at": time.time() + 600,
            }

        self.credentials.update_object(save)
        return (
            AUTHORIZE_URL
            + "?"
            + urlencode(
                {
                    "client_id": config.client_id,
                    "redirect_uri": config.redirect_uri,
                    "response_type": "code",
                    "scope": "mcp:connect",
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "resource": MCP_URL,
                }
            )
        )

    def pending(self, state, login):
        pending = self.credentials.read_object()["pending"].get(digest(state))
        if not pending or pending["expires_at"] < time.time() or pending["login"] != login:
            raise FigmaUnavailable("OAuth request expired or belongs to a different operator")
        return pending

    def complete(self, state, code, login):
        pending = self.pending(state, login)
        directory, connection_id = pending["directory"], pending["connection_id"]
        config = self.store.settings(directory)[1].connections.get(connection_id)
        if not config or config.binding() != pending["binding"]:
            raise FigmaUnavailable("Connection settings changed; restart OAuth")
        with self.store.file_lock("oauth:" + self._key(directory, connection_id)):
            pending = self.pending(state, login)
            self.credentials.update_object(
                lambda payload: payload["pending"].pop(digest(state), None)
            )
            token = self._exchange(
                config,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": config.redirect_uri,
                    "code_verifier": pending["verifier"],
                },
            )
            self._save(directory, connection_id, config, token, secrets.token_hex(16))
        return {"directory": directory, "connection_id": connection_id, "connected": True}

    def _exchange(self, config, data):
        secret = self.secret_resolver(config.client_secret_env)
        if not secret:
            raise FigmaUnavailable("Figma OAuth needs host configuration")
        try:
            with httpx.Client(
                transport=self.transport, timeout=20, follow_redirects=False
            ) as client:
                with client.stream(
                    "POST", TOKEN_URL, data=data, auth=(config.client_id, secret)
                ) as response:
                    body = bytearray()
                    for part in response.iter_bytes():
                        body.extend(part)
                        if len(body) > 64 * 1024:
                            raise ValueError("oversized token response")
                    response.raise_for_status()
                    import json

                    token = json.loads(body)
            if not isinstance(token, dict):
                raise ValueError("invalid token response")
            if not isinstance(token.get("access_token"), str) or not token["access_token"]:
                raise ValueError("missing token")
            token_type = token.get("token_type", "Bearer")
            if not isinstance(token_type, str) or token_type.lower() != "bearer":
                raise ValueError("unsupported token type")
            expires_in = float(token.get("expires_in", 3600))
            if not math.isfinite(expires_in) or expires_in < 0:
                raise ValueError("invalid expiry")
            if token.get("refresh_token") is not None and not isinstance(
                token["refresh_token"], str
            ):
                raise ValueError("invalid refresh token")
            token["expires_in"] = min(expires_in, 86400)
            return token
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise FigmaUnavailable(
                "Figma OAuth failed; check client approval or reconnect"
            ) from None

    def _save(self, directory, connection_id, config, token, generation):
        record = {
            "binding": config.binding(),
            "generation": generation,
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token"),
            "expires_at": time.time() + token["expires_in"],
        }
        self.credentials.update_object(
            lambda payload: payload["tokens"].__setitem__(
                self._key(directory, connection_id), record
            )
        )

    def status(self, directory, connection_id, config):
        token = self.credentials.read_object()["tokens"].get(
            self._key(directory, connection_id), {}
        )
        bound = token.get("binding") == config.binding()
        return {
            "connected": bool(bound and token.get("access_token")),
            "generation": token.get("generation") if bound else None,
            "expires_at": token.get("expires_at") if bound else None,
        }

    def token(self, directory, connection_id, config):
        with self.store.file_lock("oauth:" + self._key(directory, connection_id)):
            token = self.credentials.read_object()["tokens"].get(
                self._key(directory, connection_id)
            )
            if not token or token["binding"] != config.binding():
                raise FigmaUnavailable("Connect an approved Figma OAuth client in Admin Web")
            if token["expires_at"] <= time.time() + 90:
                if not token.get("refresh_token"):
                    raise FigmaUnavailable("Figma authorization expired; reconnect in Admin Web")
                new = self._exchange(
                    config, {"grant_type": "refresh_token", "refresh_token": token["refresh_token"]}
                )
                new.setdefault("refresh_token", token["refresh_token"])
                self._save(directory, connection_id, config, new, token["generation"])
                return new["access_token"]
            return token["access_token"]

    def disconnect(self, directory, connection_id):
        with self.store.file_lock("oauth:" + self._key(directory, connection_id)):

            def remove(payload):
                payload["tokens"].pop(self._key(directory, connection_id), None)
                payload["pending"] = {
                    key: value
                    for key, value in payload["pending"].items()
                    if (value["directory"], value["connection_id"]) != (directory, connection_id)
                }

            self.credentials.update_object(remove)
