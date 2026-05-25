from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent_sandbox.admin.service import AdminService
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.models import VmSummary
from agent_sandbox.orchestration.host_polling import notify_wake_socket_for_lock
from agent_sandbox.triggers.queue import SqliteTriggerEventQueue, TriggerEventQueue

logger = logging.getLogger(__name__)

_DEFAULT_TRIGGER_ID = "__await-agent-completion__"
_DEFAULT_VM_USER = "agent"
_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_DEFAULT_TIMEOUT_SECONDS = 24 * 60 * 60


def build_agent_run_completed_event(
    *,
    trigger_id: str,
    dispatch_id: str,
    vm_user: str,
    summary: VmSummary,
    session_id: str | None,
    directory_path: str | None,
    previous_state: str = "running",
) -> TriggerEvent:
    updated_at = summary.last_state_update_at or datetime.now(UTC)
    payload: dict[str, object] = {
        "vm": summary.model_dump(mode="json"),
        "vm_name": summary.vm_name,
        "provider": summary.provider,
        "session_ids": list(summary.session_ids),
        "labels": dict(summary.labels),
        "directory_paths": list(summary.directory_paths),
        "agent_repo_names": list(summary.agent_repo_names),
        "power_state": summary.power_state,
        "libvirt_state": summary.libvirt_state,
        "provider_state": summary.provider_state,
        "agent_state": summary.agent_state,
        "previous_agent_state": previous_state,
        "transition": {"from": previous_state, "to": summary.agent_state},
        "last_state_update_at": updated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "scope": {
            "directory_path": directory_path,
            "vm_name": summary.vm_name,
            "session_id": session_id,
            "vm_user": vm_user,
        },
        "dispatch_id": dispatch_id,
    }
    if summary.ip_address is not None:
        payload["ip_address"] = summary.ip_address
    return TriggerEvent(
        event_id=f"{trigger_id}:{dispatch_id}:{updated_at.astimezone(UTC).isoformat()}",
        trigger_id=trigger_id,
        trigger_type="trigger:agent:run:completed",
        occurred_at=updated_at.astimezone(UTC),
        payload=payload,
        trace_id=str(uuid4()),
    )


def await_agent_completion_and_publish(
    *,
    runtime_dir: Path,
    vm_name: str,
    dispatch_id: str,
    event_queue_store_path: Path | None = None,
    workflow_event_journal_path: Path | None = None,
    orchestration_store_backend: str = "json",
    orchestration_store_path: Path | None = None,
    orchestration_retention_days: int | None = None,
    vm_user: str = _DEFAULT_VM_USER,
    session_id: str | None = None,
    directory_path: str | None = None,
    trigger_id: str = _DEFAULT_TRIGGER_ID,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    admin_service = AdminService(runtime_dir=runtime_dir)
    if orchestration_store_backend == "sqlite":
        event_queue = SqliteTriggerEventQueue(
            orchestration_store_path or runtime_dir / "orchestration.db",
            retention_days=orchestration_retention_days,
        )
    else:
        queue_kwargs = (
            {"journal_path": workflow_event_journal_path}
            if workflow_event_journal_path is not None
            else {}
        )
        event_queue = TriggerEventQueue(
            event_queue_store_path or runtime_dir / "trigger-orchestrator-events.json",
            **queue_kwargs,
        )
    started_at = time.monotonic()

    while time.monotonic() - started_at < timeout_seconds:
        summaries = admin_service.list_vms(
            include_inactive=True,
            omit_state=False,
            vm_user=vm_user,
            directory_path=None,
            direct_only=False,
        )
        matching = next(
            (
                summary
                for summary in summaries
                if summary.vm_name == vm_name
                and str(summary.dispatch_id or "").strip() == dispatch_id
            ),
            None,
        )
        if matching is not None and matching.agent_state != "running":
            event = build_agent_run_completed_event(
                trigger_id=trigger_id,
                dispatch_id=dispatch_id,
                vm_user=vm_user,
                summary=matching,
                session_id=session_id,
                directory_path=directory_path,
            )
            already_queued = event_queue.contains(event.event_id)
            record = event_queue.publish(event, idempotency_key=f"{trigger_id}:{dispatch_id}")
            if not already_queued and record.event_id == event.event_id:
                notify_wake_socket_for_lock(runtime_dir / "effect-orchestrator.lock")
            return True
        time.sleep(max(0.1, poll_interval_seconds))
    return False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Wait for a dispatched agent run to settle and publish a completion event."
    )
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--vm-name", required=True)
    parser.add_argument("--dispatch-id", required=True)
    parser.add_argument("--event-queue-store-path")
    parser.add_argument("--workflow-event-journal-path")
    parser.add_argument("--orchestration-store-backend", choices=("json", "sqlite"), default="json")
    parser.add_argument("--orchestration-store-path")
    parser.add_argument("--orchestration-retention-days", type=int)
    parser.add_argument("--vm-user", default=_DEFAULT_VM_USER)
    parser.add_argument("--session-id")
    parser.add_argument("--directory-path")
    parser.add_argument("--trigger-id", default=_DEFAULT_TRIGGER_ID)
    parser.add_argument(
        "--poll-interval-seconds", type=float, default=_DEFAULT_POLL_INTERVAL_SECONDS
    )
    parser.add_argument("--timeout-seconds", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    published = await_agent_completion_and_publish(
        runtime_dir=Path(args.runtime_dir),
        vm_name=args.vm_name,
        dispatch_id=args.dispatch_id,
        event_queue_store_path=Path(args.event_queue_store_path)
        if args.event_queue_store_path
        else None,
        workflow_event_journal_path=Path(args.workflow_event_journal_path)
        if args.workflow_event_journal_path
        else None,
        orchestration_store_backend=args.orchestration_store_backend,
        orchestration_store_path=Path(args.orchestration_store_path)
        if args.orchestration_store_path
        else None,
        orchestration_retention_days=args.orchestration_retention_days,
        vm_user=args.vm_user,
        session_id=args.session_id,
        directory_path=args.directory_path,
        trigger_id=args.trigger_id,
        poll_interval_seconds=args.poll_interval_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    if not published:
        logger.warning(
            "Agent completion watcher timed out vm_name=%s dispatch_id=%s session_id=%s",
            args.vm_name,
            args.dispatch_id,
            args.session_id,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
