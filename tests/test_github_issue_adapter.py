from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evander.issues.service import GitHubIssueAdapter


@dataclass
class _Config:
    github_token_env: str = "GITHUB_TOKEN"


class _Resolver:
    def resolve_for_directory(self, directory_path: str) -> _Config:
        del directory_path
        return _Config()


class _Response:
    def __init__(self, payload: Any, *, links: dict[str, dict[str, str]] | None = None) -> None:
        self._payload = payload
        self.links = links or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _Client:
    requests: list[dict[str, Any]] = []
    responses: list[_Response] = []

    def __init__(self, *, timeout: float) -> None:
        del timeout

    def __enter__(self) -> "_Client":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Response:
        self.requests.append({"url": url, "params": params, "headers": headers})
        return self.responses.pop(0)


def test_github_issue_adapter_lists_issues_with_token_and_excludes_prs(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token-1")
    monkeypatch.setattr("evander.issues.service.httpx.Client", _Client)
    _Client.requests = []
    _Client.responses = [
        _Response(
            [
                {
                    "number": 42,
                    "title": "Crash on launch",
                    "state": "open",
                    "labels": [{"name": "bug"}],
                    "assignees": [{"login": "octocat"}],
                    "user": {"login": "alice", "id": 1},
                    "created_at": "2026-06-01T09:00:00Z",
                    "updated_at": "2026-06-01T10:00:00Z",
                    "html_url": "https://github.com/owner/repo/issues/42",
                    "body": "Steps",
                },
                {
                    "number": 43,
                    "title": "Pull request shaped item",
                    "pull_request": {"url": "https://api.github.test/pulls/43"},
                },
            ]
        )
    ]

    adapter = GitHubIssueAdapter(_Resolver(), api_base_url="https://api.github.test")
    result = adapter.query_remote_issues(
        directory_path="root/github",
        repo="owner/repo",
        state="all",
        labels_any=["bug"],
    )

    assert _Client.requests[0]["headers"]["Authorization"] == "Bearer token-1"
    assert _Client.requests[0]["params"]["state"] == "all"
    assert result["count"] == 1
    assert result["items"][0]["remote_issue_id"] == "42"
    assert result["items"][0]["labels"] == ["bug"]
    assert result["items"][0]["assignees"] == ["octocat"]
    assert result["items"][0]["author"]["login"] == "alice"


def test_github_issue_adapter_scans_until_labels_any_matches(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("evander.issues.service.httpx.Client", _Client)
    _Client.requests = []
    _Client.responses = [
        _Response(
            [
                {
                    "number": 41,
                    "title": "Documentation cleanup",
                    "state": "open",
                    "labels": [{"name": "docs"}],
                    "html_url": "https://github.com/owner/repo/issues/41",
                }
            ],
            links={
                "next": {
                    "url": "https://api.github.test/repos/owner/repo/issues?page=2"
                }
            },
        ),
        _Response(
            [
                {
                    "number": 42,
                    "title": "Crash on launch",
                    "state": "open",
                    "labels": [{"name": "bug"}],
                    "html_url": "https://github.com/owner/repo/issues/42",
                }
            ]
        ),
    ]

    adapter = GitHubIssueAdapter(_Resolver(), api_base_url="https://api.github.test")
    result = adapter.query_remote_issues(
        directory_path="root/github",
        repo="owner/repo",
        labels_any=["bug"],
        top=1,
    )

    assert len(_Client.requests) == 2
    assert result["count"] == 1
    assert result["items"][0]["remote_issue_id"] == "42"


def test_github_issue_adapter_reads_issue_comments(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("evander.issues.service.httpx.Client", _Client)
    _Client.requests = []
    _Client.responses = [
        _Response(
            {
                "number": 42,
                "title": "Crash on launch",
                "state": "open",
                "labels": [],
                "assignees": [],
                "user": {"login": "alice", "id": 1},
                "created_at": "2026-06-01T09:00:00Z",
                "updated_at": "2026-06-01T10:00:00Z",
                "html_url": "https://github.com/owner/repo/issues/42",
                "body": "Steps",
            }
        ),
        _Response(
            [
                {
                    "id": 9001,
                    "body": "I can reproduce this.",
                    "user": {"login": "bob", "id": 2},
                    "created_at": "2026-06-01T10:01:00Z",
                    "html_url": "https://github.com/owner/repo/issues/42#issuecomment-9001",
                }
            ]
        ),
    ]

    adapter = GitHubIssueAdapter(_Resolver(), api_base_url="https://api.github.test")
    result = adapter.read_issue(
        remote_issue_id="42",
        directory_path="root/github",
        project="owner/repo",
        include_comments=True,
    )

    assert "Authorization" not in _Client.requests[0]["headers"]
    assert result["remote_issue_id"] == "42"
    assert result["comments"][0]["comment_id"] == "9001"
    assert result["comments"][0]["created_by"] == "bob"
