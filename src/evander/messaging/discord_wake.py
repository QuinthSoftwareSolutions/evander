from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evander.agent_control import AgentControlStore
from evander.bootstrap import load_runtime_gateway_config
from evander.config import DiscordWakeWorkerGlobalConfig, GatewayConfig
from evander.models import DirectoryPath, SessionManifest, ToolName
from evander.runtime import RuntimeContext, build_runtime
from evander.orchestration.host_polling import HostFileLockManager, HostSleeper
from evander.orchestration.kernels import (
    PollingKernelConfig,
    PollingOrchestratorKernel,
)
from evander.messaging.wake_dispatch import (
    HostWakeDispatcher,
    SessionWakeBinding,
    compute_next_sleep_seconds,
    directory_is_within_scope,
    is_session_in_cooldown,
    probe_agent_dispatch_state,
    set_session_cooldown,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscordWakeCycleResult:
    channels_polled: int
    pending_channels: int
    next_sleep_seconds: float


class DiscordWakeWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: DiscordWakeWorkerGlobalConfig,
        runtime: RuntimeContext,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.dispatcher = HostWakeDispatcher(
            config=config,
            runtime=runtime,
            agent_control_store=AgentControlStore(config.host_agent_control_state_path),
        )
        self._kernel = PollingOrchestratorKernel(
            config=PollingKernelConfig(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                lock_path=self.worker_config.lock_path,
            ),
            lock_manager=HostFileLockManager(
                busy_error_text="Discord wake worker already running"
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

    def run_once_with_lock(self) -> DiscordWakeCycleResult:
        return self._kernel.run_once_with_lock()

    def run_once(self) -> DiscordWakeCycleResult:
        now = datetime.now(UTC)
        active_sessions = self.runtime.session_repository.list_active_sessions(now=now)
        directories = self._directories_to_scan(active_sessions)
        if not directories:
            return DiscordWakeCycleResult(
                channels_polled=0,
                pending_channels=0,
                next_sleep_seconds=compute_next_sleep_seconds(
                    poll_interval_seconds=self.worker_config.poll_interval_seconds,
                    jitter_seconds=self.worker_config.jitter_seconds,
                    cooldown_seconds=self.worker_config.cooldown_seconds,
                    activity_detected=0,
                ),
            )
        channels_polled = 0
        pending_channels = 0
        for directory_path in directories:
            channel_bindings = self._collect_discord_channel_mappings(
                directory_path=directory_path,
                active_sessions=active_sessions,
            )
            for channel_id, bindings in sorted(channel_bindings.items()):
                channels_polled += 1
                if self._discord_channel_has_unread_bindings(
                    channel_id=channel_id, bindings=bindings
                ):
                    pending_channels += 1
                    self._wake_pending_bindings(channel_id=channel_id, bindings=bindings)
        return DiscordWakeCycleResult(
            channels_polled=channels_polled,
            pending_channels=pending_channels,
            next_sleep_seconds=compute_next_sleep_seconds(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                cooldown_seconds=self.worker_config.cooldown_seconds,
                activity_detected=pending_channels,
            ),
        )

    def _log_start(self) -> None:
        logger.info(
            "Discord wake worker started poll_interval=%.3f jitter=%.3f backoff=%.3f cooldown=%.3f lock=%s",
            self.worker_config.poll_interval_seconds,
            self.worker_config.jitter_seconds,
            self.worker_config.backoff_seconds,
            self.worker_config.cooldown_seconds,
            self.worker_config.lock_path,
        )

    def _log_cycle_success(self, result: DiscordWakeCycleResult) -> None:
        logger.debug(
            "Discord wake cycle complete channels=%d pending=%d sleep_seconds=%.3f",
            result.channels_polled,
            result.pending_channels,
            result.next_sleep_seconds,
        )

    def _log_cycle_exception(self, _exc: Exception) -> None:
        logger.exception("Discord wake worker cycle failed")

    def _directories_to_scan(self, active_sessions: list[SessionManifest]) -> list[str]:
        if self.worker_config.directories:
            return list(self.worker_config.directories)
        return sorted(
            {
                DirectoryPath(segments=session.directory_path).as_posix()
                for session in active_sessions
            }
        )

    def _collect_discord_channel_mappings(
        self,
        *,
        directory_path: str,
        active_sessions: list[SessionManifest],
    ) -> dict[str, list[SessionWakeBinding]]:
        policy_config = getattr(self.runtime, "policy_config", None)
        if policy_config is None:
            return {}
        mapped: dict[str, list[SessionWakeBinding]] = {}
        for session in active_sessions:
            if not directory_is_within_scope(session.directory_path, directory_path):
                continue
            if ToolName.DISCORD_GET_COMMANDS not in session.allowed_tools:
                continue
            for channel_id in policy_config.resolved_discord_channel_ids_for_session(
                session,
                capability="read",
            ):
                mapped.setdefault(channel_id, []).append(
                    SessionWakeBinding(
                        session_id=session.session_id,
                        vm_id=session.vm_id,
                        source="session_discord_allowlist",
                        directory_path=session.directory_path,
                    )
                )
        return {
            channel_id: list(
                {
                    (binding.session_id, binding.vm_id, binding.source): binding
                    for binding in bindings
                }.values()
            )
            for channel_id, bindings in mapped.items()
        }

    def _discord_channel_has_unread_bindings(
        self, *, channel_id: str, bindings: list[SessionWakeBinding]
    ) -> bool:
        return any(
            self._binding_has_unread_discord_messages(binding=binding, channel_id=channel_id)
            for binding in bindings
        )

    def _binding_has_unread_discord_messages(
        self, *, binding: SessionWakeBinding, channel_id: str
    ) -> bool:
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

    def _wake_pending_bindings(
        self, *, channel_id: str, bindings: list[SessionWakeBinding]
    ) -> None:
        for binding in bindings:
            if not self._binding_has_unread_discord_messages(
                binding=binding, channel_id=channel_id
            ):
                continue
            scoped_config = self.dispatcher.resolve_scoped_config(
                session_id=binding.session_id,
                directory_path=binding.directory_path,
                source=f"discord_wake:{binding.source}",
            )
            if self.dispatcher.is_host_disabled(binding):
                continue
            if is_session_in_cooldown(
                path=scoped_config.wake_session_cooldown_path,
                session_id=binding.session_id,
            ):
                continue
            if not binding.vm_id:
                continue
            agent_state = probe_agent_dispatch_state(
                session_id=binding.session_id,
                vm_id=binding.vm_id,
                script_path=scoped_config.host_busy_check_script_path,
            )
            if agent_state != "idle":
                if agent_state == "busy":
                    set_session_cooldown(
                        path=scoped_config.wake_session_cooldown_path,
                        session_id=binding.session_id,
                        cooldown_seconds=scoped_config.wake_cooldown_seconds,
                    )
                continue
            self.dispatcher.resume_binding(
                binding,
                worker_config=scoped_config,
                prompt=str(scoped_config.discord_resume_prompt).strip(),
                dispatch_source="discord_wake_worker",
            )
            set_session_cooldown(
                path=scoped_config.wake_session_cooldown_path,
                session_id=binding.session_id,
                cooldown_seconds=scoped_config.wake_cooldown_seconds,
            )


def _worker_global_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "discord-wake" / "config.json"


def _load_discord_wake_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> DiscordWakeWorkerGlobalConfig:
    source = base_config or GatewayConfig()
    base_payload = DiscordWakeWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": source.runtime_dir,
            "directories": [],
            "poll_interval_seconds": source.wake_poll_interval_seconds,
            "jitter_seconds": source.wake_jitter_seconds,
            "cooldown_seconds": source.wake_cooldown_seconds,
            "lock_path": source.runtime_dir / "discord-wake.lock",
            "session_cooldown_path": source.wake_session_cooldown_path,
            "busy_check_script_path": source.host_busy_check_script_path,
            "resume_script_path": source.host_resume_script_path,
            "wake_state_path": source.runtime_dir / "discord-wake-state.json",
            "vm_user": source.wake_vm_user,
            "resume_timeout_seconds": source.wake_resume_timeout_seconds,
        }
    ).model_dump(mode="json")
    path = _worker_global_config_path(project_dir)
    if not path.exists():
        return DiscordWakeWorkerGlobalConfig.model_validate(base_payload)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return DiscordWakeWorkerGlobalConfig.model_validate({**base_payload, **raw})


def build_discord_wake_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
) -> DiscordWakeWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = _load_discord_wake_worker_global_config(
        resolved_project_dir, base_config=config
    )
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
            "wake_poll_interval_seconds": worker_config.poll_interval_seconds,
            "wake_jitter_seconds": worker_config.jitter_seconds,
            "wake_cooldown_seconds": worker_config.cooldown_seconds,
            "wake_session_cooldown_path": worker_config.session_cooldown_path,
            "host_busy_check_script_path": worker_config.busy_check_script_path,
            "host_resume_script_path": worker_config.resume_script_path,
            "wake_dispatch_state_path": worker_config.wake_state_path,
            "wake_vm_user": worker_config.vm_user,
            "wake_resume_timeout_seconds": worker_config.resume_timeout_seconds,
        }
    )
    runtime = build_runtime(effective_config)
    return DiscordWakeWorker(config=effective_config, worker_config=worker_config, runtime=runtime)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Discord wake worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--log-level", default=os.environ.get("DISCORD_WAKE_LOG_LEVEL", "INFO"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging_level = getattr(logging, str(args.log_level).upper(), None)
    if not isinstance(logging_level, int):
        raise SystemExit(f"Invalid --log-level value: {args.log_level}")
    logging.basicConfig(
        level=logging_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    config = load_runtime_gateway_config(runtime_dir=runtime_dir)
    worker = build_discord_wake_worker(config, project_dir=runtime_dir.parent)
    if args.once:
        result = worker.run_once_with_lock()
        print(
            f"Discord wake worker cycle complete channels={result.channels_polled} "
            f"pending={result.pending_channels} sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return
    worker.run_forever()
