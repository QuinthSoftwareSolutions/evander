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

import httpx
from .bootstrap import load_runtime_gateway_config
from .config import DiscordIngestWorkerGlobalConfig, GatewayConfig
from .models import DirectoryPath, DiscordMessageEnvelope, SessionManifest
from .runtime import RuntimeContext, build_runtime
from .services import DiscordBotApiService

logger = logging.getLogger(__name__)


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
        worker_config: DiscordIngestWorkerGlobalConfig,
        runtime: RuntimeContext,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.sessions = runtime.sessions

    def run_forever(self) -> None:
        with _single_instance_lock(self.worker_config.lock_path):
            logger.info(
                "Discord ingest worker started poll_interval=%.3f jitter=%.3f backoff=%.3f lock=%s",
                self.worker_config.poll_interval_seconds,
                self.worker_config.jitter_seconds,
                self.worker_config.backoff_seconds,
                self.worker_config.lock_path,
            )
            while True:
                try:
                    result = self.run_once()
                except Exception:
                    logger.exception("Discord ingest worker cycle failed")
                    time.sleep(self.worker_config.backoff_seconds)
                    continue
                logger.debug(
                    (
                        "Discord ingest worker cycle complete directories=%d sessions=%d "
                        "sleep_seconds=%.3f"
                    ),
                    result.directories_polled,
                    result.sessions_seen,
                    result.next_sleep_seconds,
                )
                time.sleep(result.next_sleep_seconds)

    def run_once_with_lock(self) -> DiscordIngestCycleResult:
        with _single_instance_lock(self.worker_config.lock_path):
            return self.run_once()

    def run_once(self) -> DiscordIngestCycleResult:
        now = datetime.now(UTC)
        active_sessions = self.runtime.session_repository.list_active_sessions(now=now)
        directories = self._directories_to_scan(active_sessions)
        for directory_path in directories:
            routed_channels = self.runtime.policy_config.discord_inbox_routes_for_directory(directory_path)
            if not routed_channels:
                continue
            self._poll_directory(directory_path, channel_ids=sorted(routed_channels))
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
        return DiscordIngestCycleResult(
            directories_polled=len(directories),
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
        adapter = DiscordBotApiService(
            self.runtime.discord_commands,
            token=token,
            api_base_url=provider_config.discord_api_base_url,
            poll_limit=provider_config.discord_poll_limit,
            timeout_seconds=provider_config.discord_timeout_seconds,
            retry_count=provider_config.discord_retry_count,
            retry_base_backoff_seconds=provider_config.discord_retry_base_backoff_seconds,
            retry_max_backoff_seconds=provider_config.discord_retry_max_backoff_seconds,
            allowed_channel_ids_provider=lambda: channel_ids,
        )
        with httpx.Client(timeout=provider_config.discord_timeout_seconds) as client:
            for channel_id in channel_ids:
                self._poll_channel(client, adapter=adapter, channel_id=channel_id)

    def _poll_channel(
        self,
        client: httpx.Client,
        *,
        adapter: DiscordBotApiService,
        channel_id: str,
    ) -> None:
        response = adapter._request_with_retries(
            client,
            "GET",
            f"{adapter.api_base_url}/channels/{channel_id}/messages",
            params={"limit": adapter.poll_limit},
        )
        raw_messages = response.json()
        since_cursor = self.runtime.discord_inbox_service.store.get_cursor(channel_id)
        sorted_messages = sorted(raw_messages, key=lambda payload: _message_sort_key(str(payload.get("id", ""))))
        discovered: list[DiscordMessageEnvelope] = []
        for payload in sorted_messages:
            created_at = _parse_discord_timestamp(payload.get("timestamp"))
            if since_cursor is not None and created_at <= _parse_discord_timestamp(since_cursor):
                continue
            discovered.append(self._normalize_message(channel_id=channel_id, payload=payload, created_at=created_at))
        for envelope in discovered:
            self.runtime.discord_inbox_service.store.add_message_if_new(envelope)
        if discovered:
            latest_created_at = discovered[-1].created_at.isoformat().replace("+00:00", "Z")
            self.runtime.discord_inbox_service.store.set_cursor(channel_id, latest_created_at)

    def _normalize_message(
        self,
        *,
        channel_id: str,
        payload: dict[str, object],
        created_at: datetime,
    ) -> DiscordMessageEnvelope:
        message_id = str(payload.get("id", "")).strip()
        referenced_message = payload.get("referenced_message")
        in_reply_to = None
        if isinstance(referenced_message, dict):
            in_reply_to = str(referenced_message.get("id", "")).strip() or None
        command = self.runtime.discord_commands.get(message_id)
        raw: dict[str, object] = {
            "source": "discord_bot_api",
            "message": payload,
        }
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
            raw=raw,
        )


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


def _compute_next_sleep_seconds(*, poll_interval_seconds: float, jitter_seconds: float) -> float:
    delay = poll_interval_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    return max(0.0, delay)


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
        raise RuntimeError(f"Discord ingest worker already running (lock held at {path})") from exc

    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    return _LockHandle(path, fd)


def _directory_is_within_scope(directory_path: str | None, scope_directory_path: str) -> bool:
    candidate_segments = DirectoryPath(segments=directory_path or "root").segments
    scope_segments = DirectoryPath(segments=scope_directory_path).segments
    return candidate_segments[: len(scope_segments)] == scope_segments
