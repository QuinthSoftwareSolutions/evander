"""Exercise captured intake through correction, event bindings, and host PR publication."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from evander.admin.services.pr_publication_policy import PrPublicationPolicyError
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.pr_create import PrCreateEffectProvider
from evander.effects.providers.schemas import PrCreateEffectDefinition
from evander.issues.service import IssueService
from evander.workflow.binding import resolve_workflow_bindings
from evander_team_addon.models import (
    Deliverable,
    ProjectGitConfig,
    ReleasePackage,
    ReviewRequest,
    TeamRecordState,
)
from evander_team_addon.provider_schemas import TeamCreateTaskEffectDefinition
from evander_team_addon.providers import TeamCreateTaskEffectProvider
from evander_team_addon.services.ado_attribution import release_publication_context
from evander_team_addon.services.domain.release_gates import (
    materialize_release_review_rejection_correction,
)
from test_effect_pr_create_provider import _build_context
from test_app_ado_attribution_workflow import app_document, migration
from test_pr_publication_policy import publisher
from test_team_addon_repository import _seed_hierarchy, _team_admin_service
from test_team_task_authorship import _create


def bind(config, node_id, output):
    return resolve_workflow_bindings(
        config,
        {node_id: output},
        {node_id: set(output)},
    ).resolved_config


@pytest.mark.parametrize("repair_path", [False, True])
@pytest.mark.parametrize("lost_context", [None, "release_event", "packaging_event", "pr_step"])
def test_attributed_intake_and_release_correction_reach_both_pr_paths(
    tmp_path,
    monkeypatch,
    repair_path,
    lost_context,
):
    service, remote, args = publisher(tmp_path, monkeypatch)
    m = migration()
    config, graphs = app_document()
    config, _ = m.upgrade_app_document(config, graphs, require_personal=True)
    effects = {node["id"]: node for node in config["effects"]}
    team_dir = tmp_path / "team-state"
    repo = _seed_hierarchy(team_dir)

    # Use the migration's actual intake bindings and the host ADO identity capture path.
    monkeypatch.setattr(
        IssueService,
        "get_remote_backlog_item_by_id",
        lambda *a, **k: {
            "fields": {
                "System.AssignedTo": {
                    "id": "alice",
                    "displayName": "Alice",
                    "uniqueName": "alice@example.test",
                }
            },
        },
    )
    intake_config = bind(
        effects[m.INTAKE]["config"],
        m.TRIGGER,
        {
            "work_item_id": "17015",
            "work_item": {"title": "Alarms"},
            "project": "APP",
        },
    )
    intake = TeamCreateTaskEffectProvider()
    intake.initialize(
        TeamCreateTaskEffectDefinition(
            id=m.INTAKE,
            type="effect:team:create-task",
            display_name="Intake",
            **intake_config,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(team_dir)},
            provider_config_resolver=SimpleNamespace(
                resolve_for_directory=lambda _: SimpleNamespace(
                    azure_devops_organization_url="https://dev.azure.com/org",
                )
            ),
        ),
    )
    captured = intake.execute()
    assert captured["success"], captured
    admin = _team_admin_service(repo)
    _create(admin, "implementation", parent_task_id=captured["task_id"])
    _create(admin, "integration", originating_task_id="implementation")
    state = repo.read()
    assert state.tasks["integration"].work_item_attributions[0].assignee_id == "alice"

    # The same product path that generated the incident's release correction must retain it.
    state.deliverables["original"] = Deliverable(
        id="original",
        task_id="integration",
        producer_agent_id="backend",
        summary="Alarms",
        state=TeamRecordState.ACCEPTED,
    )
    release = ReleasePackage(
        id="candidate",
        project_id="project",
        owner_agent_id="lead",
        release_branch="team/team/releases/candidate",
        candidate_commit_sha="1" * 40,
        deliverable_ids=["original"],
        state=TeamRecordState.REJECTED,
    )
    review = ReviewRequest(
        id="review",
        release_package_id=release.id,
        candidate_commit_sha="1" * 40,
        reviewer_agent_id="worker",
        state=TeamRecordState.REJECTED,
    )
    state.release_packages[release.id] = release
    state.review_requests[review.id] = review
    materialize_release_review_rejection_correction(
        state,
        release=release,
        review=review,
        findings=["Fix validation"],
        result={"events": []},
        now=datetime.now(UTC),
    )
    correction = state.tasks[release.review_correction_task_id]
    assert correction.work_item_attributions == state.tasks["integration"].work_item_attributions
    state.deliverables["correction"] = Deliverable(
        id="correction",
        task_id=correction.id,
        producer_agent_id="lead",
        summary="Corrected",
        state=TeamRecordState.ACCEPTED,
    )
    successor = ReleasePackage(
        id="successor",
        project_id="project",
        published_commit_sha="1" * 40,
        deliverable_ids=["original", "correction"],
        state=TeamRecordState.ACCEPTED,
    )
    state.release_packages[successor.id] = successor
    state.projects["project"].git = ProjectGitConfig(
        managed_repo_id="mr_0123456789abcdefabcd",
        directory_path="root/app",
        base_branch="main",
    )
    record = service._require_scoped_record("repo", "root/app")
    record["managed_repo_id"] = state.projects["project"].git.managed_repo_id
    args["managed_repo_id"] = record["managed_repo_id"]
    monkeypatch.setattr(service, "get_repository", lambda *a, **k: record)
    context_id = release_publication_context(
        state, release_package_id=successor.id, managed_git_admin=service
    )

    # Resolve every actual publication-context binding introduced by the two pack migrations.
    team_config, _ = m.upgrade_team_runtime(
        {
            "effects": [
                {
                    "id": "team-emit-release-published",
                    "type": "effect:event:emit",
                    "config": {"payload": {}},
                }
            ]
        },
        [],
    )
    release_payload = bind(
        team_config["effects"][0]["config"]["payload"],
        "team-mark-release-published",
        {
            "publication_context_id": context_id,
        },
    )
    if lost_context == "release_event":
        release_payload.pop("publication_context_id")
    packaging_payload = bind(
        effects["main-effect-request-true-app-release-pr-packaging"]["config"]["payload"],
        "main-trigger-team-release-published",
        {"payload": release_payload},
    )
    if lost_context == "packaging_event":
        packaging_payload.pop("publication_context_id")
    node_id = "main-effect-create-app-release-draft-pr" + (
        "-after-artifact-repair" if repair_path else ""
    )
    pr_config = {
        **{k: v for k, v in args.items() if k != "directory_path"},
        **effects[node_id]["config"],
    }
    resolved = bind(
        pr_config, "main-trigger-app-release-pr-packaging-requested", {"payload": packaging_payload}
    )
    if lost_context == "pr_step":
        resolved["publication_context_id"] = None
    effect = PrCreateEffectProvider()
    effect.initialize(
        PrCreateEffectDefinition(
            id=node_id, display_name="Publish", type="effect:pr:create", **pr_config
        ),
        _build_context(tmp_path, service),
    )
    effect.runtime_context.metadata["directory_path"] = "root/app"
    if lost_context:
        with pytest.raises(PrPublicationPolicyError, match="publication_context_missing"):
            effect.execute(resolved_config=resolved)
        assert remote.calls == []
    else:
        result = effect.execute(resolved_config=resolved)
        assert result["success"]
        assert result["actual_creator"]["id"] == result["intended_creator_id"] == "alice"
        assert result["credential_source"] == "personal_pat"
        assert result["fallback_reason"] is None
