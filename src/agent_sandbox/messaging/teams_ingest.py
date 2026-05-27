from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from agent_sandbox.bootstrap import load_runtime_gateway_config
from agent_sandbox.config import (
    GatewayConfig,
    TeamsIngestWorkerGlobalConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from agent_sandbox.events.models import materialize_node_definition
from agent_sandbox.events.provider_models import TeamsDirectMessageTriggerDefinition
from agent_sandbox.repositories.cursors import (
    JsonTeamsInboxCursorRepository,
    TeamsInboxCursorRepository,
)
from agent_sandbox.models import DirectoryPath, SessionManifest, TeamsMessageEnvelope, ToolName
from agent_sandbox.orchestration.host_polling import (
    HostFileLockManager,
    HostSleeper,
    HostWakeNotifier,
    wake_socket_path_for_lock,
)
from agent_sandbox.orchestration.kernels import (
    PollingKernelConfig,
    PollingOrchestratorKernel,
    compute_next_sleep_seconds,
)
from agent_sandbox.runtime import (
    RuntimeContext,
    _build_delegated_teams_token_provider,
    build_runtime,
)
from agent_sandbox.repositories.records import SqliteTeamsRecordsRepository, teams_records_db_path
from agent_sandbox.services import (
    GraphTeamsService,
    normalize_teams_target,
    parse_teams_target,
)
from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage
from agent_sandbox.workflow.runtime_config import load_trigger_orchestrator_worker_global_config
from agent_sandbox.workflow.node_state import workflow_node_is_enabled

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


@dataclass(frozen=True)
class _TeamsDirectoryPollResult:
    direct_chats_polled: int
    new_messages_discovered: int


@dataclass(frozen=True)
class _TeamsDirectChatsPollResult:
    targets_polled: set[str]
    new_messages_discovered: int


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
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
        self.session_repository = runtime.session_repository
        self.store = store or JsonTeamsInboxCursorRepository(
            config.runtime_dir / "teams-inbox-store.json"
        )
        self._trigger_wake_notifier = trigger_wake_notifier
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
        now = datetime.now(UTC)
        active_sessions = self.runtime.session_repository.list_active_sessions(now=now)
        channel_targets_polled = 0
        direct_chats_polled = 0
        new_messages_discovered = 0
        direct_sender_refs = self._direct_message_sender_refs()
        directories = self._directories_to_scan(
            active_sessions,
            include_default_directory=bool(direct_sender_refs),
        )
        for directory_path in directories:
            channel_targets = self._channel_targets_for_directory(
                directory_path,
                active_sessions=active_sessions,
            )
            if not channel_targets and not direct_sender_refs:
                continue
            channel_targets_polled += len(channel_targets)
            directory_result = self._poll_directory(
                directory_path,
                channel_targets=channel_targets,
                direct_sender_refs=direct_sender_refs,
            )
            direct_chats_polled += directory_result.direct_chats_polled
            new_messages_discovered += directory_result.new_messages_discovered
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
        trigger_wake_notified = self._notify_trigger_orchestrator_if_needed(new_messages_discovered)
        return TeamsIngestCycleResult(
            directories_polled=len(directories),
            channel_targets_polled=channel_targets_polled,
            direct_chats_polled=direct_chats_polled,
            sessions_seen=len(matching_sessions),
            new_messages_discovered=new_messages_discovered,
            trigger_wake_notified=trigger_wake_notified,
            next_sleep_seconds=next_sleep_seconds,
        )

    def _log_start(self) -> None:
        logger.info(
            "Teams ingest worker started poll_interval=%.3f jitter=%.3f backoff=%.3f lock=%s",
            self.worker_config.poll_interval_seconds,
            self.worker_config.jitter_seconds,
            self.worker_config.backoff_seconds,
            self.worker_config.lock_path,
        )

    def _log_cycle_success(self, result: TeamsIngestCycleResult) -> None:
        logger.debug(
            (
                "Teams ingest worker cycle complete directories=%d channel_targets=%d "
                "direct_chats=%d sessions=%d new_messages=%d "
                "trigger_wake_notified=%s sleep_seconds=%.3f"
            ),
            result.directories_polled,
            result.channel_targets_polled,
            result.direct_chats_polled,
            result.sessions_seen,
            result.new_messages_discovered,
            result.trigger_wake_notified,
            result.next_sleep_seconds,
        )

    def _log_cycle_exception(self, _exc: Exception) -> None:
        logger.exception("Teams ingest worker cycle failed")

    def _notify_trigger_orchestrator_if_needed(self, new_messages_discovered: int) -> bool:
        if new_messages_discovered <= 0 or self._trigger_wake_notifier is None:
            return False
        try:
            return self._trigger_wake_notifier()
        except Exception:
            logger.warning("Failed to notify trigger orchestrator wake socket", exc_info=True)
            return False

    def _directories_to_scan(
        self,
        active_sessions: list[SessionManifest],
        *,
        include_default_directory: bool = False,
    ) -> list[str]:
        if self.worker_config.directories:
            return list(self.worker_config.directories)
        directories = {
            DirectoryPath(segments=session.directory_path).as_posix() for session in active_sessions
        }
        if include_default_directory and not directories:
            directories.add("root")
        return sorted(directories)

    def _channel_targets_for_directory(
        self,
        directory_path: str,
        *,
        active_sessions: list[SessionManifest],
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
        return sorted(targets)

    def _direct_message_sender_refs(self) -> set[str]:
        try:
            trigger_config = load_trigger_orchestrator_worker_global_config(
                self.project_dir,
                base_config=self.config,
            )
        except Exception:
            logger.exception("Failed to load trigger orchestrator config for Teams DM ingest")
            return set()

        sender_refs: set[str] = set()
        for trigger in trigger_config.triggers:
            if not workflow_node_is_enabled(trigger):
                continue
            if trigger.type != "trigger:teams:direct-message":
                continue
            materialized = materialize_node_definition(trigger)
            if not isinstance(materialized, TeamsDirectMessageTriggerDefinition):
                continue
            sender_refs.update(
                value.strip() for value in materialized.sender_user_ids if value.strip()
            )
        return sender_refs

    def _poll_directory(
        self,
        directory_path: str,
        *,
        channel_targets: list[str],
        direct_sender_refs: set[str],
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
        direct_chats_polled = 0
        new_messages_discovered = 0
        with httpx.Client(timeout=provider_config.teams_timeout_seconds) as client:
            direct_chat_targets_polled: set[str] = set()
            direct_sender_refs_by_id = self._resolve_direct_message_sender_refs_by_id(
                client,
                service=service,
                sender_refs=direct_sender_refs,
                directory_path=directory_path,
            )
            if direct_sender_refs_by_id:
                direct_chats_result = self._poll_direct_chats(
                    client,
                    service=service,
                    sender_refs_by_id=direct_sender_refs_by_id,
                    allowlisted_chat_targets={
                        target for target in channel_targets if target.startswith("chat:")
                    },
                )
                direct_chat_targets_polled = direct_chats_result.targets_polled
                direct_chats_polled = len(direct_chat_targets_polled)
                new_messages_discovered += direct_chats_result.new_messages_discovered
            for channel_target in channel_targets:
                if channel_target in direct_chat_targets_polled:
                    continue
                new_messages_discovered += self._poll_channel(
                    client,
                    service=service,
                    channel_target=channel_target,
                    budget=service.new_pagination_budget(),
                )
        return _TeamsDirectoryPollResult(
            direct_chats_polled=direct_chats_polled,
            new_messages_discovered=new_messages_discovered,
        )

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
            SqliteTeamsRecordsRepository(
                teams_records_db_path(self.config.teams_message_store_path),
                legacy_json_path=self.config.teams_message_store_path,
            ),
            token=token,
            token_provider=resolved_token_provider,
            api_base_url=provider_config.teams_graph_api_base_url,
            poll_limit=provider_config.teams_poll_limit,
            history_page_cap=provider_config.teams_history_page_cap,
            history_deadline_seconds=provider_config.teams_history_deadline_seconds,
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
        )

    def _resolve_direct_message_sender_refs_by_id(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        sender_refs: set[str],
        directory_path: str,
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
            try:
                resolved_id = service.resolve_user_id(client, sender_ref)
            except Exception:
                logger.warning(
                    "Failed to resolve Teams direct-message sender user ref %r for directory=%s",
                    raw_ref,
                    directory_path,
                    exc_info=True,
                )
                continue
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

    def _poll_channel(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        channel_target: str,
        budget: Any | None = None,
        direct_message_sender_refs_by_id: dict[str, set[str]] | None = None,
        store_unmatched_direct_messages: bool = False,
    ) -> int:
        target = parse_teams_target(channel_target)
        cursor_state = self.store.get_cursor_state(channel_target)
        discovered: list[TeamsInboxMessage] = []
        latest_seen_message: TeamsInboxMessage | None = None
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
        candidates.sort(key=lambda item: (item.created_at, item.cursor))
        for message in candidates:
            if not message.message_id or message.metadata.get("source") != "teams_graph_api":
                continue
            if not _message_is_after_cursor_state(message, cursor_state):
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

    def _poll_direct_chats(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        sender_refs_by_id: dict[str, set[str]],
        allowlisted_chat_targets: set[str],
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
        team_id = str(metadata.get("team_id", envelope.team_id)).strip() or envelope.team_id
        channel_id = (
            str(metadata.get("channel_id", envelope.channel_id)).strip() or envelope.channel_id
        )
        raw_chat_id = metadata.get("chat_id", envelope.chat_id)
        chat_id = str(raw_chat_id).strip() if raw_chat_id is not None else ""
        chat_id = chat_id or None
        thread_id = (
            str(metadata.get("thread_id", envelope.thread_id or "")).strip() or envelope.thread_id
        )
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
    resolved_trigger_wake_notifier = trigger_wake_notifier or _build_trigger_wake_notifier(
        effective_config,
        project_dir=resolved_project_dir,
    )
    return TeamsIngestWorker(
        config=effective_config,
        worker_config=worker_config,
        runtime=runtime,
        project_dir=resolved_project_dir,
        trigger_wake_notifier=resolved_trigger_wake_notifier,
    )


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
            f"trigger_wake_notified={result.trigger_wake_notified} "
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
