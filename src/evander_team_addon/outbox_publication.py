from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evander.config import GatewayConfig
from evander.events.internal import InternalEventBus, InternalEventSource
from evander.orchestration.host_polling import notify_wake_socket_for_lock
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config

from .services.commands.outbox import TEAM_OUTBOX_PUBLISH_REQUESTED_EVENT_TYPE


def request_team_outbox_publication(
    context: Any,
    *,
    correlation_key: str,
) -> dict[str, object]:
    """Persist and signal a prompt outbox-publisher workflow wake.

    The Team outbox is already committed before this helper is called. If the
    trigger event or socket notification cannot be created, the periodic Team
    runtime tick remains the recovery path rather than rolling back the
    completed mutation.
    """

    try:
        runtime_dir = _context_runtime_dir(context)
        project_dir = (
            Path(context.project_dir)
            if getattr(context, "project_dir", None) is not None
            else runtime_dir.parent
        )
        context_config = getattr(context, "config", None)
        base_config = (
            context_config
            if isinstance(context_config, GatewayConfig)
            else GatewayConfig(runtime_dir=runtime_dir)
        )
        worker_config = load_trigger_orchestrator_worker_global_config(
            project_dir,
            base_config=base_config,
        )
        bus = InternalEventBus(
            worker_config.internal_event_store_path,
            journal_path=worker_config.workflow_event_journal_path,
        )
        event = bus.publish(
            event_type=TEAM_OUTBOX_PUBLISH_REQUESTED_EVENT_TYPE,
            payload={},
            correlation_key=correlation_key,
            idempotency_key=(f"{TEAM_OUTBOX_PUBLISH_REQUESTED_EVENT_TYPE}:{correlation_key}"),
            source=InternalEventSource(
                source_type="team-addon",
                source_name="team-addon",
            ),
        )
        wake_notified = notify_wake_socket_for_lock(worker_config.lock_path)
    except Exception as exc:  # noqa: BLE001
        return {
            "event_ids": [],
            "wake_notified": False,
            "error": str(exc),
        }
    return {
        "event_ids": [event.event_id],
        "wake_notified": wake_notified,
    }


def _context_runtime_dir(context: Any) -> Path:
    runtime_dir = getattr(context, "runtime_dir", None)
    if runtime_dir is None:
        runtime_dir = getattr(getattr(context, "resolved_config", None), "runtime_dir", None)
    if runtime_dir is None:
        provider_config = getattr(context, "provider_config", None)
        if isinstance(provider_config, Mapping):
            runtime_dir = provider_config.get("runtime_dir")
    if runtime_dir is None:
        raise RuntimeError("Team outbox publication requires a runtime directory")
    return Path(runtime_dir)


def request_team_outbox_publication_for_records(
    context: Any,
    *,
    outbox_record_ids: Sequence[str],
) -> dict[str, object] | None:
    normalized_ids = sorted(
        {
            text
            for record_id in outbox_record_ids
            if (text := str(record_id).strip())
        }
    )
    if not normalized_ids:
        return None
    digest = hashlib.sha256("\n".join(normalized_ids).encode("utf-8")).hexdigest()
    result = request_team_outbox_publication(
        context,
        correlation_key=f"outbox-records:{digest}",
    )
    return {
        "outbox_record_ids": normalized_ids,
        **result,
    }


__all__ = [
    "request_team_outbox_publication",
    "request_team_outbox_publication_for_records",
]
