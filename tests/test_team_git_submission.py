from types import SimpleNamespace

import pytest

from evander.addons import AddonContext
from evander.admin.services.git_submission_service import GitSubmissionService
from evander.effects.providers.base import EffectRuntimeContext
from evander_team_addon.git_submission import validate_submission_checkout
from evander_team_addon.models import ProjectGitConfig, GitWorkContextState
from evander_team_addon.providers import (
    TeamWakeDueAgentsEffectDefinition,
    TeamWakeDueAgentsEffectProvider,
)
from team_runtime_support import (
    FakeSessionStore,
    FakeResumer,
    FakeFileCopier,
    FakeRepoCloner,
    FakeVmExecutor,
    _seed_team,
)


@pytest.mark.parametrize("retirement", ["closed", "generation", "owner"])
def test_team_preparation_registers_and_revalidates_submission_binding(tmp_path, retirement):
    repo_id = "mr_0123456789abcdefabcd"
    repo = _seed_team(tmp_path)

    def configure(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id=repo_id, base_branch="main"
        )
        return state

    repo.update(configure)
    sessions = FakeSessionStore()
    session = sessions.get_session("session-backend")
    session.metadata["managed_git_access_grants"] = {
        repo_id: {
            "managed_repo_id": repo_id,
            "access_mode": "write",
            "lifetime": "session",
        }
    }
    addon_context = AddonContext(
        addon_id="team-addon", runtime_dir=tmp_path, addon_runtime_dir=tmp_path
    )
    service = GitSubmissionService(
        SimpleNamespace(session_repository=sessions),
        SimpleNamespace(runtime_dir=tmp_path, clone_info=lambda *a, **kw: {}),
        tmp_path,
        execute=lambda *a: pytest.fail("A retired binding must not execute"),
        validators={
            "team-addon": lambda binding: validate_submission_checkout(addon_context, binding)
        },
    )
    admin = SimpleNamespace(
        register_git_submission_checkout=service.register_checkout, git_submission_admin=service
    )
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake", display_name="Wake", type="effect:team:wake-due-agents"
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=FakeResumer(),
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=FakeRepoCloner(),
            vm_executor=FakeVmExecutor(),
            session_lookup=sessions,
            session_metadata_updater=sessions,
            admin_service=admin,
        ),
    )
    result = provider.execute()
    assert result["dispatches"], result.get("errors", result)
    context = result["dispatches"][0]["project_repo"]["git_work_contexts"][0]
    cid = context["checkout_id"]
    assert service.resolve(session, checkout_id=cid)["success"]
    assert "submit_git_changes" in session.allowed_tools

    def retire(state):
        work = state.git_work_contexts[context["id"]]
        if retirement == "closed":
            work.state = GitWorkContextState.ABANDONED
        elif retirement == "generation":
            state.tasks[work.task_id].description += " changed implementation requirements"
        else:
            state.tasks[work.task_id].owner_agent_id = "lead"
        return state

    repo.update(retire)
    assert service.resolve(session, checkout_id=cid)["code"] == "checkout_unavailable"
    assert cid not in {binding["checkout_id"] for binding in service.store.list(session.session_id)}
