from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from config_helpers import write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.models import SessionManifest
from evander.policy import PolicyError
from evander.queries.providers.base import QueryRuntimeContext
from evander_azure_devops_addon.delivery import (
    ApprovedMergeEffect,
    ClaimRepairEffect,
    ClaimStartEffect,
    DeliveryDefinition,
    DeliveryStatusQuery,
    MergeApprovalEffect,
    RecordPublicationEffect,
)

SHA = "a" * 40
BASE = "b" * 40
MERGE = "c" * 40


@pytest.fixture
def environment(tmp_path, monkeypatch):
    config = GatewayConfig(
        runtime_dir=tmp_path / "runtime",
        ado_organization="example",
        azure_devops_project="Project",
        azure_devops_pr_pat_env="TEST_ADO_PR",
        azure_devops_pat_env="TEST_ADO_BUILD",
    )
    admin = build_admin_runtime(write_runtime_config(tmp_path))
    admin.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="coder",
            vm_id="vm-coder",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    monkeypatch.setattr(
        admin.managed_git_admin,
        "get_repository",
        lambda *a, **kw: {
            "provider": "azure_devops",
            "canonical_repository_url": "https://dev.azure.com/example/Project/_git/repo",
        },
    )
    publication = dict(
        managed_repo_id="mr_demo",
        pr_id="17",
        commit_sha=SHA,
        source_branch="feature/delivery",
        target_branch="main",
        build_id="20",
        pipeline_id="3",
    )
    resolver = SimpleNamespace(config=config, resolve_for_directory=lambda d: config)
    contexts = dict(
        provider_config_resolver=resolver,
        resolved_config=config,
        admin_service=admin,
        env={"TEST_ADO_PR": "fixture-token", "TEST_ADO_BUILD": "fixture-build-token"},
        metadata={"directory_path": "root", "graph_id": "delivery"},
    )
    state = {
        "pr": {
            "pullRequestId": 17,
            "status": "active",
            "isDraft": False,
            "sourceRefName": "refs/heads/feature/delivery",
            "targetRefName": "refs/heads/main",
            "lastMergeSourceCommit": {"commitId": SHA},
            "lastMergeTargetCommit": {"commitId": BASE},
            "repository": {"id": "repo-id", "project": {"id": "project-id"}},
            "createdBy": {"id": "author"},
            "reviewers": [{"id": "reviewer", "vote": 10}],
        },
        "build": {
            "id": 20,
            "status": "completed",
            "result": "succeeded",
            "sourceVersion": SHA,
            "sourceBranch": "refs/heads/feature/delivery",
            "definition": {"id": 3},
            "repository": {"id": "repo-id"},
        },
        "policies": [
            {"status": "approved", "configuration": {"isEnabled": True, "isBlocking": True}}
        ],
        "threads": [],
        "writes": [],
        "merge_visible": True,
    }

    def handle(request):
        if request.method == "PATCH":
            import json

            state["writes"].append(json.loads(request.content))
            state["pr"].update(status="completed", lastMergeCommit={"commitId": MERGE})
            return httpx.Response(200, json=state["pr"])
        path = request.url.path
        import base64

        expected_token = "fixture-build-token" if "/build/" in path else "fixture-token"
        assert (
            request.headers["authorization"]
            == "Basic " + base64.b64encode((":" + expected_token).encode()).decode()
        )
        if path.endswith("/pullRequests/17"):
            return httpx.Response(200, json=copy.deepcopy(state["pr"]))
        if path.endswith("/builds/20"):
            return httpx.Response(200, json=state["build"])
        if path.endswith("/evaluations"):
            return httpx.Response(200, json={"value": state["policies"]})
        if path.endswith("/threads"):
            return httpx.Response(200, json={"value": state["threads"]})
        if path.endswith("/refs"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "name": "refs/heads/main",
                            "objectId": MERGE if state["merge_visible"] else BASE,
                        }
                    ]
                },
            )
        raise AssertionError(path)

    client_class = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *args, **kwargs: client_class(
            *args, **kwargs, transport=httpx.MockTransport(handle)
        ),
    )

    def run(provider_class, **overrides):
        provider = provider_class()
        execution_id = overrides.pop("execution_id", "original-event")
        values = dict(
            publication=publication, session_id="coder", min_reviewers=1, max_repairs=2, **overrides
        )
        kind = (
            "query:ado:delivery-status"
            if issubclass(provider_class, DeliveryStatusQuery)
            else {
                MergeApprovalEffect: "effect:ado:merge-approval",
                ApprovedMergeEffect: "effect:ado:merge-approved",
                ClaimRepairEffect: "effect:ado:claim-repair",
                ClaimStartEffect: "effect:ado:claim-start",
                RecordPublicationEffect: "effect:ado:record-publication",
            }[provider_class]
        )
        definition = DeliveryDefinition(id="check", display_name="Check", type=kind, **values)
        context = (
            QueryRuntimeContext(**contexts)
            if kind.startswith("query:")
            else EffectRuntimeContext(**contexts)
        )
        provider.initialize(definition, context)
        if provider_class in {ClaimRepairEffect, ClaimStartEffect}:
            return provider.execute(execution_context={"idempotency_key": execution_id})
        return provider.execute()

    return admin, state, publication, run


@pytest.mark.parametrize(
    "part,field,value,blocker",
    [
        ("pr", "lastMergeSourceCommit", {"commitId": "d" * 40}, "source_commit_changed"),
        ("build", "sourceVersion", "d" * 40, "build_does_not_match_commit"),
        ("build", "definition", {"id": 99}, "build_does_not_match_commit"),
        ("pr", "reviewers", [], "review_pending"),
        ("pr", "isDraft", True, "pull_request_is_draft"),
    ],
)
def test_delivery_never_approves_stale_or_unreviewed_code(environment, part, field, value, blocker):
    _, state, _, run = environment
    assert run(DeliveryStatusQuery)["ready"] is True
    state[part][field] = value
    result = run(DeliveryStatusQuery)
    assert result["ready"] is False
    assert blocker in result["blockers"]
    assert not state["writes"]


def test_merge_uses_existing_approval_and_rechecks_head_before_writing(environment):
    admin, state, _, run = environment
    request = run(MergeApprovalEffect)
    assert request["approved"] is False
    with pytest.raises(PolicyError):
        run(ApprovedMergeEffect)
    admin.approval_audit_admin.decide_approval_request(
        request["request_id"], approver="operator", approve=True, reason="Reviewed"
    )
    state["pr"]["lastMergeSourceCommit"]["commitId"] = "d" * 40
    assert run(ApprovedMergeEffect)["merged"] is False
    assert state["writes"] == []
    state["pr"]["lastMergeSourceCommit"]["commitId"] = SHA
    result = run(ApprovedMergeEffect)
    assert result["merged"] is True
    assert state["writes"] == [
        {
            "status": "completed",
            "lastMergeSourceCommit": {"commitId": SHA},
            "completionOptions": {
                "bypassPolicy": False,
                "deleteSourceBranch": False,
                "mergeStrategy": "noFastForward",
            },
        }
    ]
    assert run(ApprovedMergeEffect)["merged"] is True
    assert len(state["writes"]) == 1


def test_changed_source_requires_a_new_approval(environment):
    admin, state, publication, run = environment
    request = run(MergeApprovalEffect)
    admin.approval_audit_admin.decide_approval_request(
        request["request_id"], approver="operator", approve=True, reason="Reviewed"
    )
    publication["commit_sha"] = "e" * 40
    state["pr"]["lastMergeSourceCommit"]["commitId"] = "e" * 40
    state["build"]["sourceVersion"] = "e" * 40
    next_request = run(MergeApprovalEffect)
    assert next_request["request_id"] != request["request_id"]
    assert next_request["approved"] is False
    assert not state["writes"]


def test_queued_merge_is_not_success_without_target_readback(environment):
    admin, state, _, run = environment
    request = run(MergeApprovalEffect)
    admin.approval_audit_admin.decide_approval_request(
        request["request_id"], approver="operator", approve=True, reason="Reviewed"
    )
    state["merge_visible"] = False
    result = run(ApprovedMergeEffect)
    assert result["merged"] is False
    assert "merge_target_needs_verification" in result["blockers"]


def test_failed_policies_and_unresolved_review_are_actionable(environment):
    _, state, _, run = environment
    state["policies"][0]["status"] = "rejected"
    state["threads"] = [
        {"status": "active", "comments": [{"content": "Handle empty input", "commentType": "text"}]}
    ]
    result = run(DeliveryStatusQuery)
    assert not result["ready"]
    assert {"branch_policy_not_satisfied", "review_threads_unresolved"} <= set(result["blockers"])
    assert result["repairable"]
    assert "Handle empty input" in result["feedback"]


def test_repair_budget_is_persistent_and_deduplicated_per_commit(environment):
    _, _, publication, run = environment
    assert run(ClaimRepairEffect)["attempt"] == 1
    assert run(ClaimRepairEffect)["replayed"] is True
    publication["commit_sha"] = "d" * 40
    assert run(ClaimRepairEffect)["attempt"] == 2
    publication["commit_sha"] = "e" * 40
    result = run(ClaimRepairEffect)
    assert result["allowed"] is False
    assert result["blockers"] == ["repair_budget_exhausted"]


def test_later_polls_cannot_repeat_a_claimed_repair(environment):
    _, _, _, run = environment
    assert run(ClaimRepairEffect, execution_id="first-poll")["allowed"]
    assert run(ClaimRepairEffect, execution_id="first-poll")["allowed"]
    assert not run(ClaimRepairEffect, execution_id="later-poll")["allowed"]


def test_start_is_replayable_only_by_its_original_execution(environment):
    _, _, _, run = environment
    assert run(ClaimStartEffect, execution_id="first-click")["allowed"]
    assert run(ClaimStartEffect, execution_id="first-click")["allowed"]
    assert not run(ClaimStartEffect, execution_id="second-click")["allowed"]


def test_superseded_publication_cannot_replace_the_tracked_commit(environment):
    admin, state, publication, run = environment
    from evander.workflow.state_store import WorkflowStateStore

    assert run(RecordPublicationEffect)["success"]
    original = dict(publication)
    publication["commit_sha"] = "d" * 40
    state["pr"]["lastMergeSourceCommit"]["commitId"] = "d" * 40
    assert run(RecordPublicationEffect)["success"]
    publication.update(original)
    assert not run(RecordPublicationEffect)["success"]
    record = (
        WorkflowStateStore(admin.runtime.config.runtime_dir)
        .get("root", "delivery", "ado-delivery", "publication")
        .record
    )
    assert record.value["commit_sha"] == "d" * 40
