from __future__ import annotations

import fcntl
import json
import logging
import os
import random
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .audit import AuditEventType
from .config import GatewayConfig
from .pr_feedback_worker import (
    AzureDevOpsFeedbackAdapter,
    FeedbackProviderAdapter,
    GitHubFeedbackAdapter,
    PullRequestFeedbackWorker,
    build_pr_feedback_worker,
)
from .runtime import RuntimeContext, build_runtime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedbackStream:
    provider: str
    repo: str
    pr_id: str


@dataclass(frozen=True)
class StreamSessionBinding:
    session_id: str
    vm_id: str | None
    source: str


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
        runtime: RuntimeContext,
        feedback_worker: PullRequestFeedbackWorker,
        adapters: dict[str, FeedbackProviderAdapter],
    ) -> None:
        self.config = config
        self.runtime = runtime
        self.feedback_worker = feedback_worker
        self.adapters = adapters

    def run_forever(self) -> None:
        with _single_instance_lock(self.config.host_heartbeat_lock_path):
            logger.info(
                "Host heartbeat worker started poll_interval=%.3f jitter=%.3f cooldown=%.3f lock=%s",
                self.config.host_heartbeat_poll_interval_seconds,
                self.config.host_heartbeat_jitter_seconds,
                self.config.host_heartbeat_cooldown_seconds,
                self.config.host_heartbeat_lock_path,
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
        with _single_instance_lock(self.config.host_heartbeat_lock_path):
            return self.run_once()

    def run_once(self) -> HeartbeatCycleResult:
        stream_mappings = self._collect_stream_mappings()
        streams = sorted(
            stream_mappings.keys(),
            key=lambda stream: (stream.provider, stream.repo, stream.pr_id),
        )
        discovered_feedback = 0
        streams_polled = 0

        with httpx.Client(timeout=self.config.host_heartbeat_http_timeout_seconds) as client:
            for stream in streams:
                bindings = stream_mappings.get(stream, [])
                if len(bindings) > 1:
                    logger.warning(
                        "PR feedback stream matched multiple sessions policy=%s provider=%s repo=%s pr=%s session_ids=%s",
                        self.config.host_heartbeat_stream_conflict_policy,
                        stream.provider,
                        stream.repo,
                        stream.pr_id,
                        [binding.session_id for binding in bindings],
                    )
                    if self.config.host_heartbeat_stream_conflict_policy == "reject":
                        continue
                adapter = self.adapters.get(stream.provider)
                if adapter is None:
                    logger.warning(
                        "Skipping unsupported feedback provider=%s repo=%s pr=%s",
                        stream.provider,
                        stream.repo,
                        stream.pr_id,
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
                            }
                            for binding in bindings
                        ],
                    )
                except Exception:
                    logger.warning(
                        "Host heartbeat stream poll failed provider=%s repo=%s pr=%s",
                        stream.provider,
                        stream.repo,
                        stream.pr_id,
                        exc_info=True,
                    )
                    continue
                discovered_feedback += len(discovered)
                if discovered:
                    self._record_busy_skips(stream=stream, bindings=bindings)

        next_sleep_seconds = _compute_next_sleep_seconds(
            poll_interval_seconds=self.config.host_heartbeat_poll_interval_seconds,
            jitter_seconds=self.config.host_heartbeat_jitter_seconds,
            cooldown_seconds=self.config.host_heartbeat_cooldown_seconds,
            discovered_feedback=discovered_feedback,
        )
        self.runtime.audit.log_decision(
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

    def _record_busy_skips(self, *, stream: FeedbackStream, bindings: list[StreamSessionBinding]) -> None:
        for binding in bindings:
            if _is_session_in_cooldown(
                path=self.config.host_heartbeat_session_cooldown_path,
                session_id=binding.session_id,
            ):
                self.runtime.audit.log_decision(
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
            if _is_agent_busy(
                session_id=binding.session_id,
                vm_id=binding.vm_id,
                script_path=self.config.host_busy_check_script_path,
            ):
                _set_session_cooldown(
                    path=self.config.host_heartbeat_session_cooldown_path,
                    session_id=binding.session_id,
                    cooldown_seconds=self.config.host_heartbeat_cooldown_seconds,
                )
                self.runtime.audit.log_decision(
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
            self._resume_binding(binding)
            _set_session_cooldown(
                path=self.config.host_heartbeat_session_cooldown_path,
                session_id=binding.session_id,
                cooldown_seconds=self.config.host_heartbeat_cooldown_seconds,
            )

    def _resume_binding(self, binding: StreamSessionBinding) -> None:
        if not binding.vm_id:
            return
        script_path = self.config.host_resume_script_path
        if not script_path.exists():
            logger.warning(
                "Resume script missing path=%s session_id=%s vm_id=%s",
                script_path,
                binding.session_id,
                binding.vm_id,
            )
            return
        cmd = [
            str(script_path),
            "--prompt",
            self.config.host_heartbeat_resume_prompt,
            "--vm-name",
            binding.vm_id,
            "--session-id",
            binding.session_id,
            "--vm-user",
            self.config.host_heartbeat_vm_user,
            "--worker-mode",
            "--host-state-file",
            str(self.config.host_heartbeat_wake_state_path),
        ]
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.config.host_heartbeat_resume_timeout_seconds,
            )
        except OSError:
            logger.warning(
                "Resume script invocation failed path=%s session_id=%s vm_id=%s",
                script_path,
                binding.session_id,
                binding.vm_id,
                exc_info=True,
            )
            return
        except subprocess.TimeoutExpired:
            logger.warning(
                "Resume script timed out path=%s session_id=%s vm_id=%s timeout_seconds=%.3f",
                script_path,
                binding.session_id,
                binding.vm_id,
                self.config.host_heartbeat_resume_timeout_seconds,
            )
            return

        if completed.returncode == 0:
            logger.info(
                "Host heartbeat wake dispatched session_id=%s vm_id=%s",
                binding.session_id,
                binding.vm_id,
            )
            return
        logger.warning(
            "Host heartbeat wake failed session_id=%s vm_id=%s exit_code=%s stdout=%s stderr=%s",
            binding.session_id,
            binding.vm_id,
            completed.returncode,
            (completed.stdout or "").strip(),
            (completed.stderr or "").strip(),
        )

    def _collect_stream_mappings(self) -> dict[FeedbackStream, list[StreamSessionBinding]]:
        now = datetime.now(timezone.utc)
        sessions_by_id = self.runtime.sessions.read_all()
        stream_index: dict[tuple[str, str, str], list[StreamSessionBinding]] = {}

        for session in sessions_by_id.values():
            if session.expires_at <= now:
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
                key = (parsed.provider, parsed.repo, parsed.pr_id)
                binding = StreamSessionBinding(
                    session_id=session.session_id,
                    vm_id=session.vm_id,
                    source="session_manifest",
                )
                stream_index.setdefault(key, []).append(binding)
                self.runtime.audit.log_decision(
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
            if key in stream_index:
                continue
            session_id = str(raw_entry.get("session_id", "")).strip()
            if not session_id:
                continue
            session = sessions_by_id.get(session_id)
            vm_id = session.vm_id if session is not None else None
            binding = StreamSessionBinding(
                session_id=session_id,
                vm_id=vm_id,
                source="pr_map",
            )
            stream_index[key] = [binding]
            self.runtime.audit.log_decision(
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

    def _collect_streams(self) -> list[FeedbackStream]:
        return sorted(
            self._collect_stream_mappings().keys(),
            key=lambda stream: (stream.provider, stream.repo, stream.pr_id),
        )


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


def _parse_stream(raw_stream: Any) -> FeedbackStream | None:
    if not isinstance(raw_stream, dict):
        return None

    provider = str(raw_stream.get("provider", "")).strip()
    repo = str(raw_stream.get("repo", "")).strip()
    pr_id = str(raw_stream.get("pr_id", "")).strip()
    if not provider or not repo or not pr_id:
        return None
    return FeedbackStream(provider=provider, repo=repo, pr_id=pr_id)


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


def _is_agent_busy(*, session_id: str, vm_id: str, script_path: Path) -> bool:
    if not script_path.exists():
        logger.warning(
            "Busy check script missing path=%s session_id=%s vm_id=%s",
            script_path,
            session_id,
            vm_id,
        )
        return False
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
        return False

    output = (completed.stdout or "").strip()
    if completed.returncode == 0:
        logger.info(
            "Busy check status=busy session_id=%s vm_id=%s output=%s",
            session_id,
            vm_id,
            output,
        )
        return True
    if completed.returncode == 1:
        return False
    logger.warning(
        "Busy check status=unknown session_id=%s vm_id=%s exit_code=%s output=%s",
        session_id,
        vm_id,
        completed.returncode,
        output,
    )
    return False


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


def build_host_heartbeat_worker(config: GatewayConfig) -> HostHeartbeatWorker:
    runtime = build_runtime(config)
    adapters: dict[str, FeedbackProviderAdapter] = {
        "github": GitHubFeedbackAdapter(),
        "azure_devops": AzureDevOpsFeedbackAdapter(
            organization_url=config.azure_devops_organization_url,
            project=config.azure_devops_project,
        ),
    }
    feedback_worker = build_pr_feedback_worker(config.runtime_dir, adapters=adapters)
    return HostHeartbeatWorker(
        config=config,
        runtime=runtime,
        feedback_worker=feedback_worker,
        adapters=adapters,
    )
