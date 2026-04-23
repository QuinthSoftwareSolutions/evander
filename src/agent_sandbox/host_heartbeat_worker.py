from __future__ import annotations

import fcntl
import json
import logging
import os
import random
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .agent_control import AgentControlStore
from .admin_service import resolve_agent_model_for_manifest, resolve_effective_issue_setup_config
from .audit import AuditEventType
from .config import GatewayConfig, HostHeartbeatWorkerGlobalConfig
from .provider_config import ProviderConfigResolver
from .pr_feedback_worker import (
    AzureDevOpsFeedbackAdapter,
    FeedbackProviderAdapter,
    FeedbackProviderAuthError,
    FeedbackProviderConfigurationError,
    GitHubFeedbackAdapter,
    PullRequestFeedbackWorker,
    build_pr_feedback_worker,
)
from .runtime import RuntimeContext, build_runtime
from .wake_store import WakeRequestRepository
from .worker_directory_context import WorkerDirectoryContextResolver, log_shadow_context
from .models import DirectoryPath, WakeDispatchRecord, WakeResumePromptPayload, WakeScheduledRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedbackStream:
    provider: str
    repo: str
    pr_id: str


_HOST_HEARTBEAT_DISABLED_FEEDBACK_PROVIDERS = frozenset({"azure_devops"})


@dataclass(frozen=True)
class StreamSessionBinding:
    session_id: str
    vm_id: str | None
    source: str
    directory_path: str | None = None
    directory_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class HeartbeatCycleResult:
    discovered_feedback: int
    streams_polled: int
    next_sleep_seconds: float


class HostHeartbeatWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: HostHeartbeatWorkerGlobalConfig | None = None,
        runtime: RuntimeContext,
        feedback_worker: PullRequestFeedbackWorker,
        adapters: dict[str, FeedbackProviderAdapter],
        agent_control_store: AgentControlStore | None = None,
        provider_config_resolver: ProviderConfigResolver | None = None,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.feedback_worker = feedback_worker
        self.adapters = adapters
        self.agent_control_store = agent_control_store
        self.provider_config_resolver = provider_config_resolver
        if getattr(config, "enable_directory_scoped_config", False):
            try:
                self.directory_context_resolver = WorkerDirectoryContextResolver(config)
            except Exception:
                logger.debug("Directory shadow resolver unavailable for host heartbeat worker", exc_info=True)
                self.directory_context_resolver = None
        else:
            self.directory_context_resolver = None

    def _log_audit_decision(self, **kwargs: Any) -> None:
        audit = getattr(self.runtime, "audit", None)
        if audit is None:
            return
        log_decision = getattr(audit, "log_decision", None)
        if log_decision is None:
            return
        log_decision(**kwargs)

    def _session_repository(self) -> Any | None:
        return getattr(self.runtime, "session_repository", None)

    def _sessions_by_id(self) -> dict[str, Any]:
        repository = self._session_repository()
        if repository is not None:
            return {session.session_id: session for session in repository.list_sessions()}
        session_store = getattr(self.runtime, "sessions", None)
        if session_store is None:
            return {}
        read_all = getattr(session_store, "read_all", None)
        if callable(read_all):
            return dict(read_all())
        return {}

    def _get_session(self, session_id: str) -> Any | None:
        repository = self._session_repository()
        if repository is not None:
            return repository.get_session(session_id)
        return self._sessions_by_id().get(session_id)

    def _root_worker_config(self) -> GatewayConfig:
        return self._resolve_worker_config_for_directory("root", source="host_heartbeat:root")

    def _configured_directory_paths(self, worker_config: GatewayConfig | HostHeartbeatWorkerGlobalConfig) -> list[str]:
        raw_paths = getattr(worker_config, "host_heartbeat_directory_paths", None)
        if raw_paths is None:
            return ["root"]
        normalized_paths: list[str] = []
        for raw_path in raw_paths:
            text = str(raw_path).strip()
            if not text:
                continue
            try:
                normalized_paths.append(DirectoryPath(segments=text).as_posix())
            except Exception:
                logger.warning(
                    "Skipping invalid host heartbeat directory path path=%r",
                    raw_path,
                    exc_info=True,
                )
        return normalized_paths

    def _resolve_worker_config_for_binding(self, binding: StreamSessionBinding) -> GatewayConfig:
        return self._resolve_worker_config_for_directory(
            binding.directory_path,
            session_id=binding.session_id,
            source=f"host_heartbeat:{binding.source}",
        )

    def _resolve_worker_config_for_directory(
        self,
        directory_path: str | None,
        *,
        session_id: str | None = None,
        source: str,
    ) -> GatewayConfig:
        if self.directory_context_resolver is None:
            return self.config
        try:
            resolution = self.directory_context_resolver.resolve_effective_domain_for_session(
                session_id,
                domain="heartbeat",
                fallback_directory_path=directory_path,
            )
        except Exception:
            logger.warning(
                "Failed to resolve directory-scoped heartbeat config source=%s session_id=%s directory_path=%s",
                source,
                session_id,
                directory_path or "root",
                exc_info=True,
            )
            return self.config
        if resolution.validation_errors:
            logger.warning(
                "Invalid directory-scoped heartbeat config source=%s session_id=%s directory_path=%s errors=%s",
                source,
                session_id,
                resolution.selected_directory.path,
                resolution.validation_errors,
            )
            return self.config
        payload = self.config.model_dump(mode="json")
        payload.update(resolution.effective_config)
        return GatewayConfig.model_validate(payload)

    def _resolve_provider_adapter(
        self,
        provider: str,
        binding: StreamSessionBinding,
    ) -> FeedbackProviderAdapter | None:
        if self.provider_config_resolver is None:
            return self.adapters.get(provider)
        try:
            resolved_config = self.provider_config_resolver.resolve_for_session_id(binding.session_id)
        except ValueError:
            resolved_config = self.provider_config_resolver.resolve_for_directory(binding.directory_path)
        if provider == "github":
            github_token = os.environ.get(resolved_config.github_token_env, "").strip()
            return GitHubFeedbackAdapter(token=github_token)
        if provider == "azure_devops":
            azure_pat = os.environ.get(resolved_config.azure_devops_pr_pat_env, "").strip()
            return AzureDevOpsFeedbackAdapter(
                organization_url=resolved_config.azure_devops_organization_url,
                project=resolved_config.azure_devops_project,
                pat=azure_pat,
                pat_env_name=resolved_config.azure_devops_pr_pat_env,
            )
        return self.adapters.get(provider)

    @staticmethod
    def _group_bindings_by_directory(
        bindings: list[StreamSessionBinding],
    ) -> list[list[StreamSessionBinding]]:
        grouped: dict[str, list[StreamSessionBinding]] = {}
        for binding in bindings:
            key = binding.directory_path or "root"
            grouped.setdefault(key, []).append(binding)
        return [grouped[key] for key in sorted(grouped)]

    def run_forever(self) -> None:
        worker_config = self._root_worker_config()
        with _single_instance_lock(worker_config.host_heartbeat_lock_path):
            logger.info(
                "Host heartbeat worker started poll_interval=%.3f jitter=%.3f cooldown=%.3f lock=%s",
                worker_config.host_heartbeat_poll_interval_seconds,
                worker_config.host_heartbeat_jitter_seconds,
                worker_config.host_heartbeat_cooldown_seconds,
                worker_config.host_heartbeat_lock_path,
            )
            while True:
                result = self.run_once()
                logger.debug(
                    "Host heartbeat cycle complete streams=%d discovered=%d sleep_seconds=%.3f",
                    result.streams_polled,
                    result.discovered_feedback,
                    result.next_sleep_seconds,
                )
                time.sleep(result.next_sleep_seconds)

    def run_once_with_lock(self) -> HeartbeatCycleResult:
        with _single_instance_lock(self._root_worker_config().host_heartbeat_lock_path):
            return self.run_once()

    def run_once(self) -> HeartbeatCycleResult:
        worker_config = self._root_worker_config()
        self._dispatch_scheduled_wakes(now_utc=datetime.now(UTC), worker_config=worker_config)
        configured_directory_paths = self._configured_directory_paths(self.worker_config or self.config)
        discovered_feedback = 0
        streams_polled = 0
        if configured_directory_paths:
            with httpx.Client(timeout=worker_config.host_heartbeat_http_timeout_seconds) as client:
                for configured_directory_path in configured_directory_paths:
                    stream_mappings = self._collect_stream_mappings(directory_path=configured_directory_path)
                    streams = sorted(
                        stream_mappings.keys(),
                        key=lambda stream: (stream.provider, stream.repo, stream.pr_id),
                    )
                    for stream in streams:
                        bindings = stream_mappings.get(stream, [])
                        if len(bindings) > 1:
                            logger.warning(
                                "PR feedback stream matched multiple sessions policy=%s provider=%s repo=%s pr=%s session_ids=%s",
                                worker_config.host_heartbeat_stream_conflict_policy,
                                stream.provider,
                                stream.repo,
                                stream.pr_id,
                                [binding.session_id for binding in bindings],
                            )
                            if worker_config.host_heartbeat_stream_conflict_policy == "reject":
                                continue
                        discovered_for_stream = 0
                        for binding_group in self._group_bindings_by_directory(bindings):
                            adapter = self._resolve_provider_adapter(stream.provider, binding_group[0])
                            if adapter is None:
                                logger.warning(
                                    "Skipping unsupported feedback provider=%s repo=%s pr=%s directory_path=%s",
                                    stream.provider,
                                    stream.repo,
                                    stream.pr_id,
                                    binding_group[0].directory_path or "root",
                                )
                                continue
                            streams_polled += 1
                            try:
                                discovered = self.feedback_worker.poll_once(
                                    client=client,
                                    provider=stream.provider,
                                    repo=stream.repo,
                                    pr_id=stream.pr_id,
                                    stream_bindings=[
                                        {
                                            "session_id": binding.session_id,
                                            "vm_id": binding.vm_id,
                                            "source": binding.source,
                                            "directory_path": binding.directory_path,
                                            "directory_context": binding.directory_context,
                                        }
                                        for binding in binding_group
                                    ],
                                    adapter=adapter,
                                )
                            except (FeedbackProviderConfigurationError, FeedbackProviderAuthError) as exc:
                                logger.warning(
                                    "Host heartbeat stream poll skipped provider=%s repo=%s pr=%s directory_path=%s reason=%s",
                                    stream.provider,
                                    stream.repo,
                                    stream.pr_id,
                                    binding_group[0].directory_path or "root",
                                    exc,
                                )
                                continue
                            except Exception:
                                logger.warning(
                                    "Host heartbeat stream poll failed provider=%s repo=%s pr=%s directory_path=%s",
                                    stream.provider,
                                    stream.repo,
                                    stream.pr_id,
                                    binding_group[0].directory_path or "root",
                                    exc_info=True,
                                )
                                continue
                            discovered_for_stream += len(discovered)
                        discovered_feedback += discovered_for_stream
                        if discovered_for_stream or self._stream_has_unread_bindings(stream=stream, bindings=bindings):
                            self._record_busy_skips(stream=stream, bindings=bindings)
                    discord_bindings = self._collect_discord_channel_mappings(directory_path=configured_directory_path)
                    for channel_id, bindings in sorted(discord_bindings.items()):
                        streams_polled += 1
                        if self._discord_channel_has_unread_bindings(channel_id=channel_id, bindings=bindings):
                            discovered_feedback += 1
                            self._record_discord_busy_skips(channel_id=channel_id, bindings=bindings)

        next_sleep_seconds = _compute_next_sleep_seconds(
            poll_interval_seconds=worker_config.host_heartbeat_poll_interval_seconds,
            jitter_seconds=worker_config.host_heartbeat_jitter_seconds,
            cooldown_seconds=worker_config.host_heartbeat_cooldown_seconds,
            discovered_feedback=discovered_feedback,
        )
        self._log_audit_decision(
            event_type=AuditEventType.HOST_HEARTBEAT_CYCLE_COMPLETED,
            decision="completed",
            reason="cycle-finished",
            result={
                "streams_polled": streams_polled,
                "discovered_feedback": discovered_feedback,
                "next_sleep_seconds": next_sleep_seconds,
            },
        )
        return HeartbeatCycleResult(
            discovered_feedback=discovered_feedback,
            streams_polled=streams_polled,
            next_sleep_seconds=next_sleep_seconds,
        )

    def _dispatch_scheduled_wakes(self, *, now_utc: datetime, worker_config: GatewayConfig) -> None:
        wake_store = getattr(self.runtime, "wake_store", None)
        if wake_store is None:
            return
        sessions_by_id = self._sessions_by_id()
        for request in wake_store.find_due_requests(now_utc=now_utc):
            session = sessions_by_id.get(request.session_id)
            source = "scheduled_cron" if (request.schedule.cron or "").strip() else "scheduled_once"
            if session is None or session.expires_at <= now_utc:
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=request.session_id,
                    decision="skipped",
                    reason="session-invalid",
                    args={"source": source, "request_id": request.request_id},
                )
                continue
            binding = StreamSessionBinding(
                session_id=session.session_id,
                vm_id=session.vm_id,
                source=source,
                directory_path=session.directory_path,
            )
            scoped_config = self._resolve_worker_config_for_binding(binding)
            if self._binding_is_host_disabled(binding):
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    decision="skipped",
                    reason="agent-disabled",
                    args={"source": source, "request_id": request.request_id},
                )
                continue
            if _is_session_in_cooldown(
                path=scoped_config.host_heartbeat_session_cooldown_path,
                session_id=binding.session_id,
            ):
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    decision="skipped",
                    reason="session-cooldown",
                    args={"source": source, "request_id": request.request_id},
                )
                continue
            if not binding.vm_id:
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    decision="skipped",
                    reason="vm-unbound",
                    args={"source": source, "request_id": request.request_id},
                )
                continue
            agent_state = _probe_agent_dispatch_state(
                session_id=binding.session_id,
                vm_id=binding.vm_id,
                script_path=scoped_config.host_busy_check_script_path,
            )
            if agent_state == "busy":
                _set_session_cooldown(
                    path=scoped_config.host_heartbeat_session_cooldown_path,
                    session_id=binding.session_id,
                    cooldown_seconds=scoped_config.host_heartbeat_cooldown_seconds,
                )
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    decision="skipped",
                    reason="agent-busy",
                    args={"source": source, "request_id": request.request_id},
                )
                continue
            if agent_state == "unavailable":
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    decision="skipped",
                    reason="agent-unavailable",
                    args={"source": source, "request_id": request.request_id},
                )
                continue
            dispatch = self._resume_binding(
                binding,
                worker_config=scoped_config,
                override_prompt=self._build_scheduled_resume_prompt(
                    worker_config=scoped_config,
                    requested_prompt=request.resume_prompt.prompt,
                ),
                resume_mode=request.resume_mode,
                dispatch_source=source,
                request_id=request.request_id,
                scheduled_for=request.next_run_at or request.schedule.resume_at,
                wake_metadata={"schedule_cron": request.schedule.cron},
            )
            _set_session_cooldown(
                path=scoped_config.host_heartbeat_session_cooldown_path,
                session_id=binding.session_id,
                cooldown_seconds=scoped_config.host_heartbeat_cooldown_seconds,
            )
            if dispatch is not None:
                self._log_audit_decision(
                    event_type=AuditEventType.RESUME_SUCCEEDED if dispatch.status == "dispatched" else AuditEventType.RESUME_FAILED,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    decision="completed" if dispatch.status == "dispatched" else "failed",
                    reason=dispatch.status,
                    args={"source": source, "request_id": request.request_id},
                    result={"status": dispatch.status},
                )
            if (request.schedule.cron or "").strip():
                _advance_cron_wake_request(
                    wake_store=wake_store,
                    request=request,
                    now_utc=now_utc,
                    mark_consumed=False,
                    was_dispatched=bool(dispatch and dispatch.status == "dispatched"),
                )
            elif dispatch is not None and dispatch.status == "dispatched":
                _advance_cron_wake_request(
                    wake_store=wake_store,
                    request=request,
                    now_utc=now_utc,
                    mark_consumed=True,
                    was_dispatched=True,
                )

    def _record_busy_skips(self, *, stream: FeedbackStream, bindings: list[StreamSessionBinding]) -> None:
        for binding in bindings:
            worker_config = self._resolve_worker_config_for_binding(binding)
            if not self._binding_has_unread_feedback(binding=binding, stream=stream):
                continue
            if self._binding_is_host_disabled(binding):
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    provider=stream.provider,
                    repo=stream.repo,
                    pr_id=stream.pr_id,
                    decision="skipped",
                    reason="agent-disabled",
                    args={"source": binding.source},
                )
                logger.info(
                    "Host heartbeat skip reason=agent-disabled session_id=%s vm_id=%s",
                    binding.session_id,
                    binding.vm_id,
                )
                continue
            if _is_session_in_cooldown(
                path=worker_config.host_heartbeat_session_cooldown_path,
                session_id=binding.session_id,
            ):
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    provider=stream.provider,
                    repo=stream.repo,
                    pr_id=stream.pr_id,
                    decision="skipped",
                    reason="session-cooldown",
                    args={"source": binding.source},
                )
                logger.info(
                    "Host heartbeat skip reason=session-cooldown session_id=%s vm_id=%s",
                    binding.session_id,
                    binding.vm_id,
                )
                continue
            if not binding.vm_id:
                continue
            agent_state = _probe_agent_dispatch_state(
                session_id=binding.session_id,
                vm_id=binding.vm_id,
                script_path=worker_config.host_busy_check_script_path,
            )
            if agent_state == "busy":
                _set_session_cooldown(
                    path=worker_config.host_heartbeat_session_cooldown_path,
                    session_id=binding.session_id,
                    cooldown_seconds=worker_config.host_heartbeat_cooldown_seconds,
                )
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    provider=stream.provider,
                    repo=stream.repo,
                    pr_id=stream.pr_id,
                    decision="skipped",
                    reason="agent-busy",
                    args={"source": binding.source},
                )
                logger.info(
                    "Host heartbeat skip reason=agent-busy session_id=%s vm_id=%s",
                    binding.session_id,
                    binding.vm_id,
                )
                continue
            if agent_state == "unavailable":
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    provider=stream.provider,
                    repo=stream.repo,
                    pr_id=stream.pr_id,
                    decision="skipped",
                    reason="agent-unavailable",
                    args={"source": binding.source},
                )
                logger.info(
                    "Host heartbeat skip reason=agent-unavailable session_id=%s vm_id=%s",
                    binding.session_id,
                    binding.vm_id,
                )
                continue
            self._resume_binding(binding, worker_config=worker_config)
            _set_session_cooldown(
                path=worker_config.host_heartbeat_session_cooldown_path,
                session_id=binding.session_id,
                cooldown_seconds=worker_config.host_heartbeat_cooldown_seconds,
            )

    def _binding_is_host_disabled(self, binding: StreamSessionBinding) -> bool:
        if self.agent_control_store is None or not binding.vm_id:
            return False
        return self.agent_control_store.is_disabled(binding.vm_id)

    def _stream_has_unread_bindings(self, *, stream: FeedbackStream, bindings: list[StreamSessionBinding]) -> bool:
        for binding in bindings:
            if self._binding_has_unread_feedback(binding=binding, stream=stream):
                return True
        return False

    def _discord_channel_has_unread_bindings(
        self,
        *,
        channel_id: str,
        bindings: list[StreamSessionBinding],
    ) -> bool:
        for binding in bindings:
            if self._binding_has_unread_discord_messages(binding=binding, channel_id=channel_id):
                return True
        return False

    def _binding_has_unread_feedback(self, *, binding: StreamSessionBinding, stream: FeedbackStream) -> bool:
        pr_feedback_service = getattr(self.runtime, "pr_feedback_service", None)
        if pr_feedback_service is not None:
            has_pending_feedback = getattr(pr_feedback_service, "has_pending_feedback", None)
            if has_pending_feedback is not None:
                response = has_pending_feedback(
                    session_id=binding.session_id,
                    streams=[{"provider": stream.provider, "repo": stream.repo, "pr_id": stream.pr_id}],
                )
                pending = getattr(response, "pending", None)
                if pending is not None:
                    return bool(pending)
                if isinstance(response, dict):
                    return bool(response.get("pending"))
        store = getattr(self.feedback_worker, "store", None)
        if store is None:
            return False
        has_unread_feedback = getattr(store, "has_unread_feedback", None)
        if has_unread_feedback is None:
            return False
        return bool(
            has_unread_feedback(
                session_id=binding.session_id,
                streams=[{"provider": stream.provider, "repo": stream.repo, "pr_id": stream.pr_id}],
            )
        )

    def _binding_has_unread_discord_messages(self, *, binding: StreamSessionBinding, channel_id: str) -> bool:
        discord_inbox_service = getattr(self.runtime, "discord_inbox_service", None)
        if discord_inbox_service is None:
            return False
        response = discord_inbox_service.has_pending_discord_messages(
            session_id=binding.session_id,
            streams=[{"channel_id": channel_id}],
        )
        pending = getattr(response, "pending", None)
        if pending is not None:
            return bool(pending)
        if isinstance(response, dict):
            return bool(response.get("pending"))
        return False

    def _record_discord_busy_skips(self, *, channel_id: str, bindings: list[StreamSessionBinding]) -> None:
        for binding in bindings:
            worker_config = self._resolve_worker_config_for_binding(binding)
            if not self._binding_has_unread_discord_messages(binding=binding, channel_id=channel_id):
                continue
            if self._binding_is_host_disabled(binding):
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    decision="skipped",
                    reason="agent-disabled",
                    args={"source": binding.source, "channel_id": channel_id},
                )
                continue
            if _is_session_in_cooldown(
                path=worker_config.host_heartbeat_session_cooldown_path,
                session_id=binding.session_id,
            ):
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    decision="skipped",
                    reason="session-cooldown",
                    args={"source": binding.source, "channel_id": channel_id},
                )
                continue
            if not binding.vm_id:
                continue
            agent_state = _probe_agent_dispatch_state(
                session_id=binding.session_id,
                vm_id=binding.vm_id,
                script_path=worker_config.host_busy_check_script_path,
            )
            if agent_state == "busy":
                _set_session_cooldown(
                    path=worker_config.host_heartbeat_session_cooldown_path,
                    session_id=binding.session_id,
                    cooldown_seconds=worker_config.host_heartbeat_cooldown_seconds,
                )
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    decision="skipped",
                    reason="agent-busy",
                    args={"source": binding.source, "channel_id": channel_id},
                )
                continue
            if agent_state == "unavailable":
                self._log_audit_decision(
                    event_type=AuditEventType.BUSY_SKIP,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    decision="skipped",
                    reason="agent-unavailable",
                    args={"source": binding.source, "channel_id": channel_id},
                )
                continue
            self._resume_binding(binding, worker_config=worker_config)
            _set_session_cooldown(
                path=worker_config.host_heartbeat_session_cooldown_path,
                session_id=binding.session_id,
                cooldown_seconds=worker_config.host_heartbeat_cooldown_seconds,
            )

    def _build_resume_prompt(self, binding: StreamSessionBinding, *, worker_config: GatewayConfig) -> str:
        prompt_parts: list[str] = []
        unread_pr_streams = [
            stream
            for stream in self._collect_pr_streams_for_session(binding)
            if self._binding_has_unread_feedback(binding=binding, stream=stream)
        ]
        if unread_pr_streams:
            prompt_parts.append(self._pr_resume_prompt(worker_config))

        unread_discord_channel_ids = [
            channel_id
            for channel_id in self._collect_discord_channels_for_session(binding.session_id)
            if self._binding_has_unread_discord_messages(binding=binding, channel_id=channel_id)
        ]
        if unread_discord_channel_ids:
            prompt_parts.append(self._discord_resume_prompt(worker_config))

        if prompt_parts:
            return " ".join(prompt_parts)
        return self._pr_resume_prompt(worker_config)

    @staticmethod
    def _pr_resume_prompt(worker_config: GatewayConfig) -> str:
        return str(getattr(worker_config, "host_heartbeat_pr_resume_prompt", "")).strip()

    @staticmethod
    def _discord_resume_prompt(worker_config: GatewayConfig) -> str:
        return str(
            getattr(
                worker_config,
                "host_heartbeat_discord_resume_prompt",
                "You have unread Discord messages. Please fetch them with "
                "list_unread_discord_messages and respond with discord_post_status if needed.",
            )
        ).strip()

    def _build_scheduled_resume_prompt(self, *, worker_config: GatewayConfig, requested_prompt: str) -> str:
        preamble = str(
            getattr(
                worker_config,
                "host_heartbeat_scheduled_wake_prefix_text",
                self._pr_resume_prompt(worker_config),
            )
        ).strip()
        cleaned_requested = requested_prompt.strip()
        if preamble and cleaned_requested:
            return f"{preamble} {cleaned_requested}"
        if preamble:
            return preamble
        return cleaned_requested

    def _resume_binding(
        self,
        binding: StreamSessionBinding,
        *,
        worker_config: GatewayConfig,
        override_prompt: str | None = None,
        resume_mode: str = "same_context",
        dispatch_source: str = "host_heartbeat_worker",
        request_id: str | None = None,
        scheduled_for: datetime | None = None,
        wake_metadata: dict[str, Any] | None = None,
    ) -> WakeDispatchRecord | None:
        if not binding.vm_id:
            return
        wake_store = getattr(self.runtime, "wake_store", None)
        resume_prompt = override_prompt or self._build_resume_prompt(binding, worker_config=worker_config)
        script_path = worker_config.host_resume_script_path
        if not script_path.exists():
            logger.warning(
                "Resume script missing path=%s session_id=%s vm_id=%s",
                script_path,
                binding.session_id,
                binding.vm_id,
            )
            return None
        cmd = [
            str(script_path),
            "--prompt",
            resume_prompt,
            "--vm-name",
            binding.vm_id,
            "--session-id",
            binding.session_id,
            "--vm-user",
            worker_config.host_heartbeat_vm_user,
            "--resume-mode",
            resume_mode,
            "--worker-mode",
            "--host-state-file",
            str(worker_config.host_heartbeat_wake_state_path),
        ]
        host_control_state_path = getattr(self.config, "host_agent_control_state_path", None)
        if host_control_state_path is not None:
            cmd.extend(["--host-control-state-path", str(host_control_state_path)])
        run_kwargs: dict[str, Any] = {
            "check": False,
            "capture_output": True,
            "text": True,
            "timeout": worker_config.host_heartbeat_resume_timeout_seconds,
        }
        agent_model = self._resolve_agent_model_for_binding(binding)
        if agent_model:
            run_kwargs["env"] = os.environ | {"AGENT_MODEL": agent_model}
        try:
            completed = subprocess.run(cmd, **run_kwargs)
        except OSError:
            logger.warning(
                "Resume script invocation failed path=%s session_id=%s vm_id=%s",
                script_path,
                binding.session_id,
                binding.vm_id,
                exc_info=True,
            )
            dispatch_record = WakeDispatchRecord(
                dispatch_id=f"hb-{int(time.time() * 1_000_000)}-{binding.session_id}",
                request_id=request_id,
                session_id=binding.session_id,
                status="failed",
                resume_prompt=WakeResumePromptPayload(prompt=resume_prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                metadata={"vm_id": binding.vm_id, "source": binding.source, **(wake_metadata or {})},
                error="resume script invocation failed",
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record
        except subprocess.TimeoutExpired:
            logger.warning(
                "Resume script timed out path=%s session_id=%s vm_id=%s timeout_seconds=%.3f",
                script_path,
                binding.session_id,
                binding.vm_id,
                worker_config.host_heartbeat_resume_timeout_seconds,
            )
            dispatch_record = WakeDispatchRecord(
                dispatch_id=f"hb-{int(time.time() * 1_000_000)}-{binding.session_id}",
                request_id=request_id,
                session_id=binding.session_id,
                status="timed_out",
                resume_prompt=WakeResumePromptPayload(prompt=resume_prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                metadata={
                    "vm_id": binding.vm_id,
                    "source": binding.source,
                    "timeout_seconds": worker_config.host_heartbeat_resume_timeout_seconds,
                    **(wake_metadata or {}),
                },
                error="resume script timed out",
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record

        if completed.returncode == 0:
            logger.info(
                "Host heartbeat wake dispatched session_id=%s vm_id=%s",
                binding.session_id,
                binding.vm_id,
            )
            dispatch_record = WakeDispatchRecord(
                dispatch_id=f"hb-{int(time.time() * 1_000_000)}-{binding.session_id}",
                request_id=request_id,
                session_id=binding.session_id,
                status="dispatched",
                resume_prompt=WakeResumePromptPayload(prompt=resume_prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                metadata={"vm_id": binding.vm_id, "source": binding.source, **(wake_metadata or {})},
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record
        logger.warning(
            "Host heartbeat wake failed session_id=%s vm_id=%s exit_code=%s stdout=%s stderr=%s",
            binding.session_id,
            binding.vm_id,
            completed.returncode,
            (completed.stdout or "").strip(),
            (completed.stderr or "").strip(),
        )
        dispatch_record = WakeDispatchRecord(
            dispatch_id=f"hb-{int(time.time() * 1_000_000)}-{binding.session_id}",
            request_id=request_id,
            session_id=binding.session_id,
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt=resume_prompt),
            resume_mode=resume_mode,
            scheduled_for=scheduled_for,
            source=dispatch_source,
            metadata={
                "vm_id": binding.vm_id,
                "source": binding.source,
                "exit_code": completed.returncode,
                "stdout": (completed.stdout or "").strip(),
                "stderr": (completed.stderr or "").strip(),
                **(wake_metadata or {}),
            },
            error=(completed.stderr or "").strip() or f"exit_code={completed.returncode}",
        )
        if wake_store is not None:
            wake_store.record_dispatch(dispatch_record)
        return dispatch_record

    def _resolve_agent_model_for_binding(self, binding: StreamSessionBinding) -> str | None:
        session = self._get_session(binding.session_id)
        if session is None:
            return os.environ.get("AGENT_MODEL") or None
        if not hasattr(self.runtime, "directories"):
            session_model = str(getattr(session, "model", "") or "").strip()
            if session_model:
                return session_model
            return os.environ.get("AGENT_MODEL") or None
        try:
            issue_setup = resolve_effective_issue_setup_config(
                config=self.config,
                runtime=self.runtime,
                directory_path=session.directory_path,
                root_dir=Path(__file__).resolve().parents[2],
            )
            return resolve_agent_model_for_manifest(session, issue_setup=issue_setup)
        except ValueError as exc:
            logger.warning(
                "Cannot resolve AGENT_MODEL for heartbeat wake session_id=%s vm_id=%s error=%s",
                binding.session_id,
                binding.vm_id,
                exc,
            )
            session_model = str(getattr(session, "model", "") or "").strip()
            if session_model:
                return session_model
            return os.environ.get("AGENT_MODEL") or None

    def _collect_stream_mappings(
        self,
        *,
        directory_path: str | None = None,
    ) -> dict[FeedbackStream, list[StreamSessionBinding]]:
        now = datetime.now(timezone.utc)
        sessions_by_id = self._sessions_by_id()
        stream_index: dict[tuple[str, str, str], list[StreamSessionBinding]] = {}
        scope_directory_path = DirectoryPath(segments=directory_path or "root").as_posix()
        scope_is_root = scope_directory_path == "root"

        for session in sessions_by_id.values():
            if session.expires_at <= now:
                continue
            if not scope_is_root and not _directory_is_within_scope(session.directory_path, scope_directory_path):
                continue
            raw_streams = session.metadata.get("pr_feedback_streams")
            if not isinstance(raw_streams, list):
                continue
            for raw_stream in raw_streams:
                parsed = _parse_stream(raw_stream)
                if parsed is None:
                    logger.warning(
                        "Skipping invalid pr_feedback_streams entry session_id=%s value=%r",
                        session.session_id,
                        raw_stream,
                    )
                    continue
                if not _host_heartbeat_manages_feedback_provider(parsed.provider):
                    continue
                key = (parsed.provider, parsed.repo, parsed.pr_id)
                binding = StreamSessionBinding(
                    session_id=session.session_id,
                    vm_id=session.vm_id,
                    source="session_manifest",
                    directory_path=session.directory_path,
                )
                binding = self._attach_directory_context(binding, provider=parsed.provider, repo=parsed.repo, pr_id=parsed.pr_id)
                stream_index.setdefault(key, []).append(binding)
                self._log_audit_decision(
                    event_type=AuditEventType.MAPPING_RESOLVED,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    provider=parsed.provider,
                    repo=parsed.repo,
                    pr_id=parsed.pr_id,
                    decision="resolved",
                    reason="session-manifest",
                    args={"source": binding.source},
                )

        for key, raw_entry in _load_pr_map_entries(self.config.pr_map_path).items():
            if not _host_heartbeat_manages_feedback_provider(key[0]):
                continue
            if key in stream_index:
                continue
            session_id = str(raw_entry.get("session_id", "")).strip()
            if not session_id:
                continue
            session = sessions_by_id.get(session_id)
            entry_directory_path = (
                session.directory_path
                if session is not None
                else str(raw_entry.get("directory_path", "")).strip() or None
            )
            if not scope_is_root and not _directory_is_within_scope(entry_directory_path, scope_directory_path):
                continue
            vm_id = session.vm_id if session is not None else None
            binding = StreamSessionBinding(
                session_id=session_id,
                vm_id=vm_id,
                source="pr_map",
                directory_path=entry_directory_path,
            )
            binding = self._attach_directory_context(binding, provider=key[0], repo=key[1], pr_id=key[2])
            stream_index[key] = [binding]
            self._log_audit_decision(
                event_type=AuditEventType.MAPPING_RESOLVED,
                session_id=binding.session_id,
                vm_id=binding.vm_id,
                provider=key[0],
                repo=key[1],
                pr_id=key[2],
                decision="resolved",
                reason="pr-map",
                args={"source": binding.source},
            )

        mapped: dict[FeedbackStream, list[StreamSessionBinding]] = {}
        for (provider, repo, pr_id), bindings in stream_index.items():
            stream = FeedbackStream(provider=provider, repo=repo, pr_id=pr_id)
            deduped_bindings = {
                (binding.session_id, binding.vm_id, binding.source): binding for binding in bindings
            }
            mapped[stream] = list(deduped_bindings.values())
        return mapped

    def _attach_directory_context(
        self,
        binding: StreamSessionBinding,
        *,
        provider: str,
        repo: str,
        pr_id: str,
    ) -> StreamSessionBinding:
        if self.directory_context_resolver is None:
            return binding
        context = self.directory_context_resolver.resolve_for_session(
            binding.session_id,
            fallback_directory_path=binding.directory_path,
            source=f"host_heartbeat:{binding.source}",
        )
        log_shadow_context(
            logger,
            prefix=f"provider={provider} repo={repo} pr={pr_id} session_id={binding.session_id} shadow_readiness",
            context=context,
        )
        return StreamSessionBinding(
            session_id=binding.session_id,
            vm_id=binding.vm_id,
            source=binding.source,
            directory_path=context.directory_path,
            directory_context=context.as_log_dict(),
        )

    def _collect_streams(self) -> list[FeedbackStream]:
        return sorted(
            self._collect_stream_mappings().keys(),
            key=lambda stream: (stream.provider, stream.repo, stream.pr_id),
        )

    def _collect_pr_streams_for_session(self, binding: StreamSessionBinding) -> list[FeedbackStream]:
        sessions_by_id = self._sessions_by_id()
        session = sessions_by_id.get(binding.session_id)
        collected: dict[tuple[str, str, str], FeedbackStream] = {}
        if session is not None:
            raw_streams = session.metadata.get("pr_feedback_streams")
            if isinstance(raw_streams, list):
                for raw_stream in raw_streams:
                    parsed = _parse_stream(raw_stream)
                    if parsed is not None and _host_heartbeat_manages_feedback_provider(parsed.provider):
                        collected[(parsed.provider, parsed.repo, parsed.pr_id)] = parsed

        for (provider, repo, pr_id), raw_entry in _load_pr_map_entries(self.config.pr_map_path).items():
            if not _host_heartbeat_manages_feedback_provider(provider):
                continue
            session_id = str(raw_entry.get("session_id", "")).strip()
            if session_id != binding.session_id:
                continue
            collected[(provider, repo, pr_id)] = FeedbackStream(provider=provider, repo=repo, pr_id=pr_id)

        return sorted(collected.values(), key=lambda stream: (stream.provider, stream.repo, stream.pr_id))

    def _collect_discord_channels_for_session(self, session_id: str) -> list[str]:
        policy_config = getattr(self.runtime, "policy_config", None)
        if policy_config is None:
            return []
        return list(policy_config.discord_inbox_channel_ids_for_session_id(session_id))

    def _collect_discord_channel_mappings(
        self,
        *,
        directory_path: str | None = None,
    ) -> dict[str, list[StreamSessionBinding]]:
        policy_config = getattr(self.runtime, "policy_config", None)
        if policy_config is None:
            return {}
        now = datetime.now(timezone.utc)
        scope_directory_path = DirectoryPath(segments=directory_path or "root").as_posix()
        scope_is_root = scope_directory_path == "root"
        mapped: dict[str, list[StreamSessionBinding]] = {}
        for session in self._sessions_by_id().values():
            if session.expires_at <= now:
                continue
            if not scope_is_root and not _directory_is_within_scope(session.directory_path, scope_directory_path):
                continue
            for channel_id in policy_config.discord_inbox_channel_ids_for_session_id(session.session_id):
                binding = StreamSessionBinding(
                    session_id=session.session_id,
                    vm_id=session.vm_id,
                    source="discord_inbox_routes",
                    directory_path=session.directory_path,
                )
                mapped.setdefault(channel_id, []).append(binding)
        return {
            channel_id: list({(binding.session_id, binding.vm_id, binding.source): binding for binding in bindings}.values())
            for channel_id, bindings in mapped.items()
        }


class _LockHandle:
    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self.fd = fd

    def __enter__(self) -> _LockHandle:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def _single_instance_lock(path: Path) -> _LockHandle:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise RuntimeError(
            f"host heartbeat worker already running (lock held at {path})"
        ) from exc

    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    return _LockHandle(path, fd)


def _compute_next_sleep_seconds(
    *,
    poll_interval_seconds: float,
    jitter_seconds: float,
    cooldown_seconds: float,
    discovered_feedback: int,
) -> float:
    delay = poll_interval_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    if discovered_feedback > 0:
        delay = max(delay, cooldown_seconds)
    return max(0.0, delay)


def _directory_is_within_scope(directory_path: str | None, scope_directory_path: str) -> bool:
    candidate_segments = DirectoryPath(segments=directory_path or "root").segments
    scope_segments = DirectoryPath(segments=scope_directory_path).segments
    return candidate_segments[: len(scope_segments)] == scope_segments


def _parse_stream(raw_stream: Any) -> FeedbackStream | None:
    if not isinstance(raw_stream, dict):
        return None

    provider = str(raw_stream.get("provider", "")).strip()
    repo = str(raw_stream.get("repo", "")).strip()
    pr_id = str(raw_stream.get("pr_id", "")).strip()
    if not provider or not repo or not pr_id:
        return None
    return FeedbackStream(provider=provider, repo=repo, pr_id=pr_id)


def _host_heartbeat_manages_feedback_provider(provider: str) -> bool:
    return provider not in _HOST_HEARTBEAT_DISABLED_FEEDBACK_PROVIDERS


def _load_pr_map_entries(path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unable to read PR map path=%s", path, exc_info=True)
        return {}
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, dict):
        return {}

    entries: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw_entry in raw_entries.values():
        if not isinstance(raw_entry, dict):
            continue
        provider = str(raw_entry.get("provider", "")).strip()
        repo = str(raw_entry.get("repo", "")).strip()
        pr_id = str(raw_entry.get("pr_id", "")).strip()
        if not provider or not repo or not pr_id:
            continue
        entries[(provider, repo, pr_id)] = raw_entry
    return entries


def _advance_cron_wake_request(
    *,
    wake_store: WakeRequestRepository,
    request: WakeScheduledRequest,
    now_utc: datetime,
    mark_consumed: bool,
    was_dispatched: bool,
) -> None:
    request.last_dispatched_at = now_utc if was_dispatched else request.last_dispatched_at
    request.updated_at = now_utc
    if mark_consumed:
        request.enabled = False
        request.next_run_at = None
        wake_store.upsert_request(request)
        return
    cron = (request.schedule.cron or "").strip()
    if not cron:
        wake_store.upsert_request(request)
        return
    next_base = request.next_run_at or now_utc
    request.next_run_at = _next_cron_time(cron, next_base)
    wake_store.upsert_request(request)


def _next_cron_time(expression: str, base: datetime) -> datetime | None:
    fields = expression.split()
    if len(fields) != 5:
        return None
    minute_values = _parse_cron_field(fields[0], 0, 59)
    hour_values = _parse_cron_field(fields[1], 0, 23)
    day_values = _parse_cron_field(fields[2], 1, 31)
    month_values = _parse_cron_field(fields[3], 1, 12)
    weekday_values = _parse_cron_field(fields[4], 0, 6)
    if any(value is None for value in (minute_values, hour_values, day_values, month_values, weekday_values)):
        return None
    assert minute_values is not None
    assert hour_values is not None
    assert day_values is not None
    assert month_values is not None
    assert weekday_values is not None
    candidate = base.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = candidate + timedelta(days=366)
    while candidate <= deadline:
        cron_weekday = (candidate.weekday() + 1) % 7
        if (
            candidate.minute in minute_values
            and candidate.hour in hour_values
            and candidate.day in day_values
            and candidate.month in month_values
            and cron_weekday in weekday_values
        ):
            return candidate
        candidate += timedelta(minutes=1)
    return None


def _parse_cron_field(token: str, lower: int, upper: int) -> set[int] | None:
    normalized = token.strip()
    if not normalized:
        return None
    if normalized == "*":
        return set(range(lower, upper + 1))
    values: set[int] = set()
    for chunk in normalized.split(","):
        part = chunk.strip()
        if not part:
            return None
        if part.startswith("*/"):
            try:
                step = int(part[2:])
            except ValueError:
                return None
            if step <= 0:
                return None
            values.update(range(lower, upper + 1, step))
            continue
        try:
            value = int(part)
        except ValueError:
            return None
        if value < lower or value > upper:
            return None
        values.add(value)
    return values


def _probe_agent_dispatch_state(*, session_id: str, vm_id: str, script_path: Path) -> str:
    if not script_path.exists():
        logger.warning(
            "Busy check script missing path=%s session_id=%s vm_id=%s",
            script_path,
            session_id,
            vm_id,
        )
        return "unknown"
    try:
        completed = subprocess.run(
            [str(script_path), session_id, vm_id],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        logger.warning(
            "Busy check script invocation failed path=%s session_id=%s vm_id=%s",
            script_path,
            session_id,
            vm_id,
            exc_info=True,
        )
        return "unknown"

    output = (completed.stdout or "").strip()
    if completed.returncode == 0:
        logger.info(
            "Busy check status=busy session_id=%s vm_id=%s output=%s",
            session_id,
            vm_id,
            output,
        )
        return "busy"
    if completed.returncode == 1:
        return "idle"
    if completed.returncode == 3:
        logger.info(
            "Busy check status=unavailable session_id=%s vm_id=%s output=%s",
            session_id,
            vm_id,
            output,
        )
        return "unavailable"
    logger.warning(
        "Busy check status=unknown session_id=%s vm_id=%s exit_code=%s output=%s",
        session_id,
        vm_id,
        completed.returncode,
        output,
    )
    return "unknown"


def _is_agent_busy(*, session_id: str, vm_id: str, script_path: Path) -> bool:
    return _probe_agent_dispatch_state(session_id=session_id, vm_id=vm_id, script_path=script_path) == "busy"


def _is_session_in_cooldown(*, path: Path, session_id: str) -> bool:
    state = _load_cooldown_state(path)
    now = time.time()
    expires_at = float(state.get(session_id, 0.0))
    if expires_at <= now:
        if session_id in state:
            state.pop(session_id, None)
            _save_cooldown_state(path, state)
        return False
    return True


def _set_session_cooldown(*, path: Path, session_id: str, cooldown_seconds: float) -> None:
    state = _load_cooldown_state(path)
    state[session_id] = max(0.0, time.time() + cooldown_seconds)
    _save_cooldown_state(path, state)


def _load_cooldown_state(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unable to read host heartbeat cooldown state path=%s", path, exc_info=True)
        return {}
    if not isinstance(payload, dict):
        return {}
    state: dict[str, float] = {}
    for session_id, raw_expires_at in payload.items():
        try:
            state[str(session_id)] = float(raw_expires_at)
        except (TypeError, ValueError):
            continue
    return state


def _save_cooldown_state(path: Path, state: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _worker_global_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "host-heartbeat" / "config.json"


def _load_host_heartbeat_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> HostHeartbeatWorkerGlobalConfig:
    base_payload = HostHeartbeatWorkerGlobalConfig.model_validate(
        (base_config or GatewayConfig()).model_dump(mode="json")
    ).model_dump(mode="json")
    path = _worker_global_config_path(project_dir)
    if not path.exists():
        return HostHeartbeatWorkerGlobalConfig.model_validate(base_payload)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return HostHeartbeatWorkerGlobalConfig.model_validate({**base_payload, **raw})


def build_host_heartbeat_worker(config: GatewayConfig, *, project_dir: Path | None = None) -> HostHeartbeatWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = _load_host_heartbeat_worker_global_config(resolved_project_dir, base_config=config)
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            **worker_config.model_dump(mode="json"),
        }
    )
    runtime = build_runtime(effective_config)
    provider_config_resolver = ProviderConfigResolver(
        effective_config,
        directory_store=runtime.directories,
        session_store=runtime.sessions,
    )
    root_provider_config = provider_config_resolver.resolve_for_directory("root")
    github_token = os.environ.get(root_provider_config.github_token_env, "").strip()
    azure_pat = os.environ.get(root_provider_config.azure_devops_pr_pat_env, "").strip()
    adapters: dict[str, FeedbackProviderAdapter] = {
        "github": GitHubFeedbackAdapter(token=github_token),
        "azure_devops": AzureDevOpsFeedbackAdapter(
            organization_url=root_provider_config.azure_devops_organization_url,
            project=root_provider_config.azure_devops_project,
            pat=azure_pat,
            pat_env_name=root_provider_config.azure_devops_pr_pat_env,
        ),
    }
    feedback_worker = build_pr_feedback_worker(effective_config.runtime_dir, adapters=adapters, gateway_config=effective_config)
    return HostHeartbeatWorker(
        config=effective_config,
        worker_config=worker_config,
        runtime=runtime,
        feedback_worker=feedback_worker,
        adapters=adapters,
        agent_control_store=AgentControlStore(effective_config.host_agent_control_state_path),
        provider_config_resolver=provider_config_resolver,
    )
