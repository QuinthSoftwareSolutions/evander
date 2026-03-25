from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_sandbox.audit import AuditLogger
from agent_sandbox.pr_feedback_worker import (
    AzureDevOpsFeedbackAdapter,
    FeedbackEnvelope,
    GitHubFeedbackAdapter,
    PrFeedbackStore,
    PullRequestFeedbackWorker,
)


def test_pr_feedback_store_dedupes_feedback(tmp_path: Path) -> None:
    store = PrFeedbackStore(tmp_path / "runtime" / "pr-feedback-store.json")
    envelope = FeedbackEnvelope(
        feedback_id="github:issue_comment:1",
        provider="github",
        repo="org/repo",
        pr_id="123",
        thread_id="issue_comment:1",
        comment_id="1",
        author="octocat",
        body="Needs tests",
        created_at=datetime(2026, 3, 21, tzinfo=timezone.utc),
    )

    assert store.add_feedback_if_new(envelope) is True
    assert store.add_feedback_if_new(envelope) is False

    payload = json.loads((tmp_path / "runtime" / "pr-feedback-store.json").read_text(encoding="utf-8"))
    assert list(payload["feedback"].keys()) == ["github:issue_comment:1"]


def test_pr_feedback_store_persists_and_resumes_cursors(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "pr-feedback-store.json"
    store = PrFeedbackStore(path)
    stream_key = "github:org/repo:123"
    cursor = "2026-03-21T00:00:00Z"

    store.set_cursor(stream_key, cursor)

    reloaded = PrFeedbackStore(path)
    assert reloaded.get_cursor(stream_key) == cursor


def test_pr_feedback_store_persists_session_read_cursors(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "pr-feedback-store.json"
    store = PrFeedbackStore(path)
    stream_key = "github:org/repo:123"

    store.advance_session_cursor("session-1", stream_key, "2026-03-21T00:00:00Z")
    store.advance_session_cursor("session-1", stream_key, "2026-03-20T00:00:00Z")

    reloaded = PrFeedbackStore(path)
    assert reloaded.get_session_cursor("session-1", stream_key) == "2026-03-21T00:00:00Z"


def test_pr_feedback_store_reports_unread_feedback_per_session(tmp_path: Path) -> None:
    store = PrFeedbackStore(tmp_path / "runtime" / "pr-feedback-store.json")
    envelope = FeedbackEnvelope(
        feedback_id="github:issue_comment:1",
        provider="github",
        repo="org/repo",
        pr_id="123",
        thread_id="issue_comment:1",
        comment_id="1",
        author="octocat",
        body="Needs tests",
        created_at=datetime(2026, 3, 21, tzinfo=timezone.utc),
    )

    assert store.add_feedback_if_new(envelope) is True
    assert (
        store.has_unread_feedback(
            session_id="session-1",
            streams=[{"provider": "github", "repo": "org/repo", "pr_id": "123"}],
        )
        is True
    )

    store.advance_session_cursor("session-1", "github:org/repo:123", "2026-03-21T00:00:00Z")

    assert (
        store.has_unread_feedback(
            session_id="session-1",
            streams=[{"provider": "github", "repo": "org/repo", "pr_id": "123"}],
        )
        is False
    )


def test_github_mapping_review_comment_edge_cases() -> None:
    comments = [
        {
            "id": 55,
            "pull_request_review_id": 7,
            "user": {"login": "reviewer"},
            "body": None,
            "path": "src/main.py",
            "original_line": 98,
            "created_at": "2026-03-20T12:00:00Z",
            "in_reply_to_id": 54,
        }
    ]
    mapped = GitHubFeedbackAdapter._map_review_comments(repo="org/repo", pr_id="9", comments=comments)

    assert len(mapped) == 1
    envelope = mapped[0]
    assert envelope.feedback_id == "github:review_comment:55"
    assert envelope.thread_id == "7"
    assert envelope.line == 98
    assert envelope.body == ""
    assert envelope.in_reply_to == "54"


def test_github_mapping_review_event_filters_cursor() -> None:
    reviews = [
        {
            "id": 11,
            "user": {"login": "maintainer"},
            "body": "ship it",
            "submitted_at": "2026-03-21T00:00:01Z",
        },
        {
            "id": 10,
            "user": {"login": "maintainer"},
            "body": "old",
            "submitted_at": "2026-03-20T23:59:59Z",
        },
    ]

    mapped = GitHubFeedbackAdapter._map_reviews(
        repo="org/repo",
        pr_id="9",
        reviews=reviews,
        cursor="2026-03-21T00:00:00Z",
    )

    assert [event.feedback_id for event in mapped] == ["github:review:11"]


def test_ado_mapping_thread_edge_cases() -> None:
    adapter = AzureDevOpsFeedbackAdapter("https://dev.azure.com/example", "proj")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "value": [
                    {
                        "id": 44,
                        "status": "fixed",
                        "threadContext": {
                            "filePath": "/src/app.py",
                            "rightFileStart": {"line": 12},
                        },
                        "comments": [
                            {
                                "id": 91,
                                "author": {"displayName": "Dev One"},
                                "content": "please rename",
                                "publishedDate": "2026-03-21T01:00:00Z",
                                "parentCommentId": 0,
                                "isDeleted": False,
                            },
                            {
                                "id": 92,
                                "author": {"displayName": "Dev Two"},
                                "content": "outdated",
                                "publishedDate": "2026-03-21T01:00:01Z",
                                "isDeleted": True,
                            },
                        ],
                    }
                ]
            }

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    result = adapter.poll(client=FakeClient(), repo="repo-1", pr_id="101", cursor=None)

    assert [item.feedback_id for item in result.feedback] == ["azure_devops:thread_comment:44:91"]
    envelope = result.feedback[0]
    assert envelope.resolved is True
    assert envelope.in_reply_to is None
    assert envelope.file_path == "/src/app.py"
    assert envelope.line == 12


def test_github_adapter_sends_auth_headers_when_token_configured() -> None:
    adapter = GitHubFeedbackAdapter(token="gh-test-token")
    captured_headers: list[dict[str, str] | None] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, object]]:
            return []

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            captured_headers.append(kwargs.get("headers"))
            return FakeResponse()

    result = adapter.poll(client=FakeClient(), repo="org/repo", pr_id="101", cursor=None)

    assert result.feedback == []
    assert captured_headers
    assert all(headers is not None for headers in captured_headers)
    assert all(headers["Authorization"] == "Bearer gh-test-token" for headers in captured_headers if headers)
    assert all(headers["Accept"] == "application/vnd.github+json" for headers in captured_headers if headers)


def test_ado_adapter_sends_basic_auth_header_when_pat_configured() -> None:
    adapter = AzureDevOpsFeedbackAdapter("https://dev.azure.com/example", "proj", pat="ado-test-pat")
    captured_headers: list[dict[str, str] | None] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"value": []}

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            captured_headers.append(kwargs.get("headers"))
            return FakeResponse()

    result = adapter.poll(client=FakeClient(), repo="repo-1", pr_id="101", cursor=None)

    assert result.feedback == []
    assert captured_headers == [
        {"Authorization": f"Basic {base64.b64encode(b':ado-test-pat').decode('ascii')}"}
    ]


def test_ado_adapter_normalizes_composite_repo_identifier() -> None:
    adapter = AzureDevOpsFeedbackAdapter("https://dev.azure.com/example", "proj", pat="ado-test-pat")
    captured_urls: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"value": []}

    class FakeClient:
        def get(self, url: str, *args: object, **kwargs: object) -> FakeResponse:
            captured_urls.append(url)
            return FakeResponse()

    adapter.poll(
        client=FakeClient(),
        repo="example-org/example project/example-repo",
        pr_id="101",
        cursor=None,
    )

    assert captured_urls == [
        "https://dev.azure.com/example/proj/_apis/git/repositories/example-repo/pullRequests/101/threads"
    ]


def test_worker_logs_audit_for_new_feedback(tmp_path: Path) -> None:
    store = PrFeedbackStore(tmp_path / "runtime" / "pr-feedback-store.json")
    audit = AuditLogger(tmp_path / "runtime" / "audit.log")

    class StubAdapter:
        provider_name = "github"

        def poll(self, **kwargs: object):
            return type("Result", (), {
                "feedback": [
                    FeedbackEnvelope(
                        feedback_id="github:issue_comment:17",
                        provider="github",
                        repo="org/repo",
                        pr_id="77",
                        thread_id="issue_comment:17",
                        comment_id="17",
                        author="octocat",
                        body="looks good",
                        created_at=datetime(2026, 3, 21, 0, 0, 0, tzinfo=timezone.utc),
                    )
                ],
                "next_cursor": "2026-03-21T00:00:00Z",
            })()

    worker = PullRequestFeedbackWorker(
        store=store,
        adapters={"github": StubAdapter()},
        audit_logger=audit,
    )

    first = worker.poll_once(
        client=object(),
        provider="github",
        repo="org/repo",
        pr_id="77",
        stream_bindings=[{"session_id": "session-123", "vm_id": "vm-9", "source": "session_manifest"}],
    )
    second = worker.poll_once(client=object(), provider="github", repo="org/repo", pr_id="77")

    assert len(first) == 1
    assert second == []

    lines = (tmp_path / "runtime" / "audit.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_type"] == "feedback_detected"
    assert payload["provider"] == "github"
    assert payload["repo"] == "org/repo"
    assert payload["pr_id"] == "77"
    assert payload["args"]["stream_bindings"] == [
        {"session_id": "session-123", "source": "session_manifest", "vm_id": "vm-9"}
    ]
