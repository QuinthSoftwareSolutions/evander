from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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
    runtime_dir: Path | None = None
    wake_reconciliation: Callable[[], None] | None = None
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

    def list_profiles(self, directory_path: str) -> dict[str, Any]:
        payload = self._client(directory_path).list_codex_auth_profiles()
        queue_counts = self._queued_counts_by_profile()
        profiles = []
        for raw in payload.get("profiles", []):
            if not isinstance(raw, dict):
                continue
            profile = dict(raw)
            profile_id = str(profile.get("profile_id") or "").strip()
            profile["queued_prompt_count"] = queue_counts.get(profile_id, 0)
            profiles.append(profile)
            if self._profile_is_ready(profile):
                self._make_subscription_profile_ready(profile_id)
        config = self._deps.provider_config.resolve_for_directory(directory_path)
        result = {
            "directory_path": directory_path,
            "worker_url": config.container_worker_base_url,
            "profiles": profiles,
        }
        # A manual refresh is also the operator-controlled wake for capacity that
        # recovered before the ordinary effect-orchestrator poll.
        self._wake()
        return result

    def profile_status(self, directory_path: str, profile_id: str) -> dict[str, Any]:
        payload = self._client(directory_path).codex_auth_profile_status(profile_id)
        payload["queued_prompt_count"] = self._queued_counts_by_profile().get(profile_id, 0)
        if self._profile_is_ready(payload):
            self._make_subscription_profile_ready(profile_id)
        self._wake()
        return payload

    def create_profile(
        self,
        directory_path: str,
        payload: dict[str, Any],
        *,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        result = self._mutate(
            "codex_subscription_profile_create",
            directory_path,
            actor_user_id,
            lambda: self._client(directory_path).create_codex_auth_profile(payload),
        )
        if bool(result.get("enabled")):
            self._make_subscription_profile_ready(str(result.get("profile_id") or ""))
            self._wake()
        return result

    def patch_profile(
        self,
        directory_path: str,
        profile_id: str,
        payload: dict[str, Any],
        *,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        result = self._mutate(
            "codex_subscription_profile_update",
            directory_path,
            actor_user_id,
            lambda: self._client(directory_path).patch_codex_auth_profile(profile_id, payload),
        )
        if payload.get("enabled") is True:
            self._make_subscription_profile_ready(profile_id)
            self._wake()
        return result

    def start_profile_device_login(
        self,
        directory_path: str,
        profile_id: str,
        *,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        return self._mutate(
            "codex_subscription_profile_login_start",
            directory_path,
            actor_user_id,
            lambda: self._client(directory_path).start_codex_profile_device_login(profile_id),
        )

    def profile_device_login_status(
        self, directory_path: str, profile_id: str, login_id: str
    ) -> dict[str, Any]:
        result = self._client(directory_path).codex_profile_device_login_status(
            profile_id, login_id
        )
        if result.get("status") == "completed":
            self._make_subscription_profile_ready(profile_id)
            self._wake()
        return result

    def cancel_profile_device_login(
        self,
        directory_path: str,
        profile_id: str,
        login_id: str,
        *,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        return self._mutate(
            "codex_subscription_profile_login_cancel",
            directory_path,
            actor_user_id,
            lambda: self._client(directory_path).cancel_codex_profile_device_login(
                profile_id, login_id
            ),
        )

    def logout_profile(
        self,
        directory_path: str,
        profile_id: str,
        *,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        return self._mutate(
            "codex_subscription_profile_logout",
            directory_path,
            actor_user_id,
            lambda: self._client(directory_path).logout_codex_auth_profile(profile_id),
        )

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

    def _mutate(
        self,
        event_type: str,
        directory_path: str,
        actor_user_id: str | None,
        action: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            payload = action()
        except ContainerWorkerClientError:
            self._audit(event_type, directory_path, actor_user_id, "error")
            raise
        self._audit(event_type, directory_path, actor_user_id, "allowed")
        return payload

    def _queued_counts_by_profile(self) -> dict[str, int]:
        runtime_dir = self._deps.runtime_dir
        if runtime_dir is None:
            return {}
        try:
            from evander.messaging.agent_prompt_queue import AgentPromptQueueStore

            records = AgentPromptQueueStore(
                Path(runtime_dir) / "queued-agent-prompts.json"
            ).read_all()
        except Exception:
            return {}
        counts: dict[str, int] = {}
        for record in records.values():
            profile_id = str(getattr(record, "subscription_profile_id", "") or "").strip()
            if profile_id:
                counts[profile_id] = counts.get(profile_id, 0) + 1
        return counts

    def _make_subscription_profile_ready(self, profile_id: str) -> None:
        runtime_dir = self._deps.runtime_dir
        normalized = str(profile_id or "").strip()
        if runtime_dir is None or not normalized:
            return
        try:
            from evander.messaging.agent_prompt_queue import AgentPromptQueueStore

            AgentPromptQueueStore(
                Path(runtime_dir) / "queued-agent-prompts.json"
            ).make_subscription_profile_ready(normalized)
        except Exception:
            pass

    @staticmethod
    def _profile_is_ready(profile: dict[str, Any]) -> bool:
        if not (
            bool(profile.get("enabled"))
            and bool(profile.get("available"))
            and bool(profile.get("signed_in"))
        ):
            return False
        rate_limits = profile.get("rate_limits")
        if not isinstance(rate_limits, dict):
            return True
        buckets: list[dict[str, Any]] = []
        default = rate_limits.get("default")
        if isinstance(default, dict):
            buckets.append(default)
        by_limit_id = rate_limits.get("by_limit_id")
        if isinstance(by_limit_id, dict):
            buckets.extend(
                bucket for bucket in by_limit_id.values() if isinstance(bucket, dict)
            )
        for bucket in buckets:
            if bucket.get("rate_limit_reached_type"):
                return False
            for window_name in ("primary", "secondary"):
                window = bucket.get(window_name)
                if not isinstance(window, dict):
                    continue
                try:
                    if float(window.get("used_percent")) >= 100:
                        return False
                except (TypeError, ValueError):
                    continue
        return True

    def _wake(self) -> None:
        wake = self._deps.wake_reconciliation
        if wake is None:
            return
        try:
            wake()
        except Exception:
            pass
