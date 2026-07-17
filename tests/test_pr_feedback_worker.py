from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from evander.audit import AuditLogger
from evander.config import GatewayConfig
import evander.daemons.pr_feedback_worker as daemon_pr_feedback_worker
from evander.daemons.pr_feedback_worker import (
    PullRequestFeedbackWorker,
    build_pr_feedback_worker,
)
from evander.pr_feedback import (
    FeedbackEnvelope,
    FeedbackProviderAuthError,
    FeedbackProviderConfigurationError,
    GitHubFeedbackAdapter,
    PrFeedbackStore,
    AzureDevOpsFeedbackAdapter,
    annotate_agent_directed_feedback,
    pr_feedback_item_payload,
)
from evander.models import PrFeedbackItem
from evander.worker_directory_context import ShadowDomainContext, WorkerDirectoryContext


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

    payload = json.loads(
        (tmp_path / "runtime" / "pr-feedback-store.json").read_text(encoding="utf-8")
    )
    assert list(payload["feedback"].keys()) == ["github:issue_comment:1"]


def test_pr_feedback_daemon_reexports_match_shared_package() -> None:
    assert daemon_pr_feedback_worker.FeedbackEnvelope is FeedbackEnvelope
    assert daemon_pr_feedback_worker.PrFeedbackStore is PrFeedbackStore
    assert daemon_pr_feedback_worker.GitHubFeedbackAdapter is GitHubFeedbackAdapter
    assert daemon_pr_feedback_worker.AzureDevOpsFeedbackAdapter is AzureDevOpsFeedbackAdapter


def test_agent_directed_annotation_marks_self_authored_feedback_not_directed() -> None:
    item = PrFeedbackItem(
        feedback_id="azure_devops:thread_comment:44:5",
        provider="azure_devops",
        repo="org/repo",
        pr_id="7566",
        thread_id="44",
        comment_id="5",
        author="evander Bot",
        body="@<C74221CB-EE08-6A83-9055-AF01C3333112> I handled this.",
        created_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
        metadata={
            "mentioned_ids": ["c74221cb-ee08-6a83-9055-af01c3333112"],
            "prior_thread_comment_author_names": ["evander Bot"],
        },
    )

    annotated = annotate_agent_directed_feedback(
        item,
        agent_author_names=["evander Bot"],
        agent_mention_names=["Truman Bot"],
        agent_mention_ids=["c74221cb-ee08-6a83-9055-af01c3333112"],
    )

    assert annotated.metadata["agent_directed"] is False
    assert annotated.metadata["agent_directed_author_self"] is True
    assert annotated.metadata["agent_directed_reason"] == "self_authored"


def test_pr_feedback_store_preserves_raw_embedded_inline_media_payload(
    tmp_path: Path,
) -> None:
    store = PrFeedbackStore(tmp_path / "runtime" / "pr-feedback-store.json")
    store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="feedback-1",
            provider="github",
            repo="org/repo",
            pr_id="123",
            comment_id="comment-1",
            author="octocat",
            body="Screenshot attached",
            created_at=datetime(2026, 3, 21, tzinfo=timezone.utc),
            inline_media=[
                {
                    "media_id": "media-1",
                    "filename": "review.png",
                    "mime_type": "image/png",
                    "content_base64": base64.b64encode(b"png-bytes").decode("ascii"),
                }
            ],
        )
    )

    raw_payload = json.loads(
        (tmp_path / "runtime" / "pr-feedback-store.json").read_text(encoding="utf-8")
    )
    stored_media = raw_payload["feedback"]["feedback-1"]["inline_media"][0]
    listed = store.list_feedback_for_streams(
        streams=[{"provider": "github", "repo": "org/repo", "pr_id": "123"}],
        after={("github", "org/repo", "123"): None},
    )
    public_media = pr_feedback_item_payload(listed[0])["inline_media"][0]

    assert stored_media["content_base64"] == base64.b64encode(b"png-bytes").decode("ascii")
    assert public_media["source_kind"] == "pr_feedback"
    assert public_media["provider"] == "github"
    assert public_media["resource_id"] == "feedback-1"
    assert public_media["comment_id"] == "comment-1"
    assert "content_base64" not in public_media["metadata"]


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


def test_pr_feedback_store_lists_feedback_by_stream_and_marks_session_read(tmp_path: Path) -> None:
    store = PrFeedbackStore(tmp_path / "runtime" / "pr-feedback-store.json")
    stream = {"provider": "github", "repo": "org/repo", "pr_id": "123"}
    store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="feedback-1",
            provider="github",
            repo="org/repo",
            pr_id="123",
            author="octocat",
            body="first",
            created_at=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        )
    )
    store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="feedback-2",
            provider="github",
            repo="org/repo",
            pr_id="123",
            author="octocat",
            body="second",
            created_at=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
        )
    )

    items = store.list_feedback_for_streams(
        streams=[stream],
        after={("github", "org/repo", "123"): "2026-03-20T13:00:00Z"},
    )
    updated = store.mark_session_streams_read(
        session_id="session-1",
        streams=[stream],
        cursor="2026-03-21T12:00:00Z",
    )

    assert [item.feedback_id for item in items] == ["feedback-2"]
    assert updated == {"github:org/repo:123": "2026-03-21T12:00:00Z"}
    assert store.get_session_cursor("session-1", "github:org/repo:123") == "2026-03-21T12:00:00Z"


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


def test_pr_feedback_store_compacts_old_feedback_once_stream_cursor_has_advanced(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "pr-feedback-store.json"
    store = PrFeedbackStore(path)
    old_envelope = FeedbackEnvelope(
        feedback_id="github:issue_comment:1",
        provider="github",
        repo="org/repo",
        pr_id="123",
        thread_id="issue_comment:1",
        comment_id="1",
        author="octocat",
        body="Old feedback",
        created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )

    assert store.add_feedback_if_new(old_envelope) is True
    store.set_cursor("github:org/repo:123", "2099-01-01T00:00:00Z")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["feedback"] == {}


def test_pr_feedback_store_keeps_recent_feedback_even_when_stream_cursor_has_advanced(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "pr-feedback-store.json"
    store = PrFeedbackStore(path)
    recent_envelope = FeedbackEnvelope(
        feedback_id="github:issue_comment:1",
        provider="github",
        repo="org/repo",
        pr_id="123",
        thread_id="issue_comment:1",
        comment_id="1",
        author="octocat",
        body="Recent feedback",
        created_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )

    assert store.add_feedback_if_new(recent_envelope) is True
    store.set_cursor("github:org/repo:123", "2099-01-02T00:00:00Z")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert list(payload["feedback"]) == ["github:issue_comment:1"]


def test_pr_feedback_store_prune_streams_removes_matching_feedback_and_cursor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "pr-feedback-store.json"
    store = PrFeedbackStore(path)
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    other_created_at = created_at + timedelta(seconds=1)
    cursor = created_at.isoformat().replace("+00:00", "Z")
    other_cursor = other_created_at.isoformat().replace("+00:00", "Z")
    assert store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="github:issue_comment:1",
            provider="github",
            repo="org/repo",
            pr_id="123",
            thread_id="issue_comment:1",
            comment_id="1",
            author="octocat",
            body="Needs tests",
            created_at=created_at,
        )
    )
    assert store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="github:issue_comment:2",
            provider="github",
            repo="other/repo",
            pr_id="999",
            thread_id="issue_comment:2",
            comment_id="2",
            author="octocat",
            body="Keep me",
            created_at=other_created_at,
        )
    )
    store.set_cursor("github:org/repo:123", cursor)
    store.set_cursor("github:other/repo:999", other_cursor)

    removed_cursors, removed_feedback = store.prune_streams({("github", "org/repo", "123")})

    assert removed_cursors == 1
    assert removed_feedback == 1
    payload = store.read_raw()
    assert payload["cursors"] == {"github:other/repo:999": other_cursor}
    assert list(payload["feedback"]) == ["github:issue_comment:2"]


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
    mapped = GitHubFeedbackAdapter._map_review_comments(
        repo="org/repo", pr_id="9", comments=comments
    )

    assert len(mapped) == 1
    envelope = mapped[0]
    assert envelope.feedback_id == "github:review_comment:55"
    assert envelope.thread_id == "7"
    assert envelope.line == 98
    assert envelope.body == ""
    assert envelope.in_reply_to == "54"


def test_github_mapping_extracts_markdown_inline_images() -> None:
    comments = [
        {
            "id": 56,
            "user": {"login": "reviewer"},
            "body": "Please compare ![actual screenshot](https://example.test/uploads/actual.png)",
            "created_at": "2026-03-20T12:00:00Z",
        }
    ]

    mapped = GitHubFeedbackAdapter._map_issue_comments(
        repo="org/repo", pr_id="9", comments=comments
    )

    assert len(mapped) == 1
    media = mapped[0].inline_media
    assert len(media) == 1
    assert media[0].source_kind == "pr_feedback"
    assert media[0].provider == "github"
    assert media[0].resource_id == "github:issue_comment:56"
    assert media[0].comment_id == "56"
    assert media[0].media_id == "markdown-image-0"
    assert media[0].filename == "actual.png"
    assert media[0].mime_type == "image/png"
    assert media[0].url == "https://example.test/uploads/actual.png"
    assert media[0].metadata["alt"] == "actual screenshot"


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


def test_ado_mapping_extracts_html_inline_images_and_attachment_links() -> None:
    adapter = AzureDevOpsFeedbackAdapter("https://dev.azure.com/example", "proj", pat="test-pat")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "value": [
                    {
                        "id": 45,
                        "status": "active",
                        "comments": [
                            {
                                "id": 93,
                                "author": {"displayName": "Dev One"},
                                "content": (
                                    "<p>See <img alt='diff' "
                                    "src='https://dev.azure.com/org/project/_apis/wit/attachments/abc?fileName=diff.png' /></p>"
                                    "<p><a href='https://dev.azure.com/org/project/_apis/wit/attachments/def'>expected.jpg</a></p>"
                                ),
                                "publishedDate": "2026-03-21T01:00:00Z",
                                "isDeleted": False,
                            },
                        ],
                    }
                ]
            }

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    result = adapter.poll(client=FakeClient(), repo="repo-1", pr_id="101", cursor=None)

    assert len(result.feedback) == 1
    media = result.feedback[0].inline_media
    assert len(media) == 2
    assert media[0].source_kind == "pr_feedback"
    assert media[0].provider == "azure_devops"
    assert media[0].resource_id == "azure_devops:thread_comment:45:93"
    assert media[0].comment_id == "93"
    assert media[0].media_id == "ado-img-0"
    assert media[0].filename == "diff.png"
    assert media[0].mime_type == "image/png"
    assert media[0].metadata["source"] == "html_image"
    assert media[0].metadata["alt"] == "diff"
    assert media[1].media_id == "ado-a-1"
    assert media[1].filename == "expected.jpg"
    assert media[1].mime_type == "image/jpeg"
    assert media[1].metadata["source"] == "ado_attachment_link"


def test_ado_mapping_extracts_markdown_pr_attachment_images() -> None:
    adapter = AzureDevOpsFeedbackAdapter("https://dev.azure.com/example", "proj", pat="test-pat")
    attachment_url = (
        "https://dev.azure.com/exampleindustry/70a1569b-16ac-476e-a6b5-44830b54dcc7/"
        "_apis/git/repositories/d30d0ddf-69c9-4a6b-98e4-1bc60506b8a1/"
        "pullRequests/6690/attachments/image%20%282%29.png"
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "value": [
                    {
                        "id": 33772,
                        "status": "active",
                        "comments": [
                            {
                                "id": 8,
                                "author": {"displayName": "Jonathan Quinth"},
                                "content": (
                                    "Are you able to load this screenshot into your workspace and view it?\n"
                                    f"![image (2).png]({attachment_url})\n"
                                    "![external](https://example.test/uploads/attachment.png)"
                                ),
                                "publishedDate": "2026-05-11T09:33:42.277000Z",
                                "isDeleted": False,
                            },
                        ],
                    }
                ]
            }

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    result = adapter.poll(client=FakeClient(), repo="repo-1", pr_id="6690", cursor=None)

    assert len(result.feedback) == 1
    media = result.feedback[0].inline_media
    assert len(media) == 1
    assert media[0].source_kind == "pr_feedback"
    assert media[0].provider == "azure_devops"
    assert media[0].resource_id == "azure_devops:thread_comment:33772:8"
    assert media[0].comment_id == "8"
    assert media[0].media_id == "ado-markdown-image-0"
    assert media[0].filename == "image (2).png"
    assert media[0].mime_type == "image/png"
    assert media[0].url == attachment_url
    assert media[0].metadata["source"] == "ado_markdown_image"
    assert media[0].metadata["alt"] == "image (2).png"


def test_ado_mapping_thread_edge_cases() -> None:
    adapter = AzureDevOpsFeedbackAdapter("https://dev.azure.com/example", "proj", pat="test-pat")

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


def test_ado_mapping_exposes_thread_authors_and_mentions_for_filters() -> None:
    adapter = AzureDevOpsFeedbackAdapter("https://dev.azure.com/example", "proj", pat="test-pat")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "value": [
                    {
                        "id": 44,
                        "status": "active",
                        "comments": [
                            {
                                "id": 1,
                                "author": {
                                    "displayName": "evander Bot",
                                    "uniqueName": "evander@example.com",
                                    "id": "bot-id",
                                },
                                "content": "Please update this.",
                                "publishedDate": "2026-03-21T01:00:00Z",
                                "isDeleted": False,
                            },
                            {
                                "id": 2,
                                "author": {"displayName": "Dev One"},
                                "content": (
                                    "@<a href='#' data-vss-mention='version:2.0'>"
                                    "Truman Bot</a> can you make the fix?"
                                ),
                                "publishedDate": "2026-03-21T01:00:01Z",
                                "isDeleted": False,
                            },
                        ],
                    },
                    {
                        "id": 45,
                        "status": "active",
                        "comments": [
                            {
                                "id": 1,
                                "author": {"displayName": "Dev Two"},
                                "content": (
                                    "@<C74221CB-EE08-6A83-9055-AF01C3333112> can you make the fix?"
                                ),
                                "publishedDate": "2026-03-21T01:00:02Z",
                                "isDeleted": False,
                            },
                        ],
                    },
                ]
            }

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    result = adapter.poll(client=FakeClient(), repo="repo-1", pr_id="101", cursor=None)

    assert len(result.feedback) == 3
    metadata = result.feedback[1].metadata
    assert metadata["prior_thread_comment_author_names"] == ["evander Bot"]
    assert metadata["prior_thread_comment_author_unique_names"] == ["evander@example.com"]
    assert metadata["prior_thread_comment_author_ids"] == ["bot-id"]
    assert metadata["mentioned_names"] == ["Truman Bot"]
    assert metadata["thread_comment_count"] == 2
    compact_metadata = result.feedback[2].metadata
    assert compact_metadata["prior_thread_comment_author_names"] == []
    assert compact_metadata["mentioned_names"] == []
    assert compact_metadata["mentioned_ids"] == ["c74221cb-ee08-6a83-9055-af01c3333112"]
    assert compact_metadata["thread_comment_count"] == 1


def test_ado_mapping_filters_comments_at_or_before_cursor_and_keeps_next_cursor() -> None:
    adapter = AzureDevOpsFeedbackAdapter("https://dev.azure.com/example", "proj", pat="test-pat")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "value": [
                    {
                        "id": 44,
                        "status": "active",
                        "comments": [
                            {
                                "id": 90,
                                "author": {"displayName": "Dev Zero"},
                                "content": "old",
                                "publishedDate": "2026-03-21T00:00:00Z",
                                "isDeleted": False,
                            },
                            {
                                "id": 91,
                                "author": {"displayName": "Dev One"},
                                "content": "new",
                                "publishedDate": "2026-03-21T00:00:01Z",
                                "isDeleted": False,
                            },
                        ],
                    }
                ]
            }

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    result = adapter.poll(
        client=FakeClient(),
        repo="repo-1",
        pr_id="101",
        cursor="2026-03-21T00:00:00Z",
    )

    assert [item.feedback_id for item in result.feedback] == ["azure_devops:thread_comment:44:91"]
    assert result.next_cursor == "2026-03-21T00:00:01Z"


def test_ado_mapping_skips_system_review_state_change_comments_and_advances_cursor() -> None:
    adapter = AzureDevOpsFeedbackAdapter("https://dev.azure.com/example", "proj", pat="test-pat")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "value": [
                    {
                        "id": 29766,
                        "status": "active",
                        "comments": [
                            {
                                "id": 1,
                                "author": {"displayName": "Microsoft.VisualStudio.Services.TFS"},
                                "content": "Kamil Niescieronek declined to review",
                                "publishedDate": "2026-03-24T15:21:52.040000Z",
                                "isDeleted": False,
                            }
                        ],
                    }
                ]
            }

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    result = adapter.poll(
        client=FakeClient(),
        repo="exampleindustry/Example Industry APP/ExampleIndustryAPP",
        pr_id="5822",
        cursor=None,
    )

    assert result.feedback == []
    assert result.next_cursor == "2026-03-24T15:21:52.040000Z"


def test_ado_mapping_skips_reviewer_membership_and_vote_events() -> None:
    adapter = AzureDevOpsFeedbackAdapter("https://dev.azure.com/example", "proj", pat="test-pat")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "value": [
                    {
                        "id": 29683,
                        "status": "active",
                        "comments": [
                            {
                                "id": 1,
                                "author": {"displayName": "Microsoft.VisualStudio.Services.TFS"},
                                "content": "Kamil Niescieronek joined as a reviewer",
                                "publishedDate": "2026-03-24T09:01:41.927000Z",
                                "isDeleted": False,
                            }
                        ],
                    },
                    {
                        "id": 29685,
                        "status": "active",
                        "comments": [
                            {
                                "id": 1,
                                "author": {"displayName": "Microsoft.VisualStudio.Services.TFS"},
                                "content": "Kamil Niescieronek added Jonathan Quinth as a reviewer",
                                "publishedDate": "2026-03-24T09:01:54.303000Z",
                                "isDeleted": False,
                            }
                        ],
                    },
                    {
                        "id": 29690,
                        "status": "active",
                        "comments": [
                            {
                                "id": 1,
                                "author": {"displayName": "Microsoft.VisualStudio.Services.TFS"},
                                "content": "Kamil Niescieronek is changed to be a required reviewer",
                                "publishedDate": "2026-03-24T10:07:32.147000Z",
                                "isDeleted": False,
                            }
                        ],
                    },
                    {
                        "id": 29746,
                        "status": "active",
                        "comments": [
                            {
                                "id": 1,
                                "author": {"displayName": "Kamil Niescieronek"},
                                "content": "Kamil Niescieronek voted 5",
                                "publishedDate": "2026-03-24T14:03:03.637000Z",
                                "isDeleted": False,
                            }
                        ],
                    },
                ]
            }

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    result = adapter.poll(
        client=FakeClient(),
        repo="exampleindustry/Example Industry APP/ExampleIndustryAPP",
        pr_id="5822",
        cursor=None,
    )

    assert result.feedback == []
    assert result.next_cursor == "2026-03-24T14:03:03.637000Z"


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
    assert all(
        headers["Authorization"] == "Bearer gh-test-token"
        for headers in captured_headers
        if headers
    )
    assert all(
        headers["Accept"] == "application/vnd.github+json"
        for headers in captured_headers
        if headers
    )


def test_ado_adapter_sends_basic_auth_header_when_pat_configured() -> None:
    adapter = AzureDevOpsFeedbackAdapter(
        "https://dev.azure.com/example", "proj", pat="ado-test-pat"
    )
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


def test_ado_adapter_requires_pat_env_when_pat_missing() -> None:
    adapter = AzureDevOpsFeedbackAdapter(
        "https://dev.azure.com/example",
        "proj",
        pat=None,
        pat_env_name="TEAM_ADO_PAT",
    )

    class FakeClient:
        def get(self, *args: object, **kwargs: object):  # noqa: ANN003
            raise AssertionError("client.get should not be called without a PAT")

    with pytest.raises(FeedbackProviderConfigurationError, match="TEAM_ADO_PAT"):
        adapter.poll(client=FakeClient(), repo="repo-1", pr_id="101", cursor=None)


def test_ado_adapter_raises_auth_error_on_401() -> None:
    adapter = AzureDevOpsFeedbackAdapter(
        "https://dev.azure.com/example",
        "proj",
        pat="ado-test-pat",
        pat_env_name="TEAM_ADO_PAT",
    )

    class FakeResponse:
        status_code = 401

        def raise_for_status(self) -> None:
            request = httpx.Request(
                "GET",
                "https://dev.azure.com/example/proj/_apis/git/repositories/repo-1/pullRequests/101/threads",
            )
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    with pytest.raises(FeedbackProviderAuthError, match="TEAM_ADO_PAT"):
        adapter.poll(client=FakeClient(), repo="repo-1", pr_id="101", cursor=None)


def test_ado_adapter_normalizes_composite_repo_identifier() -> None:
    adapter = AzureDevOpsFeedbackAdapter(
        "https://dev.azure.com/example", "proj", pat="ado-test-pat"
    )
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
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    cursor = created_at.isoformat().replace("+00:00", "Z")

    class StubAdapter:
        provider_name = "github"

        def poll(self, **kwargs: object):
            return type(
                "Result",
                (),
                {
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
                            created_at=created_at,
                        )
                    ],
                    "next_cursor": cursor,
                },
            )()

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
        stream_bindings=[
            {"session_id": "session-123", "vm_id": "vm-9", "source": "session_manifest"}
        ],
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


def test_worker_enriches_stream_bindings_with_directory_context(tmp_path: Path) -> None:
    store = PrFeedbackStore(tmp_path / "runtime" / "pr-feedback-store.json")
    audit = AuditLogger(tmp_path / "runtime" / "audit.log")

    class StubAdapter:
        provider_name = "github"

        def poll(self, **kwargs: object):
            return type(
                "Result",
                (),
                {
                    "feedback": [
                        FeedbackEnvelope(
                            feedback_id="github:issue_comment:18",
                            provider="github",
                            repo="org/repo",
                            pr_id="77",
                            thread_id="issue_comment:18",
                            comment_id="18",
                            author="octocat",
                            body="check shadow config",
                            created_at=datetime(2026, 3, 21, 0, 0, 1, tzinfo=timezone.utc),
                        )
                    ],
                    "next_cursor": "2026-03-21T00:00:01Z",
                },
            )()

    class StubResolver:
        def resolve_for_session(
            self, session_id: str | None, *, fallback_directory_path: str | None = None, source: str
        ):
            return WorkerDirectoryContext(
                directory_path=fallback_directory_path or "root/team-a",
                source=source,
                session_id=session_id,
                domains=(ShadowDomainContext(domain="workers", diff_paths=("pool.max",)),),
            )

    worker = PullRequestFeedbackWorker(
        store=store,
        adapters={"github": StubAdapter()},
        audit_logger=audit,
        directory_context_resolver=StubResolver(),
    )

    worker.poll_once(
        client=object(),
        provider="github",
        repo="org/repo",
        pr_id="77",
        stream_bindings=[
            {
                "session_id": "session-123",
                "vm_id": "vm-9",
                "source": "session_manifest",
                "directory_path": "root/team-a",
            }
        ],
    )

    payload = json.loads((tmp_path / "runtime" / "audit.log").read_text(encoding="utf-8").strip())
    assert payload["args"]["stream_bindings"] == [
        {
            "session_id": "session-123",
            "source": "session_manifest",
            "vm_id": "vm-9",
            "directory_path": "root/team-a",
            "directory_context": {
                "directory_path": "root/team-a",
                "source": "session_manifest",
                "session_id": "session-123",
                "domains": [
                    {"domain": "workers", "diff_paths": ["pool.max"], "validation_errors": []}
                ],
            },
        }
    ]


def test_build_pr_feedback_worker_keeps_global_behavior_when_directory_scoped_config_disabled(
    tmp_path: Path,
) -> None:
    worker = build_pr_feedback_worker(
        tmp_path / "runtime",
        adapters={},
        gateway_config=GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
    )

    assert worker.directory_context_resolver is None
