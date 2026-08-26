from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any
from urllib.parse import quote, unquote

import httpx

from evander.bootstrap import load_runtime_gateway_config
from evander.config import (
    GatewayConfig,
    TeamsIngestWorkerGlobalConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from evander.events.models import (
    TriggerEvent,
    materialize_node_definition,
    validate_output_mapping_keys,
)
from evander.events.provider_models import (
    CommunicationNewMessageTriggerDefinition,
    TeamsDirectMessageTriggerDefinition,
    TeamsNewMessageTriggerDefinition,
)
from evander.messaging.targets import load_communication_targets_config
from evander.repositories.cursors import (
    JsonTeamsInboxCursorRepository,
    TeamsInboxCursorRepository,
)
from evander.models import DirectoryPath, SessionManifest, TeamsMessageEnvelope, ToolName
from evander.orchestration.host_polling import (
    HostFileLockManager,
    HostSleeper,
    HostWakeNotifier,
    wake_socket_path_for_lock,
)
from evander.orchestration.kernels import (
    PollingKernelConfig,
    PollingOrchestratorKernel,
    compute_next_sleep_seconds,
)
from evander.runtime import (
    RuntimeContext,
    _build_delegated_teams_token_provider,
    build_runtime,
)
from evander.repositories.records import SqliteTeamsRecordsRepository
from evander.services import (
    GraphTeamsService,
    TeamsAuthenticationError,
    normalize_teams_target,
    parse_teams_target,
)
from evander.messaging.teams_inbox_store import (
    TeamsInboxMessage,
    TeamsInboxPollHealth,
    TeamsInboxStore,
    TeamsNotificationQueueEntry,
)
from evander.messaging.teams_subscriptions import (
    TeamsSubscriptionReconciler,
    tenant_id_from_graph_token,
)
from evander.triggers.providers.teams_message_events import (
    CommunicationTeamsMessageEventMatcher,
    TeamsDirectMessageEventMatcher,
    TeamsNewMessageEventMatcher,
)
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.workflow.graph_metadata import resolve_workflow_graph_metadata
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config
from evander.workflow.node_state import workflow_node_is_enabled

logger = logging.getLogger(__name__)
_ATTACHMENT_SECRET_KEYS = {
    "authorization",
    "token",
    "access_token",
    "content",
    "contentBytes",
    "content_bytes",
    "content_base64",
    "data",
}


@dataclass(frozen=True)
class TeamsIngestCycleResult:
    directories_polled: int
    channel_targets_polled: int
    direct_chats_polled: int
    sessions_seen: int
    new_messages_discovered: int
    trigger_wake_notified: bool
    next_sleep_seconds: float
    targets_skipped_backoff: int = 0
    targets_failed: int = 0
    targets_recovered: int = 0
    messages_matched: int = 0
    events_created: int = 0
    events_deduplicated: int = 0
    publication_failures: int = 0
    effect_wake_notified: bool = False
    workflow_config_revision: str | None = None
    notifications_processed: int = 0


@dataclass(frozen=True)
class _TeamsDirectoryPollResult:
    direct_chats_polled: int
    new_messages_discovered: int


@dataclass(frozen=True)
class _TeamsDirectChatsPollResult:
    targets_polled: set[str]
    new_messages_discovered: int


@dataclass(frozen=True)
class _GraphPollAttempt:
    attempted: bool
    succeeded: bool
    value: Any = None


@dataclass(frozen=True)
class _GraphTargetPollRequest:
    target_kind: str
    target: str
    operation: Callable[[], Any]
    is_direct_chat: bool = False


_TeamsMessageMatcher = (
    TeamsNewMessageEventMatcher
    | TeamsDirectMessageEventMatcher
    | CommunicationTeamsMessageEventMatcher
)
_TriggerEventQueue = SqliteTriggerEventQueue


@dataclass(frozen=True)
class _CapturedTeamsMessageMatcher:
    trigger_id: str
    trigger_type: str
    directory_path: str
    matcher: _TeamsMessageMatcher

    def events_for(self, message: TeamsInboxMessage) -> tuple[TriggerEvent, ...]:
        if isinstance(self.matcher, CommunicationTeamsMessageEventMatcher):
            return self.matcher.events_for(message)
        event = self.matcher.event_for(message)
        return (event,) if event is not None else ()


@dataclass(frozen=True)
class _TeamsTriggerConfigSnapshot:
    revision: str
    captured_at: datetime
    trigger_config: TriggerOrchestratorWorkerGlobalConfig
    direct_sender_refs: frozenset[str]
    poll_targets_by_directory: Mapping[str, frozenset[str]]
    matchers: tuple[_CapturedTeamsMessageMatcher, ...]
    event_queue: _TriggerEventQueue | None


class TeamsIngestWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: TeamsIngestWorkerGlobalConfig,
        runtime: RuntimeContext,
        store: TeamsInboxCursorRepository | None = None,
        project_dir: Path | None = None,
        trigger_wake_notifier: Callable[[], bool] | None = None,
        effect_wake_notifier: Callable[[], bool] | None = None,
        event_queue: _TriggerEventQueue | None = None,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
        self.session_repository = runtime.session_repository
        self.store = store or JsonTeamsInboxCursorRepository(
            config.runtime_dir / "teams-inbox-store.db"
        )
        self._notification_store = self.store if isinstance(self.store, TeamsInboxStore) else None
        self._subscription_reconciler = (
            TeamsSubscriptionReconciler(
                store=self._notification_store,
                worker_config=self.worker_config,
            )
            if self._notification_store is not None
            else None
        )
        self._trigger_wake_notifier = trigger_wake_notifier
        self._effect_wake_notifier = effect_wake_notifier
        self._event_queue_override = event_queue
        self._event_queue_cache: tuple[tuple[object, ...], _TriggerEventQueue] | None = None
        self._cycle_state_lock = RLock()
        self._cycle_trigger_wake_notified = False
        self._cycle_effect_wake_notified = False
        self._cycle_effect_wake_attempted = False
        self._cycle_targets_skipped_backoff = 0
        self._cycle_targets_failed = 0
        self._cycle_targets_recovered = 0
        self._cycle_messages_matched = 0
        self._cycle_events_created = 0
        self._cycle_events_deduplicated = 0
        self._cycle_publication_failures = 0
        self._cycle_auth_failures: set[str] = set()
        self._kernel = PollingOrchestratorKernel(
            config=PollingKernelConfig(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                lock_path=self.worker_config.lock_path,
            ),
            lock_manager=HostFileLockManager(busy_error_text="Teams ingest worker already running"),
            sleeper=HostSleeper(),
            run_cycle=self.run_once,
            get_sleep_seconds=lambda result: result.next_sleep_seconds,
            on_start=self._log_start,
            on_cycle_success=self._log_cycle_success,
            on_cycle_exception=self._log_cycle_exception,
            get_exception_sleep_seconds=lambda _exc: self.worker_config.backoff_seconds,
        )

    def run_forever(self) -> None:
        self._kernel.run_forever()

    def run_once_with_lock(self) -> TeamsIngestCycleResult:
        return self._kernel.run_once_with_lock()

    def run_once(self) -> TeamsIngestCycleResult:
        with self._cycle_state_lock:
            self._cycle_trigger_wake_notified = False
            self._cycle_effect_wake_notified = False
            self._cycle_effect_wake_attempted = False
            self._cycle_targets_skipped_backoff = 0
            self._cycle_targets_failed = 0
            self._cycle_targets_recovered = 0
            self._cycle_messages_matched = 0
            self._cycle_events_created = 0
            self._cycle_events_deduplicated = 0
            self._cycle_publication_failures = 0
            self._cycle_auth_failures = set()
        now = datetime.now(UTC)
        trigger_snapshot = self._capture_trigger_config_snapshot(captured_at=now)
        active_sessions = self.runtime.session_repository.list_active_sessions(now=now)
        channel_targets_polled = 0
        direct_chats_polled = 0
        new_messages_discovered = 0
        direct_sender_refs = set(trigger_snapshot.direct_sender_refs)
        trigger_targets_by_directory = trigger_snapshot.poll_targets_by_directory
        directories = self._directories_to_scan(
            active_sessions,
            include_default_directory=bool(direct_sender_refs),
            include_directories=set(trigger_targets_by_directory),
        )
        notifications_processed = self._process_notification_queue(
            active_sessions=active_sessions,
            trigger_snapshot=trigger_snapshot,
            directories=directories,
        )
        if not self.worker_config.change_notifications_enabled:
            self._cleanup_owned_subscriptions(now=now)
        try:
            for directory_path in directories:
                channel_targets = self._channel_targets_for_directory(
                    directory_path,
                    active_sessions=active_sessions,
                    trigger_targets_by_directory=trigger_targets_by_directory,
                )
                channel_targets_polled += len(channel_targets)
                directory_result = self._poll_directory(
                    directory_path,
                    channel_targets=channel_targets,
                    direct_sender_refs=direct_sender_refs,
                    trigger_snapshot=trigger_snapshot,
                )
                direct_chats_polled += directory_result.direct_chats_polled
                new_messages_discovered += directory_result.new_messages_discovered
            if self.worker_config.change_notifications_enabled:
                self._cleanup_orphaned_subscriptions(
                    active_directories=set(directories),
                    now=now,
                )
        finally:
            self._notify_effect_orchestrator_if_needed(trigger_snapshot=trigger_snapshot)
        if self.worker_config.directories:
            matching_sessions = {
                session.session_id
                for session in active_sessions
                if any(
                    _directory_is_within_scope(session.directory_path, directory_path)
                    for directory_path in directories
                )
            }
        else:
            matching_sessions = {session.session_id for session in active_sessions}
        next_sleep_seconds = compute_next_sleep_seconds(
            poll_interval_seconds=self.worker_config.poll_interval_seconds,
            jitter_seconds=self.worker_config.jitter_seconds,
        )
        with self._cycle_state_lock:
            trigger_wake_notified = self._cycle_trigger_wake_notified
            effect_wake_notified = self._cycle_effect_wake_notified
            targets_skipped_backoff = self._cycle_targets_skipped_backoff
            targets_failed = self._cycle_targets_failed
            targets_recovered = self._cycle_targets_recovered
            messages_matched = self._cycle_messages_matched
            events_created = self._cycle_events_created
            events_deduplicated = self._cycle_events_deduplicated
            publication_failures = self._cycle_publication_failures
        return TeamsIngestCycleResult(
            directories_polled=len(directories),
            channel_targets_polled=channel_targets_polled,
            direct_chats_polled=direct_chats_polled,
            sessions_seen=len(matching_sessions),
            new_messages_discovered=new_messages_discovered,
            trigger_wake_notified=trigger_wake_notified,
            next_sleep_seconds=next_sleep_seconds,
            targets_skipped_backoff=targets_skipped_backoff,
            targets_failed=targets_failed,
            targets_recovered=targets_recovered,
            messages_matched=messages_matched,
            events_created=events_created,
            events_deduplicated=events_deduplicated,
            publication_failures=publication_failures,
            effect_wake_notified=effect_wake_notified,
            workflow_config_revision=trigger_snapshot.revision,
            notifications_processed=notifications_processed,
        )

    def _log_start(self) -> None:
        logger.info(
            (
                "Teams ingest worker started poll_interval=%.3f jitter=%.3f backoff=%.3f "
                "max_concurrent_target_polls=%d publish_trigger_events_directly=%s lock=%s"
            ),
            self.worker_config.poll_interval_seconds,
            self.worker_config.jitter_seconds,
            self.worker_config.backoff_seconds,
            self.worker_config.max_concurrent_target_polls,
            self.worker_config.publish_trigger_events_directly,
            self.worker_config.lock_path,
        )

    def _log_cycle_success(self, result: TeamsIngestCycleResult) -> None:
        logger.debug(
            (
                "Teams ingest worker cycle complete directories=%d channel_targets=%d "
                "direct_chats=%d sessions=%d new_messages=%d "
                "messages_matched=%d events_created=%d events_deduplicated=%d "
                "publication_failures=%d effect_wake_notified=%s trigger_wake_notified=%s "
                "workflow_config_revision=%s targets_skipped_backoff=%d targets_failed=%d "
                "targets_recovered=%d notifications_processed=%d sleep_seconds=%.3f"
            ),
            result.directories_polled,
            result.channel_targets_polled,
            result.direct_chats_polled,
            result.sessions_seen,
            result.new_messages_discovered,
            result.messages_matched,
            result.events_created,
            result.events_deduplicated,
            result.publication_failures,
            result.effect_wake_notified,
            result.trigger_wake_notified,
            result.workflow_config_revision,
            result.targets_skipped_backoff,
            result.targets_failed,
            result.targets_recovered,
            result.notifications_processed,
            result.next_sleep_seconds,
        )

    def _log_cycle_exception(self, _exc: Exception) -> None:
        logger.exception("Teams ingest worker cycle failed")

    def _notify_trigger_orchestrator_if_needed(
        self,
        new_messages_discovered: int,
        *,
        trigger_snapshot: _TeamsTriggerConfigSnapshot,
    ) -> bool:
        with self._cycle_state_lock:
            if new_messages_discovered <= 0 or self._cycle_trigger_wake_notified:
                return False
            try:
                notifier = self._trigger_wake_notifier or self._wake_notifier_for_lock(
                    trigger_snapshot.trigger_config.lock_path
                )
                notified = notifier()
                self._cycle_trigger_wake_notified = self._cycle_trigger_wake_notified or notified
                return notified
            except Exception:
                logger.warning(
                    "Failed to notify trigger orchestrator wake socket revision=%s",
                    trigger_snapshot.revision,
                    exc_info=True,
                )
                return False

    def _notify_effect_orchestrator_if_needed(
        self,
        *,
        trigger_snapshot: _TeamsTriggerConfigSnapshot,
    ) -> bool:
        with self._cycle_state_lock:
            if self._cycle_events_created <= 0 or self._cycle_effect_wake_attempted:
                return False
            self._cycle_effect_wake_attempted = True
            try:
                notifier = self._effect_wake_notifier or self._wake_notifier_for_lock(
                    trigger_snapshot.trigger_config.effect_lock_path
                )
                notified = notifier()
                self._cycle_effect_wake_notified = self._cycle_effect_wake_notified or notified
                return notified
            except Exception:
                logger.warning(
                    "Failed to notify effect orchestrator wake socket revision=%s",
                    trigger_snapshot.revision,
                    exc_info=True,
                )
                return False

    def _wake_notifier_for_lock(self, lock_path: Path | None) -> Callable[[], bool]:
        resolved_lock_path = lock_path or self.config.runtime_dir / "trigger-orchestrator.lock"
        if not resolved_lock_path.is_absolute():
            resolved_lock_path = self.project_dir / resolved_lock_path
        return HostWakeNotifier(wake_socket_path_for_lock(resolved_lock_path)).notify

    def _directories_to_scan(
        self,
        active_sessions: list[SessionManifest],
        *,
        include_default_directory: bool = False,
        include_directories: set[str] | None = None,
    ) -> list[str]:
        if self.worker_config.directories:
            return list(self.worker_config.directories)
        directories = {
            DirectoryPath(segments=session.directory_path).as_posix() for session in active_sessions
        }
        directories.update(include_directories or set())
        if include_default_directory and not directories:
            directories.add("root")
        return sorted(directories)

    def _channel_targets_for_directory(
        self,
        directory_path: str,
        *,
        active_sessions: list[SessionManifest],
        trigger_targets_by_directory: Mapping[str, frozenset[str]] | None = None,
    ) -> list[str]:
        targets = {
            normalized
            for session in active_sessions
            if ToolName.TEAMS_GET_MESSAGES in session.allowed_tools
            and _directory_is_within_scope(session.directory_path, directory_path)
            for normalized in self.runtime.policy_config.resolved_teams_channel_targets_for_session(
                session,
                capability="read",
            )
        }
        for trigger_directory, trigger_targets in (trigger_targets_by_directory or {}).items():
            if _directory_is_within_scope(trigger_directory, directory_path):
                targets.update(trigger_targets)
        return sorted(targets)

    def _capture_trigger_config_snapshot(
        self,
        *,
        captured_at: datetime,
    ) -> _TeamsTriggerConfigSnapshot:
        try:
            trigger_config = load_trigger_orchestrator_worker_global_config(
                self.project_dir,
                base_config=self.config,
            )
        except Exception:
            logger.exception(
                "Failed to capture trigger orchestrator config for Teams ingest; "
                "using an empty recovery snapshot"
            )
            trigger_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
                {"runtime_dir": self.config.runtime_dir}
            )

        trigger_config = trigger_config.model_copy(deep=True)
        revision = _trigger_config_revision(trigger_config)
        direct_sender_refs: set[str] = set()
        targets_by_directory: dict[str, set[str]] = {}
        matchers: list[_CapturedTeamsMessageMatcher] = []
        communication_targets_by_directory: dict[str, dict[str, Any]] = {}
        nodes_by_id = {
            node.id: node
            for node in (
                *trigger_config.triggers,
                *trigger_config.gates,
                *trigger_config.effects,
                *trigger_config.queries,
            )
        }
        node_graph_metadata_by_id = resolve_workflow_graph_metadata(
            nodes_by_id=nodes_by_id,
            edges=trigger_config.edges,
            workflow_graphs=trigger_config.workflow_graphs,
        )

        for trigger in trigger_config.triggers:
            if not workflow_node_is_enabled(trigger):
                continue
            try:
                materialized = materialize_node_definition(trigger)
                if isinstance(materialized, TeamsDirectMessageTriggerDefinition):
                    direct_sender_refs.update(
                        value.strip() for value in materialized.sender_user_ids if value.strip()
                    )
                    if not self.worker_config.publish_trigger_events_directly:
                        continue
                    matcher = TeamsDirectMessageEventMatcher.from_cursor_state(
                        materialized,
                        _read_trigger_cursor_state(
                            self.config.runtime_dir / "trigger-cursors-teams.json",
                            materialized.id,
                        ),
                        missing_cursor_baseline=captured_at,
                    )
                elif isinstance(materialized, TeamsNewMessageTriggerDefinition):
                    if not self.worker_config.publish_trigger_events_directly:
                        continue
                    matcher = TeamsNewMessageEventMatcher.from_cursor_state(
                        materialized,
                        _read_trigger_cursor_state(
                            self.config.runtime_dir / "trigger-cursors-teams.json",
                            materialized.id,
                        ),
                    )
                    graph_metadata = node_graph_metadata_by_id.get(materialized.id)
                    directory_path = DirectoryPath(
                        segments=(graph_metadata.directory_path if graph_metadata else "root")
                    ).as_posix()
                    targets_by_directory.setdefault(directory_path, set()).update(
                        matcher.target_filters
                    )
                elif isinstance(materialized, CommunicationNewMessageTriggerDefinition):
                    directory_path = DirectoryPath(
                        segments=materialized.directory_path or "root"
                    ).as_posix()
                    targets_by_id = communication_targets_by_directory.get(directory_path)
                    if targets_by_id is None:
                        targets_by_id = load_communication_targets_config(
                            runtime_dir=self.config.runtime_dir,
                            directory_store=self.runtime.directories,
                            directory_path=directory_path,
                        ).by_id()
                        communication_targets_by_directory[directory_path] = targets_by_id
                    matcher = CommunicationTeamsMessageEventMatcher.from_cursor_state(
                        materialized,
                        _read_trigger_cursor_state(
                            self.config.runtime_dir / "trigger-cursors-communication.json",
                            materialized.id,
                        ),
                        targets_by_id=targets_by_id,
                    )
                    if matcher.teams_targets_by_address:
                        targets_by_directory.setdefault(directory_path, set()).update(
                            matcher.teams_targets_by_address
                        )
                    if not self.worker_config.publish_trigger_events_directly:
                        continue
                else:
                    continue
                matchers.append(
                    _CapturedTeamsMessageMatcher(
                        trigger_id=materialized.id,
                        trigger_type=materialized.type,
                        directory_path=DirectoryPath(
                            segments=(
                                str(getattr(materialized, "directory_path", "") or "").strip()
                                or (
                                    node_graph_metadata_by_id[materialized.id].directory_path
                                    if materialized.id in node_graph_metadata_by_id
                                    else "root"
                                )
                            )
                        ).as_posix(),
                        matcher=matcher,
                    )
                )
            except Exception:
                logger.warning(
                    "Failed to capture Teams trigger matcher trigger_id=%s trigger_type=%s "
                    "revision=%s",
                    getattr(trigger, "id", ""),
                    getattr(trigger, "type", ""),
                    revision,
                    exc_info=True,
                )

        event_queue = None
        if self.worker_config.publish_trigger_events_directly and matchers:
            try:
                event_queue = self._event_queue_for(trigger_config)
            except Exception:
                logger.warning(
                    "Failed to initialize direct Teams trigger event queue revision=%s; "
                    "continuing with inbox persistence and trigger wake recovery",
                    revision,
                    exc_info=True,
                )
        return _TeamsTriggerConfigSnapshot(
            revision=revision,
            captured_at=captured_at,
            trigger_config=trigger_config,
            direct_sender_refs=frozenset(direct_sender_refs),
            poll_targets_by_directory=MappingProxyType(
                {
                    directory_path: frozenset(targets)
                    for directory_path, targets in targets_by_directory.items()
                }
            ),
            matchers=tuple(matchers),
            event_queue=event_queue,
        )

    def _event_queue_for(
        self,
        trigger_config: TriggerOrchestratorWorkerGlobalConfig,
    ) -> _TriggerEventQueue:
        if self._event_queue_override is not None:
            return self._event_queue_override
        cache_key: tuple[object, ...] = (
            str(trigger_config.orchestration_store_path),
            trigger_config.orchestration_retention_days,
            trigger_config.orchestration_compact_history_retention_days,
        )
        if self._event_queue_cache is None or self._event_queue_cache[0] != cache_key:
            self._event_queue_cache = (
                cache_key,
                SqliteTriggerEventQueue(
                    trigger_config.orchestration_store_path,
                    retention_days=trigger_config.orchestration_retention_days,
                    compact_history_retention_days=(
                        trigger_config.orchestration_compact_history_retention_days
                    ),
                ),
            )
        assert self._event_queue_cache is not None
        return self._event_queue_cache[1]

    def _poll_directory(
        self,
        directory_path: str,
        *,
        channel_targets: list[str],
        direct_sender_refs: set[str],
        trigger_snapshot: _TeamsTriggerConfigSnapshot,
    ) -> _TeamsDirectoryPollResult:
        provider_config = self.runtime.provider_config.resolve_for_directory(directory_path)
        if provider_config.teams_backend not in {"graph_api", "delegated_graph_api"}:
            logger.debug(
                "Skipping Teams ingest for directory=%s because teams_backend=%s",
                directory_path,
                provider_config.teams_backend,
            )
            return _TeamsDirectoryPollResult(direct_chats_polled=0, new_messages_discovered=0)
        service = self._build_graph_service(
            provider_config=provider_config,
            channel_targets=channel_targets,
            directory_path=directory_path,
        )
        poll_scope = _teams_graph_poll_scope(provider_config, directory_path=directory_path)
        direct_chats_polled = 0
        new_messages_discovered = 0
        with httpx.Client(timeout=provider_config.teams_timeout_seconds) as client:
            target_poll_requests: list[_GraphTargetPollRequest] = []
            direct_chat_targets_polled: set[str] = set()
            direct_sender_refs_by_id = self._resolve_direct_message_sender_refs_by_id(
                client,
                service=service,
                sender_refs=direct_sender_refs,
                directory_path=directory_path,
                poll_scope=poll_scope,
            )
            if direct_sender_refs_by_id:
                discovered_targets = self.store.get_discovered_chat_targets(poll_scope)
                if self._reconciliation_poll_due(
                    poll_scope=poll_scope,
                    target_kind="discovery",
                    target="/me/chats",
                    now=datetime.now(UTC),
                ):
                    discovery = self._run_graph_poll_target(
                        poll_scope=poll_scope,
                        target_kind="discovery",
                        target="/me/chats",
                        operation=lambda: list(
                            service._iter_one_on_one_chats(
                                client,
                                budget=service.new_pagination_budget(),
                            )
                        ),
                    )
                    if discovery.succeeded:
                        discovered_targets = _one_on_one_chat_targets(discovery.value)
                        self.store.replace_discovered_chat_targets(
                            poll_scope,
                            discovered_targets,
                            discovered_at=datetime.now(UTC),
                        )
                direct_targets = discovered_targets | {
                    target for target in channel_targets if target.startswith("chat:")
                }
                for chat_target in sorted(direct_targets):
                    target_poll_requests.append(
                        _GraphTargetPollRequest(
                            target_kind="chat",
                            target=chat_target,
                            is_direct_chat=True,
                            operation=lambda target=chat_target: self._poll_channel(
                                client,
                                service=service,
                                channel_target=target,
                                budget=service.new_pagination_budget(),
                                direct_message_sender_refs_by_id=direct_sender_refs_by_id,
                                store_unmatched_direct_messages=target in channel_targets,
                                trigger_snapshot=trigger_snapshot,
                            ),
                        )
                    )
                    direct_chat_targets_polled.add(chat_target)
            for channel_target in channel_targets:
                if channel_target in direct_chat_targets_polled:
                    continue
                parsed_target = parse_teams_target(channel_target)
                target_poll_requests.append(
                    _GraphTargetPollRequest(
                        target_kind=parsed_target.kind,
                        target=channel_target,
                        operation=lambda target=channel_target: self._poll_channel(
                            client,
                            service=service,
                            channel_target=target,
                            budget=service.new_pagination_budget(),
                            trigger_snapshot=trigger_snapshot,
                        ),
                    )
                )
            healthy_notification_targets = self._reconcile_subscriptions(
                client,
                service=service,
                provider_config=provider_config,
                directory_path=directory_path,
                targets={request.target for request in target_poll_requests},
            )
            target_poll_requests = [
                request
                for request in target_poll_requests
                if request.target not in healthy_notification_targets
                or self._reconciliation_poll_due(
                    poll_scope=poll_scope,
                    target_kind=request.target_kind,
                    target=request.target,
                    now=datetime.now(UTC),
                )
            ]
            for target_poll_request, attempt in self._run_target_poll_requests(
                poll_scope=poll_scope,
                target_poll_requests=target_poll_requests,
            ):
                if attempt.succeeded:
                    if target_poll_request.is_direct_chat:
                        direct_chats_polled += 1
                    discovered = int(attempt.value or 0)
                    new_messages_discovered += discovered
                    self._notify_trigger_orchestrator_if_needed(
                        discovered,
                        trigger_snapshot=trigger_snapshot,
                    )
        return _TeamsDirectoryPollResult(
            direct_chats_polled=direct_chats_polled,
            new_messages_discovered=new_messages_discovered,
        )

    def _reconcile_subscriptions(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        provider_config: GatewayConfig,
        directory_path: str,
        targets: set[str],
    ) -> set[str]:
        reconciler = self._subscription_reconciler
        if not self.worker_config.change_notifications_enabled or reconciler is None:
            return set()
        try:
            tenant_id = tenant_id_from_graph_token(service, provider_config)
            healthy_before_reconciliation = reconciler.healthy_targets(
                directory_path=directory_path,
                tenant_id=tenant_id,
                now=datetime.now(UTC),
            )
            healthy_after_reconciliation = reconciler.reconcile_directory(
                client,
                service=service,
                provider_config=provider_config,
                directory_path=directory_path,
                targets=targets,
                now=datetime.now(UTC),
            )
            # A newly created or lifecycle-repaired subscription gets one immediate
            # polling reconciliation before the target moves to the slow cadence.
            return healthy_before_reconciliation & healthy_after_reconciliation
        except Exception:
            if self._notification_store is not None:
                self._notification_store.replace_notification_target_health(
                    directory_path=directory_path,
                    desired_targets=targets,
                    healthy_targets=set(),
                    errors_by_target={
                        target: "subscription reconciliation failed" for target in targets
                    },
                    updated_at=datetime.now(UTC),
                )
            logger.warning(
                "Teams Graph subscription reconciliation failed directory=%s; "
                "keeping fast polling fallback",
                directory_path,
                exc_info=True,
            )
            return set()

    def _reconciliation_poll_due(
        self,
        *,
        poll_scope: str,
        target_kind: str,
        target: str,
        now: datetime,
    ) -> bool:
        if not self.worker_config.change_notifications_enabled:
            return True
        health = self.store.get_poll_health(_teams_poll_health_key(poll_scope, target_kind, target))
        if health is None or health.last_success_at is None:
            return True
        return (
            _coerce_utc(health.last_success_at)
            + timedelta(
                seconds=self.worker_config.change_notification_reconciliation_poll_interval_seconds
            )
            <= now
        )

    def _cleanup_owned_subscriptions(self, *, now: datetime) -> None:
        reconciler = self._subscription_reconciler
        store = self._notification_store
        if reconciler is None or store is None:
            return
        subscriptions_by_directory: dict[str, list[Any]] = {}
        for subscription in store.list_owned_subscriptions():
            subscriptions_by_directory.setdefault(subscription.directory_path, []).append(
                subscription
            )
        self._cleanup_subscription_groups(
            subscriptions_by_directory,
            now=now,
            retain_fallback_targets=True,
        )

    def _cleanup_orphaned_subscriptions(
        self,
        *,
        active_directories: set[str],
        now: datetime,
    ) -> None:
        store = self._notification_store
        if store is None:
            return
        subscriptions_by_directory: dict[str, list[Any]] = {}
        for subscription in store.list_owned_subscriptions():
            if subscription.directory_path not in active_directories:
                subscriptions_by_directory.setdefault(
                    subscription.directory_path, []
                ).append(subscription)
        self._cleanup_subscription_groups(
            subscriptions_by_directory,
            now=now,
            retain_fallback_targets=False,
        )

    def _cleanup_subscription_groups(
        self,
        subscriptions_by_directory: dict[str, list[Any]],
        *,
        now: datetime,
        retain_fallback_targets: bool,
    ) -> None:
        reconciler = self._subscription_reconciler
        store = self._notification_store
        if reconciler is None or store is None:
            return
        for directory_path, subscriptions in subscriptions_by_directory.items():
            store.replace_notification_target_health(
                directory_path=directory_path,
                desired_targets=(
                    {item.target for item in subscriptions}
                    if retain_fallback_targets
                    else set()
                ),
                healthy_targets=set(),
                errors_by_target={
                    item.target: "notifications disabled" for item in subscriptions
                },
                updated_at=now,
            )
            provider_config = self.runtime.provider_config.resolve_for_directory(directory_path)
            if provider_config.teams_backend not in {"graph_api", "delegated_graph_api"}:
                continue
            try:
                service = self._build_graph_service(
                    provider_config=provider_config,
                    channel_targets=[item.target for item in subscriptions],
                    directory_path=directory_path,
                )
                with httpx.Client(timeout=provider_config.teams_timeout_seconds) as client:
                    reconciler.cleanup_directory(
                        client,
                        service=service,
                        directory_path=directory_path,
                        now=now,
                    )
            except Exception as exc:
                logger.warning(
                    "Unable to clean up owned Teams Graph subscriptions directory=%s; "
                    "subscriptions will be allowed to expire",
                    directory_path,
                    exc_info=True,
                )
                for subscription in subscriptions:
                    store.upsert_owned_subscription(
                        subscription.model_copy(
                            update={
                                "health": "abandoned",
                                "cleanup_attempted": True,
                                "updated_at": now,
                                "last_error": " ".join(str(exc).split())[:500],
                            }
                        )
                    )

    def _process_notification_queue(
        self,
        *,
        active_sessions: list[SessionManifest],
        trigger_snapshot: _TeamsTriggerConfigSnapshot,
        directories: list[str],
    ) -> int:
        store = self._notification_store
        reconciler = self._subscription_reconciler
        if store is None or reconciler is None:
            return 0
        processed = 0
        now = datetime.now(UTC)
        for entry in store.lease_notifications(now=now, limit=100, lease_seconds=60):
            try:
                if entry.notification_kind == "lifecycle":
                    reconciler.apply_lifecycle_event(entry, now=datetime.now(UTC))
                    store.complete_notification(entry, completed_at=datetime.now(UTC))
                    processed += 1
                    continue
                discovered = self._process_message_notification(
                    entry,
                    active_sessions=active_sessions,
                    trigger_snapshot=trigger_snapshot,
                    directories=directories,
                )
                self._notify_trigger_orchestrator_if_needed(
                    discovered,
                    trigger_snapshot=trigger_snapshot,
                )
                store.complete_notification(entry, completed_at=datetime.now(UTC))
                processed += 1
            except Exception as exc:
                retryable = _notification_error_is_retryable(exc)
                age = datetime.now(UTC) - _coerce_utc(entry.received_at)
                if retryable and age < timedelta(hours=24):
                    backoff_seconds = min(900.0, float(2 ** min(entry.attempt_count, 9)))
                    store.retry_notification(
                        entry,
                        next_attempt_at=datetime.now(UTC) + timedelta(seconds=backoff_seconds),
                        error=str(exc),
                    )
                else:
                    store.fail_notification(
                        entry,
                        failed_at=datetime.now(UTC),
                        error=str(exc),
                    )
                logger.warning(
                    "Teams notification processing failed subscription=%s retryable=%s",
                    entry.subscription_id,
                    retryable,
                    exc_info=True,
                )
        return processed

    def _process_message_notification(
        self,
        entry: TeamsNotificationQueueEntry,
        *,
        active_sessions: list[SessionManifest],
        trigger_snapshot: _TeamsTriggerConfigSnapshot,
        directories: list[str],
    ) -> int:
        store = self._notification_store
        if store is None:
            return 0
        subscription = store.get_owned_subscription(entry.subscription_id)
        if subscription is None or subscription.directory_path not in set(directories):
            return 0
        directory_path = subscription.directory_path
        channel_targets = self._channel_targets_for_directory(
            directory_path,
            active_sessions=active_sessions,
            trigger_targets_by_directory=trigger_snapshot.poll_targets_by_directory,
        )
        provider_config = self.runtime.provider_config.resolve_for_directory(directory_path)
        if provider_config.teams_backend not in {"graph_api", "delegated_graph_api"}:
            return 0
        poll_scope = _teams_graph_poll_scope(provider_config, directory_path=directory_path)
        discovered_chats = self.store.get_discovered_chat_targets(poll_scope)
        target = subscription.target
        direct_sender_refs = set(trigger_snapshot.direct_sender_refs)
        if target not in channel_targets and not (
            target.startswith("chat:") and target in discovered_chats and direct_sender_refs
        ):
            return 0
        service = self._build_graph_service(
            provider_config=provider_config,
            channel_targets=[target],
            directory_path=directory_path,
        )
        parsed_target = parse_teams_target(target)
        with httpx.Client(timeout=provider_config.teams_timeout_seconds) as client:
            resolved_tenant = tenant_id_from_graph_token(service, provider_config)
            if resolved_tenant != subscription.tenant_id:
                raise PermissionError("Teams notification tenant no longer matches credentials")
            notified_target = _notification_resource_target(entry.resource)
            if normalize_teams_target(notified_target) != normalize_teams_target(target):
                raise ValueError("Teams notification resource does not match its subscription")
            message_id, thread_id = _notification_message_locator(entry.resource)
            if parsed_target.kind == "chat":
                url = (
                    f"{service.api_base_url}/chats/{quote(parsed_target.chat_id or '', safe='')}"
                    f"/messages/{quote(message_id, safe='')}"
                )
            elif thread_id:
                url = (
                    f"{service.api_base_url}/teams/{quote(parsed_target.team_id or '', safe='')}"
                    f"/channels/{quote(parsed_target.channel_id or '', safe='')}"
                    f"/messages/{quote(thread_id, safe='')}/replies/"
                    f"{quote(message_id, safe='')}"
                )
            else:
                url = (
                    f"{service.api_base_url}/teams/{quote(parsed_target.team_id or '', safe='')}"
                    f"/channels/{quote(parsed_target.channel_id or '', safe='')}"
                    f"/messages/{quote(message_id, safe='')}"
                )
            response = service._request_with_retries(client, "GET", url)
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Teams Graph message response was not an object")
            message = self._normalize_message(
                service._to_envelope(
                    payload,
                    target=parsed_target,
                    reply_to_id=thread_id,
                )
            )
            direct_sender_refs_by_id = None
            store_unmatched = False
            if parsed_target.kind == "chat":
                direct_sender_refs_by_id = self._resolve_direct_message_sender_refs_by_id(
                    client,
                    service=service,
                    sender_refs=direct_sender_refs,
                    directory_path=directory_path,
                    poll_scope=poll_scope,
                )
                store_unmatched = target in channel_targets
            discovered = self._ingest_message_candidates(
                [message],
                channel_target=target,
                cursor_state=self.store.get_cursor_state(target),
                direct_message_sender_refs_by_id=direct_sender_refs_by_id,
                store_unmatched_direct_messages=store_unmatched,
                trigger_snapshot=trigger_snapshot,
            )
            if discovered > 0:
                store.record_notification_ingestion(ingested_at=datetime.now(UTC))
            return discovered

    def _run_target_poll_requests(
        self,
        *,
        poll_scope: str,
        target_poll_requests: list[_GraphTargetPollRequest],
    ) -> Iterator[tuple[_GraphTargetPollRequest, _GraphPollAttempt]]:
        if self.worker_config.max_concurrent_target_polls == 1:
            for target_poll_request in target_poll_requests:
                yield (
                    target_poll_request,
                    self._run_graph_poll_target(
                        poll_scope=poll_scope,
                        target_kind=target_poll_request.target_kind,
                        target=target_poll_request.target,
                        operation=target_poll_request.operation,
                    ),
                )
            return

        with ThreadPoolExecutor(
            max_workers=self.worker_config.max_concurrent_target_polls,
            thread_name_prefix="teams-ingest",
        ) as executor:
            futures = {
                executor.submit(
                    self._run_graph_poll_target,
                    poll_scope=poll_scope,
                    target_kind=target_poll_request.target_kind,
                    target=target_poll_request.target,
                    operation=target_poll_request.operation,
                ): target_poll_request
                for target_poll_request in target_poll_requests
            }
            first_exception: Exception | None = None
            for future in as_completed(futures):
                try:
                    attempt = future.result()
                except Exception as exc:
                    if first_exception is None:
                        first_exception = exc
                    continue
                yield futures[future], attempt
            if first_exception is not None:
                raise first_exception

    def _build_graph_service(
        self,
        *,
        provider_config: GatewayConfig,
        channel_targets: list[str],
        directory_path: str,
    ) -> GraphTeamsService:
        token = None
        token_provider = None
        if provider_config.teams_backend == "graph_api":
            env_var_name = provider_config.teams_graph_access_token_env.strip()
            if not env_var_name:
                raise RuntimeError(
                    "teams backend 'graph_api' requires config.teams_graph_access_token_env to be set"
                )
            token = os.environ.get(env_var_name, "").strip()
            if not token:
                raise RuntimeError(
                    "teams backend 'graph_api' requires an access token in environment variable "
                    f"{env_var_name!r}"
                )
        else:
            token_provider = _build_delegated_teams_token_provider(provider_config)
        if token_provider is None:
            resolved_token_provider = None
        elif hasattr(token_provider, "get_bearer_token"):
            resolved_token_provider = token_provider.get_bearer_token
        else:
            resolved_token_provider = token_provider
        return GraphTeamsService(
            SqliteTeamsRecordsRepository(self.config.teams_message_store_path),
            token=token,
            token_provider=resolved_token_provider,
            api_base_url=provider_config.teams_graph_api_base_url,
            poll_limit=provider_config.teams_poll_limit,
            history_page_cap=provider_config.teams_history_page_cap,
            history_deadline_seconds=provider_config.teams_history_deadline_seconds,
            history_request_timeout_seconds=(provider_config.teams_history_request_timeout_seconds),
            timeout_seconds=provider_config.teams_timeout_seconds,
            retry_count=provider_config.teams_retry_count,
            retry_base_backoff_seconds=provider_config.teams_retry_base_backoff_seconds,
            retry_max_backoff_seconds=provider_config.teams_retry_max_backoff_seconds,
            allowed_channel_targets_provider=lambda: channel_targets,
            user_chat_cache=self.runtime.teams_user_chat_cache,
            user_chat_cache_namespace=_teams_user_cache_namespace(
                provider_config,
                directory_path=directory_path,
            ),
            defer_retry_after=True,
        )

    def _resolve_direct_message_sender_refs_by_id(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        sender_refs: set[str],
        directory_path: str,
        poll_scope: str,
    ) -> dict[str, set[str]]:
        sender_refs_by_id: dict[str, set[str]] = {}
        for raw_ref in sorted(sender_refs):
            raw_ref_text = str(raw_ref or "").strip().strip("/")
            sender_ref = _normalize_direct_message_sender_ref(raw_ref)
            if not sender_ref:
                continue
            aliases = {sender_ref}
            if raw_ref_text:
                aliases.add(raw_ref_text)
            if not _direct_message_sender_ref_needs_resolution(sender_ref):
                _add_direct_message_sender_ref_aliases(
                    sender_refs_by_id,
                    sender_id=sender_ref,
                    aliases=aliases,
                )
                continue
            attempt = self._run_graph_poll_target(
                poll_scope=poll_scope,
                target_kind="user",
                target=sender_ref,
                operation=lambda ref=sender_ref: service.resolve_user_id(client, ref),
            )
            if not attempt.succeeded:
                continue
            resolved_id = attempt.value
            if resolved_id:
                _add_direct_message_sender_ref_aliases(
                    sender_refs_by_id,
                    sender_id=resolved_id,
                    aliases={*aliases, resolved_id},
                )
                continue
            logger.warning(
                "Could not resolve Teams direct-message sender user ref %r for directory=%s",
                raw_ref,
                directory_path,
            )
        return sender_refs_by_id

    def _run_graph_poll_target(
        self,
        *,
        poll_scope: str,
        target_kind: str,
        target: str,
        operation: Callable[[], Any],
    ) -> _GraphPollAttempt:
        target_key = _teams_poll_health_key(poll_scope, target_kind, target)
        auth_key = _teams_poll_health_key(poll_scope, "auth", "credential-set")
        now = datetime.now(UTC)
        for health_key in (auth_key, target_key):
            health = self.store.get_poll_health(health_key)
            if health is not None and health.next_attempt_at is not None:
                next_attempt_at = _coerce_utc(health.next_attempt_at)
                if next_attempt_at > now:
                    with self._cycle_state_lock:
                        self._cycle_targets_skipped_backoff += 1
                    logger.debug(
                        "Skipping Teams Graph poll target due to backoff target=%s next_attempt_at=%s",
                        health_key,
                        next_attempt_at.isoformat(),
                    )
                    return _GraphPollAttempt(attempted=False, succeeded=False)

        previous = self.store.get_poll_health(target_key)
        try:
            value = operation()
        except Exception as exc:
            classification = _classify_graph_poll_error(exc)
            if classification is None:
                raise
            error_class, retry_after_seconds, directory_scoped = classification
            failure_key = auth_key if directory_scoped else target_key
            if directory_scoped:
                with self._cycle_state_lock:
                    if auth_key in self._cycle_auth_failures:
                        state = None
                    else:
                        state = self._record_graph_poll_failure(
                            failure_key=failure_key,
                            error_class=error_class,
                            error_message=str(exc),
                            retry_after_seconds=retry_after_seconds,
                        )
                        self._cycle_auth_failures.add(auth_key)
            else:
                state = self._record_graph_poll_failure(
                    failure_key=failure_key,
                    error_class=error_class,
                    error_message=str(exc),
                    retry_after_seconds=retry_after_seconds,
                )
            with self._cycle_state_lock:
                self._cycle_targets_failed += 1
            if state is None:
                logger.warning(
                    "Teams Graph poll target failed target=%s error=%s "
                    "credential_failure_already_recorded=true",
                    failure_key,
                    error_class,
                )
            else:
                selected_backoff_seconds = (
                    (state.next_attempt_at - state.last_failed_at).total_seconds()
                    if state.next_attempt_at is not None and state.last_failed_at is not None
                    else 0.0
                )
                logger.warning(
                    "Teams Graph poll target failed target=%s failures=%d "
                    "next_attempt_at=%s error=%s backoff_seconds=%.3f",
                    failure_key,
                    state.consecutive_failures,
                    state.next_attempt_at.isoformat() if state.next_attempt_at else None,
                    error_class,
                    selected_backoff_seconds,
                )
            return _GraphPollAttempt(attempted=True, succeeded=False)

        succeeded_at = datetime.now(UTC)
        self.store.record_poll_success(target_key, succeeded_at=succeeded_at)
        with self._cycle_state_lock:
            auth_health = self.store.get_poll_health(auth_key)
            auth_failed_this_cycle = auth_key in self._cycle_auth_failures
            if auth_health is not None and not auth_failed_this_cycle:
                self.store.record_poll_success(auth_key, succeeded_at=succeeded_at)
        if previous is not None and previous.consecutive_failures > 0:
            with self._cycle_state_lock:
                self._cycle_targets_recovered += 1
            logger.info("Teams Graph poll target recovered target=%s", target_key)
        return _GraphPollAttempt(attempted=True, succeeded=True, value=value)

    def _record_graph_poll_failure(
        self,
        *,
        failure_key: str,
        error_class: str,
        error_message: str,
        retry_after_seconds: float | None,
    ) -> TeamsInboxPollHealth:
        previous_failure = self.store.get_poll_health(failure_key)
        failure_count = (
            previous_failure.consecutive_failures if previous_failure is not None else 0
        ) + 1
        if _graph_poll_error_uses_transient_backoff(error_class):
            initial_seconds = self.worker_config.target_transient_backoff_initial_seconds
            max_seconds = self.worker_config.target_transient_backoff_max_seconds
        else:
            initial_seconds = self.worker_config.target_backoff_initial_seconds
            max_seconds = self.worker_config.target_backoff_max_seconds
        computed_backoff = _compute_target_backoff_seconds(
            failure_count=failure_count,
            initial_seconds=initial_seconds,
            max_seconds=max_seconds,
        )
        backoff_seconds = max(computed_backoff, retry_after_seconds or 0.0)
        failed_at = datetime.now(UTC)
        return self.store.record_poll_failure(
            failure_key,
            next_attempt_at=failed_at + timedelta(seconds=backoff_seconds),
            error_class=error_class,
            error_message=error_message,
            failed_at=failed_at,
        )

    def _poll_channel(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        channel_target: str,
        budget: Any | None = None,
        direct_message_sender_refs_by_id: dict[str, set[str]] | None = None,
        store_unmatched_direct_messages: bool = False,
        trigger_snapshot: _TeamsTriggerConfigSnapshot,
    ) -> int:
        target = parse_teams_target(channel_target)
        cursor_state = self.store.get_cursor_state(channel_target)
        candidates: list[TeamsInboxMessage] = []
        if target.kind == "chat":
            iterator = service._iter_chat_messages(
                client,
                chat_id=target.chat_id or "",
                budget=budget,
            )
        else:
            iterator = service._iter_channel_messages(
                client,
                team_id=target.team_id or "",
                channel_id=target.channel_id or "",
                budget=budget,
            )
        for message_payload in iterator:
            candidates.append(
                self._normalize_message(service._to_envelope(message_payload, target=target))
            )
            for reply_payload in message_payload.get("replies") or []:
                if not isinstance(reply_payload, dict):
                    continue
                candidates.append(
                    self._normalize_message(service._to_envelope(reply_payload, target=target))
                )
        return self._ingest_message_candidates(
            candidates,
            channel_target=channel_target,
            cursor_state=cursor_state,
            direct_message_sender_refs_by_id=direct_message_sender_refs_by_id,
            store_unmatched_direct_messages=store_unmatched_direct_messages,
            trigger_snapshot=trigger_snapshot,
        )

    def _ingest_message_candidates(
        self,
        candidates: list[TeamsInboxMessage],
        *,
        channel_target: str,
        cursor_state: Any | None,
        direct_message_sender_refs_by_id: dict[str, set[str]] | None = None,
        store_unmatched_direct_messages: bool = False,
        trigger_snapshot: _TeamsTriggerConfigSnapshot,
    ) -> int:
        discovered: list[TeamsInboxMessage] = []
        latest_seen_message: TeamsInboxMessage | None = None
        candidates.sort(key=lambda item: (item.created_at, item.cursor))
        for message in candidates:
            if not message.message_id or message.metadata.get("source") != "teams_graph_api":
                continue
            if (
                not _message_is_after_cursor_state(message, cursor_state)
                and self.store.get_message(message.cursor) is not None
            ):
                continue
            latest_seen_message = message
            if direct_message_sender_refs_by_id is not None:
                message = _mark_direct_message(message)
                direct_sender_refs = direct_message_sender_refs_by_id.get(
                    message.author_id.strip(),
                    set(),
                )
                if not direct_sender_refs and not store_unmatched_direct_messages:
                    continue
                if direct_sender_refs:
                    message = _mark_direct_message(
                        message,
                        sender_refs=direct_sender_refs,
                    )
            if self._is_known_status_post(message.cursor):
                continue
            if self.store.add_message_if_new(message):
                discovered.append(message)
                self._publish_matching_trigger_events(
                    message,
                    trigger_snapshot=trigger_snapshot,
                )
        latest = (
            latest_seen_message
            if direct_message_sender_refs_by_id is not None
            else discovered[-1]
            if discovered
            else latest_seen_message
        )
        if latest is not None:
            self.store.set_cursor(
                channel_target,
                latest.cursor,
                created_at=latest.created_at,
            )
        return len(discovered)

    def _publish_matching_trigger_events(
        self,
        message: TeamsInboxMessage,
        *,
        trigger_snapshot: _TeamsTriggerConfigSnapshot,
    ) -> None:
        event_queue = trigger_snapshot.event_queue
        if event_queue is None or not trigger_snapshot.matchers:
            return

        message_matched = False
        for captured_matcher in trigger_snapshot.matchers:
            try:
                events = captured_matcher.events_for(message)
            except Exception:
                with self._cycle_state_lock:
                    self._cycle_publication_failures += 1
                logger.warning(
                    "Failed to materialize direct Teams trigger event trigger_id=%s "
                    "trigger_type=%s message_cursor=%s revision=%s",
                    captured_matcher.trigger_id,
                    captured_matcher.trigger_type,
                    message.cursor,
                    trigger_snapshot.revision,
                    exc_info=True,
                )
                continue

            if events:
                message_matched = True
            for event in events:
                try:
                    validate_output_mapping_keys(
                        event.payload,
                        context=(
                            f"{captured_matcher.trigger_type} "
                            f"{captured_matcher.trigger_id} runtime outputs"
                        ),
                    )
                    publish_result = event_queue.publish_result(
                        event,
                        config_revision=trigger_snapshot.revision,
                        directory_path=captured_matcher.directory_path,
                    )
                    if publish_result.record.event_id != event.event_id:
                        raise RuntimeError(
                            "trigger queue returned a record for a different deterministic event id"
                        )
                except Exception:
                    with self._cycle_state_lock:
                        self._cycle_publication_failures += 1
                    logger.warning(
                        "Failed to publish direct Teams trigger event trigger_id=%s "
                        "trigger_type=%s event_id=%s message_cursor=%s revision=%s",
                        captured_matcher.trigger_id,
                        captured_matcher.trigger_type,
                        event.event_id,
                        message.cursor,
                        trigger_snapshot.revision,
                        exc_info=True,
                    )
                    continue

                with self._cycle_state_lock:
                    if publish_result.created:
                        self._cycle_events_created += 1
                    else:
                        self._cycle_events_deduplicated += 1

        if message_matched:
            with self._cycle_state_lock:
                self._cycle_messages_matched += 1

    def _poll_direct_chats(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        sender_refs_by_id: dict[str, set[str]],
        allowlisted_chat_targets: set[str],
        trigger_snapshot: _TeamsTriggerConfigSnapshot,
    ) -> _TeamsDirectChatsPollResult:
        chats_polled: set[str] = set()
        new_messages_discovered = 0
        for chat_payload in service._iter_one_on_one_chats(
            client,
            budget=service.new_pagination_budget(),
        ):
            chat_type = str(chat_payload.get("chatType") or "oneOnOne").strip()
            if chat_type != "oneOnOne":
                continue
            chat_id = str(chat_payload.get("id") or "").strip()
            if not chat_id:
                continue
            chat_target = f"chat:{chat_id}"
            chats_polled.add(chat_target)
            new_messages_discovered += self._poll_channel(
                client,
                service=service,
                channel_target=chat_target,
                budget=service.new_pagination_budget(),
                direct_message_sender_refs_by_id=sender_refs_by_id,
                store_unmatched_direct_messages=chat_target in allowlisted_chat_targets,
                trigger_snapshot=trigger_snapshot,
            )
        return _TeamsDirectChatsPollResult(
            targets_polled=chats_polled,
            new_messages_discovered=new_messages_discovered,
        )

    def _normalize_message(self, envelope: TeamsMessageEnvelope) -> TeamsInboxMessage:
        metadata = dict(envelope.metadata)
        metadata["attachments"] = _sanitize_teams_attachments(metadata.get("attachments"))
        if isinstance(metadata.get("message"), dict):
            safe_message = dict(metadata["message"])
            safe_message["attachments"] = _sanitize_teams_attachments(
                safe_message.get("attachments")
            )
            safe_message["hostedContents"] = _sanitize_teams_attachments(
                safe_message.get("hostedContents")
            )
            metadata["message"] = safe_message
        raw_message = metadata.get("message")
        author_name = (
            _extract_teams_author_name(raw_message)
            or str(metadata.get("author_name") or "").strip()
        )
        author_id = str(metadata.get("author_id", envelope.author_id)).strip() or envelope.author_id
        raw_team_id = metadata.get("team_id", envelope.team_id)
        team_id = str(raw_team_id or envelope.team_id or "").strip()
        raw_channel_id = metadata.get("channel_id", envelope.channel_id)
        channel_id = str(raw_channel_id or envelope.channel_id or "").strip()
        raw_chat_id = metadata.get("chat_id", envelope.chat_id)
        chat_id = str(raw_chat_id).strip() if raw_chat_id is not None else ""
        chat_id = chat_id or None
        raw_thread_id = metadata.get("thread_id", envelope.thread_id)
        thread_id = str(raw_thread_id or envelope.thread_id or "").strip() or None
        target = _target_from_envelope(envelope, metadata=metadata)
        metadata.setdefault("target", target)
        metadata.setdefault("target_type", "chat" if chat_id else "channel")
        metadata.setdefault("team_id", team_id)
        metadata.setdefault("channel_id", channel_id)
        metadata.setdefault("chat_id", chat_id)
        metadata.setdefault("thread_id", thread_id)
        metadata.setdefault("author_id", author_id)
        if author_name:
            metadata["author_name"] = author_name
        return TeamsInboxMessage(
            cursor=envelope.cursor,
            message_id=envelope.message_id,
            team_id=team_id,
            channel_id=channel_id,
            chat_id=chat_id,
            thread_id=thread_id,
            author_id=author_id,
            author_name=author_name or author_id,
            content=envelope.content,
            created_at=envelope.created_at,
            metadata=metadata,
        )

    def _is_known_status_post(self, cursor: str) -> bool:
        known = self.runtime.teams_messages.get(cursor)
        if known is None:
            return False
        return str(known.metadata.get("source") or "").strip() == "teams_graph_api_status"


def _sanitize_teams_attachments(raw_attachments: object) -> list[dict[str, Any]]:
    if not isinstance(raw_attachments, list):
        return []
    sanitized: list[dict[str, Any]] = []
    for raw in raw_attachments:
        if not isinstance(raw, dict):
            continue
        sanitized.append(
            {key: value for key, value in raw.items() if key not in _ATTACHMENT_SECRET_KEYS}
        )
    return sanitized


def _worker_global_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "teams-ingest" / "config.json"


def _trigger_config_revision(config: TriggerOrchestratorWorkerGlobalConfig) -> str:
    encoded = json.dumps(
        config.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_trigger_cursor_state(path: Path, trigger_id: str) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"trigger cursor file {path} must contain a JSON object")
    state = raw.get(trigger_id)
    return dict(state) if isinstance(state, dict) else {}


def _teams_user_cache_namespace(config: GatewayConfig, *, directory_path: str) -> str:
    if config.teams_backend == "delegated_graph_api":
        return (
            f"delegated:{config.teams_delegated_identity_id or 'default'}:"
            f"{directory_path or 'root'}"
        )
    return f"graph_api:{config.teams_graph_access_token_env}:{directory_path or 'root'}"


def _normalize_direct_message_sender_ref(value: str) -> str:
    normalized = str(value or "").strip().strip("/")
    if normalized.startswith("user:"):
        normalized = normalized.removeprefix("user:").strip()
    return normalized


def _direct_message_sender_ref_needs_resolution(value: str) -> bool:
    return "@" in value


def _add_direct_message_sender_ref_aliases(
    sender_refs_by_id: dict[str, set[str]],
    *,
    sender_id: str,
    aliases: set[str],
) -> None:
    normalized_sender_id = _normalize_direct_message_sender_ref(sender_id)
    if not normalized_sender_id:
        return
    sender_aliases = sender_refs_by_id.setdefault(normalized_sender_id, set())
    sender_aliases.add(normalized_sender_id)
    for alias in aliases:
        normalized_alias = str(alias or "").strip()
        if normalized_alias:
            sender_aliases.add(normalized_alias)


def _load_teams_ingest_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> TeamsIngestWorkerGlobalConfig:
    base_payload = TeamsIngestWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": (
                base_config.runtime_dir if base_config is not None else Path("runtime")
            ),
        }
    ).model_dump(mode="json")
    path = _worker_global_config_path(project_dir)
    if not path.exists():
        return TeamsIngestWorkerGlobalConfig.model_validate(base_payload)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return TeamsIngestWorkerGlobalConfig.model_validate({**base_payload, **raw})


def _build_trigger_wake_notifier(
    config: GatewayConfig,
    *,
    project_dir: Path,
) -> Callable[[], bool]:
    try:
        trigger_config = load_trigger_orchestrator_worker_global_config(
            project_dir,
            base_config=config,
        )
        lock_path = trigger_config.lock_path
    except Exception:
        logger.warning(
            "Failed to load trigger orchestrator config for Teams ingest wake notifier; "
            "using default trigger lock path",
            exc_info=True,
        )
        lock_path = TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {"runtime_dir": config.runtime_dir}
        ).lock_path
    if lock_path is None:
        lock_path = config.runtime_dir / "trigger-orchestrator.lock"
    if not lock_path.is_absolute():
        lock_path = project_dir / lock_path
    notifier = HostWakeNotifier(wake_socket_path_for_lock(lock_path))
    return notifier.notify


def build_teams_ingest_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
    trigger_wake_notifier: Callable[[], bool] | None = None,
    effect_wake_notifier: Callable[[], bool] | None = None,
    event_queue: _TriggerEventQueue | None = None,
) -> TeamsIngestWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = _load_teams_ingest_worker_global_config(
        resolved_project_dir,
        base_config=config,
    )
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
        }
    )
    runtime = build_runtime(effective_config)
    return TeamsIngestWorker(
        config=effective_config,
        worker_config=worker_config,
        runtime=runtime,
        project_dir=resolved_project_dir,
        trigger_wake_notifier=trigger_wake_notifier,
        effect_wake_notifier=effect_wake_notifier,
        event_queue=event_queue,
    )


def _teams_graph_poll_scope(provider_config: GatewayConfig, *, directory_path: str) -> str:
    identity = "\0".join(
        (
            DirectoryPath(segments=directory_path).as_posix(),
            provider_config.teams_backend,
            provider_config.teams_graph_api_base_url.rstrip("/").casefold(),
            provider_config.teams_graph_access_token_env.strip(),
            (provider_config.teams_delegated_identity_id or "").strip(),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"{DirectoryPath(segments=directory_path).as_posix()}:{digest}"


def _teams_poll_health_key(poll_scope: str, target_kind: str, target: str) -> str:
    return "|".join(
        (
            str(poll_scope).strip(),
            str(target_kind).strip().casefold(),
            str(target).strip().strip("/"),
        )
    )


def _one_on_one_chat_targets(payloads: object) -> set[str]:
    if not isinstance(payloads, list):
        return set()
    targets: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        if str(payload.get("chatType") or "oneOnOne").strip() != "oneOnOne":
            continue
        chat_id = str(payload.get("id") or "").strip()
        if chat_id:
            targets.add(f"chat:{chat_id}")
    return targets


def _classify_graph_poll_error(
    exc: Exception,
) -> tuple[str, float | None, bool] | None:
    if isinstance(exc, TeamsAuthenticationError):
        return ("authentication", None, True)
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        retry_after = _retry_after_seconds(exc.response)
        if status_code == 401:
            return ("http_401", retry_after, True)
        if 400 <= status_code <= 599:
            return (f"http_{status_code}", retry_after, False)
    if isinstance(exc, httpx.TimeoutException):
        return ("timeout", None, False)
    if isinstance(exc, httpx.TransportError):
        return ("transport", None, False)
    if isinstance(exc, TimeoutError):
        return ("retry_budget_timeout", None, False)
    return None


def _compute_target_backoff_seconds(
    *,
    failure_count: int,
    initial_seconds: float,
    max_seconds: float,
) -> float:
    initial = max(0.0, float(initial_seconds))
    maximum = max(initial, float(max_seconds))
    exponent = max(0, int(failure_count) - 1)
    if initial == 0.0 or initial >= maximum:
        return min(initial, maximum)
    exponent_until_cap = max(0, math.ceil(math.log2(maximum) - math.log2(initial)))
    if exponent >= exponent_until_cap:
        return maximum
    return min(maximum, math.ldexp(initial, exponent))


def _graph_poll_error_uses_transient_backoff(error_class: str) -> bool:
    if error_class in {"timeout", "transport", "retry_budget_timeout"}:
        return True
    if not error_class.startswith("http_"):
        return False
    try:
        status_code = int(error_class.removeprefix("http_"))
    except ValueError:
        return False
    return 500 <= status_code <= 599


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw_value = str(response.headers.get("Retry-After") or "").strip()
    if not raw_value:
        return None
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError, OverflowError):
        return None
    retry_at = _coerce_utc(retry_at)
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _notification_message_locator(resource: str) -> tuple[str, str | None]:
    normalized = unquote(str(resource or "").strip())
    reply_match = re.search(
        r"/messages(?:\('([^']+)'\)|/([^/]+))/replies(?:\('([^']+)'\)|/([^/]+))/?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if reply_match is not None:
        thread_id = next(value for value in reply_match.groups()[:2] if value)
        message_id = next(value for value in reply_match.groups()[2:] if value)
        return message_id, thread_id
    message_match = re.search(
        r"/messages(?:\('([^']+)'\)|/([^/]+))/?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if message_match is None:
        raise ValueError("Teams notification resource does not identify a message")
    message_id = next(value for value in message_match.groups() if value)
    return message_id, None


def _notification_resource_target(resource: str) -> str:
    normalized = unquote(str(resource or "").strip())
    channel_match = re.search(
        r"(?:^|/)teams(?:\('([^']+)'\)|/([^/]+))/channels"
        r"(?:\('([^']+)'\)|/([^/]+))/messages",
        normalized,
        flags=re.IGNORECASE,
    )
    if channel_match is not None:
        team_id = next(value for value in channel_match.groups()[:2] if value)
        channel_id = next(value for value in channel_match.groups()[2:] if value)
        return f"{team_id}/{channel_id}"
    chat_match = re.search(
        r"(?:^|/)chats(?:\('([^']+)'\)|/([^/]+))/messages",
        normalized,
        flags=re.IGNORECASE,
    )
    if chat_match is not None:
        chat_id = next(value for value in chat_match.groups() if value)
        return f"chat:{chat_id}"
    raise ValueError("Teams notification resource does not identify a target")


def _notification_error_is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, TimeoutError)):
        return True
    if isinstance(exc, TeamsAuthenticationError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code in {401, 404, 408, 409, 425, 429} or status_code >= 500
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Teams ingest worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--log-level", default=os.environ.get("TEAMS_INGEST_LOG_LEVEL", "INFO"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging_level = getattr(logging, str(args.log_level).upper(), None)
    if not isinstance(logging_level, int):
        raise SystemExit(f"Invalid --log-level value: {args.log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    config = load_runtime_gateway_config(runtime_dir=runtime_dir)
    worker = build_teams_ingest_worker(config, project_dir=runtime_dir.parent)
    if args.once:
        result = worker.run_once_with_lock()
        print(
            f"Teams ingest worker cycle complete directories={result.directories_polled} "
            f"channel_targets={result.channel_targets_polled} sessions={result.sessions_seen} "
            f"direct_chats={result.direct_chats_polled} "
            f"new_messages={result.new_messages_discovered} "
            f"messages_matched={result.messages_matched} "
            f"events_created={result.events_created} "
            f"events_deduplicated={result.events_deduplicated} "
            f"publication_failures={result.publication_failures} "
            f"effect_wake_notified={result.effect_wake_notified} "
            f"trigger_wake_notified={result.trigger_wake_notified} "
            f"workflow_config_revision={result.workflow_config_revision} "
            f"targets_skipped_backoff={result.targets_skipped_backoff} "
            f"targets_failed={result.targets_failed} "
            f"targets_recovered={result.targets_recovered} "
            f"notifications_processed={result.notifications_processed} "
            f"sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return
    worker.run_forever()


def _target_from_envelope(envelope: TeamsMessageEnvelope, *, metadata: dict[str, Any]) -> str:
    if metadata.get("target"):
        return normalize_teams_target(str(metadata["target"]))
    if envelope.chat_id:
        return f"chat:{envelope.chat_id}"
    return f"{envelope.team_id}/{envelope.channel_id}"


def _mark_direct_message(
    message: TeamsInboxMessage,
    *,
    sender_refs: set[str] | None = None,
) -> TeamsInboxMessage:
    metadata = dict(message.metadata)
    metadata["target_type"] = "direct_message"
    metadata["chat_type"] = "oneOnOne"
    if sender_refs:
        direct_sender_refs = _metadata_string_set(metadata.get("direct_message_sender_refs"))
        direct_sender_refs.update(str(ref).strip() for ref in sender_refs if str(ref).strip())
        metadata["direct_message_sender_refs"] = sorted(direct_sender_refs)
    raw_message = metadata.get("message")
    if isinstance(raw_message, dict):
        safe_message = dict(raw_message)
        safe_message["target_type"] = "direct_message"
        safe_message["chat_type"] = "oneOnOne"
        metadata["message"] = safe_message
    return message.model_copy(update={"metadata": metadata})


def _metadata_string_set(value: object) -> set[str]:
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _extract_teams_author_name(raw_message: object) -> str:
    if not isinstance(raw_message, dict):
        return ""
    sender = raw_message.get("from")
    if not isinstance(sender, dict):
        return ""
    for source_key in ("user", "application", "device"):
        source = sender.get(source_key)
        if not isinstance(source, dict):
            continue
        candidate = str(source.get("displayName") or source.get("id") or "").strip()
        if candidate:
            return candidate
    return ""


def _message_is_after_cursor_state(
    message: TeamsInboxMessage,
    cursor_state: Any,
) -> bool:
    if cursor_state is None:
        return True
    if message.cursor == cursor_state.cursor:
        return False
    if cursor_state.created_at is not None:
        return (message.created_at, message.cursor) > (cursor_state.created_at, cursor_state.cursor)
    return message.cursor > cursor_state.cursor


def _directory_is_within_scope(directory_path: str | None, scope_directory_path: str) -> bool:
    candidate_segments = DirectoryPath(segments=directory_path or "root").segments
    scope_segments = DirectoryPath(segments=scope_directory_path).segments
    return candidate_segments[: len(scope_segments)] == scope_segments
