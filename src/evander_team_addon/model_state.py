"""Team aggregate and cross-collection invariants."""

from __future__ import annotations

from collections.abc import Sequence

from datetime import datetime

from pydantic import Field, PrivateAttr, model_validator

from evander.models import utc_now

from .case_context_models import CaseContextSnapshot, CaseContextView, TeamNote
from .model_chat import (
    ExternalChatDeliveryRecord,
    ExternalChatIngressRecord,
    TeamChatAttachment,
    TeamChatAttachmentPolicy,
    TeamConversation,
    TeamConversationMessage,
    TeamWorkLineage,
)
from .model_common import (
    SCHEMA_VERSION,
    AgentProjectAssignmentKind,
    GovernanceAgendaItemState,
    InitialReleaseDispositionState,
    RolePermission,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamCaseStatus,
    TeamGitPreparationAttemptState,
    TeamModel,
    TeamRecordState,
    TeamTaskState,
    _optional_text,
)
from .model_deliverables_git import (
    Deliverable,
    DependencyCompositionRecovery,
    GitWorkContext,
    TeamGitPreparationAttempt,
)
from .model_dispatch import (
    EphemeralAssignment,
    GitPromotionRecord,
    TeamDispatch,
    TeamDispatchStatus,
    WorktreeCleanupCandidate,
)
from .model_inbox_memory import (
    AgentActionRecord,
    AgentInboxItem,
    TeamBlocker,
    TeamBlockerEvent,
    TeamMemoryRecord,
)
from .model_organization import (
    AgentProjectAssignment,
    AgentRuntime,
    AgentStandDown,
    GovernanceAgendaItem,
    GovernanceGroup,
    GovernanceRuntime,
    PersistentAgent,
    Project,
    Team,
    TeamRole,
)
from .model_release_validation import validate_release_packages
from .model_releases import (
    HumanReviewDecision,
    HumanReviewFinding,
    HumanReviewRequest,
    ReleasePackage,
    ReleaseVerificationRun,
)
from .model_reviews import (
    ReviewProfile,
    ReviewRequest,
)
from .model_tasks import (
    AgentTask,
    ExternalSourceEvidenceSnapshot,
    OutcomeKindDefinition,
    TeamCase,
)


from .model_git_recovery import GitRecoveryIncident


class TeamState(TeamModel):
    schema_version: int = SCHEMA_VERSION
    revision: int = Field(default=0, ge=0)
    teams: dict[str, Team] = Field(default_factory=dict)
    governance_groups: dict[str, GovernanceGroup] = Field(default_factory=dict)
    governance_agenda: dict[str, GovernanceAgendaItem] = Field(default_factory=dict)
    governance_runtimes: dict[str, GovernanceRuntime] = Field(default_factory=dict)
    projects: dict[str, Project] = Field(default_factory=dict)
    roles: dict[str, TeamRole] = Field(default_factory=dict)
    agents: dict[str, PersistentAgent] = Field(default_factory=dict)
    project_assignments: dict[str, AgentProjectAssignment] = Field(default_factory=dict)
    agent_runtimes: dict[str, AgentRuntime] = Field(default_factory=dict)
    work_lineages: dict[str, "TeamWorkLineage"] = Field(default_factory=dict)
    cases: dict[str, TeamCase] = Field(default_factory=dict)
    tasks: dict[str, AgentTask] = Field(default_factory=dict)
    external_source_evidence_snapshots: dict[str, ExternalSourceEvidenceSnapshot] = Field(
        default_factory=dict
    )
    deliverables: dict[str, Deliverable] = Field(default_factory=dict)
    review_requests: dict[str, ReviewRequest] = Field(default_factory=dict)
    outcome_kind_definitions: dict[str, OutcomeKindDefinition] = Field(default_factory=dict)
    review_profiles: dict[str, ReviewProfile] = Field(default_factory=dict)
    inbox: dict[str, AgentInboxItem] = Field(default_factory=dict)
    blockers: dict[str, TeamBlocker] = Field(default_factory=dict)
    dependency_composition_recoveries: dict[str, DependencyCompositionRecovery] = Field(
        default_factory=dict
    )
    blocker_events: dict[str, TeamBlockerEvent] = Field(default_factory=dict)
    action_records: dict[str, AgentActionRecord] = Field(default_factory=dict)
    notes: dict[str, TeamNote] = Field(default_factory=dict)
    case_context_views: dict[str, CaseContextView] = Field(default_factory=dict)
    # Transaction-local additions only; persisted history is read through the repository.
    _case_context_snapshot_writes: dict[str, CaseContextSnapshot] = PrivateAttr(
        default_factory=dict
    )
    memories: dict[str, TeamMemoryRecord] = Field(default_factory=dict)
    conversations: dict[str, TeamConversation] = Field(default_factory=dict)
    conversation_messages: dict[str, TeamConversationMessage] = Field(default_factory=dict)
    external_chat_ingress: dict[str, ExternalChatIngressRecord] = Field(default_factory=dict)
    external_chat_deliveries: dict[str, ExternalChatDeliveryRecord] = Field(default_factory=dict)
    chat_attachments: dict[str, TeamChatAttachment] = Field(default_factory=dict)
    chat_attachment_policies: dict[str, TeamChatAttachmentPolicy] = Field(default_factory=dict)
    release_packages: dict[str, ReleasePackage] = Field(default_factory=dict)
    release_verification_runs: dict[str, ReleaseVerificationRun] = Field(default_factory=dict)
    human_review_requests: dict[str, HumanReviewRequest] = Field(default_factory=dict)
    human_review_decisions: dict[str, HumanReviewDecision] = Field(default_factory=dict)
    human_review_findings: dict[str, HumanReviewFinding] = Field(default_factory=dict)
    dispatches: dict[str, TeamDispatch] = Field(default_factory=dict)
    git_recovery_incidents: dict[str, GitRecoveryIncident] = Field(default_factory=dict)
    git_preparation_attempts: dict[str, TeamGitPreparationAttempt] = Field(default_factory=dict)
    worktree_cleanup_candidates: dict[str, WorktreeCleanupCandidate] = Field(default_factory=dict)
    ephemeral_assignments: dict[str, EphemeralAssignment] = Field(default_factory=dict)
    git_promotions: dict[str, GitPromotionRecord] = Field(default_factory=dict)
    git_work_contexts: dict[str, GitWorkContext] = Field(default_factory=dict)
    stand_downs: dict[str, AgentStandDown] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def empty(cls) -> "TeamState":
        return cls()

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_project_assignments(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        projects = dict(migrated.get("projects") or {})
        agents = dict(migrated.get("agents") or {})
        roles = dict(migrated.get("roles") or {})
        assignments = dict(migrated.get("project_assignments") or {})

        def role_project_id(role_id: object) -> str | None:
            if role_id is None:
                return None
            role = roles.get(str(role_id))
            if isinstance(role, TeamRole):
                return role.project_id
            if isinstance(role, dict):
                return _optional_text(role.get("project_id"))
            return None

        def assignment_id(project_id: str, agent_id: str, kind: str) -> str:
            return f"{project_id}__{agent_id}__{kind}"

        def assignment_kind_value(value: object) -> str | None:
            if isinstance(value, AgentProjectAssignmentKind):
                return value.value
            text = _optional_text(value)
            if text is None:
                return None
            if text.startswith("AgentProjectAssignmentKind."):
                key = text.rsplit(".", 1)[-1]
                try:
                    return AgentProjectAssignmentKind[key].value
                except KeyError:
                    return text
            return text

        def has_assignment(project_id: str, agent_id: str, kind: str | None = None) -> bool:
            for assignment in assignments.values():
                if isinstance(assignment, AgentProjectAssignment):
                    current_project_id = assignment.project_id
                    current_agent_id = assignment.agent_id
                    current_kind = assignment.kind.value
                elif isinstance(assignment, dict):
                    current_project_id = _optional_text(assignment.get("project_id"))
                    current_agent_id = _optional_text(assignment.get("agent_id"))
                    current_kind = assignment_kind_value(assignment.get("kind"))
                else:
                    continue
                if (
                    current_project_id == project_id
                    and current_agent_id == agent_id
                    and (kind is None or current_kind == kind)
                ):
                    return True
            return False

        def put_assignment(
            *, project_id: str, agent_id: str, team_id: str, role_id: str | None, kind: str
        ) -> None:
            if has_assignment(project_id, agent_id, kind):
                return
            key = assignment_id(project_id, agent_id, kind)
            suffix = 2
            while key in assignments:
                key = f"{assignment_id(project_id, agent_id, kind)}-{suffix}"
                suffix += 1
            assignments[key] = {
                "id": key,
                "team_id": team_id,
                "project_id": project_id,
                "agent_id": agent_id,
                "role_id": role_id,
                "kind": kind,
            }

        for project_id, raw_project in projects.items():
            project = (
                raw_project.model_dump(mode="python")
                if isinstance(raw_project, TeamModel)
                else raw_project
            )
            if not isinstance(project, dict):
                continue
            lead_agent_id = _optional_text(project.get("lead_agent_id"))
            team_id = _optional_text(project.get("team_id"))
            if lead_agent_id is None or team_id is None:
                continue
            raw_agent = agents.get(lead_agent_id)
            agent = (
                raw_agent.model_dump(mode="python")
                if isinstance(raw_agent, TeamModel)
                else raw_agent
            )
            role_id = _optional_text(agent.get("role_id")) if isinstance(agent, dict) else None
            if role_project_id(role_id) != str(project_id):
                role_id = next(
                    (
                        role_key
                        for role_key, raw_role in roles.items()
                        if (
                            raw_role.project_id
                            if isinstance(raw_role, TeamRole)
                            else _optional_text(raw_role.get("project_id"))
                            if isinstance(raw_role, dict)
                            else None
                        )
                        == str(project_id)
                    ),
                    role_id,
                )
            put_assignment(
                project_id=str(project_id),
                agent_id=lead_agent_id,
                team_id=team_id,
                role_id=role_id,
                kind=AgentProjectAssignmentKind.PROJECT_LEAD.value,
            )

        for agent_id, raw_agent in list(agents.items()):
            agent = (
                raw_agent.model_dump(mode="python")
                if isinstance(raw_agent, TeamModel)
                else dict(raw_agent)
                if isinstance(raw_agent, dict)
                else raw_agent
            )
            if not isinstance(agent, dict):
                continue
            team_id = _optional_text(agent.get("team_id"))
            legacy_project_id = _optional_text(agent.pop("project_id", None))
            role_id = _optional_text(agent.get("role_id"))
            role_project = role_project_id(role_id)
            project_id = legacy_project_id or role_project
            if (
                team_id is not None
                and project_id is not None
                and not has_assignment(project_id, str(agent_id))
            ):
                put_assignment(
                    project_id=project_id,
                    agent_id=str(agent_id),
                    team_id=team_id,
                    role_id=role_id if role_project == project_id else None,
                    kind=AgentProjectAssignmentKind.PROJECT_MEMBER.value,
                )
            agents[agent_id] = agent

        migrated["agents"] = agents
        migrated["project_assignments"] = assignments
        return migrated

    @model_validator(mode="after")
    def _validate_state(self) -> "TeamState":
        errors: list[str] = []
        self._ensure_project_assignment_defaults()
        self._ensure_core_role_permissions()
        self._validate_key_matches(errors)
        self._validate_teams(errors)
        self._validate_governance_groups(errors)
        self._validate_governance_agenda(errors)
        self._validate_governance_runtimes(errors)
        self._validate_projects(errors)
        self._validate_roles(errors)
        self._validate_agents(errors)
        self._validate_project_assignments(errors)
        self._validate_agent_runtimes(errors)
        self._validate_work_lineages(errors)
        self._validate_external_source_evidence_snapshots(errors)
        self._validate_cases(errors)
        self._validate_tasks(errors)
        self._validate_task_graph(errors)
        self._validate_deliverables(errors)
        self._validate_outcome_kind_definitions(errors)
        self._validate_review_profiles(errors)
        self._validate_reviews(errors)
        self._validate_inbox(errors)
        self._validate_blockers(errors)
        self._validate_action_records(errors)
        self._validate_memories(errors)
        self._validate_conversations(errors)
        self._validate_external_chat_bridge(errors)
        self._validate_releases(errors)
        self._validate_human_reviews(errors)
        self._validate_dispatches(errors)
        self._validate_git_preparation_attempts(errors)
        self._validate_worktree_cleanup_candidates(errors)
        self._validate_ephemeral_assignments(errors)
        self._validate_git_promotions(errors)
        self._validate_stand_downs(errors)
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def ordered_tasks_for_agent(self, agent_id: str) -> list[AgentTask]:
        return sorted(
            [task for task in self.tasks.values() if task.owner_agent_id == agent_id],
            key=lambda task: (task.priority, task.created_at, task.id),
        )

    def _validate_key_matches(self, errors: list[str]) -> None:
        for collection_name in (
            "teams",
            "governance_groups",
            "governance_agenda",
            "governance_runtimes",
            "projects",
            "roles",
            "agents",
            "project_assignments",
            "agent_runtimes",
            "work_lineages",
            "cases",
            "tasks",
            "external_source_evidence_snapshots",
            "deliverables",
            "review_requests",
            "outcome_kind_definitions",
            "review_profiles",
            "inbox",
            "blockers",
            "dependency_composition_recoveries",
            "git_recovery_incidents",
            "blocker_events",
            "action_records",
            "notes",
            "case_context_views",
            "memories",
            "conversations",
            "conversation_messages",
            "external_chat_ingress",
            "external_chat_deliveries",
            "chat_attachments",
            "chat_attachment_policies",
            "release_packages",
            "release_verification_runs",
            "human_review_requests",
            "human_review_decisions",
            "human_review_findings",
            "dispatches",
            "git_preparation_attempts",
            "worktree_cleanup_candidates",
            "ephemeral_assignments",
            "git_promotions",
            "git_work_contexts",
            "stand_downs",
        ):
            collection = getattr(self, collection_name)
            for key, record in collection.items():
                record_id = getattr(
                    record,
                    "id",
                    getattr(record, "agent_id", getattr(record, "governance_group_id", None)),
                )
                if key != record_id:
                    errors.append(
                        f"{collection_name}.{key} key does not match record id {record_id}"
                    )

    def _validate_work_lineages(self, errors: list[str]) -> None:
        for lineage in self.work_lineages.values():
            for ref in lineage.chat_source_refs:
                conversation = self.conversations.get(ref.conversation_id)
                if conversation is None:
                    errors.append(
                        f"work lineage {lineage.id} references missing conversation "
                        f"{ref.conversation_id}"
                    )
                for message_id in ref.message_ids:
                    message = self.conversation_messages.get(message_id)
                    if message is None:
                        errors.append(
                            f"work lineage {lineage.id} references missing Chat message "
                            f"{message_id}"
                        )
                    elif message.conversation_id != ref.conversation_id:
                        errors.append(
                            f"work lineage {lineage.id} Chat message {message_id} belongs to "
                            f"conversation {message.conversation_id}, not {ref.conversation_id}"
                        )
        for runtime in self.agent_runtimes.values():
            for lineage_id in (
                runtime.work_lineage_thread_ids.keys()
                | runtime.work_lineage_thread_dispatch_ids.keys()
            ):
                if lineage_id not in self.work_lineages:
                    errors.append(
                        f"agent runtime {runtime.agent_id} references missing work lineage "
                        f"{lineage_id}"
                    )
        for record_kind, records in (
            ("task", self.tasks.values()),
            ("release package", self.release_packages.values()),
            ("dispatch", self.dispatches.values()),
        ):
            for record in records:
                lineage_id = record.work_lineage_id
                if lineage_id is not None and lineage_id not in self.work_lineages:
                    errors.append(
                        f"{record_kind} {record.id} references missing work lineage {lineage_id}"
                    )

    def _ensure_project_assignment_defaults(self) -> None:
        from .model_queries import _agent_has_project_assignment, _project_assignment_id

        now = utc_now()
        for project in self.projects.values():
            if project.lead_agent_id in self.agents and not _agent_has_project_assignment(
                self,
                project.lead_agent_id,
                project.id,
                kind=AgentProjectAssignmentKind.PROJECT_LEAD,
            ):
                lead = self.agents[project.lead_agent_id]
                role = self.roles.get(lead.role_id) if lead.role_id is not None else None
                role_id = (
                    lead.role_id if role is not None and role.project_id == project.id else None
                )
                assignment_id = _project_assignment_id(
                    project.id, lead.id, AgentProjectAssignmentKind.PROJECT_LEAD
                )
                self.project_assignments[assignment_id] = AgentProjectAssignment(
                    id=assignment_id,
                    team_id=project.team_id,
                    project_id=project.id,
                    agent_id=lead.id,
                    role_id=role_id,
                    kind=AgentProjectAssignmentKind.PROJECT_LEAD,
                    created_at=now,
                    updated_at=now,
                )
        for agent in self.agents.values():
            role = self.roles.get(agent.role_id) if agent.role_id is not None else None
            if role is None or role.project_id is None or agent.team_id is None:
                continue
            if _agent_has_project_assignment(self, agent.id, role.project_id):
                continue
            assignment_id = _project_assignment_id(
                role.project_id, agent.id, AgentProjectAssignmentKind.PROJECT_MEMBER
            )
            self.project_assignments[assignment_id] = AgentProjectAssignment(
                id=assignment_id,
                team_id=agent.team_id,
                project_id=role.project_id,
                agent_id=agent.id,
                role_id=role.id,
                kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
                created_at=now,
                updated_at=now,
            )

    def _ensure_core_role_permissions(self) -> None:
        for team in self.teams.values():
            manager = self.agents.get(team.manager_agent_id)
            role = self.roles.get(manager.role_id) if manager is not None else None
            if role is not None and RolePermission.CREATE_ROOT_TASK not in role.permissions:
                role.permissions.append(RolePermission.CREATE_ROOT_TASK)
        for assignment in self.project_assignments.values():
            if (
                assignment.kind != AgentProjectAssignmentKind.PROJECT_LEAD
                or assignment.role_id is None
            ):
                continue
            role = self.roles.get(assignment.role_id)
            if role is not None and RolePermission.CREATE_ROOT_TASK not in role.permissions:
                role.permissions.append(RolePermission.CREATE_ROOT_TASK)

    def _validate_teams(self, errors: list[str]) -> None:
        for team in self.teams.values():
            manager = self.agents.get(team.manager_agent_id)
            if manager is None:
                errors.append(f"team {team.id} references missing manager {team.manager_agent_id}")
            elif manager.team_id != team.id:
                errors.append(
                    f"team {team.id} manager {manager.id} belongs to team {manager.team_id}"
                )
            for project_id in team.project_ids:
                project = self.projects.get(project_id)
                if project is None:
                    errors.append(f"team {team.id} references missing project {project_id}")
                elif project.team_id != team.id:
                    errors.append(
                        f"team {team.id} project {project_id} belongs to team {project.team_id}"
                    )

    def _validate_governance_groups(self, errors: list[str]) -> None:
        for group in self.governance_groups.values():
            if group.manager_agent_id not in self.agents:
                errors.append(
                    f"governance group {group.id} references missing manager "
                    f"{group.manager_agent_id}"
                )
            for team_id in group.member_team_ids:
                if team_id not in self.teams:
                    errors.append(
                        f"governance group {group.id} references missing member team {team_id}"
                    )

    def _validate_governance_agenda(self, errors: list[str]) -> None:
        for item in self.governance_agenda.values():
            group = self.governance_groups.get(item.governance_group_id)
            if group is None:
                errors.append(
                    f"governance agenda item {item.id} references missing group "
                    f"{item.governance_group_id}"
                )
            if item.team_id is not None:
                team = self.teams.get(item.team_id)
                if team is None:
                    errors.append(
                        f"governance agenda item {item.id} references missing team {item.team_id}"
                    )
                elif (
                    group is not None
                    and item.state != GovernanceAgendaItemState.HANDLED
                    and item.team_id not in group.member_team_ids
                ):
                    errors.append(
                        f"governance agenda item {item.id} team {item.team_id} is not in "
                        f"group {group.id}"
                    )
            if item.project_id is not None:
                project = self.projects.get(item.project_id)
                if project is None:
                    errors.append(
                        f"governance agenda item {item.id} references missing project "
                        f"{item.project_id}"
                    )
                elif item.team_id is not None and project.team_id != item.team_id:
                    errors.append(
                        f"governance agenda item {item.id} project {project.id} belongs "
                        f"to team {project.team_id}"
                    )
                elif (
                    group is not None
                    and item.state != GovernanceAgendaItemState.HANDLED
                    and project.team_id not in group.member_team_ids
                ):
                    errors.append(
                        f"governance agenda item {item.id} project {project.id} team "
                        f"{project.team_id} is not in group {group.id}"
                    )
            if item.agent_id is not None:
                agent = self.agents.get(item.agent_id)
                if agent is None:
                    errors.append(
                        f"governance agenda item {item.id} references missing agent {item.agent_id}"
                    )
                elif item.team_id is not None and agent.team_id != item.team_id:
                    errors.append(
                        f"governance agenda item {item.id} agent {agent.id} belongs "
                        f"to team {agent.team_id}"
                    )
                elif (
                    group is not None
                    and item.state != GovernanceAgendaItemState.HANDLED
                    and agent.team_id not in group.member_team_ids
                ):
                    errors.append(
                        f"governance agenda item {item.id} agent {agent.id} team "
                        f"{agent.team_id} is not in group {group.id}"
                    )

    def _validate_governance_runtimes(self, errors: list[str]) -> None:
        for group_id, runtime in self.governance_runtimes.items():
            if runtime.governance_group_id != group_id:
                errors.append(f"governance runtime {group_id} does not point back")
            if group_id not in self.governance_groups:
                errors.append(f"governance runtime {group_id} references missing group")

    def _validate_projects(self, errors: list[str]) -> None:
        from .model_queries import project_lead_agent_id

        for project in self.projects.values():
            if project.team_id not in self.teams:
                errors.append(f"project {project.id} references missing team {project.team_id}")
            lead_id = project_lead_agent_id(self, project.id) or project.lead_agent_id
            lead = self.agents.get(lead_id) if lead_id is not None else None
            if lead is None:
                errors.append(
                    f"project {project.id} references missing lead {lead_id or project.lead_agent_id}"
                )
            elif lead.team_id != project.team_id:
                errors.append(f"project {project.id} lead {lead.id} belongs to team {lead.team_id}")

    def _validate_roles(self, errors: list[str]) -> None:
        for role in self.roles.values():
            if role.team_id not in self.teams:
                errors.append(f"role {role.id} references missing team {role.team_id}")
            if role.project_id is not None:
                project = self.projects.get(role.project_id)
                if project is None:
                    errors.append(f"role {role.id} references missing project {role.project_id}")
                elif project.team_id != role.team_id:
                    errors.append(
                        f"role {role.id} project {project.id} belongs to team {project.team_id}"
                    )

    def _validate_agents(self, errors: list[str]) -> None:
        from .model_queries import _agent_has_project_assignment

        for agent in self.agents.values():
            if agent.team_id is not None and agent.team_id not in self.teams:
                errors.append(f"agent {agent.id} references missing team {agent.team_id}")
            role = self.roles.get(agent.role_id) if agent.role_id is not None else None
            if agent.role_id is not None and role is None:
                errors.append(f"agent {agent.id} references missing role {agent.role_id}")
            elif role is not None and role.team_id != agent.team_id:
                errors.append(f"agent {agent.id} role {role.id} belongs to team {role.team_id}")
            elif (
                role is not None
                and role.project_id is not None
                and not _agent_has_project_assignment(self, agent.id, role.project_id)
            ):
                errors.append(
                    f"agent {agent.id} role {role.id} belongs to project {role.project_id}"
                )
            if agent.reports_to_agent_id is not None:
                superior = self.agents.get(agent.reports_to_agent_id)
                if superior is None:
                    errors.append(
                        f"agent {agent.id} references missing superior {agent.reports_to_agent_id}"
                    )
                elif superior.team_id != agent.team_id:
                    errors.append(f"agent {agent.id} superior {superior.id} is in another team")

    def _validate_project_assignments(self, errors: list[str]) -> None:
        lead_assignments_by_project: dict[str, list[str]] = {}
        seen_agent_project_kind: set[tuple[str, str, AgentProjectAssignmentKind]] = set()
        for assignment in self.project_assignments.values():
            project = self.projects.get(assignment.project_id)
            if project is None:
                errors.append(
                    f"project assignment {assignment.id} references missing project {assignment.project_id}"
                )
            elif project.team_id != assignment.team_id:
                errors.append(
                    f"project assignment {assignment.id} project {project.id} belongs to team {project.team_id}"
                )
            agent = self.agents.get(assignment.agent_id)
            if agent is None:
                errors.append(
                    f"project assignment {assignment.id} references missing agent {assignment.agent_id}"
                )
            elif agent.team_id != assignment.team_id:
                errors.append(
                    f"project assignment {assignment.id} agent {agent.id} belongs to team {agent.team_id}"
                )
            role = self.roles.get(assignment.role_id) if assignment.role_id is not None else None
            if assignment.role_id is not None and role is None:
                errors.append(
                    f"project assignment {assignment.id} references missing role {assignment.role_id}"
                )
            elif role is not None:
                if role.team_id != assignment.team_id:
                    errors.append(
                        f"project assignment {assignment.id} role {role.id} belongs to team {role.team_id}"
                    )
                if role.project_id is not None and role.project_id != assignment.project_id:
                    errors.append(
                        f"project assignment {assignment.id} role {role.id} belongs to project {role.project_id}"
                    )
            key = (assignment.agent_id, assignment.project_id, assignment.kind)
            if key in seen_agent_project_kind:
                errors.append(
                    f"duplicate {assignment.kind.value} assignment for agent {assignment.agent_id} project {assignment.project_id}"
                )
            seen_agent_project_kind.add(key)
            if assignment.kind == AgentProjectAssignmentKind.PROJECT_LEAD:
                lead_assignments_by_project.setdefault(assignment.project_id, []).append(
                    assignment.agent_id
                )
        for project in self.projects.values():
            lead_agent_ids = lead_assignments_by_project.get(project.id, [])
            if len(lead_agent_ids) != 1:
                errors.append(f"project {project.id} must have exactly one project lead assignment")
            elif project.lead_agent_id != lead_agent_ids[0]:
                errors.append(
                    f"project {project.id} lead_agent_id {project.lead_agent_id} does not match lead assignment {lead_agent_ids[0]}"
                )
            for subordinate_id in agent.subordinate_agent_ids:
                subordinate = self.agents.get(subordinate_id)
                if subordinate is None:
                    errors.append(
                        f"agent {agent.id} references missing subordinate {subordinate_id}"
                    )
                elif subordinate.reports_to_agent_id != agent.id:
                    errors.append(
                        f"agent {agent.id} subordinate {subordinate_id} does not report back"
                    )
        for agent_id in self.agents:
            self._validate_no_reporting_cycle(agent_id, errors)

    def _validate_agent_runtimes(self, errors: list[str]) -> None:
        for agent_id, runtime in self.agent_runtimes.items():
            if runtime.agent_id != agent_id:
                errors.append(f"agent runtime {agent_id} does not point back")
            if agent_id not in self.agents:
                errors.append(f"agent runtime {agent_id} references missing agent")

    def _validate_no_reporting_cycle(self, agent_id: str, errors: list[str]) -> None:
        seen: set[str] = set()
        current_id: str | None = agent_id
        while current_id is not None:
            if current_id in seen:
                errors.append(f"agent reporting cycle includes {current_id}")
                return
            seen.add(current_id)
            current = self.agents.get(current_id)
            current_id = current.reports_to_agent_id if current is not None else None

    def _validate_external_source_evidence_snapshots(self, errors: list[str]) -> None:
        for snapshot in self.external_source_evidence_snapshots.values():
            if snapshot.team_id not in self.teams:
                errors.append(
                    f"external source evidence snapshot {snapshot.id} references "
                    f"missing team {snapshot.team_id}"
                )
            if snapshot.project_id is not None:
                project = self.projects.get(snapshot.project_id)
                if project is None:
                    errors.append(
                        f"external source evidence snapshot {snapshot.id} references "
                        f"missing project {snapshot.project_id}"
                    )
                elif project.team_id != snapshot.team_id:
                    errors.append(
                        f"external source evidence snapshot {snapshot.id} project "
                        f"{project.id} belongs to team {project.team_id}"
                    )

    def _validate_cases(self, errors: list[str]) -> None:
        for case in self.cases.values():
            team = self.teams.get(case.team_id)
            if team is None:
                errors.append(f"case {case.id} references missing team {case.team_id}")
            if case.project_id is not None:
                project = self.projects.get(case.project_id)
                if project is None:
                    errors.append(f"case {case.id} references missing project {case.project_id}")
                elif project.team_id != case.team_id:
                    errors.append(
                        f"case {case.id} project {project.id} belongs to team {project.team_id}"
                    )
            if case.owner_agent_id is not None:
                owner = self.agents.get(case.owner_agent_id)
                if owner is None:
                    errors.append(f"case {case.id} references missing owner {case.owner_agent_id}")
                elif owner.team_id != case.team_id and not (
                    case.status == TeamCaseStatus.CLOSED
                    and owner.team_id is None
                    and owner.retired_team_id == case.team_id
                ):
                    errors.append(
                        f"case {case.id} owner {owner.id} belongs to team {owner.team_id}"
                    )
            for task_id in case.task_ids:
                task = self.tasks.get(task_id)
                if task is None:
                    errors.append(f"case {case.id} references missing task {task_id}")
                    continue
                if task.case_id != case.id:
                    errors.append(f"case {case.id} task {task_id} does not point back")
                project = self.projects.get(task.project_id)
                if project is not None and project.team_id != case.team_id:
                    errors.append(
                        f"case {case.id} task {task.id} belongs to team {project.team_id}"
                    )
                if case.project_id is not None and task.project_id != case.project_id:
                    errors.append(
                        f"case {case.id} task {task.id} belongs to project {task.project_id}"
                    )
            for snapshot_id in case.source_evidence_snapshot_ids:
                snapshot = self.external_source_evidence_snapshots.get(snapshot_id)
                if snapshot is None:
                    errors.append(
                        f"case {case.id} references missing external source evidence "
                        f"snapshot {snapshot_id}"
                    )
                    continue
                if snapshot.team_id != case.team_id:
                    errors.append(
                        f"case {case.id} external source evidence snapshot {snapshot.id} "
                        f"belongs to team {snapshot.team_id}"
                    )
                if snapshot.project_id is not None and snapshot.project_id != case.project_id:
                    errors.append(
                        f"case {case.id} external source evidence snapshot {snapshot.id} "
                        f"belongs to project {snapshot.project_id}"
                    )

    def _validate_tasks(self, errors: list[str]) -> None:
        from .model_queries import resolve_outcome_kind_definition

        terminal_task_states = {
            TeamTaskState.FINISHED,
            TeamTaskState.ACCEPTED,
            TeamTaskState.REJECTED,
            TeamTaskState.CANCELLED,
        }
        for task in self.tasks.values():
            task_is_terminal = task.state in terminal_task_states
            owner = self.agents.get(task.owner_agent_id)
            if owner is None and task.owner_agent_id is not None:
                errors.append(f"task {task.id} references missing owner {task.owner_agent_id}")
            if task.assignment_mode == "role":
                assignment_role = self.roles.get(task.assignment_role_id or "")
                assignment_project = self.projects.get(task.project_id)
                if (
                    assignment_role is None
                    or assignment_project is None
                    or (
                        assignment_role.team_id != assignment_project.team_id
                        or assignment_role.project_id not in {None, task.project_id}
                    )
                ):
                    errors.append(f"task {task.id} has an invalid assignment role")
            project = self.projects.get(task.project_id)
            if project is None:
                errors.append(f"task {task.id} references missing project {task.project_id}")
            elif not task_is_terminal and owner is not None and owner.team_id != project.team_id:
                errors.append(f"task {task.id} owner {owner.id} is outside project team")
            if project is not None and task.outcome_contract is not None:
                contract = task.outcome_contract
                referenced_kinds = (
                    contract.required_outcome_kinds + contract.forbidden_outcome_kinds
                )
                for kind in referenced_kinds:
                    if (
                        resolve_outcome_kind_definition(
                            self,
                            team_id=project.team_id,
                            project_id=project.id,
                            kind=kind,
                            include_archived=True,
                        )
                        is None
                    ):
                        errors.append(f"task {task.id} references unknown outcome kind {kind}")
                snapshot_kinds = {
                    definition.kind for definition in contract.outcome_kind_definitions
                }
                missing_snapshots = sorted(set(referenced_kinds) - snapshot_kinds)
                if missing_snapshots:
                    errors.append(
                        f"task {task.id} is missing outcome kind definition snapshots: "
                        + ", ".join(missing_snapshots)
                    )
            if task.case_id is not None:
                case = self.cases.get(task.case_id)
                if case is None:
                    errors.append(f"task {task.id} references missing case {task.case_id}")
                elif project is not None:
                    if case.team_id != project.team_id:
                        errors.append(
                            f"task {task.id} case {case.id} belongs to team {case.team_id}"
                        )
                    if case.project_id is not None and case.project_id != task.project_id:
                        errors.append(
                            f"task {task.id} case {case.id} belongs to project {case.project_id}"
                        )
                    if task.id not in case.task_ids:
                        errors.append(f"task {task.id} case {case.id} does not link back")
            if task.created_by_agent_id is not None and task.created_by_agent_id not in self.agents:
                errors.append(
                    f"task {task.id} references missing creator {task.created_by_agent_id}"
                )
            for deliverable_id in task.deliverable_ids:
                deliverable = self.deliverables.get(deliverable_id)
                if deliverable is None:
                    errors.append(f"task {task.id} references missing deliverable {deliverable_id}")
                elif deliverable.task_id != task.id:
                    errors.append(
                        f"task {task.id} deliverable {deliverable_id} does not point back"
                    )
            for snapshot_id in task.source_evidence_snapshot_ids:
                snapshot = self.external_source_evidence_snapshots.get(snapshot_id)
                if snapshot is None:
                    errors.append(
                        f"task {task.id} references missing external source evidence "
                        f"snapshot {snapshot_id}"
                    )
                    continue
                if project is not None and snapshot.team_id != project.team_id:
                    errors.append(
                        f"task {task.id} external source evidence snapshot {snapshot.id} "
                        f"belongs to team {snapshot.team_id}"
                    )
                if snapshot.project_id is not None and snapshot.project_id != task.project_id:
                    errors.append(
                        f"task {task.id} external source evidence snapshot {snapshot.id} "
                        f"belongs to project {snapshot.project_id}"
                    )

    def _validate_task_graph(self, errors: list[str]) -> None:
        # Historical graph contradictions are deliberately readable. Command
        # boundaries prevent new invalid edges, while TaskAssessment reports
        # missing/cross-project/cyclic parent and dependency relationships and
        # blocks new completion or publication until an audited repair occurs.
        return

    def _validate_no_parent_cycle(self, task_id: str, errors: list[str]) -> None:
        seen: set[str] = set()
        current_id: str | None = task_id
        while current_id is not None:
            if current_id in seen:
                errors.append(f"task parent cycle includes {current_id}")
                return
            seen.add(current_id)
            current = self.tasks.get(current_id)
            current_id = current.parent_task_id if current is not None else None

    def _validate_no_dependency_cycle(self, errors: list[str]) -> None:
        visited: set[str] = set()
        visiting: set[str] = set()
        reported = False
        for start in self.tasks:
            if start in visited:
                continue
            stack: list[str] = [start]
            while stack:
                node = stack[-1]
                visiting.add(node)
                task = self.tasks.get(node)
                dependencies = task.dependency_task_ids if task is not None else []
                advanced = False
                for dependency_id in dependencies:
                    if dependency_id not in self.tasks:
                        continue
                    if dependency_id in visited:
                        continue
                    if dependency_id in visiting:
                        if not reported:
                            errors.append(f"task dependency cycle includes {dependency_id}")
                            reported = True
                        continue
                    stack.append(dependency_id)
                    advanced = True
                    break
                if not advanced:
                    visiting.discard(node)
                    visited.add(node)
                    stack.pop()

    def child_task_ids(self, task_id: str) -> list[str]:
        return sorted(task.id for task in self.tasks.values() if task.parent_task_id == task_id)

    def dependency_status(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None or not task.dependency_task_ids:
            return "satisfied"
        dependency_states = [
            self.tasks[dep_id].state for dep_id in task.dependency_task_ids if dep_id in self.tasks
        ]
        if any(
            state in {TeamTaskState.BLOCKED, TeamTaskState.REJECTED} for state in dependency_states
        ):
            return "blocked"
        if any(
            state
            not in {
                TeamTaskState.ACCEPTED,
                TeamTaskState.FINISHED,
                TeamTaskState.CANCELLED,
            }
            for state in dependency_states
        ):
            return "waiting"
        return "satisfied"

    def blocked_by_task_ids(self, task_id: str) -> list[str]:
        task = self.tasks.get(task_id)
        if task is None:
            return []
        blocked_by: list[str] = []
        for dependency_id in task.dependency_task_ids:
            dependency = self.tasks.get(dependency_id)
            if dependency is None:
                continue
            if dependency.state not in {
                TeamTaskState.ACCEPTED,
                TeamTaskState.FINISHED,
                TeamTaskState.CANCELLED,
            }:
                blocked_by.append(dependency_id)
        return blocked_by

    def subtask_counts(
        self, task_id: str, *, child_task_ids: Sequence[str] | None = None,
    ) -> dict[str, int]:
        counts = {"total": 0, "accepted": 0, "finished": 0, "blocked": 0, "open": 0}
        for child_id in self.child_task_ids(task_id) if child_task_ids is None else child_task_ids:
            child = self.tasks[child_id]
            counts["total"] += 1
            if child.state == TeamTaskState.ACCEPTED:
                counts["accepted"] += 1
            elif child.state in {TeamTaskState.FINISHED, TeamTaskState.CANCELLED}:
                counts["finished"] += 1
            elif child.state in {TeamTaskState.BLOCKED, TeamTaskState.REJECTED}:
                counts["blocked"] += 1
            elif child.state in {
                TeamTaskState.PENDING,
                TeamTaskState.IN_PROGRESS,
                TeamTaskState.WAITING_FOR_INPUT,
                TeamTaskState.WAITING_FOR_REVIEW,
            }:
                counts["open"] += 1
        return counts

    def _validate_deliverables(self, errors: list[str]) -> None:
        from .model_queries import resolve_outcome_kind_definition

        for deliverable in self.deliverables.values():
            task = self.tasks.get(deliverable.task_id)
            if task is None:
                errors.append(
                    f"deliverable {deliverable.id} references missing task {deliverable.task_id}"
                )
            else:
                project = self.projects.get(task.project_id)
                if project is not None:
                    for outcome in deliverable.outcomes:
                        definition = resolve_outcome_kind_definition(
                            self,
                            team_id=project.team_id,
                            project_id=project.id,
                            kind=outcome.kind,
                            include_archived=True,
                        )
                        if definition is None:
                            errors.append(
                                f"deliverable {deliverable.id} references unknown "
                                f"outcome kind {outcome.kind}"
                            )
                        if outcome.definition_snapshot is None:
                            errors.append(
                                f"deliverable {deliverable.id} outcome {outcome.kind} "
                                "is missing its definition snapshot"
                            )
            if deliverable.producer_agent_id not in self.agents:
                errors.append(
                    f"deliverable {deliverable.id} references missing producer "
                    f"{deliverable.producer_agent_id}"
                )
            disposition = deliverable.initial_release_disposition
            if (
                disposition is not None
                and disposition.state == InitialReleaseDispositionState.RELEASE_REQUESTED
            ):
                release = self.release_packages.get(disposition.release_package_id or "")
                if release is None:
                    errors.append(
                        f"deliverable {deliverable.id} initial release disposition "
                        f"references missing release {disposition.release_package_id}"
                    )
                elif deliverable.id not in release.deliverable_ids:
                    errors.append(
                        f"deliverable {deliverable.id} initial release disposition "
                        f"references release {release.id} that does not include it"
                    )
            if (
                deliverable.rework_requested_by_agent_id is not None
                and deliverable.rework_requested_by_agent_id not in self.agents
            ):
                errors.append(
                    f"deliverable {deliverable.id} references missing rework requester "
                    f"{deliverable.rework_requested_by_agent_id}"
                )
            for review_request_id in deliverable.review_request_ids:
                review = self.review_requests.get(review_request_id)
                if review is None:
                    errors.append(
                        f"deliverable {deliverable.id} references missing review {review_request_id}"
                    )
                elif review.deliverable_id != deliverable.id:
                    errors.append(
                        f"deliverable {deliverable.id} review {review_request_id} does not point back"
                    )
            finding_ids: set[str] = set()
            for finding in deliverable.carried_findings:
                if finding.id in finding_ids:
                    errors.append(
                        f"deliverable {deliverable.id} has duplicate carried finding {finding.id}"
                    )
                finding_ids.add(finding.id)
                if finding.source_review_request_id not in self.review_requests:
                    errors.append(
                        f"deliverable {deliverable.id} finding {finding.id} references "
                        f"missing source review {finding.source_review_request_id}"
                    )
                if finding.source_deliverable_id not in self.deliverables:
                    errors.append(
                        f"deliverable {deliverable.id} finding {finding.id} references "
                        f"missing source deliverable {finding.source_deliverable_id}"
                    )
                if (
                    finding.resolved_by_review_request_id is not None
                    and finding.resolved_by_review_request_id not in self.review_requests
                ):
                    errors.append(
                        f"deliverable {deliverable.id} finding {finding.id} references "
                        f"missing resolving review {finding.resolved_by_review_request_id}"
                    )
            if deliverable.supersedes_deliverable_id is not None:
                previous = self.deliverables.get(deliverable.supersedes_deliverable_id)
                if previous is None:
                    errors.append(
                        f"deliverable {deliverable.id} supersedes missing deliverable "
                        f"{deliverable.supersedes_deliverable_id}"
                    )
                elif previous.id == deliverable.id:
                    errors.append(f"deliverable {deliverable.id} cannot supersede itself")
                else:
                    previous_task = self.tasks.get(previous.task_id)
                    replacement_task = self.tasks.get(deliverable.task_id)
                    if (
                        previous_task is not None
                        and replacement_task is not None
                        and previous_task.project_id != replacement_task.project_id
                    ):
                        errors.append(
                            f"deliverable {deliverable.id} supersedes deliverable "
                            f"{previous.id} from another project"
                        )
                    if previous.superseded_by_deliverable_id != deliverable.id:
                        errors.append(
                            f"deliverable {deliverable.id} supersedes deliverable "
                            f"{previous.id} without a reciprocal superseded_by link"
                        )
                    if previous.state not in {
                        TeamRecordState.REJECTED,
                        TeamRecordState.BLOCKED,
                    } and not (
                        previous.state == TeamRecordState.ACCEPTED
                        and previous.rework_requested_at is not None
                    ):
                        errors.append(
                            f"deliverable {deliverable.id} supersedes a deliverable "
                            f"{previous.id} that was neither rejected, blocked, nor "
                            "explicitly reopened for rework"
                        )
            if deliverable.superseded_by_deliverable_id is not None:
                replacement = self.deliverables.get(deliverable.superseded_by_deliverable_id)
                if replacement is None:
                    errors.append(
                        f"deliverable {deliverable.id} is superseded by missing deliverable "
                        f"{deliverable.superseded_by_deliverable_id}"
                    )
                elif replacement.id == deliverable.id:
                    errors.append(f"deliverable {deliverable.id} cannot supersede itself")
                else:
                    predecessor_task = self.tasks.get(deliverable.task_id)
                    replacement_task = self.tasks.get(replacement.task_id)
                    if (
                        predecessor_task is not None
                        and replacement_task is not None
                        and predecessor_task.project_id != replacement_task.project_id
                    ):
                        errors.append(
                            f"deliverable {deliverable.id} is superseded by deliverable "
                            f"{replacement.id} from another project"
                        )
                    if replacement.supersedes_deliverable_id != deliverable.id:
                        errors.append(
                            f"deliverable {deliverable.id} is superseded by deliverable "
                            f"{replacement.id} without a reciprocal supersedes link"
                        )
                    if deliverable.state not in {
                        TeamRecordState.REJECTED,
                        TeamRecordState.BLOCKED,
                    } and not (
                        deliverable.state == TeamRecordState.ACCEPTED
                        and deliverable.rework_requested_at is not None
                    ):
                        errors.append(
                            f"deliverable {deliverable.id} cannot be superseded without "
                            "rejection, a block, or an explicit rework request"
                        )

    def _validate_outcome_kind_definitions(self, errors: list[str]) -> None:
        seen_scopes: set[tuple[str, str | None, str]] = set()
        for definition in self.outcome_kind_definitions.values():
            if definition.team_id not in self.teams:
                errors.append(
                    f"outcome kind {definition.id} references missing team {definition.team_id}"
                )
            if definition.project_id is not None:
                project = self.projects.get(definition.project_id)
                if project is None:
                    errors.append(
                        f"outcome kind {definition.id} references missing project "
                        f"{definition.project_id}"
                    )
                elif project.team_id != definition.team_id:
                    errors.append(
                        f"outcome kind {definition.id} project {project.id} belongs "
                        f"to team {project.team_id}"
                    )
            scope = (
                definition.team_id,
                definition.project_id,
                definition.kind,
            )
            if scope in seen_scopes:
                errors.append(
                    "duplicate outcome kind definition for "
                    f"{definition.kind} in "
                    f"{definition.project_id or definition.team_id}"
                )
            seen_scopes.add(scope)

    def _validate_review_profiles(self, errors: list[str]) -> None:
        from .model_queries import resolve_outcome_kind_definition

        for profile in self.review_profiles.values():
            if profile.team_id not in self.teams:
                errors.append(
                    f"review profile {profile.id} references missing team {profile.team_id}"
                )
            if profile.project_id is not None:
                project = self.projects.get(profile.project_id)
                if project is None:
                    errors.append(
                        f"review profile {profile.id} references missing project "
                        f"{profile.project_id}"
                    )
                elif project.team_id != profile.team_id:
                    errors.append(
                        f"review profile {profile.id} project {project.id} belongs to team "
                        f"{project.team_id}"
                    )
            for role_id in profile.applicable_role_ids:
                role = self.roles.get(role_id)
                if role is None:
                    errors.append(f"review profile {profile.id} references missing role {role_id}")
                elif role.team_id != profile.team_id:
                    errors.append(
                        f"review profile {profile.id} role {role.id} belongs to team {role.team_id}"
                    )
                elif role.project_id is not None and role.project_id != profile.project_id:
                    errors.append(
                        f"review profile {profile.id} role {role.id} belongs to project "
                        f"{role.project_id}"
                    )
            for kind in profile.required_outcome_kinds:
                if (
                    resolve_outcome_kind_definition(
                        self,
                        team_id=profile.team_id,
                        project_id=profile.project_id,
                        kind=kind,
                        include_archived=profile.archived,
                    )
                    is None
                ):
                    errors.append(
                        f"review profile {profile.id} references unknown outcome kind {kind}"
                    )

    def _validate_reviews(self, errors: list[str]) -> None:
        current_cohort_reviewers: set[tuple[str, str, str]] = set()
        current_release_cohort_members: set[tuple[str, str, str, str | None]] = set()
        for review in self.review_requests.values():
            if review.deliverable_id is not None and review.deliverable_id not in self.deliverables:
                errors.append(
                    f"review request {review.id} references missing deliverable "
                    f"{review.deliverable_id}"
                )
            deliverable = (
                self.deliverables.get(review.deliverable_id)
                if review.deliverable_id is not None
                else None
            )
            release = (
                self.release_packages.get(review.release_package_id)
                if review.release_package_id is not None
                else None
            )
            if (
                review.review_cohort_id is not None
                and deliverable is not None
                and review.review_cohort_id != deliverable.review_cohort_id
            ):
                errors.append(
                    f"review request {review.id} cohort "
                    f"{review.review_cohort_id} does not match deliverable "
                    f"{deliverable.id} cohort {deliverable.review_cohort_id}"
                )
            if (
                deliverable is not None
                and deliverable.review_cohort_id is not None
                and review.superseded_by_review_request_id is None
                and review.retired_at is None
                and review.review_cohort_id != deliverable.review_cohort_id
            ):
                errors.append(
                    f"current review request {review.id} must carry deliverable "
                    f"{deliverable.id} cohort {deliverable.review_cohort_id}"
                )
            if (
                review.deliverable_id is not None
                and review.review_cohort_id is not None
                and review.superseded_by_review_request_id is None
                and review.retired_at is None
            ):
                cohort_reviewer_key = (
                    review.deliverable_id,
                    review.review_cohort_id,
                    review.reviewer_agent_id,
                )
                if cohort_reviewer_key in current_cohort_reviewers:
                    errors.append(
                        f"deliverable {review.deliverable_id} cohort "
                        f"{review.review_cohort_id} has duplicate current reviewer "
                        f"{review.reviewer_agent_id}"
                    )
                current_cohort_reviewers.add(cohort_reviewer_key)
            if (
                review.review_cohort_id is not None
                and release is not None
                and review.review_cohort_id != release.review_cohort_id
            ):
                errors.append(
                    f"release review {review.id} cohort {review.review_cohort_id} does not "
                    f"match release {release.id} cohort {release.review_cohort_id}"
                )
            if (
                release is not None
                and release.review_cohort_id is not None
                and review.review_cohort_id != release.review_cohort_id
            ):
                errors.append(
                    f"release review {review.id} must carry release {release.id} cohort "
                    f"{release.review_cohort_id}"
                )
            if (
                review.release_package_id is not None
                and review.review_cohort_id is not None
                and review.superseded_by_review_request_id is None
                and review.retired_at is None
            ):
                release_member_key = (
                    review.release_package_id,
                    review.review_cohort_id,
                    review.reviewer_agent_id,
                    review.review_profile_id,
                )
                if release_member_key in current_release_cohort_members:
                    errors.append(
                        f"release {review.release_package_id} cohort "
                        f"{review.review_cohort_id} has duplicate current reviewer/profile "
                        f"slot {review.reviewer_agent_id}/{review.review_profile_id}"
                    )
                current_release_cohort_members.add(release_member_key)
            if (
                review.release_package_id is not None
                and review.release_package_id not in self.release_packages
            ):
                errors.append(
                    f"review request {review.id} references missing release package "
                    f"{review.release_package_id}"
                )
            if review.reviewer_agent_id not in self.agents:
                errors.append(
                    f"review request {review.id} references missing reviewer {review.reviewer_agent_id}"
                )
            if (
                review.requested_by_agent_id is not None
                and review.requested_by_agent_id not in self.agents
            ):
                errors.append(
                    f"review request {review.id} references missing requester "
                    f"{review.requested_by_agent_id}"
                )
            profile = None
            if review.review_profile_id is not None:
                profile = self.review_profiles.get(review.review_profile_id)
                if profile is None:
                    errors.append(
                        f"review request {review.id} references missing review profile "
                        f"{review.review_profile_id}"
                    )
            if review.criteria_snapshot is not None and review.review_profile_id is None:
                errors.append(
                    f"review request {review.id} has a criteria snapshot but no review profile"
                )
            if review.supersedes_review_request_id is not None:
                previous = self.review_requests.get(review.supersedes_review_request_id)
                if previous is None or (
                    previous.deliverable_id != review.deliverable_id
                    or previous.release_package_id != review.release_package_id
                ):
                    errors.append(f"review request {review.id} supersedes an invalid review")
                elif previous.superseded_by_review_request_id != review.id:
                    errors.append(
                        f"review request {review.id} has non-reciprocal supersession lineage"
                    )
            if review.superseded_by_review_request_id is not None:
                successor = self.review_requests.get(review.superseded_by_review_request_id)
                if successor is None or (
                    successor.deliverable_id != review.deliverable_id
                    or successor.release_package_id != review.release_package_id
                ):
                    errors.append(f"review request {review.id} has an invalid successor")
                elif successor.supersedes_review_request_id != review.id:
                    errors.append(
                        f"review request {review.id} has non-reciprocal successor lineage"
                    )
            if review.criteria and review.review_profile_id is None:
                errors.append(
                    f"review request {review.id} has criterion results but no review profile"
                )
            criterion_definitions = (
                review.criteria_snapshot
                if review.criteria_snapshot is not None
                else profile.criteria
                if profile is not None
                else []
            )
            valid_criterion_ids = {criterion.id for criterion in criterion_definitions}
            seen: set[str] = set()
            for result in review.criteria:
                if profile is not None and result.criterion_id not in valid_criterion_ids:
                    errors.append(
                        f"review request {review.id} criterion result references unknown "
                        f"criterion {result.criterion_id}"
                    )
                if result.criterion_id in seen:
                    errors.append(
                        f"review request {review.id} has duplicate criterion result "
                        f"{result.criterion_id}"
                    )
                seen.add(result.criterion_id)

    def _validate_inbox(self, errors: list[str]) -> None:
        for item in self.inbox.values():
            if item.recipient_agent_id not in self.agents:
                errors.append(
                    f"inbox item {item.id} references missing recipient {item.recipient_agent_id}"
                )
            if item.sender_agent_id is not None and item.sender_agent_id not in self.agents:
                errors.append(
                    f"inbox item {item.id} references missing sender {item.sender_agent_id}"
                )
            if item.task_id is not None and item.task_id not in self.tasks:
                errors.append(f"inbox item {item.id} references missing task {item.task_id}")

    def _validate_blockers(self, errors: list[str]) -> None:
        open_sources: dict[tuple[str, TeamBlockerSourceKind, str], str] = {}
        for blocker in self.blockers.values():
            team = self.teams.get(blocker.team_id)
            if team is None:
                errors.append(f"blocker {blocker.id} references missing team {blocker.team_id}")
            project = self.projects.get(blocker.project_id) if blocker.project_id else None
            if blocker.project_id is not None and project is None:
                errors.append(
                    f"blocker {blocker.id} references missing project {blocker.project_id}"
                )
            elif project is not None and project.team_id != blocker.team_id:
                errors.append(
                    f"blocker {blocker.id} project {project.id} belongs to team {project.team_id}"
                )
            task = self.tasks.get(blocker.task_id) if blocker.task_id else None
            if blocker.task_id is not None and task is None:
                errors.append(f"blocker {blocker.id} references missing task {blocker.task_id}")
            elif task is not None and blocker.project_id is not None:
                if task.project_id != blocker.project_id:
                    errors.append(
                        f"blocker {blocker.id} task {task.id} belongs to project {task.project_id}"
                    )
            for field_name, agent_id in (
                ("reporter", blocker.reporter_agent_id),
                ("owner", blocker.owner_agent_id),
                ("delegate", blocker.delegate_agent_id),
            ):
                if agent_id is None:
                    continue
                agent = self.agents.get(agent_id)
                if agent is None:
                    errors.append(
                        f"blocker {blocker.id} references missing {field_name} {agent_id}"
                    )
                elif (
                    blocker.state == TeamBlockerState.OPEN
                    and field_name in {"owner", "delegate"}
                    and agent.team_id != blocker.team_id
                ):
                    errors.append(
                        f"blocker {blocker.id} {field_name} {agent.id} belongs to team "
                        f"{agent.team_id}"
                    )
            source_exists = {
                TeamBlockerSourceKind.REVIEW_REQUEST: blocker.source_id in self.review_requests,
                TeamBlockerSourceKind.TASK: blocker.source_id in self.tasks,
                TeamBlockerSourceKind.DELIVERABLE: blocker.source_id in self.deliverables,
                TeamBlockerSourceKind.RELEASE_PACKAGE: blocker.source_id in self.release_packages,
                TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST: blocker.source_id
                in self.human_review_requests,
                TeamBlockerSourceKind.AGENT_EXECUTION_STALL: blocker.source_id in self.agents,
                TeamBlockerSourceKind.MCP_CAPABILITY: (
                    (
                        blocker.mcp_capability_identity is not None
                        and blocker.mcp_capability_identity.agent_id in self.agents
                        and all(
                            review_id in self.review_requests
                            for review_id in blocker.mcp_capability_identity.review_request_ids
                        )
                    )
                    or (
                        # Compatibility for blockers persisted before capability
                        # identity received dedicated structured storage.
                        blocker.mcp_capability_identity is None
                        and ":" in blocker.source_id
                        and blocker.source_id.split(":", 1)[0] in self.agents
                        and any(
                            review_id for review_id in blocker.source_id.split(":", 1)[1].split("|")
                        )
                        and all(
                            review_id in self.review_requests
                            for review_id in blocker.source_id.split(":", 1)[1].split("|")
                            if review_id
                        )
                    )
                ),
                TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR: (
                    blocker.source_id.startswith("candidate-correction-successor:")
                    and blocker.source_id.removeprefix("candidate-correction-successor:")
                    in self.release_packages
                ),
                TeamBlockerSourceKind.EXPLICIT: True,
            }[blocker.source_kind]
            if not source_exists:
                errors.append(
                    f"blocker {blocker.id} references missing {blocker.source_kind.value} "
                    f"{blocker.source_id}"
                )
            if blocker.state == TeamBlockerState.OPEN:
                key = (blocker.team_id, blocker.source_kind, blocker.source_id)
                if key in open_sources:
                    errors.append(
                        f"blockers {open_sources[key]} and {blocker.id} are both open for "
                        f"{blocker.source_kind.value} {blocker.source_id}"
                    )
                open_sources[key] = blocker.id
        for event in self.blocker_events.values():
            if event.blocker_id not in self.blockers:
                errors.append(
                    f"blocker event {event.id} references missing blocker {event.blocker_id}"
                )
            for field_name, agent_id in (
                ("actor", event.actor_agent_id),
                ("from agent", event.from_agent_id),
                ("to agent", event.to_agent_id),
            ):
                if agent_id is not None and agent_id not in self.agents:
                    errors.append(
                        f"blocker event {event.id} references missing {field_name} {agent_id}"
                    )

    def _validate_action_records(self, errors: list[str]) -> None:
        for record in self.action_records.values():
            if record.agent_id not in self.agents:
                errors.append(
                    f"action record {record.id} references missing agent {record.agent_id}"
                )

    def _validate_memories(self, errors: list[str]) -> None:
        for memory in self.memories.values():
            if memory.team_id not in self.teams:
                errors.append(f"memory {memory.id} references missing team {memory.team_id}")
            if memory.project_id is not None:
                project = self.projects.get(memory.project_id)
                if project is None:
                    errors.append(
                        f"memory {memory.id} references missing project {memory.project_id}"
                    )
                elif project.team_id != memory.team_id:
                    errors.append(
                        f"memory {memory.id} project {project.id} belongs to team {project.team_id}"
                    )
            if memory.agent_id is not None:
                agent = self.agents.get(memory.agent_id)
                if agent is None:
                    errors.append(f"memory {memory.id} references missing agent {memory.agent_id}")
                elif (agent.team_id or agent.retired_team_id) != memory.team_id:
                    errors.append(
                        f"memory {memory.id} agent {agent.id} belongs to team {agent.team_id}"
                    )
            if memory.created_by_agent_id is not None:
                creator = self.agents.get(memory.created_by_agent_id)
                if creator is None:
                    errors.append(
                        f"memory {memory.id} references missing creator "
                        f"{memory.created_by_agent_id}"
                    )
                elif (creator.team_id or creator.retired_team_id) != memory.team_id:
                    errors.append(
                        f"memory {memory.id} creator {creator.id} belongs to team {creator.team_id}"
                    )
            if (
                memory.supersedes_memory_id is not None
                and memory.supersedes_memory_id not in self.memories
            ):
                errors.append(
                    f"memory {memory.id} supersedes missing memory {memory.supersedes_memory_id}"
                )
            if (
                memory.superseded_by_memory_id is not None
                and memory.superseded_by_memory_id not in self.memories
            ):
                errors.append(
                    f"memory {memory.id} is superseded by missing memory "
                    f"{memory.superseded_by_memory_id}"
                )

    def _validate_conversations(self, errors: list[str]) -> None:
        conversation_pairs: dict[tuple[str, str], str] = {}
        for conversation in self.conversations.values():
            if conversation.agent_id not in self.agents:
                errors.append(
                    f"conversation {conversation.id} references missing agent "
                    f"{conversation.agent_id}"
                )
            pair = (conversation.agent_id, conversation.operator_user_id)
            existing = conversation_pairs.get(pair)
            if existing is not None:
                errors.append(
                    f"agent {conversation.agent_id} and operator "
                    f"{conversation.operator_user_id} have multiple conversations: "
                    f"{existing}, {conversation.id}"
                )
            conversation_pairs[pair] = conversation.id
        for message in self.conversation_messages.values():
            if message.conversation_id not in self.conversations:
                errors.append(
                    f"conversation message {message.id} references missing conversation "
                    f"{message.conversation_id}"
                )
            if (
                message.source_message_id is not None
                and message.source_message_id not in self.conversation_messages
            ):
                errors.append(
                    f"conversation message {message.id} references missing source message "
                    f"{message.source_message_id}"
                )
            for source_message_id in message.source_message_ids:
                if source_message_id not in self.conversation_messages:
                    errors.append(
                        f"conversation message {message.id} references missing source message "
                        f"{source_message_id}"
                    )
            for attachment_id in message.attachment_ids:
                attachment = self.chat_attachments.get(attachment_id)
                if attachment is None:
                    errors.append(
                        f"conversation message {message.id} references missing attachment "
                        f"{attachment_id}"
                    )
                elif attachment.message_id != message.id:
                    errors.append(
                        f"attachment {attachment.id} belongs to message {attachment.message_id}, "
                        f"not {message.id}"
                    )
        for attachment in self.chat_attachments.values():
            message = self.conversation_messages.get(attachment.message_id)
            if message is None:
                errors.append(
                    f"chat attachment {attachment.id} references missing message "
                    f"{attachment.message_id}"
                )
            if attachment.conversation_id not in self.conversations:
                errors.append(
                    f"chat attachment {attachment.id} references missing conversation "
                    f"{attachment.conversation_id}"
                )
            if message is not None and attachment.conversation_id != message.conversation_id:
                errors.append(
                    f"chat attachment {attachment.id} conversation {attachment.conversation_id} "
                    f"does not match message conversation {message.conversation_id}"
                )
            if message is not None and attachment.id not in message.attachment_ids:
                errors.append(
                    f"chat attachment {attachment.id} is not linked from message {message.id}"
                )
        for dispatch in self.dispatches.values():
            chat_conversation_ids: set[str] = set()
            for message_id in dispatch.chat_message_ids:
                message = self.conversation_messages.get(message_id)
                if message is None:
                    errors.append(
                        f"dispatch {dispatch.id} references missing chat message {message_id}"
                    )
                    continue
                if message.sender != "operator":
                    errors.append(
                        f"dispatch {dispatch.id} chat message {message_id} is not an operator message"
                    )
                if message.dispatch_id != dispatch.id:
                    errors.append(
                        f"dispatch {dispatch.id} chat message {message_id} has a different dispatch"
                    )
                chat_conversation_ids.add(message.conversation_id)
            if len(chat_conversation_ids) > 1:
                errors.append(f"dispatch {dispatch.id} chat messages span multiple conversations")

    def _validate_external_chat_bridge(self, errors: list[str]) -> None:
        ingress_keys: set[tuple[str, str, str]] = set()
        for record in self.external_chat_ingress.values():
            key = (
                record.provider,
                record.communication_target_id,
                record.source_message_id,
            )
            if key in ingress_keys:
                errors.append(
                    "external chat ingress has duplicate provider/target/source message "
                    f"identity {key!r}"
                )
            ingress_keys.add(key)
            message = self.conversation_messages.get(record.message_id)
            if message is None:
                errors.append(
                    f"external chat ingress {record.id} references missing message {record.message_id}"
                )
                continue
            if message.conversation_id != record.conversation_id:
                errors.append(
                    f"external chat ingress {record.id} conversation does not match message"
                )
            if message.dispatch_id != record.dispatch_id:
                errors.append(f"external chat ingress {record.id} dispatch does not match message")
        delivery_keys: set[tuple[str, str]] = set()
        for record in self.external_chat_deliveries.values():
            key = (record.agent_reply_message_id, record.communication_target_id)
            if key in delivery_keys:
                errors.append(f"external chat delivery has duplicate reply/target identity {key!r}")
            delivery_keys.add(key)
            reply = self.conversation_messages.get(record.agent_reply_message_id)
            if reply is None or reply.sender != "agent":
                errors.append(
                    f"external chat delivery {record.id} references missing agent reply "
                    f"{record.agent_reply_message_id}"
                )
            operator_message = self.conversation_messages.get(record.operator_message_id)
            if operator_message is None or operator_message.sender != "operator":
                errors.append(
                    f"external chat delivery {record.id} references missing operator message "
                    f"{record.operator_message_id}"
                )

    def _validate_releases(self, errors: list[str]) -> None:
        validate_release_packages(self, errors)

    def _validate_human_reviews(self, errors: list[str]) -> None:
        request_ids_by_release: dict[str, list[str]] = {}
        for request in self.human_review_requests.values():
            release = self.release_packages.get(request.release_package_id)
            if release is None:
                errors.append(
                    f"human review {request.id} references missing release "
                    f"{request.release_package_id}"
                )
                continue
            request_ids_by_release.setdefault(release.id, []).append(request.id)
            if release.candidate_commit_sha != request.candidate_commit_sha:
                errors.append(
                    f"human review {request.id} candidate does not match release {release.id}"
                )
            if release.candidate_tree_hash != request.candidate_tree_hash:
                errors.append(f"human review {request.id} tree does not match release {release.id}")
        for release_id, request_ids in request_ids_by_release.items():
            current = [
                request_id
                for request_id in request_ids
                if self.human_review_requests[request_id].state
                in {"preparing", "pending", "blocked"}
            ]
            if len(current) > 1:
                errors.append(f"release {release_id} has multiple current human review requests")

        decision_keys: set[tuple[str, str]] = set()
        for decision in self.human_review_decisions.values():
            if decision.human_review_request_id not in self.human_review_requests:
                errors.append(
                    f"human decision {decision.id} references missing request "
                    f"{decision.human_review_request_id}"
                )
            for finding_id in decision.confirmed_finding_ids:
                if finding_id not in self.human_review_findings:
                    errors.append(
                        f"human decision {decision.id} confirms missing finding {finding_id}"
                    )
            key = (decision.human_review_request_id, decision.request_id)
            if key in decision_keys:
                errors.append(
                    f"human review {decision.human_review_request_id} has duplicate "
                    f"decision request id {decision.request_id}"
                )
            decision_keys.add(key)

        for finding in self.human_review_findings.values():
            source_request = self.human_review_requests.get(finding.source_human_review_request_id)
            if source_request is None:
                errors.append(
                    f"human finding {finding.id} references missing source request "
                    f"{finding.source_human_review_request_id}"
                )
            if finding.correction_task_id not in self.tasks:
                errors.append(
                    f"human finding {finding.id} references missing correction task "
                    f"{finding.correction_task_id}"
                )
            for deliverable_id in finding.target_deliverable_ids:
                if deliverable_id not in self.deliverables:
                    errors.append(
                        f"human finding {finding.id} references missing deliverable "
                        f"{deliverable_id}"
                    )
            for deliverable_id in finding.proposed_by_deliverable_ids:
                if deliverable_id not in self.deliverables:
                    errors.append(
                        f"human finding {finding.id} references missing proposed "
                        f"deliverable {deliverable_id}"
                    )
            if (
                finding.resolved_by_human_review_request_id is not None
                and finding.resolved_by_human_review_request_id not in self.human_review_requests
            ):
                errors.append(
                    f"human finding {finding.id} references missing resolving request "
                    f"{finding.resolved_by_human_review_request_id}"
                )
            elif finding.resolved_by_human_review_request_id is not None:
                resolving_request = self.human_review_requests[
                    finding.resolved_by_human_review_request_id
                ]
                if resolving_request.id == finding.source_human_review_request_id:
                    errors.append(
                        f"human finding {finding.id} cannot be resolved by its source request"
                    )
                elif source_request is not None and not self._release_descends_from(
                    resolving_request.release_package_id,
                    source_request.release_package_id,
                ):
                    errors.append(
                        f"human finding {finding.id} resolving request "
                        f"{resolving_request.id} is not on a successor release"
                    )

    def _release_descends_from(
        self,
        release_package_id: str,
        ancestor_release_package_id: str,
    ) -> bool:
        current_id = release_package_id
        seen: set[str] = set()
        while current_id not in seen:
            seen.add(current_id)
            current = self.release_packages.get(current_id)
            if current is None or current.supersedes_release_package_id is None:
                return False
            current_id = current.supersedes_release_package_id
            if current_id == ancestor_release_package_id:
                return True
        return False

    def _validate_dispatches(self, errors: list[str]) -> None:
        for dispatch in self.dispatches.values():
            dispatch_is_live = dispatch.status in {
                TeamDispatchStatus.PREPARING,
                TeamDispatchStatus.LEASED,
            }
            agent = self.agents.get(dispatch.agent_id)
            if agent is None:
                errors.append(
                    f"dispatch {dispatch.id} references missing agent {dispatch.agent_id}"
                )
            elif dispatch_is_live and agent.session_id != dispatch.session_id:
                errors.append(f"dispatch {dispatch.id} session does not match agent {agent.id}")
            for task_id in dispatch.task_ids:
                task = self.tasks.get(task_id)
                if task is None:
                    errors.append(f"dispatch {dispatch.id} references missing task {task_id}")
            for blocker_id in dispatch.blocker_ids:
                if blocker_id not in self.blockers:
                    errors.append(f"dispatch {dispatch.id} references missing blocker {blocker_id}")
            for review_id in dispatch.review_request_ids:
                review = self.review_requests.get(review_id)
                if review is None:
                    errors.append(f"dispatch {dispatch.id} references missing review {review_id}")
            for inbox_id in dispatch.inbox_item_ids:
                item = self.inbox.get(inbox_id)
                if item is None:
                    errors.append(
                        f"dispatch {dispatch.id} references missing inbox item {inbox_id}"
                    )
            for project_id in dispatch.project_ids:
                if project_id not in self.projects:
                    errors.append(f"dispatch {dispatch.id} references missing project {project_id}")
            for snapshot_id in dispatch.source_evidence_snapshot_ids:
                snapshot = self.external_source_evidence_snapshots.get(snapshot_id)
                if snapshot is None:
                    errors.append(
                        f"dispatch {dispatch.id} references missing external source evidence "
                        f"snapshot {snapshot_id}"
                    )
                    continue
                effective_agent_team_id = (
                    agent.team_id or agent.retired_team_id if agent is not None else None
                )
                if (
                    effective_agent_team_id is not None
                    and snapshot.team_id != effective_agent_team_id
                ):
                    errors.append(
                        f"dispatch {dispatch.id} external source evidence snapshot "
                        f"{snapshot.id} belongs to team {snapshot.team_id}"
                    )
                if (
                    snapshot.project_id is not None
                    and snapshot.project_id not in dispatch.project_ids
                ):
                    errors.append(
                        f"dispatch {dispatch.id} external source evidence snapshot "
                        f"{snapshot.id} is outside dispatch project scope"
                    )
            for deliverable_id in dispatch.initial_release_decision_deliverable_ids:
                if deliverable_id not in self.deliverables:
                    errors.append(
                        f"dispatch {dispatch.id} initial release authority references "
                        f"missing deliverable {deliverable_id}"
                    )
            for authority in dispatch.review_routing_authorities:
                reviewer = self.agents.get(authority.reviewer_agent_id)
                project = self.projects.get(authority.project_id)
                task = self.tasks.get(authority.task_id)
                deliverable = self.deliverables.get(authority.deliverable_id)
                if reviewer is None:
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority references "
                        f"missing agent {authority.reviewer_agent_id}"
                    )
                if project is None:
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority references "
                        f"missing project {authority.project_id}"
                    )
                if task is None:
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority references "
                        f"missing task {authority.task_id}"
                    )
                elif task.project_id != authority.project_id:
                    errors.append(
                        f"dispatch {dispatch.id} review routing task {task.id} "
                        f"does not belong to project {authority.project_id}"
                    )
                if deliverable is None:
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority references "
                        f"missing deliverable {authority.deliverable_id}"
                    )
                elif deliverable.task_id != authority.task_id:
                    errors.append(
                        f"dispatch {dispatch.id} review routing deliverable "
                        f"{deliverable.id} does not belong to task {authority.task_id}"
                    )
                consumed_review = (
                    self.review_requests.get(authority.consumed_by_review_request_id)
                    if authority.consumed_by_review_request_id is not None
                    else None
                )
                if authority.consumed_by_review_request_id is not None and consumed_review is None:
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority references "
                        "missing consuming review "
                        f"{authority.consumed_by_review_request_id}"
                    )
                elif consumed_review is not None and (
                    consumed_review.deliverable_id != authority.deliverable_id
                    or consumed_review.reviewer_agent_id != authority.reviewer_agent_id
                    or consumed_review.requested_by_agent_id != dispatch.agent_id
                ):
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority has "
                        f"invalid consuming review {consumed_review.id}"
                    )
                if (
                    dispatch_is_live
                    and reviewer is not None
                    and project is not None
                    and reviewer.team_id != project.team_id
                ):
                    errors.append(
                        f"dispatch {dispatch.id} review routing agent {reviewer.id} "
                        f"does not belong to project team {project.team_id}"
                    )

    def _validate_worktree_cleanup_candidates(self, errors: list[str]) -> None:
        for candidate in self.worktree_cleanup_candidates.values():
            if candidate.agent_id not in self.agents:
                errors.append(
                    f"worktree cleanup {candidate.id} references missing agent {candidate.agent_id}"
                )
            task = self.tasks.get(candidate.task_id)
            if task is None:
                errors.append(
                    f"worktree cleanup {candidate.id} references missing task {candidate.task_id}"
                )
            elif task.project_id != candidate.project_id:
                errors.append(
                    f"worktree cleanup {candidate.id} task belongs to project {task.project_id}"
                )
            project = self.projects.get(candidate.project_id)
            if project is None:
                errors.append(
                    f"worktree cleanup {candidate.id} references missing project "
                    f"{candidate.project_id}"
                )

    def _validate_git_preparation_attempts(self, errors: list[str]) -> None:
        for incident in self.git_recovery_incidents.values():
            task = self.tasks.get(incident.task_id)
            blocker = self.blockers.get(incident.blocker_id)
            if task is None or task.project_id != incident.project_id:
                errors.append(f"Git recovery {incident.id} has an invalid task/project scope")
            if blocker is None or blocker.project_id != incident.project_id:
                errors.append(f"Git recovery {incident.id} has an invalid blocker scope")
            for task_id in incident.recovery_task_ids:
                successor = self.tasks.get(task_id)
                if successor is None or successor.project_id != incident.project_id:
                    errors.append(f"Git recovery {incident.id} has an invalid successor {task_id}")
        for attempt in self.git_preparation_attempts.values():
            if attempt.agent_id not in self.agents:
                errors.append(
                    f"Git preparation attempt {attempt.id} references missing agent "
                    f"{attempt.agent_id}"
                )
            task = self.tasks.get(attempt.task_id)
            if task is None:
                errors.append(
                    f"Git preparation attempt {attempt.id} references missing task "
                    f"{attempt.task_id}"
                )
            elif task.project_id != attempt.project_id:
                errors.append(
                    f"Git preparation attempt {attempt.id} task belongs to project "
                    f"{task.project_id}"
                )
            project = self.projects.get(attempt.project_id)
            if project is None:
                errors.append(
                    f"Git preparation attempt {attempt.id} references missing project "
                    f"{attempt.project_id}"
                )
            if attempt.state == TeamGitPreparationAttemptState.COMMITTED:
                context = self.git_work_contexts.get(attempt.git_work_context_id or "")
                if context is None:
                    errors.append(
                        f"Git preparation attempt {attempt.id} references missing committed "
                        f"work context {attempt.git_work_context_id}"
                    )
                elif (
                    context.agent_id != attempt.agent_id
                    or context.task_id != attempt.task_id
                    or context.agent_branch != attempt.branch
                    or context.worktree_path != attempt.worktree_path
                ):
                    errors.append(
                        f"Git preparation attempt {attempt.id} committed context provenance "
                        "does not match"
                    )

    def _validate_ephemeral_assignments(self, errors: list[str]) -> None:
        from .model_queries import _agent_has_project_assignment

        for assignment in self.ephemeral_assignments.values():
            parent = self.agents.get(assignment.parent_agent_id)
            if parent is None:
                errors.append(
                    f"ephemeral assignment {assignment.id} references missing parent "
                    f"{assignment.parent_agent_id}"
                )
            project = self.projects.get(assignment.project_id)
            if project is None:
                errors.append(
                    f"ephemeral assignment {assignment.id} references missing project "
                    f"{assignment.project_id}"
                )
            task = self.tasks.get(assignment.task_id)
            if task is None:
                errors.append(
                    f"ephemeral assignment {assignment.id} references missing task "
                    f"{assignment.task_id}"
                )
            if parent is not None and not _agent_has_project_assignment(
                self, parent.id, assignment.project_id
            ):
                errors.append(
                    f"ephemeral assignment {assignment.id} parent {parent.id} "
                    f"is not assigned to project {assignment.project_id}"
                )
            if task is not None and task.project_id != assignment.project_id:
                errors.append(
                    f"ephemeral assignment {assignment.id} task {task.id} "
                    f"belongs to project {task.project_id}"
                )
            if task is not None and task.owner_agent_id != assignment.parent_agent_id:
                errors.append(
                    f"ephemeral assignment {assignment.id} task {task.id} "
                    f"is owned by {task.owner_agent_id}"
                )

    def _validate_git_promotions(self, errors: list[str]) -> None:
        for promotion in self.git_promotions.values():
            if promotion.project_id not in self.projects:
                errors.append(
                    f"git promotion {promotion.id} references missing project "
                    f"{promotion.project_id}"
                )
            if promotion.task_id is not None and promotion.task_id not in self.tasks:
                errors.append(
                    f"git promotion {promotion.id} references missing task {promotion.task_id}"
                )
            if (
                promotion.deliverable_id is not None
                and promotion.deliverable_id not in self.deliverables
            ):
                errors.append(
                    f"git promotion {promotion.id} references missing deliverable "
                    f"{promotion.deliverable_id}"
                )
            if (
                promotion.release_package_id is not None
                and promotion.release_package_id not in self.release_packages
            ):
                errors.append(
                    f"git promotion {promotion.id} references missing release "
                    f"{promotion.release_package_id}"
                )

    def _validate_stand_downs(self, errors: list[str]) -> None:
        for stand_down in self.stand_downs.values():
            if stand_down.agent_id not in self.agents:
                errors.append(
                    f"stand-down {stand_down.id} references missing agent {stand_down.agent_id}"
                )
            if stand_down.cleared_at is not None and stand_down.cleared_by is None:
                errors.append(f"stand-down {stand_down.id} has cleared_at without cleared_by")
