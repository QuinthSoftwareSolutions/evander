from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.events.models import TriggerEvent
from evander.events.provider_models import RemoteIssueCommentCreatedTriggerDefinition
from evander.issues.service import IssueService
from evander.triggers.providers._cursor_state import TriggerCursorStore, merge_cursor_ids
from evander.triggers.providers.base import TriggerProviderBase


@dataclass(frozen=True)
class _NormalizedComment:
    key: str
    comment_id: str
    body: str
    created_at: datetime
    created_at_text: str
    url: str
    author_names: tuple[str, ...]
    author_ids: tuple[str, ...]
    raw: dict[str, Any]


class RemoteIssueCommentCreatedTriggerProvider(TriggerProviderBase):
    _CURSOR_FILENAME = "trigger-cursors-remote-issue-comments.json"

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        issue_service = self._issue_service()
        directory_path = trigger.directory_path or self._context_directory_path()
        result = self._query_issues(trigger, issue_service, directory_path=directory_path)
        items = result.get("items")
        if not isinstance(items, list):
            return []

        state = self._cursor_store().get(trigger.id)
        streams_state = _normalize_streams_state(state.get("streams"))
        baseline_updates: dict[str, dict[str, Any]] = {}
        events: list[TriggerEvent] = []
        provider = _normalize_provider(trigger.provider)
        default_scope = _scope_from_result_or_trigger(result, trigger)

        for item in items:
            if not isinstance(item, Mapping):
                continue
            remote_issue_id = _remote_issue_id(item)
            if not remote_issue_id:
                continue
            scope = _scope_from_item(item, provider=provider) or default_scope
            detail = self._get_issue_detail(
                issue_service,
                provider=provider,
                directory_path=directory_path,
                scope=scope,
                remote_issue_id=remote_issue_id,
            )
            scope = _scope_from_item(detail, provider=provider) or scope
            remote_issue_id = _remote_issue_id(detail) or remote_issue_id
            stream_key = _stream_key(
                provider=provider,
                directory_path=directory_path,
                scope=scope,
                remote_issue_id=remote_issue_id,
            )
            comments = _normalize_comments(remote_issue_id=remote_issue_id, issue=detail)
            stream_state = streams_state.get(stream_key)
            if stream_state is None and not trigger.include_existing:
                baseline_updates[stream_key] = _baseline_stream_state(comments)
                continue

            recent_comment_keys = {
                value
                for value in _normalize_text_list(
                    stream_state.get("recent_comment_keys") if stream_state else []
                )
            }
            last_comment_created_at = _parse_dt(
                stream_state.get("last_comment_created_at") if stream_state else None
            )
            for comment in comments:
                if _comment_is_behind_cursor(
                    last_comment_created_at,
                    comment.created_at,
                    comment.key,
                    recent_comment_keys,
                ):
                    continue
                if not _comment_matches_trigger(comment, trigger):
                    continue
                events.append(
                    TriggerEvent(
                        event_id=f"{trigger.id}:{stream_key}:{comment.key}",
                        trigger_id=trigger.id,
                        trigger_type=trigger.type,
                        occurred_at=comment.created_at.astimezone(UTC),
                        payload=_comment_event_payload(
                            provider=provider,
                            directory_path=directory_path,
                            scope=scope,
                            issue=dict(detail),
                            remote_issue_id=remote_issue_id,
                            comment=comment,
                        ),
                    )
                )

        if baseline_updates:
            self._cursor_store().update(
                trigger.id,
                lambda state: _seed_stream_states(state, baseline_updates),
            )

        events.sort(key=lambda event: (event.occurred_at, event.event_id))
        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload if isinstance(event.payload, dict) else {}
        provider = _normalize_provider(payload.get("provider") or trigger.provider)
        directory_path = str(payload.get("directory_path") or "").strip()
        remote_issue_id = _remote_issue_id(payload)
        comment_id = str(payload.get("comment_id") or "").strip()
        created_at = _parse_dt(payload.get("comment_created_at")) or event.occurred_at
        if not directory_path or not remote_issue_id or not comment_id or created_at is None:
            return
        scope = _scope_from_payload_or_trigger(payload, trigger)
        stream_key = _stream_key(
            provider=provider,
            directory_path=directory_path,
            scope=scope,
            remote_issue_id=remote_issue_id,
        )
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_comment(state, stream_key, comment_id, created_at),
        )

    def _query_issues(
        self,
        trigger: RemoteIssueCommentCreatedTriggerDefinition,
        issue_service: IssueService,
        *,
        directory_path: str,
    ) -> dict[str, Any]:
        return _coerce_result(
            issue_service.query_remote_issues(
                provider=trigger.provider,
                directory_path=directory_path,
                project=trigger.project,
                repo=trigger.repo,
                query_mode=trigger.query_mode,
                wiql=trigger.wiql,
                current_iteration=trigger.current_iteration,
                team=trigger.team,
                iteration=trigger.iteration,
                area_path=trigger.area_path,
                states=trigger.states,
                types=trigger.types,
                tags_any=trigger.tags_any,
                text_search=trigger.text_search,
                state=trigger.state,
                labels_any=trigger.labels_any,
                labels_all=trigger.labels_all,
                assignee=trigger.assignee,
                author=trigger.author,
                mentioned=trigger.mentioned,
                milestone=trigger.milestone,
                since=trigger.since,
                top=trigger.top,
                skip=0,
            )
        )

    def _get_issue_detail(
        self,
        issue_service: IssueService,
        *,
        provider: str,
        directory_path: str,
        scope: str,
        remote_issue_id: str,
    ) -> dict[str, Any]:
        return _coerce_result(
            issue_service.read_remote_issue(
                provider=provider,
                remote_issue_id=remote_issue_id,
                directory_path=directory_path,
                project=scope,
                include_comments=True,
            )
        )

    def _issue_service(self) -> IssueService:
        context = self._context()
        if context.provider_config_resolver is None:
            raise RuntimeError(
                "trigger:remote-issue-comment-created provider_config_resolver is not available"
            )
        return IssueService(context.provider_config_resolver)

    def _runtime_dir(self) -> Path:
        runtime_dir = getattr(self._context().resolved_config, "runtime_dir", Path("runtime"))
        return Path(runtime_dir)

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / self._CURSOR_FILENAME)

    def _context_directory_path(self) -> str:
        return str(self._context().metadata.get("directory_path") or "root").strip() or "root"

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("RemoteIssueCommentCreatedTriggerProvider has no runtime context")
        return self.runtime_context

    def _trigger(self) -> RemoteIssueCommentCreatedTriggerDefinition:
        if not isinstance(self.trigger_definition, RemoteIssueCommentCreatedTriggerDefinition):
            raise RuntimeError(
                "RemoteIssueCommentCreatedTriggerProvider initialized with wrong definition"
            )
        return self.trigger_definition


def _comment_event_payload(
    *,
    provider: str,
    directory_path: str,
    scope: str,
    issue: dict[str, Any],
    remote_issue_id: str,
    comment: _NormalizedComment,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": provider,
        "directory_path": directory_path,
        "remote_issue_id": remote_issue_id,
        "issue_id": str(issue.get("issue_id") or remote_issue_id),
        "issue_number": str(issue.get("issue_number") or issue.get("number") or remote_issue_id),
        "updated_at": _updated_at(issue),
        "issue": issue,
        "comment_id": comment.comment_id,
        "comment_body": comment.body,
        "comment_created_at": comment.created_at_text,
        "comment_url": comment.url,
        "comment_author": {
            "names": list(comment.author_names),
            "ids": list(comment.author_ids),
        },
        "comment": dict(comment.raw),
    }
    if provider == "github":
        payload["repo"] = scope
    if provider == "azure_devops":
        payload["project"] = scope
        payload["work_item_id"] = remote_issue_id
        payload["changed_at"] = _updated_at(issue)
        payload["work_item"] = issue
    return payload


def _normalize_comments(
    *, remote_issue_id: str, issue: Mapping[str, Any]
) -> list[_NormalizedComment]:
    comments = issue.get("comments")
    if not isinstance(comments, list):
        return []
    fallback_created_at = _parse_dt(_updated_at(issue)) or datetime.now(UTC)
    normalized: list[_NormalizedComment] = []
    for raw_comment in comments:
        if not isinstance(raw_comment, Mapping):
            continue
        raw = dict(raw_comment)
        created_at_text = _comment_created_at(raw)
        created_at = _parse_dt(created_at_text) or fallback_created_at
        body = _comment_body(raw)
        author_names = tuple(_comment_author_names(raw))
        author_ids = tuple(_comment_author_ids(raw))
        comment_id = _comment_id(raw)
        key = comment_id or _generated_comment_key(
            remote_issue_id=remote_issue_id,
            body=body,
            created_at=created_at_text,
            author_names=author_names,
            author_ids=author_ids,
        )
        normalized.append(
            _NormalizedComment(
                key=key,
                comment_id=key,
                body=body,
                created_at=created_at,
                created_at_text=created_at_text
                or created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                url=_first_text(raw, "url", "web_url", "html_url") or _nested_web_url(raw),
                author_names=author_names,
                author_ids=author_ids,
                raw=raw,
            )
        )
    normalized.sort(key=lambda comment: (comment.created_at, comment.key))
    return normalized


def _comment_matches_trigger(
    comment: _NormalizedComment,
    trigger: RemoteIssueCommentCreatedTriggerDefinition,
) -> bool:
    excluded_names = {_match_key(value) for value in trigger.comment_author_exclude_names}
    excluded_ids = {_match_key(value) for value in trigger.comment_author_exclude_ids}
    if excluded_names and excluded_names.intersection(
        _match_key(value) for value in comment.author_names
    ):
        return False
    if excluded_ids and excluded_ids.intersection(
        _match_key(value) for value in comment.author_ids
    ):
        return False

    blob = _comment_search_blob(comment.raw)
    if not trigger.comment_case_sensitive:
        blob = blob.casefold()
    if trigger.comment_contains:
        needle = _search_needle(trigger.comment_contains, trigger.comment_case_sensitive)
        if needle not in blob:
            return False
    if trigger.comment_contains_any:
        needles = [
            _search_needle(value, trigger.comment_case_sensitive)
            for value in trigger.comment_contains_any
        ]
        if not any(needle in blob for needle in needles):
            return False
    return True


def _comment_is_behind_cursor(
    last_created_at: datetime | None,
    created_at: datetime,
    comment_key: str,
    recent_comment_keys: set[str],
) -> bool:
    if last_created_at is None:
        return comment_key in recent_comment_keys
    if created_at < last_created_at:
        return True
    if created_at == last_created_at and comment_key in recent_comment_keys:
        return True
    return False


def _baseline_stream_state(comments: list[_NormalizedComment]) -> dict[str, Any]:
    if not comments:
        return {"last_comment_created_at": None, "recent_comment_keys": []}
    latest_created_at = max(comment.created_at for comment in comments)
    recent_keys = [comment.key for comment in comments if comment.created_at == latest_created_at]
    return {
        "last_comment_created_at": latest_created_at.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "recent_comment_keys": merge_cursor_ids([], recent_keys),
    }


def _acknowledge_comment(
    state: dict[str, Any], stream_key: str, comment_id: str, created_at: datetime
) -> dict[str, Any]:
    streams = _normalize_streams_state(state.get("streams"))
    stream_state = streams.get(stream_key, {})
    previous_created_at = _parse_dt(stream_state.get("last_comment_created_at"))
    created_at_utc = created_at.astimezone(UTC)
    if previous_created_at is None or created_at_utc > previous_created_at:
        stream_state = {
            "last_comment_created_at": created_at_utc.isoformat().replace("+00:00", "Z"),
            "recent_comment_keys": [comment_id],
        }
    elif created_at_utc == previous_created_at:
        stream_state["recent_comment_keys"] = merge_cursor_ids(
            stream_state.get("recent_comment_keys"),
            [comment_id],
        )
    streams[stream_key] = stream_state
    state["streams"] = streams
    return state


def _seed_stream_states(
    state: dict[str, Any], updates: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    streams = _normalize_streams_state(state.get("streams"))
    for stream_key, stream_state in updates.items():
        streams.setdefault(stream_key, stream_state)
    state["streams"] = streams
    return state


def _normalize_streams_state(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, raw_state in value.items():
        if isinstance(raw_state, dict):
            normalized[str(key)] = dict(raw_state)
    return normalized


def _scope_from_result_or_trigger(
    result: Mapping[str, Any], trigger: RemoteIssueCommentCreatedTriggerDefinition
) -> str:
    if _normalize_provider(trigger.provider) == "github":
        return str(result.get("repo") or trigger.repo or trigger.project or "").strip()
    return str(result.get("project") or trigger.project or "").strip()


def _scope_from_payload_or_trigger(
    payload: Mapping[str, Any], trigger: RemoteIssueCommentCreatedTriggerDefinition
) -> str:
    if _normalize_provider(payload.get("provider") or trigger.provider) == "github":
        return str(payload.get("repo") or trigger.repo or trigger.project or "").strip()
    return str(payload.get("project") or trigger.project or "").strip()


def _scope_from_item(item: Mapping[str, Any], *, provider: str) -> str:
    if provider == "github":
        return str(item.get("repo") or "").strip()
    return str(item.get("project") or "").strip()


def _stream_key(*, provider: str, directory_path: str, scope: str, remote_issue_id: str) -> str:
    return "|".join([provider, directory_path, scope, remote_issue_id])


def _remote_issue_id(item: Mapping[str, Any]) -> str:
    for key in ("remote_issue_id", "issue_id", "issue_number", "work_item_id", "id", "number"):
        text = str(item.get(key) or "").strip()
        if text:
            return text
    fields = item.get("fields")
    if isinstance(fields, Mapping):
        return str(fields.get("System.Id") or "").strip()
    return ""


def _updated_at(item: Mapping[str, Any]) -> str:
    for key in ("updated_at", "changed_at", "updated_date"):
        text = str(item.get(key) or "").strip()
        if text:
            return text
    fields = item.get("fields")
    if isinstance(fields, Mapping):
        return str(fields.get("System.ChangedDate") or "").strip()
    return ""


def _comment_id(comment: Mapping[str, Any]) -> str:
    return _first_text(comment, "comment_id", "id", "commentId", "node_id")


def _comment_body(comment: Mapping[str, Any]) -> str:
    return _first_text(comment, "comment_body", "body", "text", "content")


def _comment_created_at(comment: Mapping[str, Any]) -> str:
    return _first_text(
        comment,
        "comment_created_at",
        "created_at",
        "createdDate",
        "created_date",
    )


def _comment_author_names(comment: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("created_by", "createdBy", "author", "user"):
        value = comment.get(key)
        if isinstance(value, Mapping):
            for nested_key in ("displayName", "display_name", "name", "login", "uniqueName"):
                text = str(value.get(nested_key) or "").strip()
                if text:
                    names.append(text)
        else:
            text = str(value or "").strip()
            if text:
                names.append(text)
    identity = comment.get("created_by_identity")
    if isinstance(identity, Mapping):
        for key in ("displayName", "display_name", "name", "login", "uniqueName"):
            text = str(identity.get(key) or "").strip()
            if text:
                names.append(text)
    return _dedupe(names)


def _comment_author_ids(comment: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("created_by_identity", "createdBy", "author", "user"):
        value = comment.get(key)
        if isinstance(value, Mapping):
            for nested_key in ("id", "descriptor", "login", "node_id"):
                text = str(value.get(nested_key) or "").strip()
                if text:
                    ids.append(text)
    return _dedupe(ids)


def _comment_search_blob(comment: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            _comment_body(comment),
            " ".join(_comment_author_names(comment)),
            " ".join(_comment_author_ids(comment)),
        ]
    )


def _first_text(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _nested_web_url(payload: Mapping[str, Any]) -> str:
    links = payload.get("_links")
    if not isinstance(links, Mapping):
        return ""
    web = links.get("web")
    if not isinstance(web, Mapping):
        return ""
    return str(web.get("href") or "").strip()


def _generated_comment_key(
    *,
    remote_issue_id: str,
    body: str,
    created_at: str,
    author_names: tuple[str, ...],
    author_ids: tuple[str, ...],
) -> str:
    digest_payload = repr(
        {
            "remote_issue_id": remote_issue_id,
            "body": body,
            "created_at": created_at,
            "author_names": author_names,
            "author_ids": author_ids,
        }
    ).encode("utf-8", errors="replace")
    return "generated:" + hashlib.sha256(digest_payload).hexdigest()[:32]


def _normalize_provider(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"ado", "azure_devops", "azure-devops"}:
        return "azure_devops"
    return text


def _normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _match_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _search_needle(value: str, case_sensitive: bool) -> str:
    return value if case_sensitive else value.casefold()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


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


def _coerce_result(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, Mapping):
        return dict(result)
    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if hasattr(result, "__dict__"):
        return dict(vars(result))
    return {"result": result}
