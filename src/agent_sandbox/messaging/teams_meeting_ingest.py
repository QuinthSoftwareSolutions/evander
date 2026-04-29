from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

from agent_sandbox.bootstrap import load_runtime_gateway_config
from agent_sandbox.config import GatewayConfig, TeamsMeetingIngestWorkerGlobalConfig
from agent_sandbox.models import DirectoryPath
from agent_sandbox.runtime import RuntimeContext, build_runtime
from agent_sandbox.teams_meeting import TeamsMeetingBridgeEvent, TeamsMeetingStore
from agent_sandbox.messaging.teams_ingest import _single_instance_lock

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

    def run_forever(self) -> None:
        with _single_instance_lock(self.worker_config.lock_path):
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
            while True:
                try:
                    result = self.run_once()
                except Exception:
                    logger.exception("Teams meeting ingest worker cycle failed")
                    time.sleep(self.worker_config.backoff_seconds)
                    continue
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
                time.sleep(result.next_sleep_seconds)

    def run_once_with_lock(self) -> TeamsMeetingIngestCycleResult:
        with _single_instance_lock(self.worker_config.lock_path):
            return self.run_once()

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
            events_ingested += len(appended)
        return TeamsMeetingIngestCycleResult(
            meetings_polled=meetings_polled,
            events_ingested=events_ingested,
            errors_recorded=errors_recorded,
            next_sleep_seconds=_compute_next_sleep_seconds(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
            ),
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


def _compute_next_sleep_seconds(*, poll_interval_seconds: float, jitter_seconds: float) -> float:
    delay = poll_interval_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    return max(0.0, delay)


def _directory_is_within_scope(directory_path: str | None, scope_directory_path: str) -> bool:
    candidate_segments = DirectoryPath(segments=directory_path or "root").segments
    scope_segments = DirectoryPath(segments=scope_directory_path).segments
    return candidate_segments[: len(scope_segments)] == scope_segments
