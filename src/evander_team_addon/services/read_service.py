from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..models import (
    AgentInboxItem,
    AgentTask,
    Deliverable,
    PersistentAgent,
    Project,
    ReleasePackage,
    ReviewRequest,
    TeamCase,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    agent_project_assignment_payloads,
    agent_lead_project_ids,
    agent_project_ids,
    effective_role_permissions,
    release_package_payload_with_derived,
    runtime_operations_payload,
    task_payload_with_derived,
)

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

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        task = self._state.tasks.get(_text(task_id))
        if task is None or not self._task_visible(task):
            return None
        payload = task_payload_with_derived(self._state, task)
        git_contexts = self._git_work_context_rows_for_task(task.id)
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
        return _without_none(release_package_payload_with_derived(self._state, release))

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
        return _without_none(payload)

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
        rows = [
            self._runtime_attention_row(row)
            for row in attention
            if isinstance(row, Mapping)
            and self._runtime_attention_visible(row)
            and _matches(kind, row.get("kind"))
            and _matches(severity, row.get("severity"))
        ]
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

    def _deliverable_visible(self, deliverable: Deliverable) -> bool:
        task = self._state.tasks.get(deliverable.task_id)
        return self._agent_visible(deliverable.producer_agent_id) or (
            task is not None and self._task_visible(task)
        )

    def _review_request_visible(self, request: ReviewRequest) -> bool:
        deliverable = self._state.deliverables.get(request.deliverable_id)
        return self._agent_visible(request.reviewer_agent_id) or (
            deliverable is not None and self._deliverable_visible(deliverable)
        )

    def _release_visible(self, release: ReleasePackage) -> bool:
        return self._project_visible(release.project_id)

    def _runtime_attention_visible(self, row: Mapping[str, Any]) -> bool:
        if self._agent_visible(_maybe_text(row.get("agent_id"))):
            return True
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

    def _git_work_context_rows_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for context in self._state.git_work_contexts.values():
            if context.task_id != task_id:
                continue
            if context.agent_id != self._actor_id:
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
        derived = task_payload_with_derived(self._state, task)
        child_ids = _capped_list(derived.get("child_task_ids") or [], 12)
        row: dict[str, Any] = {
            "id": task.id,
            "title": _compact_text(task.title, 180),
            "description_preview": _compact_text(task.description, 240),
            "state": task.state.value,
            "priority": task.priority,
            "owner_agent_id": task.owner_agent_id,
            "project_id": task.project_id,
            "case_id": task.case_id,
            "dependency_status": derived.get("dependency_status"),
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
        payload = release_package_payload_with_derived(self._state, release)
        return _without_none(
            {
                "id": release.id,
                "project_id": release.project_id,
                "state": release.state.value,
                "release_branch": release.release_branch,
                "canonical_branch": release.canonical_branch,
                "published_commit_sha": release.published_commit_sha,
                "deliverable_count": len(release.deliverable_ids),
                "feature_branch_count": len(release.feature_branches),
                "next_action": payload.get("next_action"),
                "next_action_hint": _compact_text(payload.get("next_action_hint"), 360),
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
                "goal_count": len(project.goals),
                "has_git": project.git is not None,
                "updated_at": _json_value(project.updated_at),
            }
        )

    def _runtime_attention_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return _without_none(
            {
                "id": _maybe_text(row.get("id")),
                "kind": _maybe_text(row.get("kind")),
                "severity": _maybe_text(row.get("severity")),
                "title": _compact_text(row.get("title"), 180),
                "detail": _compact_text(row.get("detail"), 360),
                "state": _maybe_text(row.get("state")),
                "agent_id": _maybe_text(row.get("agent_id")),
                "project_id": _maybe_text(row.get("project_id")),
                "project_ids": [
                    project_id
                    for value in _sequence(row.get("project_ids"))
                    if (project_id := _maybe_text(value)) is not None
                ],
                "task_id": _maybe_text(row.get("task_id")),
                "inbox_item_id": _maybe_text(row.get("inbox_item_id")),
                "deliverable_id": _maybe_text(row.get("deliverable_id")),
                "release_package_id": _maybe_text(row.get("release_package_id")),
                "blocked_by_task_ids": _sequence(row.get("blocked_by_task_ids")),
            }
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
    }
    return (1 if terminal else 0, -task.priority, _json_value(task.updated_at) or "", task.id)


def _inbox_sort_key(item: AgentInboxItem) -> tuple[int, str, str]:
    priority = {"high": 0, "normal": 1, "low": 2}.get(item.priority.value, 9)
    return (priority, _json_value(item.created_at) or "", item.id)


def _case_sort_key(case: TeamCase) -> tuple[int, str, str]:
    terminal = case.status.value in {"closed", "resolved", "archived"}
    return (1 if terminal else 0, _json_value(case.updated_at) or "", case.id)


def _deliverable_sort_key(deliverable: Deliverable) -> tuple[int, str, str]:
    terminal = deliverable.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}
    return (1 if terminal else 0, _json_value(deliverable.created_at) or "", deliverable.id)


def _review_request_sort_key(request: ReviewRequest) -> tuple[int, str, str]:
    terminal = request.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}
    return (1 if terminal else 0, _json_value(request.created_at) or "", request.id)


def _release_sort_key(release: ReleasePackage) -> tuple[int, str, str]:
    terminal = release.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}
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
