from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import math
from pathlib import Path
from typing import Any, Protocol

from evander.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from evander.config import GatewayConfig
from evander.codex_auth import (
    CodexAuthMode,
    CodexSubscriptionResetRequest,
    effective_codex_auth_mode,
    effective_subscription_profile_id,
    normalize_subscription_profile_id,
    normalize_subscription_quota_limit_id,
    normalize_subscription_quota_window_minutes,
)
from evander.codex_capacity import subscription_rate_limit_state
from evander.models import AuditEvent
from evander.repositories.session_manifests import SessionManifestRepository
from evander.subscription_profile_bindings import subscription_profile_binding_lock


class CodexAuthProviderConfigPort(Protocol):
    def resolve_for_directory(self, directory_path: str) -> GatewayConfig: ...

    def resolve_for_session_id(self, session_id: str) -> GatewayConfig: ...


class CodexAuthAuditPort(Protocol):
    def log(self, event: AuditEvent) -> None: ...


class CodexSubscriptionResetConflict(ValueError):
    """The configured destination no longer matches the redemption attempt."""


class CodexSubscriptionProfileConflict(ValueError):
    """A profile is still referenced or the selected worker changed."""


@dataclass(frozen=True, slots=True)
class CodexAuthAdminDependencies:
    provider_config: CodexAuthProviderConfigPort
    audit: CodexAuthAuditPort
    runtime_dir: Path | None = None
    wake_reconciliation: Callable[[], None] | None = None
    client_factory: Callable[..., ContainerWorkerClient] = ContainerWorkerClient
    session_repository: SessionManifestRepository | None = None


class CodexAuthAdminService:
    """Coordinate worker Codex-account operations independently of HTTP transport."""

    def __init__(self, dependencies: CodexAuthAdminDependencies) -> None:
        self._deps = dependencies

    def status(self, directory_path: str) -> dict[str, Any]:
        return {
            "directory_path": directory_path,
            **self._client(directory_path).codex_auth_status(),
        }

    def list_profiles(self, directory_path: str, *, worker_id: str | None = None) -> dict[str, Any]:
        config = self._deps.provider_config.resolve_for_directory(directory_path)
        if worker_id:
            from evander.admin.services.container_worker_assignment_service import (
                ContainerWorkerAssignmentService,
            )

            config = ContainerWorkerAssignmentService(config).config_for_worker_id(worker_id)
        worker_url = str(config.container_worker_base_url or "").strip()
        payload = self._client_for_config(config).list_codex_auth_profiles()
        queue_counts = self._queued_counts_by_profile(worker_url=worker_url)
        profiles = []
        for raw in payload.get("profiles", []):
            if not isinstance(raw, dict):
                continue
            profile = dict(raw)
            profile_id = str(profile.get("profile_id") or "").strip()
            profile["queued_prompt_count"] = queue_counts.get(profile_id, 0)
            profiles.append(profile)
            if self._profile_is_ready(profile):
                self._make_subscription_profile_ready(profile_id, worker_url=worker_url)
        result = {
            "directory_path": directory_path,
            "worker_url": worker_url,
            "profiles": profiles,
        }
        # A manual refresh is also the operator-controlled wake for capacity that
        # recovered before the ordinary effect-orchestrator poll.
        self._wake()
        return result

    def profile_status(self, directory_path: str, profile_id: str) -> dict[str, Any]:
        config = self._deps.provider_config.resolve_for_directory(directory_path)
        worker_url = str(config.container_worker_base_url or "").strip()
        payload = self._client_for_config(config).codex_auth_profile_status(profile_id)
        payload["queued_prompt_count"] = self._queued_counts_by_profile(
            worker_url=worker_url
        ).get(profile_id, 0)
        if self._profile_is_ready(payload):
            self._make_subscription_profile_ready(profile_id, worker_url=worker_url)
        self._wake()
        return payload

    def profile_quota(
        self,
        directory_path: str,
        profile_id: str,
        *,
        limit_id: str = "codex",
        window_duration_minutes: int = 10080,
    ) -> dict[str, Any]:
        """Read quota telemetry without waking queued work or returning account metadata."""
        profile_id = str(normalize_subscription_profile_id(profile_id, allow_none=False))
        limit_id = normalize_subscription_quota_limit_id(limit_id)
        window_duration_minutes = normalize_subscription_quota_window_minutes(
            window_duration_minutes
        )
        result = {
            "directory_path": directory_path,
            "profile_id": profile_id,
            "limit_id": limit_id,
            "window_duration_minutes": window_duration_minutes,
            "available": False,
            "unavailable_reason": None,
            "used_percent": None,
            "remaining_percent": None,
            "resets_at": None,
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        config = self._deps.provider_config.resolve_for_directory(directory_path)
        if not config.container_worker_base_url:
            return {**result, "unavailable_reason": "worker_not_configured"}
        try:
            profile = self._client_for_config(config).codex_auth_profile_status(profile_id)
        except ContainerWorkerClientError as exc:
            # Worker errors may contain provider response bodies or credential details.
            reason = "profile_not_found" if exc.status_code == 404 else "worker_unavailable"
            return {**result, "unavailable_reason": reason}

        observed_at = datetime.now(UTC)
        result["observed_at"] = observed_at.isoformat().replace("+00:00", "Z")
        if not isinstance(profile, Mapping) or profile.get("profile_id") != profile_id:
            return {**result, "unavailable_reason": "profile_mismatch"}
        if profile.get("signed_in") is not True:
            return {**result, "unavailable_reason": "signed_out"}
        if profile.get("available") is not True or profile.get("error"):
            return {**result, "unavailable_reason": "telemetry_unavailable"}
        rate_limits = profile.get("rate_limits")
        if not isinstance(rate_limits, Mapping):
            return {**result, "unavailable_reason": "window_unavailable"}

        by_limit_id = rate_limits.get("by_limit_id")
        named_bucket = by_limit_id.get(limit_id) if isinstance(by_limit_id, Mapping) else None
        buckets = [named_bucket]
        default_bucket = rate_limits.get("default")
        if isinstance(default_bucket, Mapping) and (
            default_bucket.get("limit_id") == limit_id
            or (not default_bucket.get("limit_id") and limit_id == "codex")
        ):
            buckets.append(default_bucket)
        for bucket in buckets:
            if not isinstance(bucket, Mapping):
                continue
            for window_name in ("primary", "secondary"):
                window = bucket.get(window_name)
                normalized = _quota_window(window, window_duration_minutes, observed_at)
                if normalized is not None:
                    return {**result, "available": True, **normalized}
        return {**result, "unavailable_reason": "window_unavailable"}

    def create_profile(
        self,
        directory_path: str,
        payload: dict[str, Any],
        *,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        config = self._deps.provider_config.resolve_for_directory(directory_path)
        result = self._mutate(
            "codex_subscription_profile_create",
            directory_path,
            actor_user_id,
            lambda: self._client_for_config(config).create_codex_auth_profile(payload),
        )
        if bool(result.get("enabled")):
            self._make_subscription_profile_ready(
                str(result.get("profile_id") or ""),
                worker_url=str(config.container_worker_base_url or "").strip(),
            )
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
        config = self._deps.provider_config.resolve_for_directory(directory_path)
        result = self._mutate(
            "codex_subscription_profile_update",
            directory_path,
            actor_user_id,
            lambda: self._client_for_config(config).patch_codex_auth_profile(
                profile_id, payload
            ),
        )
        if payload.get("enabled") is True:
            self._make_subscription_profile_ready(
                profile_id,
                worker_url=str(config.container_worker_base_url or "").strip(),
            )
            self._wake()
        return result

    def delete_profile(
        self,
        directory_path: str,
        profile_id: str,
        payload: dict[str, Any],
        *,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        profile_id = str(normalize_subscription_profile_id(profile_id, allow_none=False))
        expected_worker = payload.get("expected_worker_url")
        if not isinstance(expected_worker, str) or not expected_worker.strip():
            raise ValueError("expected_worker_url is required")
        config = self._deps.provider_config.resolve_for_directory(directory_path)
        worker_url = self._normalize_worker_url(config.container_worker_base_url)
        if worker_url != self._normalize_worker_url(expected_worker):
            raise CodexSubscriptionProfileConflict("Worker changed; refresh subscriptions before deleting")
        if self._deps.runtime_dir is None:
            raise CodexSubscriptionProfileConflict("Runtime directory is unavailable")
        with subscription_profile_binding_lock(self._deps.runtime_dir, worker_url, profile_id):
            return self._delete_unused_profile(
                directory_path, profile_id, config=config, actor_user_id=actor_user_id
            )

    def _delete_unused_profile(
        self, directory_path: str, profile_id: str, *, config: GatewayConfig,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        worker_url = self._normalize_worker_url(config.container_worker_base_url)
        repository = self._deps.session_repository
        if repository is None:
            raise CodexSubscriptionProfileConflict("Session inventory is unavailable")
        now = datetime.now(UTC)
        for session in repository.read_all().values():
            if session.expires_at <= now:
                continue
            session_config = self._deps.provider_config.resolve_for_session_id(session.session_id)
            selected = effective_subscription_profile_id(session=session)
            if selected is None and effective_codex_auth_mode(
                session=session, config=session_config
            ) is CodexAuthMode.CHATGPT_SUBSCRIPTION:
                selected = "default"
            if selected == profile_id and self._normalize_worker_url(
                session_config.container_worker_base_url
            ) == worker_url:
                raise CodexSubscriptionProfileConflict(
                    "Subscription profile is assigned to an unexpired session; "
                    "reassign or remove its sessions before deleting"
                )
        if self._deps.runtime_dir is not None:
            from evander.messaging.agent_prompt_queue import AgentPromptQueueStore

            records = AgentPromptQueueStore(
                self._deps.runtime_dir / "queued-agent-prompts.json"
            ).read_all()
            for record in records.values():
                selected = record.subscription_profile_id
                if selected is None and str(record.queue_reason or "").startswith("subscription_"):
                    selected = "default"
                if selected != profile_id:
                    continue
                record_worker = self._worker_url_for_record(record)
                if record_worker in ("", worker_url):
                    raise CodexSubscriptionProfileConflict(
                        "Subscription profile has queued prompts; drain or cancel them before deleting"
                    )
        try:
            return self._mutate(
                "codex_subscription_profile_delete", directory_path, actor_user_id,
                lambda: self._client_for_config(config).delete_codex_auth_profile(profile_id),
            )
        except ContainerWorkerClientError as exc:
            if exc.status_code == 409:
                raise CodexSubscriptionProfileConflict("Subscription profile has active jobs") from exc
            raise

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

    def consume_profile_reset_credit(
        self,
        directory_path: str,
        profile_id: str,
        payload: dict[str, Any],
        *,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        request = CodexSubscriptionResetRequest.model_validate(payload)
        config = self._deps.provider_config.resolve_for_directory(directory_path)
        worker_url = str(config.container_worker_base_url or "").strip()
        if self._normalize_worker_url(request.expected_worker_url) != self._normalize_worker_url(
            worker_url
        ):
            self._audit(
                "codex_subscription_reset_credit_consume", directory_path, actor_user_id, "deny"
            )
            raise CodexSubscriptionResetConflict(
                "Subscription worker changed. Refresh profiles before redeeming a reset."
            )
        result = self._mutate(
            "codex_subscription_reset_credit_consume",
            directory_path,
            actor_user_id,
            lambda: self._client_for_config(config).consume_codex_profile_reset_credit(
                profile_id, request.model_dump(exclude={"expected_worker_url"}, exclude_none=True)
            ),
        )
        profile = result.get("profile")
        if isinstance(profile, dict):
            profile["queued_prompt_count"] = self._queued_counts_by_profile(
                worker_url=worker_url
            ).get(profile_id, 0)
            if result.get("outcome") in {"reset", "alreadyRedeemed"}:
                if self._profile_is_ready(profile):
                    self._make_subscription_profile_ready(profile_id, worker_url=worker_url)
                self._wake()
        return result

    def profile_device_login_status(
        self, directory_path: str, profile_id: str, login_id: str
    ) -> dict[str, Any]:
        config = self._deps.provider_config.resolve_for_directory(directory_path)
        result = self._client_for_config(config).codex_profile_device_login_status(
            profile_id, login_id
        )
        if result.get("status") == "completed":
            self._make_subscription_profile_ready(
                profile_id,
                worker_url=str(config.container_worker_base_url or "").strip(),
            )
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
        return self._client_for_config(config)

    def _client_for_config(self, config: GatewayConfig) -> ContainerWorkerClient:
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

    def _queued_counts_by_profile(self, *, worker_url: str) -> dict[str, int]:
        runtime_dir = self._deps.runtime_dir
        normalized_worker_url = self._normalize_worker_url(worker_url)
        if runtime_dir is None or not normalized_worker_url:
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
            if self._worker_url_for_record(record) != normalized_worker_url:
                continue
            profile_id = str(getattr(record, "subscription_profile_id", "") or "").strip()
            if profile_id:
                counts[profile_id] = counts.get(profile_id, 0) + 1
        return counts

    def _make_subscription_profile_ready(self, profile_id: str, *, worker_url: str) -> None:
        runtime_dir = self._deps.runtime_dir
        normalized = str(profile_id or "").strip()
        normalized_worker_url = self._normalize_worker_url(worker_url)
        if runtime_dir is None or not normalized or not normalized_worker_url:
            return
        try:
            from evander.messaging.agent_prompt_queue import AgentPromptQueueStore

            AgentPromptQueueStore(
                Path(runtime_dir) / "queued-agent-prompts.json"
            ).make_subscription_profile_ready(
                normalized,
                record_filter=lambda record: (
                    self._worker_url_for_record(record) == normalized_worker_url
                ),
            )
        except Exception:
            pass

    def _worker_url_for_record(self, record: Any) -> str:
        session_id = str(getattr(record, "session_id", "") or "").strip()
        if not session_id:
            return ""
        try:
            config = self._deps.provider_config.resolve_for_session_id(session_id)
        except Exception:
            return ""
        return self._normalize_worker_url(config.container_worker_base_url)

    @staticmethod
    def _normalize_worker_url(value: object) -> str:
        return str(value or "").strip().rstrip("/")

    @staticmethod
    def _profile_is_ready(profile: dict[str, Any]) -> bool:
        if not (
            bool(profile.get("enabled"))
            and bool(profile.get("available"))
            and bool(profile.get("signed_in"))
        ):
            return False
        rate_limited, _retry_at = subscription_rate_limit_state(profile.get("rate_limits"))
        return not rate_limited

    def _wake(self) -> None:
        wake = self._deps.wake_reconciliation
        if wake is None:
            return
        try:
            wake()
        except Exception:
            pass


def _quota_window(
    value: object, duration_minutes: int, observed_at: datetime
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    duration = value.get("window_duration_minutes")
    if type(duration) is not int or duration != duration_minutes:
        return None
    used = value.get("used_percent")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    try:
        used = float(used)
    except OverflowError:
        return None
    if not math.isfinite(used) or used < 0:
        return None
    reset = value.get("resets_at")
    if not isinstance(reset, str):
        return None
    try:
        resets_at = datetime.fromisoformat(reset.replace("Z", "+00:00"))
    except ValueError:
        return None
    if resets_at.tzinfo is None or resets_at <= observed_at:
        return None
    return {
        "used_percent": used,
        "remaining_percent": max(0.0, min(100.0, 100.0 - used)),
        "resets_at": resets_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
