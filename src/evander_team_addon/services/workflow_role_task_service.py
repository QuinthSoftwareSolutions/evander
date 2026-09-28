"""Durable create-or-reuse of workflow-owned role tasks, without agent dispatches."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from evander.models import DirectoryPath, utc_now

from ..models import AgentStatus, TeamIdempotencyRecord, TeamState
from ..repository import TeamStateConflictError, TeamStateRepository
from .admin_service import TeamAdminService
from .commands.outbox import _team_event_outbox_records
from .commands.task_creation_commands import create_queued_role_task
from .commands.work_item_helpers import _normalize_task_creation_mapping
from .domain.common import _required_text


ROLE_TASK_EFFECT = "effect:team:create-task-for-role"


class TeamWorkflowRoleTaskService:
    def __init__(self, repository: TeamStateRepository, *, managed_git_admin: Any = None):
        self.repository = repository
        self.admin = TeamAdminService(repository, managed_git_admin=managed_git_admin)

    def create_or_reuse(
        self,
        *,
        team_id: str,
        actor_agent_id: str,
        role_id: str,
        task: Mapping[str, Any],
        directory_scope: str,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        team_id = _required_text(team_id, "team_id")
        actor_agent_id = _required_text(actor_agent_id, "actor_agent_id")
        role_id = _required_text(role_id, "role_id")
        creation = _normalize_task_creation_mapping(task, field_name="task")
        task_id = _required_text(creation.get("task_id"), "task_id")
        project_id = _required_text(creation.get("project_id"), "project_id")
        scope = DirectoryPath(segments=directory_scope).as_posix()
        # Stable identity is separate from editable instructions and dependency snapshots.
        # A later event can observe different inputs but must never rewrite existing work.
        identity = {
            "team_id": team_id,
            "project_id": project_id,
            "actor_agent_id": actor_agent_id,
            "role_id": role_id,
            "parent_task_id": creation["parent_task_id"],
            "originating_task_id": creation["originating_task_id"],
            "case_id": creation["case_id"],
        }
        digest = self.repository.normalized_payload_digest(identity)
        receipt_key = {
            "tool_name": ROLE_TASK_EFFECT,
            "caller_identity": "workflow",
            "scope_kind": "task",
            "scope_id": task_id,
            "request_id": task_id,
        }

        def apply(state: TeamState, tx: Any):
            project = state.projects.get(project_id)
            actor = state.agents.get(actor_agent_id)
            if project is None or project.team_id != team_id:
                raise ValueError("project does not belong to the requested team")
            project_directory = DirectoryPath(segments=project.directory_path).as_posix()
            if project_directory != scope and not project_directory.startswith(scope + "/"):
                raise ValueError("project is outside workflow directory scope")
            if actor is None or actor.team_id != team_id or actor.status == AgentStatus.ARCHIVED:
                raise ValueError("delegating actor must belong to the team and not be archived")
            receipt = tx.get_idempotency_record(**receipt_key)
            if receipt is not None:
                if receipt.normalized_payload_digest != digest:
                    raise ValueError("task_id is already bound to a different role-task identity")
                existing = state.tasks.get(task_id)
                if existing is None or existing.project_id != project_id:
                    raise ValueError("role-task receipt no longer references its original task")
                return state, {
                    "created": False,
                    "outbox_record_ids": list(receipt.response_payload["outbox_record_ids"]),
                }
            if task_id in state.tasks:
                raise ValueError("task_id already exists without this workflow's role-task receipt")
            if expected_revision is not None and expected_revision != state.revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            applied = create_queued_role_task(
                state,
                agent=actor,
                role_id=role_id,
                creation=creation,
                git_default_branch_resolver=self.admin._resolve_managed_git_default_branch,
                git_base_commit_resolver=self.admin._resolve_managed_git_branch_commit,
                chat_source_refs=[],
            )
            state.tasks[task_id].created_by_actor = f"workflow:{ROLE_TASK_EFFECT}"
            records = [
                tx.enqueue_outbox_record(record)
                for record in _team_event_outbox_records(applied.response_payload.get("events", []))
            ]
            result = {"created": True, "outbox_record_ids": [record.id for record in records]}
            tx.store_idempotency_record(
                TeamIdempotencyRecord(
                    id=f"idempotency-{uuid4().hex}",
                    **receipt_key,
                    normalized_payload_digest=digest,
                    response_payload=result,
                    created_domain_ids=[task_id, *result["outbox_record_ids"]],
                )
            )
            state.revision += 1
            state.updated_at = utc_now()
            return state, result

        return self.repository.store.update_state_with_command_transaction(apply)
