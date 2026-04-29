from __future__ import annotations

from datetime import UTC
from pathlib import Path

from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import TeamsMeetingNewTranscriptTriggerDefinition
from agent_sandbox.teams_meeting import TeamsMeetingEvent, TeamsMeetingStore
from agent_sandbox.triggers.providers._cursor_state import TriggerCursorStore
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class TeamsMeetingNewTranscriptTriggerProvider(TriggerProviderBase):
    _CURSOR_FILENAME = "trigger-cursors-teams-meeting.json"

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        last_sequences = _last_sequences_by_meeting(state)
        session_filter = {value.strip() for value in trigger.session_ids if value.strip()}

        transcript_events, next_last_sequences = self._list_transcript_events(
            last_sequences=last_sequences,
            session_filter=session_filter,
        )
        transcript_events.sort(key=lambda item: (item.occurred_at, item.meeting_id, item.sequence))

        events: list[TriggerEvent] = []
        for transcript in transcript_events:
            event_key = f"{transcript.meeting_id}:{transcript.sequence}"
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{event_key}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=transcript.occurred_at.astimezone(UTC),
                    payload={
                        "session_id": transcript.session_id,
                        "meeting_id": transcript.meeting_id,
                        "transcript": {
                            "sequence": transcript.sequence,
                            "text": transcript.text or "",
                            "occurred_at": transcript.occurred_at.astimezone(UTC)
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "received_at": transcript.received_at.astimezone(UTC)
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "metadata": transcript.metadata,
                        },
                    },
                )
            )

        cursor_store.put(
            trigger.id,
            {"last_sequences_by_meeting": next_last_sequences},
        )
        return events

    def _list_transcript_events(
        self,
        *,
        last_sequences: dict[str, int],
        session_filter: set[str],
    ) -> tuple[list[TeamsMeetingEvent], dict[str, int]]:
        store = TeamsMeetingStore(Path(self.runtime_context.resolved_config.teams_meeting_store_path))
        transcript_events: list[TeamsMeetingEvent] = []
        next_last_sequences = dict(last_sequences)
        for binding in store.list_bindings():
            if session_filter and binding.session_id not in session_filter:
                continue
            since_sequence = last_sequences.get(binding.meeting_id, 0)
            events = store.list_events_for_session(
                binding.session_id,
                since_sequence=since_sequence,
                limit=10_000,
            )
            if events:
                next_last_sequences[binding.meeting_id] = max(
                    since_sequence,
                    *(event.sequence for event in events),
                )
            transcript_events.extend(event for event in events if event.event_type == "transcript")
        return transcript_events, next_last_sequences

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(
            Path(self.runtime_context.resolved_config.runtime_dir) / self._CURSOR_FILENAME
        )

    def _trigger(self) -> TeamsMeetingNewTranscriptTriggerDefinition:
        if not isinstance(self.trigger_definition, TeamsMeetingNewTranscriptTriggerDefinition):
            raise RuntimeError(
                "TeamsMeetingNewTranscriptTriggerProvider initialized with wrong definition"
            )
        return self.trigger_definition


def _last_sequences_by_meeting(state: dict[str, object]) -> dict[str, int]:
    raw_sequences = state.get("last_sequences_by_meeting")
    if isinstance(raw_sequences, dict):
        return {
            str(meeting_id): int(sequence)
            for meeting_id, sequence in raw_sequences.items()
            if str(meeting_id).strip() and isinstance(sequence, int) and sequence >= 0
        }

    sequences: dict[str, int] = {}
    raw_emitted_ids = state.get("emitted_event_ids")
    if isinstance(raw_emitted_ids, list):
        for value in raw_emitted_ids:
            if not isinstance(value, str) or ":" not in value:
                continue
            meeting_id, sequence_text = value.rsplit(":", 1)
            if not meeting_id.strip():
                continue
            try:
                sequence = int(sequence_text)
            except ValueError:
                continue
            if sequence >= 0:
                sequences[meeting_id] = max(sequences.get(meeting_id, 0), sequence)
    return sequences
