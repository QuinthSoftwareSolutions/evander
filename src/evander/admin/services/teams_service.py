from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.messaging.teams_auth import (
    DeviceCodeLoginSession,
    JsonTeamsIdentityCredentialStore,
    MicrosoftDeviceCodeAuthConfig,
    TeamsAuthCredentials,
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
    ) -> tuple[str, GatewayConfig, str, TeamsDeviceCodeTokenProvider]:
        normalized_directory = self._deps.normalize_directory_reference(directory_path)
        resolved_config = self._deps.runtime.provider_config.resolve_for_directory(
            normalized_directory
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
            JsonTeamsIdentityCredentialStore(
                resolved_config.teams_delegated_credential_store_path,
                identity_id,
            ),
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

    def login(self, *, directory: str = "root") -> DeviceCodeLoginSession:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory)
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
    ) -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory)
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

    def status(self, *, directory: str = "root") -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory)
        )
        return self._teams_auth_payload(
            directory_path=normalized_directory,
            config=config,
            identity_id=identity_id,
            credentials=provider.load_credentials(),
        )

    def logout(self, *, directory: str = "root") -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory)
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
            entries.append(
                self._teams_auth_payload(
                    directory_path=directory_path,
                    config=config,
                    identity_id=identity_id,
                    credentials=provider.load_credentials(),
                )
            )
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
