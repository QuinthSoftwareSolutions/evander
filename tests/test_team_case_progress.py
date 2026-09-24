from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from evander_team_addon.services.domain.case_task import inherited_task_case_id
from team_repository_support import (
    _command_service,
    _lease_agent_dispatch,
    _seed_hierarchy,
    _team_agent_action_service,
    _team_admin_service,
)
from test_team_task_authorship import _create


def case(admin, case_id="intake", **kwargs):
    return admin.create_case_with_id(
        case_id=case_id,
        team_id="team",
        project_id="project",
        title="Intake",
        **kwargs,
    )


def test_case_reuse_is_atomic_and_preserves_operator_edits(tmp_path):
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    kwargs = dict(source="ticket", source_ref="org:project:42", reuse_existing=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: case(admin, f"intake-{i}", **kwargs), range(2)))
    assert results[0][1] == results[1][1]
    case_id = results[0][1]
    admin.update_case(case_id=case_id, title="Operator title", status="closed")
    state, reused = case(admin, **kwargs)
    assert reused == case_id
    assert state.cases[reused].title == "Operator title"
    assert state.cases[reused].status.value == "closed"
    with pytest.raises(ValueError, match="already exists"):
        case(admin, case_id)
    with pytest.raises(ValueError, match="source and source_ref"):
        case(admin, reuse_existing=True)
    with pytest.raises(ValueError, match="already exists"):
        case(admin, case_id, **{**kwargs, "source_ref": "other"})


def test_task_cases_inherit_in_admin_and_followup_paths(tmp_path):
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    case(admin)
    case(admin, "correction")
    _create(admin, "root", case_id="intake")
    _create(admin, "child", parent_task_id="root")
    _create(admin, "followup", originating_task_id="child")
    _create(admin, "correction-task", originating_task_id="root", case_id="correction")
    _create(admin, "correction-child", parent_task_id="correction-task")
    state = repo.read()
    assert set(state.cases["intake"].task_ids) == {"root", "child", "followup"}
    assert set(state.cases["correction"].task_ids) == {"correction-task", "correction-child"}
    _create(admin, "correction-child")  # An upsert with omitted case preserves it.
    assert repo.read().tasks["correction-child"].case_id == "correction"


def test_case_inheritance_covers_reversed_batches_and_command_creation(tmp_path):
    repo = _seed_hierarchy(tmp_path)
    case(_team_admin_service(repo))
    _lease_agent_dispatch(repo, agent_id="lead")
    commands = _command_service(repo)
    tasks = [
        {"task_id": "grandchild", "parent_task_id": "child"},
        {"task_id": "child", "parent_task_id": "root"},
        {"task_id": "root", "case_id": "intake"},
    ]
    for task in tasks:
        task.update(
            owner_agent_id="backend",
            project_id="project",
            title=task["task_id"],
            description="Work",
        )
    state, result = commands.create_tasks_command(
        session_id="session-lead",
        request_id="case-batch",
        tasks=tasks,
    )
    assert result.get("success", True), result
    assert set(state.cases["intake"].task_ids) == {"root", "child", "grandchild"}
    state, result = commands.create_task_command(
        session_id="session-lead",
        request_id="followup",
        task_id="followup",
        owner_agent_id="backend",
        project_id="project",
        title="Follow up",
        description="Work",
        originating_task_id="grandchild",
    )
    assert state.tasks["followup"].case_id == "intake", result
    with pytest.raises(ValueError, match="another project"):
        inherited_task_case_id(state, project_id="other", parent_task_id="root")


def test_case_inheritance_detects_legacy_cycles(tmp_path):
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    _create(admin, "first")
    _create(admin, "second", originating_task_id="first")
    state = repo.read()
    state.tasks["first"].originating_task_id = "second"
    with pytest.raises(ValueError, match="cycle"):
        inherited_task_case_id(state, project_id="project", parent_task_id="first")


@pytest.mark.parametrize("shared_predecessor", [True, False])
def test_case_less_lineage_resolves_each_ancestor_once(tmp_path, shared_predecessor):
    state = _seed_hierarchy(tmp_path).read()
    depth = 24

    class CountingPlans(dict):
        reads = 0

        def get(self, key, default=None):
            self.reads += 1
            # Fail promptly on the old exponential traversal, without a timing assertion.
            assert self.reads <= depth * 2, "case-less lineage repeatedly traversed"
            return super().get(key, default)

    plans = CountingPlans()
    for index in range(depth):
        plans[f"node-{index}"] = SimpleNamespace(
            project_id="project",
            case_id=None,
            originating_task_id=f"node-{index - 1}" if index else None,
            parent_task_id=(
                f"node-{index - (1 if shared_predecessor else 2)}" if index > 1 else None
            ),
        )
    assert (
        inherited_task_case_id(
            state, project_id="project", parent_task_id=f"node-{depth - 1}", plans=plans
        )
        is None
    )
    assert plans.reads == depth


@pytest.mark.parametrize("reverse", [False, True])
def test_agent_action_keeps_valid_creations_when_other_plans_have_missing_parents(
    tmp_path, reverse
):
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    case(admin)
    _create(admin, "root", case_id="intake")
    tasks = [
        {"task_id": "invalid", "parent_task_id": "missing"},
        {"task_id": "invalid-child", "parent_task_id": "invalid"},
        {"task_id": "invalid-dependent", "dependency_task_ids": ["invalid"]},
        {"task_id": "valid", "parent_task_id": "root"},
    ]
    for task in tasks:
        task.update(
            owner_agent_id="backend",
            project_id="project",
            title=task["task_id"],
            description="Work",
        )
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={"agent_id": "lead", "task_creations": tasks[::-1] if reverse else tasks}
    )
    assert result["created_task_ids"] == ["valid"], result
    assert state.tasks["valid"].case_id == "intake"
    assert not {"invalid", "invalid-child", "invalid-dependent"}.intersection(state.tasks)
    assert any("missing parent missing" in error for error in result["errors"])


def test_create_case_effect_returns_encoded_permalink_and_reuses_case(tmp_path):
    from evander.effects.providers.base import EffectRuntimeContext
    from evander_team_addon.provider_schemas import TeamCreateCaseEffectDefinition
    from evander_team_addon.providers import TeamCreateCaseEffectProvider

    _seed_hierarchy(tmp_path)
    provider = TeamCreateCaseEffectProvider()
    provider.initialize(
        TeamCreateCaseEffectDefinition(
            id="intake",
            display_name="Intake",
            type="effect:team:create-case",
            team_id="team",
            project_id="project",
            title="Intake",
            case_id="case A&42",
            source="ticket",
            source_ref="org:42",
            reuse_existing=True,
        ),
        EffectRuntimeContext(provider_config={"runtime_dir": str(tmp_path)}),
    )
    output = provider.execute()
    assert output["success"], output
    assert output["task_board_path"] == "/#addon:team-addon?view=task-board&case_id=case+A%2642"
    assert provider.execute()["case_id"] == "case A&42"
