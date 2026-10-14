from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from evander.bootstrap import load_runtime_gateway_config
from evander.config import GatewayConfig, TeamsMeetingIngestWorkerGlobalConfig
from evander.models import DirectoryPath
from evander.orchestration.host_polling import HostFileLockManager, HostSleeper
from evander.orchestration.kernels import (
    PollingKernelConfig,
    PollingOrchestratorKernel,
    compute_next_sleep_seconds,
)
from evander.runtime import RuntimeContext, build_runtime
from evander.teams_meeting import (
    TeamsMeetingBridgeEvent,
    TeamsMeetingStore,
    voice_session_transcript_entries_from_teams_events,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamsMeetingIngestCycleResult:
    meetings_polled: int
    events_ingested: int
    errors_recorded: int
    next_sleep_seconds: float


class TeamsMeetingIngestWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: TeamsMeetingIngestWorkerGlobalConfig,
        runtime: RuntimeContext,
        store: TeamsMeetingStore | None = None,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.store = store or runtime.teams_meeting_store
        self._kernel = PollingOrchestratorKernel(
            config=PollingKernelConfig(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                lock_path=self.worker_config.lock_path,
            ),
            lock_manager=HostFileLockManager(
                busy_error_text="Teams meeting ingest worker already running"
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

    def run_once_with_lock(self) -> TeamsMeetingIngestCycleResult:
        return self._kernel.run_once_with_lock()

    def run_once(self) -> TeamsMeetingIngestCycleResult:
        meetings_polled = 0
        events_ingested = 0
        errors_recorded = 0
        for binding in self.store.list_active_bindings():
            if self.worker_config.directories and not any(
                _directory_is_within_scope(binding.directory_path, scope)
                for scope in self.worker_config.directories
            ):
                continue
            meetings_polled += 1
            try:
                bridge_events = self.runtime.teams_meeting_bridge_client.get_events(
                    meeting_id=binding.meeting_id,
                    after_sequence=binding.last_bridge_sequence,
                )
            except Exception as exc:
                logger.warning(
                    "Teams meeting bridge poll failed meeting_id=%s session_id=%s",
                    binding.meeting_id,
                    binding.session_id,
                    exc_info=True,
                )
                self.store.append_local_event(
                    meeting_id=binding.meeting_id,
                    event_type="error",
                    text=str(exc),
                    metadata={"source": "teams_meeting_ingest"},
                )
                errors_recorded += 1
                continue
            appended = self.store.append_bridge_events(
                meeting_id=binding.meeting_id,
                bridge_events=[
                    event
                    if isinstance(event, TeamsMeetingBridgeEvent)
                    else TeamsMeetingBridgeEvent.model_validate(event)
                    for event in bridge_events
                ],
            )
            self._append_voice_transcript_events(binding, appended)
            events_ingested += len(appended)
        calls_polled = 0
        voice_service = getattr(self.runtime, "teams_voice_service", None)
        if voice_service is not None:
            calls_polled, call_errors = voice_service.supervise(
                directories=self.worker_config.directories
            )
            meetings_polled += calls_polled
            errors_recorded += call_errors
        return TeamsMeetingIngestCycleResult(
            meetings_polled=meetings_polled,
            events_ingested=events_ingested,
            errors_recorded=errors_recorded,
            next_sleep_seconds=compute_next_sleep_seconds(
                poll_interval_seconds=(
                    min(5.0, self.worker_config.poll_interval_seconds)
                    if calls_polled
                    else self.worker_config.poll_interval_seconds
                ),
                jitter_seconds=self.worker_config.jitter_seconds,
            ),
        )

    def _log_start(self) -> None:
        logger.info(
            (
                "Teams meeting ingest worker started poll_interval=%.3f jitter=%.3f "
                "backoff=%.3f lock=%s"
            ),
            self.worker_config.poll_interval_seconds,
            self.worker_config.jitter_seconds,
            self.worker_config.backoff_seconds,
            self.worker_config.lock_path,
        )

    def _log_cycle_success(self, result: TeamsMeetingIngestCycleResult) -> None:
        logger.debug(
            (
                "Teams meeting ingest cycle complete meetings=%d events=%d "
                "errors=%d sleep_seconds=%.3f"
            ),
            result.meetings_polled,
            result.events_ingested,
            result.errors_recorded,
            result.next_sleep_seconds,
        )

    def _log_cycle_exception(self, _exc: Exception) -> None:
        logger.exception("Teams meeting ingest worker cycle failed")

    def _append_voice_transcript_events(self, binding, events) -> None:
        voice_session_id = str(binding.metadata.get("voice_session_id") or "").strip()
        if not voice_session_id:
            return
        voice_store = getattr(self.runtime, "voice_session_store", None)
        if voice_store is None:
            return
        for event, speaker, text in voice_session_transcript_entries_from_teams_events(events):
            voice_store.append_transcript_event(
                voice_session_id,
                text=text,
                speaker=speaker,
                occurred_at=event.occurred_at,
                metadata={
                    "source": "teams_meeting_ingest",
                    "meeting_id": event.meeting_id,
                    "meeting_sequence": event.sequence,
                    "bridge_sequence": event.bridge_sequence,
                },
            )


def _worker_global_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "teams-meeting-ingest" / "config.json"


def _load_teams_meeting_ingest_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> TeamsMeetingIngestWorkerGlobalConfig:
    source = base_config or GatewayConfig()
    base_payload = TeamsMeetingIngestWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": source.runtime_dir,
            "poll_interval_seconds": source.teams_meeting_poll_interval_seconds,
        }
    ).model_dump(mode="json")
    path = _worker_global_config_path(project_dir)
    if not path.exists():
        return TeamsMeetingIngestWorkerGlobalConfig.model_validate(base_payload)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return TeamsMeetingIngestWorkerGlobalConfig.model_validate({**base_payload, **raw})


def build_teams_meeting_ingest_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
) -> TeamsMeetingIngestWorker:
    from evander.optional_integrations import require_teams_graph_module

    require_teams_graph_module("evander_teams_graph_addon", config)
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = _load_teams_meeting_ingest_worker_global_config(
        resolved_project_dir,
        base_config=config,
    )
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
            "teams_meeting_poll_interval_seconds": worker_config.poll_interval_seconds,
            "teams_meeting_store_path": worker_config.runtime_dir / "teams-meetings.json",
        }
    )
    runtime = build_runtime(effective_config)
    return TeamsMeetingIngestWorker(
        config=effective_config,
        worker_config=worker_config,
        runtime=runtime,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Teams meeting ingest worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("TEAMS_MEETING_INGEST_LOG_LEVEL", "INFO"),
    )
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
    from evander.optional_integrations import teams_graph_enabled

    if not teams_graph_enabled(config):
        logging.getLogger(__name__).info("Teams/Graph is disabled; worker not started.")
        return
    worker = build_teams_meeting_ingest_worker(config, project_dir=runtime_dir.parent)
    if args.once:
        result = worker.run_once_with_lock()
        print(
            f"Teams meeting ingest worker cycle complete meetings={result.meetings_polled} "
            f"events={result.events_ingested} errors={result.errors_recorded} "
            f"sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return
    worker.run_forever()


def _directory_is_within_scope(directory_path: str | None, scope_directory_path: str) -> bool:
    candidate_segments = DirectoryPath(segments=directory_path or "root").segments
    scope_segments = DirectoryPath(segments=scope_directory_path).segments
    return candidate_segments[: len(scope_segments)] == scope_segments
