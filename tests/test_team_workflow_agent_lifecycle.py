from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from config_helpers import canonical_worker_config
from evander.config import GatewayConfig
from evander.daemons.trigger_orchestrator import TriggerOrchestratorWorker
from evander.effects.providers.agent_destroy import AgentDestroyEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.schemas import AgentDestroyEffectDefinition
from evander.models import utc_now
from evander_team_addon.models import AgentStatus, TeamCaseStatus
from evander_team_addon.provider_schemas import (
    TeamPublishOutboxEventsEffectDefinition,
    TeamRegisterAgentEffectDefinition,
    TeamRetireAgentEffectDefinition,
)
from evander_team_addon.providers import (
    TeamPublishOutboxEventsEffectProvider,
    TeamRegisterAgentEffectProvider,
    TeamRetireAgentEffectProvider,
    register_providers,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService


@dataclass
class _Session:
    session_id: str
    directory_path: str = "root"
    expires_at: object = None


class _Sessions:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = {session.session_id: session for session in sessions}

    def get_session(self, session_id: str) -> _Session | None:
        return self.sessions.get(session_id)


class _DestroyingAdminService:
    def __init__(self, sessions: _Sessions) -> None:
        self._sessions = sessions
        self.destroyed_session_ids: list[str] = []

    def delete_admin_agent(self, target: str, *, backend: str) -> dict[str, object]:
        self.destroyed_session_ids.append(target)
        self._sessions.sessions.pop(target, None)
        return {
            "success": True,
            "target_name": target,
            "session_id": target,
            "status": "destroyed",
            "backend": backend,
        }


def _seed_team(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    admin = TeamAdminService(repo)
    admin.create_team(
        team_id="team",
        name="Lifecycle Team",
        manager_agent_id="manager",
        manager_session_id="manager-session",
        expected_revision=0,
    )
    admin.create_role(
        role_id="support",
        team_id="team",
        name="Support",
        expected_revision=repo.read().revision,
    )
    return repo


def _register(
    runtime_dir: Path,
    sessions: _Sessions,
    *,
    session_id: str,
    agent_id: str | None = None,
    reports_to_agent_id: str | None = "manager",
    directory_path: str = "root",
    expected_revision: int | None = None,
) -> dict[str, object]:
    provider = TeamRegisterAgentEffectProvider()
    provider.initialize(
        TeamRegisterAgentEffectDefinition(
            id=f"register-{session_id}",
            display_name="Register Team agent",
            type="effect:team:register-agent",
            session_id=session_id,
            team_id="team",
            role_id="support",
            agent_id=agent_id,
            reports_to_agent_id=reports_to_agent_id,
            expected_revision=expected_revision,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(runtime_dir)},
            session_lookup=sessions,
            metadata={"directory_path": directory_path},
        ),
    )
    return provider.execute()


def _retire(
    runtime_dir: Path,
    sessions: _Sessions,
    *,
    agent_id: str,
    session_id: str,
    expected_team_id: str = "team",
) -> dict[str, object]:
    provider = TeamRetireAgentEffectProvider()
    provider.initialize(
        TeamRetireAgentEffectDefinition(
            id=f"retire-{agent_id}",
            display_name="Retire Team agent",
            type="effect:team:retire-agent",
            agent_id=agent_id,
            expected_session_id=session_id,
            expected_team_id=expected_team_id,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(runtime_dir)},
            session_lookup=sessions,
            metadata={"directory_path": "root"},
        ),
    )
    return provider.execute()


def test_register_agent_effect_is_idempotent_and_enforces_session_scope(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(
        _Session("support-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("generated-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("expired-session", expires_at=utc_now() - timedelta(seconds=1)),
        _Session(
            "outside-session",
            directory_path="other",
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )

    created = _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )
    assert created["success"] is True
    assert created["created"] is True
    assert created["agent_id"] == "support-agent"
    state = repo.read()
    assert state.agents["support-agent"].session_id == "support-session"
    assert state.agents["support-agent"].reports_to_agent_id == "manager"
    revision_after_create = state.revision

    retry = _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )
    assert retry["success"] is True
    assert retry["created"] is False
    assert retry["revision"] == revision_after_create

    generated = _register(tmp_path, sessions, session_id="generated-session")
    assert generated["success"] is True
    assert generated["created"] is True
    assert str(generated["agent_id"]).startswith("agent-team-")

    duplicate = _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="different-agent",
    )
    assert duplicate["success"] is False
    assert "already bound" in str(duplicate["error"])

    expired = _register(tmp_path, sessions, session_id="expired-session")
    outside = _register(
        tmp_path,
        sessions,
        session_id="outside-session",
        directory_path="root/workflow",
    )
    missing = _register(tmp_path, sessions, session_id="missing-session")
    assert expired["success"] is False and "expired" in str(expired["error"])
    assert outside["success"] is False and "outside workflow directory" in str(outside["error"])
    assert missing["success"] is False and "does not exist" in str(missing["error"])


def test_retire_agent_effect_requires_child_first_cleanup_and_preserves_retry_history(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(
        _Session("support-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("discovery-session", expires_at=utc_now() + timedelta(hours=1)),
    )
    assert _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )["success"]
    assert _register(
        tmp_path,
        sessions,
        session_id="discovery-session",
        agent_id="discovery-agent",
        reports_to_agent_id="support-agent",
    )["success"]

    blocked = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )
    assert blocked["success"] is False
    assert any("subordinate" in blocker for blocker in blocked["blockers"])

    retired_child = _retire(
        tmp_path,
        sessions,
        agent_id="discovery-agent",
        session_id="discovery-session",
    )
    assert retired_child["success"] is True
    assert retired_child["retired"] is True
    assert retired_child["already_retired"] is False
    state = repo.read()
    child = state.agents["discovery-agent"]
    assert child.status == AgentStatus.ARCHIVED
    assert child.team_id is None
    assert child.role_id is None
    assert child.reports_to_agent_id is None
    assert child.retired_team_id == "team"
    assert "discovery-agent" not in state.agents["support-agent"].subordinate_agent_ids

    sessions.sessions.pop("discovery-session")
    retry = _retire(
        tmp_path,
        sessions,
        agent_id="discovery-agent",
        session_id="discovery-session",
    )
    assert retry["success"] is True
    assert retry["already_retired"] is True

    wrong_identity = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
        expected_team_id="other-team",
    )
    assert wrong_identity["success"] is False
    assert "does not match expected_team_id" in str(wrong_identity["error"])

    retired_parent = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )
    assert retired_parent["success"] is True


def test_case_close_event_is_outboxed_once_and_not_for_initial_or_resolved_cases(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    admin = TeamAdminService(repo)
    admin.create_case(
        case_id="open-case",
        team_id="team",
        title="Close through workflow",
        expected_revision=repo.read().revision,
    )
    admin.update_case(
        case_id="open-case",
        status=TeamCaseStatus.CLOSED,
        expected_revision=repo.read().revision,
    )

    records = repo.list_outbox_records(intent_type="team.case.closed")
    assert len(records) == 1
    event = records[0].payload["payload"]
    assert event["case_id"] == "open-case"
    assert event["team_id"] == "team"
    assert event["previous_status"] == "open"
    assert records[0].idempotency_key

    admin.update_case(
        case_id="open-case",
        status=TeamCaseStatus.CLOSED,
        expected_revision=repo.read().revision,
    )
    admin.create_case(
        case_id="initially-closed",
        team_id="team",
        title="No event on initial close",
        status=TeamCaseStatus.CLOSED,
        expected_revision=repo.read().revision,
    )
    admin.create_case(
        case_id="resolved-case",
        team_id="team",
        title="No event on resolve",
        expected_revision=repo.read().revision,
    )
    admin.update_case(
        case_id="resolved-case",
        status=TeamCaseStatus.RESOLVED,
        expected_revision=repo.read().revision,
    )
    assert len(repo.list_outbox_records(intent_type="team.case.closed")) == 1


def test_agent_case_update_uses_the_same_transactional_close_event(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    admin = TeamAdminService(repo)
    admin.create_case(
        case_id="agent-case",
        team_id="team",
        title="Agent closure",
        owner_agent_id="manager",
        expected_revision=repo.read().revision,
    )

    state, result = TeamAgentActionService(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "case_updates": [{"case_id": "agent-case", "status": "closed"}],
        },
        expected_revision=repo.read().revision,
    )

    assert result["success"] is True
    assert state.cases["agent-case"].status == TeamCaseStatus.CLOSED
    records = repo.list_outbox_records(intent_type="team.case.closed")
    assert len(records) == 1
    assert records[0].payload["payload"]["case_id"] == "agent-case"


def test_case_closed_trigger_supports_child_first_retirement_then_session_destroy(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(
        _Session("support-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("discovery-session", expires_at=utc_now() + timedelta(hours=1)),
    )
    assert _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )["success"]
    assert _register(
        tmp_path,
        sessions,
        session_id="discovery-session",
        agent_id="discovery-agent",
        reports_to_agent_id="support-agent",
    )["success"]

    admin = TeamAdminService(repo)
    admin.create_case(
        case_id="cleanup-case",
        team_id="team",
        title="Workflow cleanup",
        expected_revision=repo.read().revision,
    )
    admin.update_case(
        case_id="cleanup-case",
        status=TeamCaseStatus.CLOSED,
        expected_revision=repo.read().revision,
    )

    publisher = TeamPublishOutboxEventsEffectProvider()
    publisher.initialize(
        TeamPublishOutboxEventsEffectDefinition(
            id="publish-case-closure",
            display_name="Publish case closure",
            type="effect:team:publish-outbox-events",
            intent_types=["team.case.closed"],
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    published = publisher.execute()
    assert published["success"] is True
    assert published["published_count"] == 1

    worker_config = canonical_worker_config(
        {
            "runtime_dir": tmp_path,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "case-closed-cleanup",
                    "display_name": "Case closed cleanup",
                    "type": "trigger:event",
                    "event_type": "team.case.closed",
                }
            ],
        }
    )
    trigger_worker = TriggerOrchestratorWorker(
        config=GatewayConfig(runtime_dir=tmp_path),
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    assert trigger_worker.run_once().events_published == 1

    destroying_admin = _DestroyingAdminService(sessions)
    for agent_id, session_id in (
        ("discovery-agent", "discovery-session"),
        ("support-agent", "support-session"),
    ):
        retired = _retire(
            tmp_path,
            sessions,
            agent_id=agent_id,
            session_id=session_id,
        )
        assert retired["success"] is True
        assert sessions.get_session(session_id) is not None

        destroy = AgentDestroyEffectProvider()
        destroy.initialize(
            AgentDestroyEffectDefinition(
                id=f"destroy-{agent_id}",
                display_name="Destroy retired core session",
                type="effect:agent:destroy",
                session_id=session_id,
                execution_backend="container_worker_vm",
            ),
            EffectRuntimeContext(
                resolved_config={"runtime_dir": str(tmp_path)},
                admin_service=destroying_admin,
            ),
        )
        assert destroy.execute()["success"] is True

    assert destroying_admin.destroyed_session_ids == [
        "discovery-session",
        "support-session",
    ]
    state = repo.read()
    assert state.agents["discovery-agent"].status == AgentStatus.ARCHIVED
    assert state.agents["support-agent"].status == AgentStatus.ARCHIVED


def test_lifecycle_effects_are_registered_with_output_contracts() -> None:
    registration = register_providers()

    assert registration.effect_providers["effect:team:register-agent"] is TeamRegisterAgentEffectProvider
    assert registration.effect_providers["effect:team:retire-agent"] is TeamRetireAgentEffectProvider
