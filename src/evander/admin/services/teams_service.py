from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from evander.config import GatewayConfig
from evander.credentials.teams import teams_credential_store, teams_owner_credential_store
from evander.directories import DirectoryService
from evander.integration_services.teams import normalize_teams_user_target
from evander.messaging.teams_auth import (
    DeviceCodeLoginSession,
    MicrosoftDeviceCodeAuthConfig,
    TeamsAuthCredentials,
    TeamsAuthenticationRequiredError,
    TeamsDeviceCodeTokenProvider,
)


class TeamsGraphChannelClient(Protocol):
    def list_channels(self, *, team_id: str) -> list[dict[str, Any]]: ...


class TeamsGraphClient:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        token_provider: TeamsDeviceCodeTokenProvider,
    ) -> None:
        self._config = config
        self._token_provider = token_provider

    def list_channels(self, *, team_id: str) -> list[dict[str, Any]]:
        encoded_team_id = quote(team_id, safe="")
        next_url = f"{self._config.teams_graph_api_base_url}/teams/{encoded_team_id}/channels"
        channels: list[dict[str, Any]] = []
        with httpx.Client(timeout=self._config.teams_timeout_seconds) as client:
            while next_url:
                response = client.get(
                    next_url,
                    headers={
                        "Authorization": f"Bearer {self._token_provider.get_bearer_token()}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                for item in payload.get("value", []):
                    if isinstance(item, dict):
                        channels.append(item)
                raw_next = payload.get("@odata.nextLink")
                next_url = str(raw_next).strip() if raw_next else ""
        return channels


TeamsGraphClientFactory = Callable[
    [GatewayConfig, TeamsDeviceCodeTokenProvider],
    TeamsGraphChannelClient,
]


@dataclass(frozen=True)
class TeamsAdminDependencies:
    runtime: Any
    normalize_directory_reference: Any
    graph_client_factory: TeamsGraphClientFactory | None = None


class TeamsAdminService:
    def __init__(self, dependencies: TeamsAdminDependencies) -> None:
        self._deps = dependencies

    def normalize_directory_reference(self, reference: str | None) -> str:
        return self._deps.normalize_directory_reference(reference)

    def _teams_auth_provider_for_directory(
        self,
        directory_path: str | None,
        credential_ref: str | None = None,
    ) -> tuple[str, GatewayConfig, str, TeamsDeviceCodeTokenProvider]:
        normalized_directory = self._deps.normalize_directory_reference(directory_path)
        resolved_config = self._deps.runtime.provider_config.resolve_for_directory(
            normalized_directory
        )
        credential_store = None
        if credential_ref is not None:
            resolved_config, credential_store = teams_owner_credential_store(
                resolved_config, normalized_directory, credential_ref
            )
        if resolved_config.teams_backend != "delegated_graph_api":
            raise ValueError(
                f"Directory '{normalized_directory}' is not configured for delegated Teams auth "
                f"(teams_backend={resolved_config.teams_backend!r})."
            )
        identity_id = str(resolved_config.teams_delegated_identity_id or "").strip()
        if not identity_id:
            raise ValueError(
                f"Directory '{normalized_directory}' is missing teams_delegated_identity_id."
            )
        client_id = str(resolved_config.teams_delegated_client_id or "").strip()
        if not client_id:
            raise ValueError(
                f"Directory '{normalized_directory}' is missing teams_delegated_client_id."
            )
        provider = TeamsDeviceCodeTokenProvider(
            MicrosoftDeviceCodeAuthConfig(
                client_id=client_id,
                tenant_id=resolved_config.teams_delegated_tenant_id,
                scopes=resolved_config.teams_delegated_scopes,
                authority_base_url=resolved_config.teams_delegated_authority_base_url,
                timeout_seconds=resolved_config.teams_timeout_seconds,
            ),
            credential_store or teams_credential_store(resolved_config, normalized_directory),
        )
        return normalized_directory, resolved_config, identity_id, provider

    def _teams_auth_payload(
        self,
        *,
        directory_path: str,
        config: GatewayConfig,
        identity_id: str,
        credentials: TeamsAuthCredentials | None,
    ) -> dict[str, object]:
        return {
            "directory_path": directory_path,
            "identity_id": identity_id,
            "backend": config.teams_backend,
            "authenticated": credentials is not None,
            "status": "authenticated" if credentials is not None else "reauth-required",
            "account_identifier": identity_id,
            "tenant_id": config.teams_delegated_tenant_id,
            "scopes": list(config.teams_delegated_scopes),
            "expires_at": credentials.expires_at if credentials is not None else None,
            "issued_at": credentials.issued_at if credentials is not None else None,
            "store_path": str(config.teams_delegated_credential_store_path),
        }

    def _graph_client(
        self,
        config: GatewayConfig,
        provider: TeamsDeviceCodeTokenProvider,
    ) -> TeamsGraphChannelClient:
        if self._deps.graph_client_factory is not None:
            return self._deps.graph_client_factory(config, provider)
        return TeamsGraphClient(config=config, token_provider=provider)

    def login(
        self, *, directory: str = "root", credential_ref: str | None = None
    ) -> DeviceCodeLoginSession:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory, credential_ref)
        )
        session = provider.begin_device_code_login()
        metadata = dict(session.metadata)
        metadata.update(
            {
                "directory_path": normalized_directory,
                "identity_id": identity_id,
                "store_path": str(config.teams_delegated_credential_store_path),
            }
        )
        return session.model_copy(update={"metadata": metadata})

    def complete_login(
        self,
        login_session: DeviceCodeLoginSession,
        *,
        directory: str = "root",
        timeout_seconds: float | None = None,
        credential_ref: str | None = None,
    ) -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory, credential_ref)
        )
        credentials = provider.complete_device_code_login(
            login_session, timeout_seconds=timeout_seconds
        )
        return self._teams_auth_payload(
            directory_path=normalized_directory,
            config=config,
            identity_id=identity_id,
            credentials=credentials,
        )

    def status(
        self, *, directory: str = "root", credential_ref: str | None = None
    ) -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory, credential_ref)
        )
        return self._teams_auth_payload(
            directory_path=normalized_directory,
            config=config,
            identity_id=identity_id,
            credentials=provider.load_credentials(),
        )

    def logout(
        self, *, directory: str = "root", credential_ref: str | None = None
    ) -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory, credential_ref)
        )
        provider.clear_credentials()
        return {
            "directory_path": normalized_directory,
            "identity_id": identity_id,
            "backend": config.teams_backend,
            "status": "logged_out",
            "authenticated": False,
            "store_path": str(config.teams_delegated_credential_store_path),
        }

    def list(self) -> list[dict[str, object]]:
        directory_service = DirectoryService(self._deps.runtime.directories)
        directory_paths = [
            "root",
            *[item.path for item in directory_service.list_descendants("root")],
        ]
        entries: list[dict[str, object]] = []
        for directory_path in directory_paths:
            resolved_config = self._deps.runtime.provider_config.resolve_for_directory(
                directory_path
            )
            if resolved_config.teams_backend != "delegated_graph_api":
                continue
            _, config, identity_id, provider = self._teams_auth_provider_for_directory(
                directory_path
            )
            unavailable = False
            try:
                credentials = provider.load_credentials()
            except TeamsAuthenticationRequiredError:
                # Administration must still list other identities when one record
                # is disabled, deleted or no longer usable with its live binding.
                # Do not change the token provider's fail-closed retrieval path.
                credentials = None
                unavailable = True
            entry = self._teams_auth_payload(
                directory_path=directory_path,
                config=config,
                identity_id=identity_id,
                credentials=credentials,
            )
            if unavailable:
                entry["status"] = "unavailable"
            entries.append(entry)
        return entries

    def list_channels(
        self,
        *,
        directory: str = "root",
        team_id: str,
        channel_name: str | None = None,
        include_all: bool = False,
    ) -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory)
        )
        normalized_team_id = str(team_id or "").strip()
        if not normalized_team_id:
            raise ValueError("team_id is required")
        normalized_channel_name = str(channel_name or "").strip()
        if not normalized_channel_name and not include_all:
            raise ValueError("channel_name is required unless include_all=True")

        channels = self._graph_client(config, provider).list_channels(team_id=normalized_team_id)
        matches: list[dict[str, object]] = []
        for channel in channels:
            display_name = str(channel.get("displayName") or "").strip()
            if (
                normalized_channel_name
                and display_name.casefold() != normalized_channel_name.casefold()
            ):
                continue
            channel_id = str(channel.get("id") or "").strip()
            matches.append(
                {
                    "team_id": normalized_team_id,
                    "channel_id": channel_id,
                    "display_name": display_name,
                    "membership_type": str(channel.get("membershipType") or "").strip() or None,
                    "web_url": str(channel.get("webUrl") or "").strip() or None,
                    "allowlist_entry": f"{normalized_team_id}/{channel_id}" if channel_id else None,
                }
            )

        return {
            "directory_path": normalized_directory,
            "identity_id": identity_id,
            "backend": config.teams_backend,
            "team_id": normalized_team_id,
            "channel_name": normalized_channel_name or None,
            "match_count": len(matches),
            "channels": matches if normalized_channel_name or include_all else [],
        }

    def resolve_direct_chat_targets(
        self,
        *,
        directory: str = "root",
        user_refs: list[str],
    ) -> list[dict[str, str]]:
        normalized_directory = self._deps.normalize_directory_reference(directory)
        config = self._deps.runtime.provider_config.resolve_for_directory(normalized_directory)
        if config.teams_backend not in {"graph_api", "delegated_graph_api"}:
            raise ValueError(
                f"Directory '{normalized_directory}' is not configured for Microsoft Graph "
                f"Teams access (teams_backend={config.teams_backend!r})."
            )

        normalized_refs: list[str] = []
        seen_refs: set[str] = set()
        for raw_ref in user_refs:
            user_ref = str(raw_ref or "").strip()
            if not user_ref:
                continue
            if "@" not in user_ref:
                raise ValueError(
                    f"Teams direct-chat user {user_ref!r} must be a UPN or mail address."
                )
            dedupe_key = user_ref.casefold()
            if dedupe_key in seen_refs:
                continue
            seen_refs.add(dedupe_key)
            normalized_refs.append(user_ref)
        if not normalized_refs:
            raise ValueError("At least one Teams direct-chat user UPN is required.")

        allowed_user_targets = [normalize_teams_user_target(ref) for ref in normalized_refs]
        teams_service = self._deps.runtime.teams_service_for_directory(
            normalized_directory,
            allowed_channel_targets=[],
            allowed_user_targets=allowed_user_targets,
        )
        resolve_user_id = getattr(teams_service, "resolve_user_id_for_reference", None)
        if not callable(resolve_user_id):
            raise ValueError(
                f"Directory '{normalized_directory}' does not support Teams direct-chat resolution."
            )

        resolved_users: list[tuple[str, str]] = []
        seen_user_ids: set[str] = set()
        for user_ref in normalized_refs:
            try:
                user_id = str(resolve_user_id(user_ref) or "").strip()
            except Exception as exc:
                raise ValueError(
                    f"Microsoft Graph could not resolve Teams user {user_ref!r}: {exc}"
                ) from exc
            if not user_id:
                raise ValueError(
                    f"Microsoft Graph could not resolve Teams user {user_ref!r}."
                )
            user_id_key = user_id.casefold()
            if user_id_key in seen_user_ids:
                continue
            seen_user_ids.add(user_id_key)
            resolved_users.append((user_ref, user_id))

        chat_service = self._deps.runtime.teams_service_for_directory(
            normalized_directory,
            allowed_channel_targets=[],
            allowed_user_targets=[
                normalize_teams_user_target(user_id) for _, user_id in resolved_users
            ],
        )
        ensure_chat = getattr(chat_service, "ensure_one_on_one_chat", None)
        if not callable(ensure_chat):
            raise ValueError(
                f"Directory '{normalized_directory}' does not support Teams direct-chat resolution."
            )

        resolutions: list[dict[str, str]] = []
        for user_ref, user_id in resolved_users:
            try:
                chat_target = str(ensure_chat(user_id) or "").strip()
            except Exception as exc:
                raise ValueError(
                    f"Microsoft Graph could not ensure a one-to-one chat for Teams user "
                    f"{user_ref!r}: {exc}"
                ) from exc
            if not chat_target.startswith("chat:") or not chat_target.removeprefix(
                "chat:"
            ).strip():
                raise ValueError(
                    f"Microsoft Graph did not return a one-to-one chat for Teams user {user_ref!r}."
                )
            resolutions.append(
                {
                    "user_principal_name": user_ref,
                    "user_id": user_id,
                    "chat_target": chat_target,
                }
            )
        return resolutions
