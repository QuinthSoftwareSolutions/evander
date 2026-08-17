from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from evander.cli import app
from evander.config import TriggerOrchestratorWorkerGlobalConfig
from evander.daemons.effect_orchestrator import (
    EffectExecutionRecord,
    GateExecutionRecord,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from evander.events.internal import InternalEventBus
from evander.host_config import HostConfig, save_host_config
from evander.orchestration.host_polling import single_instance_lock
from evander.orchestration.internal_events import build_internal_event_bus
from evander.orchestration.json_finalization import (
    OrchestrationJsonFinalizationBlocked,
    check_orchestration_json_finalization,
    finalize_orchestration_json_store,
)
from evander.repositories.orchestration_db import ensure_orchestration_schema
from evander.triggers.queue import SqliteTriggerEventQueue, TriggerQueueRecord
from evander.workflow.events import SqliteWorkflowEventJournal, WorkflowChangeEvent


def _worker_config(tmp_path: Path, *, backend: str = "sqlite"):
    return TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": tmp_path,
            "orchestration_store_backend": backend,
            "orchestration_retention_days": 30,
        }
    )


def _execution(
    *,
    execution_id: str,
    now: datetime,
    status: str,
    gate: bool = False,
    dismissed_at: datetime | None = None,
) -> EffectExecutionRecord | GateExecutionRecord:
    common = {
        "execution_id": execution_id,
        "idempotency_key": f"idem-{execution_id}",
        "event_id": f"event-{execution_id}",
        "trace_id": f"trace-{execution_id}",
        "source_node_id": "trigger-1",
        "source_trigger_type": "trigger:test",
        "status": status,
        "started_at": now,
        "finished_at": now,
        "resolved_config_hash": "hash",
        "dismissed_at": dismissed_at,
    }
    if gate:
        return GateExecutionRecord(
            **common,
            gate_id="gate-1",
            gate_type="gate:test",
        )
    return EffectExecutionRecord(
        **common,
        effect_id="effect-1",
        effect_type="effect:test",
    )


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "database_path": tmp_path / "orchestration.db",
        "trigger_events_path": tmp_path / "trigger-orchestrator-events.json",
        "effect_executions_path": tmp_path / "effect-orchestrator-executions.json",
        "gate_executions_path": tmp_path / "gate-orchestrator-executions.json",
        "workflow_events_path": tmp_path / "workflow-events.jsonl",
        "marker_path": tmp_path / "migrations" / "orchestration-finalized.json",
    }


def test_internal_event_bus_uses_sqlite_journal_for_explicit_sqlite_backend(
    tmp_path: Path,
) -> None:
    config = _worker_config(tmp_path)

    event = build_internal_event_bus(config).publish(
        event_type="test.event",
        payload={"value": 1},
        idempotency_key="test-event-1",
    )

    assert not config.workflow_event_journal_path.exists()
    events, _cursor = SqliteWorkflowEventJournal(config.orchestration_store_path).read_after(0)
    assert [(item.event_type, item.subject_id) for item in events] == [
        ("workflow.internal_event.published", event.event_id)
    ]


def test_reading_internal_events_does_not_recreate_legacy_workflow_journal(
    tmp_path: Path,
) -> None:
    bus = InternalEventBus(tmp_path / "internal-events.json")

    assert bus.list_events() == []
    assert not (tmp_path / "workflow-events.jsonl").exists()


def test_internal_event_bus_preserves_explicit_json_rollback_journal(
    tmp_path: Path,
) -> None:
    config = _worker_config(tmp_path, backend="json")

    build_internal_event_bus(config).publish(event_type="test.rollback")

    assert "workflow.internal_event.published" in config.workflow_event_journal_path.read_text(
        encoding="utf-8"
    )
    assert not config.orchestration_store_path.exists()


def test_finalization_accepts_nonterminal_records_already_present_in_sqlite(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    paths = _paths(tmp_path)
    ensure_orchestration_schema(paths["database_path"])
    active = _execution(execution_id="active-existing", now=now, status="running")
    SqliteExecutionStore(paths["database_path"]).record_execution(active)
    paths["effect_executions_path"].write_text(
        json.dumps({active.execution_id: active.model_dump(mode="json")}),
        encoding="utf-8",
    )

    report = check_orchestration_json_finalization(
        **paths,
        retention_days=30,
        now=now,
    )

    effect_report = next(
        source for source in report.sources if source.source == "effect_executions"
    )
    assert report.safe_to_apply is True
    assert effect_report.existing == 1


def test_finalization_check_fails_closed_for_malformed_and_nonterminal_sources(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    paths = _paths(tmp_path)
    ensure_orchestration_schema(paths["database_path"])
    paths["trigger_events_path"].write_text("{not-json", encoding="utf-8")
    active = _execution(execution_id="active-1", now=now, status="running")
    paths["effect_executions_path"].write_text(
        json.dumps({active.execution_id: active.model_dump(mode="json")}),
        encoding="utf-8",
    )

    report = check_orchestration_json_finalization(
        **paths,
        retention_days=30,
        now=now,
    )

    assert report.safe_to_apply is False
    assert {blocker.reason for blocker in report.blockers} == {
        "unreadable_source",
        "missing_nonterminal_record",
    }
    with pytest.raises(OrchestrationJsonFinalizationBlocked):
        finalize_orchestration_json_store(
            **paths,
            archive_root=tmp_path / "archives",
            retention_days=30,
            now=now,
        )
    assert paths["trigger_events_path"].exists()
    assert not paths["marker_path"].exists()


def test_finalization_imports_retained_history_and_archives_expired_history(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=31)
    paths = _paths(tmp_path)
    ensure_orchestration_schema(paths["database_path"])

    delivered_trigger = TriggerQueueRecord(
        event_id="trigger-current",
        trigger_id="trigger-1",
        trigger_type="trigger:test",
        trace_id="trace-trigger",
        occurred_at=now,
        delivery_state="delivered",
        delivered_at=now,
        idempotency_key="idem-trigger-current",
    )
    expired_effect = _execution(
        execution_id="effect-expired",
        now=old,
        status="succeeded",
    )
    dismissed_gate = _execution(
        execution_id="gate-current",
        now=now,
        status="failed",
        gate=True,
        dismissed_at=now,
    )
    workflow_event = WorkflowChangeEvent(
        event_id="workflow-current",
        event_type="workflow.test",
        occurred_at=now,
        source="test",
    )
    paths["trigger_events_path"].write_text(
        json.dumps({delivered_trigger.event_id: delivered_trigger.model_dump(mode="json")}),
        encoding="utf-8",
    )
    paths["effect_executions_path"].write_text(
        json.dumps({expired_effect.execution_id: expired_effect.model_dump(mode="json")}),
        encoding="utf-8",
    )
    paths["gate_executions_path"].write_text(
        json.dumps({dismissed_gate.execution_id: dismissed_gate.model_dump(mode="json")}),
        encoding="utf-8",
    )
    paths["workflow_events_path"].write_text(
        json.dumps(workflow_event.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    result = finalize_orchestration_json_store(
        **paths,
        archive_root=tmp_path / "archives",
        retention_days=30,
        lock_paths=(tmp_path / "trigger.lock", tmp_path / "effect.lock"),
        now=now,
    )

    assert result.applied is True
    assert all(
        not path.exists()
        for key, path in paths.items()
        if key.endswith("_path") and key not in {"database_path", "marker_path"}
    )
    assert SqliteTriggerEventQueue(paths["database_path"]).contains("trigger-current")
    assert SqliteExecutionStore(paths["database_path"]).get_execution("effect-expired") is None
    assert (
        SqliteGateExecutionStore(paths["database_path"]).get_execution("gate-current") is not None
    )
    events, _cursor = SqliteWorkflowEventJournal(paths["database_path"]).read_after(0)
    assert [event.event_id for event in events] == ["workflow-current"]

    archive_path = Path(result.archive_path or "")
    manifest = json.loads((archive_path / "manifest.json").read_text(encoding="utf-8"))
    marker = json.loads(paths["marker_path"].read_text(encoding="utf-8"))
    assert manifest["state"] == "complete"
    assert marker["state"] == "complete"
    assert len(manifest["sources"]) == 4

    repeated = check_orchestration_json_finalization(
        **paths,
        retention_days=30,
        now=now,
    )
    assert repeated.safe_to_apply is True
    assert repeated.already_finalized is True


def test_completed_marker_is_rejected_for_a_different_database(tmp_path: Path) -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    paths = _paths(tmp_path)
    ensure_orchestration_schema(paths["database_path"])
    finalize_orchestration_json_store(
        **paths,
        archive_root=tmp_path / "archives",
        retention_days=30,
        now=now,
    )
    other_database = tmp_path / "other-orchestration.db"
    ensure_orchestration_schema(other_database)
    other_paths = {**paths, "database_path": other_database}

    report = check_orchestration_json_finalization(
        **other_paths,
        retention_days=30,
        now=now,
    )

    assert report.safe_to_apply is False
    assert report.already_finalized is False
    assert [blocker.reason for blocker in report.blockers] == ["marker_database_mismatch"]
    with pytest.raises(OrchestrationJsonFinalizationBlocked):
        finalize_orchestration_json_store(
            **other_paths,
            archive_root=tmp_path / "other-archives",
            retention_days=30,
            now=now,
        )


def test_finalization_apply_requires_worker_locks_to_be_free(tmp_path: Path) -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    paths = _paths(tmp_path)
    ensure_orchestration_schema(paths["database_path"])
    lock_path = tmp_path / "trigger.lock"

    with single_instance_lock(lock_path):
        with pytest.raises(OrchestrationJsonFinalizationBlocked) as exc_info:
            finalize_orchestration_json_store(
                **paths,
                archive_root=tmp_path / "archives",
                retention_days=30,
                lock_paths=(lock_path,),
                now=now,
            )

    assert [blocker.reason for blocker in exc_info.value.report.blockers] == ["worker_running"]
    assert not paths["marker_path"].exists()


def test_cli_check_does_not_initialize_a_missing_runtime_directory(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "mistyped-runtime"

    result = CliRunner().invoke(
        app,
        [
            "finalize-orchestration-json-store",
            "--runtime-dir",
            str(runtime_dir),
            "--check",
        ],
    )

    assert result.exit_code != 0
    assert "does not contain host-config.json" in result.output
    assert not runtime_dir.exists()


def test_cli_check_with_valid_config_does_not_initialize_runtime_metadata(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    save_host_config(HostConfig(runtime_dir=runtime_dir))
    worker_config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "orchestration_store_backend": "sqlite",
            }
        ),
        encoding="utf-8",
    )
    ensure_orchestration_schema(runtime_dir / "orchestration.db")
    before_paths = {
        path.relative_to(runtime_dir) for path in runtime_dir.rglob("*") if path.is_file()
    }
    before_metadata = {
        path.relative_to(runtime_dir): path.read_bytes()
        for path in runtime_dir.rglob("*")
        if path.is_file() and not path.name.startswith("orchestration.db")
    }

    result = CliRunner().invoke(
        app,
        [
            "finalize-orchestration-json-store",
            "--runtime-dir",
            str(runtime_dir),
            "--check",
            "--json",
        ],
    )

    after_paths = {
        path.relative_to(runtime_dir) for path in runtime_dir.rglob("*") if path.is_file()
    }
    after_metadata = {
        path.relative_to(runtime_dir): path.read_bytes()
        for path in runtime_dir.rglob("*")
        if path.is_file() and not path.name.startswith("orchestration.db")
    }
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["safe_to_apply"] is True
    assert after_paths == before_paths
    assert after_metadata == before_metadata


def test_finalizer_operations_examples_pass_runtime_directory() -> None:
    operations = (Path(__file__).resolve().parents[1] / "docs" / "operations.md").read_text(
        encoding="utf-8"
    )

    assert "--runtime-dir runtime --check" in operations
    assert "--runtime-dir runtime --apply" in operations
    assert "--config runtime/host-config.json" not in operations
