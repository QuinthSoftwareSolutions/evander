from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evander.teams_meeting import TeamsMeetingStore
from evander.triggers.providers._cursor_state import TriggerCursorStore


@dataclass(frozen=True)
class _CursorSpec:
    filename: str
    timestamp_field: str
    recent_ids_field: str


_CURSOR_SPECS: dict[str, _CursorSpec] = {
    "trigger:communication:new-message": _CursorSpec(
        filename="trigger-cursors-communication.json",
        timestamp_field="last_created_at",
        recent_ids_field="recent_message_ids",
    ),
    "trigger:email:new-message": _CursorSpec(
        filename="trigger-cursors-email.json",
        timestamp_field="last_received_at",
        recent_ids_field="recent_message_ids",
    ),
    "trigger:discord:new-message": _CursorSpec(
        filename="trigger-cursors-discord.json",
        timestamp_field="last_created_at",
        recent_ids_field="recent_message_ids",
    ),
    "trigger:teams:new-message": _CursorSpec(
        filename="trigger-cursors-teams.json",
        timestamp_field="last_created_at",
        recent_ids_field="recent_cursors",
    ),
    "trigger:teams:direct-message": _CursorSpec(
        filename="trigger-cursors-teams.json",
        timestamp_field="last_created_at",
        recent_ids_field="recent_cursors",
    ),
}
_TEAMS_MEETING_TRANSCRIPT_TRIGGER_TYPE = "trigger:teams-meeting:new-transcript"


@dataclass(frozen=True)
class _InboundTrigger:
    id: str
    type: str
    session_ids: tuple[str, ...] = ()


def seed_new_message_trigger_cursors(
    *,
    runtime_dir: Path,
    previous_config: dict[str, object],
    next_config: dict[str, object],
    teams_meeting_store_path: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Baseline newly registered inbound-message triggers at their save time.

    A new subscription observes messages after it was registered. Reusing an ID after
    removal, or changing its trigger type, is also a new subscription and resets its
    cursor at that boundary.
    """

    previous_triggers = _inbound_triggers(previous_config)
    new_triggers = [
        trigger
        for trigger in _inbound_triggers(next_config).values()
        if previous_triggers.get(trigger.id) is None
        or previous_triggers[trigger.id].type != trigger.type
    ]
    if not new_triggers:
        return

    baseline_at = (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    for trigger in new_triggers:
        if trigger.type == _TEAMS_MEETING_TRANSCRIPT_TRIGGER_TYPE:
            _baseline_teams_meeting_transcript_trigger(
                runtime_dir=runtime_dir,
                trigger=trigger,
                baseline_at=baseline_at,
                teams_meeting_store_path=teams_meeting_store_path,
            )
            continue
        spec = _CURSOR_SPECS[trigger.type]
        TriggerCursorStore(runtime_dir / spec.filename).update(
            trigger.id,
            lambda state, spec=spec: _baseline_cursor_state(
                state,
                spec=spec,
                baseline_at=baseline_at,
            ),
        )


def _inbound_triggers(config: dict[str, object]) -> dict[str, _InboundTrigger]:
    triggers = config.get("triggers")
    if not isinstance(triggers, list):
        return {}

    trigger_definitions: dict[str, _InboundTrigger] = {}
    for raw_trigger in triggers:
        if not isinstance(raw_trigger, dict):
            continue
        trigger_id = str(raw_trigger.get("id") or "").strip()
        trigger_type = str(raw_trigger.get("type") or "").strip()
        if not trigger_id or (
            trigger_type not in _CURSOR_SPECS
            and trigger_type != _TEAMS_MEETING_TRANSCRIPT_TRIGGER_TYPE
        ):
            continue
        session_ids = raw_trigger.get("session_ids")
        normalized_session_ids = (
            tuple(str(value).strip() for value in session_ids if str(value).strip())
            if isinstance(session_ids, list)
            else ()
        )
        trigger_definitions[trigger_id] = _InboundTrigger(
            id=trigger_id,
            type=trigger_type,
            session_ids=normalized_session_ids,
        )
    return trigger_definitions


def _baseline_cursor_state(
    state: dict[str, object],
    *,
    spec: _CursorSpec,
    baseline_at: str,
) -> dict[str, object]:
    state["initialized_at"] = baseline_at
    state[spec.timestamp_field] = baseline_at
    state[spec.recent_ids_field] = []
    return state


def _baseline_teams_meeting_transcript_trigger(
    *,
    runtime_dir: Path,
    trigger: _InboundTrigger,
    baseline_at: str,
    teams_meeting_store_path: Path | None,
) -> None:
    meeting_store = TeamsMeetingStore(
        teams_meeting_store_path or runtime_dir / "teams-meetings.json"
    )
    session_filter = set(trigger.session_ids)
    last_sequences_by_meeting = {
        binding.meeting_id: meeting_store.max_event_sequence(binding.meeting_id)
        for binding in meeting_store.list_bindings()
        if not session_filter or binding.session_id in session_filter
    }
    TriggerCursorStore(runtime_dir / "trigger-cursors-teams-meeting.json").update(
        trigger.id,
        lambda state: _baseline_teams_meeting_cursor_state(
            state,
            baseline_at=baseline_at,
            last_sequences_by_meeting=last_sequences_by_meeting,
        ),
    )


def _baseline_teams_meeting_cursor_state(
    state: dict[str, object],
    *,
    baseline_at: str,
    last_sequences_by_meeting: dict[str, int],
) -> dict[str, object]:
    state["initialized_at"] = baseline_at
    state["last_sequences_by_meeting"] = last_sequences_by_meeting
    return state
