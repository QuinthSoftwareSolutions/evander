from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import pytest

from evander.integration_services.pull_requests import GitHubPrQueryProvider, PrQueryService

SOURCE = "a" * 40
MERGE = "b" * 40
UUID = "630b9d5e-3f2a-4f7e-8b0c-2d5f9a8c1e42"
PR = "/repos/owner/repo/pulls/17"


@pytest.fixture
def github(monkeypatch):
    state: dict[str, Any] = {
        "pr": {
            "number": 17,
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": True,
            "head": {"sha": SOURCE},
            "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
            "merge_commit_sha": "c" * 40,
        },
        "downstack": [],
        "writes": [],
        "requests": [],
        "status_code": 202,
        "submitted": {
            "status": "pending",
            "details": {"uuid": UUID, "expected_head_sha": SOURCE, "bypass_rules": False},
        },
        "poll": {"status": "merged", "details": {"sha": MERGE}},
        "mirror_merge": True,
    }

    def mark_merged(payload):
        if payload.get("status") == "merged" and state["mirror_merge"]:
            state["pr"].update(state="closed", merged=True, merge_commit_sha=MERGE)

    def handler(request):
        state["requests"].append((request.method, request.url.path))
        assert request.url.host == "api.github.test"
        assert request.headers["authorization"] == "Bearer fixture-token"
        assert request.headers["x-github-api-version"] == "2026-03-10"
        if request.url.path == PR and request.method == "GET":
            if state.get("redirect"):
                return httpx.Response(302, headers={"Location": "https://untrusted.test/stolen"})
            return httpx.Response(200, json=copy.deepcopy(state["pr"]))
        if request.url.path == "/repos/owner/repo/pulls":
            assert dict(request.url.params) == {
                "state": "open",
                "head": "owner:main",
                "per_page": "1",
            }
            return httpx.Response(200, json=state["downstack"])
        if request.url.path == PR + "/merge-async" and request.method == "PUT":
            state["writes"].append(json.loads(request.content))
            mark_merged(state["submitted"])
            return httpx.Response(state["status_code"], json=state["submitted"])
        if request.url.path == PR + "/merge-async/" + UUID and request.method == "GET":
            mark_merged(state["poll"])
            return httpx.Response(200, json=state["poll"])
        raise AssertionError((request.method, str(request.url)))

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )
    provider = GitHubPrQueryProvider(token="fixture-token", api_base_url="https://api.github.test")
    service = PrQueryService(providers={"github": provider})
    return state, lambda **overrides: service.complete_pull_request(
        **{
            "provider": "github",
            "repo": "owner/repo",
            "pr_id": "17",
            "expected_source_commit": SOURCE,
            "expected_target_branch": "refs/heads/main",
            **overrides,
        }
    )


def test_github_completion_is_pinned_verified_and_does_not_bypass_rules(github):
    state, complete = github
    result = complete()
    assert result.provider == "github" and result.merged and not result.pending
    assert result.merge_commit == MERGE and result.completion_id == UUID
    assert state["writes"] == [{"sha": SOURCE, "merge_action": "default", "bypass_rules": False}]
    # A later verification call never submits an already completed PR again.
    assert complete().merged
    assert len(state["writes"]) == 1


@pytest.mark.parametrize(
    "change,blocker",
    [
        ({"head": {"sha": "d" * 40}}, "source_commit_changed"),
        ({"base": {"ref": "other", "repo": {"full_name": "owner/repo"}}}, "target_branch_changed"),
        (
            {"base": {"ref": "main", "repo": {"full_name": "other/repo"}}},
            "pull_request_scope_mismatch",
        ),
        ({"number": 18}, "pull_request_scope_mismatch"),
        ({"draft": True}, "pull_request_is_draft"),
        ({"state": "closed"}, "pull_request_not_active"),
        ({"mergeable": False}, "merge_not_ready"),
    ],
)
def test_github_rejects_changed_or_blocked_pr_before_any_write(github, change, blocker):
    state, complete = github
    state["pr"].update(change)
    result = complete()
    assert blocker in result.blockers and not result.merged
    assert state["writes"] == []


def test_unknown_mergeability_is_pending_without_a_write(github):
    state, complete = github
    state["pr"]["mergeable"] = None
    result = complete()
    assert result.pending and not result.merged and not result.blockers
    assert state["writes"] == []


def test_completion_never_merges_an_open_downstack_pr(github):
    state, complete = github
    state["downstack"] = [{"number": 4}]
    assert complete().blockers == ["stacked_pull_request_requires_separate_completion"]
    assert state["writes"] == []


@pytest.mark.parametrize("status", ["pending", "enqueued"])
def test_pending_or_queued_work_is_not_reported_as_merged(github, status):
    state, complete = github
    state["poll"] = {"status": status, "details": {"uuid": UUID, "expected_head_sha": SOURCE}}
    result = complete()
    assert result.pending and not result.merged and not result.blockers
    assert result.completion_id == UUID


def test_async_rule_failure_is_reported(github):
    state, complete = github
    state["poll"] = {"status": "failed", "details": {"message": "Required review is missing."}}
    result = complete()
    assert result.blockers == ["github_merge_failed"] and not result.merged and not result.pending
    assert result.message == "Required review is missing."


def test_existing_matching_request_is_polled(github):
    state, complete = github
    state["status_code"] = 409
    result = complete()
    assert result.merged and result.completion_id == UUID
    assert ("GET", PR + "/merge-async/" + UUID) in state["requests"]


@pytest.mark.parametrize(
    "details",
    [
        {"uuid": UUID, "expected_head_sha": "d" * 40, "bypass_rules": False},
        {"uuid": UUID, "expected_head_sha": SOURCE, "bypass_rules": True},
        {"uuid": UUID, "expected_head_sha": SOURCE},
    ],
)
def test_existing_different_request_is_not_treated_as_approved(github, details):
    state, complete = github
    state["status_code"] = 409
    state["submitted"] = {"status": "pending", "details": details}
    assert complete().blockers == ["existing_merge_request_mismatch"]
    assert ("GET", PR + "/merge-async/" + UUID) not in state["requests"]


def test_api_claim_is_not_enough_without_a_confirming_pr_read(github):
    state, complete = github
    state["mirror_merge"] = False
    assert complete().pending


@pytest.mark.parametrize("repo", ["../repo", "owner/../repo", "owner/repo?secret=1"])
def test_invalid_repository_never_reaches_the_network(github, repo):
    state, complete = github
    with pytest.raises(ValueError, match="owner/repository"):
        complete(repo=repo)
    assert state["requests"] == []


def test_github_requires_host_credentials_before_network(github):
    state, _ = github
    provider = GitHubPrQueryProvider()
    with pytest.raises(ValueError, match="configured token"):
        provider.complete_pull_request(
            repo="owner/repo",
            pr_id="17",
            expected_source_commit=SOURCE,
            expected_target_branch="main",
        )
    assert state["requests"] == []


def test_redirect_is_not_followed_with_host_credentials(github):
    state, complete = github
    state["redirect"] = True
    with pytest.raises(httpx.HTTPStatusError):
        complete()
    assert state["requests"] == [("GET", PR)]


def test_invalid_async_id_cannot_change_the_poll_url(github):
    state, complete = github
    state["submitted"]["details"]["uuid"] = "../../other"
    with pytest.raises(ValueError, match="invalid completion ID"):
        complete()
    assert len(state["requests"]) == 3


def test_provider_permission_error_remains_an_error(github):
    state, complete = github
    state["status_code"] = 403
    state["submitted"] = {"message": "Forbidden"}
    with pytest.raises(httpx.HTTPStatusError):
        complete()


def test_retry_reads_a_failed_job_without_submitting_another(github):
    state, complete = github
    state["poll"] = {"status": "pending", "details": {"uuid": UUID, "expected_head_sha": SOURCE}}
    first = complete()
    assert first.pending and first.completion_id == UUID
    state["poll"] = {
        "status": "failed",
        "details": {"message": "A required review is missing."},
    }
    # Even if the PR is no longer mergeable, retrieve this job's terminal error.
    state["pr"]["mergeable"] = False
    state["submitted"]["details"]["uuid"] = "530b9d5e-3f2a-4f7e-8b0c-2d5f9a8c1e42"
    second = complete(completion_id=first.completion_id)
    assert second.blockers == ["github_merge_failed"]
    assert second.message == "A required review is missing."
    assert not second.pending and not second.merged
    assert second.completion_id == first.completion_id
    assert len(state["writes"]) == 1


def test_explicit_poll_does_not_require_new_submission_eligibility(github):
    state, complete = github
    state["pr"].update(draft=True, mergeable=None)
    state["poll"] = {"status": "pending", "details": {"uuid": UUID, "expected_head_sha": SOURCE}}
    result = complete(completion_id=UUID)
    assert result.pending and state["writes"] == []
    assert state["requests"] == [("GET", PR), ("GET", PR + "/merge-async/" + UUID), ("GET", PR)]


def test_explicit_poll_rejects_invalid_id_before_network(github):
    state, complete = github
    with pytest.raises(ValueError, match="Invalid GitHub completion ID"):
        complete(completion_id="../../other")
    assert state["requests"] == []


def test_explicit_poll_keeps_expected_source_guard(github):
    state, complete = github
    state["pr"]["head"]["sha"] = "d" * 40
    assert complete(completion_id=UUID).blockers == ["source_commit_changed"]
    assert state["requests"] == [("GET", PR)]
