"""Optional consumer adapter; technology add-ons do not import Team."""

from evander.resources.models import Owner

from .repository import TeamStateRepository
from .services.task_resource_scope import TaskResourceAuthority, TaskResourceScope


class TeamResourceAuthority:
    def __init__(self, context):
        self.context = context
        self.repository = TeamStateRepository(context.runtime_dir)
        self.authority = TaskResourceAuthority(self.repository)

    def handles_session(self, session_id):
        return any(a.session_id == session_id for a in self.repository.read().agents.values())

    def resolve(self, session_id, reference, tool):
        if set(reference) != {"task_id"}:
            raise ValueError("Team resources require exactly one task_id")
        scope = self.authority.resolve(
            session_id=session_id, task_id=reference["task_id"], tool_name=tool
        )
        runtime = getattr(self.context.service, "runtime", None) or self.context.service
        session = runtime.policy.get_session(session_id)
        return Owner(
            provider="team-addon",
            key=f"{scope.team_id}:{scope.task_id}",
            directory=session.directory_path,
            session_id=session_id,
            reference={"task_id": scope.task_id},
            job_tags={"dispatch_id": scope.dispatch_id},
            valid_until=scope.dispatch_expires_at,
        )

    def owns(self, owner):
        state = self.repository.read()
        task = state.tasks.get(owner.reference.get("task_id"))
        agent = state.agents.get(task.owner_agent_id) if task else None
        if not task or not agent or owner.key != f"{agent.team_id}:{task.id}":
            return False
        # The durable owner survives idle dispatches, but never reassignment or session replacement.
        scope = TaskResourceScope(
            owner.session_id,
            agent.id,
            agent.team_id,
            task.project_id,
            task.id,
            owner.job_tags.get("dispatch_id", ""),
            owner.valid_until or 0,
        )
        return self.authority.owns(scope)

    def notify(self, owner, value):
        from .services.admin_service import TeamAdminService
        from .outbox_publication import request_team_outbox_publication_for_records

        state = self.repository.read()
        task = state.tasks.get(owner.reference["task_id"])
        if not self.owns(owner):
            return
        _, _, result = TeamAdminService(self.repository).create_operator_directive(
            team_id=state.projects[task.project_id].team_id,
            recipient_agent_id=task.owner_agent_id,
            project_id=task.project_id,
            task_id=task.id,
            title="Resource lease readiness",
            message=f"Resource lease {value['lease_id']} is {value['state']}. Reacquire its configured profile in the current job to bind private configuration. Resource readiness does not establish application verification or approval.",
            source="resource-addons",
            source_message_id=f"{value['lease_id']}:{value['notice_id']}",
            created_by="system:resource-addons",
            expected_revision=state.revision,
        )
        request_team_outbox_publication_for_records(
            self.repository, outbox_record_ids=result.get("outbox_record_ids", [])
        )


def build_resource_authority(context):
    return TeamResourceAuthority(context)
