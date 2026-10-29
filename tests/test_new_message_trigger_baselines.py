from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from evander.teams_meeting import TeamsMeetingBinding, TeamsMeetingStore
from evander.triggers.providers._cursor_state import TriggerCursorStore
from evander.triggers.providers._new_message_baselines import seed_new_message_trigger_cursors


def test_new_message_trigger_registration_baselines_each_cursor_kind(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    email_store = TriggerCursorStore(runtime_dir / "trigger-cursors-email.json")
    email_store.put(
        "email-existing",
        {
            "last_received_at": "2026-08-10T07:00:00Z",
            "recent_message_ids": ["already-seen"],
        },
    )
    baseline_at = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)

    seed_new_message_trigger_cursors(
        runtime_dir=runtime_dir,
        previous_config={
            "triggers": [
                {
                    "id": "email-existing",
                    "type": "trigger:email:new-message",
                }
            ]
        },
        next_config={
            "triggers": [
                {
                    "id": "email-existing",
                    "type": "trigger:email:new-message",
                },
                {
                    "id": "communication-new",
                    "type": "trigger:communication:new-message",
                },
                {"id": "email-new", "type": "trigger:email:new-message"},
                {"id": "discord-new", "type": "trigger:discord:new-message"},
                {"id": "teams-new", "type": "trigger:teams:new-message"},
                {"id": "teams-direct-new", "type": "trigger:teams:direct-message"},
            ]
        },
        now=baseline_at,
    )

    assert email_store.get("email-existing") == {
        "last_received_at": "2026-08-10T07:00:00Z",
        "recent_message_ids": ["already-seen"],
    }
    assert TriggerCursorStore(runtime_dir / "trigger-cursors-communication.json").get(
        "communication-new"
    ) == {
        "initialized_at": "2026-08-10T08:00:00Z",
        "last_created_at": "2026-08-10T08:00:00Z",
        "recent_message_ids": [],
    }
    assert email_store.get("email-new") == {
        "initialized_at": "2026-08-10T08:00:00Z",
        "last_received_at": "2026-08-10T08:00:00Z",
        "recent_message_ids": [],
    }
    assert TriggerCursorStore(runtime_dir / "trigger-cursors-discord.json").get("discord-new") == {
        "initialized_at": "2026-08-10T08:00:00Z",
        "last_created_at": "2026-08-10T08:00:00Z",
        "recent_message_ids": [],
    }
    teams_store = TriggerCursorStore(runtime_dir / "trigger-cursors-teams.json")
    expected_teams_state = {
        "initialized_at": "2026-08-10T08:00:00Z",
        "last_created_at": "2026-08-10T08:00:00Z",
        "recent_cursors": [],
    }
    assert teams_store.get("teams-new") == expected_teams_state
    assert teams_store.get("teams-direct-new") == expected_teams_state


def test_new_message_trigger_registration_resets_reused_trigger_id(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = TriggerCursorStore(runtime_dir / "trigger-cursors-email.json")
    store.put(
        "email-reused",
        {
            "last_received_at": "2026-08-10T07:00:00Z",
            "recent_message_ids": ["old-message"],
        },
    )

    seed_new_message_trigger_cursors(
        runtime_dir=runtime_dir,
        previous_config={"triggers": []},
        next_config={
            "triggers": [
                {"id": "email-reused", "type": "trigger:email:new-message"},
            ]
        },
        now=datetime(2026, 8, 10, 8, 0, tzinfo=UTC),
    )

    assert store.get("email-reused") == {
        "initialized_at": "2026-08-10T08:00:00Z",
        "last_received_at": "2026-08-10T08:00:00Z",
        "recent_message_ids": [],
    }


def test_new_transcript_trigger_registration_baselines_existing_meeting_sequences(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    meeting_store = TeamsMeetingStore(runtime_dir / "teams-meetings.json")
    meeting_store.upsert_binding(
        TeamsMeetingBinding(
            meeting_id="meeting-1",
            session_id="session-1",
        )
    )
    meeting_store.append_local_event(
        meeting_id="meeting-1",
        event_type="transcript",
        text="Existing transcript",
    )
    meeting_store.append_local_event(
        meeting_id="meeting-1",
        event_type="speech_status",
        speech_status="completed",
    )

    seed_new_message_trigger_cursors(
        runtime_dir=runtime_dir,
        previous_config={"triggers": []},
        next_config={
            "triggers": [
                {
                    "id": "meeting-new",
                    "type": "trigger:teams-meeting:new-transcript",
                    "session_ids": ["session-1"],
                }
            ]
        },
        now=datetime(2026, 8, 10, 8, 0, tzinfo=UTC),
    )

    assert TriggerCursorStore(runtime_dir / "trigger-cursors-teams-meeting.json").get(
        "meeting-new"
    ) == {
        "initialized_at": "2026-08-10T08:00:00Z",
        "last_sequences_by_meeting": {"meeting-1": 2},
    }
