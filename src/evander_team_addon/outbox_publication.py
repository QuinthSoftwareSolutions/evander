from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evander.config import GatewayConfig
from evander.events.internal import InternalEventSource
from evander.orchestration.host_polling import notify_wake_socket_for_lock
from evander.orchestration.internal_events import build_internal_event_bus
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config

from .services.commands.outbox import TEAM_OUTBOX_PUBLISH_REQUESTED_EVENT_TYPE
from .repository import TeamStateRepository
from .services.domain.wake import AGENT_WAKE_REQUESTED_EVENT_TYPE


def request_team_outbox_publication(
    context: Any,
    *,
    correlation_key: str,
    payload: Mapping[str, Any] | None = None,
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
        bus = build_internal_event_bus(worker_config)
        event = bus.publish(
            event_type=TEAM_OUTBOX_PUBLISH_REQUESTED_EVENT_TYPE,
            payload=dict(payload or {}),
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
        {text for record_id in outbox_record_ids if (text := str(record_id).strip())}
    )
    if not normalized_ids:
        return None
    digest = hashlib.sha256("\n".join(normalized_ids).encode("utf-8")).hexdigest()
    result = request_team_outbox_publication(
        context,
        correlation_key=f"outbox-records:{digest}",
        payload={"outbox_record_ids": normalized_ids},
    )
    return {
        "outbox_record_ids": normalized_ids,
        **result,
    }


def publish_team_wake_events_with_outbox_recovery(
    context: Any,
    *,
    repository: TeamStateRepository,
    events: object,
    outbox_record_ids: Sequence[str],
    publish_events: Callable[[object], list[str]],
) -> dict[str, object]:
    """Publish committed wake events, acknowledge them, and prompt recovery if needed."""

    normalized_ids = sorted(
        {text for record_id in outbox_record_ids if (text := str(record_id).strip())}
    )
    wake_events = (
        [
            event
            for event in events
            if isinstance(event, Mapping)
            and event.get("event_type") == AGENT_WAKE_REQUESTED_EVENT_TYPE
        ]
        if isinstance(events, list)
        else []
    )
    result: dict[str, object] = {
        "outbox_record_ids": normalized_ids,
        "published_event_ids": [],
        "completed_outbox_record_ids": [],
    }

    if wake_events:
        try:
            result["published_event_ids"] = publish_events(wake_events)
        except Exception as exc:  # noqa: BLE001 - recovery must retain the committed intent
            result["event_publish_error"] = str(exc)

    published_event_ids = result["published_event_ids"]
    if isinstance(published_event_ids, list) and published_event_ids:
        idempotency_keys = {
            str(event["idempotency_key"])
            for event in wake_events
            if event.get("idempotency_key") is not None
        }
        completed_ids: list[str] = []
        try:
            for record in repository.list_outbox_records(
                intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE
            ):
                if (
                    record.id not in normalized_ids
                    or record.idempotency_key not in idempotency_keys
                ):
                    continue
                if record.status.value != "completed":
                    repository.complete_outbox_record(record_id=record.id)
                completed_ids.append(record.id)
        except Exception as exc:  # noqa: BLE001 - the pending outbox is the recovery path
            result["outbox_ack_error"] = str(exc)
        result["completed_outbox_record_ids"] = completed_ids

    completed = {
        str(record_id)
        for record_id in result["completed_outbox_record_ids"]
        if str(record_id).strip()
    }
    pending_ids = sorted(set(normalized_ids) - completed)
    publication_wake = request_team_outbox_publication_for_records(
        context,
        outbox_record_ids=pending_ids,
    )
    if publication_wake is not None:
        result["outbox_publication_wake"] = publication_wake
    return result


__all__ = [
    "request_team_outbox_publication",
    "request_team_outbox_publication_for_records",
    "publish_team_wake_events_with_outbox_recovery",
]
