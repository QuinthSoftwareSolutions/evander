from dataclasses import asdict

from evander.execution.container_worker_backend import ContainerWorkerVmExecutionBackend
from evander.policy import PolicyError
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.task_resource_scope import TaskResourceAuthority, TaskResourceScope

from .models import FixtureError, Scope


class Authority:
    def __init__(self, runtime, runtime_dir):
        # Gateway passes RuntimeContext; Admin Web and workflow providers pass
        # the supported composed admin runtime or its provider adapter.
        self.runtime = getattr(runtime, "runtime", None) or runtime
        self.team = TaskResourceAuthority(TeamStateRepository(runtime_dir))

    def assignment(self, session_id):
        session = self.runtime.policy.get_session(session_id)
        config = self.runtime.provider_config.resolve_for_session_manifest(session)
        if config.execution_backend != "container_worker_vm":
            raise FixtureError("Test fixtures require a container worker session")
        directory = (
            session.directory_path.as_posix()
            if hasattr(session.directory_path, "as_posix")
            else str(session.directory_path or "root")
        )
        config = ContainerWorkerVmExecutionBackend(config=config).config_for_session(
            session_id, directory_path=directory
        )
        return directory, str(config.container_worker_base_url).rstrip("/")

    def resolve(self, session_id, task_id, tool, settings):
        self.runtime.policy.require_tool(session_id, tool)
        scope = self.team.resolve(session_id=session_id, task_id=task_id, tool_name=tool)
        directory, worker_url = self.assignment(session_id)
        if scope.team_id not in settings.allowed_team_ids:
            raise FixtureError("Team is not enabled for test fixtures")
        workers = [
            key for key, worker in settings.workers.items() if worker.worker_base_url == worker_url
        ]
        if len(workers) != 1:
            raise FixtureError("Actual session worker has no unambiguous fixture service binding")
        return Scope(**asdict(scope), directory=directory, worker_id=workers[0])

    def owns(self, scope):
        task_scope = TaskResourceScope(
            **{key: getattr(scope, key) for key in TaskResourceScope.__dataclass_fields__}
        )
        try:
            self.runtime.policy.require_tool(scope.session_id, "test_fixtures_acquire")
        except PolicyError:
            return False
        directory, _ = self.assignment(scope.session_id)
        return directory == scope.directory and self.team.owns(task_scope)

    def notify(self, lease):
        from evander_team_addon.services.admin_service import TeamAdminService
        from evander_team_addon.outbox_publication import (
            request_team_outbox_publication_for_records,
        )

        scope = lease["scope"]
        next_step = (
            "Resources are provisioned. Call test_fixtures_acquire for this task/profile to bind the private configuration in the current job. "
            if lease["state"] == "awaiting_job"
            else "Use test_fixtures_get for readiness and the private job configuration path. "
        )
        message = (
            f"Test fixture lease {lease['lease_id']} is {lease['state']}. "
            + next_step
            + "This reports fixture readiness only; application verification and approval remain required."
        )
        state, _, result = TeamAdminService(self.team.repository).create_operator_directive(
            team_id=scope["team_id"],
            recipient_agent_id=scope["agent_id"],
            project_id=scope["project_id"],
            task_id=scope["task_id"],
            title="Test fixture readiness",
            message=message,
            source="test-fixtures",
            source_message_id=lease["lease_id"] + ":" + lease["state"],
            created_by="system:test-fixtures",
            expected_revision=self.team.repository.read().revision,
        )
        request_team_outbox_publication_for_records(
            self.team.repository,
            outbox_record_ids=result.get("outbox_record_ids", []),
        )
