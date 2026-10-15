import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from config_helpers import canonical_worker_config
from evander.admin.services.security_scan_service import SecurityScanService
from evander.config import GatewayConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.effects.registry import EffectProviderFactory
from evander.events.models import TriggerEvent
from evander.provider_config import ProviderConfigResolver
from evander.security_scans import SecurityScanHandle, SecurityScanResult


@pytest.mark.parametrize("complete", [True, False])
def test_workflow_waits_and_recovers_scan_completion_after_worker_restart(tmp_path, complete):
    starts = []
    terminal = []

    class Scanner:
        def start(self, *, scan_id, **kwargs):
            starts.append(scan_id)
            return SecurityScanHandle(execution_id=scan_id)

        def poll(self, **kwargs):
            return terminal[0] if terminal else SecurityScanResult(status="running")

        def cancel(self, **kwargs):
            pass

    config = GatewayConfig(runtime_dir=tmp_path)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": tmp_path,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "tick",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "scan",
                    "display_name": "Scan",
                    "type": "effect:security:scan-repo",
                    "session_id": "session-1",
                    "repo_path": "/home/agent/repo",
                }
            ],
            "edges": [{"source_id": "tick", "target_id": "scan"}],
        }
    )
    session = SimpleNamespace(
        directory_path="root", expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    service = SecurityScanService(
        runtime_dir=tmp_path,
        providers={"codex-security": Scanner()},
        sessions=SimpleNamespace(get_session=lambda sid: session),
    )

    def make_worker():
        worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)
        worker.provider_factory = EffectProviderFactory(
            ProviderConfigResolver(config),
            provider_runtime=SimpleNamespace(security_scan_service=service),
        )
        return worker

    worker = make_worker()
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-1",
            trigger_id="tick",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(UTC),
            payload={},
        )
    )
    try:
        for _ in range(30):
            worker.run_once()
            rows = list(worker.execution_store.read_all().values())
            if rows and rows[0].status == "waiting":
                break
            time.sleep(0.01)
        assert rows[0].status == "waiting"
        assert len(starts) == 1
    finally:
        worker.close()

    terminal.append(
        SecurityScanResult(
            status="completed",
            coverage="complete" if complete else "partial",
            findings=[{"severity": "critical"}],
            artifacts={"report": "report.md"},
        )
    )
    worker = make_worker()
    try:
        for _ in range(30):
            worker._recovery_schedule.next_due = 0
            worker.run_once()
            rows = list(worker.execution_store.read_all().values())
            if rows[0].status not in {"waiting", "running"}:
                break
            time.sleep(0.01)
        assert rows[0].status == ("succeeded" if complete else "failed")
        assert rows[0].outputs["findings"] == [{"severity": "critical"}]
        assert rows[0].outputs["artifacts"] == {"report": "report.md"}
        assert len(starts) == 1
        if complete:
            assert rows[0].published_event_id is not None
    finally:
        worker.close()
