import copy
import json

import httpx

import pytest

from evander_azure_devops_addon.pr_completion import complete_pull_request

SHA = "a" * 40
MERGE = "b" * 40
PATH = "git/repositories/repo/pullRequests/17"


@pytest.fixture
def provider():
    state = {
        "pr": {
            "pullRequestId": 17,
            "status": "active",
            "targetRefName": "refs/heads/main",
            "lastMergeSourceCommit": {"commitId": SHA},
            "reviewers": [],
            "repository": {"project": {"id": "project-id"}},
        },
        "policies": [],
        "threads": [],
        "writes": [],
        "head": MERGE,
        "complete": True,
    }

    def request(method, path, **kwargs):
        if method == "PATCH":
            state["writes"].append(kwargs["json"])
            if state["complete"]:
                state["pr"].update(status="completed", lastMergeCommit={"commitId": MERGE})
            return copy.deepcopy(state["pr"])
        if path == PATH:
            return copy.deepcopy(state["pr"])
        if path == PATH + "/threads":
            return {"value": state["threads"]}
        if path == "policy/evaluations":
            return {"value": state["policies"]}
        if path == "git/repositories/repo/refs":
            return {"value": [{"name": "refs/heads/main", "objectId": state["head"]}]}
        raise AssertionError((method, path))

    state["request"] = request
    return state, lambda: complete_pull_request(
        request,
        pr_path=PATH,
        expected_source_commit=SHA,
        expected_target_branch="main",
    )


def test_completion_is_pinned_and_verified_and_retries_do_not_merge_again(provider):
    state, complete = provider
    assert complete()["merged"]
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
    assert complete()["merged"]
    assert len(state["writes"]) == 1


@pytest.mark.parametrize(
    "change,blocker",
    [
        ({"lastMergeSourceCommit": {"commitId": "c" * 40}}, "source_commit_changed"),
        ({"targetRefName": "refs/heads/other"}, "target_branch_changed"),
        ({"isDraft": True}, "pull_request_is_draft"),
        ({"status": "abandoned"}, "pull_request_not_active"),
        ({"reviewers": [{"vote": -5}]}, "review_changes_requested"),
        ({"mergeStatus": "conflicts"}, "merge_not_ready"),
    ],
)
def test_changed_or_unready_pr_is_never_completed(provider, change, blocker):
    state, complete = provider
    state["pr"].update(change)
    result = complete()
    assert blocker in result["blockers"]
    assert not result["merged"] and state["writes"] == []


@pytest.mark.parametrize(
    "field,value,blocker",
    [
        (
            "policies",
            [{"configuration": {"isBlocking": True}, "status": "running"}],
            "branch_policy_not_satisfied",
        ),
        ("policies", None, "policy_evaluation_incomplete"),
        ("threads", [{"status": "active"}], "review_threads_unresolved"),
        ("threads", None, "review_threads_unavailable"),
    ],
)
def test_incomplete_review_or_policy_blocks_completion(provider, field, value, blocker):
    state, complete = provider
    state[field] = value
    assert blocker in complete()["blockers"]
    assert state["writes"] == []


def test_queued_completion_is_not_reported_as_merged(provider):
    state, complete = provider
    state["complete"] = False
    result = complete()
    assert result["pending"] and not result["merged"]


def test_unverified_target_is_not_reported_as_merged(provider):
    state, complete = provider
    state["head"] = "c" * 40
    result = complete()
    assert not result["merged"]
    assert result["blockers"] == ["merge_target_needs_verification"]


def test_ado_provider_uses_configured_project_and_shared_completion(provider, monkeypatch):
    from evander_azure_devops_addon.pull_requests import AzureDevOpsPrQueryProvider

    state, _ = provider

    def handler(request):
        assert request.url.host == "dev.azure.com"
        assert request.url.path.startswith("/example/Project Name/_apis/")
        assert request.url.params["api-version"].startswith("7.1")
        assert request.headers["authorization"].startswith("Basic ")
        path = request.url.path.split("/_apis/", 1)[1]
        payload = json.loads(request.content) if request.content else None
        return httpx.Response(
            200,
            json=state["request"](
                request.method,
                path,
                **({"json": payload} if payload else {}),
            ),
        )

    client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client(
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )
    ado = AzureDevOpsPrQueryProvider(
        organization_url="https://dev.azure.com/example",
        project="Project Name",
        pat="test-token",
    )
    result = ado.complete_pull_request(
        repo="repo",
        pr_id="17",
        expected_source_commit=SHA,
        expected_target_branch="refs/heads/main",
    )
    assert result.merged and result.merge_commit == MERGE
    assert len(state["writes"]) == 1
