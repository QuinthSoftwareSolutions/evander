from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from evander.daemons.effect_orchestrator import EffectExecutionRecord, GateExecutionRecord
from evander.orchestration.host_polling import single_instance_lock
from evander.repositories.orchestration_db import connect_orchestration_db, json_dumps
from evander.triggers.queue import SqliteTriggerEventQueue, TriggerQueueRecord
from evander.workflow.events import WorkflowChangeEvent

FINALIZATION_VERSION = 1
_EXPECTED_TABLES = frozenset(
    {"trigger_events", "effect_executions", "gate_executions", "workflow_events"}
)
_TERMINAL_EXECUTION_STATUSES = frozenset({"succeeded", "skipped", "dropped"})


@dataclass(frozen=True)
class OrchestrationJsonFinalizationBlocker:
    source: str
    record_id: str | None
    reason: str
    detail: str


@dataclass(frozen=True)
class OrchestrationJsonSourceReport:
    source: str
    path: str
    present: bool
    sha256: str | None = None
    records: int = 0
    existing: int = 0
    importable: int = 0
    archive_only: int = 0
    unclassified: int = 0
    invalid: int = 0


@dataclass(frozen=True)
class OrchestrationJsonFinalizationReport:
    database_path: str
    marker_path: str
    checked_at: str
    retention_days: int
    safe_to_apply: bool
    already_finalized: bool
    sources: tuple[OrchestrationJsonSourceReport, ...]
    blockers: tuple[OrchestrationJsonFinalizationBlocker, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrchestrationJsonFinalizationResult:
    report: OrchestrationJsonFinalizationReport
    applied: bool
    archive_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = self.report.as_dict()
        payload.update({"applied": self.applied, "archive_path": self.archive_path})
        return payload


class OrchestrationJsonFinalizationBlocked(RuntimeError):
    def __init__(self, report: OrchestrationJsonFinalizationReport) -> None:
        self.report = report
        super().__init__(
            "orchestration JSON finalization is blocked; inspect the finalization report"
        )


@dataclass(frozen=True)
class _DatabaseIndex:
    trigger_ids: frozenset[str]
    trigger_idempotency_keys: frozenset[str]
    effect_ids: frozenset[str]
    effect_idempotency_keys: frozenset[str]
    gate_ids: frozenset[str]
    gate_idempotency_keys: frozenset[str]
    workflow_event_ids: frozenset[str]


@dataclass(frozen=True)
class _SourceScan:
    report: OrchestrationJsonSourceReport
    trigger_records: tuple[TriggerQueueRecord, ...] = ()
    effect_records: tuple[EffectExecutionRecord, ...] = ()
    gate_records: tuple[GateExecutionRecord, ...] = ()
    workflow_events: tuple[WorkflowChangeEvent, ...] = ()


@dataclass(frozen=True)
class _FinalizationPlan:
    report: OrchestrationJsonFinalizationReport
    scans: tuple[_SourceScan, ...]


def check_orchestration_json_finalization(
    *,
    database_path: Path,
    trigger_events_path: Path,
    effect_executions_path: Path,
    gate_executions_path: Path,
    workflow_events_path: Path,
    marker_path: Path,
    retention_days: int,
    now: datetime | None = None,
) -> OrchestrationJsonFinalizationReport:
    return _build_finalization_plan(
        database_path=database_path,
        trigger_events_path=trigger_events_path,
        effect_executions_path=effect_executions_path,
        gate_executions_path=gate_executions_path,
        workflow_events_path=workflow_events_path,
        marker_path=marker_path,
        retention_days=retention_days,
        now=now,
    ).report


def finalize_orchestration_json_store(
    *,
    database_path: Path,
    trigger_events_path: Path,
    effect_executions_path: Path,
    gate_executions_path: Path,
    workflow_events_path: Path,
    marker_path: Path,
    archive_root: Path,
    retention_days: int,
    lock_paths: tuple[Path, ...] = (),
    now: datetime | None = None,
) -> OrchestrationJsonFinalizationResult:
    checked_at = _utc(now or datetime.now(UTC))
    initial_plan = _build_finalization_plan(
        database_path=database_path,
        trigger_events_path=trigger_events_path,
        effect_executions_path=effect_executions_path,
        gate_executions_path=gate_executions_path,
        workflow_events_path=workflow_events_path,
        marker_path=marker_path,
        retention_days=retention_days,
        now=checked_at,
    )
    if initial_plan.report.already_finalized and initial_plan.report.safe_to_apply:
        return OrchestrationJsonFinalizationResult(
            report=initial_plan.report,
            applied=False,
            archive_path=_completed_marker_archive_path(marker_path),
        )
    if not initial_plan.report.safe_to_apply:
        raise OrchestrationJsonFinalizationBlocked(initial_plan.report)

    with ExitStack() as stack:
        for lock_path in dict.fromkeys(Path(path) for path in lock_paths):
            try:
                stack.enter_context(
                    single_instance_lock(
                        lock_path,
                        busy_error_text="orchestration worker must be stopped before finalization",
                    )
                )
            except RuntimeError as exc:
                blocker = OrchestrationJsonFinalizationBlocker(
                    source="worker_lock",
                    record_id=None,
                    reason="worker_running",
                    detail=str(exc),
                )
                blocked_report = replace(
                    initial_plan.report,
                    safe_to_apply=False,
                    blockers=(*initial_plan.report.blockers, blocker),
                )
                raise OrchestrationJsonFinalizationBlocked(blocked_report) from exc

        plan = _build_finalization_plan(
            database_path=database_path,
            trigger_events_path=trigger_events_path,
            effect_executions_path=effect_executions_path,
            gate_executions_path=gate_executions_path,
            workflow_events_path=workflow_events_path,
            marker_path=marker_path,
            retention_days=retention_days,
            now=checked_at,
        )
        if not plan.report.safe_to_apply:
            raise OrchestrationJsonFinalizationBlocked(plan.report)

        archive_path = _create_archive(plan, Path(archive_root), checked_at)
        _import_plan(Path(database_path), plan)
        _verify_source_fingerprints(plan)

        marker_payload = {
            "version": FINALIZATION_VERSION,
            "state": "archived",
            "database_path": str(Path(database_path).expanduser().resolve()),
            "archive_path": str(archive_path.expanduser().resolve()),
            "started_at": checked_at.isoformat(),
            "retention_days": retention_days,
        }
        _write_json_atomic(Path(marker_path), marker_payload)

        for scan in plan.scans:
            if scan.report.present:
                Path(scan.report.path).unlink()
        recreated = [scan.report.path for scan in plan.scans if Path(scan.report.path).exists()]
        if recreated:
            raise RuntimeError(
                "legacy orchestration JSON source was recreated during finalization: "
                + ", ".join(recreated)
            )

        completed_at = datetime.now(UTC)
        manifest_path = archive_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["state"] = "complete"
        manifest["completed_at"] = completed_at.isoformat()
        _write_json_atomic(manifest_path, manifest)
        marker_payload["state"] = "complete"
        marker_payload["completed_at"] = completed_at.isoformat()
        _write_json_atomic(Path(marker_path), marker_payload)

    completed_report = replace(
        plan.report,
        safe_to_apply=True,
        already_finalized=True,
    )
    return OrchestrationJsonFinalizationResult(
        report=completed_report,
        applied=True,
        archive_path=str(archive_path),
    )


def _build_finalization_plan(
    *,
    database_path: Path,
    trigger_events_path: Path,
    effect_executions_path: Path,
    gate_executions_path: Path,
    workflow_events_path: Path,
    marker_path: Path,
    retention_days: int,
    now: datetime | None,
) -> _FinalizationPlan:
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")
    checked_at = _utc(now or datetime.now(UTC))
    blockers: list[OrchestrationJsonFinalizationBlocker] = []
    marker = _read_marker(Path(marker_path), blockers)
    if marker is not None and marker.get("state") == "complete":
        marker_database_value = marker.get("database_path")
        requested_database_path = Path(database_path).expanduser().resolve()
        try:
            marker_database_path = (
                Path(str(marker_database_value)).expanduser().resolve()
                if marker_database_value
                else None
            )
        except (OSError, RuntimeError, ValueError):
            marker_database_path = None
        if marker_database_path != requested_database_path:
            blockers.append(
                OrchestrationJsonFinalizationBlocker(
                    source="marker",
                    record_id=None,
                    reason="marker_database_mismatch",
                    detail=(
                        f"completed marker database_path {marker_database_value!r} does not "
                        f"match requested database {requested_database_path}"
                    ),
                )
            )
        archive_value = marker.get("archive_path")
        archive_manifest = Path(str(archive_value)) / "manifest.json" if archive_value else None
        try:
            archived_payload = (
                json.loads(archive_manifest.read_text(encoding="utf-8"))
                if archive_manifest is not None
                else None
            )
        except (OSError, json.JSONDecodeError) as exc:
            archived_payload = None
            archive_error = str(exc)
        else:
            archive_error = "archive_path or complete manifest is missing"
        if (
            not isinstance(archived_payload, dict)
            or archived_payload.get("version") != FINALIZATION_VERSION
            or archived_payload.get("state") != "complete"
        ):
            blockers.append(
                OrchestrationJsonFinalizationBlocker(
                    source="marker",
                    record_id=None,
                    reason="invalid_completed_marker",
                    detail=(
                        f"completed marker archive cannot be validated at {archive_manifest}: "
                        f"{archive_error}"
                    ),
                )
            )
    database_index = _read_database_index(Path(database_path), blockers)
    cutoff = checked_at - timedelta(days=retention_days)

    scans = (
        _scan_object_source(
            source="trigger_events",
            path=Path(trigger_events_path),
            model=TriggerQueueRecord,
            id_field="event_id",
            existing_ids=(database_index.trigger_ids if database_index else frozenset()),
            existing_idempotency_keys=(
                database_index.trigger_idempotency_keys if database_index else frozenset()
            ),
            database_available=database_index is not None,
            cutoff=cutoff,
            blockers=blockers,
        ),
        _scan_object_source(
            source="effect_executions",
            path=Path(effect_executions_path),
            model=EffectExecutionRecord,
            id_field="execution_id",
            existing_ids=(database_index.effect_ids if database_index else frozenset()),
            existing_idempotency_keys=(
                database_index.effect_idempotency_keys if database_index else frozenset()
            ),
            database_available=database_index is not None,
            cutoff=cutoff,
            blockers=blockers,
        ),
        _scan_object_source(
            source="gate_executions",
            path=Path(gate_executions_path),
            model=GateExecutionRecord,
            id_field="execution_id",
            existing_ids=(database_index.gate_ids if database_index else frozenset()),
            existing_idempotency_keys=(
                database_index.gate_idempotency_keys if database_index else frozenset()
            ),
            database_available=database_index is not None,
            cutoff=cutoff,
            blockers=blockers,
        ),
        _scan_workflow_events(
            Path(workflow_events_path),
            existing_ids=(database_index.workflow_event_ids if database_index else frozenset()),
            database_available=database_index is not None,
            cutoff=cutoff,
            blockers=blockers,
        ),
    )

    source_present = any(scan.report.present for scan in scans)
    if marker is not None and marker.get("state") != "complete":
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source="marker",
                record_id=None,
                reason="incomplete_previous_finalization",
                detail=(
                    f"marker {marker_path} is in state {marker.get('state')!r}; "
                    "inspect its archive_path before recovery"
                ),
            )
        )
    elif marker is not None and source_present:
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source="marker",
                record_id=None,
                reason="source_recreated_after_finalization",
                detail="a completed marker exists but one or more legacy JSON sources reappeared",
            )
        )

    already_finalized = bool(
        marker and marker.get("state") == "complete" and not source_present and not blockers
    )

    report = OrchestrationJsonFinalizationReport(
        database_path=str(Path(database_path)),
        marker_path=str(Path(marker_path)),
        checked_at=checked_at.isoformat(),
        retention_days=retention_days,
        safe_to_apply=not blockers,
        already_finalized=already_finalized,
        sources=tuple(scan.report for scan in scans),
        blockers=tuple(blockers),
    )
    return _FinalizationPlan(report=report, scans=scans)


def _read_marker(
    path: Path, blockers: list[OrchestrationJsonFinalizationBlocker]
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source="marker",
                record_id=None,
                reason="invalid_marker",
                detail=f"cannot read {path}: {exc}",
            )
        )
        return None
    if not isinstance(payload, dict) or payload.get("version") != FINALIZATION_VERSION:
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source="marker",
                record_id=None,
                reason="invalid_marker",
                detail=f"{path} must be a version {FINALIZATION_VERSION} JSON object",
            )
        )
        return None
    return payload


def _read_database_index(
    path: Path, blockers: list[OrchestrationJsonFinalizationBlocker]
) -> _DatabaseIndex | None:
    if not path.is_file():
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source="database",
                record_id=None,
                reason="database_missing",
                detail=f"SQLite orchestration database does not exist at {path}",
            )
        )
        return None
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or str(quick_check[0]).casefold() != "ok":
                raise ValueError(f"PRAGMA quick_check returned {quick_check!r}")
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            missing_tables = sorted(_EXPECTED_TABLES - tables)
            if missing_tables:
                raise ValueError("missing tables: " + ", ".join(missing_tables))
            trigger_rows = connection.execute(
                "SELECT event_id, idempotency_key FROM trigger_events"
            ).fetchall()
            effect_rows = connection.execute(
                "SELECT execution_id, idempotency_key FROM effect_executions"
            ).fetchall()
            gate_rows = connection.execute(
                "SELECT execution_id, idempotency_key FROM gate_executions"
            ).fetchall()
            workflow_rows = connection.execute("SELECT event_id FROM workflow_events").fetchall()
    except (OSError, sqlite3.Error, ValueError) as exc:
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source="database",
                record_id=None,
                reason="database_invalid",
                detail=f"cannot validate {path}: {exc}",
            )
        )
        return None
    return _DatabaseIndex(
        trigger_ids=frozenset(str(row["event_id"]) for row in trigger_rows),
        trigger_idempotency_keys=frozenset(
            str(row["idempotency_key"])
            for row in trigger_rows
            if row["idempotency_key"] is not None
        ),
        effect_ids=frozenset(str(row["execution_id"]) for row in effect_rows),
        effect_idempotency_keys=frozenset(str(row["idempotency_key"]) for row in effect_rows),
        gate_ids=frozenset(str(row["execution_id"]) for row in gate_rows),
        gate_idempotency_keys=frozenset(str(row["idempotency_key"]) for row in gate_rows),
        workflow_event_ids=frozenset(str(row["event_id"]) for row in workflow_rows),
    )


def _scan_object_source(
    *,
    source: str,
    path: Path,
    model: type[TriggerQueueRecord] | type[EffectExecutionRecord] | type[GateExecutionRecord],
    id_field: Literal["event_id", "execution_id"],
    existing_ids: frozenset[str],
    existing_idempotency_keys: frozenset[str],
    database_available: bool,
    cutoff: datetime,
    blockers: list[OrchestrationJsonFinalizationBlocker],
) -> _SourceScan:
    payload, digest = _read_json_object_strict(source, path, blockers)
    if payload is None:
        return _SourceScan(
            report=OrchestrationJsonSourceReport(
                source=source,
                path=str(path),
                present=path.exists(),
                sha256=digest,
                invalid=1 if path.exists() else 0,
            )
        )

    existing = importable = archive_only = unclassified = invalid = 0
    import_records: list[Any] = []
    seen_idempotency: dict[str, str] = {}
    for record_key, raw_record in payload.items():
        record_key_text = str(record_key)
        if not isinstance(raw_record, dict):
            invalid += 1
            _append_invalid_record(blockers, source, record_key_text, "record must be an object")
            continue
        candidate = dict(raw_record)
        candidate.setdefault(id_field, record_key_text)
        try:
            record = model.model_validate(candidate)
        except ValidationError as exc:
            invalid += 1
            _append_invalid_record(blockers, source, record_key_text, str(exc))
            continue
        record_id = str(getattr(record, id_field))
        if record_id != record_key_text:
            invalid += 1
            _append_invalid_record(
                blockers,
                source,
                record_key_text,
                f"object key does not match embedded {id_field} {record_id!r}",
            )
            continue
        idempotency_key = str(getattr(record, "idempotency_key", "") or "").strip()
        if idempotency_key:
            previous_id = seen_idempotency.setdefault(idempotency_key, record_id)
            if previous_id != record_id:
                invalid += 1
                _append_invalid_record(
                    blockers,
                    source,
                    record_id,
                    f"duplicate idempotency_key also used by {previous_id}",
                )
                continue
        if not database_available:
            unclassified += 1
            continue
        if record_id in existing_ids or (
            idempotency_key and idempotency_key in existing_idempotency_keys
        ):
            existing += 1
            continue
        terminal_at = _terminal_record_time(record)
        if terminal_at is None:
            blockers.append(
                OrchestrationJsonFinalizationBlocker(
                    source=source,
                    record_id=record_id,
                    reason="missing_nonterminal_record",
                    detail=_nonterminal_detail(record),
                )
            )
            continue
        if terminal_at < cutoff:
            archive_only += 1
            continue
        importable += 1
        import_records.append(record)

    report = OrchestrationJsonSourceReport(
        source=source,
        path=str(path),
        present=path.exists(),
        sha256=digest,
        records=len(payload),
        existing=existing,
        importable=importable,
        archive_only=archive_only,
        unclassified=unclassified,
        invalid=invalid,
    )
    kwargs: dict[str, Any] = {"report": report}
    if model is TriggerQueueRecord:
        kwargs["trigger_records"] = tuple(import_records)
    elif model is EffectExecutionRecord:
        kwargs["effect_records"] = tuple(import_records)
    else:
        kwargs["gate_records"] = tuple(import_records)
    return _SourceScan(**kwargs)


def _scan_workflow_events(
    path: Path,
    *,
    existing_ids: frozenset[str],
    database_available: bool,
    cutoff: datetime,
    blockers: list[OrchestrationJsonFinalizationBlocker],
) -> _SourceScan:
    source = "workflow_events"
    if not path.exists():
        return _SourceScan(
            report=OrchestrationJsonSourceReport(source=source, path=str(path), present=False)
        )
    if path.is_symlink() or not path.is_file():
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source=source,
                record_id=None,
                reason="invalid_source_type",
                detail=f"legacy source must be a regular non-symlink file: {path}",
            )
        )
        return _SourceScan(
            report=OrchestrationJsonSourceReport(
                source=source,
                path=str(path),
                present=True,
                invalid=1,
            )
        )
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source=source,
                record_id=None,
                reason="unreadable_source",
                detail=f"cannot read {path}: {exc}",
            )
        )
        return _SourceScan(
            report=OrchestrationJsonSourceReport(
                source=source,
                path=str(path),
                present=True,
                invalid=1,
            )
        )

    digest = hashlib.sha256(raw).hexdigest()
    existing = importable = archive_only = unclassified = invalid = records = 0
    import_events: list[WorkflowChangeEvent] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        records += 1
        record_label = f"line:{line_number}"
        try:
            raw_event = json.loads(stripped)
            if not isinstance(raw_event, dict):
                raise ValueError("event must be an object")
            event = WorkflowChangeEvent.model_validate(raw_event)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            invalid += 1
            _append_invalid_record(blockers, source, record_label, str(exc))
            continue
        if event.event_id in seen_ids:
            invalid += 1
            _append_invalid_record(
                blockers,
                source,
                event.event_id,
                "duplicate workflow event_id in JSONL source",
            )
            continue
        seen_ids.add(event.event_id)
        if not database_available:
            unclassified += 1
        elif event.event_id in existing_ids:
            existing += 1
        elif _utc(event.occurred_at) < cutoff:
            archive_only += 1
        else:
            importable += 1
            import_events.append(event)
    return _SourceScan(
        report=OrchestrationJsonSourceReport(
            source=source,
            path=str(path),
            present=True,
            sha256=digest,
            records=records,
            existing=existing,
            importable=importable,
            archive_only=archive_only,
            unclassified=unclassified,
            invalid=invalid,
        ),
        workflow_events=tuple(import_events),
    )


def _read_json_object_strict(
    source: str,
    path: Path,
    blockers: list[OrchestrationJsonFinalizationBlocker],
) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return {}, None
    if path.is_symlink() or not path.is_file():
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source=source,
                record_id=None,
                reason="invalid_source_type",
                detail=f"legacy source must be a regular non-symlink file: {path}",
            )
        )
        return None, None
    try:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source=source,
                record_id=None,
                reason="unreadable_source",
                detail=f"cannot parse {path}: {exc}",
            )
        )
        return None, locals().get("digest")
    if not isinstance(payload, dict):
        blockers.append(
            OrchestrationJsonFinalizationBlocker(
                source=source,
                record_id=None,
                reason="invalid_source_shape",
                detail=f"{path} must contain a JSON object",
            )
        )
        return None, digest
    return payload, digest


def _terminal_record_time(
    record: TriggerQueueRecord | EffectExecutionRecord | GateExecutionRecord,
) -> datetime | None:
    if isinstance(record, TriggerQueueRecord):
        if record.delivery_state != "delivered":
            return None
        return _utc(record.delivered_at or record.occurred_at)
    status = str(record.status).strip().casefold()
    if status in _TERMINAL_EXECUTION_STATUSES:
        return _utc(record.finished_at or record.started_at)
    if status == "failed" and (record.dismissed_at is not None or record.cleared_at is not None):
        return _utc(
            record.dismissed_at or record.cleared_at or record.finished_at or record.started_at
        )
    return None


def _nonterminal_detail(
    record: TriggerQueueRecord | EffectExecutionRecord | GateExecutionRecord,
) -> str:
    if isinstance(record, TriggerQueueRecord):
        return f"delivery_state={record.delivery_state!r} is not terminal in SQLite"
    return (
        f"status={record.status!r} is active or unresolved; failed records require "
        "dismissed_at or cleared_at before archival"
    )


def _append_invalid_record(
    blockers: list[OrchestrationJsonFinalizationBlocker],
    source: str,
    record_id: str,
    detail: str,
) -> None:
    blockers.append(
        OrchestrationJsonFinalizationBlocker(
            source=source,
            record_id=record_id,
            reason="invalid_record",
            detail=detail,
        )
    )


def _create_archive(plan: _FinalizationPlan, archive_root: Path, checked_at: datetime) -> Path:
    archive_path = archive_root / (
        f"orchestration-json-finalization-{checked_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    )
    archive_path.mkdir(parents=True, exist_ok=False)
    archived_sources: list[dict[str, Any]] = []
    for scan in plan.scans:
        if not scan.report.present:
            continue
        source_path = Path(scan.report.path)
        archive_name = f"{scan.report.source}--{source_path.name}"
        destination = archive_path / archive_name
        shutil.copy2(source_path, destination)
        archived_digest = _sha256_file(destination)
        if archived_digest != scan.report.sha256:
            raise RuntimeError(f"source changed while archiving: {source_path}")
        archived_sources.append(
            {
                "source": scan.report.source,
                "original_path": str(source_path),
                "archive_file": archive_name,
                "sha256": archived_digest,
            }
        )
    _write_json_atomic(
        archive_path / "manifest.json",
        {
            "version": FINALIZATION_VERSION,
            "state": "archived",
            "created_at": checked_at.isoformat(),
            "report": plan.report.as_dict(),
            "sources": archived_sources,
        },
    )
    return archive_path


def _import_plan(database_path: Path, plan: _FinalizationPlan) -> None:
    with connect_orchestration_db(database_path) as connection:
        for scan in plan.scans:
            for record in scan.trigger_records:
                SqliteTriggerEventQueue._insert_record(connection, record)
            for record in scan.effect_records:
                _insert_execution(connection, "effect_executions", "effect", record)
            for record in scan.gate_records:
                _insert_execution(connection, "gate_executions", "gate", record)
            for event in scan.workflow_events:
                payload = event.model_dump(mode="json")
                connection.execute(
                    """
                    INSERT INTO workflow_events(
                        event_id, event_type, source, subject_id, occurred_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.event_type,
                        event.source,
                        event.subject_id,
                        _storage_datetime(event.occurred_at),
                        json_dumps(payload),
                    ),
                )


def _insert_execution(
    connection: sqlite3.Connection,
    table: Literal["effect_executions", "gate_executions"],
    node_prefix: Literal["effect", "gate"],
    record: EffectExecutionRecord | GateExecutionRecord,
) -> None:
    node_id_field = f"{node_prefix}_id"
    node_type_field = f"{node_prefix}_type"
    payload = record.model_dump(mode="json")
    connection.execute(
        f"""
        INSERT INTO {table}(
            execution_id, idempotency_key, event_id, trace_id,
            source_node_id, source_trigger_type, status,
            {node_id_field}, {node_type_field}, started_at, finished_at,
            cleared_at, dismissed_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.execution_id,
            record.idempotency_key,
            record.event_id,
            record.trace_id,
            record.source_node_id,
            record.source_trigger_type,
            record.status,
            getattr(record, node_id_field),
            getattr(record, node_type_field),
            _storage_datetime(record.started_at),
            _storage_datetime(record.finished_at),
            _storage_datetime(record.cleared_at),
            _storage_datetime(record.dismissed_at),
            json_dumps(payload),
        ),
    )


def _verify_source_fingerprints(plan: _FinalizationPlan) -> None:
    for scan in plan.scans:
        path = Path(scan.report.path)
        if not scan.report.present:
            if path.exists():
                raise RuntimeError(f"legacy source appeared during finalization: {path}")
            continue
        if not path.is_file() or _sha256_file(path) != scan.report.sha256:
            raise RuntimeError(f"legacy source changed during finalization: {path}")


def _completed_marker_archive_path(marker_path: Path) -> str | None:
    try:
        payload = json.loads(Path(marker_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("archive_path") if isinstance(payload, dict) else None
    return str(value) if value else None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _storage_datetime(value: datetime | None) -> str | None:
    return _utc(value).isoformat().replace("+00:00", "Z") if value is not None else None


__all__ = [
    "OrchestrationJsonFinalizationBlocked",
    "OrchestrationJsonFinalizationReport",
    "OrchestrationJsonFinalizationResult",
    "check_orchestration_json_finalization",
    "finalize_orchestration_json_store",
]
