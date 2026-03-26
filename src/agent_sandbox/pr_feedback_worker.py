from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from urllib.parse import unquote

import httpx
from pydantic import BaseModel

from .audit import AuditEventType, AuditLogger


logger = logging.getLogger(__name__)

_FEEDBACK_RETENTION = timedelta(days=90)


class FeedbackEnvelope(BaseModel):
    feedback_id: str
    provider: str
    repo: str
    pr_id: str
    thread_id: str | None = None
    comment_id: str | None = None
    author: str
    body: str
    file_path: str | None = None
    line: int | None = None
    created_at: datetime
    in_reply_to: str | None = None
    resolved: bool | None = None


@dataclass(frozen=True)
class ProviderPollResult:
    feedback: list[FeedbackEnvelope]
    next_cursor: str | None


class PrFeedbackStore:
    """Atomic JSON-backed store for feedback, provider cursors, and session read cursors."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"cursors": {}, "feedback": {}, "session_reads": {}})

    def get_cursor(self, stream_key: str) -> str | None:
        with self._lock:
            payload = self._read_raw()
            cursor = payload.get("cursors", {}).get(stream_key)
            return str(cursor) if cursor is not None else None

    def set_cursor(self, stream_key: str, cursor: str | None) -> None:
        with self._lock:
            payload = self._read_raw()
            cursors = payload.setdefault("cursors", {})
            if cursor is None:
                cursors.pop(stream_key, None)
            else:
                cursors[stream_key] = cursor
            self._compact_feedback(payload)
            self._atomic_write(payload)

    def add_feedback_if_new(self, envelope: FeedbackEnvelope) -> bool:
        with self._lock:
            payload = self._read_raw()
            stored_feedback = payload.setdefault("feedback", {})
            if envelope.feedback_id in stored_feedback:
                return False
            stored_feedback[envelope.feedback_id] = envelope.model_dump(mode="json")
            self._compact_feedback(payload)
            self._atomic_write(payload)
            return True

    def get_session_cursor(self, session_id: str, stream_key: str) -> str | None:
        with self._lock:
            payload = self._read_raw()
            raw_session_reads = payload.get("session_reads", {})
            if not isinstance(raw_session_reads, dict):
                return None
            session_reads = raw_session_reads.get(session_id, {})
            if not isinstance(session_reads, dict):
                return None
            cursor = session_reads.get(stream_key)
            return str(cursor) if cursor is not None else None

    def advance_session_cursor(self, session_id: str, stream_key: str, cursor: str | None) -> str | None:
        with self._lock:
            payload = self._read_raw()
            session_reads = payload.setdefault("session_reads", {})
            if not isinstance(session_reads, dict):
                session_reads = {}
                payload["session_reads"] = session_reads
            current_reads = session_reads.get(session_id)
            if not isinstance(current_reads, dict):
                current_reads = {}
                session_reads[session_id] = current_reads

            current_cursor = current_reads.get(stream_key)
            next_cursor = _latest_cursor(
                [
                    str(current_cursor) if current_cursor is not None else None,
                    cursor,
                ]
            )
            if next_cursor is None:
                current_reads.pop(stream_key, None)
            else:
                current_reads[stream_key] = next_cursor
            if not current_reads:
                session_reads.pop(session_id, None)
            self._compact_feedback(payload)
            self._atomic_write(payload)
            return next_cursor

    def has_unread_feedback(self, *, session_id: str, streams: list[dict[str, str]]) -> bool:
        with self._lock:
            payload = self._read_raw()
            feedback_items = payload.get("feedback", {})
            if not isinstance(feedback_items, dict):
                return False
            for stream in streams:
                stream_key = _stream_key(stream["provider"], stream["repo"], stream["pr_id"])
                session_cursor = self._get_session_cursor_from_payload(payload, session_id, stream_key)
                if self._stream_has_unread_feedback(
                    feedback_items=feedback_items,
                    provider=stream["provider"],
                    repo=stream["repo"],
                    pr_id=stream["pr_id"],
                    cursor=session_cursor,
                ):
                    return True
            return False

    @staticmethod
    def _get_session_cursor_from_payload(payload: dict[str, Any], session_id: str, stream_key: str) -> str | None:
        raw_session_reads = payload.get("session_reads", {})
        if not isinstance(raw_session_reads, dict):
            return None
        session_reads = raw_session_reads.get(session_id, {})
        if not isinstance(session_reads, dict):
            return None
        cursor = session_reads.get(stream_key)
        return str(cursor) if cursor is not None else None

    @staticmethod
    def _stream_has_unread_feedback(
        *,
        feedback_items: dict[str, Any],
        provider: str,
        repo: str,
        pr_id: str,
        cursor: str | None,
    ) -> bool:
        cursor_dt = _parse_dt(cursor) if cursor else None
        for raw_value in feedback_items.values():
            if not isinstance(raw_value, dict):
                continue
            if (
                str(raw_value.get("provider")) != provider
                or str(raw_value.get("repo")) != repo
                or str(raw_value.get("pr_id")) != pr_id
            ):
                continue
            created_at = _parse_dt(raw_value.get("created_at"))
            if cursor_dt and created_at <= cursor_dt:
                continue
            return True
        return False

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _compact_feedback(self, payload: dict[str, Any]) -> None:
        feedback = payload.get("feedback")
        cursors = payload.get("cursors")
        if not isinstance(feedback, dict) or not isinstance(cursors, dict):
            return

        cutoff = datetime.now(timezone.utc) - _FEEDBACK_RETENTION
        compacted: dict[str, Any] = {}
        changed = False
        for feedback_id, raw_value in feedback.items():
            if not isinstance(raw_value, dict):
                compacted[feedback_id] = raw_value
                continue
            provider = str(raw_value.get("provider", "")).strip()
            repo = str(raw_value.get("repo", "")).strip()
            pr_id = str(raw_value.get("pr_id", "")).strip()
            if not provider or not repo or not pr_id:
                compacted[feedback_id] = raw_value
                continue
            created_at = _parse_dt(raw_value.get("created_at"))
            stream_cursor = cursors.get(_stream_key(provider, repo, pr_id))
            if created_at < cutoff and stream_cursor is not None and created_at <= _parse_dt(stream_cursor):
                changed = True
                continue
            compacted[feedback_id] = raw_value
        if changed:
            payload["feedback"] = compacted

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)


def _stream_key(provider: str, repo: str, pr_id: str) -> str:
    return f"{provider}:{repo}:{pr_id}"


class FeedbackProviderAdapter(Protocol):
    provider_name: str

    def poll(
        self,
        *,
        client: httpx.Client,
        repo: str,
        pr_id: str,
        cursor: str | None,
    ) -> ProviderPollResult: ...


class GitHubFeedbackAdapter:
    provider_name = "github"

    def __init__(self, api_base_url: str = "https://api.github.com", token: str | None = None) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.token = token.strip() if token else None

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def poll(
        self,
        *,
        client: httpx.Client,
        repo: str,
        pr_id: str,
        cursor: str | None,
    ) -> ProviderPollResult:
        issue_comments = self._get_issue_comments(client, repo, pr_id, cursor)
        review_comments = self._get_review_comments(client, repo, pr_id, cursor)
        reviews = self._get_reviews(client, repo, pr_id)

        feedback: list[FeedbackEnvelope] = []
        feedback.extend(self._map_issue_comments(repo=repo, pr_id=pr_id, comments=issue_comments))
        feedback.extend(self._map_review_comments(repo=repo, pr_id=pr_id, comments=review_comments))
        feedback.extend(self._map_reviews(repo=repo, pr_id=pr_id, reviews=reviews, cursor=cursor))

        ordered = sorted(feedback, key=lambda item: item.created_at)
        next_cursor = _latest_cursor([cursor, *[item.created_at.isoformat() for item in ordered]])
        return ProviderPollResult(feedback=ordered, next_cursor=next_cursor)

    def _get_issue_comments(
        self, client: httpx.Client, repo: str, pr_id: str, cursor: str | None
    ) -> list[dict[str, Any]]:
        url = f"{self.api_base_url}/repos/{repo}/issues/{pr_id}/comments"
        params: dict[str, str] = {"sort": "updated", "direction": "asc", "per_page": "100"}
        if cursor:
            params["since"] = cursor
        response = client.get(url, params=params, headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def _get_review_comments(
        self, client: httpx.Client, repo: str, pr_id: str, cursor: str | None
    ) -> list[dict[str, Any]]:
        url = f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/comments"
        params: dict[str, str] = {"sort": "updated", "direction": "asc", "per_page": "100"}
        if cursor:
            params["since"] = cursor
        response = client.get(url, params=params, headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def _get_reviews(self, client: httpx.Client, repo: str, pr_id: str) -> list[dict[str, Any]]:
        url = f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/reviews"
        response = client.get(url, params={"per_page": "100"}, headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _map_issue_comments(
        *, repo: str, pr_id: str, comments: list[dict[str, Any]]
    ) -> list[FeedbackEnvelope]:
        mapped: list[FeedbackEnvelope] = []
        for comment in comments:
            comment_id = str(comment.get("id"))
            created_at = _parse_dt(comment.get("created_at") or comment.get("updated_at"))
            mapped.append(
                FeedbackEnvelope(
                    feedback_id=f"github:issue_comment:{comment_id}",
                    provider="github",
                    repo=repo,
                    pr_id=str(pr_id),
                    thread_id=f"issue_comment:{comment_id}",
                    comment_id=comment_id,
                    author=_github_author(comment.get("user")),
                    body=(comment.get("body") or "").strip(),
                    created_at=created_at,
                )
            )
        return mapped

    @staticmethod
    def _map_review_comments(
        *, repo: str, pr_id: str, comments: list[dict[str, Any]]
    ) -> list[FeedbackEnvelope]:
        mapped: list[FeedbackEnvelope] = []
        for comment in comments:
            comment_id = str(comment.get("id"))
            review_id = comment.get("pull_request_review_id")
            line = comment.get("line") if isinstance(comment.get("line"), int) else comment.get("original_line")
            mapped.append(
                FeedbackEnvelope(
                    feedback_id=f"github:review_comment:{comment_id}",
                    provider="github",
                    repo=repo,
                    pr_id=str(pr_id),
                    thread_id=str(review_id) if review_id is not None else None,
                    comment_id=comment_id,
                    author=_github_author(comment.get("user")),
                    body=(comment.get("body") or "").strip(),
                    file_path=comment.get("path"),
                    line=line if isinstance(line, int) else None,
                    created_at=_parse_dt(comment.get("created_at") or comment.get("updated_at")),
                    in_reply_to=(
                        str(comment.get("in_reply_to_id"))
                        if comment.get("in_reply_to_id") is not None
                        else None
                    ),
                )
            )
        return mapped

    @staticmethod
    def _map_reviews(
        *, repo: str, pr_id: str, reviews: list[dict[str, Any]], cursor: str | None
    ) -> list[FeedbackEnvelope]:
        mapped: list[FeedbackEnvelope] = []
        cursor_dt = _parse_dt(cursor) if cursor else None
        for review in reviews:
            submitted_or_created = _parse_dt(review.get("submitted_at") or review.get("created_at"))
            if cursor_dt and submitted_or_created <= cursor_dt:
                continue
            review_id = str(review.get("id"))
            mapped.append(
                FeedbackEnvelope(
                    feedback_id=f"github:review:{review_id}",
                    provider="github",
                    repo=repo,
                    pr_id=str(pr_id),
                    thread_id=review_id,
                    comment_id=review_id,
                    author=_github_author(review.get("user")),
                    body=(review.get("body") or "").strip(),
                    created_at=submitted_or_created,
                    resolved=None,
                )
            )
        return mapped


class AzureDevOpsFeedbackAdapter:
    provider_name = "azure_devops"

    def __init__(self, organization_url: str, project: str, pat: str | None = None) -> None:
        self.organization_url = organization_url.rstrip("/")
        self.project = project
        self.pat = pat.strip() if pat else None

    def _headers(self) -> dict[str, str]:
        if not self.pat:
            return {}
        token = base64.b64encode(f":{self.pat}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def poll(
        self,
        *,
        client: httpx.Client,
        repo: str,
        pr_id: str,
        cursor: str | None,
    ) -> ProviderPollResult:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        url = (
            f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
            f"pullRequests/{pr_id}/threads"
        )
        response = client.get(url, params={"api-version": "7.1"}, headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        threads = payload.get("value", []) if isinstance(payload, dict) else []
        cursor_dt = _parse_dt(cursor) if cursor else None

        feedback: list[FeedbackEnvelope] = []
        seen_timestamps: list[str] = []
        for thread in threads:
            thread_id = str(thread.get("id"))
            resolved = _ado_thread_resolved(thread.get("status"))
            thread_context = thread.get("threadContext") if isinstance(thread.get("threadContext"), dict) else {}
            file_path = thread_context.get("filePath")
            line = _ado_line_from_context(thread_context)
            for comment in thread.get("comments", []):
                if comment.get("isDeleted"):
                    continue
                created_at = _parse_dt(comment.get("publishedDate") or comment.get("lastUpdatedDate"))
                if cursor_dt and created_at <= cursor_dt:
                    continue
                seen_timestamps.append(created_at.isoformat())
                comment_id = str(comment.get("id"))
                parent_comment_id = comment.get("parentCommentId")
                in_reply_to = (
                    str(parent_comment_id)
                    if isinstance(parent_comment_id, int) and parent_comment_id > 0
                    else None
                )
                author = (comment.get("author", {}).get("displayName") or "unknown").strip()
                body = (comment.get("content") or "").strip()
                if _ado_should_skip_comment(
                    author=author,
                    body=body,
                    comment_type=comment.get("commentType"),
                    file_path=file_path,
                ):
                    continue
                feedback.append(
                    FeedbackEnvelope(
                        feedback_id=f"azure_devops:thread_comment:{thread_id}:{comment_id}",
                        provider="azure_devops",
                        repo=repo,
                        pr_id=str(pr_id),
                        thread_id=thread_id,
                        comment_id=comment_id,
                        author=author,
                        body=body,
                        file_path=file_path,
                        line=line,
                        created_at=created_at,
                        in_reply_to=in_reply_to,
                        resolved=resolved,
                    )
                )

        ordered = sorted(feedback, key=lambda item: item.created_at)
        next_cursor = _latest_cursor([cursor, *seen_timestamps])
        return ProviderPollResult(feedback=ordered, next_cursor=next_cursor)


_ADO_SYSTEM_STATE_CHANGE_PATTERNS = (
    re.compile(r"^.+ voted -?\d+$"),
    re.compile(r"^.+ approved the changes$"),
    re.compile(r"^.+ approved with suggestions$"),
    re.compile(r"^.+ waiting for author$"),
    re.compile(r"^.+ declined to review$"),
    re.compile(r"^.+ reset all code reviewer votes$"),
    re.compile(r"^.+ rejected this pull request$"),
    re.compile(r"^.+ updated the pull request status to .+$"),
    re.compile(r"^the reference refs/heads/.+ was updated\.$"),
    re.compile(r"^.+ joined as a reviewer$"),
    re.compile(r"^.+ added .+ as a reviewer$"),
    re.compile(r"^.+ is changed to be a required reviewer$"),
    re.compile(r"^.+ is no longer a required reviewer$"),
    re.compile(r"^.+ removed .+ as a reviewer$"),
)


def _ado_should_skip_comment(
    *,
    author: str,
    body: str,
    comment_type: object,
    file_path: str | None,
) -> bool:
    comment_type_value = str(comment_type or "").strip().lower()
    if comment_type_value == "system":
        return True
    if file_path:
        return False
    normalized_body = body.strip().casefold()
    if not normalized_body:
        return False
    return any(pattern.fullmatch(normalized_body) for pattern in _ADO_SYSTEM_STATE_CHANGE_PATTERNS)


class PullRequestFeedbackWorker:
    def __init__(
        self,
        *,
        store: PrFeedbackStore,
        adapters: dict[str, FeedbackProviderAdapter],
        audit_logger: AuditLogger,
    ) -> None:
        self.store = store
        self.adapters = adapters
        self.audit_logger = audit_logger

    def poll_once(
        self,
        *,
        client: httpx.Client,
        provider: str,
        repo: str,
        pr_id: str,
        stream_bindings: list[dict[str, str | None]] | None = None,
    ) -> list[FeedbackEnvelope]:
        adapter = self.adapters[provider]
        stream_key = f"{provider}:{repo}:{pr_id}"
        cursor = self.store.get_cursor(stream_key)
        result = adapter.poll(client=client, repo=repo, pr_id=pr_id, cursor=cursor)

        discovered: list[FeedbackEnvelope] = []
        for envelope in result.feedback:
            if not self.store.add_feedback_if_new(envelope):
                continue
            discovered.append(envelope)
            self._log_discovery(envelope, stream_bindings=stream_bindings)

        self.store.set_cursor(stream_key, result.next_cursor)
        return discovered

    def _log_discovery(
        self,
        envelope: FeedbackEnvelope,
        *,
        stream_bindings: list[dict[str, str | None]] | None = None,
    ) -> None:
        bindings = stream_bindings or []
        logger.info(
            "Discovered new PR feedback provider=%s repo=%s pr=%s feedback_id=%s stream_bindings=%s",
            envelope.provider,
            envelope.repo,
            envelope.pr_id,
            envelope.feedback_id,
            bindings,
        )
        self.audit_logger.log_decision(
            event_type=AuditEventType.FEEDBACK_DETECTED,
            session_id=bindings[0]["session_id"] if bindings else None,
            vm_id=bindings[0]["vm_id"] if bindings else None,
            provider=envelope.provider,
            repo=envelope.repo,
            pr_id=envelope.pr_id,
            decision="detected",
            reason="new-feedback",
            args={
                "feedback": envelope.model_dump(mode="json"),
                "stream_bindings": bindings,
            },
        )


def _github_author(user: Any) -> str:
    if not isinstance(user, dict):
        return "unknown"
    return (user.get("login") or user.get("name") or "unknown").strip()


def _ado_thread_resolved(status: Any) -> bool | None:
    if not isinstance(status, str):
        return None
    normalized = status.strip().lower()
    if normalized in {"closed", "fixed", "wontfix", "bydesign"}:
        return True
    if normalized in {"active", "pending"}:
        return False
    return None


def _ado_line_from_context(thread_context: dict[str, Any]) -> int | None:
    right = thread_context.get("rightFileStart")
    if isinstance(right, dict) and isinstance(right.get("line"), int):
        return right["line"]
    left = thread_context.get("leftFileStart")
    if isinstance(left, dict) and isinstance(left.get("line"), int):
        return left["line"]
    return None


def _normalize_azure_devops_repo_identifier(repo: str) -> str:
    normalized = unquote(repo.strip())
    if not normalized:
        return normalized
    if "/_git/" in normalized:
        return normalized.split("/_git/", 1)[1].split("/", 1)[0]
    if "/_apis/git/repositories/" in normalized:
        return normalized.split("/_apis/git/repositories/", 1)[1].split("/", 1)[0]
    if "ssh.dev.azure.com:v3/" in normalized:
        return normalized.split("ssh.dev.azure.com:v3/", 1)[1].rsplit("/", 1)[-1]
    if normalized.count("/") >= 2:
        return normalized.rsplit("/", 1)[-1]
    return normalized


def _latest_cursor(candidates: list[str | None]) -> str | None:
    datetimes = [_parse_dt(value) for value in candidates if value]
    if not datetimes:
        return None
    latest = max(datetimes)
    return latest.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return datetime.min.replace(tzinfo=timezone.utc)
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_pr_feedback_worker(
    runtime_dir: Path, *, adapters: dict[str, FeedbackProviderAdapter]
) -> PullRequestFeedbackWorker:
    store = PrFeedbackStore(runtime_dir / "pr-feedback-store.json")
    audit = AuditLogger(runtime_dir / "audit.log")
    return PullRequestFeedbackWorker(store=store, adapters=adapters, audit_logger=audit)
