"""Pull-request feedback, queries, replies, and publication."""

from __future__ import annotations
from evander.pr_feedback.identity import (
    normalize_azure_devops_repo_identifier as _normalize_azure_devops_repo_identifier,  # noqa: F401 - legacy services export
)
from evander.optional_integrations import optional_azure_devops_class, require_azure_devops_module
from contextlib import contextmanager
import fcntl
import json
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Iterator, Literal, Mapping, Protocol
import httpx
from evander.ado_mentions import (
    normalize_ado_mention_refs,
)
from evander.models import (
    AdoPrCommentActivityResponse,
    CompletePullRequestResponse,
    AdoPrCommitActivityResponse,
    HasPendingFeedbackResponse,
    GetPullRequestResponse,
    ListPullRequestsResponse,
    ListUnreadFeedbackResponse,
    PostPrFeedbackReplyResponse,
    PullRequestCommit,
    PullRequestItem,
    PullRequestReviewer,
    PullRequestWorkItemsResponse,
    PrFeedbackItem,
    ResolveAdoIdentityResponse,
    UpdatePullRequestResponse,
)
from evander.pr_feedback.providers import (
    pr_feedback_item_payload,
)
from evander.pr_feedback.identity import (
    pr_feedback_stream_aliases,
)
from evander.pr_feedback.store import PrFeedbackRepository, PrFeedbackStore, _stream_key
from evander.integration_services.pr_comments import GitHubPrComments

from .common import (
    _IGNORED_FEEDBACK_RETENTION,
    _LOGGER,
    _normalize_optional_text,
    _normalize_string_list,
    _parse_dt,
    normalize_pr_automation_provider,
    normalize_pr_feedback_provider,
)


class PrFeedbackService:
    def __init__(
        self,
        store_path: Path,
        *,
        ignored_feedback_store_path: Path | None = None,
        pr_map_path: Path | None = None,
        repository: PrFeedbackRepository | None = None,
    ) -> None:
        self.store_path = store_path
        self.store: PrFeedbackRepository = repository or PrFeedbackStore(store_path)
        self.ignored_feedback_store_path = ignored_feedback_store_path
        self.pr_map_path = pr_map_path

    def has_pending_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None = None,
        cursor: str | None = None,
    ) -> HasPendingFeedbackResponse:
        feedback = self._list_feedback(
            streams=streams,
            session_id=session_id,
            cursor=cursor,
            limit=None,
            persist_session_cursor=False,
            exclude_ignored_feedback=True,
        )
        return HasPendingFeedbackResponse(
            pending=bool(feedback.feedback),
            next_cursor=feedback.next_cursor,
        )

    def list_unread_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> ListUnreadFeedbackResponse:
        return self._list_feedback(
            streams=streams,
            session_id=session_id,
            cursor=cursor,
            limit=limit,
            persist_session_cursor=True,
            exclude_ignored_feedback=True,
        )

    def list_all_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        limit: int | None = None,
    ) -> ListUnreadFeedbackResponse:
        return self._list_feedback(
            streams=streams,
            session_id=None,
            cursor=None,
            limit=limit,
            persist_session_cursor=False,
            exclude_ignored_feedback=False,
        )

    def _list_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None,
        cursor: str | None,
        limit: int | None,
        persist_session_cursor: bool,
        exclude_ignored_feedback: bool,
    ) -> ListUnreadFeedbackResponse:
        alias_groups = [
            pr_feedback_stream_aliases(stream, pr_map_path=self.pr_map_path) for stream in streams
        ]
        expanded_streams: list[dict[str, str]] = []
        for aliases in alias_groups:
            for alias in aliases:
                if alias not in expanded_streams:
                    expanded_streams.append(alias)

        stream_cursors: dict[tuple[str, str, str], str | None] = {}
        for aliases in alias_groups:
            effective_cursor = self._effective_cursor(
                session_id=session_id,
                streams=aliases,
                explicit_cursor=cursor,
            )
            for alias in aliases:
                key = (alias["provider"], alias["repo"], alias["pr_id"])
                stream_cursors[key] = self._max_cursor(
                    stream_cursors.get(key),
                    effective_cursor,
                )
        ignored_feedback_ids = (
            self._read_ignored_feedback_ids() if exclude_ignored_feedback else None
        )
        items = [
            PrFeedbackItem.model_validate(pr_feedback_item_payload(item))
            for item in self.store.list_feedback_for_streams(
                streams=expanded_streams,
                after=stream_cursors,
                exclude_feedback_ids=ignored_feedback_ids,
                limit=limit,
            )
        ]
        next_cursor = (
            items[-1].created_at.isoformat().replace("+00:00", "Z")
            if items
            else self._max_cursors(stream_cursors.values())
        )
        if persist_session_cursor and session_id is not None:
            self.store.mark_session_streams_read(
                session_id=session_id,
                streams=expanded_streams,
                cursor=next_cursor if next_cursor is not None else cursor,
            )
        return ListUnreadFeedbackResponse(feedback=items, next_cursor=next_cursor)

    def _read_store(self) -> dict[str, Any]:
        payload = self.store.read_raw()
        if "feedback" not in payload:
            payload["feedback"] = {}
        return payload

    def _read_ignored_feedback_ids(self) -> set[str]:
        path = self.ignored_feedback_store_path
        if path is None or not path.exists():
            return set()
        with _exclusive_json_file_lock(path):
            if not path.exists():
                return set()
            payload = json.loads(path.read_text(encoding="utf-8"))
            pruned_payload, feedback_ids, changed = _normalize_ignored_feedback_payload(payload)
            if changed:
                _write_json_atomic(path, pruned_payload)
        return feedback_ids

    def _effective_cursor(
        self,
        *,
        session_id: str | None,
        streams: list[dict[str, str]],
        explicit_cursor: str | None,
    ) -> str | None:
        if session_id is None:
            return explicit_cursor
        return self._max_cursors(
            [
                explicit_cursor,
                *[
                    self.store.get_session_cursor(
                        session_id,
                        _stream_key(stream["provider"], stream["repo"], stream["pr_id"]),
                    )
                    for stream in streams
                ],
            ]
        )

    def _max_cursor(self, first: str | None, second: str | None) -> str | None:
        if first is None:
            return second
        if second is None:
            return first
        if self._parse_cursor(first) >= self._parse_cursor(second):
            return first
        return second

    def _max_cursors(self, cursors: Any) -> str | None:
        maximum: str | None = None
        for cursor in cursors:
            maximum = self._max_cursor(maximum, cursor)
        return maximum

    @staticmethod
    def _parse_cursor(cursor: str) -> datetime:
        normalized = cursor.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


def _pr_artifact_filename(artifact: dict[str, Any]) -> str:
    return (
        str(
            artifact.get("normalized_safe_filename")
            or artifact.get("filename")
            or artifact.get("original_filename")
            or "attachment"
        ).strip()
        or "attachment"
    )


def _pr_artifact_mime_type(artifact: dict[str, Any]) -> str | None:
    mime_type = str(artifact.get("mime_type") or "").strip()
    return mime_type or mimetypes.guess_type(_pr_artifact_filename(artifact))[0]


def _pr_artifact_is_image(artifact: dict[str, Any]) -> bool:
    mime_type = _pr_artifact_mime_type(artifact)
    if mime_type and mime_type.lower().startswith("image/"):
        return True
    guessed, _encoding = mimetypes.guess_type(_pr_artifact_filename(artifact))
    return bool(guessed and guessed.lower().startswith("image/"))


def _pr_artifact_bytes(artifact: dict[str, Any]) -> bytes:
    path = str(artifact.get("path") or "").strip()
    if not path:
        raise ValueError("Published artifact payload is missing path")
    return Path(path).read_bytes()


def _append_markdown_attachment_links(
    body_text: str, uploaded_attachments: list[dict[str, Any]]
) -> str:
    if not uploaded_attachments:
        return body_text
    lines = [body_text.rstrip(), ""]
    for item in uploaded_attachments:
        filename = str(item.get("filename") or "attachment").strip() or "attachment"
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        escaped_label = filename.replace("[", "\\[").replace("]", "\\]")
        if item.get("is_image"):
            lines.append(f"![{escaped_label}]({url})")
        else:
            lines.append(f"[{escaped_label}]({url})")
    return "\n".join(lines).rstrip()


def _reject_unsupported_attachments(
    provider: str, attachments: list[dict[str, Any]] | None
) -> None:
    if attachments:
        raise ValueError(f"{provider} PR publishing does not support artifact attachments")


def _reject_unsupported_comment_attachments(provider: str, comments: list[dict[str, Any]]) -> None:
    if any(comment.get("attachments") for comment in comments):
        raise ValueError(f"{provider} PR review comments do not support artifact attachments")


def _reject_unsupported_mentions(provider: str, mentions: list[str] | None) -> None:
    if normalize_ado_mention_refs(mentions):
        raise ValueError(f"{provider} PR comments do not support ADO mentions")



def _ado_resolved_mention_payloads(*args, **kwargs):
    return require_azure_devops_module("evander_azure_devops_addon.pull_requests")._ado_resolved_mention_payloads(*args, **kwargs)




class PrReplyProvider(Protocol):
    def post_reply(
        self,
        *,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
        feedback_kind: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> PostPrFeedbackReplyResponse: ...


class PrQueryProvider(Protocol):
    def complete_pull_request(
        self, *, repo: str, pr_id: str, expected_source_commit: str,
        expected_target_branch: str,
    ) -> CompletePullRequestResponse: ...

    def list_pull_requests(
        self,
        *,
        repo: str,
        project: str | None = None,
        status: str | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        source_ref_name: str | None = None,
        target_ref_name: str | None = None,
        source_repository_id: str | None = None,
        repository_id: str | None = None,
        min_time: str | None = None,
        max_time: str | None = None,
        query_time_range_type: str | None = None,
        include_links: bool | None = None,
        max_comment_length: int | None = None,
        tags: str | None = None,
        top: int | None = None,
        skip: int | None = None,
        cursor: str | None = None,
    ) -> ListPullRequestsResponse: ...

    def get_pull_request(
        self,
        *,
        repo: str,
        pr_id: str,
        include_reviewers: bool | None = None,
        include_commits: bool | None = None,
    ) -> GetPullRequestResponse: ...

    def get_pull_request_work_items(
        self,
        *,
        repo: str,
        pr_id: str,
    ) -> PullRequestWorkItemsResponse: ...


class GitHubPrReplyProvider:
    def __init__(
        self,
        *,
        token: str,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def post_reply(
        self,
        *,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
        feedback_kind: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> PostPrFeedbackReplyResponse:
        _reject_unsupported_mentions("GitHub", mentions)
        _reject_unsupported_attachments("GitHub", attachments)
        normalized_feedback_kind = _normalize_github_feedback_kind(feedback_kind)
        if not comment_id:
            raise ValueError("GitHub reply posting requires comment_id")
        if normalized_feedback_kind == "review_comment":
            url = f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/comments/{comment_id}/replies"
        else:
            # GitHub issue comments and whole-review feedback do not expose a
            # threaded reply endpoint.  A PR conversation comment is the valid
            # response surface for both kinds.
            url = f"{self.api_base_url}/repos/{repo}/issues/{pr_id}/comments"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"body": body_text},
            )
            response.raise_for_status()
            payload = response.json()
        return PostPrFeedbackReplyResponse(
            status="posted",
            provider="github",
            repo=repo,
            pr_id=pr_id,
            thread_id=thread_id,
            comment_id=comment_id,
            feedback_kind=(
                "review_comment"
                if normalized_feedback_kind == "review_comment"
                else "issue_comment"
            ),
            reply_id=str(payload.get("id", "")) or None,
            reply_url=payload.get("html_url") or payload.get("url"),
        )






class GitHubPrQueryProvider:
    def __init__(
        self,
        *,
        token: str | None = None,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.token = token.strip() if token else None
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def complete_pull_request(
        self, *, repo: str, pr_id: str, expected_source_commit: str,
        expected_target_branch: str,
    ) -> CompletePullRequestResponse:
        from .github_pr_completion import complete_github_pull_request

        if not self.token:
            raise ValueError("GitHub PR completion requires a configured token")
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
            return complete_github_pull_request(
                client=client, api_base_url=self.api_base_url,
                headers={**self._headers(), "X-GitHub-Api-Version": "2026-03-10"},
                repo=repo, pr_id=pr_id, expected_source_commit=expected_source_commit,
                expected_target_branch=expected_target_branch,
            )

    def list_pull_requests(
        self,
        *,
        repo: str,
        project: str | None = None,
        status: str | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        source_ref_name: str | None = None,
        target_ref_name: str | None = None,
        source_repository_id: str | None = None,
        repository_id: str | None = None,
        min_time: str | None = None,
        max_time: str | None = None,
        query_time_range_type: str | None = None,
        include_links: bool | None = None,
        max_comment_length: int | None = None,
        tags: str | None = None,
        top: int | None = None,
        skip: int | None = None,
        cursor: str | None = None,
    ) -> ListPullRequestsResponse:
        raise ValueError("GitHub PR listing is not supported")

    def get_pull_request(
        self,
        *,
        repo: str,
        pr_id: str,
        include_reviewers: bool | None = None,
        include_commits: bool | None = None,
    ) -> GetPullRequestResponse:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()

            base_payload = payload if isinstance(payload, dict) else {}
            raw_payload: dict[str, Any] = dict(base_payload)

            commits_payload: list[dict[str, Any]] = []
            if include_commits:
                commits_payload = self._get_paginated_items(
                    client,
                    f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/commits",
                )
                raw_payload["commits"] = commits_payload

            review_payload: list[dict[str, Any]] = []
            if include_reviewers:
                review_payload = self._get_paginated_items(
                    client,
                    f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/reviews",
                )
                raw_payload["reviews"] = review_payload

        return GetPullRequestResponse(
            provider="github",
            repo=repo,
            pr_id=pr_id,
            title=str(base_payload.get("title", "")).strip() or None,
            status=self._normalize_status(base_payload),
            url=(
                str(base_payload.get("html_url") or base_payload.get("url") or "").strip() or None
            ),
            source_branch=self._normalize_pull_branch(base_payload, branch_name="head", key="ref"),
            target_branch=self._normalize_pull_branch(base_payload, branch_name="base", key="ref"),
            source_commit=self._normalize_pull_branch(base_payload, branch_name="head", key="sha"),
            target_commit=self._normalize_pull_branch(base_payload, branch_name="base", key="sha"),
            created_by=self._normalize_created_by(base_payload),
            reviewers=self._normalize_reviewers(base_payload, review_payload, include_reviewers),
            commits=self._normalize_commits(commits_payload),
            raw=raw_payload,
        )

    def _get_paginated_items(self, client: httpx.Client, url: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        params: dict[str, str] | None = {"per_page": "100"}
        while next_url:
            response = client.get(next_url, params=params, headers=self._headers())
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                raw_items = payload
            elif isinstance(payload, dict):
                raw_items = payload.get("value")
            else:
                raw_items = []
            if isinstance(raw_items, list):
                items.extend(item for item in raw_items if isinstance(item, dict))
            next_url = self._next_page_url(response)
            params = None
        return items

    @staticmethod
    def _next_page_url(response: httpx.Response) -> str | None:
        next_link = response.links.get("next")
        if not isinstance(next_link, dict):
            return None
        next_url = str(next_link.get("url") or "").strip()
        return next_url or None

    def _normalize_status(self, payload: dict[str, Any]) -> str | None:
        merged = payload.get("merged")
        merged_at = payload.get("merged_at")
        if merged is True or str(merged_at or "").strip():
            return "merged"
        status = str(payload.get("state", "")).strip().lower()
        return status or None

    def _normalize_pull_branch(
        self,
        payload: dict[str, Any],
        *,
        branch_name: str,
        key: str,
    ) -> str | None:
        branch = payload.get(branch_name)
        if not isinstance(branch, dict):
            return None
        value = str(branch.get(key, "")).strip()
        return value or None

    def _normalize_created_by(self, payload: dict[str, Any]) -> str | None:
        user = payload.get("user")
        if not isinstance(user, dict):
            return None
        value = str(user.get("login", "")).strip()
        return value or None

    def _normalize_commits(self, commits_payload: list[dict[str, Any]]) -> list[PullRequestCommit]:
        commits: list[PullRequestCommit] = []
        for item in commits_payload:
            commit_id = str(item.get("sha", "")).strip()
            if not commit_id:
                continue
            commit_info = item.get("commit") if isinstance(item.get("commit"), dict) else {}
            message = str(commit_info.get("message", "")).strip() or None
            commits.append(
                PullRequestCommit(
                    commit_id=commit_id,
                    url=str(item.get("html_url") or item.get("url") or "").strip() or None,
                    comment=message,
                )
            )
        return commits

    def _normalize_reviewers(
        self,
        payload: dict[str, Any],
        review_payload: list[dict[str, Any]],
        include_reviewers: bool | None,
    ) -> list[PullRequestReviewer]:
        if include_reviewers is False:
            return []
        if include_reviewers is None:
            requested = payload.get("requested_reviewers")
            reviewer_items = requested if isinstance(requested, list) else []
            reviewers: list[PullRequestReviewer] = []
            for item in reviewer_items:
                if not isinstance(item, dict):
                    continue
                reviewer_id = self._github_reviewer_id(item)
                if reviewer_id is None:
                    continue
                reviewers.append(
                    PullRequestReviewer(
                        reviewer_id=reviewer_id,
                        display_name=self._github_reviewer_display_name(item),
                        status="requested",
                    )
                )
            return reviewers

        latest_review_by_reviewer: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
        for index, review in enumerate(review_payload):
            reviewer_id = self._github_reviewer_id(review)
            if reviewer_id is None:
                continue
            sort_key = self._github_review_sort_key(review, index)
            existing = latest_review_by_reviewer.get(reviewer_id)
            if existing is None or sort_key > existing[:2]:
                latest_review_by_reviewer[reviewer_id] = (sort_key[0], sort_key[1], review)

        reviewers = [
            self._review_to_reviewer(reviewer_id=reviewer_id, review=review)
            for reviewer_id, (_sort_time, _sort_index, review) in latest_review_by_reviewer.items()
        ]

        requested = payload.get("requested_reviewers")
        requested_reviewers = requested if isinstance(requested, list) else []
        present_ids = {
            reviewer.reviewer_id.casefold() for reviewer in reviewers if reviewer.reviewer_id
        }
        for item in requested_reviewers:
            if not isinstance(item, dict):
                continue
            reviewer_id = self._github_reviewer_id(item)
            if reviewer_id is None or reviewer_id.casefold() in present_ids:
                continue
            reviewers.append(
                PullRequestReviewer(
                    reviewer_id=reviewer_id,
                    display_name=self._github_reviewer_display_name(item),
                    status="requested",
                )
            )
            present_ids.add(reviewer_id.casefold())
        return reviewers

    def _github_reviewer_id(self, payload: dict[str, Any]) -> str | None:
        user = payload.get("user") if isinstance(payload.get("user"), dict) else payload
        if not isinstance(user, dict):
            return None
        value = str(user.get("login") or user.get("id") or "").strip()
        return value or None

    def _github_reviewer_display_name(self, payload: dict[str, Any]) -> str | None:
        user = payload.get("user") if isinstance(payload.get("user"), dict) else payload
        if not isinstance(user, dict):
            return None
        value = str(user.get("login") or user.get("name") or user.get("id") or "").strip()
        return value or None

    def _github_review_sort_key(self, review: dict[str, Any], index: int) -> tuple[datetime, int]:
        submitted_at = str(review.get("submitted_at") or review.get("submittedAt") or "").strip()
        if submitted_at:
            try:
                return (_parse_dt(submitted_at), index)
            except ValueError:
                pass
        return (datetime.min.replace(tzinfo=UTC), index)

    def _review_to_reviewer(
        self, *, reviewer_id: str, review: dict[str, Any]
    ) -> PullRequestReviewer:
        user = review.get("user") if isinstance(review.get("user"), dict) else {}
        state = str(review.get("state", "")).strip().lower() or None
        vote_by_state = {
            "approved": 10,
            "changes_requested": -10,
            "commented": 0,
            "dismissed": None,
            "pending": None,
        }
        return PullRequestReviewer(
            reviewer_id=reviewer_id,
            display_name=str(user.get("login") or user.get("name") or reviewer_id or "").strip()
            or None,
            vote=vote_by_state.get(state),
            status=state,
        )


def _expand_pull_request_statuses(
    *,
    status: str | None = None,
    statuses: list[str] | None = None,
) -> list[str | None]:
    raw_values = _normalize_string_list(statuses)
    if not raw_values and status is not None:
        raw_values = _normalize_string_list(status)
    if not raw_values:
        return [None]
    normalized: list[str | None] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = raw.strip().lower()
        expanded = ["completed", "abandoned"] if value == "closed" else [value]
        for item in expanded:
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)
    return normalized or [None]


def _activity_statuses(statuses: list[str] | None = None) -> list[str]:
    raw = _normalize_string_list(statuses) or ["active", "completed", "abandoned"]
    return [item for item in _expand_pull_request_statuses(statuses=raw) if item is not None]


def _activity_max_candidates(value: int) -> int:
    return max(1, min(int(value), 1000))


@dataclass(frozen=True)
class _PullRequestTagExpression:
    kind: Literal["tag", "and", "or", "not"]
    value: str | None = None
    children: tuple["_PullRequestTagExpression", ...] = ()


@dataclass(frozen=True)
class _PullRequestTagToken:
    kind: Literal["tag", "operator", "left_paren", "right_paren"]
    value: str


class _PullRequestTagExpressionParser:
    def __init__(self, expression: str) -> None:
        self._tokens = _tokenize_pull_request_tag_expression(expression)
        self._index = 0

    def parse(self) -> _PullRequestTagExpression:
        if not self._tokens:
            raise ValueError("pull request tag expression must not be empty")
        expression = self._parse_or()
        trailing = self._peek()
        if trailing is not None:
            raise ValueError(f"unexpected token {trailing.value!r} in pull request tag expression")
        return expression

    def _parse_or(self) -> _PullRequestTagExpression:
        expression = self._parse_and()
        while self._match_operator("or"):
            expression = _PullRequestTagExpression(
                kind="or",
                children=(expression, self._parse_and()),
            )
        return expression

    def _parse_and(self) -> _PullRequestTagExpression:
        expression = self._parse_not()
        while self._match_operator("and"):
            expression = _PullRequestTagExpression(
                kind="and",
                children=(expression, self._parse_not()),
            )
        return expression

    def _parse_not(self) -> _PullRequestTagExpression:
        if self._match_operator("not"):
            return _PullRequestTagExpression(kind="not", children=(self._parse_not(),))
        return self._parse_primary()

    def _parse_primary(self) -> _PullRequestTagExpression:
        token = self._peek()
        if token is None:
            raise ValueError("expected tag or '(' in pull request tag expression")
        if token.kind == "tag":
            self._index += 1
            return _PullRequestTagExpression(kind="tag", value=token.value)
        if token.kind == "left_paren":
            self._index += 1
            expression = self._parse_or()
            closing = self._peek()
            if closing is None or closing.kind != "right_paren":
                raise ValueError("missing ')' in pull request tag expression")
            self._index += 1
            return expression
        raise ValueError(f"expected tag or '(' before {token.value!r}")

    def _match_operator(self, operator: str) -> bool:
        token = self._peek()
        if token is None or token.kind != "operator" or token.value != operator:
            return False
        self._index += 1
        return True

    def _peek(self) -> _PullRequestTagToken | None:
        if self._index >= len(self._tokens):
            return None
        return self._tokens[self._index]


def _parse_pull_request_tag_expression(
    expression: str | None,
) -> _PullRequestTagExpression | None:
    if expression is None:
        return None
    if not isinstance(expression, str):
        raise ValueError("pull request tags must be a string")
    text = expression.strip()
    if not text:
        return None
    return _PullRequestTagExpressionParser(text).parse()


def _tokenize_pull_request_tag_expression(expression: str) -> list[_PullRequestTagToken]:
    tokens: list[_PullRequestTagToken] = []
    index = 0
    while index < len(expression):
        char = expression[index]
        if char.isspace():
            index += 1
            continue
        if char == "(":
            tokens.append(_PullRequestTagToken("left_paren", char))
            index += 1
            continue
        if char == ")":
            tokens.append(_PullRequestTagToken("right_paren", char))
            index += 1
            continue
        if char in {"'", '"'}:
            value, index = _read_quoted_pull_request_tag(expression, index)
            tokens.append(_PullRequestTagToken("tag", value))
            continue

        start = index
        while index < len(expression) and not expression[index].isspace():
            if expression[index] in {"(", ")"}:
                break
            index += 1
        raw_value = expression[start:index].strip()
        if not raw_value:
            continue
        normalized = raw_value.casefold()
        if normalized in {"and", "or", "not"}:
            tokens.append(_PullRequestTagToken("operator", normalized))
        else:
            tokens.append(_PullRequestTagToken("tag", raw_value))
    return tokens


def _read_quoted_pull_request_tag(expression: str, start: int) -> tuple[str, int]:
    quote_char = expression[start]
    index = start + 1
    value_chars: list[str] = []
    while index < len(expression):
        char = expression[index]
        if char == "\\" and index + 1 < len(expression):
            value_chars.append(expression[index + 1])
            index += 2
            continue
        if char == quote_char:
            value = "".join(value_chars).strip()
            if not value:
                raise ValueError("pull request tag expression contains an empty quoted tag")
            return value, index + 1
        value_chars.append(char)
        index += 1
    raise ValueError("unterminated quoted tag in pull request tag expression")


def _pull_request_tags_match(
    tags: list[str],
    *,
    tag_filter: _PullRequestTagExpression,
) -> bool:
    present = {tag.casefold() for tag in tags}
    return _evaluate_pull_request_tag_expression(tag_filter, present)


def _evaluate_pull_request_tag_expression(
    expression: _PullRequestTagExpression,
    present_tags: set[str],
) -> bool:
    if expression.kind == "tag":
        return bool(expression.value and expression.value.casefold() in present_tags)
    if expression.kind == "and":
        return all(
            _evaluate_pull_request_tag_expression(child, present_tags)
            for child in expression.children
        )
    if expression.kind == "or":
        return any(
            _evaluate_pull_request_tag_expression(child, present_tags)
            for child in expression.children
        )
    if expression.kind == "not":
        return not _evaluate_pull_request_tag_expression(expression.children[0], present_tags)
    raise ValueError(f"unknown pull request tag expression node: {expression.kind}")


def _azure_pr_web_url(payload: dict[str, Any]) -> str | None:
    return (
        str(payload.get("_links", {}).get("web", {}).get("href") or "").strip()
        or str(payload.get("remoteUrl") or payload.get("url") or "").strip()
        or None
    )


class PrQueryService:
    def __init__(self, *, providers: dict[str, PrQueryProvider]) -> None:
        self.providers = providers

    def list_pull_requests(
        self,
        *,
        provider: str,
        repo: str,
        project: str | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        source_ref_name: str | None = None,
        target_ref_name: str | None = None,
        source_repository_id: str | None = None,
        repository_id: str | None = None,
        min_time: str | None = None,
        max_time: str | None = None,
        query_time_range_type: str | None = None,
        include_links: bool | None = None,
        max_comment_length: int | None = None,
        tags: str | None = None,
        top: int | None = None,
        skip: int | None = None,
        cursor: str | None = None,
    ) -> ListPullRequestsResponse:
        provider_name = normalize_pr_automation_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(
                f"PR query provider {provider_name!r} is not configured for this gateway instance"
            )
        status_values = _expand_pull_request_statuses(status=status, statuses=statuses)
        merged_page = len(status_values) > 1
        merged_skip = max(0, int(skip or 0)) if merged_page else skip
        merged_fetch_top = (merged_skip or 0) + top if merged_page and top is not None else top
        responses = [
            self.providers[provider_name].list_pull_requests(
                repo=repo,
                project=project,
                status=status_value,
                creator_id=creator_id,
                reviewer_id=reviewer_id,
                source_ref_name=source_ref_name,
                target_ref_name=target_ref_name,
                source_repository_id=source_repository_id,
                repository_id=repository_id,
                min_time=min_time,
                max_time=max_time,
                query_time_range_type=query_time_range_type,
                include_links=include_links,
                max_comment_length=max_comment_length,
                tags=tags,
                top=merged_fetch_top,
                skip=0 if merged_page else skip,
                cursor=None if merged_page else cursor,
            )
            for status_value in status_values
        ]
        if len(responses) == 1:
            response = responses[0]
            response.paging = {**response.paging, "statuses": status_values}
            return response
        merged: dict[tuple[str, str, str], PullRequestItem] = {}
        paging_by_status: dict[str, dict[str, Any]] = {}
        for status_value, response in zip(status_values, responses, strict=True):
            paging_by_status[str(status_value)] = response.paging
            for item in response.pull_requests:
                merged[(item.provider, item.repo, item.pr_id)] = item
        items = list(merged.values())
        items.sort(
            key=lambda item: item.updated_at or item.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        total_merged_count = len(items)
        page_start = int(merged_skip or 0)
        page_end = page_start + top if top is not None else None
        limited_items = items[page_start:page_end]
        has_more = (page_end is not None and total_merged_count > page_end) or any(
            bool(response.paging.get("has_more")) for response in responses
        )
        next_skip = (
            page_start + len(limited_items)
            if has_more and top is not None and limited_items
            else None
        )
        return ListPullRequestsResponse(
            provider=responses[0].provider,
            repo=responses[0].repo,
            project=responses[0].project,
            pull_requests=limited_items,
            count=len(limited_items),
            paging={
                "statuses": status_values,
                "by_status": paging_by_status,
                "returned_count": len(limited_items),
                "total_merged_count": total_merged_count,
                "has_more": has_more,
                "next_skip": next_skip,
            },
            next_cursor=None,
        )

    def resolve_ado_identity(
        self,
        *,
        query: str,
        project: str | None = None,
    ) -> ResolveAdoIdentityResponse:
        provider_name = "azure_devops"
        if provider_name not in self.providers:
            raise ValueError(
                "PR query provider 'azure_devops' is not configured for this gateway instance"
            )
        resolve_identity = getattr(self.providers[provider_name], "resolve_identity", None)
        if not callable(resolve_identity):
            raise ValueError(
                "PR query provider 'azure_devops' does not support identity resolution"
            )
        return resolve_identity(query=query, project=project)

    def list_ado_pr_comment_activity(
        self,
        *,
        repo: str,
        author: str | None,
        since: datetime,
        until: datetime | None = None,
        project: str | None = None,
        statuses: list[str] | None = None,
        include_system_comments: bool = False,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> AdoPrCommentActivityResponse:
        provider_name = "azure_devops"
        if provider_name not in self.providers:
            raise ValueError(
                "PR query provider 'azure_devops' is not configured for this gateway instance"
            )
        list_activity = getattr(self.providers[provider_name], "list_pr_comment_activity", None)
        if not callable(list_activity):
            raise ValueError(
                "PR query provider 'azure_devops' does not support PR comment activity"
            )
        return list_activity(
            repo=repo,
            author=author,
            since=since,
            until=until,
            project=project,
            statuses=statuses,
            include_system_comments=include_system_comments,
            top=top,
            skip=skip,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )

    def list_ado_pr_commit_activity(
        self,
        *,
        repo: str,
        author: str | None,
        since: datetime,
        until: datetime | None = None,
        project: str | None = None,
        statuses: list[str] | None = None,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> AdoPrCommitActivityResponse:
        provider_name = "azure_devops"
        if provider_name not in self.providers:
            raise ValueError(
                "PR query provider 'azure_devops' is not configured for this gateway instance"
            )
        list_activity = getattr(self.providers[provider_name], "list_pr_commit_activity", None)
        if not callable(list_activity):
            raise ValueError("PR query provider 'azure_devops' does not support PR commit activity")
        return list_activity(
            repo=repo,
            author=author,
            since=since,
            until=until,
            project=project,
            statuses=statuses,
            top=top,
            skip=skip,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )

    def complete_pull_request(
        self, *, provider: str, repo: str, pr_id: str,
        expected_source_commit: str, expected_target_branch: str,
    ) -> CompletePullRequestResponse:
        provider_name = normalize_pr_automation_provider(provider)
        implementation = self.providers.get(provider_name)
        complete = getattr(implementation, "complete_pull_request", None)
        if not callable(complete):
            raise ValueError(f"PR provider {provider_name!r} does not support completion")
        return complete(
            repo=repo, pr_id=pr_id, expected_source_commit=expected_source_commit,
            expected_target_branch=expected_target_branch,
        )

    def update_pull_request(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        project: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        expected_statuses: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> UpdatePullRequestResponse:
        provider_name = normalize_pr_automation_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(
                f"PR query provider {provider_name!r} is not configured for this gateway instance"
            )
        update_pull_request = getattr(self.providers[provider_name], "update_pull_request", None)
        if not callable(update_pull_request):
            raise ValueError(f"PR update provider {provider_name!r} does not support updates")
        update_kwargs: dict[str, Any] = {
            "repo": repo,
            "pr_id": pr_id,
            "project": project,
            "title": title,
            "description": description,
            "status": status,
            "add_tags": add_tags,
            "remove_tags": remove_tags,
            "expected_statuses": expected_statuses,
        }
        if attachments:
            update_kwargs["attachments"] = attachments
        return update_pull_request(**update_kwargs)

    def get_pull_request(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        include_reviewers: bool | None = None,
        include_commits: bool | None = None,
    ) -> GetPullRequestResponse:
        provider_name = normalize_pr_automation_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(
                f"PR query provider {provider_name!r} is not configured for this gateway instance"
            )
        return self.providers[provider_name].get_pull_request(
            repo=repo,
            pr_id=pr_id,
            include_reviewers=include_reviewers,
            include_commits=include_commits,
        )


class PrReplyService:
    def __init__(
        self,
        *,
        providers: dict[str, PrReplyProvider],
        idempotency_store_path: Path,
        ignored_feedback_store_path: Path | None = None,
        provider_state_namespaces: Mapping[str, str] | None = None,
    ) -> None:
        self.providers = providers
        self.idempotency_store_path = idempotency_store_path
        self.ignored_feedback_store_path = ignored_feedback_store_path
        self.provider_state_namespaces = dict(provider_state_namespaces or {})
        self._lock = Lock()
        self.idempotency_store_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, _exclusive_json_file_lock(self.idempotency_store_path):
            if not self.idempotency_store_path.exists():
                self._write_raw({})

    def post_reply(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
        feedback_kind: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> PostPrFeedbackReplyResponse:
        provider_name = normalize_pr_feedback_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(
                f"PR feedback provider {provider_name!r} is not configured for this gateway instance"
            )
        normalized_key = idempotency_key.strip() if idempotency_key else None
        state_namespace = self.provider_state_namespaces.get(provider_name, "")
        cache_key = (
            json.dumps(
                [state_namespace, provider_name, repo, pr_id, normalized_key],
                separators=(",", ":"),
            )
            if normalized_key
            else None
        )

        normalized_mentions = normalize_ado_mention_refs(mentions)
        reply_kwargs: dict[str, Any] = {
            "repo": repo,
            "pr_id": pr_id,
            "thread_id": thread_id,
            "comment_id": comment_id,
            "body_text": body_text.strip(),
            "attachments": attachments,
        }
        if normalized_mentions:
            reply_kwargs["mentions"] = normalized_mentions
        if feedback_kind is not None and provider_name == "github":
            reply_kwargs["feedback_kind"] = feedback_kind

        if cache_key:
            response = self._post_idempotent_reply(
                cache_key=cache_key,
                provider_name=provider_name,
                reply_kwargs=reply_kwargs,
                idempotency_key=normalized_key,
            )
        else:
            response = self.providers[provider_name].post_reply(**reply_kwargs)
            response.idempotency_key = normalized_key
        self._record_ignored_feedback_id(response)
        return response

    def _post_idempotent_reply(
        self,
        *,
        cache_key: str,
        provider_name: str,
        reply_kwargs: dict[str, Any],
        idempotency_key: str | None,
    ) -> PostPrFeedbackReplyResponse:
        with self._lock, _exclusive_json_file_lock(self.idempotency_store_path):
            payload = self._read_raw()
            existing = payload.get(cache_key)
            if isinstance(existing, dict):
                if existing.get("_state") == "claimed":
                    raise RuntimeError(
                        "PR reply idempotency key has an unresolved prior claim; "
                        "refusing to risk a duplicate external reply"
                    )
                cached = PostPrFeedbackReplyResponse.model_validate(existing)
                return cached.model_copy(update={"status": "deduplicated"})

            payload[cache_key] = {
                "_state": "claimed",
                "claimed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            self._write_raw(payload)
            try:
                response = self.providers[provider_name].post_reply(**reply_kwargs)
            except Exception:
                # A provider exception is a failed attempt and may be retried.
                # Process termination after an ambiguous external success leaves
                # the durable claim in place, preventing an unsafe duplicate.
                latest_payload = self._read_raw()
                latest_payload.pop(cache_key, None)
                self._write_raw(latest_payload)
                raise

            response.idempotency_key = idempotency_key
            latest_payload = self._read_raw()
            latest_payload[cache_key] = response.model_dump(mode="json")
            self._write_raw(latest_payload)
            return response

    def _record_ignored_feedback_id(self, response: PostPrFeedbackReplyResponse) -> None:
        feedback_id = _feedback_id_for_reply_response(response)
        if feedback_id is None or self.ignored_feedback_store_path is None:
            return

        path = self.ignored_feedback_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, _exclusive_json_file_lock(path):
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
            else:
                payload = {}
            normalized_payload, _, _ = _normalize_ignored_feedback_payload(payload)
            state_namespace = self.provider_state_namespaces.get(response.provider, "")
            scope_key = pr_feedback_scope_key(
                state_namespace=state_namespace,
                provider=response.provider,
                repo=response.repo,
                pr_id=response.pr_id,
            )
            entries = normalized_payload.get("feedback_ids", [])
            if any(
                isinstance(entry, dict)
                and entry.get("feedback_id") == feedback_id
                and entry.get("scope_key") == scope_key
                for entry in entries
            ):
                return
            entries.append(
                {
                    "feedback_id": feedback_id,
                    "scope_key": scope_key,
                    "ignored_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            )
            normalized_payload["feedback_ids"] = entries
            final_payload, _, _ = _normalize_ignored_feedback_payload(normalized_payload)
            _write_json_atomic(path, final_payload)

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.idempotency_store_path.read_text(encoding="utf-8"))

    def _write_raw(self, payload: dict[str, Any]) -> None:
        _write_json_atomic(self.idempotency_store_path, payload)


def _feedback_id_for_reply_response(response: PostPrFeedbackReplyResponse) -> str | None:
    if not response.reply_id:
        return None
    if response.provider == "github":
        feedback_kind = response.feedback_kind or "review_comment"
        return f"github:{feedback_kind}:{response.reply_id}"
    if response.provider == "azure_devops" and response.thread_id:
        return f"azure_devops:thread_comment:{response.thread_id}:{response.reply_id}"
    return None


def _normalize_ignored_feedback_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], set[str], bool]:
    now = datetime.now(UTC)
    cutoff = now - _IGNORED_FEEDBACK_RETENTION
    raw_entries = payload.get("feedback_ids", [])
    normalized_entries: list[dict[str, str]] = []
    feedback_ids: set[str] = set()
    feedback_scopes: set[tuple[str, str | None]] = set()

    if isinstance(raw_entries, list):
        for raw_entry in raw_entries:
            if isinstance(raw_entry, str):
                feedback_id = raw_entry.strip()
                if not feedback_id:
                    continue
                if (feedback_id, None) in feedback_scopes:
                    continue
                normalized_entries.append(
                    {
                        "feedback_id": feedback_id,
                        "ignored_at": now.isoformat().replace("+00:00", "Z"),
                    }
                )
                feedback_ids.add(feedback_id)
                feedback_scopes.add((feedback_id, None))
                continue
            if not isinstance(raw_entry, dict):
                continue
            feedback_id = str(raw_entry.get("feedback_id", "")).strip()
            if not feedback_id:
                continue
            ignored_at = str(raw_entry.get("ignored_at", "")).strip()
            scope_key = str(raw_entry.get("scope_key", "")).strip() or None
            if ignored_at:
                try:
                    ignored_at_dt = PrFeedbackService._parse_cursor(ignored_at)
                except ValueError:
                    ignored_at_dt = now
            else:
                ignored_at_dt = now
            if ignored_at_dt < cutoff:
                continue
            if (feedback_id, scope_key) in feedback_scopes:
                continue
            normalized_entry = {
                "feedback_id": feedback_id,
                "ignored_at": ignored_at_dt.isoformat().replace("+00:00", "Z"),
            }
            if scope_key is not None:
                normalized_entry["scope_key"] = scope_key
            normalized_entries.append(normalized_entry)
            feedback_ids.add(feedback_id)
            feedback_scopes.add((feedback_id, scope_key))

    normalized_payload = {"feedback_ids": normalized_entries}
    changed = normalized_payload != payload
    return normalized_payload, feedback_ids, changed


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


@contextmanager
def _exclusive_json_file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def pr_reply_state_namespace(
    *,
    provider: str,
    directory_path: str,
    ado_organization_url: str | None = None,
    project: str | None = None,
) -> str:
    provider_name = normalize_pr_feedback_provider(provider)
    payload: dict[str, str] = {
        "directory_path": directory_path.strip() or "root",
        "provider": provider_name,
    }
    if provider_name == "azure_devops":
        payload["ado_organization_url"] = (ado_organization_url or "").rstrip("/")
        payload["project"] = (project or "").strip()
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def pr_feedback_scope_key(*, state_namespace: str, provider: str, repo: str, pr_id: str) -> str:
    return json.dumps(
        [state_namespace, normalize_pr_feedback_provider(provider), repo, pr_id],
        separators=(",", ":"),
    )


def ignored_feedback_ids_for_scope(payload: dict[str, Any], *, scope_key: str) -> set[str]:
    normalized_payload, _, _ = _normalize_ignored_feedback_payload(payload)
    feedback_ids: set[str] = set()
    for entry in normalized_payload.get("feedback_ids", []):
        if not isinstance(entry, dict):
            continue
        entry_scope = str(entry.get("scope_key", "")).strip() or None
        if entry_scope is None or entry_scope == scope_key:
            feedback_ids.add(str(entry.get("feedback_id", "")).strip())
    feedback_ids.discard("")
    return feedback_ids


def read_ignored_feedback_payload(path: Path) -> dict[str, Any]:
    with _exclusive_json_file_lock(path):
        if not path.exists():
            return {"feedback_ids": []}
        payload = json.loads(path.read_text(encoding="utf-8"))
        normalized_payload, _, changed = _normalize_ignored_feedback_payload(payload)
        if changed:
            _write_json_atomic(path, normalized_payload)
        return normalized_payload


def _normalize_github_feedback_kind(value: str | None) -> str:
    normalized = str(value or "review_comment").strip().lower().replace("-", "_")
    if normalized.startswith("github:"):
        parts = normalized.split(":", 2)
        normalized = parts[1] if len(parts) > 1 else normalized
    if normalized not in {"review_comment", "issue_comment", "review"}:
        raise ValueError(
            "GitHub reply posting feedback_kind must be review_comment, issue_comment, or review"
        )
    return normalized


class PrPublishProvider(Protocol):
    def delete_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        comment_id: str,
        thread_id: str | None = None,
        feedback_kind: str | None = None,
    ) -> dict[str, Any]: ...

    def resolve_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        thread_id: str | None = None,
        comment_id: str | None = None,
    ) -> dict[str, Any]: ...

    def post_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...

    def submit_review(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        comments: list[dict[str, Any]] | None = None,
        event: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


class GitHubPrPublishProvider:
    def __init__(
        self,
        *,
        token: str,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def delete_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        comment_id: str,
        thread_id: str | None = None,
        feedback_kind: str | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return GitHubPrComments(
                client, api_base_url=self.api_base_url, headers=self._headers()
            ).delete_comment(
                repo=repo, pr_id=pr_id, comment_id=comment_id, feedback_kind=feedback_kind
            )

    def resolve_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        thread_id: str | None = None,
        comment_id: str | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return GitHubPrComments(
                client, api_base_url=self.api_base_url, headers=self._headers()
            ).resolve_comment(repo=repo, pr_id=pr_id, thread_id=thread_id, comment_id=comment_id)

    def post_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _reject_unsupported_mentions("GitHub", mentions)
        _reject_unsupported_attachments("GitHub", attachments)
        normalized_body = body_text.strip()
        if not normalized_body:
            raise ValueError("GitHub PR comment publishing requires body_text")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.api_base_url}/repos/{repo}/issues/{pr_id}/comments",
                headers=self._headers(),
                json={"body": normalized_body},
            )
            response.raise_for_status()
            payload = response.json()
        comment_id = str(payload.get("id", "")).strip() or None
        return {
            "status": "posted",
            "provider": "github",
            "repo": repo,
            "pr_id": pr_id,
            "comment_id": comment_id,
            "comment_url": payload.get("html_url") or payload.get("url"),
            "published_feedback_ids": [f"github:issue_comment:{comment_id}"] if comment_id else [],
        }

    def submit_review(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        comments: list[dict[str, Any]] | None = None,
        event: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _reject_unsupported_mentions("GitHub", mentions)
        _reject_unsupported_attachments("GitHub", attachments)
        normalized_body = body_text.strip()
        normalized_comments = _normalize_pr_review_comments(comments)
        if any(comment.get("mentions") for comment in normalized_comments):
            raise ValueError("GitHub PR review comments do not support ADO mentions")
        _reject_unsupported_comment_attachments("GitHub", normalized_comments)
        normalized_event = _normalize_pr_review_event(event)
        if not normalized_body and not normalized_comments:
            raise ValueError("GitHub PR review publishing requires body_text or comments")

        review_payload: dict[str, Any] = {"event": normalized_event}
        if normalized_body:
            review_payload["body"] = normalized_body

        with httpx.Client(timeout=self.timeout_seconds) as client:
            review_response = client.post(
                f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/reviews",
                headers=self._headers(),
                json=review_payload,
            )
            review_response.raise_for_status()
            review_payload_json = review_response.json()

            comment_ids: list[str] = []
            comment_urls: list[str] = []
            for comment in normalized_comments:
                review_comment_payload: dict[str, Any] = {
                    "body": comment["body_text"],
                    "path": comment["path"],
                }
                for field_name in ("line", "start_line", "side", "start_side", "commit_id"):
                    value = comment.get(field_name)
                    if value is not None:
                        review_comment_payload[field_name] = value
                review_comment_response = client.post(
                    f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/comments",
                    headers=self._headers(),
                    json=review_comment_payload,
                )
                review_comment_response.raise_for_status()
                review_comment_json = review_comment_response.json()
                review_comment_id = str(review_comment_json.get("id", "")).strip() or None
                if review_comment_id is not None:
                    comment_ids.append(review_comment_id)
                comment_url = review_comment_json.get("html_url") or review_comment_json.get("url")
                if comment_url:
                    comment_urls.append(str(comment_url))

        review_id = str(review_payload_json.get("id", "")).strip() or None
        review_url = review_payload_json.get("html_url") or review_payload_json.get("url")
        return {
            "status": "posted",
            "provider": "github",
            "repo": repo,
            "pr_id": pr_id,
            "review_id": review_id,
            "review_url": review_url,
            "event": normalized_event,
            "comment_ids": comment_ids,
            "comment_urls": comment_urls,
            "published_feedback_ids": [
                *([f"github:review:{review_id}"] if review_id else []),
                *[f"github:review_comment:{comment_id}" for comment_id in comment_ids],
            ],
        }





_AzureDevOpsPrChange = optional_azure_devops_class("evander_azure_devops_addon.pull_requests", "_AzureDevOpsPrChange")



_AzureDevOpsPrIterationContext = optional_azure_devops_class("evander_azure_devops_addon.pull_requests", "_AzureDevOpsPrIterationContext")



def _azure_devops_thread_context_for_review_comment(*args, **kwargs):
    return require_azure_devops_module("evander_azure_devops_addon.pull_requests")._azure_devops_thread_context_for_review_comment(*args, **kwargs)



def _azure_devops_pr_iteration_context(*args, **kwargs):
    return require_azure_devops_module("evander_azure_devops_addon.pull_requests")._azure_devops_pr_iteration_context(*args, **kwargs)



def _azure_devops_pr_change_from_payload(*args, **kwargs):
    return require_azure_devops_module("evander_azure_devops_addon.pull_requests")._azure_devops_pr_change_from_payload(*args, **kwargs)



def _azure_devops_pull_request_thread_context_for_review_comment(*args, **kwargs):
    return require_azure_devops_module("evander_azure_devops_addon.pull_requests")._azure_devops_pull_request_thread_context_for_review_comment(*args, **kwargs)


def _normalize_azure_devops_pr_path(path: Any) -> str | None:
    normalized = _normalize_optional_text(path)
    if normalized is None:
        return None
    return "/" + normalized.lstrip("/")



def _azure_devops_comment_file_path(*args, **kwargs):
    return require_azure_devops_module("evander_azure_devops_addon.pull_requests")._azure_devops_comment_file_path(*args, **kwargs)



def _azure_devops_inline_comment_error_context(*args, **kwargs):
    return require_azure_devops_module("evander_azure_devops_addon.pull_requests")._azure_devops_inline_comment_error_context(*args, **kwargs)


def _raise_azure_devops_pr_publish_error(
    response: httpx.Response,
    *,
    action: str,
    context: str | None = None,
) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        details = _azure_devops_error_details(response)
        context_suffix = f" ({context})" if context else ""
        detail_suffix = f": {details}" if details else ""
        message = (
            f"Azure DevOps {action} failed{context_suffix} "
            f"(status {response.status_code}) for url {response.url}{detail_suffix}"
        )
        _LOGGER.warning(
            "Azure DevOps PR publish failed: action=%s status=%s url=%s context=%s error=%s",
            action,
            response.status_code,
            response.url,
            context,
            details or str(exc),
        )
        raise httpx.HTTPStatusError(
            message,
            request=exc.request,
            response=exc.response,
        ) from exc



def _azure_devops_error_details(*args, **kwargs):
    return require_azure_devops_module("evander_azure_devops_addon.pull_requests")._azure_devops_error_details(*args, **kwargs)


class PrPublishService:
    def __init__(
        self,
        *,
        providers: dict[str, PrPublishProvider],
        idempotency_store_path: Path,
        ignored_feedback_store_path: Path | None = None,
    ) -> None:
        self.providers = providers
        self.idempotency_store_path = idempotency_store_path
        self.ignored_feedback_store_path = ignored_feedback_store_path
        self._lock = Lock()
        self.idempotency_store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.idempotency_store_path.exists():
            self._write_raw({})

    def _comment_provider(self, provider: str) -> PrPublishProvider:
        provider_name = normalize_pr_feedback_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(
                f"PR publish provider {provider_name!r} is not configured for this gateway instance"
            )
        return self.providers[provider_name]

    def delete_comment(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        comment_id: str,
        thread_id: str | None = None,
        feedback_kind: str | None = None,
    ) -> dict[str, Any]:
        return self._comment_provider(provider).delete_comment(
            repo=repo,
            pr_id=pr_id,
            comment_id=comment_id,
            thread_id=thread_id,
            feedback_kind=feedback_kind,
        )

    def resolve_comment(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        thread_id: str | None = None,
        comment_id: str | None = None,
    ) -> dict[str, Any]:
        return self._comment_provider(provider).resolve_comment(
            repo=repo, pr_id=pr_id, thread_id=thread_id, comment_id=comment_id
        )

    def post_comment(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._publish(
            operation="post_comment",
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            body_text=body_text,
            mentions=mentions,
            attachments=attachments,
            comments=None,
            event=None,
            idempotency_key=idempotency_key,
        )

    def submit_review(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        comments: list[dict[str, Any]] | None = None,
        event: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._publish(
            operation="submit_review",
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            body_text=body_text,
            mentions=mentions,
            attachments=attachments,
            comments=comments,
            event=event,
            idempotency_key=idempotency_key,
        )

    def post_inline_comment(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        path: str,
        line: int,
        body_text: str,
        start_line: int | None = None,
        side: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        comment: dict[str, Any] = {
            "path": path,
            "line": line,
            "body_text": body_text,
        }
        if start_line is not None:
            comment["start_line"] = start_line
        if side is not None:
            comment["side"] = side
        normalized_mentions = normalize_ado_mention_refs(mentions)
        if normalized_mentions:
            comment["mentions"] = normalized_mentions
        if attachments:
            comment["attachments"] = attachments
        return self._publish(
            operation="post_inline_comment",
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            body_text="",
            mentions=None,
            attachments=None,
            comments=[comment],
            event=None,
            idempotency_key=idempotency_key,
        )

    def _publish(
        self,
        *,
        operation: str,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        mentions: list[str] | None,
        attachments: list[dict[str, Any]] | None,
        comments: list[dict[str, Any]] | None,
        event: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        provider_name = normalize_pr_feedback_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(
                f"PR publish provider {provider_name!r} is not configured for this gateway instance"
            )
        normalized_key = idempotency_key.strip() if idempotency_key else None
        cache_key = (
            f"{operation}:{provider_name}:{repo}:{pr_id}:{normalized_key}"
            if normalized_key
            else None
        )
        if cache_key:
            with self._lock:
                existing = self._read_raw().get(cache_key)
            if isinstance(existing, dict):
                cached = dict(existing)
                cached["status"] = "deduplicated"
                return cached

        provider = self.providers[provider_name]
        normalized_mentions = normalize_ado_mention_refs(mentions)
        if operation == "post_comment":
            comment_kwargs: dict[str, Any] = {
                "repo": repo,
                "pr_id": pr_id,
                "body_text": body_text.strip(),
                "attachments": attachments,
            }
            if normalized_mentions:
                comment_kwargs["mentions"] = normalized_mentions
            response = provider.post_comment(**comment_kwargs)
        elif operation in {"submit_review", "post_inline_comment"}:
            review_kwargs: dict[str, Any] = {
                "repo": repo,
                "pr_id": pr_id,
                "body_text": body_text.strip(),
                "comments": comments,
                "event": event,
                "attachments": attachments,
            }
            if normalized_mentions:
                review_kwargs["mentions"] = normalized_mentions
            response = provider.submit_review(**review_kwargs)
        else:
            raise ValueError(f"Unsupported publish operation: {operation!r}")
        response = dict(response)
        response["idempotency_key"] = normalized_key
        self._record_ignored_feedback_ids(response)
        if cache_key:
            with self._lock:
                payload = self._read_raw()
                payload[cache_key] = response
                self._write_raw(payload)
        return response

    def _record_ignored_feedback_ids(self, response: dict[str, Any]) -> None:
        feedback_ids = _publication_feedback_ids(response)
        if not feedback_ids or self.ignored_feedback_store_path is None:
            return

        path = self.ignored_feedback_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, _exclusive_json_file_lock(path):
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
            else:
                payload = {}
            normalized_payload, _, _ = _normalize_ignored_feedback_payload(payload)
            entries = normalized_payload.get("feedback_ids", [])
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            for feedback_id in feedback_ids:
                if any(
                    isinstance(entry, dict)
                    and entry.get("feedback_id") == feedback_id
                    and not entry.get("scope_key")
                    for entry in entries
                ):
                    continue
                entries.append({"feedback_id": feedback_id, "ignored_at": now})
            normalized_payload["feedback_ids"] = entries
            final_payload, _, _ = _normalize_ignored_feedback_payload(normalized_payload)
            _write_json_atomic(path, final_payload)

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.idempotency_store_path.read_text(encoding="utf-8"))

    def _write_raw(self, payload: dict[str, Any]) -> None:
        _write_json_atomic(self.idempotency_store_path, payload)


def _normalize_pr_review_comments(comments: Any) -> list[dict[str, Any]]:
    if comments is None:
        return []
    if isinstance(comments, dict):
        comments = [comments]
    if not isinstance(comments, list):
        raise ValueError("PR review comments must be a list of objects")

    normalized: list[dict[str, Any]] = []
    for index, raw_comment in enumerate(comments):
        if not isinstance(raw_comment, dict):
            raise ValueError(f"PR review comment at index {index} must be an object")
        body_text = _normalize_optional_text(
            raw_comment.get("body_text") or raw_comment.get("body")
        )
        path = _normalize_optional_text(raw_comment.get("path"))
        if not body_text:
            raise ValueError(f"PR review comment at index {index} requires body_text")
        if not path:
            raise ValueError(f"PR review comment at index {index} requires path")
        normalized_comment: dict[str, Any] = {"body_text": body_text, "path": path}
        line = _as_int_or_none(raw_comment.get("line"))
        if line is not None:
            normalized_comment["line"] = line
        start_line = _as_int_or_none(raw_comment.get("start_line"))
        if start_line is not None:
            normalized_comment["start_line"] = start_line
        side = _normalize_optional_text(raw_comment.get("side"))
        if side is not None:
            normalized_side = side.upper()
            if normalized_side not in {"LEFT", "RIGHT"}:
                raise ValueError(f"PR review comment at index {index} side must be LEFT or RIGHT")
            normalized_comment["side"] = normalized_side
        start_side = _normalize_optional_text(raw_comment.get("start_side"))
        if start_side is not None:
            normalized_start_side = start_side.upper()
            if normalized_start_side not in {"LEFT", "RIGHT"}:
                raise ValueError(
                    f"PR review comment at index {index} start_side must be LEFT or RIGHT"
                )
            normalized_comment["start_side"] = normalized_start_side
        commit_id = _normalize_optional_text(raw_comment.get("commit_id"))
        if commit_id is not None:
            normalized_comment["commit_id"] = commit_id
        mentions = normalize_ado_mention_refs(raw_comment.get("mentions"))
        if mentions:
            normalized_comment["mentions"] = mentions
        raw_attachments = raw_comment.get("attachments")
        if raw_attachments is None:
            raw_attachments = raw_comment.get("artifacts")
        if raw_attachments is not None:
            if not isinstance(raw_attachments, list):
                raise ValueError(f"PR review comment at index {index} attachments must be a list")
            normalized_comment["attachments"] = [
                item for item in raw_attachments if isinstance(item, dict)
            ]
        normalized.append(normalized_comment)
    return normalized


def _normalize_pr_review_event(event: Any) -> str:
    normalized = _normalize_optional_text(event)
    if normalized is None:
        return "COMMENT"
    upper = normalized.upper()
    if upper not in {"COMMENT", "APPROVE", "REQUEST_CHANGES"}:
        raise ValueError("PR review event must be one of: COMMENT, APPROVE, REQUEST_CHANGES")
    return upper


def _publication_feedback_ids(response: dict[str, Any]) -> list[str]:
    feedback_ids = response.get("published_feedback_ids")
    if not isinstance(feedback_ids, list):
        return []
    normalized: list[str] = []
    for feedback_id in feedback_ids:
        if feedback_id is None:
            continue
        text = str(feedback_id).strip()
        if text:
            normalized.append(text)
    return normalized


def _as_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)



def _upload_azure_devops_pr_attachments(*args, **kwargs):
    return require_azure_devops_module('evander_azure_devops_addon.pull_requests')._upload_azure_devops_pr_attachments(*args, **kwargs)

AzureDevOpsPrReplyProvider = optional_azure_devops_class('evander_azure_devops_addon.pull_requests', 'AzureDevOpsPrReplyProvider')

AzureDevOpsPrQueryProvider = optional_azure_devops_class('evander_azure_devops_addon.pull_requests', 'AzureDevOpsPrQueryProvider')

AzureDevOpsPrPublishProvider = optional_azure_devops_class('evander_azure_devops_addon.pull_requests', 'AzureDevOpsPrPublishProvider')
