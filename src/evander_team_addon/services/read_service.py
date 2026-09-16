from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from ..models import (
    AgentInboxItem,
    AgentTask,
    Deliverable,
    PersistentAgent,
    Project,
    ReleasePackage,
    ReviewRequest,
    TeamCase,
    TeamBlocker,
    TeamBlockerState,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    agent_project_assignment_payloads,
    agent_lead_project_ids,
    agent_project_ids,
    effective_role_permissions,
    external_source_evidence_snapshot_payload,
    runtime_operations_payload,
)
from ..human_review_projection import release_package_api_payload
from .domain.project_status import project_blocking_sources
from .domain.outcome_catalog import effective_outcome_kind_payloads
from .domain.task_assessment import task_payload_with_assessment
from .wake_prompt_context import build_dispatch_summary

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50


class TeamReadService:
    """Read-only visible-scope queries for persistent Team agents."""

    def __init__(self, *, state: TeamState, actor: PersistentAgent) -> None:
        self._state = state
        self._actor = actor
        self._actor_id = actor.id
        self._managed_team_ids = self._managed_team_ids_for_actor()
        self._visible_team_ids = self._compute_visible_team_ids()
        self._visible_project_ids = self._compute_visible_project_ids()
        self._visible_agent_ids = self._compute_visible_agent_ids()

    def list_tasks(
        self,
        *,
        project_id: str | None = None,
        owner_agent_id: str | None = None,
        state: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        tasks = [
            task
            for task in self._state.tasks.values()
            if self._task_visible(task)
            and _matches(project_id, task.project_id)
            and _matches(owner_agent_id, task.owner_agent_id)
            and _matches_enum(state, task.state)
        ]
        rows = [self._task_row(task) for task in sorted(tasks, key=_task_sort_key)]
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def get_task(
        self,
        task_id: str,
        *,
        dispatch_id: str | None = None,
    ) -> dict[str, Any] | None:
        task = self._state.tasks.get(_text(task_id))
        if task is None or not self._task_visible(task):
            return None
        payload = task_payload_with_assessment(self._state, task)
        git_contexts = self._git_work_context_rows_for_task(
            task.id,
            dispatch_id=dispatch_id,
        )
        if git_contexts:
            payload["git_work_contexts"] = git_contexts
        return _without_none(payload)

    def list_inbox(
        self,
        *,
        state: str | None = None,
        include_handled: bool = False,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        items = [
            item
            for item in self._state.inbox.values()
            if self._inbox_visible(item)
            and (include_handled or item.state == TeamInboxItemState.PENDING)
            and _matches_enum(state, item.state)
        ]
        rows = [self._inbox_row(item) for item in sorted(items, key=_inbox_sort_key)]
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def get_inbox_item(self, inbox_item_id: str) -> dict[str, Any] | None:
        item = self._state.inbox.get(_text(inbox_item_id))
        if item is None or not self._inbox_visible(item):
            return None
        return _without_none(item.model_dump(mode="json"))

    def list_blockers(
        self,
        *,
        team_id: str | None = None,
        project_id: str | None = None,
        owner_agent_id: str | None = None,
        delegate_agent_id: str | None = None,
        state: str | None = None,
        source_kind: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        blockers = [
            blocker
            for blocker in self._state.blockers.values()
            if self._blocker_visible(blocker)
            and _matches(team_id, blocker.team_id)
            and _matches(project_id, blocker.project_id)
            and _matches(owner_agent_id, blocker.owner_agent_id)
            and _matches(delegate_agent_id, blocker.delegate_agent_id)
            and _matches_enum(state, blocker.state)
            and _matches_enum(source_kind, blocker.source_kind)
        ]
        rows = [self._blocker_row(blocker) for blocker in sorted(blockers, key=_blocker_sort_key)]
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def get_blocker(self, blocker_id: str) -> dict[str, Any] | None:
        blocker = self._state.blockers.get(_text(blocker_id))
        if blocker is None or not self._blocker_visible(blocker):
            return None
        payload = blocker.model_dump(mode="json")
        payload["events"] = [
            event.model_dump(mode="json")
            for event in sorted(
                (
                    event
                    for event in self._state.blocker_events.values()
                    if event.blocker_id == blocker.id
                ),
                key=lambda event: (event.created_at, event.id),
            )
        ]
        recoveries = sorted(
            (
                recovery
                for recovery in self._state.dependency_composition_recoveries.values()
                if recovery.blocker_id == blocker.id
            ),
            key=lambda recovery: (recovery.created_at, recovery.id),
        )
        if recoveries:
            payload["dependency_composition_recovery"] = recoveries[-1].model_dump(mode="json")
        return _without_none(payload)

    def list_cases(
        self,
        *,
        project_id: str | None = None,
        owner_agent_id: str | None = None,
        status: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        cases = [
            case
            for case in self._state.cases.values()
            if self._case_visible(case)
            and _matches(project_id, case.project_id)
            and _matches(owner_agent_id, case.owner_agent_id)
            and _matches_enum(status, case.status)
        ]
        rows = [self._case_row(case) for case in sorted(cases, key=_case_sort_key)]
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        case = self._state.cases.get(_text(case_id))
        if case is None or not self._case_visible(case):
            return None
        return _without_none(case.model_dump(mode="json"))

    def list_deliverables(
        self,
        *,
        task_id: str | None = None,
        producer_agent_id: str | None = None,
        state: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        deliverables = [
            deliverable
            for deliverable in self._state.deliverables.values()
            if self._deliverable_visible(deliverable)
            and _matches(task_id, deliverable.task_id)
            and _matches(producer_agent_id, deliverable.producer_agent_id)
            and _matches_enum(state, deliverable.state)
        ]
        rows = [
            self._deliverable_row(deliverable)
            for deliverable in sorted(deliverables, key=_deliverable_sort_key)
        ]
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def get_deliverable(self, deliverable_id: str) -> dict[str, Any] | None:
        deliverable = self._state.deliverables.get(_text(deliverable_id))
        if deliverable is None or not self._deliverable_visible(deliverable):
            return None
        return _without_none(deliverable.model_dump(mode="json"))

    def list_review_requests(
        self,
        *,
        deliverable_id: str | None = None,
        reviewer_agent_id: str | None = None,
        state: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        requests = [
            request
            for request in self._state.review_requests.values()
            if self._review_request_visible(request)
            and _matches(deliverable_id, request.deliverable_id)
            and _matches(reviewer_agent_id, request.reviewer_agent_id)
            and _matches_enum(state, request.state)
        ]
        rows = [
            self._review_request_row(request)
            for request in sorted(requests, key=_review_request_sort_key)
        ]
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def get_review_request(self, review_request_id: str) -> dict[str, Any] | None:
        request = self._state.review_requests.get(_text(review_request_id))
        if request is None or not self._review_request_visible(request):
            return None
        return _without_none(request.model_dump(mode="json"))

    def list_release_packages(
        self,
        *,
        project_id: str | None = None,
        state: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        releases = [
            release
            for release in self._state.release_packages.values()
            if self._release_visible(release)
            and _matches(project_id, release.project_id)
            and _matches_enum(state, release.state)
        ]
        rows = [self._release_row(release) for release in sorted(releases, key=_release_sort_key)]
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def get_release_package(self, release_package_id: str) -> dict[str, Any] | None:
        release = self._state.release_packages.get(_text(release_package_id))
        if release is None or not self._release_visible(release):
            return None
        return _without_none(
            release_package_api_payload(
                self._state,
                release,
                include_human_details=False,
            )
        )

    def list_agents(
        self,
        *,
        team_id: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        agents = [
            agent
            for agent in self._state.agents.values()
            if self._agent_visible(agent.id)
            and _matches(team_id, agent.team_id)
            and _matches_enum(status, agent.status)
            and (
                project_id is None
                or project_id in set(agent_project_ids(self._state, agent.id))
                or project_id in set(agent_lead_project_ids(self._state, agent.id))
            )
        ]
        rows = [self._agent_row(agent) for agent in sorted(agents, key=lambda item: item.id)]
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        agent = self._state.agents.get(_text(agent_id))
        if agent is None or not self._agent_visible(agent.id):
            return None
        return self._agent_detail_payload(agent, full_identity=agent.id == self._actor_id)

    def get_my_identity(self) -> dict[str, Any]:
        return self._agent_detail_payload(self._actor, full_identity=True)

    def get_current_dispatch_context(
        self,
        *,
        detail: Literal["summary", "full"] = "full",
    ) -> dict[str, Any] | None:
        """Hydrate the caller's active immutable dispatch scope on demand."""

        if detail not in {"summary", "full"}:
            raise ValueError("detail must be summary or full")
        runtime = self._state.agent_runtimes.get(self._actor_id)
        dispatch_id = runtime.active_dispatch_id if runtime is not None else None
        if dispatch_id is None:
            return None
        dispatch = self._state.dispatches.get(dispatch_id)
        if dispatch is None or dispatch.agent_id != self._actor_id:
            return None

        tasks = [
            payload
            for task_id in dispatch.task_ids
            if (
                payload := self.get_task(
                    task_id,
                    dispatch_id=dispatch.id,
                )
            )
            is not None
        ]
        blockers = [
            payload
            for blocker_id in dispatch.blocker_ids
            if (payload := self.get_blocker(blocker_id)) is not None
        ]
        reviews = [
            payload
            for review_id in dispatch.review_request_ids
            if (payload := self.get_review_request(review_id)) is not None
        ]
        inbox = [
            payload
            for inbox_item_id in dispatch.inbox_item_ids
            if (payload := self.get_inbox_item(inbox_item_id)) is not None
        ]
        projects = [
            payload
            for project_id in dispatch.project_ids
            if (payload := self.get_project(project_id)) is not None
        ]
        from .case_context_dispatch import dispatch_case_contexts

        payload = _without_none(
            {
                "dispatch": dispatch.model_dump(mode="json"),
                "tasks": tasks,
                "blockers": blockers,
                "reviews": reviews,
                "inbox": inbox,
                "projects": projects,
                "case_contexts": dispatch_case_contexts(self._state, dispatch.id),
                "external_source_evidence_snapshots": [
                    external_source_evidence_snapshot_payload(
                        self._state.external_source_evidence_snapshots[snapshot_id]
                    )
                    for snapshot_id in dispatch.source_evidence_snapshot_ids
                    if snapshot_id in self._state.external_source_evidence_snapshots
                ],
                "git_work_contexts": [
                    row
                    for task_id in dispatch.task_ids
                    for row in self._git_work_context_rows_for_task(
                        task_id,
                        dispatch_id=dispatch.id,
                    )
                ],
            }
        )
        return build_dispatch_summary(payload) if detail == "summary" else payload

    def get_source_evidence_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        """Read one immutable capture from the caller's current frozen scope."""

        snapshot_id = _text(snapshot_id)
        runtime = self._state.agent_runtimes.get(self._actor_id)
        dispatch = self._state.dispatches.get(runtime.active_dispatch_id) if runtime else None
        if (
            dispatch is None
            or dispatch.agent_id != self._actor_id
            or snapshot_id not in dispatch.source_evidence_snapshot_ids
        ):
            return None
        snapshot = self._state.external_source_evidence_snapshots.get(snapshot_id)
        return external_source_evidence_snapshot_payload(snapshot) if snapshot else None

    def supervisory_overview(self) -> dict[str, Any]:
        """Current cross-lineage awareness within existing read visibility only."""

        if not (
            self._managed_team_ids
            or self._subordinate_agent_ids()
            or agent_lead_project_ids(self._state, self._actor_id)
        ):
            return {}
        projects = []
        for project_id in sorted(self._visible_project_ids):
            project = self._state.projects.get(project_id)
            if project is None:
                continue
            projects.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "status": project.status.value,
                    "task_counts": _state_counts(
                        task.state.value
                        for task in self._state.tasks.values()
                        if task.project_id == project.id and self._task_visible(task)
                    ),
                    "open_blocker_count": sum(
                        blocker.project_id == project.id
                        and blocker.state == TeamBlockerState.OPEN
                        and self._blocker_visible(blocker)
                        for blocker in self._state.blockers.values()
                    ),
                    "release_counts": _state_counts(
                        release.state.value
                        for release in self._state.release_packages.values()
                        if release.project_id == project.id and self._release_visible(release)
                    ),
                }
            )
        return {
            "read_only": True,
            "execution_scope": "current_dispatch",
            "project_count": len(projects),
            "projects": projects[:8],
            "omitted_project_count": max(0, len(projects) - 8),
            "retrieval": {
                "projects": {"tool": "team_list_projects", "paginate": True},
                "tasks": {"tool": "team_list_tasks", "paginate": True},
                "blockers": {"tool": "team_list_blockers", "paginate": True},
                "reviews": {"tool": "team_list_review_requests", "paginate": True},
                "releases": {"tool": "team_list_release_packages", "paginate": True},
            },
        }

    def list_projects(
        self,
        *,
        team_id: str | None = None,
        status: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        projects = [
            project
            for project in self._state.projects.values()
            if self._project_visible(project.id)
            and _matches(team_id, project.team_id)
            and _matches_enum(status, project.status)
        ]
        rows = [
            self._project_row(project) for project in sorted(projects, key=lambda item: item.id)
        ]
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        project = self._state.projects.get(_text(project_id))
        if project is None or not self._project_visible(project.id):
            return None
        payload = project.model_dump(mode="json")
        payload["agent_ids"] = sorted(
            assignment.agent_id
            for assignment in self._state.project_assignments.values()
            if assignment.project_id == project.id and self._agent_visible(assignment.agent_id)
        )
        payload["task_counts"] = _state_counts(
            task.state.value
            for task in self._state.tasks.values()
            if task.project_id == project.id and self._task_visible(task)
        )
        payload["blocking_sources"] = project_blocking_sources(
            self._state,
            project.id,
        )
        return _without_none(payload)

    def list_outcome_kinds(
        self,
        *,
        project_id: str | None = None,
        team_id: str | None = None,
        include_archived: bool = False,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        normalized_project_id = _maybe_text(project_id)
        normalized_team_id = _maybe_text(team_id)
        if normalized_project_id is not None:
            project = self._state.projects.get(normalized_project_id)
            if project is None or not self._project_visible(project.id):
                return _page([], limit=limit, cursor=cursor, offset=offset)
            rows = effective_outcome_kind_payloads(
                self._state,
                team_id=project.team_id,
                project_id=project.id,
                include_archived=include_archived,
            )
        else:
            effective_team_id = normalized_team_id or self._actor.team_id
            if effective_team_id is None or effective_team_id not in self._visible_team_ids:
                return _page([], limit=limit, cursor=cursor, offset=offset)
            rows = effective_outcome_kind_payloads(
                self._state,
                team_id=effective_team_id,
                project_id=None,
                include_archived=include_archived,
            )
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def get_outcome_kind(self, definition_id: str) -> dict[str, Any] | None:
        definition = self._state.outcome_kind_definitions.get(_text(definition_id))
        if definition is None or definition.team_id not in self._visible_team_ids:
            return None
        if definition.project_id is not None and not self._project_visible(definition.project_id):
            return None
        return _without_none(definition.model_dump(mode="json"))

    def list_runtime_attention(
        self,
        *,
        kind: str | None = None,
        severity: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        attention = runtime_operations_payload(self._state).get("attention") or []
        rows = sorted(
            (
                self._runtime_attention_row(row)
                for row in attention
                if isinstance(row, Mapping)
                and self._runtime_attention_visible(row)
                and _matches(kind, row.get("kind"))
                and _matches(severity, row.get("severity"))
            ),
            key=_runtime_attention_read_sort_key,
        )
        return _page(rows, limit=limit, cursor=cursor, offset=offset)

    def _managed_team_ids_for_actor(self) -> set[str]:
        managed = set()
        for team in self._state.teams.values():
            if team.manager_agent_id == self._actor_id:
                managed.add(team.id)
        return managed

    def _compute_visible_team_ids(self) -> set[str]:
        visible = set(self._managed_team_ids)
        if self._actor.team_id is not None:
            visible.add(self._actor.team_id)
        return visible

    def _compute_visible_project_ids(self) -> set[str]:
        visible = set(agent_project_ids(self._state, self._actor_id))
        visible.update(agent_lead_project_ids(self._state, self._actor_id))
        if self._actor.role_id is not None:
            role = self._state.roles.get(self._actor.role_id)
            if role is not None and role.project_id is not None:
                visible.add(role.project_id)
        for project in self._state.projects.values():
            if project.lead_agent_id == self._actor_id:
                visible.add(project.id)
            if project.team_id in self._managed_team_ids:
                visible.add(project.id)
        for subordinate_id in self._subordinate_agent_ids():
            visible.update(agent_project_ids(self._state, subordinate_id))
            visible.update(agent_lead_project_ids(self._state, subordinate_id))
        return visible

    def _compute_visible_agent_ids(self) -> set[str]:
        visible = {self._actor_id}
        for agent in self._state.agents.values():
            if agent.team_id in self._visible_team_ids:
                visible.add(agent.id)
        visible.update(self._governance_superior_agent_ids())
        return visible

    def _governance_superior_agent_ids(self) -> set[str]:
        superior_id = self._actor.reports_to_agent_id
        if superior_id is None or not self._managed_team_ids:
            return set()
        for group in self._state.governance_groups.values():
            if group.archived or group.manager_agent_id != superior_id:
                continue
            if set(group.member_team_ids).intersection(self._managed_team_ids):
                return {superior_id}
        return set()

    def _subordinate_agent_ids(self) -> set[str]:
        result: set[str] = set()
        stack = list(self._actor.subordinate_agent_ids)
        while stack:
            agent_id = stack.pop()
            if agent_id in result:
                continue
            result.add(agent_id)
            agent = self._state.agents.get(agent_id)
            if agent is not None:
                stack.extend(agent.subordinate_agent_ids)
        for agent in self._state.agents.values():
            if agent.id != self._actor_id and self._reports_under(agent.id, self._actor_id):
                result.add(agent.id)
        return result

    def _reports_under(self, agent_id: str, ancestor_id: str) -> bool:
        seen: set[str] = set()
        current = self._state.agents.get(agent_id)
        while current is not None and current.reports_to_agent_id is not None:
            parent_id = current.reports_to_agent_id
            if parent_id == ancestor_id:
                return True
            if parent_id in seen:
                return False
            seen.add(parent_id)
            current = self._state.agents.get(parent_id)
        return False

    def _agent_visible(self, agent_id: str | None) -> bool:
        return agent_id is not None and agent_id in self._visible_agent_ids

    def _project_visible(self, project_id: str | None) -> bool:
        return project_id is not None and project_id in self._visible_project_ids

    def _task_visible(self, task: AgentTask) -> bool:
        return (
            self._project_visible(task.project_id)
            or self._agent_visible(task.owner_agent_id)
            or self._agent_visible(task.created_by_agent_id)
        )

    def _case_visible(self, case: TeamCase) -> bool:
        return (
            self._project_visible(case.project_id)
            or self._agent_visible(case.owner_agent_id)
            or case.team_id in self._managed_team_ids
            or any(
                self._task_visible(task)
                for task_id in case.task_ids
                if (task := self._state.tasks.get(task_id)) is not None
            )
        )

    def _inbox_visible(self, item: AgentInboxItem) -> bool:
        task = self._state.tasks.get(item.task_id) if item.task_id is not None else None
        return (
            self._agent_visible(item.recipient_agent_id)
            or self._agent_visible(item.sender_agent_id)
            or (task is not None and self._task_visible(task))
        )

    def _blocker_visible(self, blocker: TeamBlocker) -> bool:
        if blocker.team_id in self._managed_team_ids:
            return True
        if blocker.project_id is not None and self._project_visible(blocker.project_id):
            return True
        if blocker.task_id is not None:
            task = self._state.tasks.get(blocker.task_id)
            if task is not None and self._task_visible(task):
                return True
        return any(
            self._agent_visible(agent_id)
            for agent_id in (
                blocker.reporter_agent_id,
                blocker.owner_agent_id,
                blocker.delegate_agent_id,
            )
        )

    def _deliverable_visible(self, deliverable: Deliverable) -> bool:
        task = self._state.tasks.get(deliverable.task_id)
        return self._agent_visible(deliverable.producer_agent_id) or (
            task is not None and self._task_visible(task)
        )

    def _review_request_visible(self, request: ReviewRequest) -> bool:
        deliverable = self._state.deliverables.get(request.deliverable_id)
        release = self._state.release_packages.get(request.release_package_id)
        return (
            self._agent_visible(request.reviewer_agent_id)
            or (deliverable is not None and self._deliverable_visible(deliverable))
            or (release is not None and self._release_visible(release))
        )

    def _release_visible(self, release: ReleasePackage) -> bool:
        return self._project_visible(release.project_id)

    def _runtime_attention_visible(self, row: Mapping[str, Any]) -> bool:
        agent_id = _maybe_text(row.get("agent_id"))
        if agent_id is not None:
            # The row's durable owner is authoritative for scope. Derived links
            # may quote payload IDs, but must never broaden who can read it.
            return self._agent_visible(agent_id)
        team_id = _maybe_text(row.get("team_id"))
        if team_id is not None:
            return team_id in self._visible_team_ids
        if self._project_visible(_maybe_text(row.get("project_id"))):
            return True
        project_ids = row.get("project_ids")
        if isinstance(project_ids, Sequence) and not isinstance(project_ids, (str, bytes)):
            if any(self._project_visible(_maybe_text(project_id)) for project_id in project_ids):
                return True
        task_id = _maybe_text(row.get("task_id"))
        if task_id is not None and (task := self._state.tasks.get(task_id)) is not None:
            return self._task_visible(task)
        deliverable_id = _maybe_text(row.get("deliverable_id"))
        if deliverable_id is not None:
            deliverable = self._state.deliverables.get(deliverable_id)
            if deliverable is not None and self._deliverable_visible(deliverable):
                return True
        inbox_item_id = _maybe_text(row.get("inbox_item_id"))
        if inbox_item_id is not None:
            item = self._state.inbox.get(inbox_item_id)
            if item is not None and self._inbox_visible(item):
                return True
        release_id = _maybe_text(row.get("release_package_id"))
        if release_id is not None:
            release = self._state.release_packages.get(release_id)
            if release is not None and self._release_visible(release):
                return True
        return False

    def _runtime_attention_link_visible(self, kind: str, link_id: str) -> bool:
        if kind == "team_ids":
            return link_id in self._visible_team_ids
        if kind == "project_ids":
            return self._project_visible(link_id)
        if kind == "agent_ids":
            return self._agent_visible(link_id)
        if kind == "task_ids":
            task = self._state.tasks.get(link_id)
            return task is not None and self._task_visible(task)
        if kind == "deliverable_ids":
            deliverable = self._state.deliverables.get(link_id)
            return deliverable is not None and self._deliverable_visible(deliverable)
        if kind == "release_package_ids":
            release = self._state.release_packages.get(link_id)
            return release is not None and self._release_visible(release)
        if kind == "review_request_ids":
            request = self._state.review_requests.get(link_id)
            return request is not None and self._review_request_visible(request)
        if kind == "blocker_ids":
            blocker = self._state.blockers.get(link_id)
            return blocker is not None and self._blocker_visible(blocker)
        if kind == "inbox_item_ids":
            item = self._state.inbox.get(link_id)
            return item is not None and self._inbox_visible(item)
        if kind == "dispatch_ids":
            dispatch = self._state.dispatches.get(link_id)
            return dispatch is not None and (
                self._agent_visible(dispatch.agent_id)
                or any(self._project_visible(project_id) for project_id in dispatch.project_ids)
            )
        if kind == "assignment_ids":
            assignment = self._state.ephemeral_assignments.get(link_id)
            return assignment is not None and (
                self._agent_visible(assignment.parent_agent_id)
                or self._project_visible(assignment.project_id)
            )
        if kind == "promotion_ids":
            promotion = self._state.git_promotions.get(link_id)
            return promotion is not None and (
                self._agent_visible(promotion.actor_agent_id)
                or self._project_visible(promotion.project_id)
            )
        if kind == "session_ids":
            # Agent detail reads expose a session only for the actor's own
            # identity; attention-link expansion must keep the same boundary.
            return self._actor.session_id == link_id
        return False

    def _runtime_attention_primary_target(
        self,
        row: Mapping[str, Any],
    ) -> tuple[str, str] | None:
        inbox_id = _maybe_text(row.get("inbox_item_id"))
        item = self._state.inbox.get(inbox_id or "")
        if item is None:
            return None
        for field, kind in (
            ("review_request_id", "review_request"),
            ("release_package_id", "release_package"),
            ("deliverable_id", "deliverable"),
            ("task_id", "task"),
            ("dispatch_id", "dispatch"),
            ("session_id", "session"),
        ):
            target_id = _maybe_text(item.payload.get(field))
            if target_id is not None:
                return kind, target_id
        if item.task_id is not None:
            return "task", item.task_id
        return None

    def _git_work_context_rows_for_task(
        self,
        task_id: str,
        *,
        dispatch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for context in self._state.git_work_contexts.values():
            if context.task_id != task_id:
                continue
            if context.agent_id != self._actor_id:
                continue
            if dispatch_id is not None and context.dispatch_id != dispatch_id:
                continue
            if _enum_value(context.state) != "active":
                continue
            rows.append(
                _without_none(
                    {
                        "id": context.id,
                        "branch_context_id": context.id,
                        "agent_id": context.agent_id,
                        "task_id": context.task_id,
                        "agent_branch": context.agent_branch,
                        "base_branch": context.base_branch,
                        "base_commit": context.base_commit,
                        "commit_sha": context.commit_sha,
                        "dispatch_id": context.dispatch_id,
                        "worktree_path": context.worktree_path,
                        "state": _enum_value(context.state),
                    }
                )
            )
        return sorted(rows, key=lambda row: (_maybe_text(row.get("task_id")) or "", row["id"]))

    def _task_row(self, task: AgentTask) -> dict[str, Any]:
        derived = task_payload_with_assessment(self._state, task)
        child_ids = _capped_list(derived.get("child_task_ids") or [], 12)
        row: dict[str, Any] = {
            "id": task.id,
            "title": _compact_text(task.title, 180),
            "description_preview": _compact_text(task.description, 240),
            "state": task.state.value,
            "priority": task.priority,
            "owner_agent_id": task.owner_agent_id,
            "project_id": task.project_id,
            "authored_by": task.authored_by.model_dump(mode="json") if task.authored_by else None,
            "work_item_attributions": [
                item.model_dump(mode="json") for item in task.work_item_attributions
            ]
            if task.work_item_attributions
            else None,
            "git_base_branch": task.git_base_branch,
            "git_base_commit_sha": task.git_base_commit_sha,
            "case_id": task.case_id,
            "dependency_status": derived.get("dependency_status"),
            "resolution_status": derived.get("resolution_status"),
            "readiness": derived.get("readiness"),
            "integration_ready": derived.get("integration_ready"),
            "assessment": derived.get("assessment"),
            "blocked_by_task_ids": derived.get("blocked_by_task_ids") or [],
            "child_task_ids": child_ids["items"],
            "child_task_omitted_count": child_ids["omitted_count"],
            "subtask_counts": derived.get("subtask_counts") or {},
            "deliverable_count": len(task.deliverable_ids),
            "updated_at": _json_value(task.updated_at),
        }
        git_contexts = self._git_work_context_rows_for_task(task.id)
        if git_contexts:
            row["git_work_contexts"] = git_contexts
        return _without_none(row)

    def _inbox_row(self, item: AgentInboxItem) -> dict[str, Any]:
        return _without_none(
            {
                "id": item.id,
                "item_type": item.item_type.value,
                "sender_agent_id": item.sender_agent_id,
                "recipient_agent_id": item.recipient_agent_id,
                "task_id": item.task_id,
                "priority": item.priority.value,
                "response_requested": item.response_requested,
                "state": item.state.value,
                "title": _compact_text(_first_text(item.payload, ("title", "subject")), 180),
                "message": _compact_text(
                    _first_text(item.payload, ("message", "question", "summary", "description")),
                    220,
                ),
                "created_at": _json_value(item.created_at),
                "updated_at": _json_value(item.updated_at),
            }
        )

    def _blocker_row(self, blocker: TeamBlocker) -> dict[str, Any]:
        event_count = sum(
            1 for event in self._state.blocker_events.values() if event.blocker_id == blocker.id
        )
        recovery = next(
            (
                item
                for item in sorted(
                    self._state.dependency_composition_recoveries.values(),
                    key=lambda item: (item.created_at, item.id),
                    reverse=True,
                )
                if item.blocker_id == blocker.id
            ),
            None,
        )
        return _without_none(
            {
                "id": blocker.id,
                "team_id": blocker.team_id,
                "project_id": blocker.project_id,
                "task_id": blocker.task_id,
                "source_kind": blocker.source_kind.value,
                "source_id": blocker.source_id,
                "resolution_scope": blocker.resolution_scope.value,
                "summary": _compact_text(blocker.summary, 180),
                "details": _compact_text(blocker.details, 240),
                "priority": blocker.priority.value,
                "state": blocker.state.value,
                "route": blocker.route.value,
                "reporter_agent_id": blocker.reporter_agent_id,
                "owner_agent_id": blocker.owner_agent_id,
                "delegate_agent_id": blocker.delegate_agent_id,
                "escalation_depth": blocker.escalation_depth,
                "active_dispatch_id": blocker.active_dispatch_id,
                "follow_up_at": _json_value(blocker.follow_up_at),
                "last_progress_at": _json_value(blocker.last_progress_at),
                "event_count": event_count,
                "dependency_composition_recovery_id": (
                    recovery.id if recovery is not None else None
                ),
                "dependency_composition_recovery_state": (
                    recovery.state.value if recovery is not None else None
                ),
                "dependency_composition_recovery_task_id": (
                    recovery.recovery_task_id if recovery is not None else None
                ),
                "created_at": _json_value(blocker.created_at),
                "updated_at": _json_value(blocker.updated_at),
                "resolved_at": _json_value(blocker.resolved_at),
            }
        )

    def _case_row(self, case: TeamCase) -> dict[str, Any]:
        return _without_none(
            {
                "id": case.id,
                "title": _compact_text(case.title, 180),
                "status": case.status.value,
                "priority": case.priority.value,
                "owner_agent_id": case.owner_agent_id,
                "project_id": case.project_id,
                "task_ids": case.task_ids,
                "updated_at": _json_value(case.updated_at),
            }
        )

    def _deliverable_row(self, deliverable: Deliverable) -> dict[str, Any]:
        git_refs = {
            key: value
            for key, value in deliverable.git_refs.model_dump(mode="json").items()
            if value
        }
        return _without_none(
            {
                "id": deliverable.id,
                "task_id": deliverable.task_id,
                "producer_agent_id": deliverable.producer_agent_id,
                "state": deliverable.state.value,
                "summary": _compact_text(deliverable.summary, 240),
                "review_request_ids": deliverable.review_request_ids,
                "artifact_ref_count": len(deliverable.artifact_refs),
                "git_refs": git_refs,
                "verification_status": (
                    deliverable.verification.status
                    if deliverable.verification is not None
                    else None
                ),
                "initial_release_disposition": (
                    deliverable.initial_release_disposition.model_dump(mode="json")
                    if deliverable.initial_release_disposition is not None
                    else None
                ),
                "created_at": _json_value(deliverable.created_at),
            }
        )

    def _review_request_row(self, request: ReviewRequest) -> dict[str, Any]:
        return _without_none(
            {
                "id": request.id,
                "deliverable_id": request.deliverable_id,
                "reviewer_agent_id": request.reviewer_agent_id,
                "state": request.state.value,
                "review_profile_id": request.review_profile_id,
                "instructions": _compact_text(request.instructions, 240),
                "findings_count": len(request.findings),
                "created_at": _json_value(request.created_at),
                "decision_at": _json_value(request.decision_at),
            }
        )

    def _release_row(self, release: ReleasePackage) -> dict[str, Any]:
        payload = release_package_api_payload(
            self._state,
            release,
            include_human_details=False,
        )
        return _without_none(
            {
                "id": release.id,
                "project_id": release.project_id,
                "state": release.state.value,
                "summary": _compact_text(release.summary, 360),
                "release_branch": release.release_branch,
                "canonical_branch": release.canonical_branch,
                "published_commit_sha": release.published_commit_sha,
                "deliverable_count": len(release.deliverable_ids),
                "feature_branch_count": len(release.feature_branches),
                "next_action": payload.get("next_action"),
                "next_action_hint": _compact_text(payload.get("next_action_hint"), 360),
                "human_review": payload.get("human_review"),
                "error": _compact_text(release.error, 240),
                "updated_at": _json_value(release.updated_at),
                "created_at": _json_value(release.created_at),
            }
        )

    def _agent_row(self, agent: PersistentAgent) -> dict[str, Any]:
        role = self._state.roles.get(agent.role_id) if agent.role_id is not None else None
        project_ids = agent_project_ids(self._state, agent.id)
        lead_project_ids = agent_lead_project_ids(self._state, agent.id)
        return _without_none(
            {
                "id": agent.id,
                "display_name": agent.display_name,
                "team_id": agent.team_id,
                "role_id": agent.role_id,
                "role_name": role.name if role is not None else None,
                "status": agent.status.value,
                "reports_to_agent_id": agent.reports_to_agent_id,
                "subordinate_agent_ids": [
                    agent_id
                    for agent_id in agent.subordinate_agent_ids
                    if self._agent_visible(agent_id)
                ],
                "project_ids": [
                    project_id for project_id in project_ids if self._project_visible(project_id)
                ],
                "lead_project_ids": [
                    project_id
                    for project_id in lead_project_ids
                    if self._project_visible(project_id)
                ],
            }
        )

    def _agent_detail_payload(
        self,
        agent: PersistentAgent,
        *,
        full_identity: bool,
    ) -> dict[str, Any]:
        role = self._state.roles.get(agent.role_id) if agent.role_id is not None else None
        project_assignments = self._agent_project_assignment_payloads(
            agent,
            full_identity=full_identity,
        )
        effective_permissions = self._agent_effective_permission_values(
            agent,
            role=role,
            project_assignments=project_assignments,
            full_identity=full_identity,
        )
        payload = self._agent_row(agent)
        payload.update(
            {
                "session_id": agent.session_id if full_identity else None,
                "role_description": role.description if role is not None else "",
                "role_permissions": self._agent_role_permission_values(
                    role,
                    full_identity=full_identity,
                ),
                "effective_role_permissions": effective_permissions,
                "ephemeral_policy": agent.ephemeral_policy.model_dump(mode="json"),
                "superior": self._agent_reference(
                    agent.reports_to_agent_id,
                    allow_direct_superior=full_identity and agent.id == self._actor_id,
                ),
                "subordinates": [
                    reference
                    for subordinate_id in agent.subordinate_agent_ids
                    if (reference := self._agent_reference(subordinate_id)) is not None
                ],
                "project_assignments": project_assignments,
            }
        )
        return _without_none(payload)

    def _agent_project_assignment_payloads(
        self,
        agent: PersistentAgent,
        *,
        full_identity: bool,
    ) -> list[dict[str, Any]]:
        assignments = sorted(
            agent_project_assignment_payloads(self._state, agent.id),
            key=lambda item: (
                _maybe_text(item.get("project_id")) or "",
                _maybe_text(item.get("kind")) or "",
                _maybe_text(item.get("id")) or "",
            ),
        )
        if full_identity:
            return assignments
        return [
            assignment
            for assignment in assignments
            if _maybe_text(assignment.get("team_id")) in self._visible_team_ids
        ]

    def _agent_effective_permission_values(
        self,
        agent: PersistentAgent,
        *,
        role: Any,
        project_assignments: list[dict[str, Any]],
        full_identity: bool,
    ) -> list[str]:
        if full_identity:
            return [
                permission.value for permission in effective_role_permissions(self._state, agent)
            ]
        values: list[str] = []

        def add(items: object) -> None:
            for item in _sequence(items):
                text = _maybe_text(getattr(item, "value", item))
                if text is not None and text not in values:
                    values.append(text)

        if role is not None and role.team_id in self._visible_team_ids:
            add(role.permissions)
        for assignment in project_assignments:
            add(assignment.get("role_permissions"))
        return values

    def _agent_role_permission_values(self, role: Any, *, full_identity: bool) -> list[str]:
        if role is None or (not full_identity and role.team_id not in self._visible_team_ids):
            return []
        return [permission.value for permission in role.permissions]

    def _agent_reference(
        self,
        agent_id: str | None,
        *,
        allow_direct_superior: bool = False,
    ) -> dict[str, Any] | None:
        if not self._agent_visible(agent_id) and not (
            allow_direct_superior and agent_id == self._actor.reports_to_agent_id
        ):
            return None
        agent = self._state.agents.get(agent_id or "")
        if agent is None:
            return None
        role = self._state.roles.get(agent.role_id) if agent.role_id is not None else None
        return _without_none(
            {
                "id": agent.id,
                "display_name": agent.display_name,
                "team_id": agent.team_id,
                "role_id": agent.role_id,
                "role_name": role.name if role is not None else None,
                "status": agent.status.value,
            }
        )

    def _project_row(self, project: Project) -> dict[str, Any]:
        return _without_none(
            {
                "id": project.id,
                "team_id": project.team_id,
                "name": project.name,
                "lead_agent_id": project.lead_agent_id,
                "status": project.status.value,
                "blocking_source_count": len(project_blocking_sources(self._state, project.id)),
                "goal_count": len(project.goals),
                "has_git": project.git is not None,
                "updated_at": _json_value(project.updated_at),
            }
        )

    def _runtime_attention_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        raw_links = row.get("links")
        raw_session_ids = {
            session_id
            for value in (
                _sequence(raw_links.get("session_ids")) if isinstance(raw_links, Mapping) else []
            )
            if (session_id := _maybe_text(value)) is not None
        }
        if direct_session_id := _maybe_text(row.get("session_id")):
            raw_session_ids.add(direct_session_id)
        hidden_session_ids = {
            session_id
            for session_id in raw_session_ids
            if not self._runtime_attention_link_visible("session_ids", session_id)
        }
        links = (
            {
                str(kind): [
                    link_id
                    for value in _sequence(values)
                    if (link_id := _maybe_text(value)) is not None
                    and self._runtime_attention_link_visible(str(kind), link_id)
                ]
                for kind, values in raw_links.items()
                if str(kind) in _RUNTIME_ATTENTION_LINK_FIELDS
            }
            if isinstance(raw_links, Mapping)
            else {}
        )
        links = {kind: values for kind, values in links.items() if values}
        terminal_context = []
        for context in _sequence(row.get("terminal_context")):
            if not isinstance(context, Mapping):
                continue
            context_kind = _maybe_text(context.get("kind"))
            context_id = _maybe_text(context.get("id"))
            link_kind = _RUNTIME_ATTENTION_CONTEXT_LINK_FIELDS.get(context_kind or "")
            if (
                context_kind is None
                or context_id is None
                or link_kind is None
                or not self._runtime_attention_link_visible(link_kind, context_id)
            ):
                continue
            terminal_context.append(
                _without_none(
                    {
                        "kind": context_kind,
                        "id": context_id,
                        "state": _maybe_text(context.get("state")),
                    }
                )
            )
        reconciliation_required = bool(row.get("reconciliation_required"))
        if reconciliation_required:
            primary_target = self._runtime_attention_primary_target(row)
            if primary_target is None:
                reconciliation_required = any(
                    context.get("kind")
                    in {"task", "deliverable", "release_package", "dispatch", "session"}
                    for context in terminal_context
                )
            else:
                reconciliation_required = any(
                    (context.get("kind"), context.get("id")) == primary_target
                    for context in terminal_context
                )
        attention_group = _maybe_text(row.get("attention_group"))
        attention_reason = _maybe_text(row.get("attention_reason"))
        if bool(row.get("reconciliation_required")) and not reconciliation_required:
            if bool(row.get("response_requested")):
                attention_group = "active_obligation"
                attention_reason = "awaiting_response"
            else:
                attention_group = "retained_history"
                attention_reason = "pending_without_response_obligation"
        team_id = _maybe_text(row.get("team_id"))
        if team_id not in self._visible_team_ids:
            team_id = None
        project_id = _maybe_text(row.get("project_id"))
        if not self._project_visible(project_id):
            project_id = None

        def visible_link_id(field: str, link_kind: str) -> str | None:
            link_id = _maybe_text(row.get(field))
            if link_id is None or not self._runtime_attention_link_visible(
                link_kind,
                link_id,
            ):
                return None
            return link_id

        def projected_narrative(field: str, limit: int) -> str | None:
            narrative = _maybe_text(row.get(field))
            if narrative is None:
                return None
            for session_id in sorted(hidden_session_ids, key=len, reverse=True):
                narrative = narrative.replace(session_id, "[not visible]")
            return _compact_text(narrative, limit)

        visible_promotion_id = visible_link_id("promotion_id", "promotion_ids")
        visible_session_id = visible_link_id("session_id", "session_ids")
        return _without_none(
            {
                "id": _maybe_text(row.get("id")),
                "kind": _maybe_text(row.get("kind")),
                "severity": _maybe_text(row.get("severity")),
                "title": projected_narrative("title", 180),
                "detail": projected_narrative("detail", 360),
                "state": _maybe_text(row.get("state")),
                "occurred_at": _maybe_text(row.get("occurred_at")),
                "age_seconds": row.get("age_seconds"),
                "age_bucket": _maybe_text(row.get("age_bucket")),
                "attention_group": attention_group,
                "attention_reason": attention_reason,
                "reconciliation_required": reconciliation_required,
                "terminal_context": terminal_context,
                "links": links,
                "team_id": team_id,
                "agent_id": visible_link_id("agent_id", "agent_ids"),
                "project_id": project_id,
                "project_ids": links.get("project_ids", []),
                "task_id": visible_link_id("task_id", "task_ids"),
                "inbox_item_id": visible_link_id("inbox_item_id", "inbox_item_ids"),
                "deliverable_id": visible_link_id("deliverable_id", "deliverable_ids"),
                "release_package_id": visible_link_id(
                    "release_package_id",
                    "release_package_ids",
                ),
                "review_request_id": visible_link_id(
                    "review_request_id",
                    "review_request_ids",
                ),
                "blocker_id": visible_link_id("blocker_id", "blocker_ids"),
                "assignment_id": visible_link_id("assignment_id", "assignment_ids"),
                "promotion_id": visible_promotion_id,
                "commit_sha": (
                    _maybe_text(row.get("commit_sha"))
                    if visible_promotion_id is not None
                    and _maybe_text(row.get("kind")) == "failed_git_promotion"
                    else None
                ),
                "action_record_id": _maybe_text(row.get("action_record_id")),
                "dispatch_id": visible_link_id("dispatch_id", "dispatch_ids"),
                "session_id": visible_session_id,
                "source_kind": _maybe_text(row.get("source_kind")),
                "source_id": _maybe_text(row.get("source_id")),
                "owner_agent_id": visible_link_id("owner_agent_id", "agent_ids"),
                "delegate_agent_id": visible_link_id("delegate_agent_id", "agent_ids"),
                "route": _maybe_text(row.get("route")),
                "escalation_depth": row.get("escalation_depth"),
                "blocked_by_task_ids": [
                    task_id
                    for value in _sequence(row.get("blocked_by_task_ids"))
                    if (task_id := _maybe_text(value)) is not None
                    and self._runtime_attention_link_visible("task_ids", task_id)
                ],
            }
        )


_RUNTIME_ATTENTION_LINK_FIELDS = frozenset(
    {
        "team_ids",
        "project_ids",
        "agent_ids",
        "task_ids",
        "deliverable_ids",
        "release_package_ids",
        "review_request_ids",
        "blocker_ids",
        "dispatch_ids",
        "session_ids",
        "assignment_ids",
        "promotion_ids",
        "inbox_item_ids",
    }
)
_RUNTIME_ATTENTION_CONTEXT_LINK_FIELDS = {
    "agent": "agent_ids",
    "blocker": "blocker_ids",
    "deliverable": "deliverable_ids",
    "dispatch": "dispatch_ids",
    "ephemeral_assignment": "assignment_ids",
    "git_promotion": "promotion_ids",
    "project": "project_ids",
    "release_package": "release_package_ids",
    "review_request": "review_request_ids",
    "session": "session_ids",
    "task": "task_ids",
}


def _runtime_attention_read_sort_key(
    row: Mapping[str, Any],
) -> tuple[int, float, int, str, str]:
    group_rank = {
        "active_obligation": 0,
        "failed_attempt": 1,
        "retained_history": 2,
    }.get(_maybe_text(row.get("attention_group")) or "", 9)
    occurred_at = _maybe_text(row.get("occurred_at"))
    timestamp = 0.0
    if occurred_at is not None:
        try:
            parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            timestamp = (
                parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            ).timestamp()
        except ValueError:
            pass
    severity_rank = {"critical": 0, "warning": 1, "info": 2}.get(
        _maybe_text(row.get("severity")) or "",
        9,
    )
    return (
        group_rank,
        -timestamp,
        severity_rank,
        _maybe_text(row.get("kind")) or "",
        _maybe_text(row.get("id")) or "",
    )


def _page(
    records: list[dict[str, Any]],
    *,
    limit: int,
    cursor: str | None,
    offset: int | None,
) -> dict[str, Any]:
    safe_limit = max(1, min(_MAX_LIMIT, int(limit or _DEFAULT_LIMIT)))
    resolved_offset = _cursor_offset(cursor) if cursor is not None else max(0, int(offset or 0))
    page_records = records[resolved_offset : resolved_offset + safe_limit]
    next_offset = resolved_offset + len(page_records)
    next_cursor = str(next_offset) if next_offset < len(records) else None
    return {
        "records": page_records,
        "count": len(page_records),
        "total": len(records),
        "total_count": len(records),
        "omitted_count": max(0, len(records) - next_offset),
        "offset": resolved_offset,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
        "limit": safe_limit,
    }


def _cursor_offset(cursor: str | None) -> int:
    if cursor is None or not str(cursor).strip():
        return 0
    try:
        offset = int(str(cursor).strip())
    except ValueError:
        raise ValueError("cursor must be an integer offset") from None
    return max(0, offset)


def _matches(expected: str | None, actual: object) -> bool:
    expected_text = _maybe_text(expected)
    if expected_text is None:
        return True
    return _maybe_text(actual) == expected_text


def _matches_enum(expected: str | None, actual: object) -> bool:
    expected_text = _maybe_text(expected)
    if expected_text is None:
        return True
    return _enum_value(actual) == expected_text


def _task_sort_key(task: AgentTask) -> tuple[int, int, str, str]:
    terminal = task.state in {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    return (1 if terminal else 0, -task.priority, _json_value(task.updated_at) or "", task.id)


def _inbox_sort_key(item: AgentInboxItem) -> tuple[int, str, str]:
    priority = {"high": 0, "normal": 1, "low": 2}.get(item.priority.value, 9)
    return (priority, _json_value(item.created_at) or "", item.id)


def _blocker_sort_key(blocker: TeamBlocker) -> tuple[int, int, str, str]:
    terminal = blocker.state == TeamBlockerState.RESOLVED
    priority = {"high": 0, "normal": 1, "low": 2}.get(blocker.priority.value, 9)
    return (
        1 if terminal else 0,
        priority,
        _json_value(blocker.updated_at) or "",
        blocker.id,
    )


def _case_sort_key(case: TeamCase) -> tuple[int, str, str]:
    terminal = case.status.value in {"closed", "resolved", "archived"}
    return (1 if terminal else 0, _json_value(case.updated_at) or "", case.id)


def _deliverable_sort_key(deliverable: Deliverable) -> tuple[int, str, str]:
    terminal = deliverable.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED, TeamRecordState.CANCELLED}
    return (1 if terminal else 0, _json_value(deliverable.created_at) or "", deliverable.id)


def _review_request_sort_key(request: ReviewRequest) -> tuple[int, str, str]:
    terminal = request.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED, TeamRecordState.CANCELLED}
    return (1 if terminal else 0, _json_value(request.created_at) or "", request.id)


def _release_sort_key(release: ReleasePackage) -> tuple[int, str, str]:
    terminal = release.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED, TeamRecordState.CANCELLED}
    timestamp = _json_value(release.updated_at) or _json_value(release.created_at) or ""
    return (1 if terminal else 0, timestamp, release.id)


def _state_counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _first_text(payload: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        text = _maybe_text(payload.get(key))
        if text is not None:
            return text
    return None


def _compact_text(value: object, limit: int) -> str | None:
    text = _maybe_text(value)
    if text is None or len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _capped_list(values: object, limit: int) -> dict[str, Any]:
    items = _sequence(values)
    capped = items[:limit]
    return {"items": capped, "omitted_count": max(0, len(items) - len(capped))}


def _sequence(value: object) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _without_none(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if item is None:
            continue
        if isinstance(item, Mapping):
            result[key] = _without_none(item)
        elif isinstance(item, list):
            result[key] = [
                _without_none(entry) if isinstance(entry, Mapping) else entry for entry in item
            ]
        else:
            result[key] = item
    return result


def _enum_value(value: object) -> str | None:
    enum_value = getattr(value, "value", None)
    return _maybe_text(enum_value if enum_value is not None else value)


def _maybe_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text(value: object) -> str:
    return _maybe_text(value) or ""


def _json_value(value: object) -> Any:
    if hasattr(value, "isoformat"):
        isoformat = getattr(value, "isoformat")
        if callable(isoformat):
            return isoformat()
    return value
