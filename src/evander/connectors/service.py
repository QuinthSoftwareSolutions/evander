from __future__ import annotations

import asyncio
import base64
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from inspect import isclass
import json
import os
from time import monotonic
from typing import Any, Mapping

import httpx
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.exceptions import Unresolvable

from evander.addons import LoadedAddonCatalog
from evander.connectors.drivers import (
    ConnectorInvocationError,
    ConnectorUpstreamAuthenticationError,
    FilesystemConnectorDriver,
    RestConnectorDriver,
)
from evander.connectors.grants import validate_connector_grant_mapping
from evander.connectors.models import (
    ConnectorAuthProfile,
    ConnectorDefinition,
    ConnectorDirectoryConfig,
    ConnectorInvocationResult,
    normalize_connector_id,
    normalize_connector_operation,
)
from evander.connectors.registry import ConnectorCallContext, ConnectorRegistry
from evander.models import SessionManifest
from evander.credentials import CredentialError, CredentialStore
from evander.credentials.bindings import connector_binding, validate_connector_values


class ConnectorPolicyError(RuntimeError):
    pass


_MAX_OAUTH_TOKEN_RESPONSE_BYTES = 64 * 1024
_NO_REMOTE_SCHEMA_REGISTRY = Registry()
_MANAGED_VALUES: ContextVar[tuple[str, ...]] = ContextVar("connector_managed_values", default=())


@dataclass
class _OAuthToken:
    value: str
    expires_at_monotonic: float
    refresh_at_monotonic: float
    profile_fingerprint: str


def default_connector_registry(
    *, rest_transport: httpx.AsyncBaseTransport | None = None
) -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register("rest", RestConnectorDriver(transport=rest_transport))
    registry.register("filesystem", FilesystemConnectorDriver())
    return registry


def connector_registry_from_addons(
    addon_loader: Any,
    *,
    base_dir: Any,
    rest_transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectorRegistry:
    from evander.addons import load_addons

    catalog = load_addons(addon_loader, base_dir=base_dir)
    return connector_registry_from_catalog(catalog, rest_transport=rest_transport)


def connector_registry_from_catalog(
    catalog: LoadedAddonCatalog,
    *,
    rest_transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectorRegistry:
    registry = default_connector_registry(rest_transport=rest_transport)
    for registration in catalog.registrations:
        for driver_name, candidate in registration.connector_drivers.items():
            driver = candidate
            if isclass(candidate):
                driver = candidate()
            elif not (hasattr(driver, "validate_definition") and hasattr(driver, "invoke")):
                if not callable(candidate):
                    raise TypeError(
                        f"add-on {registration.addon_id} connector driver {driver_name!r} "
                        "must be a driver or zero-argument factory"
                    )
                driver = candidate()
            if not (hasattr(driver, "validate_definition") and hasattr(driver, "invoke")):
                raise TypeError(
                    f"add-on {registration.addon_id} connector driver {driver_name!r} is invalid"
                )
            registry.register(str(driver_name), driver)
    return registry


class ConnectorService:
    def __init__(
        self,
        *,
        runtime_dir: Any,
        provider_config: Any,
        artifact_store_factory: Any,
        registry: ConnectorRegistry | None = None,
        environ: Mapping[str, str] | None = None,
        auth_transport: httpx.AsyncBaseTransport | None = None,
        credential_store: CredentialStore | None = None,
        session_authorizer: Any | None = None,
        session_argument_authorizer: Any | None = None,
        host_services: Mapping[str, Any] | None = None,
    ) -> None:
        self.credentials = credential_store or CredentialStore(runtime_dir)
        self.runtime_dir = runtime_dir
        self.provider_config = provider_config
        self.artifact_store_factory = artifact_store_factory
        self.registry = registry or default_connector_registry()
        self.environ = environ if environ is not None else os.environ
        self.auth_transport = auth_transport
        self._oauth_tokens: dict[tuple[str, str], _OAuthToken] = {}
        self._oauth_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.session_authorizer = session_authorizer
        self.session_argument_authorizer = session_argument_authorizer
        self.host_services = dict(host_services or {})

    def authorize_current(self, session_id, connector_id, operation_name, arguments):
        """Recheck grants and policy between steps of a durable host operation."""
        from datetime import UTC, datetime
        session = (self.session_authorizer(session_id) if self.session_authorizer else
                   self.provider_config.get_session_manifest(session_id))
        if session.expires_at <= datetime.now(UTC):
            raise ConnectorPolicyError("Connector session has expired")
        if self.session_argument_authorizer is None:
            raise ConnectorPolicyError("Current connector argument policy is unavailable")
        self.session_argument_authorizer(
            session,
            "call_connector",
            {
                "session_id": session.session_id,
                "connector_id": normalize_connector_id(connector_id),
                "operation": normalize_connector_operation(operation_name),
                "arguments": arguments,
            },
        )
        return self.require_operation(session, connector_id, operation_name, arguments)

    async def host_auth(self, directory_path, connector_id):
        """Resolve a configured binding for trusted domain code, never an MCP result."""
        config = self.provider_config.resolve_connectors_for_directory(directory_path)
        connector = config.connectors.get(connector_id)
        if connector is None or connector.disabled:
            raise ConnectorPolicyError("Host connector binding is unavailable")
        headers = await self._auth_headers(directory_path=directory_path, config=config, connector=connector)
        return connector, headers

    def config_for_session(self, session: SessionManifest) -> ConnectorDirectoryConfig:
        return self.provider_config.resolve_connectors_for_directory(session.directory_path)

    def validate_config(self, config: ConnectorDirectoryConfig) -> None:
        for connector in config.connectors.values():
            if connector.disabled:
                continue
            self.registry.require(connector.driver).validate_definition(connector)

    def validate_grants(
        self, session_id: str, directory_path: str, grants: dict[str, list[str]]
    ) -> None:
        config = self.provider_config.resolve_connectors_for_directory(directory_path)
        self.validate_config(config)
        validate_connector_grant_mapping(config, grants, session_id=session_id)

    def list_for_session(self, session: SessionManifest) -> list[dict[str, Any]]:
        config = self.config_for_session(session)
        self.validate_config(config)
        result: list[dict[str, Any]] = []
        for connector_id, granted_operations in sorted(session.connector_grants.items()):
            connector = config.connectors.get(connector_id)
            if connector is None or connector.disabled:
                continue
            operations: list[dict[str, Any]] = []
            for operation_name in granted_operations:
                operation = connector.operations.get(operation_name)
                if operation is None or operation.disabled:
                    continue
                operations.append(
                    {
                        "name": operation_name,
                        "description": operation.description,
                        "input_schema": operation.input_schema,
                        "access": operation.access,
                        "requires_approval": operation.requires_approval,
                        "result_mode": operation.result_mode,
                    }
                )
            if not operations:
                continue
            result.append(
                {
                    "connector_id": connector_id,
                    "display_name": connector.display_name,
                    "description": connector.description,
                    "available": self.auth_status(config, connector, directory_path=session.directory_path)["available"],
                    "operations": operations,
                }
            )
        return result

    def require_operation(
        self,
        session: SessionManifest,
        connector_id: str,
        operation_name: str,
        arguments: dict[str, Any],
    ) -> tuple[ConnectorDirectoryConfig, ConnectorDefinition, Any]:
        normalized_id = normalize_connector_id(connector_id)
        normalized_operation = normalize_connector_operation(operation_name)
        granted = session.connector_grants.get(normalized_id, [])
        if normalized_operation not in granted:
            raise ConnectorPolicyError(
                f"connector operation {normalized_id}.{normalized_operation} is not granted "
                f"to session {session.session_id}"
            )
        config = self.config_for_session(session)
        connector = config.connectors.get(normalized_id)
        if connector is None or connector.disabled:
            raise ConnectorPolicyError(f"connector {normalized_id!r} is unavailable")
        operation = connector.operations.get(normalized_operation)
        if operation is None or operation.disabled:
            raise ConnectorPolicyError(
                f"connector operation {normalized_id}.{normalized_operation} is unavailable"
            )
        driver = self.registry.require(connector.driver)
        driver.validate_definition(connector)
        try:
            errors = sorted(
                Draft202012Validator(
                    operation.input_schema,
                    registry=_NO_REMOTE_SCHEMA_REGISTRY,
                ).iter_errors(arguments),
                key=lambda error: list(error.absolute_path),
            )
        except Unresolvable as exc:
            raise ConnectorPolicyError(
                "connector input schema contains an unsupported reference"
            ) from exc
        if errors:
            path = ".".join(str(part) for part in errors[0].absolute_path)
            prefix = f" at {path}" if path else ""
            raise ConnectorPolicyError(
                f"connector arguments do not match schema{prefix}: {errors[0].message}"
            )
        return config, connector, operation

    async def invoke(self, **kwargs) -> ConnectorInvocationResult:
        context_token = _MANAGED_VALUES.set(())
        try:
            return await self._invoke(**kwargs)
        except Exception:
            if _MANAGED_VALUES.get():
                raise ConnectorInvocationError("Managed connector request failed") from None
            raise
        finally:
            _MANAGED_VALUES.reset(context_token)

    async def _invoke(
        self,
        *,
        session: SessionManifest,
        connector_id: str,
        operation_name: str,
        arguments: dict[str, Any],
    ) -> ConnectorInvocationResult:
        config, connector, operation = self.require_operation(
            session, connector_id, operation_name, arguments
        )
        normalized_id = normalize_connector_id(connector_id)
        normalized_operation = normalize_connector_operation(operation_name)
        try:
            auth_headers = await self._auth_headers(
                directory_path=session.directory_path,
                config=config,
                connector=connector,
            )
        except httpx.HTTPError as exc:
            raise ConnectorPolicyError("connector authentication is unavailable") from exc
        context = ConnectorCallContext(
            session_id=session.session_id,
            directory_path=session.directory_path,
            runtime_dir=self.runtime_dir,
            artifact_store=self.artifact_store_factory(),
            auth_headers=auth_headers,
            sensitive_values=_MANAGED_VALUES.get(),
            host_service=self,
            authorize=lambda: self.authorize_current(session.session_id,
                normalized_id, normalized_operation, arguments),
        )
        driver = self.registry.require(connector.driver)
        try:
            result = await driver.invoke(
                context=context,
                connector_id=normalized_id,
                definition=connector,
                operation_name=normalized_operation,
                operation=operation,
                arguments=arguments,
            )
            if _MANAGED_VALUES.get():
                from evander.credentials.sanitize import reject_reflected_credentials
                reject_reflected_credentials(result.model_dump(mode="json"), _MANAGED_VALUES.get())
            return result
        except ConnectorUpstreamAuthenticationError:
            if not connector.auth_profile:
                raise
            profile = config.auth_profiles.get(connector.auth_profile)
            if profile is None:
                raise
            if profile.type != "oauth2_client_credentials":
                raise
            token_key = (session.directory_path, connector.auth_profile)
            self._oauth_tokens.pop(token_key, None)
            if operation.access != "read":
                raise
            try:
                retry_context = ConnectorCallContext(
                    session_id=session.session_id,
                    directory_path=session.directory_path,
                    runtime_dir=self.runtime_dir,
                    artifact_store=self.artifact_store_factory(),
                    auth_headers=await self._auth_headers(
                        directory_path=session.directory_path,
                        config=config,
                        connector=connector,
                    ),
                    sensitive_values=_MANAGED_VALUES.get(),
                    host_service=self,
                    authorize=context.authorize,
                )
                result = await driver.invoke(
                    context=retry_context,
                    connector_id=normalized_id,
                    definition=connector,
                    operation_name=normalized_operation,
                    operation=operation,
                    arguments=arguments,
                )
                if _MANAGED_VALUES.get():
                    from evander.credentials.sanitize import reject_reflected_credentials
                    reject_reflected_credentials(result.model_dump(mode="json"), _MANAGED_VALUES.get())
                return result
            except ConnectorUpstreamAuthenticationError:
                self._oauth_tokens.pop(token_key, None)
                raise
            except httpx.HTTPError as exc:
                raise ConnectorInvocationError("connector upstream request failed") from exc
        except httpx.HTTPError as exc:
            raise ConnectorInvocationError("connector upstream request failed") from exc

    def auth_status(
        self, config: ConnectorDirectoryConfig, connector: ConnectorDefinition, *, directory_path: str = "root"
    ) -> dict[str, Any]:
        if connector.auth_profile is None:
            return {"available": True, "type": "none"}
        profile = config.auth_profiles.get(connector.auth_profile)
        if profile is None:
            return {"available": False, "type": "missing"}
        if profile.credential_ref:
            try:
                self._managed_values(directory_path, config, connector)
                available = True
            except CredentialError:
                available = False
            return {"available": available, "type": profile.type, "credential_ref": profile.credential_ref}
        required_envs = self._required_env_names(profile)
        missing = [name for name in required_envs if not str(self.environ.get(name) or "").strip()]
        return {
            "available": not missing,
            "type": profile.type,
            "missing_secret_count": len(missing),
        }

    async def _auth_headers(
        self,
        *,
        directory_path: str,
        config: ConnectorDirectoryConfig,
        connector: ConnectorDefinition,
    ) -> dict[str, str]:
        if connector.auth_profile is None:
            return {}
        profile = config.auth_profiles.get(connector.auth_profile)
        if profile is None:
            raise ConnectorPolicyError("connector authentication is unavailable")
        if profile.type == "none":
            return {}
        managed = None
        if profile.credential_ref:
            try:
                managed, metadata = self._managed_values(directory_path, config, connector)
            except CredentialError:
                raise ConnectorPolicyError("Managed connector credentials are unavailable") from None
            # Only secret-bearing fields belong in the response reflection check.
            # Usernames and OAuth client IDs are normally public identifiers.
            # API-key-as-username Basic auth (including Freshservice) conventionally
            # uses the literal dummy password X; protect the key, not that sentinel.
            if profile.type == "basic":
                secret = managed["username"] if managed["password"] == "X" else managed["password"]
                _MANAGED_VALUES.set((secret,))
            elif profile.type == "oauth2_client_credentials":
                _MANAGED_VALUES.set((managed["client_secret"],))
            else:
                _MANAGED_VALUES.set((managed["token"],))
            if profile.type == "bearer":
                return {"Authorization": "Bearer " + managed["token"]}
            if profile.type == "api_key_header":
                return {str(profile.header_name): managed["token"]}
            if profile.type == "basic":
                encoded = base64.b64encode(f"{managed['username']}:{managed['password']}".encode()).decode()
                _MANAGED_VALUES.set((*_MANAGED_VALUES.get(), encoded))
                return {"Authorization": "Basic " + encoded}
        if profile.type == "bearer_env":
            return {"Authorization": f"Bearer {self._secret(profile.token_env)}"}
        if profile.type == "api_key_header_env":
            return {str(profile.header_name): self._secret(profile.token_env)}
        if profile.type == "basic_env":
            raw = f"{self._secret(profile.username_env)}:{self._secret(profile.password_env)}"
            return {"Authorization": f"Basic {base64.b64encode(raw.encode()).decode()}"}
        token = await self._oauth_token(
            directory_path=directory_path,
            profile_id=str(connector.auth_profile),
            profile=profile,
            managed=managed,
            credential_revision=metadata["revision"] if managed is not None else None,
        )
        if managed is not None:
            # A rotation/disable during token acquisition invalidates the result.
            if self.credentials.metadata(profile.credential_ref)["revision"] != metadata["revision"]:
                self._oauth_tokens.pop((directory_path, str(connector.auth_profile)), None)
                raise ConnectorPolicyError("Managed connector credentials changed")
            _MANAGED_VALUES.set((*_MANAGED_VALUES.get(), token))
        return {"Authorization": f"Bearer {token}"}

    async def _oauth_token(
        self,
        *,
        directory_path: str,
        profile_id: str,
        profile: ConnectorAuthProfile,
        managed: dict | None = None,
        credential_revision: str | None = None,
    ) -> str:
        key = (directory_path, profile_id)
        client_id = managed["client_id"] if managed is not None else self._secret(profile.client_id_env)
        client_secret = managed["client_secret"] if managed is not None else self._secret(profile.client_secret_env)
        profile_fingerprint = self._oauth_profile_fingerprint(
            profile,
            client_id=client_id,
            client_secret=client_secret,
        )
        profile_fingerprint += ":" + (credential_revision or "environment")
        cached = self._oauth_tokens.get(key)
        now = monotonic()
        if (
            cached is not None
            and cached.profile_fingerprint == profile_fingerprint
            and cached.refresh_at_monotonic > now
        ):
            return cached.value
        lock = self._oauth_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._oauth_tokens.get(key)
            now = monotonic()
            if (
                cached is not None
                and cached.profile_fingerprint == profile_fingerprint
                and cached.refresh_at_monotonic > now
            ):
                return cached.value
            data: dict[str, str] = {"grant_type": "client_credentials"}
            if profile.scopes:
                data["scope"] = " ".join(profile.scopes)
            if profile.audience:
                data["audience"] = profile.audience
            auth = None
            if profile.client_auth_method == "client_secret_basic":
                auth = (client_id, client_secret)
            else:
                data.update({"client_id": client_id, "client_secret": client_secret})
            async with httpx.AsyncClient(
                timeout=30.0,
                transport=self.auth_transport,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST", str(profile.token_url), data=data, auth=auth
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise ConnectorPolicyError("connector authentication is unavailable")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > _MAX_OAUTH_TOKEN_RESPONSE_BYTES:
                            raise ConnectorPolicyError("connector authentication is unavailable")
            try:
                payload = json.loads(content)
            except (UnicodeDecodeError, ValueError) as exc:
                raise ConnectorPolicyError("connector authentication is unavailable") from exc
            if not isinstance(payload, dict):
                raise ConnectorPolicyError("connector authentication is unavailable")
            token = str(payload.get("access_token") or "").strip()
            if not token:
                raise ConnectorPolicyError("connector authentication is unavailable")
            advertised_expiry = payload.get("expires_in")
            if advertised_expiry is None:
                expires_in = 3600
            else:
                try:
                    expires_in = max(0, int(advertised_expiry))
                except (TypeError, ValueError):
                    expires_in = 0
            expires_at = now + expires_in
            refresh_at = expires_at - min(30.0, expires_in * 0.1)
            self._oauth_tokens[key] = _OAuthToken(
                value=token,
                expires_at_monotonic=expires_at,
                refresh_at_monotonic=refresh_at,
                profile_fingerprint=profile_fingerprint,
            )
            return token

    @staticmethod
    def _oauth_profile_fingerprint(
        profile: ConnectorAuthProfile,
        *,
        client_id: str,
        client_secret: str,
    ) -> str:
        payload = profile.model_dump(mode="json")
        payload["resolved_client_id"] = client_id
        payload["resolved_client_secret"] = client_secret
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()

    def _managed_values(self, directory_path, config, connector):
        profile_id = str(connector.auth_profile)
        profile = config.auth_profiles[profile_id]
        connector_id = next((key for key, value in config.connectors.items() if value is connector), None)
        if connector_id is None:
            raise CredentialError("Connector binding is unavailable")
        owner_resolver = getattr(self.provider_config, "connector_credential_owner", None)
        owner = owner_resolver(directory_path, profile_id) if owner_resolver else directory_path
        values, metadata = self.credentials.resolve(profile.credential_ref, directory=directory_path,
            binding=connector_binding(profile_id, connector_id, profile, connector), kind="connector")
        if metadata["owner_directory"] != owner:
            raise CredentialError("Credential reference must be declared in its owning directory")
        validate_connector_values(profile, values)
        return values, metadata

    def _secret(self, env_name: str | None) -> str:
        normalized = str(env_name or "").strip()
        value = str(self.environ.get(normalized) or "").strip()
        if not normalized or not value:
            raise ConnectorPolicyError("connector authentication is unavailable")
        return value

    @staticmethod
    def _required_env_names(profile: ConnectorAuthProfile) -> tuple[str, ...]:
        values: list[str | None] = []
        if profile.type in {"bearer_env", "api_key_header_env"}:
            values.append(profile.token_env)
        elif profile.type == "basic_env":
            values.extend((profile.username_env, profile.password_env))
        elif profile.type == "oauth2_client_credentials":
            values.extend((profile.client_id_env, profile.client_secret_env))
        return tuple(str(value) for value in values if value)
