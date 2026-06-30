from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from evander.events.models import TriggerEvent
from evander.events.provider_models import IssueCommentCreatedTriggerDefinition
from evander.models import LocalIssueRecord
from evander.storage import JsonStore
from evander.triggers.providers._cursor_state import TriggerCursorStore, merge_cursor_ids
from evander.triggers.providers._local_tickets import within_ticket_directories
from evander.triggers.providers.base import TriggerProviderBase


class IssueCommentCreatedTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        seen = {str(item) for item in state.get("seen_comment_keys", []) if str(item).strip()}

        issues_store = JsonStore(self._runtime_dir() / "local-issues.json", LocalIssueRecord)
        issues = [
            issue
            for issue in issues_store.read_all().values()
            if _matches_trigger(issue, trigger)
        ]
        issues.sort(key=lambda issue: (issue.updated_at, issue.issue_id))

        events: list[TriggerEvent] = []
        next_seen = set(seen)
        for issue in issues:
            for raw_comment in issue.comments:
                if not isinstance(raw_comment, dict):
                    continue
                normalized = _normalize_comment(issue, raw_comment)
                if normalized is None:
                    continue
                seen_key = f"{issue.issue_id}:{normalized['comment_id']}"
                if seen_key in next_seen:
                    continue
                next_seen.add(seen_key)
                occurred_at = _parse_dt(normalized.get("created_at"))
                if occurred_at is None:
                    occurred_at = issue.updated_at.astimezone(UTC)
                events.append(
                    TriggerEvent(
                        event_id=f"{trigger.id}:{seen_key}",
                        trigger_id=trigger.id,
                        trigger_type=trigger.type,
                        occurred_at=occurred_at,
                        payload=normalized,
                    )
                )

        events.sort(key=lambda event: (event.occurred_at, event.event_id))
        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        issue_id = str(event.payload.get("issue_id") or "").strip()
        comment_id = str(event.payload.get("comment_id") or "").strip()
        if not issue_id or not comment_id:
            return
        seen_key = f"{issue_id}:{comment_id}"
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_comment_key(state, seen_key),
        )

    def _trigger(self) -> IssueCommentCreatedTriggerDefinition:
        if not isinstance(self.trigger_definition, IssueCommentCreatedTriggerDefinition):
            raise RuntimeError(
                "IssueCommentCreatedTriggerProvider initialized with a "
                "non-issue-comment-created trigger definition"
            )
        return self.trigger_definition

    def _runtime_dir(self):
        if self.runtime_context is None:
            raise RuntimeError("IssueCommentCreatedTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / "trigger-cursors-issue-comments.json")


def _matches_trigger(
    issue: LocalIssueRecord, trigger: IssueCommentCreatedTriggerDefinition
) -> bool:
    if not within_ticket_directories(issue.directory_path, trigger.directories):
        return False
    provider = _normalize_provider(trigger.provider)
    if provider is None:
        return True
    remote = issue.remote_reference
    issue_provider = _normalize_provider(remote.provider if remote is not None else issue.source)
    if issue_provider != provider:
        return False
    if trigger.organization_url:
        organization = (remote.organization if remote is not None else None) or ""
        if organization.rstrip("/") != trigger.organization_url.rstrip("/"):
            return False
    if trigger.project:
        project = (remote.project if remote is not None else None) or ""
        if project != trigger.project:
            return False
    return True


def _normalize_provider(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"ado", "azure_devops", "azure-devops"}:
        return "ado"
    return text


def _normalize_comment(
    issue: LocalIssueRecord, raw_comment: dict[str, Any]
) -> dict[str, Any] | None:
    body = _first_text(raw_comment, "comment_body", "body", "text", "content")
    comment_id = _first_text(raw_comment, "comment_id", "id", "commentId")
    created_at = _first_text(raw_comment, "created_at", "createdDate", "created_date")
    url = _first_text(raw_comment, "url", "web_url") or _nested_web_url(raw_comment)
    author = _author_object(raw_comment)
    if not comment_id:
        comment_id = _fallback_comment_id(
            issue_id=issue.issue_id,
            body=body,
            created_at=created_at,
            author=author,
        )
    if not comment_id:
        return None
    remote = issue.remote_reference
    metadata = {
        "directory_path": issue.directory_path,
        "raw_comment": raw_comment,
    }
    if remote is not None:
        metadata["remote_reference"] = remote.model_dump(mode="json")
    return {
        "issue_id": issue.issue_id,
        "issue_number": issue.issue_number,
        "comment_id": comment_id,
        "comment_body": body,
        "author": author,
        "created_at": created_at,
        "url": url or issue.url or "",
        "source": issue.source,
        "source_type": issue.source_type,
        "metadata": metadata,
    }


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _nested_web_url(payload: dict[str, Any]) -> str:
    links = payload.get("_links")
    if not isinstance(links, dict):
        return ""
    web = links.get("web")
    if not isinstance(web, dict):
        return ""
    return str(web.get("href") or "").strip()


def _author_object(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("author", "createdBy", "created_by", "revisedBy", "revised_by"):
        value = payload.get(key)
        if isinstance(value, dict):
            return {str(k): v for k, v in value.items()}
        if value is not None:
            text = str(value).strip()
            if text:
                return {"display_name": text}
    return {}


def _fallback_comment_id(
    *,
    issue_id: str,
    body: str,
    created_at: str,
    author: dict[str, Any],
) -> str:
    if not body and not created_at and not author:
        return ""
    digest_payload = repr(
        {
            "issue_id": issue_id,
            "body": body,
            "created_at": created_at,
            "author": author,
        }
    ).encode("utf-8", errors="replace")
    return "generated:" + hashlib.sha256(digest_payload).hexdigest()[:32]


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _acknowledge_comment_key(state: dict[str, object], seen_key: str) -> dict[str, object]:
    state["seen_comment_keys"] = merge_cursor_ids(
        state.get("seen_comment_keys"),
        [seen_key],
        limit=5000,
    )
    return state
