from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from evander.model_usage.models import (
    ModelInferenceUsageEvent,
    ModelUsageCorrelationContext,
)
from evander.model_usage.pricing import load_model_pricing_catalog_payload
from evander.model_usage.store import ModelUsageStore
from evander.models import SessionManifest
from evander.repositories.session_manifests import JsonSessionManifestRepository
import evander_team_addon.costs as costs_module
from evander_team_addon.costs import TeamCostService
from evander_team_addon.models import EphemeralAssignment
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


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
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).create_role(
        role_id="project__reviewer",
        team_id="team",
        project_id="project",
        name="Reviewer",
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    _team_admin_service(repo).register_agent(
        agent_id="reviewer",
        team_id="team",
        project_id="project",
        role_id="project__reviewer",
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
    request_id: str | None = None,
    dispatch_id: str | None = None,
    raw_usage_metadata: dict | None = None,
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
        raw_usage_metadata=raw_usage_metadata
        or {
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }
        },
        session_id=session_id,
        dispatch_id=dispatch_id,
        agent_harness="codex",
        request_id=request_id,
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


def test_team_costs_use_persisted_usage_attribution_after_agent_detached(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    store.upsert_event(
        _usage_event(
            "backend-before-detach",
            session_id="session-backend",
            raw_usage_metadata={
                "usage": {
                    "input_tokens": 1_000_000,
                    "output_tokens": 0,
                    "total_tokens": 1_000_000,
                },
                "request_metadata": {
                    "team_id": "team",
                    "project_id": "project",
                    "agent_id": "backend",
                },
            },
        )
    )
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        team_id=None,
        set_team_id=True,
        expected_revision=repo.read().revision,
    )

    payload = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=False)

    assert payload["team_costs"]["team"]["estimated_cost"] == 1.0
    assert payload["project_costs"]["project"]["estimated_cost"] == 1.0
    assert payload["unattributed_costs"]["estimated_cost"] == 0.0


def test_team_costs_preserve_dispatch_attribution_for_superseded_transcripts(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    now = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
    store.upsert_correlation_context(
        ModelUsageCorrelationContext(
            session_id="session-backend",
            dispatch_id="dispatch-backend",
            agent_harness="claude",
            team_id="team",
            project_id="project",
            agent_id="backend",
            started_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    store.upsert_event(
        ModelInferenceUsageEvent(
            event_id="claude-transcript:req-shared",
            occurred_at=now,
            provider="anthropic",
            provider_endpoint_identifier="messages",
            model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=100,
            total_tokens=1100,
            request_status="success",
            normalized_usage_status="available",
            raw_usage_metadata={
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "total_tokens": 1100,
                },
                "request_metadata": {
                    "source": costs_module.TRANSCRIPT_SOURCE_NAME,
                    "team_id": "new-team",
                    "project_id": "new-project",
                    "agent_id": "backend",
                },
                "response_metadata": {
                    "source": costs_module.TRANSCRIPT_SOURCE_NAME,
                    "provider_request_id": "req-shared",
                },
            },
            session_id="session-backend",
            agent_harness="claude",
            request_id="req-shared",
        )
    )
    store.upsert_event(
        _usage_event(
            "relay-anthropic",
            session_id="session-backend",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,
            occurred_at=now + timedelta(seconds=1),
            request_id="req-shared",
            dispatch_id="dispatch-backend",
        )
    )
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        team_id=None,
        set_team_id=True,
        expected_revision=repo.read().revision,
    )

    payload = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=False)

    assert payload["team_costs"]["team"]["source_counts"] == {"transcript": 1}
    assert payload["team_costs"]["team"]["estimated_cost"] < 0.01
    assert payload["project_costs"]["project"]["source_counts"] == {"transcript": 1}
    assert "new-team" not in payload["team_costs"]
    assert "new-project" not in payload["project_costs"]


def test_team_costs_include_lifetime_cost_guard_metadata(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    _team_admin_service(repo).update_team(
        team_id="team",
        max_cost_usd="2.50",
        set_max_cost_usd=True,
    )
    window_start = repo.read().teams["team"].cost_budget_windows[-1].started_at
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    store.upsert_event(
        _usage_event(
            "manager",
            session_id="session-manager",
            occurred_at=window_start,
        )
    )

    payload = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=False)

    team_cost = payload["team_costs"]["team"]
    assert team_cost["estimated_cost"] == 1.0
    assert team_cost["max_cost_usd"] == 2.5
    assert team_cost["remaining_cost_usd"] == 1.5
    assert team_cost["cost_limit_reached"] is False
    assert team_cost["cost_limit_basis"] == "budget_window"
    assert team_cost["lifetime_estimated_cost"] == 1.0
    assert team_cost["budget_window_estimated_cost"] == 1.0

    _team_admin_service(repo).update_team(
        team_id="team",
        max_cost_usd="1.00",
        set_max_cost_usd=True,
    )
    payload = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=False)

    team_cost = payload["team_costs"]["team"]
    assert team_cost["remaining_cost_usd"] == 0.0
    assert team_cost["cost_limit_reached"] is True


def test_team_cost_budget_reset_preserves_lifetime_cost_but_reopens_the_cap(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    admin = _team_admin_service(repo)
    admin.update_team(team_id="team", max_cost_usd="1.00", set_max_cost_usd=True)
    first_window_start = repo.read().teams["team"].cost_budget_windows[-1].started_at
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    store.upsert_event(
        _usage_event("first", session_id="session-manager", occurred_at=first_window_start)
    )

    before_reset = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=False)
    assert before_reset["team_costs"]["team"]["cost_limit_reached"] is True

    admin.reset_team_cost_budget(
        team_id="team",
        reason="Approved the next unit of work.",
        operator_actor_id="operator:jonathan",
        expected_revision=repo.read().revision,
    )
    team = repo.read().teams["team"]
    assert len(team.cost_budget_windows) == 2
    assert team.cost_budget_windows[0].ended_at is not None
    assert team.cost_budget_windows[1].reason == "Approved the next unit of work."

    after_reset = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=False)
    cost = after_reset["team_costs"]["team"]
    assert cost["estimated_cost"] == 1.0
    assert cost["budget_window_estimated_cost"] == 0.0
    assert cost["remaining_cost_usd"] == 1.0
    assert cost["cost_limit_reached"] is False


def test_legacy_team_cap_stays_lifetime_based_until_its_first_reset(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)

    def _configure_legacy_cap(state):
        state.teams["team"].max_cost_usd = Decimal("1.00")
        return state

    repo.update(_configure_legacy_cap)
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    store.upsert_event(_usage_event("legacy", session_id="session-manager"))

    cost = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=False)["team_costs"]["team"]

    assert cost["cost_limit_basis"] == "lifetime"
    assert cost["budget_window_started_at"] is None
    assert cost["budget_window_estimated_cost"] == 1.0
    assert cost["cost_limit_reached"] is True


def test_governance_group_costs_roll_up_member_team_costs(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    _team_admin_service(repo).update_team(
        team_id="team",
        max_cost_usd="2.00",
        set_max_cost_usd=True,
    )
    window_start = repo.read().teams["team"].cost_budget_windows[-1].started_at
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        max_cost_usd="5.00",
        expected_revision=repo.read().revision,
    )
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    store.upsert_event(
        _usage_event(
            "manager",
            session_id="session-manager",
            occurred_at=window_start,
        )
    )
    store.upsert_event(
        _usage_event(
            "backend",
            session_id="session-backend",
            occurred_at=window_start,
        )
    )

    payload = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=False)

    group_cost = payload["governance_group_costs"]["portfolio"]
    assert group_cost["estimated_cost"] == 2.0
    assert group_cost["max_cost_usd"] == 5.0
    assert group_cost["remaining_cost_usd"] == 3.0
    assert group_cost["hard_enforced"] is False
    assert group_cost["member_team_ids"] == ["team"]
    assert group_cost["member_team_costs"]["team"]["estimated_cost"] == 2.0
    assert group_cost["member_team_cap_reached_ids"] == ["team"]


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


def test_claude_transcript_backfill_reads_libvirt_vm_sources_and_supersedes_relay_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    sessions = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    sessions.upsert_session(
        SessionManifest(
            session_id="session-backend",
            directory_path="root/team",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            model="claude-sonnet-4-6",
            vm_provider="libvirt",
            vm_id="backend-vm",
            metadata={
                "agent_provider": "claude",
                "last_known_vm_ip": "192.168.250.10",
            },
        )
    )
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    store.upsert_event(
        _usage_event(
            "relay-anthropic",
            session_id="session-backend",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,
            request_id="req-remote-1",
        )
    )

    def fake_scan(source):
        assert source.remote_host == "192.168.250.10"
        assert source.remote_host_alias == "backend-vm"
        return [
            costs_module._RemoteClaudeTranscriptEntry(
                kind="file",
                path="/home/agent/.claude/projects/-home-agent/thread.jsonl",
            ),
            costs_module._RemoteClaudeTranscriptEntry(
                kind="record",
                path="/home/agent/.claude/projects/-home-agent/thread.jsonl",
                line_number=3,
                payload=json.dumps(
                    {
                        "sessionId": "claude-thread",
                        "requestId": "req-remote-1",
                        "timestamp": datetime(2026, 6, 16, 12, 0, tzinfo=UTC).isoformat(),
                        "message": {
                            "id": "msg-remote-1",
                            "model": "claude-sonnet-4-6",
                            "usage": {
                                "input_tokens": 1000,
                                "cache_read_input_tokens": 200,
                                "output_tokens": 100,
                            },
                        },
                    }
                ),
            ),
        ]

    monkeypatch.setattr(costs_module, "_scan_remote_claude_transcripts", fake_scan)

    payload = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        session_repository=sessions,
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=True)

    event = store.get_event("claude-transcript:req-remote-1")
    assert event is not None
    assert event.session_id == "session-backend"
    assert event.raw_usage_metadata["request_metadata"]["source"] == "claude_transcript_backfill"
    assert payload["backfill"]["sources_checked"] == 1
    assert payload["backfill"]["sources_missing"] == 0
    assert payload["backfill"]["files_scanned"] == 1
    assert payload["backfill"]["records_imported"] == 1
    assert payload["team_costs"]["team"]["source_counts"] == {"transcript": 1}
    assert payload["project_costs"]["project"]["source_counts"] == {"transcript": 1}
    assert payload["team_costs"]["team"]["estimated_cost"] < 0.01


def test_claude_transcript_backfill_keeps_relay_rows_for_unbackfilled_requests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    sessions = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    sessions.upsert_session(
        SessionManifest(
            session_id="session-backend",
            directory_path="root/team",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            model="claude-sonnet-4-6",
            vm_provider="libvirt",
            vm_id="backend-vm",
            metadata={
                "agent_provider": "claude",
                "last_known_vm_ip": "192.168.250.10",
            },
        )
    )
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    store.upsert_event(
        _usage_event(
            "relay-anthropic-matched",
            session_id="session-backend",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,
            request_id="req-remote-1",
        )
    )
    store.upsert_event(
        _usage_event(
            "relay-anthropic-unbackfilled",
            session_id="session-backend",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,
            request_id="req-remote-2",
        )
    )

    def fake_scan(source):
        assert source.remote_host == "192.168.250.10"
        assert source.remote_host_alias == "backend-vm"
        return [
            costs_module._RemoteClaudeTranscriptEntry(
                kind="file",
                path="/home/agent/.claude/projects/-home-agent/thread.jsonl",
            ),
            costs_module._RemoteClaudeTranscriptEntry(
                kind="record",
                path="/home/agent/.claude/projects/-home-agent/thread.jsonl",
                line_number=3,
                payload=json.dumps(
                    {
                        "sessionId": "claude-thread",
                        "requestId": "req-remote-1",
                        "timestamp": datetime(2026, 6, 16, 12, 0, tzinfo=UTC).isoformat(),
                        "message": {
                            "id": "msg-remote-1",
                            "model": "claude-sonnet-4-6",
                            "usage": {
                                "input_tokens": 1000,
                                "output_tokens": 100,
                            },
                        },
                    }
                ),
            ),
        ]

    monkeypatch.setattr(costs_module, "_scan_remote_claude_transcripts", fake_scan)

    payload = TeamCostService(
        runtime_dir=runtime_dir,
        state=repo.read(),
        session_repository=sessions,
        usage_store=store,
        pricing_catalog=_pricing_catalog(),
    ).costs_payload(backfill_transcripts=True)

    team_cost = payload["team_costs"]["team"]
    project_cost = payload["project_costs"]["project"]
    assert team_cost["source_counts"] == {"relay": 1, "transcript": 1}
    assert project_cost["source_counts"] == {"relay": 1, "transcript": 1}
    assert team_cost["events_count"] == 2
    assert team_cost["input_tokens"] == 1_001_000


def test_remote_claude_transcript_scan_uses_configured_vm_ssh_options(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("private key", encoding="utf-8")
    known_hosts = tmp_path / "evander-known_hosts"
    monkeypatch.setenv("SSH_PRIVATE_KEY_PATH", str(private_key))
    monkeypatch.setenv("EVANDER_GUEST_KNOWN_HOSTS_FILE", str(known_hosts))
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(costs_module.subprocess, "run", fake_run)

    entries = costs_module._scan_remote_claude_transcripts(
        costs_module.ClaudeTranscriptSource(
            session_id="session-backend",
            agent_id="backend",
            team_id="team",
            project_id="project",
            session_manifest=None,
            remote_host="192.168.250.10",
            remote_host_alias="backend-vm",
        )
    )

    assert entries == []
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "ssh"
    assert "-i" in cmd
    assert cmd[cmd.index("-i") + 1] == str(private_key)
    assert "BatchMode=yes" in cmd
    assert "IdentitiesOnly=yes" in cmd
    assert f"UserKnownHostsFile={known_hosts}" in cmd
    assert "HostKeyAlias=backend-vm" in cmd
    assert "CheckHostIP=no" in cmd
    assert "agent@192.168.250.10" in cmd
    assert known_hosts.exists()


def test_remote_claude_transcript_backfill_circuit_breaks_stale_hosts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    costs_module._reset_remote_transcript_scan_failures_for_tests()
    now = [1000.0]
    monkeypatch.setattr(costs_module.time, "monotonic", lambda: now[0])
    store = ModelUsageStore(tmp_path / "model-usage.db")
    source = costs_module.ClaudeTranscriptSource(
        session_id="session-backend",
        agent_id="backend",
        team_id="team",
        project_id="project",
        session_manifest=None,
        remote_host="192.168.250.10",
        remote_host_alias="backend-vm",
    )
    scan_calls = [0]

    def failing_scan(_source):
        scan_calls[0] += 1
        raise RuntimeError("stale host")

    monkeypatch.setattr(costs_module, "_scan_remote_claude_transcripts", failing_scan)
    first = costs_module.ClaudeTranscriptBackfillResult()
    costs_module._backfill_remote_claude_transcripts(store, source, first)
    second = costs_module.ClaudeTranscriptBackfillResult()
    costs_module._backfill_remote_claude_transcripts(store, source, second)

    assert scan_calls[0] == 1
    assert first.errors[0]["error"] == "RuntimeError: stale host"
    assert "skipped after recent failure" in second.errors[0]["error"]

    def successful_scan(_source):
        scan_calls[0] += 1
        return []

    now[0] += costs_module.REMOTE_TRANSCRIPT_STALE_HOST_TTL_SECONDS + 1
    monkeypatch.setattr(costs_module, "_scan_remote_claude_transcripts", successful_scan)
    third = costs_module.ClaudeTranscriptBackfillResult()
    costs_module._backfill_remote_claude_transcripts(store, source, third)

    assert scan_calls[0] == 2
    assert third.errors == []
    assert third.sources_missing == 1
    assert costs_module._remote_transcript_circuit_retry_after_seconds(source) is None
    costs_module._reset_remote_transcript_scan_failures_for_tests()


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
