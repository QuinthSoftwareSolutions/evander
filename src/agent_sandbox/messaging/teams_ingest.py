from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from agent_sandbox.bootstrap import load_runtime_gateway_config
from agent_sandbox.config import GatewayConfig, TeamsIngestWorkerGlobalConfig
from agent_sandbox.events.models import materialize_node_definition
from agent_sandbox.events.provider_models import TeamsDirectMessageTriggerDefinition
from agent_sandbox.repositories.cursors import (
    JsonTeamsInboxCursorRepository,
    TeamsInboxCursorRepository,
)
from agent_sandbox.models import DirectoryPath, SessionManifest, TeamsMessageEnvelope, ToolName
from agent_sandbox.orchestration.host_polling import HostFileLockManager, HostSleeper
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
from agent_sandbox.repositories.records import JsonTeamsRecordsRepository
from agent_sandbox.services import (
    GraphTeamsService,
    effective_teams_channel_targets,
    normalize_teams_target,
    parse_teams_target,
)
from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage
from agent_sandbox.workflow.runtime_config import load_trigger_orchestrator_worker_global_config

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
    next_sleep_seconds: float


class TeamsIngestWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: TeamsIngestWorkerGlobalConfig,
        runtime: RuntimeContext,
        store: TeamsInboxCursorRepository | None = None,
        project_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
        self.session_repository = runtime.session_repository
        self.store = store or JsonTeamsInboxCursorRepository(
            config.runtime_dir / "teams-inbox-store.json"
        )
        self._kernel = PollingOrchestratorKernel(
            config=PollingKernelConfig(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                lock_path=self.worker_config.lock_path,
            ),
            lock_manager=HostFileLockManager(
                busy_error_text="Teams ingest worker already running"
            ),
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
        directories = self._directories_to_scan(active_sessions)
        channel_targets_polled = 0
        direct_chats_polled = 0
        direct_sender_ids = self._direct_message_sender_ids()
        for directory_path in directories:
            channel_targets = self._channel_targets_for_directory(
                directory_path,
                active_sessions=active_sessions,
            )
            if not channel_targets and not direct_sender_ids:
                continue
            channel_targets_polled += len(channel_targets)
            direct_chats_polled += self._poll_directory(
                directory_path,
                channel_targets=channel_targets,
                direct_sender_ids=direct_sender_ids,
            )
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
        return TeamsIngestCycleResult(
            directories_polled=len(directories),
            channel_targets_polled=channel_targets_polled,
            direct_chats_polled=direct_chats_polled,
            sessions_seen=len(matching_sessions),
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
                "direct_chats=%d sessions=%d sleep_seconds=%.3f"
            ),
            result.directories_polled,
            result.channel_targets_polled,
            result.direct_chats_polled,
            result.sessions_seen,
            result.next_sleep_seconds,
        )

    def _log_cycle_exception(self, _exc: Exception) -> None:
        logger.exception("Teams ingest worker cycle failed")

    def _directories_to_scan(self, active_sessions: list[SessionManifest]) -> list[str]:
        if self.worker_config.directories:
            return list(self.worker_config.directories)
        directories = {
            DirectoryPath(segments=session.directory_path).as_posix() for session in active_sessions
        }
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
            for normalized in effective_teams_channel_targets(session, skip_user_targets=True)
        }
        return sorted(targets)

    def _direct_message_sender_ids(self) -> set[str]:
        try:
            trigger_config = load_trigger_orchestrator_worker_global_config(
                self.project_dir,
                base_config=self.config,
            )
        except Exception:
            logger.exception("Failed to load trigger orchestrator config for Teams DM ingest")
            return set()

        sender_ids: set[str] = set()
        for trigger in trigger_config.triggers:
            if trigger.type != "trigger:teams:direct-message":
                continue
            materialized = materialize_node_definition(trigger)
            if not isinstance(materialized, TeamsDirectMessageTriggerDefinition):
                continue
            sender_ids.update(
                value.strip() for value in materialized.sender_user_ids if value.strip()
            )
        return sender_ids

    def _poll_directory(
        self,
        directory_path: str,
        *,
        channel_targets: list[str],
        direct_sender_ids: set[str],
    ) -> int:
        provider_config = self.runtime.provider_config.resolve_for_directory(directory_path)
        if provider_config.teams_backend not in {"graph_api", "delegated_graph_api"}:
            logger.debug(
                "Skipping Teams ingest for directory=%s because teams_backend=%s",
                directory_path,
                provider_config.teams_backend,
            )
            return 0
        service = self._build_graph_service(
            provider_config=provider_config,
            channel_targets=channel_targets,
        )
        direct_chats_polled = 0
        with httpx.Client(timeout=provider_config.teams_timeout_seconds) as client:
            for channel_target in channel_targets:
                self._poll_channel(
                    client,
                    service=service,
                    channel_target=channel_target,
                    budget=service.new_pagination_budget(),
                )
            if direct_sender_ids:
                direct_chats_polled = self._poll_direct_chats(
                    client,
                    service=service,
                    sender_ids=direct_sender_ids,
                )
        return direct_chats_polled

    def _build_graph_service(
        self,
        *,
        provider_config: GatewayConfig,
        channel_targets: list[str],
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
            JsonTeamsRecordsRepository(self.config.teams_message_store_path),
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
        )

    def _poll_channel(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        channel_target: str,
        budget: Any | None = None,
        direct_message_sender_ids: set[str] | None = None,
    ) -> None:
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
            if direct_message_sender_ids is not None:
                message = _mark_direct_message(message)
                if message.author_id.strip() not in direct_message_sender_ids:
                    continue
            if self._is_known_status_post(message.cursor):
                continue
            if self.store.add_message_if_new(message):
                discovered.append(message)
        latest = (
            latest_seen_message
            if direct_message_sender_ids is not None
            else discovered[-1] if discovered else latest_seen_message
        )
        if latest is not None:
            self.store.set_cursor(
                channel_target,
                latest.cursor,
                created_at=latest.created_at,
            )

    def _poll_direct_chats(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        sender_ids: set[str],
    ) -> int:
        chats_polled = 0
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
            chats_polled += 1
            self._poll_channel(
                client,
                service=service,
                channel_target=f"chat:{chat_id}",
                budget=service.new_pagination_budget(),
                direct_message_sender_ids=sender_ids,
            )
        return chats_polled

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


def build_teams_ingest_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
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


def _mark_direct_message(message: TeamsInboxMessage) -> TeamsInboxMessage:
    metadata = dict(message.metadata)
    metadata["target_type"] = "direct_message"
    metadata["chat_type"] = "oneOnOne"
    raw_message = metadata.get("message")
    if isinstance(raw_message, dict):
        safe_message = dict(raw_message)
        safe_message["target_type"] = "direct_message"
        safe_message["chat_type"] = "oneOnOne"
        metadata["message"] = safe_message
    return message.model_copy(update={"metadata": metadata})


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
