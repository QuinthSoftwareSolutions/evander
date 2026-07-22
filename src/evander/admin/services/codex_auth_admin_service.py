from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from evander.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from evander.config import GatewayConfig
from evander.models import AuditEvent


class CodexAuthProviderConfigPort(Protocol):
    def resolve_for_directory(self, directory_path: str) -> GatewayConfig: ...


class CodexAuthAuditPort(Protocol):
    def log(self, event: AuditEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class CodexAuthAdminDependencies:
    provider_config: CodexAuthProviderConfigPort
    audit: CodexAuthAuditPort
    client_factory: Callable[..., ContainerWorkerClient] = ContainerWorkerClient


class CodexAuthAdminService:
    """Coordinate worker Codex-account operations independently of HTTP transport."""

    def __init__(self, dependencies: CodexAuthAdminDependencies) -> None:
        self._deps = dependencies

    def status(self, directory_path: str) -> dict[str, Any]:
        return {
            "directory_path": directory_path,
            **self._client(directory_path).codex_auth_status(),
        }

    def start_device_login(
        self, directory_path: str, *, actor_user_id: str | None
    ) -> dict[str, Any]:
        try:
            payload = self._client(directory_path).start_codex_device_login()
        except ContainerWorkerClientError:
            self._audit("codex_subscription_login_start", directory_path, actor_user_id, "error")
            raise
        self._audit("codex_subscription_login_start", directory_path, actor_user_id, "allowed")
        return payload

    def device_login_status(self, directory_path: str, login_id: str) -> dict[str, Any]:
        return self._client(directory_path).codex_device_login_status(login_id)

    def cancel_device_login(
        self,
        directory_path: str,
        login_id: str,
        *,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        try:
            payload = self._client(directory_path).cancel_codex_device_login(login_id)
        except ContainerWorkerClientError:
            self._audit("codex_subscription_login_cancel", directory_path, actor_user_id, "error")
            raise
        self._audit("codex_subscription_login_cancel", directory_path, actor_user_id, "allowed")
        return payload

    def logout(self, directory_path: str, *, actor_user_id: str | None) -> dict[str, Any]:
        try:
            payload = self._client(directory_path).logout_codex_auth()
        except ContainerWorkerClientError:
            self._audit("codex_subscription_logout", directory_path, actor_user_id, "error")
            raise
        self._audit("codex_subscription_logout", directory_path, actor_user_id, "allowed")
        return payload

    def _client(self, directory_path: str) -> ContainerWorkerClient:
        config = self._deps.provider_config.resolve_for_directory(directory_path)
        if not config.container_worker_base_url:
            raise ValueError(
                "container_worker_base_url is required to manage worker Codex authentication"
            )
        return self._deps.client_factory(
            base_url=config.container_worker_base_url,
            token_env=config.container_worker_token_env,
            timeout_seconds=config.container_worker_request_timeout_seconds,
        )

    def _audit(
        self,
        event_type: str,
        directory_path: str,
        actor_user_id: str | None,
        decision: str,
    ) -> None:
        self._deps.audit.log(
            AuditEvent(
                event_type=event_type,
                directory_path=directory_path,
                actor_user_id=actor_user_id,
                provider="openai_chatgpt_subscription",
                decision=decision,
            )
        )
