from __future__ import annotations

from evander.admin.operations_helpers import (
    _journalctl_tail,
    _run_systemd_control,
    _systemctl_show_many,
)
from evander.admin.services.snapshot_cache import SnapshotCache
from evander.admin.worker_config import _WORKER_DEFINITIONS, _worker_definition
from evander.models import WorkerActionResult, WorkerSummary


class WorkerControlAdminService:
    def __init__(self) -> None:
        self._snapshots: SnapshotCache[list[WorkerSummary]] = SnapshotCache(
            ttl_seconds=1.0,
            max_entries=1,
        )
        self._generation = 0

    def list_background_workers(self) -> list[WorkerSummary]:
        return [
            summary.model_copy(deep=True)
            for summary in self._snapshots.read(self._generation, self._collect_background_workers)
        ]

    def _collect_background_workers(self) -> list[WorkerSummary]:
        summaries: list[WorkerSummary] = []
        try:
            payloads = _systemctl_show_many([unit for _, _, unit in _WORKER_DEFINITIONS])
            error = None
        except RuntimeError as exc:
            payloads = {}
            error = str(exc)
        for worker_id, name, unit in _WORKER_DEFINITIONS:
            payload = payloads.get(unit)
            if payload is None:
                summaries.append(
                    WorkerSummary(
                        worker_id=worker_id,
                        name=name,
                        unit=unit,
                        active_state="unavailable",
                        running=False,
                        failed=False,
                        detail=error or f"No systemctl state returned for {unit}",
                    )
                )
                continue

            active_state = payload.get("ActiveState", "") or "unknown"
            sub_state = payload.get("SubState", "") or None
            result = payload.get("Result", "") or None
            unit_file_state = payload.get("UnitFileState", "") or None
            description = payload.get("Description", "") or None
            main_pid_raw = payload.get("MainPID", "").strip()
            main_pid = (
                int(main_pid_raw) if main_pid_raw.isdigit() and int(main_pid_raw) > 0 else None
            )
            detail_parts = []
            if payload.get("LoadState", "").strip():
                detail_parts.append(f"load={payload['LoadState'].strip()}")
            exec_code = payload.get("ExecMainCode", "").strip()
            exec_status = payload.get("ExecMainStatus", "").strip()
            if exec_code or exec_status:
                detail_parts.append(f"exec={exec_code or '?'}:{exec_status or '?'}")

            summaries.append(
                WorkerSummary(
                    worker_id=worker_id,
                    name=name,
                    unit=unit,
                    active_state=active_state,
                    sub_state=sub_state,
                    result=result,
                    unit_file_state=unit_file_state,
                    description=description,
                    main_pid=main_pid,
                    running=active_state == "active",
                    failed=active_state == "failed" or result == "failed",
                    detail=", ".join(detail_parts) or None,
                )
            )

        return summaries

    def get_background_worker_log_snapshot(self, worker_id: str, *, lines: int = 200) -> str:
        worker = _worker_definition(worker_id)
        if worker is None:
            return f"Worker log unavailable: unknown worker '{worker_id}'."
        _, name, unit = worker
        text = _journalctl_tail(unit, lines=lines)
        return f"Worker: {name}\nUnit: {unit}\n\n{text}".strip()

    def start_background_worker(self, worker_id: str) -> WorkerActionResult:
        worker = _worker_definition(worker_id)
        if worker is None:
            return WorkerActionResult(
                success=False,
                worker_id=worker_id,
                unit="",
                action="start",
                message=f"Unknown worker: {worker_id}",
            )
        resolved_worker_id, name, unit = worker
        completed = _run_systemd_control("start", unit)
        self._generation += 1
        message = (completed.stdout or completed.stderr or "").strip() or f"Started {name}"
        return WorkerActionResult(
            success=completed.returncode == 0,
            worker_id=resolved_worker_id,
            unit=unit,
            action="start",
            message=message,
        )

    def stop_background_worker(self, worker_id: str) -> WorkerActionResult:
        worker = _worker_definition(worker_id)
        if worker is None:
            return WorkerActionResult(
                success=False,
                worker_id=worker_id,
                unit="",
                action="stop",
                message=f"Unknown worker: {worker_id}",
            )
        resolved_worker_id, name, unit = worker
        completed = _run_systemd_control("stop", unit)
        self._generation += 1
        message = (completed.stdout or completed.stderr or "").strip() or f"Stopped {name}"
        return WorkerActionResult(
            success=completed.returncode == 0,
            worker_id=resolved_worker_id,
            unit=unit,
            action="stop",
            message=message,
        )
