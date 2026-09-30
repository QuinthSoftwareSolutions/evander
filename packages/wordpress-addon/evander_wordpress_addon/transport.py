"""Fixed-origin, bounded, host-authenticated protocol requests."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx

from evander.credentials.sanitize import reject_reflected_credentials
from evander.website_sessions import _public_transport, website_origin

from .models import Conflict, UnknownOutcome, WordPressError


class Client:
    def __init__(
        self,
        origin,
        auth,
        *,
        auth_material=None,
        transport_factory=_public_transport,
        max_response_bytes=16 * 1024**2,
    ):
        self.origin = website_origin(origin)
        self.auth = auth
        self.auth_material = auth_material
        self.transport_factory = transport_factory
        self.max_response_bytes = max_response_bytes

    async def call(self, path, payload=None, *, mutation=False):
        if not path.startswith("/") or path.startswith("//") or ".." in path or "\\" in path:
            raise WordPressError("Invalid protocol path")
        if self.auth_material is None:
            headers, secrets = await self.auth(), ()
        else:
            headers, secrets = await self.auth_material()
        secrets = [*headers.values(), *secrets]
        # Preserve safety for trusted header-only callers as well as managed bindings.
        for name, value in headers.items():
            if name.lower() == "authorization":
                scheme, _, encoded = value.partition(" ")
                if scheme.lower() == "basic":
                    try:
                        username, password = (
                            base64.b64decode(encoded, validate=True).decode().split(":", 1)
                        )
                    except (ValueError, UnicodeError):
                        raise WordPressError("Invalid host authentication") from None
                    secrets.extend((encoded, username if password == "X" else password))
                elif scheme.lower() == "bearer":
                    secrets.append(encoded)
        secrets = tuple(secrets)
        try:
            async with asyncio.timeout(60):
                transport = self.transport_factory(self.origin)
                if hasattr(transport, "__await__"):
                    transport = await transport
                async with httpx.AsyncClient(
                    transport=transport, follow_redirects=False, trust_env=False, timeout=45
                ) as client:
                    async with client.stream(
                        "POST" if payload is not None else "GET",
                        self.origin + path,
                        json=payload,
                        headers={
                            **headers,
                            "accept": "application/json",
                            "accept-encoding": "identity",
                        },
                    ) as response:
                        data = bytearray()
                        async for part in response.aiter_bytes():
                            if len(data) + len(part) > self.max_response_bytes:
                                raise WordPressError("Protocol response exceeds its size limit")
                            data.extend(part)
                        if response.status_code == 409:
                            raise Conflict("Remote resource or release changed")
                        if response.status_code >= 500 and mutation:
                            raise UnknownOutcome("Remote mutation outcome needs reconciliation")
                        if not 200 <= response.status_code < 300:
                            raise WordPressError("Remote protocol request was rejected")
                        reject_reflected_credentials(bytes(data), secrets)
                        value = json.loads(data)
                        reject_reflected_credentials(value, secrets)
                        return value
        except (Conflict, WordPressError):
            raise
        except Exception:
            if mutation:
                raise UnknownOutcome("Remote mutation outcome needs reconciliation") from None
            raise WordPressError("Remote protocol is unavailable") from None


class Companion:
    def __init__(self, client, rest_path="/wp-json/"):
        self.client = client
        self.rest_path = rest_path

    async def call(self, operation, payload=None, *, mutation=False):
        return await self.client.call(
            self.rest_path + "evander/v1/" + operation, payload, mutation=mutation
        )

    async def discover(self):
        result = await self.call("discover")
        if result.get("protocol_version") != 1:
            raise WordPressError("Companion plugin protocol version is incompatible")
        return result
