from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from evander.messaging.wake_dispatch import directory_is_within_scope
from evander.models import utc_now

from ..models import (
    AgentContextClearingPolicy,
    AgentProjectAssignment,
    AgentProjectAssignmentKind,
    AgentRuntime,
    AgentStatus,
    EphemeralAssignmentState,
    PersistentAgent,
    TeamIdempotencyRecord,
    TeamState,
    TeamRole,
    agent_project_ids,
)
from ..repository import TeamStateConflictError, TeamStateRepository
from ..sqlite_store import TeamIdempotencyConflictError
from .domain.agent_access import (
    _agent_team_removal_blockers,
    _project_assignment_id,
    _validate_role_assignment,
)
from .domain.common import (
    _append_unique,
    _generated_id,
    _optional_text,
    _require_present,
    _required_text,
)


class TeamAgentRetirementBlockedError(ValueError):
    def __init__(self, blockers: list[str]) -> None:
        self.blockers = blockers
        super().__init__("agent cannot be retired: " + "; ".join(blockers))


@dataclass(frozen=True)
class TeamWorkflowAgentRegistrationResult:
    state: TeamState
    agent: PersistentAgent
    created: bool


@dataclass(frozen=True)
class TeamWorkflowAgentRetirementResult:
    state: TeamState
    agent: PersistentAgent
    retired: bool
    already_retired: bool


@dataclass(frozen=True)
class TeamWorkflowAgentRestorationResult:
    state: TeamState
    agent: PersistentAgent
    restored: bool
    already_restored: bool
    previous_session_id: str


class TeamWorkflowAgentLifecycleService:
    """Workflow-only lifecycle bridge from core sessions into durable Team agents."""

    def __init__(
        self,
        repository: TeamStateRepository,
        *,
        session_lookup: Any,
        workflow_directory_path: str,
    ) -> None:
        self._repository = repository
        self._session_lookup = session_lookup
        self._workflow_directory_path = _required_text(
            workflow_directory_path,
            "workflow_directory_path",
        )

    def register_agent(
        self,
        *,
        session_id: str,
        team_id: str,
        role_id: str,
        agent_id: str | None = None,
        project_id: str | None = None,
        reports_to_agent_id: str | None = None,
        display_name: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamWorkflowAgentRegistrationResult:
        normalized = _RegistrationRequest(
            session_id=_required_text(session_id, "session_id"),
            team_id=_required_text(team_id, "team_id"),
            role_id=_required_text(role_id, "role_id"),
            agent_id=_optional_text(agent_id),
            project_id=_optional_text(project_id),
            reports_to_agent_id=_optional_text(reports_to_agent_id),
            display_name=_optional_text(display_name),
        )
        self._require_workflow_session(normalized.session_id, require_unexpired=True)
        before = self._repository.read()
        existing = self._exact_registration(before, normalized)
        if existing is not None:
            return TeamWorkflowAgentRegistrationResult(
                state=before,
                agent=existing,
                created=False,
            )
        _require_revision(before, expected_revision)
        self._existing_registration(before, normalized)

        created_agent_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal created_agent_id
            # Revalidate under the state lock before writing any Team record.
            existing = self._existing_registration(state, normalized)
            if existing is not None:
                raise _RegistrationAlreadyExists(existing.id)
            role, effective_agent_id = self._validate_new_registration(state, normalized)
            agent = PersistentAgent(
                id=effective_agent_id,
                team_id=normalized.team_id,
                display_name=normalized.display_name,
                role_id=normalized.role_id,
                session_id=normalized.session_id,
                reports_to_agent_id=normalized.reports_to_agent_id,
                ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
            )
            state.agents[agent.id] = agent
            if normalized.project_id is not None:
                assignment_id = _project_assignment_id(
                    normalized.project_id,
                    agent.id,
                    AgentProjectAssignmentKind.PROJECT_MEMBER,
                    existing_ids=state.project_assignments,
                )
                state.project_assignments[assignment_id] = AgentProjectAssignment(
                    id=assignment_id,
                    team_id=normalized.team_id,
                    project_id=normalized.project_id,
                    agent_id=agent.id,
                    role_id=normalized.role_id,
                    kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
                )
            if normalized.reports_to_agent_id is not None:
                superior = state.agents[normalized.reports_to_agent_id]
                superior.subordinate_agent_ids = _append_unique(
                    superior.subordinate_agent_ids,
                    agent.id,
                )
            created_agent_id = agent.id
            return state

        try:
            state = self._repository.update(_mutate, expected_revision=expected_revision)
        except TeamStateConflictError:
            state = self._repository.read()
            existing = self._exact_registration(state, normalized)
            if existing is None:
                raise
            return TeamWorkflowAgentRegistrationResult(
                state=state,
                agent=existing,
                created=False,
            )
        except _RegistrationAlreadyExists:
            state = self._repository.read()
            existing = self._exact_registration(state, normalized)
            if existing is None:  # pragma: no cover - defensive concurrent replacement guard
                _require_revision(state, expected_revision)
                raise
            return TeamWorkflowAgentRegistrationResult(
                state=state,
                agent=existing,
                created=False,
            )
        if created_agent_id is None:  # pragma: no cover - mutator contract guard
            raise RuntimeError("workflow Team agent registration did not create an agent")
        return TeamWorkflowAgentRegistrationResult(
            state=state,
            agent=state.agents[created_agent_id],
            created=True,
        )

    def retire_agent(
        self,
        *,
        agent_id: str,
        expected_session_id: str,
        expected_team_id: str,
        expected_revision: int | None = None,
    ) -> TeamWorkflowAgentRetirementResult:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_session_id = _required_text(expected_session_id, "expected_session_id")
        normalized_team_id = _required_text(expected_team_id, "expected_team_id")
        before = self._repository.read()
        agent = _require_present(before.agents, normalized_agent_id, "agent")
        self._validate_retirement_identity(
            agent,
            expected_session_id=normalized_session_id,
            expected_team_id=normalized_team_id,
        )
        if _is_fully_retired(agent):
            return TeamWorkflowAgentRetirementResult(
                state=before,
                agent=agent,
                retired=True,
                already_retired=True,
            )
        _require_revision(before, expected_revision)
        self._require_workflow_session(normalized_session_id, require_unexpired=False)
        blockers = _agent_team_removal_blockers(before, agent)
        if blockers:
            raise TeamAgentRetirementBlockedError(blockers)

        def _mutate(state: TeamState) -> TeamState:
            current = _require_present(state.agents, normalized_agent_id, "agent")
            self._validate_retirement_identity(
                current,
                expected_session_id=normalized_session_id,
                expected_team_id=normalized_team_id,
            )
            if _is_fully_retired(current):
                raise _RetirementAlreadyCompleted(current.id)
            current_blockers = _agent_team_removal_blockers(state, current)
            if current_blockers:
                raise TeamAgentRetirementBlockedError(current_blockers)
            parent_id = current.reports_to_agent_id
            if parent_id is not None and parent_id in state.agents:
                parent = state.agents[parent_id]
                parent.subordinate_agent_ids = [
                    subordinate_id
                    for subordinate_id in parent.subordinate_agent_ids
                    if subordinate_id != current.id
                ]
            for assignment_id, assignment in list(state.project_assignments.items()):
                if assignment.agent_id == current.id:
                    del state.project_assignments[assignment_id]
            current.retired_team_id = current.team_id
            current.team_id = None
            current.role_id = None
            current.reports_to_agent_id = None
            current.status = AgentStatus.ARCHIVED
            return state

        try:
            state = self._repository.update(_mutate, expected_revision=expected_revision)
        except TeamStateConflictError:
            state = self._repository.read()
            current = _require_present(state.agents, normalized_agent_id, "agent")
            self._validate_retirement_identity(
                current,
                expected_session_id=normalized_session_id,
                expected_team_id=normalized_team_id,
            )
            if not _is_fully_retired(current):
                raise
            return TeamWorkflowAgentRetirementResult(
                state=state,
                agent=current,
                retired=True,
                already_retired=True,
            )
        except _RetirementAlreadyCompleted:
            state = self._repository.read()
            current = _require_present(state.agents, normalized_agent_id, "agent")
            self._validate_retirement_identity(
                current,
                expected_session_id=normalized_session_id,
                expected_team_id=normalized_team_id,
            )
            if not _is_fully_retired(current):  # pragma: no cover - defensive guard
                _require_revision(state, expected_revision)
                raise
            return TeamWorkflowAgentRetirementResult(
                state=state,
                agent=current,
                retired=True,
                already_retired=True,
            )
        return TeamWorkflowAgentRetirementResult(
            state=state,
            agent=state.agents[normalized_agent_id],
            retired=True,
            already_retired=False,
        )

    def restore_agent(
        self,
        *,
        agent_id: str,
        new_session_id: str,
        expected_old_session_id: str,
        expected_team_id: str,
        role_id: str,
        project_id: str | None = None,
        reports_to_agent_id: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamWorkflowAgentRestorationResult:
        normalized = _RestorationRequest(
            agent_id=_required_text(agent_id, "agent_id"),
            new_session_id=_required_text(new_session_id, "new_session_id"),
            expected_old_session_id=_required_text(
                expected_old_session_id,
                "expected_old_session_id",
            ),
            expected_team_id=_required_text(expected_team_id, "expected_team_id"),
            role_id=_required_text(role_id, "role_id"),
            project_id=_optional_text(project_id),
            reports_to_agent_id=_optional_text(reports_to_agent_id),
        )
        if normalized.new_session_id == normalized.expected_old_session_id:
            raise ValueError("new_session_id must differ from expected_old_session_id")
        self._require_workflow_session(normalized.new_session_id, require_unexpired=True)
        idempotency_payload = _restoration_idempotency_payload(normalized)
        payload_digest = self._repository.normalized_payload_digest(idempotency_payload)
        already_restored = False

        def _mutate(state: TeamState, tx: Any) -> TeamState:
            nonlocal already_restored
            concurrent_replay = tx.get_idempotency_record(
                tool_name="effect:team:restore-agent",
                caller_identity="workflow-agent-lifecycle",
                scope_kind="agent",
                scope_id=normalized.agent_id,
                request_id=normalized.new_session_id,
            )
            if concurrent_replay is not None:
                if concurrent_replay.normalized_payload_digest != payload_digest:
                    raise TeamIdempotencyConflictError(
                        tool_name="effect:team:restore-agent",
                        caller_identity="workflow-agent-lifecycle",
                        scope_kind="agent",
                        scope_id=normalized.agent_id,
                        request_id=normalized.new_session_id,
                        existing_digest=concurrent_replay.normalized_payload_digest,
                        attempted_digest=payload_digest,
                    )
                existing = self._exact_restoration(state, normalized)
                if existing is None:
                    raise ValueError(
                        f"completed restoration for agent {normalized.agent_id} no longer "
                        "matches the current Team binding"
                    )
                already_restored = True
                return state
            _require_revision(state, expected_revision)
            agent = self._validate_restoration(state, normalized)
            # Revalidate the core session while holding the Team state write lock so a
            # concurrent Team mutation cannot make a stale validation authoritative.
            self._require_workflow_session(normalized.new_session_id, require_unexpired=True)
            agent.team_id = normalized.expected_team_id
            agent.role_id = normalized.role_id
            agent.session_id = normalized.new_session_id
            agent.reports_to_agent_id = normalized.reports_to_agent_id
            agent.retired_team_id = None
            # Delegation limits belong to the logical agent, not its backing session.
            # Retirement leaves the override intact, so restoration must preserve it.
            agent.status = AgentStatus.ACTIVE
            if normalized.project_id is not None:
                assignment_id = _project_assignment_id(
                    normalized.project_id,
                    agent.id,
                    AgentProjectAssignmentKind.PROJECT_MEMBER,
                    existing_ids=state.project_assignments,
                )
                state.project_assignments[assignment_id] = AgentProjectAssignment(
                    id=assignment_id,
                    team_id=normalized.expected_team_id,
                    project_id=normalized.project_id,
                    agent_id=agent.id,
                    role_id=normalized.role_id,
                    kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
                )
            if normalized.reports_to_agent_id is not None:
                superior = state.agents[normalized.reports_to_agent_id]
                superior.subordinate_agent_ids = _append_unique(
                    superior.subordinate_agent_ids,
                    agent.id,
                )
            # The execution session is a new generation. Do not carry leases, model
            # thread ids, context-rotation state, or other runtime lineage across it.
            # Context clearing mode and idle grace are operator-owned policy for the
            # logical agent, however, so they survive the backing-session replacement.
            previous_runtime = state.agent_runtimes.get(agent.id)
            state.agent_runtimes[agent.id] = AgentRuntime(
                agent_id=agent.id,
                context_clearing_policy=(
                    previous_runtime.context_clearing_policy
                    if previous_runtime is not None
                    else AgentContextClearingPolicy.OFF
                ),
                context_clearing_idle_grace_seconds=(
                    previous_runtime.context_clearing_idle_grace_seconds
                    if previous_runtime is not None
                    else 0
                ),
            )
            tx.store_idempotency_record(
                TeamIdempotencyRecord(
                    id=f"idempotency-{uuid4().hex}",
                    tool_name="effect:team:restore-agent",
                    caller_identity="workflow-agent-lifecycle",
                    scope_kind="agent",
                    scope_id=normalized.agent_id,
                    request_id=normalized.new_session_id,
                    normalized_payload_digest=payload_digest,
                    response_payload={
                        "agent_id": normalized.agent_id,
                        "previous_session_id": normalized.expected_old_session_id,
                        "session_id": normalized.new_session_id,
                    },
                    created_domain_ids=[normalized.agent_id],
                )
            )
            return state

        state = self._repository.update_with_command_transaction(_mutate)
        return TeamWorkflowAgentRestorationResult(
            state=state,
            agent=state.agents[normalized.agent_id],
            restored=True,
            already_restored=already_restored,
            previous_session_id=normalized.expected_old_session_id,
        )

    def _exact_registration(
        self,
        state: TeamState,
        request: "_RegistrationRequest",
    ) -> PersistentAgent | None:
        session_agent = next(
            (agent for agent in state.agents.values() if agent.session_id == request.session_id),
            None,
        )
        if session_agent is not None and _registration_matches(
            session_agent,
            state=state,
            request=request,
        ):
            return session_agent
        if request.agent_id is not None:
            agent = state.agents.get(request.agent_id)
            if agent is not None and _registration_matches(
                agent,
                state=state,
                request=request,
            ):
                return agent
        return None

    def _existing_registration(
        self,
        state: TeamState,
        request: "_RegistrationRequest",
    ) -> PersistentAgent | None:
        self._validate_registration_scope(state, request)
        session_agent = next(
            (agent for agent in state.agents.values() if agent.session_id == request.session_id),
            None,
        )
        if session_agent is not None:
            if _registration_matches(session_agent, state=state, request=request):
                return session_agent
            raise ValueError(
                f"session {request.session_id} is already bound to Team agent {session_agent.id}"
            )
        if request.agent_id is not None and request.agent_id in state.agents:
            existing = state.agents[request.agent_id]
            if _registration_matches(existing, state=state, request=request):
                return existing
            raise ValueError(f"agent {request.agent_id} already exists with a different binding")
        return None

    def _exact_restoration(
        self,
        state: TeamState,
        request: "_RestorationRequest",
    ) -> PersistentAgent | None:
        agent = state.agents.get(request.agent_id)
        if agent is None:
            return None
        if not _restoration_matches(agent, state=state, request=request):
            return None
        return agent

    def _validate_restoration(
        self,
        state: TeamState,
        request: "_RestorationRequest",
    ) -> PersistentAgent:
        agent = _require_present(state.agents, request.agent_id, "agent")
        if not _is_fully_retired(agent):
            raise ValueError(f"agent {agent.id} is not fully archived")
        if agent.session_id != request.expected_old_session_id:
            raise ValueError(
                f"agent {agent.id} session does not match expected_old_session_id"
            )
        if agent.retired_team_id != request.expected_team_id:
            raise ValueError(f"agent {agent.id} team does not match expected_team_id")
        lingering_assignment = next(
            (
                assignment
                for assignment in state.project_assignments.values()
                if assignment.agent_id == agent.id
            ),
            None,
        )
        if lingering_assignment is not None:
            raise ValueError(
                f"agent {agent.id} still has project assignment {lingering_assignment.id}"
            )
        bound_agent = next(
            (
                candidate
                for candidate in state.agents.values()
                if candidate.session_id == request.new_session_id
            ),
            None,
        )
        if bound_agent is not None:
            raise ValueError(
                f"session {request.new_session_id} is already bound to Team agent "
                f"{bound_agent.id}"
            )
        registration = _RegistrationRequest(
            session_id=request.new_session_id,
            team_id=request.expected_team_id,
            role_id=request.role_id,
            agent_id=request.agent_id,
            project_id=request.project_id,
            reports_to_agent_id=request.reports_to_agent_id,
            display_name=agent.display_name,
        )
        self._validate_registration_scope(state, registration)
        if request.reports_to_agent_id == agent.id:
            raise ValueError("agent cannot report to itself")
        return agent

    def _validate_new_registration(
        self,
        state: TeamState,
        request: "_RegistrationRequest",
    ) -> tuple[TeamRole, str]:
        self._validate_registration_scope(state, request)
        effective_agent_id = request.agent_id or _generated_id(
            prefix="agent",
            labels=[
                request.project_id or request.team_id,
                request.display_name or request.role_id or request.session_id,
            ],
            existing_ids=state.agents,
        )
        if effective_agent_id in state.agents:
            raise ValueError(f"agent {effective_agent_id} already exists")
        if request.reports_to_agent_id == effective_agent_id:
            raise ValueError("agent cannot report to itself")
        role = state.roles[request.role_id]
        return role, effective_agent_id

    def _validate_registration_scope(
        self,
        state: TeamState,
        request: "_RegistrationRequest",
    ) -> None:
        team = _require_present(state.teams, request.team_id, "team")
        if team.archived:
            raise ValueError(f"team {team.id} is archived")
        role = _require_present(state.roles, request.role_id, "role")
        if role.archived:
            raise ValueError(f"role {role.id} is archived")
        conflicting_assignment = next(
            (
                assignment
                for assignment in state.ephemeral_assignments.values()
                if assignment.session_id == request.session_id
                and assignment.state != EphemeralAssignmentState.CLEANED_UP
            ),
            None,
        )
        if conflicting_assignment is not None:
            raise ValueError(
                f"session {request.session_id} is already bound to non-cleaned ephemeral "
                f"assignment {conflicting_assignment.id}"
            )
        if request.project_id is not None:
            project = _require_present(state.projects, request.project_id, "project")
            if project.team_id != request.team_id:
                raise ValueError(
                    f"project {request.project_id} does not belong to team {request.team_id}"
                )
        _validate_role_assignment(
            role,
            team_id=request.team_id,
            project_ids=[request.project_id] if request.project_id is not None else [],
        )
        if request.reports_to_agent_id is not None:
            superior = _require_present(
                state.agents,
                request.reports_to_agent_id,
                "superior agent",
            )
            if superior.team_id != request.team_id or superior.status != AgentStatus.ACTIVE:
                raise ValueError(
                    f"superior agent {request.reports_to_agent_id} is not an active member of "
                    f"team {request.team_id}"
                )

    def _validate_retirement_identity(
        self,
        agent: PersistentAgent,
        *,
        expected_session_id: str,
        expected_team_id: str,
    ) -> None:
        if agent.session_id != expected_session_id:
            raise ValueError(
                f"agent {agent.id} session does not match expected_session_id"
            )
        actual_team_id = agent.team_id or agent.retired_team_id
        if actual_team_id != expected_team_id:
            raise ValueError(f"agent {agent.id} team does not match expected_team_id")

    def _require_workflow_session(self, session_id: str, *, require_unexpired: bool) -> Any:
        session = _lookup_session(self._session_lookup, session_id)
        if session is None:
            raise ValueError(f"core session {session_id} does not exist")
        directory_path = str(_session_field(session, "directory_path", "root") or "root")
        if not directory_is_within_scope(directory_path, self._workflow_directory_path):
            raise ValueError(
                f"core session {session_id} is outside workflow directory scope "
                f"{self._workflow_directory_path}"
            )
        if require_unexpired and _session_is_expired(session):
            raise ValueError(f"core session {session_id} is expired")
        return session


@dataclass(frozen=True)
class _RegistrationRequest:
    session_id: str
    team_id: str
    role_id: str
    agent_id: str | None
    project_id: str | None
    reports_to_agent_id: str | None
    display_name: str | None


@dataclass(frozen=True)
class _RestorationRequest:
    agent_id: str
    new_session_id: str
    expected_old_session_id: str
    expected_team_id: str
    role_id: str
    project_id: str | None
    reports_to_agent_id: str | None


class _RegistrationAlreadyExists(RuntimeError):
    pass


class _RetirementAlreadyCompleted(RuntimeError):
    pass


def _registration_matches(
    agent: PersistentAgent,
    *,
    state: TeamState,
    request: _RegistrationRequest,
) -> bool:
    return (
        (request.agent_id is None or agent.id == request.agent_id)
        and agent.session_id == request.session_id
        and agent.team_id == request.team_id
        and agent.role_id == request.role_id
        and agent.reports_to_agent_id == request.reports_to_agent_id
        and agent.display_name == request.display_name
        and agent.status == AgentStatus.ACTIVE
        and agent.retired_team_id is None
        and set(agent_project_ids(state, agent.id))
        == ({request.project_id} if request.project_id is not None else set())
    )


def _restoration_matches(
    agent: PersistentAgent,
    *,
    state: TeamState,
    request: _RestorationRequest,
) -> bool:
    return (
        agent.id == request.agent_id
        and agent.session_id == request.new_session_id
        and agent.team_id == request.expected_team_id
        and agent.role_id == request.role_id
        and agent.reports_to_agent_id == request.reports_to_agent_id
        and agent.status == AgentStatus.ACTIVE
        and agent.retired_team_id is None
        and set(agent_project_ids(state, agent.id))
        == ({request.project_id} if request.project_id is not None else set())
    )


def _restoration_idempotency_payload(request: _RestorationRequest) -> dict[str, str | None]:
    return {
        "agent_id": request.agent_id,
        "new_session_id": request.new_session_id,
        "expected_old_session_id": request.expected_old_session_id,
        "expected_team_id": request.expected_team_id,
        "role_id": request.role_id,
        "project_id": request.project_id,
        "reports_to_agent_id": request.reports_to_agent_id,
    }


def _is_fully_retired(agent: PersistentAgent) -> bool:
    return (
        agent.status == AgentStatus.ARCHIVED
        and agent.team_id is None
        and agent.role_id is None
        and agent.reports_to_agent_id is None
        and agent.retired_team_id is not None
    )


def _require_revision(state: TeamState, expected_revision: int | None) -> None:
    if expected_revision is not None and state.revision != expected_revision:
        raise TeamStateConflictError(
            expected_revision=expected_revision,
            current_revision=state.revision,
        )


def _session_is_expired(session: Any) -> bool:
    expires_at = _session_field(session, "expires_at")
    if not isinstance(expires_at, datetime):
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= utc_now()


def _lookup_session(session_lookup: Any, session_id: str) -> Any | None:
    reader = getattr(session_lookup, "read_all_sessions", None)
    if callable(reader):
        sessions = reader()
        if not isinstance(sessions, Mapping):
            raise RuntimeError("core session lookup returned a non-mapping session collection")
        return sessions.get(session_id)
    for method_name in ("get_session", "get"):
        getter = getattr(session_lookup, method_name, None)
        if callable(getter):
            return getter(session_id)
    raise RuntimeError("Team workflow lifecycle requires a core session lookup")


def _session_field(session: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(session, Mapping):
        return session.get(field_name, default)
    return getattr(session, field_name, default)


__all__ = [
    "TeamAgentRetirementBlockedError",
    "TeamWorkflowAgentLifecycleService",
    "TeamWorkflowAgentRegistrationResult",
    "TeamWorkflowAgentRestorationResult",
    "TeamWorkflowAgentRetirementResult",
]
