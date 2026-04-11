from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .bootstrap import load_runtime_gateway_config
from .config import GatewayConfig, TeamsIngestWorkerGlobalConfig
from .models import DirectoryPath, SessionManifest, TeamsMessageEnvelope, ToolName
from .runtime import RuntimeContext, _build_delegated_teams_token_provider, build_runtime
from .services import GraphTeamsService
from .storage import JsonStore
from .teams_inbox_store import TeamsInboxMessage, TeamsInboxStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamsIngestCycleResult:
    directories_polled: int
    channel_targets_polled: int
    sessions_seen: int
    next_sleep_seconds: float


class TeamsIngestWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: TeamsIngestWorkerGlobalConfig,
        runtime: RuntimeContext,
        store: TeamsInboxStore | None = None,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.sessions = runtime.sessions
        self.store = store or TeamsInboxStore(config.runtime_dir / "teams-inbox-store.json")

    def run_forever(self) -> None:
        with _single_instance_lock(self.worker_config.lock_path):
            logger.info(
                "Teams ingest worker started poll_interval=%.3f jitter=%.3f backoff=%.3f lock=%s",
                self.worker_config.poll_interval_seconds,
                self.worker_config.jitter_seconds,
                self.worker_config.backoff_seconds,
                self.worker_config.lock_path,
            )
            while True:
                try:
                    result = self.run_once()
                except Exception:
                    logger.exception("Teams ingest worker cycle failed")
                    time.sleep(self.worker_config.backoff_seconds)
                    continue
                logger.debug(
                    (
                        "Teams ingest worker cycle complete directories=%d channel_targets=%d "
                        "sessions=%d sleep_seconds=%.3f"
                    ),
                    result.directories_polled,
                    result.channel_targets_polled,
                    result.sessions_seen,
                    result.next_sleep_seconds,
                )
                time.sleep(result.next_sleep_seconds)

    def run_once_with_lock(self) -> TeamsIngestCycleResult:
        with _single_instance_lock(self.worker_config.lock_path):
            return self.run_once()

    def run_once(self) -> TeamsIngestCycleResult:
        now = datetime.now(UTC)
        active_sessions = [
            session
            for session in self.runtime.sessions.read_all().values()
            if session.expires_at > now
        ]
        directories = self._directories_to_scan(active_sessions)
        channel_targets_polled = 0
        for directory_path in directories:
            channel_targets = self._channel_targets_for_directory(
                directory_path,
                active_sessions=active_sessions,
            )
            if not channel_targets:
                continue
            channel_targets_polled += len(channel_targets)
            self._poll_directory(directory_path, channel_targets=channel_targets)
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
        next_sleep_seconds = _compute_next_sleep_seconds(
            poll_interval_seconds=self.worker_config.poll_interval_seconds,
            jitter_seconds=self.worker_config.jitter_seconds,
        )
        return TeamsIngestCycleResult(
            directories_polled=len(directories),
            channel_targets_polled=channel_targets_polled,
            sessions_seen=len(matching_sessions),
            next_sleep_seconds=next_sleep_seconds,
        )

    def _directories_to_scan(self, active_sessions: list[SessionManifest]) -> list[str]:
        if self.worker_config.directories:
            return list(self.worker_config.directories)
        directories = {
            DirectoryPath(segments=session.directory_path).as_posix()
            for session in active_sessions
        }
        return sorted(directories)

    def _channel_targets_for_directory(
        self,
        directory_path: str,
        *,
        active_sessions: list[SessionManifest],
    ) -> list[str]:
        targets = {
            target.strip().strip("/")
            for session in active_sessions
            if ToolName.TEAMS_GET_MESSAGES in session.allowed_tools
            and _directory_is_within_scope(session.directory_path, directory_path)
            for target in session.teams_channel_allowlist
            if target.strip()
        }
        return sorted(targets)

    def _poll_directory(self, directory_path: str, *, channel_targets: list[str]) -> None:
        provider_config = self.runtime.provider_config.resolve_for_directory(directory_path)
        if provider_config.teams_backend not in {"graph_api", "delegated_graph_api"}:
            logger.debug(
                "Skipping Teams ingest for directory=%s because teams_backend=%s",
                directory_path,
                provider_config.teams_backend,
            )
            return
        service = self._build_graph_service(
            provider_config=provider_config,
            channel_targets=channel_targets,
        )
        with httpx.Client(timeout=provider_config.teams_timeout_seconds) as client:
            for channel_target in channel_targets:
                self._poll_channel(client, service=service, channel_target=channel_target)

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
            JsonStore(self.config.teams_message_store_path, TeamsMessageEnvelope),
            token=token,
            token_provider=resolved_token_provider,
            api_base_url=provider_config.teams_graph_api_base_url,
            poll_limit=provider_config.teams_poll_limit,
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
    ) -> None:
        team_id, channel_id = _parse_teams_channel_target(channel_target)
        cursor_state = self.store.get_cursor_state(channel_target)
        discovered: list[TeamsInboxMessage] = []
        candidates: list[TeamsInboxMessage] = []
        for message_payload in service._iter_channel_messages(
            client,
            team_id=team_id,
            channel_id=channel_id,
        ):
            candidates.append(
                self._normalize_message(
                    service._to_envelope(message_payload, team_id=team_id, channel_id=channel_id)
                )
            )
            for reply_payload in message_payload.get("replies") or []:
                if not isinstance(reply_payload, dict):
                    continue
                candidates.append(
                    self._normalize_message(
                        service._to_envelope(reply_payload, team_id=team_id, channel_id=channel_id)
                    )
                )
        candidates.sort(key=lambda item: (item.created_at, item.cursor))
        for message in candidates:
            if not message.message_id or message.metadata.get("source") != "teams_graph_api":
                continue
            if not _message_is_after_cursor_state(message, cursor_state):
                continue
            if self.store.add_message_if_new(message):
                discovered.append(message)
        if discovered:
            latest = discovered[-1]
            self.store.set_cursor(
                channel_target,
                latest.cursor,
                created_at=latest.created_at,
            )

    def _normalize_message(self, envelope: TeamsMessageEnvelope) -> TeamsInboxMessage:
        metadata = dict(envelope.metadata)
        raw_message = metadata.get("message")
        author_name = _extract_teams_author_name(raw_message) or str(
            metadata.get("author_name") or ""
        ).strip()
        author_id = str(metadata.get("author_id", envelope.author_id)).strip() or envelope.author_id
        team_id = str(metadata.get("team_id", envelope.team_id)).strip() or envelope.team_id
        channel_id = str(metadata.get("channel_id", envelope.channel_id)).strip() or envelope.channel_id
        thread_id = str(metadata.get("thread_id", envelope.thread_id or "")).strip() or envelope.thread_id
        metadata.setdefault("team_id", team_id)
        metadata.setdefault("channel_id", channel_id)
        metadata.setdefault("thread_id", thread_id)
        metadata.setdefault("author_id", author_id)
        if author_name:
            metadata["author_name"] = author_name
        return TeamsInboxMessage(
            cursor=envelope.cursor,
            message_id=envelope.message_id,
            team_id=team_id,
            channel_id=channel_id,
            thread_id=thread_id,
            author_id=author_id,
            author_name=author_name or author_id,
            content=envelope.content,
            created_at=envelope.created_at,
            metadata=metadata,
        )


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
            f"sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return
    worker.run_forever()


def _compute_next_sleep_seconds(*, poll_interval_seconds: float, jitter_seconds: float) -> float:
    delay = poll_interval_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    return max(0.0, delay)


def _parse_teams_channel_target(target: str) -> tuple[str, str]:
    normalized = target.strip().strip("/")
    team_id, separator, channel_id = normalized.partition("/")
    if not separator or not team_id or not channel_id:
        raise ValueError(
            f"Invalid Teams channel target {target!r}: expected 'team-id/channel-id'."
        )
    return team_id, channel_id


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


class _LockHandle:
    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self.fd = fd

    def __enter__(self) -> _LockHandle:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        import fcntl
        import os

        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def _single_instance_lock(path: Path) -> _LockHandle:
    import fcntl
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise RuntimeError(f"Teams ingest worker already running (lock held at {path})") from exc

    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    return _LockHandle(path, fd)


def _directory_is_within_scope(directory_path: str | None, scope_directory_path: str) -> bool:
    candidate_segments = DirectoryPath(segments=directory_path or "root").segments
    scope_segments = DirectoryPath(segments=scope_directory_path).segments
    return candidate_segments[: len(scope_segments)] == scope_segments
