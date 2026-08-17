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
from evander.bootstrap import load_runtime_gateway_config
from evander.config import DiscordIngestWorkerGlobalConfig, GatewayConfig
from evander.events.models import materialize_node_definition
from evander.events.provider_models import CommunicationNewMessageTriggerDefinition
from evander.messaging.targets import load_communication_targets_config
from evander.models import DirectoryPath, DiscordMessageEnvelope, SessionManifest, ToolName
from evander.orchestration.host_polling import HostFileLockManager, HostSleeper
from evander.orchestration.kernels import (
    PollingKernelConfig,
    PollingOrchestratorKernel,
    compute_next_sleep_seconds,
)
from evander.runtime import RuntimeContext, build_runtime
from evander.services import DiscordBotApiService
from evander.workflow.node_state import workflow_node_is_enabled
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config

logger = logging.getLogger(__name__)
_PUBLIC_DISCORD_THREAD_TYPES = {10, 11}
_ATTACHMENT_SECRET_KEYS = {
    "authorization",
    "token",
    "access_token",
    "content",
    "content_bytes",
    "content_base64",
    "data",
}


@dataclass(frozen=True)
class DiscordIngestCycleResult:
    directories_polled: int
    sessions_seen: int
    next_sleep_seconds: float


class DiscordIngestWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        project_dir: Path,
        worker_config: DiscordIngestWorkerGlobalConfig,
        runtime: RuntimeContext,
    ) -> None:
        self.config = config
        self.project_dir = project_dir
        self.worker_config = worker_config
        self.runtime = runtime
        self.session_repository = runtime.session_repository
        self._kernel = PollingOrchestratorKernel(
            config=PollingKernelConfig(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                lock_path=self.worker_config.lock_path,
            ),
            lock_manager=HostFileLockManager(
                busy_error_text="Discord ingest worker already running"
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

    def run_once_with_lock(self) -> DiscordIngestCycleResult:
        return self._kernel.run_once_with_lock()

    def run_once(self) -> DiscordIngestCycleResult:
        now = datetime.now(UTC)
        active_sessions = self.runtime.session_repository.list_active_sessions(now=now)
        communication_trigger_targets = self._communication_trigger_discord_targets_by_directory()
        directories = self._directories_to_scan(
            active_sessions,
            include_directories=set(communication_trigger_targets),
        )
        for directory_path in directories:
            channel_ids = self._channel_ids_for_directory(
                directory_path,
                active_sessions=active_sessions,
                communication_trigger_targets=communication_trigger_targets,
            )
            if not channel_ids:
                continue
            self._poll_directory(directory_path, channel_ids=channel_ids)
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
        return DiscordIngestCycleResult(
            directories_polled=len(directories),
            sessions_seen=len(matching_sessions),
            next_sleep_seconds=next_sleep_seconds,
        )

    def _log_start(self) -> None:
        logger.info(
            "Discord ingest worker started poll_interval=%.3f jitter=%.3f backoff=%.3f lock=%s",
            self.worker_config.poll_interval_seconds,
            self.worker_config.jitter_seconds,
            self.worker_config.backoff_seconds,
            self.worker_config.lock_path,
        )

    def _log_cycle_success(self, result: DiscordIngestCycleResult) -> None:
        logger.debug(
            ("Discord ingest worker cycle complete directories=%d sessions=%d sleep_seconds=%.3f"),
            result.directories_polled,
            result.sessions_seen,
            result.next_sleep_seconds,
        )

    def _log_cycle_exception(self, _exc: Exception) -> None:
        logger.exception("Discord ingest worker cycle failed")

    def _directories_to_scan(
        self,
        active_sessions: list[SessionManifest],
        *,
        include_directories: set[str] | None = None,
    ) -> list[str]:
        if self.worker_config.directories:
            return list(self.worker_config.directories)
        directories = {
            DirectoryPath(segments=session.directory_path).as_posix() for session in active_sessions
        }
        directories.update(include_directories or set())
        return sorted(directories)

    def _channel_ids_for_directory(
        self,
        directory_path: str,
        *,
        active_sessions: list[SessionManifest],
        communication_trigger_targets: dict[str, set[str]] | None = None,
    ) -> list[str]:
        channel_ids = {
            channel_id
            for session in active_sessions
            if ToolName.DISCORD_GET_COMMANDS in session.allowed_tools
            and _directory_is_within_scope(session.directory_path, directory_path)
            for channel_id in self.runtime.policy_config.resolved_discord_channel_ids_for_session(
                session,
                capability="read",
            )
        }
        try:
            targets_by_id = load_communication_targets_config(
                runtime_dir=self.config.runtime_dir,
                directory_store=self.runtime.directories,
                directory_path=directory_path,
            ).by_id()
        except Exception:
            targets_by_id = {}
        for session in active_sessions:
            if (
                ToolName.DISCORD_GET_COMMANDS not in session.allowed_tools
                or not _directory_is_within_scope(session.directory_path, directory_path)
            ):
                continue
            refs = (
                session.communication_target_ids
                if session.communication_target_ids is not None
                else session.discord_channel_allowlist
            )
            for ref in refs:
                target = targets_by_id.get(str(ref or "").strip().lower())
                if (
                    target is not None
                    and target.provider == "discord"
                    and "read" in set(target.policy.capabilities)
                    and target.policy.discord_conversation_scope
                    in {"public_threads", "channel_and_public_threads"}
                ):
                    channel_ids.add(_discord_channel_id_from_address(target.address))
        for trigger_directory, trigger_channel_ids in (communication_trigger_targets or {}).items():
            if _directory_is_within_scope(trigger_directory, directory_path):
                channel_ids.update(trigger_channel_ids)
        return sorted(channel_id for channel_id in channel_ids if channel_id)

    def _communication_trigger_discord_targets_by_directory(self) -> dict[str, set[str]]:
        try:
            trigger_config = load_trigger_orchestrator_worker_global_config(
                self.project_dir,
                base_config=self.config,
            )
        except Exception:
            logger.exception(
                "Failed to load trigger orchestrator config for Discord communication ingest"
            )
            return {}

        targets_by_directory: dict[str, set[str]] = {}
        communication_targets_by_directory: dict[str, dict[str, Any]] = {}
        for trigger in trigger_config.triggers:
            if not workflow_node_is_enabled(trigger):
                continue
            if trigger.type != "trigger:communication:new-message":
                continue
            materialized = materialize_node_definition(trigger)
            if not isinstance(materialized, CommunicationNewMessageTriggerDefinition):
                continue
            directory_path = DirectoryPath(
                segments=materialized.directory_path or "root"
            ).as_posix()
            targets_by_id = communication_targets_by_directory.get(directory_path)
            if targets_by_id is None:
                try:
                    targets_by_id = load_communication_targets_config(
                        runtime_dir=self.config.runtime_dir,
                        directory_store=self.runtime.directories,
                        directory_path=directory_path,
                    ).by_id()
                except Exception:
                    logger.warning(
                        "Failed to load communication targets for Discord ingest directory=%s",
                        directory_path,
                        exc_info=True,
                    )
                    targets_by_id = {}
                communication_targets_by_directory[directory_path] = targets_by_id
            for target_id in materialized.communication_target_ids:
                target = targets_by_id.get(target_id)
                if target is None:
                    logger.warning(
                        "Skipping Discord ingest communication target %r for trigger %s "
                        "because it is not defined for directory=%s",
                        target_id,
                        materialized.id,
                        directory_path,
                    )
                    continue
                if target.provider != "discord":
                    continue
                if "read" not in set(target.policy.capabilities):
                    logger.warning(
                        "Skipping Discord ingest communication target %r for trigger %s "
                        "because it does not allow read capability",
                        target_id,
                        materialized.id,
                    )
                    continue
                channel_id = _discord_channel_id_from_address(target.address)
                if channel_id:
                    targets_by_directory.setdefault(directory_path, set()).add(channel_id)
        return targets_by_directory

    def _poll_directory(self, directory_path: str, *, channel_ids: list[str]) -> None:
        provider_config = self.runtime.provider_config.resolve_for_directory(directory_path)
        if provider_config.discord_backend != "bot_api":
            logger.debug(
                "Skipping Discord ingest for directory=%s because discord_backend=%s",
                directory_path,
                provider_config.discord_backend,
            )
            return
        token = os.environ.get(provider_config.discord_bot_token_env, "").strip()
        if not token:
            logger.warning(
                "Skipping Discord ingest for directory=%s because env var %s is empty",
                directory_path,
                provider_config.discord_bot_token_env,
            )
            return
        channel_scope_by_parent = self._discord_conversation_scopes(
            directory_path,
            channel_ids=channel_ids,
        )
        allowed_channel_ids = set(channel_ids)
        adapter = DiscordBotApiService(
            self.runtime.discord_commands,
            token=token,
            api_base_url=provider_config.discord_api_base_url,
            poll_limit=provider_config.discord_poll_limit,
            timeout_seconds=provider_config.discord_timeout_seconds,
            retry_count=provider_config.discord_retry_count,
            retry_base_backoff_seconds=provider_config.discord_retry_base_backoff_seconds,
            retry_max_backoff_seconds=provider_config.discord_retry_max_backoff_seconds,
            allowed_channel_ids_provider=lambda: sorted(allowed_channel_ids),
        )
        with httpx.Client(timeout=provider_config.discord_timeout_seconds) as client:
            thread_ids_by_parent = self._discover_public_threads(
                client,
                adapter=adapter,
                channel_scope_by_parent=channel_scope_by_parent,
            )
            for thread_ids in thread_ids_by_parent.values():
                allowed_channel_ids.update(thread_ids)

            parent_channels_to_poll = {
                channel_id
                for channel_id in channel_ids
                if self.runtime.discord_inbox_service.store.get_thread_binding(channel_id) is None
                if channel_scope_by_parent.get(channel_id, "channel")
                in {"channel", "channel_and_public_threads"}
            }
            for channel_id in sorted(parent_channels_to_poll):
                self._poll_channel(client, adapter=adapter, channel_id=channel_id)
            for parent_channel_id, thread_ids in sorted(thread_ids_by_parent.items()):
                parent_was_initialized = (
                    self.runtime.discord_inbox_service.store.register_thread_parent(
                        parent_channel_id,
                        observed_at=datetime.now(UTC),
                    )
                )
                for thread_id in thread_ids:
                    if parent_was_initialized:
                        self.runtime.discord_inbox_service.store.set_cursor(
                            thread_id,
                            datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        )
                        continue
                    self._poll_channel(
                        client,
                        adapter=adapter,
                        channel_id=thread_id,
                        parent_channel_id=parent_channel_id,
                    )

    def _discord_conversation_scopes(
        self,
        directory_path: str,
        *,
        channel_ids: list[str],
    ) -> dict[str, str]:
        selected_channels = set(channel_ids)
        try:
            targets = load_communication_targets_config(
                runtime_dir=self.config.runtime_dir,
                directory_store=self.runtime.directories,
                directory_path=directory_path,
            ).targets
        except Exception:
            logger.warning(
                "Failed to load Discord thread-aware targets for directory=%s",
                directory_path,
                exc_info=True,
            )
            return {}
        scopes: dict[str, str] = {}
        for target in targets:
            if target.provider != "discord" or "read" not in set(target.policy.capabilities):
                continue
            parent_channel_id = _discord_channel_id_from_address(target.address)
            if parent_channel_id not in selected_channels:
                continue
            scope = target.policy.discord_conversation_scope
            current = scopes.get(parent_channel_id)
            if current is None:
                scopes[parent_channel_id] = scope
            elif current != scope:
                scopes[parent_channel_id] = "channel_and_public_threads"
        return scopes

    def _discover_public_threads(
        self,
        client: httpx.Client,
        *,
        adapter: DiscordBotApiService,
        channel_scope_by_parent: dict[str, str],
    ) -> dict[str, list[str]]:
        thread_parents = {
            parent_id
            for parent_id, scope in channel_scope_by_parent.items()
            if scope in {"public_threads", "channel_and_public_threads"}
        }
        if not thread_parents:
            return {}

        guild_by_parent: dict[str, str] = {}
        for parent_channel_id in sorted(thread_parents):
            response = adapter._request_with_retries(
                client,
                "GET",
                f"{adapter.api_base_url}/channels/{parent_channel_id}",
            )
            payload = response.json()
            guild_id = str(payload.get("guild_id") or "").strip()
            if not guild_id:
                logger.warning(
                    "Skipping Discord thread discovery for parent=%s because guild_id is missing",
                    parent_channel_id,
                )
                continue
            guild_by_parent[parent_channel_id] = guild_id

        parents_by_guild: dict[str, set[str]] = {}
        for parent_channel_id, guild_id in guild_by_parent.items():
            parents_by_guild.setdefault(guild_id, set()).add(parent_channel_id)

        observed_at = datetime.now(UTC)
        thread_ids_by_parent = {parent_id: [] for parent_id in guild_by_parent}
        for guild_id, parent_ids in sorted(parents_by_guild.items()):
            response = adapter._request_with_retries(
                client,
                "GET",
                f"{adapter.api_base_url}/guilds/{guild_id}/threads/active",
            )
            payload = response.json()
            raw_threads = payload.get("threads") if isinstance(payload, dict) else None
            for raw_thread in raw_threads if isinstance(raw_threads, list) else []:
                if not isinstance(raw_thread, dict):
                    continue
                thread_id = str(raw_thread.get("id") or "").strip()
                parent_channel_id = str(raw_thread.get("parent_id") or "").strip()
                try:
                    thread_type = int(raw_thread.get("type"))
                except (TypeError, ValueError):
                    continue
                if (
                    not thread_id
                    or parent_channel_id not in parent_ids
                    or thread_type not in _PUBLIC_DISCORD_THREAD_TYPES
                ):
                    continue
                metadata = raw_thread.get("thread_metadata")
                metadata = metadata if isinstance(metadata, dict) else {}
                archived = bool(metadata.get("archived", False))
                locked = bool(metadata.get("locked", False))
                self.runtime.discord_inbox_service.store.upsert_thread_binding(
                    thread_id=thread_id,
                    parent_channel_id=parent_channel_id,
                    guild_id=guild_id,
                    thread_type=thread_type,
                    archived=archived,
                    locked=locked,
                    observed_at=observed_at,
                )
                if not archived:
                    thread_ids_by_parent[parent_channel_id].append(thread_id)

        for parent_channel_id, active_thread_ids in thread_ids_by_parent.items():
            self.runtime.discord_inbox_service.store.mark_missing_parent_threads_archived(
                parent_channel_id=parent_channel_id,
                active_thread_ids=active_thread_ids,
                observed_at=observed_at,
            )
        return {
            parent_id: sorted(thread_ids) for parent_id, thread_ids in thread_ids_by_parent.items()
        }

    def _poll_channel(
        self,
        client: httpx.Client,
        *,
        adapter: DiscordBotApiService,
        channel_id: str,
        parent_channel_id: str | None = None,
    ) -> None:
        response = adapter._request_with_retries(
            client,
            "GET",
            f"{adapter.api_base_url}/channels/{channel_id}/messages",
            params={"limit": adapter.poll_limit},
        )
        raw_messages = response.json()
        since_cursor = self.runtime.discord_inbox_service.store.get_cursor(channel_id)
        sorted_messages = sorted(
            raw_messages, key=lambda payload: _message_sort_key(str(payload.get("id", "")))
        )
        discovered: list[DiscordMessageEnvelope] = []
        for payload in sorted_messages:
            created_at = _parse_discord_timestamp(payload.get("timestamp"))
            if since_cursor is not None and created_at <= _parse_discord_timestamp(since_cursor):
                continue
            discovered.append(
                self._normalize_message(
                    channel_id=channel_id,
                    parent_channel_id=parent_channel_id,
                    payload=payload,
                    created_at=created_at,
                )
            )
        if discovered:
            self.runtime.discord_inbox_service.store.add_messages_if_new(discovered)
        if discovered:
            latest_created_at = discovered[-1].created_at.isoformat().replace("+00:00", "Z")
            self.runtime.discord_inbox_service.store.set_cursor(channel_id, latest_created_at)

    def _normalize_message(
        self,
        *,
        channel_id: str,
        parent_channel_id: str | None = None,
        payload: dict[str, object],
        created_at: datetime,
    ) -> DiscordMessageEnvelope:
        message_id = str(payload.get("id", "")).strip()
        referenced_message = payload.get("referenced_message")
        in_reply_to = None
        if isinstance(referenced_message, dict):
            in_reply_to = str(referenced_message.get("id", "")).strip() or None
        command = self.runtime.discord_commands.get(message_id)
        attachments = _sanitize_attachments(payload.get("attachments"))
        safe_payload = dict(payload)
        safe_payload["attachments"] = attachments
        raw: dict[str, object] = {
            "source": "discord_bot_api",
            "channel_id": channel_id,
            "attachments": attachments,
            "message": safe_payload,
        }
        if parent_channel_id is not None:
            raw["thread_id"] = channel_id
            raw["parent_channel_id"] = parent_channel_id
        if in_reply_to is not None:
            raw["message_reference_id"] = in_reply_to
        if command is not None:
            command_source = str(command.metadata.get("source", "")).strip()
            if command_source:
                raw["source"] = command_source
            session_id = str(command.metadata.get("session_id", "")).strip()
            if session_id:
                raw["session_id"] = session_id
        author = payload.get("author")
        author_id = ""
        author_name = ""
        if isinstance(author, dict):
            author_id = str(author.get("id", "")).strip()
            author_name = str(author.get("username", "")).strip() or author_id
        return DiscordMessageEnvelope(
            message_id=message_id,
            channel_id=channel_id,
            author_id=author_id,
            author_name=author_name,
            content=str(payload.get("content", "")),
            created_at=created_at,
            in_reply_to=in_reply_to,
            thread_id=channel_id if parent_channel_id is not None else None,
            parent_channel_id=parent_channel_id,
            raw=raw,
        )


def _sanitize_attachments(raw_attachments: object) -> list[dict[str, object]]:
    if not isinstance(raw_attachments, list):
        return []
    sanitized: list[dict[str, object]] = []
    for raw in raw_attachments:
        if not isinstance(raw, dict):
            continue
        sanitized.append(
            {key: value for key, value in raw.items() if key not in _ATTACHMENT_SECRET_KEYS}
        )
    return sanitized


def _worker_global_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "discord-ingest" / "config.json"


def _load_discord_ingest_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> DiscordIngestWorkerGlobalConfig:
    base_payload = DiscordIngestWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": (
                base_config.runtime_dir if base_config is not None else Path("runtime")
            ),
        }
    ).model_dump(mode="json")
    path = _worker_global_config_path(project_dir)
    if not path.exists():
        return DiscordIngestWorkerGlobalConfig.model_validate(base_payload)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return DiscordIngestWorkerGlobalConfig.model_validate({**base_payload, **raw})


def build_discord_ingest_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
) -> DiscordIngestWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = _load_discord_ingest_worker_global_config(
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
    return DiscordIngestWorker(
        config=effective_config,
        project_dir=resolved_project_dir,
        worker_config=worker_config,
        runtime=runtime,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Discord ingest worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--log-level", default=os.environ.get("DISCORD_INGEST_LOG_LEVEL", "INFO"))
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
    worker = build_discord_ingest_worker(config, project_dir=runtime_dir.parent)
    if args.once:
        result = worker.run_once_with_lock()
        print(
            f"Discord ingest worker cycle complete directories={result.directories_polled} "
            f"sessions={result.sessions_seen} sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return
    worker.run_forever()


def _parse_discord_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"invalid Discord timestamp: {value!r}")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _message_sort_key(message_id: str) -> tuple[int, int | str]:
    if message_id.isdigit():
        return (0, int(message_id))
    return (1, message_id)


def _discord_channel_id_from_address(address: str) -> str:
    return str(address or "").strip().split("/")[-1].strip()


def _directory_is_within_scope(directory_path: str | None, scope_directory_path: str) -> bool:
    candidate_segments = DirectoryPath(segments=directory_path or "root").segments
    scope_segments = DirectoryPath(segments=scope_directory_path).segments
    return candidate_segments[: len(scope_segments)] == scope_segments
