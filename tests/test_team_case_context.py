from copy import deepcopy

import pytest

from evander_team_addon.models import AgentTask, TeamCase, team_state_payload
from evander_team_addon.services.case_context_service import (
    CaseContextService,
    effective_view,
    legacy_notes,
    make_note,
)
from evander_team_addon.services.commands.note_commands import TeamNoteCommandService
from test_team_addon_repository import _lease_agent_dispatch, _seed_hierarchy


@pytest.fixture
def case_repo(tmp_path):
    repo = _seed_hierarchy(tmp_path)

    def seed(state):
        state.cases["case"] = TeamCase(
            id="case",
            team_id="team",
            project_id="project",
            title="Resolve customer request",
            owner_agent_id="lead",
            task_ids=["task"],
        )
        state.tasks["task"] = AgentTask(
            id="task",
            owner_agent_id="worker",
            project_id="project",
            case_id="case",
            priority=1,
            title="Implement the plan",
            description="Preserve the established contract.",
            created_by_agent_id="lead",
        )

    repo.update(seed)
    _lease_agent_dispatch(repo, agent_id="worker")
    return repo


def record(repo, **kwargs):
    return TeamNoteCommandService(repo).record_team_note_command(
        session_id="session-worker",
        body="Choose additive compatibility.",
        request_id="choice",
        **kwargs,
    )


def test_note_contract_inference_atomicity_and_idempotency(case_repo):
    state, response = record(
        case_repo, kind="decision", rationale="Existing consumers depend on it."
    )
    assert response["success"] is True
    note = state.notes[response["note_id"]]
    assert response["note_id"] == response["action_record_id"]
    assert response["progress_made"] is False
    assert note.case_id == "case" and note.task_ids == ["task"]
    assert note.rationale == "Existing consumers depend on it."
    again, replay = record(case_repo, kind="decision", rationale=note.rationale)
    assert replay["note_id"] == note.id
    assert len(again.notes) == 1
    _, rejected = TeamNoteCommandService(case_repo).record_team_note_command(
        session_id="session-worker",
        body="Invalid link",
        request_id="bad",
        task_ids=["missing"],
    )
    assert not rejected["success"]
    assert len(case_repo.read().notes) == 1


def test_legacy_call_and_backfill_keep_original_identifiers(case_repo):
    state, result = record(case_repo)
    nid = result["note_id"]
    case_repo.update(lambda s: s.notes.clear())
    assert legacy_notes(case_repo.read())[nid].case_id == "case"
    service = CaseContextService(case_repo)
    before = case_repo.read().revision
    assert service.backfill_notes()["note_ids"] == [nid]
    assert case_repo.read().revision == before
    service.backfill_notes(dry_run=False)
    assert case_repo.read().notes[nid].created_at == state.notes[nid].created_at
    assert service.backfill_notes(dry_run=False)["note_ids"] == []


def test_supersession_preserves_original_and_requires_authority(case_repo):
    state, result = record(case_repo, kind="decision")
    original = deepcopy(state.notes[result["note_id"]])
    note = make_note(
        state,
        note_id="revision",
        agent_id="worker",
        dispatch_id="dispatch-command",
        body="Updated after owner clarification",
        supersedes_note_id=original.id,
    )
    state.notes[note.id] = note
    assert state.notes[original.id] == original
    assert note.supersedes_note_id == original.id
    with pytest.raises(ValueError, match="dispatch"):
        make_note(
            state,
            note_id="bad",
            agent_id="backend",
            dispatch_id="dispatch-command",
            body="Overwrite",
            supersedes_note_id=original.id,
        )


def test_context_query_is_read_only_and_has_evidence(case_repo):
    record(case_repo, kind="decision", rationale="Protect existing integrations")
    service = CaseContextService(case_repo)
    revision = case_repo.read().revision
    context = service.assemble(case_ids=["case"])
    assert "Protect existing integrations" in context["text"]
    assert "Preserve the established contract" in context["text"]
    assert context["case_ids"] == ["case"]
    assert case_repo.read().revision == revision
    assert not case_repo.read().case_context_snapshots


def test_snapshot_is_immutable_idempotent_and_exactly_session_granted(case_repo):
    state, result = record(case_repo, kind="decision")
    service = CaseContextService(case_repo)
    snapshot = service.create_snapshot(
        request_id="package", case_ids=["case"], recipient_session_id="packager"
    )
    before = snapshot.model_dump(mode="json")
    case_repo.update(lambda s: setattr(s.cases["case"], "description", "Later requirement"))
    assert (
        service.create_snapshot(
            request_id="package", case_ids=["case"], recipient_session_id="packager"
        ).model_dump(mode="json")
        == before
    )
    with pytest.raises(ValueError, match="different inputs"):
        service.create_snapshot(request_id="package", case_ids=["case"], view_id="task-brief")
    with pytest.raises(PermissionError):
        service.get_snapshot(snapshot.id, session_id="other")
    records = service.get_snapshot(snapshot.id, session_id="packager", section="decisions")
    assert records["evidence"][0]["id"] == result["note_id"]
    assert "Later requirement" not in str(records)


def test_views_versioning_inheritance_and_budget(case_repo):
    service = CaseContextService(case_repo)
    values = dict(
        view_id="customer-brief",
        title="Customer brief",
        sections=[{"name": "request", "text_limit": 16000}],
        text_budget=1000,
    )
    service.save_view(
        directory_path="root",
        values=values,
        expected_revision=case_repo.read().revision,
        created_by="operator",
    )
    old_revision = case_repo.read().revision
    service.save_view(
        directory_path="root/child",
        values=values,
        expected_revision=old_revision,
        created_by="operator",
    )
    assert (
        effective_view(
            case_repo.read(), view_id="customer-brief", directory_path="root/child/deeper"
        ).directory_path
        == "root/child"
    )
    with pytest.raises(Exception, match="revision"):
        service.save_view(
            directory_path="root",
            values=values,
            expected_revision=old_revision,
            created_by="operator",
        )
    case_repo.update(lambda s: setattr(s.cases["case"], "description", "Long request " * 2000))
    context = service.assemble(case_ids=["case"], view_id="customer-brief")
    assert len(context["text"]) <= 1000 and not context["complete"]
    assert context["omissions"][0]["kind"] == "truncated"


def test_directory_is_not_overridden_by_recipe_and_state_omits_bodies(case_repo):
    record(case_repo)
    service = CaseContextService(case_repo)
    with pytest.raises(PermissionError):
        service.assemble(case_ids=["case"], directory_path="root/other")
    service.create_snapshot(request_id="frozen", case_ids=["case"])
    payload = team_state_payload(case_repo.read())
    assert payload["notes"] == {}
    assert payload["case_context_snapshots"] == {}


def test_multiple_dispatch_cases_do_not_guess_note_case(case_repo):
    def add(state):
        state.cases["second"] = TeamCase(
            id="second",
            team_id="team",
            project_id="project",
            title="Unrelated",
            task_ids=["second-task"],
        )
        state.tasks["second-task"] = AgentTask(
            id="second-task",
            owner_agent_id="worker",
            project_id="project",
            case_id="second",
            title="Other",
            priority=1,
            created_by_agent_id="lead",
            description="Separate work",
        )
        state.dispatches["dispatch-command"].task_ids.append("second-task")

    case_repo.update(add)
    state, result = record(case_repo)
    assert state.notes[result["note_id"]].case_id is None
    assert not state.notes[result["note_id"]].task_ids


def test_workflow_query_snapshot_registration_and_directory_scope(case_repo):
    from types import SimpleNamespace
    from evander_team_addon.case_context_providers import (
        CaseContextQueryDefinition,
        CaseContextQueryProvider,
        CaseContextSnapshotEffectDefinition,
        CaseContextSnapshotEffectProvider,
    )
    from evander_team_addon.providers import register_providers

    runtime = SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=case_repo.runtime_dir),
        metadata={"directory_path": "root"},
    )
    query = CaseContextQueryProvider()
    query.initialize(
        CaseContextQueryDefinition(
            id="context", display_name="Context", type="query:team:case-context", case_id="case"
        ),
        runtime,
    )
    before = case_repo.read().revision
    assert (
        query.execute()["text"] == CaseContextService(case_repo).assemble(case_ids=["case"])["text"]
    )
    assert case_repo.read().revision == before
    effect = CaseContextSnapshotEffectProvider()
    effect.initialize(
        CaseContextSnapshotEffectDefinition(
            id="snapshot",
            display_name="Snapshot",
            type="effect:team:snapshot-case-context",
            request_id="workflow",
            case_id="case",
            recipient_session_id="packager",
        ),
        runtime,
    )
    assert effect.execute()["snapshot_id"] == effect.execute()["snapshot_id"]
    runtime.metadata["directory_path"] = "root/child"
    with pytest.raises(ValueError, match="outside"):
        query.execute(resolved_config={"directory_path": "root"})
    registration = register_providers()
    assert "query:team:case-context" in registration.query_providers
    assert "effect:team:snapshot-case-context" in registration.effect_providers
    assert any(
        e.type == "query:team:case-context" for e in registration.workflow_editor_catalog_entries
    )


def test_source_capture_retry_and_comment_only_changes(case_repo):
    from evander_team_addon.case_context_providers import ado_evidence, record_source

    service = CaseContextService(case_repo)
    item = {
        "id": "42",
        "rev": 3,
        "title": "Request",
        "description": "Original requirement",
        "acceptance_criteria": "Keep compatibility",
        "comments": [
            {"id": "c1", "text": "Owner clarification", "created_by": "Owner"},
            {"id": "c2", "text": "Trudy confirms", "created_by": "Trudy"},
        ],
    }
    first = ado_evidence(item, external_identifier="ado:org:Project:42")
    kwargs = dict(
        directory_path="root", team_id="team", project_id="project", case_ids=["case"], task_ids=[]
    )
    recorded = record_source(service, **kwargs, evidence=first)
    assert record_source(service, **kwargs, evidence=first) == recorded
    item["comments"][0]["text"] = "Owner corrected the clarification"
    second = ado_evidence(item, external_identifier="ado:org:Project:42")
    assert first["external_version"] != second["external_version"]
    record_source(service, **kwargs, evidence=second)
    context = service.assemble(case_ids=["case"])
    assert "Trudy confirms" in context["text"]
    assert "Owner corrected" in context["text"]
    assert len(case_repo.read().external_source_evidence_snapshots) == 2
    # Existing source authorization remains dispatch-frozen for agents.
    agent_context = service.assemble(case_ids=["case"], actor_id="worker")
    assert "Trudy confirms" not in agent_context["text"]
    assert any(w["kind"] == "unavailable" for w in agent_context["omissions"])


def test_dispatch_brief_is_preserved_and_read_in_both_prompt_modes(case_repo):
    from evander_team_addon.services.case_context_dispatch import (
        prepare_case_context,
        dispatch_case_contexts,
    )
    from evander_team_addon.services.read_service import TeamReadService
    from evander_team_addon.services.wake_prompt_context import build_compact_wake_context

    record(case_repo, kind="decision")
    case_repo.update(lambda s: prepare_case_context(case_repo, s, s.dispatches["dispatch-command"]))
    state = case_repo.read()
    contexts = dispatch_case_contexts(state, "dispatch-command")
    assert contexts and "Choose additive" in contexts[0]["text"]
    read = TeamReadService(state=state, actor=state.agents["worker"])
    assert read.get_current_dispatch_context()["case_contexts"] == contexts
    assert read.get_current_dispatch_context(detail="summary")["case_contexts"] == contexts
    for fresh in [True, False]:
        compact = build_compact_wake_context({"case_contexts": contexts}, fresh_context=fresh)
        assert compact["case_contexts"] == contexts


@pytest.mark.parametrize("status", ["resolved", "closed"])
@pytest.mark.parametrize("review_target", ["deliverable", "release"])
def test_review_dispatch_preserves_terminal_case_context(case_repo, status, review_target):
    from evander_team_addon.models import Deliverable, ReleasePackage, ReviewRequest, TeamCaseStatus
    from evander_team_addon.services.case_context_dispatch import (
        dispatch_case_contexts,
        prepare_case_context,
    )
    from evander_team_addon.services.domain.wake import _dispatch_case_payloads

    record(case_repo, kind="decision")

    def seed(state):
        state.cases["case"].status = TeamCaseStatus(status)
        state.cases["unrelated"] = TeamCase(
            id="unrelated",
            team_id="team",
            project_id="project",
            title="Unrelated closed case",
            owner_agent_id="lead",
            status=TeamCaseStatus.CLOSED,
        )
        state.deliverables["delivery"] = Deliverable(
            id="delivery", task_id="task", producer_agent_id="worker", summary="Delivered"
        )
        state.tasks["task"].deliverable_ids = ["delivery"]
        target = {"deliverable_id": "delivery"}
        if review_target == "release":
            state.release_packages["release"] = ReleasePackage(
                id="release",
                project_id="project",
                deliverable_ids=["delivery"],
                summary="Release",
                candidate_commit_sha="1" * 40,
            )
            target = {"release_package_id": "release", "candidate_commit_sha": "1" * 40}
        state.review_requests["review"] = ReviewRequest(
            id="review", reviewer_agent_id="lead", **target
        )

    case_repo.update(seed)
    _lease_agent_dispatch(case_repo, agent_id="lead", dispatch_id="review-dispatch")
    case_repo.update(lambda s: prepare_case_context(case_repo, s, s.dispatches["review-dispatch"]))
    state = case_repo.read()
    dispatch = state.dispatches["review-dispatch"]
    assert not dispatch.task_ids and dispatch.review_request_ids == ["review"]
    contexts = dispatch_case_contexts(state, dispatch.id)
    assert len(contexts) == 1
    assert contexts[0]["case_ids"] == ["case"]
    assert "Choose additive compatibility" in contexts[0]["text"]
    snapshot = state.case_context_snapshots[contexts[0]["snapshot_id"]]
    assert snapshot.view.view_id == "review-brief"
    # Closed cases stay out of the active work/index projection.
    active = _dispatch_case_payloads(state, agent=state.agents["lead"], dispatch=dispatch)
    assert active["cases"] == [] and active["case_index"] == []
    # If capture fails, review-only wakes must expose the retrieval fallback too.
    state.case_context_snapshots.clear()
    assert dispatch_case_contexts(state, dispatch.id)[0]["omissions"] == [
        {"kind": "unavailable", "section": "case_context"}
    ]


def test_case_history_includes_reviews_verification_and_release_focus(case_repo):
    from evander_team_addon.models import (
        Deliverable,
        ReleasePackage,
        ReviewRequest,
        ReleaseVerificationRun,
    )

    def seed(state):
        state.deliverables["delivery"] = Deliverable(
            id="delivery",
            task_id="task",
            producer_agent_id="worker",
            summary="Compatibility delivered",
            review_request_ids=["review"],
        )
        state.tasks["task"].deliverable_ids = ["delivery"]
        state.release_packages["release"] = ReleasePackage(
            id="release",
            project_id="project",
            deliverable_ids=["delivery"],
            summary="Delivery candidate",
            candidate_commit_sha="1" * 40,
        )
        state.review_requests["review"] = ReviewRequest(
            id="review",
            reviewer_agent_id="lead",
            deliverable_id="delivery",
        )
        state.release_verification_runs["verification"] = ReleaseVerificationRun(
            id="verification",
            release_package_id="release",
            candidate_commit_sha="1" * 40,
            session_id="verifier",
        )

    case_repo.update(seed)
    service = CaseContextService(case_repo)
    case = service.assemble(case_ids=["case"])
    assert any(s["records"] for s in case["sections"] if s["name"] == "review")
    release = service.assemble(release_package_id="release")
    assert "Compatibility delivered" in release["text"]
    with pytest.raises(ValueError, match="candidate SHA"):
        service.create_snapshot(
            request_id="wrong-candidate",
            release_package_id="release",
            candidate_commit_sha="2" * 40,
        )


@pytest.fixture
def scoped_release_repo(case_repo):
    from evander_team_addon.models import (
        Deliverable,
        HumanReviewDecision,
        HumanReviewFinding,
        HumanReviewRequest,
        ReleasePackage,
        ReleaseVerificationRun,
        ReviewRequest,
    )
    from evander_team_addon.services.admin_service import TeamAdminService

    TeamAdminService(case_repo).create_project(
        project_id="private-project",
        team_id="team",
        name="Private project",
        lead_agent_id="private-lead",
        lead_session_id="session-private-lead",
        directory_path="root/b",
    )

    def seed(state):
        state.projects["project"].directory_path = "root/a"
        state.cases["private-case"] = state.cases["case"].model_copy(
            update={
                "id": "private-case",
                "project_id": "private-project",
                "task_ids": ["private-task"],
            }
        )
        state.tasks["private-task"] = state.tasks["task"].model_copy(
            update={
                "id": "private-task",
                "project_id": "private-project",
                "case_id": "private-case",
                "owner_agent_id": "private-lead",
                "created_by_agent_id": "private-lead",
            }
        )
        for prefix, project, task in (
            ("", "project", "task"),
            ("private-", "private-project", "private-task"),
        ):
            text = "OUT_OF_SCOPE_EVIDENCE" if prefix else "Authorized review evidence"
            state.deliverables[prefix + "delivery"] = Deliverable(
                id=prefix + "delivery", task_id=task, producer_agent_id="worker", summary=text
            )
            state.tasks[task].deliverable_ids = [prefix + "delivery"]
            state.release_packages[prefix + "release"] = ReleasePackage(
                id=prefix + "release",
                project_id=project,
                deliverable_ids=[prefix + "delivery"],
                summary=text,
                candidate_commit_sha="1" * 40,
                candidate_tree_hash="2" * 40,
            )
            for target in ("release", "delivery"):
                values = (
                    {"release_package_id": prefix + "release", "candidate_commit_sha": "1" * 40}
                    if target == "release"
                    else {"deliverable_id": prefix + "delivery"}
                )
                state.review_requests[prefix + target + "-review"] = ReviewRequest(
                    id=prefix + target + "-review",
                    reviewer_agent_id="lead",
                    findings=[text],
                    **values,
                )
            state.release_verification_runs[prefix + "verification"] = ReleaseVerificationRun(
                id=prefix + "verification",
                release_package_id=prefix + "release",
                candidate_commit_sha="1" * 40,
                session_id="verifier",
                error=text,
            )
            state.human_review_requests[prefix + "human"] = HumanReviewRequest(
                id=prefix + "human",
                release_package_id=prefix + "release",
                candidate_commit_sha="1" * 40,
                candidate_tree_hash="2" * 40,
                state="preparing",
                requirement_snapshot={
                    "directory_path": state.projects[project].directory_path,
                    "configured_group_ids": ["reviewers"],
                    "initially_eligible_user_ids": [],
                    "test_instructions": text,
                },
            )
            state.human_review_decisions[prefix + "decision"] = HumanReviewDecision(
                id=prefix + "decision",
                human_review_request_id=prefix + "human",
                reviewer_user_id="operator",
                reviewer_display_name_snapshot="Operator",
                decision="blocked",
                request_id=prefix + "decision",
                summary=text,
            )
            state.human_review_findings[prefix + "finding"] = HumanReviewFinding(
                id=prefix + "finding",
                source_human_review_request_id=prefix + "human",
                text=text,
                correction_task_id=task,
            )

    case_repo.update(seed)
    return case_repo


def test_operator_context_rejects_cross_directory_release_and_snapshot(scoped_release_repo):
    import asyncio
    import httpx
    from evander.addons import AddonContext
    from evander_team_addon.api import admin_api_routes
    from starlette.applications import Starlette
    from test_team_external_source_evidence import _PrivilegedAuth

    repo = scoped_release_repo

    class DirectoryOwnerAuth(_PrivilegedAuth):
        @staticmethod
        def can(_request, permission, directory_path=None):
            return permission == "admin.owner" and directory_path == "root/a"

    async def exercise():
        app = Starlette(
            routes=admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=repo.runtime_dir,
                    addon_runtime_dir=repo.runtime_dir / "addons/team-addon",
                    auth=DirectoryOwnerAuth(),
                )
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            direct = await client.get("/cases/private-case/context")
            assert direct.status_code == 403
            before = repo.read().revision
            response = await client.get("/cases/case/context?release_package_id=private-release")
            assert response.status_code == 403, response.text
            recipe = {"view_id": "preview", "title": "Preview", "sections": [{"name": "review"}]}
            preview = await client.post(
                "/case-context/preview",
                json={
                    "case_id": "case",
                    "release_package_id": "private-release",
                    "view": recipe,
                },
            )
            assert preview.status_code == 403, preview.text
            frozen = await client.post(
                "/case-context/snapshots",
                json={
                    "case_id": "case",
                    "release_package_id": "private-release",
                    "request_id": "denied",
                    "expected_revision": before,
                },
            )
            assert frozen.status_code == 403, frozen.text
            assert repo.read().revision == before
            assert not repo.read().case_context_snapshots
            for denied in (response, preview, frozen):
                assert "OUT_OF_SCOPE_EVIDENCE" not in denied.text
            allowed = await client.get("/cases/case/context?release_package_id=release")
            assert allowed.status_code == 200, allowed.text
            assert "Authorized review evidence" in allowed.text
            saved = await client.post(
                "/case-context/snapshots",
                json={
                    "case_id": "case",
                    "release_package_id": "release",
                    "request_id": "allowed",
                    "expected_revision": before,
                },
            )
            assert saved.status_code == 201, saved.text
            detail = await client.get(
                f"/case-context/snapshots/{saved.json()['snapshot_id']}?section=review"
            )
            assert detail.status_code == 200, detail.text
            assert "Authorized review evidence" in detail.text
            assert "OUT_OF_SCOPE_EVIDENCE" not in detail.text

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["query", "snapshot"])
def test_workflow_context_rejects_cross_directory_release(scoped_release_repo, operation):
    from types import SimpleNamespace
    from evander_team_addon.case_context_providers import (
        CaseContextQueryDefinition,
        CaseContextQueryProvider,
        CaseContextSnapshotEffectDefinition,
        CaseContextSnapshotEffectProvider,
    )

    repo = scoped_release_repo
    runtime = SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=repo.runtime_dir),
        metadata={"directory_path": "root/a"},
    )
    selection = dict(
        id="context", display_name="Context", case_id="case", release_package_id="private-release"
    )
    if operation == "query":
        provider = CaseContextQueryProvider()
        definition = CaseContextQueryDefinition(type="query:team:case-context", **selection)
    else:
        provider = CaseContextSnapshotEffectProvider()
        definition = CaseContextSnapshotEffectDefinition(
            type="effect:team:snapshot-case-context", request_id="workflow", **selection
        )
    provider.initialize(definition, runtime)
    before = repo.read().revision
    with pytest.raises(PermissionError, match="release context"):
        provider.execute()
    assert repo.read().revision == before
    assert not repo.read().case_context_snapshots
    allowed = provider.execute(resolved_config={"release_package_id": "release"})
    assert allowed["success"]
    assert "Authorized review evidence" in allowed["text"]
    assert "OUT_OF_SCOPE_EVIDENCE" not in allowed["text"]


@pytest.mark.parametrize("link", ["predecessor", "implicit"])
@pytest.mark.parametrize("actor_id", [None, "worker"])
def test_context_filters_related_release_evidence_before_collection(
    scoped_release_repo, link, actor_id
):
    from evander_team_addon.models import ReleaseCandidateManifest

    repo = scoped_release_repo

    def link_private_release(state):
        if link == "predecessor":
            state.release_packages["release"].manifest = ReleaseCandidateManifest(
                project_id="project",
                release_package_id="release",
                candidate_commit_sha="1" * 40,
                release_branch="release/test",
                deliverables=[{"deliverable_id": "delivery"}],
                predecessor_release_package_id="private-release",
            )
        else:
            state.tasks["task"].release_base_package_id = "private-release"

    repo.update(link_private_release)
    service = CaseContextService(repo)
    scope = {"case_ids": ["case"], "directory_path": "root/a", "actor_id": actor_id}
    if link == "predecessor":
        scope["release_package_id"] = "release"
    context = service.assemble(**scope)
    snapshot = service.create_snapshot(request_id="scoped", **scope)
    assert "OUT_OF_SCOPE_EVIDENCE" not in str(context)
    assert "OUT_OF_SCOPE_EVIDENCE" not in str(snapshot.evidence)
    assert not context["complete"]
    assert any(w["kind"] == "restricted" for w in context["omissions"])
    assert {r["id"] for r in snapshot.evidence["review"]} >= {"release-review", "delivery-review"}
    assert {r["id"] for r in snapshot.evidence["verification"]} == {"release", "verification"}
    if actor_id is None:
        assert {r["id"] for r in snapshot.evidence["review"]} >= {"human", "decision", "finding"}
    for section in ("review", "verification"):
        page = service.evidence_page(
            section=section, **{k: v for k, v in scope.items() if k != "release_package_id"}
        )
        assert "OUT_OF_SCOPE_EVIDENCE" not in str(page)


@pytest.mark.parametrize("cycle", [False, True])
@pytest.mark.parametrize("entry", ["task", "dependency", "ancestor"])
def test_task_context_follows_contributed_child_history_without_release(case_repo, cycle, entry):
    from evander_team_addon.models import Deliverable

    def seed(state):
        for task_id in ("task", "child", "grandchild", "sibling"):
            if task_id != "task":
                state.tasks[task_id] = state.tasks["task"].model_copy(
                    update={
                        "id": task_id,
                        "parent_task_id": "task",
                        "description": f"Plan for {task_id}",
                    }
                )
                state.cases["case"].task_ids.append(task_id)
                note = make_note(
                    state,
                    note_id=task_id + "-note",
                    agent_id="worker",
                    dispatch_id="dispatch-command",
                    body=f"Decision for {task_id}",
                    kind="decision",
                    task_ids=[task_id],
                )
                state.notes[note.id] = note
            state.tasks[task_id].deliverable_ids = [task_id + "-delivery"]
            sources = {"task": ["child-delivery"], "child": ["grandchild-delivery"]}.get(
                task_id, []
            )
            if task_id == "grandchild" and cycle:
                sources = ["task-delivery"]
            state.deliverables[task_id + "-delivery"] = Deliverable(
                id=task_id + "-delivery",
                task_id=task_id,
                producer_agent_id="worker",
                summary=f"Work by {task_id}",
                source_deliverable_ids=sources,
                artifact_refs=[task_id + "-plan"],
            )
        if entry != "task":
            state.tasks["selected"] = state.tasks["task"].model_copy(
                update={
                    "id": "selected",
                    "deliverable_ids": [],
                    "parent_task_id": "task" if entry == "ancestor" else None,
                    "dependency_task_ids": ["task"] if entry == "dependency" else [],
                }
            )
            state.cases["case"].task_ids.append("selected")

    case_repo.update(seed)
    service = CaseContextService(
        case_repo, artifact_reader=lambda ref, _item: {"id": ref, "text": f"Artifact {ref}"}
    )
    selected_task_id = "task" if entry == "task" else "selected"
    context = service.assemble(task_id=selected_task_id)
    snapshot = service.create_snapshot(request_id="parent-task", task_id=selected_task_id)
    for task_id in ("child", "grandchild"):
        assert f"Decision for {task_id}" in context["text"]
        assert f"Plan for {task_id}" in context["text"]
    expected_plans = {
        "task",
        "child",
        "grandchild",
        "task-plan",
        "child-plan",
        "grandchild-plan",
    }
    if entry != "task":
        expected_plans.add("selected")
    assert {r["id"] for r in snapshot.evidence["plans"]} == expected_plans
    assert {r["id"] for r in snapshot.evidence["delivery"]} == {
        "task-delivery",
        "child-delivery",
        "grandchild-delivery",
    }
    assert {r["id"] for r in snapshot.evidence["decisions"]} == {"child-note", "grandchild-note"}
    assert context["complete"] and snapshot.context["complete"]
    assert not case_repo.read().release_packages


def test_explicit_release_context_keeps_delivery_pinned(scoped_release_repo):
    from evander_team_addon.models import Deliverable

    def seed(state):
        state.deliverables["later-delivery"] = Deliverable(
            id="later-delivery",
            task_id="task",
            producer_agent_id="worker",
            summary="Later work outside the selected release",
            artifact_refs=["later-plan"],
        )
        state.tasks["task"].deliverable_ids.append("later-delivery")

    scoped_release_repo.update(seed)
    service = CaseContextService(
        scoped_release_repo, artifact_reader=lambda ref, _item: {"id": ref, "text": "Plan"}
    )
    snapshot = service.create_snapshot(
        request_id="pinned", release_package_id="release", directory_path="root/a"
    )
    assert {r["id"] for r in snapshot.evidence["delivery"]} == {"delivery"}
    assert {r["id"] for r in snapshot.evidence["plans"]} == {"task", "later-plan"}


def test_operator_context_api_preview_history_snapshot_and_permissions(case_repo):
    import asyncio
    import httpx
    from evander.addons import AddonContext
    from evander_team_addon.api import admin_api_routes
    from starlette.applications import Starlette
    from test_team_external_source_evidence import _PrivilegedAuth, _ViewerAuth

    async def exercise(auth):
        app = Starlette(
            routes=admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=case_repo.runtime_dir,
                    addon_runtime_dir=case_repo.runtime_dir / "addons/team-addon",
                    auth=auth,
                )
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/cases/case/context")
            if isinstance(auth, _ViewerAuth):
                assert response.status_code == 403
                return
            assert response.status_code == 200, response.text
            views = (await client.get("/case-context/views")).json()
            recipe = {
                "view_id": "customer",
                "title": "Customer",
                "sections": [{"name": "request"}],
                "text_budget": 1000,
            }
            before = case_repo.read().revision
            preview = await client.post(
                "/case-context/preview", json={"case_id": "case", "view": recipe}
            )
            assert preview.status_code == 200, preview.text
            assert case_repo.read().revision == before
            saved = await client.put(
                "/case-context/views", json={"view": recipe, "expected_revision": views["revision"]}
            )
            assert saved.status_code == 200, saved.text
            stale = await client.put(
                "/case-context/views", json={"view": recipe, "expected_revision": views["revision"]}
            )
            assert stale.status_code == 409
            frozen = await client.post(
                "/case-context/snapshots",
                json={
                    "case_id": "case",
                    "request_id": "test",
                    "expected_revision": case_repo.read().revision,
                },
            )
            assert frozen.status_code == 201, frozen.text
            snapshot_id = frozen.json()["snapshot_id"]
            detail = await client.get(f"/case-context/snapshots/{snapshot_id}?section=request")
            assert detail.status_code == 200 and detail.json()["evidence"]
            live_detail = await client.get("/cases/case/context/evidence?section=request")
            assert live_detail.status_code == 200 and live_detail.json()["records"]

    asyncio.run(exercise(_PrivilegedAuth()))
    asyncio.run(exercise(_ViewerAuth()))


@pytest.mark.parametrize("text", ["decision \\" * 10000, "選択😀" * 10000])
def test_total_wake_budget_includes_metadata_escaping_and_all_cases(text):
    import json
    from evander_team_addon.services.case_context_prompt import compact_case_contexts
    from evander_team_addon.services.wake_prompt_context import build_compact_wake_context

    oversized = [
        {
            "text": text,
            "snapshot_id": "snapshot",
            "complete": True,
            "case_ids": [f"case-{i}" for i in range(100)],
            "omissions": [{"kind": "truncated", "id": str(i)} for i in range(5000)],
            "read": {"tool": "team_get_case_context_snapshot", "snapshot_id": "snapshot"},
        }
    ] * 5
    bounded = compact_case_contexts(oversized)
    assert len(json.dumps({"case_contexts": bounded}, sort_keys=True, indent=2)) <= 6000
    assert bounded[0]["complete"] is False and bounded[0]["read"]["snapshot_id"] == "snapshot"
    assert compact_case_contexts(bounded) == bounded
    for fresh in [True, False]:
        assert (
            build_compact_wake_context({"case_contexts": oversized}, fresh_context=fresh)[
                "case_contexts"
            ]
            == bounded
        )
    assert "case_contexts" not in build_compact_wake_context({}, fresh_context=True)


def test_ado_comment_pagination_preserves_edits_and_reports_limits():
    import httpx
    from evander.issues.service import AzureDevOpsIssueAdapter

    adapter = AzureDevOpsIssueAdapter.__new__(AzureDevOpsIssueAdapter)
    adapter.comments_max_count = 1
    requests = []

    def handle(request):
        requests.append(request)
        token = request.url.params.get("continuationToken")
        body = {
            "comments": [
                {
                    "id": 2 if not token else 1,
                    "text": "Clarification",
                    "version": 3,
                    "modifiedDate": "2026-09-15T00:00:00Z",
                }
            ]
        }
        if not token:
            body["continuationToken"] = "page-2"
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        paging = {}
        values = dict(
            organization_url="https://dev.azure.com/org",
            project="Project",
            auth_header="test",
            remote_issue_id="42",
            paging=paging,
        )
        result = adapter._fetch_comments(client, **values, page_limit=10)
        assert [r["id"] for r in result] == ["2", "1"]
        assert result[0]["version"] == 3 and result[0]["modified_at"]
        assert paging["complete"] and paging["pages"] == 2
        adapter._fetch_comments(client, **values, page_limit=1)
        assert not paging["complete"] and paging["has_more"]


def test_case_source_refresh_is_bound_retryable_and_non_gating(case_repo, monkeypatch):
    from types import SimpleNamespace
    from evander.issues.service import IssueService
    from evander_team_addon.case_context_providers import (
        RefreshCaseSourcesDefinition,
        RefreshCaseSourcesProvider,
        ado_case_binding,
    )

    def seed(state):
        state.cases["case"].source = "azure_devops"
        state.cases["case"].source_ref = "ado:org:Project:42"

    case_repo.update(seed)
    monkeypatch.setattr(IssueService, "__init__", lambda self, resolver: None)
    item = {
        "id": "42",
        "project": "Project",
        "organization_url": "https://dev.azure.com/org",
        "rev": 1,
        "title": "Request",
        "comments": [],
        "comments_paging": {"complete": True},
    }
    monkeypatch.setattr(IssueService, "get_remote_backlog_item_by_id", lambda self, **kwargs: item)
    provider = RefreshCaseSourcesProvider()
    provider.initialize(
        RefreshCaseSourcesDefinition(
            id="refresh",
            display_name="Refresh",
            type="effect:team:refresh-case-sources",
            case_ids=["case"],
        ),
        SimpleNamespace(
            resolved_config=SimpleNamespace(runtime_dir=case_repo.runtime_dir),
            metadata={"directory_path": "root"},
            provider_config_resolver=None,
        ),
    )
    assert provider.execute()["captures"][0]["capture_status"] == "captured"
    provider.execute()
    assert len(case_repo.read().external_source_evidence_snapshots) == 1
    item["organization_url"] = "https://dev.azure.com/wrong-org"
    assert provider.execute()["captures"][0]["capture_status"] == "unavailable"
    assert len(case_repo.read().external_source_evidence_snapshots) == 1
    assert any(
        w["kind"] == "capture_unavailable"
        for w in CaseContextService(case_repo).assemble(case_ids=["case"])["omissions"]
    )
    assert ado_case_binding("https://dev.azure.com/org/My%20Project/_workitems/edit/42") == (
        "org",
        "My Project",
        "42",
    )
    with pytest.raises(ValueError):
        ado_case_binding("https://attacker.example/org/Project/_workitems/edit/42")


def test_deliverable_links_context_without_requiring_notes(case_repo):
    from evander_team_addon.services.commands.deliverable_submission_commands import (
        TeamDeliverableSubmissionCommandService,
    )
    from evander_team_addon.services.case_context_dispatch import prepare_case_context

    record(case_repo, kind="decision")
    case_repo.update(lambda s: prepare_case_context(case_repo, s, s.dispatches["dispatch-command"]))
    state, result = TeamDeliverableSubmissionCommandService(case_repo).submit_deliverable_command(
        session_id="session-worker",
        id="delivery",
        task_id="task",
        summary="Completed",
        artifact_refs=["artifact-space:/teams/team/report"],
        request_id="delivery",
    )
    assert result["success"], result
    assert state.deliverables["delivery"].case_note_ids == list(state.notes)
    assert state.deliverables["delivery"].context_snapshot_ids == list(state.case_context_snapshots)
    assert case_repo.read().deliverables["delivery"] == state.deliverables["delivery"]


def test_artifact_plan_is_captured_with_provenance_and_scope(case_repo):
    from evander.artifact_spaces import ArtifactSpaceStore
    from evander.published_artifacts import PublishedArtifactStore
    from evander_team_addon.models import Deliverable

    path = "/teams/team/plans/case"
    key = "team/team/project/plan/bundle"
    store = PublishedArtifactStore(case_repo.runtime_dir)
    metadata = store.publish_single_file(
        key=key,
        session_id="session-worker",
        filename="plan.md",
        artifact_path="plan.md",
        content=b"Implementation plan: retain compatibility.",
    )
    spaces = ArtifactSpaceStore(case_repo.runtime_dir)
    spaces.ensure_space(
        path=path, metadata={"team_id": "team", "source": "team_addon"}, subject="team-agent:worker"
    )
    spaces.publish_version(
        space_path=path,
        artifact_id="bundle",
        artifact_key=key,
        created_by_subject="team-agent:worker",
    )

    def seed(state):
        state.tasks["task"].deliverable_ids = ["plan"]
        state.deliverables["plan"] = Deliverable(
            id="plan",
            task_id="task",
            producer_agent_id="worker",
            summary="Plan",
            artifact_refs=["artifact-space:" + path],
        )

    case_repo.update(seed)
    snapshot = CaseContextService(case_repo).create_snapshot(request_id="plan", case_ids=["case"])
    artifact = next(r for r in snapshot.evidence["plans"] if r["entity_type"] == "artifact")
    assert artifact["content"]["archive_sha256"] == metadata.sha256
    assert "retain compatibility" in artifact["content"]["documents"][0]["content"]


def test_detached_mcp_snapshot_grant_and_expired_session_policy(tmp_path):
    from datetime import UTC, datetime, timedelta
    from evander.gateway import create_server
    from evander_team_addon.case_context_mcp import CASE_CONTEXT_TOOLS
    from test_team_addon_mcp_read_tools import (
        _runtime,
        _seed_read_tool_state,
        _upsert_session,
        _call_tool,
    )

    context = _runtime(tmp_path, allowed_tools=list(CASE_CONTEXT_TOOLS))
    repo = _seed_read_tool_state(context.config.runtime_dir)

    def seed(state):
        state.cases["context-case"] = TeamCase(
            id="context-case", team_id="alpha", project_id="alpha-project", title="Alpha request"
        )

    repo.update(seed)
    _upsert_session(
        context, session_id="packager", allowed_tools=["team_get_case_context_snapshot"]
    )
    _upsert_session(context, session_id="other", allowed_tools=["team_get_case_context_snapshot"])
    snapshot = CaseContextService(repo).create_snapshot(
        request_id="release", case_ids=["context-case"], recipient_session_id="packager"
    )
    server = create_server(context)
    result = _call_tool(
        server,
        "team_get_case_context_snapshot",
        {"session_id": "packager", "snapshot_id": snapshot.id},
    )
    assert result["success"] and "Alpha request" in result["context"]["text"]
    with pytest.raises(Exception, match="not granted"):
        _call_tool(
            server,
            "team_get_case_context_snapshot",
            {"session_id": "other", "snapshot_id": snapshot.id},
        )
    session = context.session_repository.get_session("packager")
    context.session_repository.upsert_session(
        session.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    )
    with pytest.raises(Exception, match="[Ee]xpired"):
        _call_tool(
            server,
            "team_get_case_context_snapshot",
            {"session_id": "packager", "snapshot_id": snapshot.id},
        )


def test_resumed_dispatch_gets_only_changes_and_fresh_context_gets_baseline(case_repo):
    from datetime import UTC, datetime, timedelta
    from evander_team_addon.models import TeamDispatchStatus, AgentContextClearingPolicy
    from evander_team_addon.services.case_context_dispatch import (
        prepare_case_context,
        dispatch_case_contexts,
    )
    from evander_team_addon.services.wake_prompt_context import build_compact_wake_context

    record(case_repo, kind="decision")
    case_repo.update(lambda s: prepare_case_context(case_repo, s, s.dispatches["dispatch-command"]))
    first_id = next(iter(case_repo.read().case_context_snapshots))

    def next_dispatch(state):
        previous = state.dispatches["dispatch-command"]
        previous.status = TeamDispatchStatus.COMPLETED
        previous.completed_at = datetime.now(UTC) - timedelta(seconds=1)
        previous.native_thread_id = "retained-thread"
        runtime = state.agent_runtimes["worker"]
        runtime.last_thread_id = "retained-thread"
        runtime.context_clearing_policy = AgentContextClearingPolicy.OFF
        state.dispatches["next"] = previous.model_copy(
            update={
                "id": "next",
                "status": TeamDispatchStatus.LEASED,
                "created_at": datetime.now(UTC),
                "completed_at": None,
                "native_thread_id": None,
            }
        )
        runtime.active_dispatch_id = "next"
        prepare_case_context(case_repo, state, state.dispatches["next"])

    case_repo.update(next_dispatch)
    state = case_repo.read()
    delta = dispatch_case_contexts(state, "next", delta=True)
    assert delta[0]["baseline_snapshot_id"] == first_id
    assert delta[0]["changed_record_count"] == 0 and "unchanged" in delta[0]["text"]
    assert "Choose additive" not in delta[0]["text"]
    lease = {"case_contexts": dispatch_case_contexts(state, "next"), "case_contexts_delta": delta}
    assert (
        "Choose additive"
        in build_compact_wake_context(lease, fresh_context=True)["case_contexts"][0]["text"]
    )
    assert (
        "unchanged"
        in build_compact_wake_context(lease, fresh_context=False)["case_contexts"][0]["text"]
    )

    def changed(state):
        state.notes["new-decision"] = make_note(
            state,
            note_id="new-decision",
            agent_id="worker",
            dispatch_id="next",
            kind="decision",
            body="New decision: add an opt-in flag.",
        )

    case_repo.update(changed)
    service = CaseContextService(case_repo)
    snapshot = service.create_snapshot(
        request_id="changed",
        case_ids=["case"],
        actor_id="worker",
        recipient_session_id="session-worker",
        view_id="task-brief",
        baseline_snapshot_id=first_id,
    )
    assert snapshot.wake_delta_context["changed_record_count"] == 1
    assert "New decision" in snapshot.wake_delta_context["text"]
    assert "Choose additive" not in snapshot.wake_delta_context["text"]
    assert (
        "Choose additive"
        in service.get_snapshot(snapshot.id, session_id="session-worker")["context"]["text"]
    )


def test_delta_baseline_excludes_failed_dispatches_thread_switches_and_resets(case_repo):
    from datetime import UTC, datetime, timedelta
    from evander_team_addon.models import TeamDispatchStatus, AgentContextClearingPolicy
    from evander_team_addon.services.case_context_dispatch import (
        prepare_case_context,
        previous_context_snapshot,
    )

    case_repo.update(lambda s: prepare_case_context(case_repo, s, s.dispatches["dispatch-command"]))
    state = case_repo.read()
    previous = state.dispatches["dispatch-command"]
    previous.completed_at = datetime.now(UTC) - timedelta(seconds=1)
    previous.native_thread_id = "thread"
    previous.status = TeamDispatchStatus.FAILED
    current = previous.model_copy(update={"id": "new", "created_at": datetime.now(UTC)})
    runtime = state.agent_runtimes["worker"]
    runtime.last_thread_id = "thread"
    assert previous_context_snapshot(state, current) is None
    previous.status = TeamDispatchStatus.COMPLETED
    assert previous_context_snapshot(state, current)
    runtime.last_thread_id = "other-thread"
    assert previous_context_snapshot(state, current) is None
    runtime.last_thread_id = "thread"
    runtime.context_rotation_pending = True
    assert previous_context_snapshot(state, current) is None
    runtime.context_rotation_pending = False
    runtime.context_clearing_policy = AgentContextClearingPolicy.WORK_LINEAGE
    current.work_lineage_id = "new-lineage"
    runtime.work_lineage_thread_ids = {"old-lineage": "thread"}
    assert previous_context_snapshot(state, current) is None


def test_context_example_catalog_and_static_asset_are_available(tmp_path):
    import asyncio
    from pathlib import Path
    import httpx
    from starlette.applications import Starlette
    from evander.addons import AddonContext
    from evander.workflow.packs import load_workflow_pack
    from evander_team_addon.case_context_providers import CONTEXT_DEFINITIONS, materialize
    from evander_team_addon.ui import admin_ui_routes

    root = Path(__file__).resolve().parents[1]
    pack = load_workflow_pack(root, "case-context.v1-example.json")
    for spec in pack.ir.nodes:
        if spec.node.type in CONTEXT_DEFINITIONS:
            assert materialize(CONTEXT_DEFINITIONS[spec.node.type], spec.node)

    async def exercise():
        context = AddonContext(
            addon_id="team-addon", runtime_dir=tmp_path, addon_runtime_dir=tmp_path / "addon"
        )
        app = Starlette(routes=admin_ui_routes(context))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/team-case-context.js")
            assert response.status_code == 200 and "createCaseContextController" in response.text

    asyncio.run(exercise())


def test_delta_reports_supersession_and_removed_records_without_old_bodies(case_repo):
    state, result = record(case_repo, kind="decision")
    service = CaseContextService(case_repo)
    baseline = service.create_snapshot(
        request_id="baseline",
        case_ids=["case"],
        actor_id="worker",
        recipient_session_id="session-worker",
    )

    def supersede(state):
        note = make_note(
            state,
            note_id="replacement",
            agent_id="worker",
            dispatch_id="dispatch-command",
            body="Use the revised design",
            supersedes_note_id=result["note_id"],
        )
        state.notes[note.id] = note

    case_repo.update(supersede)
    changed = service.create_snapshot(
        request_id="changed",
        case_ids=["case"],
        actor_id="worker",
        recipient_session_id="session-worker",
        baseline_snapshot_id=baseline.id,
    )
    assert changed.wake_delta_context["changed_record_count"] == 2
    assert "superseded" in changed.wake_delta_context["text"]
    # Scope/filter contraction reports disappearance but cannot reintroduce prior text.
    from evander_team_addon.services.case_context_service import delta_context

    empty = {name: [] for name in changed.evidence}
    delta = delta_context(empty, changed, view=changed.view, revision=1, case_ids=[], warnings=[])
    assert delta["removed_record_counts"]["decisions"] == 2
    assert "revised design" not in str(delta)
