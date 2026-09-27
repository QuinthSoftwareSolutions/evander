"""Reusable Team test builders with a fresh repository and fake state per call."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from evander_team_addon.models import EphemeralAgentPolicy, ProjectGoal
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.runtime_service import TeamRuntimeService


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


def _team_runtime_service(repo: TeamStateRepository) -> TeamRuntimeService:
    return TeamRuntimeService(repo)


def _team_agent_action_service(repo: TeamStateRepository) -> TeamAgentActionService:
    return TeamAgentActionService(repo, session_lookup=_root_session_lookup)


def _root_session_lookup(_session_id: str) -> dict[str, str]:
    return {"directory_path": "root"}


class FakeResumer:
    def __init__(
        self,
        result: dict[str, object] | None = None,
        on_resume: object | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.result = result or {"thread_id": "thread-1"}
        self.on_resume = on_resume

    def resume_agent(self, **kwargs: object) -> dict[str, object]:
        if callable(self.on_resume):
            self.on_resume(dict(kwargs))
        self.calls.append(dict(kwargs))
        return dict(self.result)


class FakeMetadataUpdater:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def session_update_metadata(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"success": True}


class FakeSessionStore(FakeMetadataUpdater):
    def __init__(self) -> None:
        super().__init__()
        self.sessions = {
            "session-backend": SimpleNamespace(
                session_id="session-backend",
                vm_id="vm-backend",
                vm_provider="libvirt",
                directory_path="root",
                metadata={},
            )
        }

    def read_all_sessions(self) -> dict[str, object]:
        return self.sessions

    def get_session(self, session_id: str) -> object | None:
        return self.sessions.get(session_id)

    def upsert_session(self, session: object) -> object:
        self.sessions[str(getattr(session, "session_id"))] = session
        return session

    def session_update_metadata(self, **kwargs: object) -> dict[str, object]:
        super().session_update_metadata(**kwargs)
        target = str(kwargs["target"])
        metadata = kwargs["metadata"]
        session = self.sessions[target]
        session.metadata = _deep_merge(dict(session.metadata), metadata)
        return {"success": True, "metadata": session.metadata}


class FakeFileCopier:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def vm_copy_files(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        files = list(kwargs.get("files") or [])
        return {
            "success": True,
            "vm_name": kwargs.get("target"),
            "copied_count": len(files),
            "copied_paths": [file["destination_path"] for file in files],
        }


class FakeRepoCloner:
    def __init__(
        self,
        *,
        success: bool = True,
        message: str = "repo prepared",
        on_clone: object | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.success = success
        self.message = message
        self.on_clone = on_clone

    def clone_repo_into_vm(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        if callable(self.on_clone):
            self.on_clone()
        return SimpleNamespace(
            success=self.success,
            vm_name=str(kwargs.get("vm_name") or "vm-backend"),
            message=self.message,
            target_dir="/home/agent/Evander",
            guest_repo_name="Evander",
        )


class FakeVmExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def vm_exec(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {
            "success": True,
            "stdout": f"EVANDER_BASE_COMMIT={'a' * 40}\n",
            "stderr": "",
            "exit_code": 0,
            "message": "task worktree prepared",
        }


def _deep_merge(base: dict[str, object], update: object) -> dict[str, object]:
    if not isinstance(update, dict):
        return base
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _seed_team(runtime_dir: Path, *, max_ephemeral: int = 0) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=1,
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=2,
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=max_ephemeral),
        expected_revision=3,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build backend",
        description="Implement backend work.",
        created_by_agent_id="lead",
        expected_revision=4,
    )
    return repo


def _add_luke_reviewer(
    repo: TeamStateRepository,
    *,
    with_review_profile: bool = True,
) -> None:
    _team_admin_service(repo).create_role(
        role_id="project__reviewer",
        team_id="team",
        project_id="project",
        name="Reviewer",
        description="Review deliverables; do not own implementation work.",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="luke",
        team_id="team",
        project_id="project",
        role_id="project__reviewer",
        session_id="session-luke",
        display_name="Luke",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    if with_review_profile:
        _team_admin_service(repo).create_review_profile(
            profile_id="project-review-profile",
            team_id="team",
            project_id="project",
            name="Project review",
            description="Default review checks for project deliverables.",
            applicable_role_ids=["project__reviewer"],
            expected_revision=repo.read().revision,
        )
