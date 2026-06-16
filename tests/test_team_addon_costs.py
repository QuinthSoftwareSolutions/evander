from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_sandbox.model_usage.models import ModelInferenceUsageEvent
from agent_sandbox.model_usage.pricing import load_model_pricing_catalog_payload
from agent_sandbox.model_usage.store import ModelUsageStore
from agent_sandbox.models import SessionManifest
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository
from truman_team_addon.costs import TeamCostService
from truman_team_addon.models import EphemeralAssignment
from truman_team_addon.repository import TeamStateRepository


def _pricing_catalog():
    return load_model_pricing_catalog_payload(
        {
            "version": "team-cost-test",
            "currency": "usd",
            "rules": [
                {
                    "id": "openai:test-model",
                    "provider": "openai",
                    "models": ["test-model"],
                    "rates_per_1m_tokens": {
                        "input": "1.00",
                        "output": "2.00",
                    },
                },
                {
                    "id": "anthropic:test-claude",
                    "provider": "anthropic",
                    "models": ["claude-sonnet-4-6"],
                    "rates_per_1m_tokens": {
                        "input": "3.00",
                        "cached_input": "0.30",
                        "cache_write_5m": "3.75",
                        "cache_write_1h": "6.00",
                        "output": "15.00",
                    },
                },
            ],
        }
    )


def _seed_team(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role="backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    repo.register_agent(
        agent_id="reviewer",
        team_id="team",
        project_id="project",
        role="reviewer",
        session_id="session-reviewer",
        reports_to_agent_id="lead",
    )
    return repo


def _usage_event(
    event_id: str,
    *,
    session_id: str,
    model: str = "test-model",
    provider: str = "openai",
    input_tokens: int = 1_000_000,
    output_tokens: int = 0,
    occurred_at: datetime | None = None,
) -> ModelInferenceUsageEvent:
    total_tokens = input_tokens + output_tokens
    return ModelInferenceUsageEvent(
        event_id=event_id,
        occurred_at=occurred_at or datetime(2026, 6, 16, 12, 0, tzinfo=UTC),
        provider=provider,
        provider_endpoint_identifier="responses",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        request_status="success",
        normalized_usage_status="available",
        raw_usage_metadata={
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }
        },
        session_id=session_id,
        agent_harness="codex",
    )


def test_team_costs_count_team_roles_and_project_confident_usage(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    for event_id, session_id in (
        ("manager", "session-manager"),
        ("lead", "session-lead"),
        ("reviewer", "session-reviewer"),
        ("backend", "session-backend"),
        ("global", "outside-team"),
    ):
        store.upsert_event(_usage_event(event_id, session_id=session_id))
    store.upsert_event(
        _usage_event("unpriced", session_id="session-manager", model="unpriced-model")
    )

    payload = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=False)

    team_cost = payload["team_costs"]["team"]
    project_cost = payload["project_costs"]["project"]
    assert team_cost["estimated_cost"] == 4.0
    assert team_cost["events_count"] == 5
    assert team_cost["unpriced_events_count"] == 1
    assert team_cost["coverage_status"] == "partial"
    assert project_cost["estimated_cost"] == 3.0
    assert project_cost["events_count"] == 3
    assert team_cost["unattributed_costs"]["estimated_cost"] == 1.0
    assert team_cost["unattributed_costs"]["events_count"] == 2
    assert payload["unattributed_costs"]["estimated_cost"] == 2.0
    assert payload["unattributed_costs"]["events_count"] == 3


def test_claude_transcript_backfill_imports_usage_without_content_and_is_idempotent(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    agent_home = tmp_path / "worker" / "sessions" / "session-backend" / "agent_home"
    transcript_dir = agent_home / ".claude" / "projects" / "-home-agent"
    transcript_dir.mkdir(parents=True)
    transcript_path = transcript_dir / "claude-thread.jsonl"
    occurred_at = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
    transcript_path.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "type": "user",
                    "sessionId": "claude-thread",
                    "timestamp": occurred_at.isoformat(),
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "secret prompt text"}],
                    },
                },
                {
                    "type": "assistant",
                    "sessionId": "claude-thread",
                    "requestId": "req-claude-1",
                    "timestamp": (occurred_at + timedelta(seconds=1)).isoformat(),
                    "message": {
                        "id": "msg-claude-1",
                        "role": "assistant",
                        "model": "claude-sonnet-4-6",
                        "content": [{"type": "text", "text": "secret answer text"}],
                        "usage": {
                            "input_tokens": 1000,
                            "cache_read_input_tokens": 200,
                            "cache_creation_input_tokens": 7,
                            "cache_creation": {
                                "ephemeral_5m_input_tokens": 3,
                                "ephemeral_1h_input_tokens": 4,
                            },
                            "output_tokens": 100,
                        },
                    },
                },
            ]
        ),
        encoding="utf-8",
    )
    sessions = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    sessions.upsert_session(
        SessionManifest(
            session_id="session-backend",
            directory_path="root/team",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "agent_provider": "claude",
                "execution_backend": "container_worker_vm",
                "agent_home_path": str(agent_home),
            },
        )
    )
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    service = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        session_repository=sessions,
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    )

    first_payload = service.costs_payload(backfill_transcripts=True)
    second_payload = service.costs_payload(backfill_transcripts=True)

    event = store.get_event("claude-transcript:req-claude-1")
    assert event is not None
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    assert "secret prompt text" not in event_json
    assert "secret answer text" not in event_json
    assert event.provider == "anthropic"
    assert event.agent_harness == "claude"
    assert event.execution_backend == "container_worker_vm"
    assert event.input_tokens == 1000
    assert event.output_tokens == 100
    assert event.raw_usage_metadata["usage"]["cache_read_input_tokens"] == 200
    assert first_payload["backfill"]["records_imported"] == 1
    assert second_payload["backfill"]["records_imported"] == 0
    assert second_payload["backfill"]["duplicate_records"] == 1
    assert second_payload["team_costs"]["team"]["source_counts"] == {"transcript": 1}
    assert second_payload["project_costs"]["project"]["source_counts"] == {"transcript": 1}


def test_claude_transcript_backfill_includes_ephemeral_assignment_sources(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    state = repo.read()
    agent_home = tmp_path / "worker" / "sessions" / "session-ephemeral" / "agent_home"
    transcript_dir = agent_home / ".claude" / "projects" / "-home-agent"
    transcript_dir.mkdir(parents=True)
    transcript_path = transcript_dir / "claude-thread.jsonl"
    transcript_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "claude-ephemeral-thread",
                "requestId": "req-claude-ephemeral",
                "timestamp": datetime(2026, 6, 16, 12, 0, tzinfo=UTC).isoformat(),
                "message": {
                    "id": "msg-claude-ephemeral",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 100,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    state.ephemeral_assignments["ephemeral-1"] = EphemeralAssignment(
        id="ephemeral-1",
        parent_agent_id="backend",
        project_id="project",
        task_id="task-1",
        session_id="session-ephemeral",
        title="Scoped fix",
        prompt="Do the scoped fix.",
        agent_provider="claude",
        execution_backend="container_worker_vm",
        directory_path="root/project",
        metadata={"agent_home_path": str(agent_home)},
    )
    store = ModelUsageStore(runtime_dir / "model-usage.db")

    payload = TeamCostService(
        runtime_dir=runtime_dir,
        state=state,
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=True)

    event = store.get_event("claude-transcript:req-claude-ephemeral")
    assert event is not None
    assert event.session_id == "session-ephemeral"
    assert event.execution_backend == "container_worker_vm"
    assert event.directory_path == "root/project"
    assert payload["backfill"]["records_imported"] == 1
    assert payload["team_costs"]["team"]["source_counts"] == {"transcript": 1}
    assert payload["project_costs"]["project"]["source_counts"] == {"transcript": 1}
