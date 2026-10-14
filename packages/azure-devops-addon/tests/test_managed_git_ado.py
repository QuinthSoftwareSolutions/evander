from __future__ import annotations
from pathlib import Path
import pytest
from evander.admin.services.managed_git_service import (
    ManagedGitAdminService,
    ManagedGitConflictError,
)

from test_managed_git_service import _service


@pytest.mark.parametrize("reuse_existing", [True, False])
def test_azure_pr_create_and_reuse_reject_moved_source_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reuse_existing: bool,
) -> None:
    expected_commit = "1" * 40
    current_commit = "2" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    pr_item = {
        "pullRequestId": 19,
        "url": "https://example/pr/19",
        "lastMergeSourceCommit": {"commitId": current_commit},
    }
    if reuse_existing:
        now = [0.0]
        monkeypatch.setattr(
            "evander_azure_devops_addon.managed_git."
            "_AZURE_PR_SOURCE_CONFIRMATION_TIMEOUT_SECONDS",
            1.0,
        )
        monkeypatch.setattr(
            "evander_azure_devops_addon.managed_git.time.monotonic",
            lambda: now[0],
        )
        monkeypatch.setattr(
            "evander_azure_devops_addon.managed_git.time.sleep",
            lambda seconds: now.__setitem__(0, now[0] + seconds),
        )
    responses = iter(
        [
            {"value": [pr_item]},
            {
                "value": [
                    {
                        "id": 1,
                        "sourceRefCommit": {"commitId": current_commit},
                    }
                ]
            },
        ]
        if reuse_existing
        else [{"value": []}, pr_item, {"status": "abandoned"}]
    )
    methods: list[str | None] = []

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object]:
        del url, auth, payload, timeout_seconds
        methods.append(method)
        return next(responses)

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)

    with pytest.raises(ManagedGitConflictError, match="source branch moved") as exc_info:
        service._create_or_reuse_azure_pr(
            record,
            destination_branch="review/topic",
            base_branch="main",
            expected_commit_sha=expected_commit,
            title="Review topic",
            body="",
            draft=False,
        )

    assert exc_info.value.details == {
        "reason": "source_ref_moved",
        "provider": "Azure DevOps",
        "expected_commit_sha": expected_commit,
        "current_commit_sha": current_commit,
    }
    assert ("PATCH" in methods) is (not reuse_existing)


def test_azure_pr_reuse_waits_for_latest_iteration_source_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    previous_commit = "2" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    pr_item = {
        "pullRequestId": 19,
        "url": "https://example/pr/19",
        "lastMergeSourceCommit": {"commitId": previous_commit},
    }
    responses = iter(
        [
            {"value": [pr_item]},
            {
                "value": [
                    {
                        "id": 1,
                        "sourceRefCommit": {"commitId": previous_commit},
                    }
                ]
            },
            {
                "value": [
                    {
                        "id": 2,
                        "sourceRefCommit": {"commitId": expected_commit},
                    },
                    {
                        "id": 1,
                        "sourceRefCommit": {"commitId": previous_commit},
                    },
                ]
            },
            pr_item,
        ]
    )
    calls: list[dict[str, object]] = []
    sleeps: list[float] = []
    timeouts: list[float] = []
    now = [0.0]

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object]:
        del auth
        calls.append(
            {
                "url": url,
                "payload": payload,
                "method": method,
                "timeout_seconds": timeout_seconds,
            }
        )
        if "/iterations?" in url:
            timeouts.append(timeout_seconds)
        return next(responses)

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)
    monkeypatch.setattr(
        "evander_azure_devops_addon.managed_git.time.monotonic",
        lambda: now[0],
    )

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(
        "evander_azure_devops_addon.managed_git.time.sleep",
        _sleep,
    )

    result = service._create_or_reuse_azure_pr(
        record,
        destination_branch="feature/issue-42",
        base_branch="main",
        expected_commit_sha=expected_commit,
        title="Updated title",
        body="Updated body",
        draft=False,
    )

    assert result["created"] is False
    assert sleeps == [1.0]
    assert timeouts == [10.0, 9.0]
    assert "/pullrequests/19/iterations?api-version=7.1" in str(calls[1]["url"])
    assert "/pullrequests/19/iterations?api-version=7.1" in str(calls[2]["url"])
    assert calls[3]["method"] == "PATCH"
    assert calls[3]["payload"] == {
        "description": "Updated body",
        "title": "Updated title",
    }


def test_azure_pr_reuse_does_not_confirm_older_matching_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    previous_commit = "2" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    monkeypatch.setattr(
        "evander_azure_devops_addon.managed_git._AZURE_PR_SOURCE_CONFIRMATION_TIMEOUT_SECONDS",
        1.0,
    )
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    pr_item = {
        "pullRequestId": 19,
        "url": "https://example/pr/19",
        "lastMergeSourceCommit": {"commitId": previous_commit},
    }
    now = [0.0]

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object]:
        del auth, payload, method, timeout_seconds
        if "/iterations?" not in url:
            return {"value": [pr_item]}
        return {
            "value": [
                {
                    "id": 1,
                    "sourceRefCommit": {"commitId": expected_commit},
                },
                {"id": 2},
            ]
        }

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)
    monkeypatch.setattr(
        "evander_azure_devops_addon.managed_git.time.monotonic",
        lambda: now[0],
    )
    monkeypatch.setattr(
        "evander_azure_devops_addon.managed_git.time.sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    with pytest.raises(
        ManagedGitConflictError,
        match="source commit could not be verified",
    ) as exc_info:
        service._create_or_reuse_azure_pr(
            record,
            destination_branch="feature/issue-42",
            base_branch="main",
            expected_commit_sha=expected_commit,
            title="Updated title",
            body="Updated body",
            draft=False,
        )

    assert exc_info.value.details == {
        "reason": "provider_pr_source_commit_unverified",
        "provider": "Azure DevOps",
        "expected_commit_sha": expected_commit,
        "current_commit_sha": None,
    }


def test_azure_pr_reuse_confirmation_enforces_wall_clock_request_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    current_commit = "2" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    monkeypatch.setattr(
        "evander_azure_devops_addon.managed_git._AZURE_PR_SOURCE_CONFIRMATION_TIMEOUT_SECONDS",
        2.0,
    )
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    pr_item = {
        "pullRequestId": 19,
        "url": "https://example/pr/19",
        "lastMergeSourceCommit": {"commitId": current_commit},
    }
    now = [100.0]
    timeouts: list[float] = []
    calls = 0

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object]:
        nonlocal calls
        del auth, payload, method
        calls += 1
        if "/iterations?" not in url:
            return {"value": [pr_item]}
        timeouts.append(timeout_seconds)
        now[0] += timeout_seconds
        return {
            "value": [
                {
                    "id": 1,
                    "sourceRefCommit": {"commitId": current_commit},
                }
            ]
        }

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)
    monkeypatch.setattr(
        "evander_azure_devops_addon.managed_git.time.monotonic",
        lambda: now[0],
    )

    with pytest.raises(ManagedGitConflictError, match="source branch moved"):
        service._create_or_reuse_azure_pr(
            record,
            destination_branch="feature/issue-42",
            base_branch="main",
            expected_commit_sha=expected_commit,
            title="Updated title",
            body="Updated body",
            draft=False,
        )

    assert calls == 2
    assert timeouts == [2.0]
    assert now[0] == 102.0


def test_azure_request_json_uses_explicit_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    timeouts: list[float] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return b"{}"

    def _fake_urlopen(request, *, timeout: float):
        del request
        timeouts.append(timeout)
        return _Response()

    monkeypatch.setattr(
        "evander_azure_devops_addon.managed_git.urlopen",
        _fake_urlopen,
    )

    assert (
        service._azure_request_json(
            "https://example.invalid/pr",
            auth="Basic test",
            timeout_seconds=2.5,
        )
        == {}
    )
    assert timeouts == [2.5]


def test_azure_pr_reuse_refreshes_metadata_links_work_item_and_returns_web_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    pr_item = {
        "pullRequestId": 19,
        "url": "https://dev.azure.com/Org/Project/_apis/git/repositories/Repo/pullRequests/19",
        "_links": {"web": {"href": "https://dev.azure.com/Org/Project/_git/Repo/pullrequest/19"}},
        "lastMergeSourceCommit": {"commitId": expected_commit},
        "repository": {"id": "repo-id", "project": {"id": "project-id"}},
    }
    responses = iter(
        [
            {"value": [pr_item]},
            pr_item,
            {"relations": []},
            {"id": 42},
        ]
    )
    calls: list[dict[str, object]] = []

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        content_type: str = "application/json",
    ) -> dict[str, object]:
        del auth
        calls.append(
            {
                "url": url,
                "payload": payload,
                "method": method,
                "content_type": content_type,
            }
        )
        return next(responses)

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)

    result = service._create_or_reuse_azure_pr(
        record,
        destination_branch="feature/issue-42",
        base_branch="main",
        expected_commit_sha=expected_commit,
        title="Updated title",
        body="x" * 4100,
        draft=False,
        title_explicit=True,
        work_item_id="42",
    )

    assert result["created"] is False
    assert result["pr_url"] == "https://dev.azure.com/Org/Project/_git/Repo/pullrequest/19"
    assert result["description_truncated"] is True
    assert result["description_original_length"] == 4100
    assert result["description_length"] == 4000
    assert result["work_item_linked"] is True
    assert all("/iterations?" not in str(call["url"]) for call in calls)
    assert calls[1]["method"] == "PATCH"
    assert calls[1]["payload"] == {"description": "x" * 4000, "title": "Updated title"}
    assert calls[3]["method"] == "PATCH"
    assert calls[3]["content_type"] == "application/json-patch+json"
    relation = calls[3]["payload"][0]["value"]  # type: ignore[index]
    assert relation["rel"] == "ArtifactLink"
    assert relation["url"] == "vstfs:///Git/PullRequestId/project-id%2Frepo-id%2F19"


def test_azure_pr_create_bounds_description_and_returns_web_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    created = {
        "pullRequestId": 20,
        "url": "https://dev.azure.com/Org/Project/_apis/git/repositories/Repo/pullRequests/20",
        "_links": {"web": {"href": "https://dev.azure.com/Org/Project/_git/Repo/pullrequest/20"}},
        "lastMergeSourceCommit": {"commitId": expected_commit},
    }
    calls: list[dict[str, object]] = []

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        content_type: str = "application/json",
    ) -> dict[str, object]:
        del url, auth, content_type
        calls.append({"payload": payload, "method": method})
        return {"value": []} if len(calls) == 1 else created

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)

    result = service._create_or_reuse_azure_pr(
        record,
        destination_branch="feature/topic",
        base_branch="main",
        expected_commit_sha=expected_commit,
        title="Topic",
        body="y" * 4001,
        draft=True,
    )

    assert result["created"] is True
    assert result["pr_url"] == "https://dev.azure.com/Org/Project/_git/Repo/pullrequest/20"
    assert result["description_truncated"] is True
    assert len(calls[1]["payload"]["description"]) == 4000  # type: ignore[index]


def test_azure_work_item_id_is_derived_from_issue_session() -> None:
    assert ManagedGitAdminService._azure_work_item_id("", "issue-14760-run-1") == "14760"
    assert ManagedGitAdminService._azure_work_item_id("42", "issue-14760-run-1") == "42"
