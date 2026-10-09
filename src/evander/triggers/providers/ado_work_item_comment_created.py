from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.events.models import TriggerEvent
from evander.events.provider_models import (
    AdoWorkItemCommentCreatedTriggerDefinition,
)
from evander.issues.service import AdoCommentPollReader, IssueService
from evander.triggers.providers._ado_work_item_query import query_ado_work_items
from evander.triggers.providers._cursor_state import TriggerCursorStore, merge_cursor_ids
from evander.triggers.providers.base import TriggerProviderBase


_RECENT_COMMENT_LIMIT = 5000


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


class AdoWorkItemCommentCreatedTriggerProvider(TriggerProviderBase):
    """Poll Azure DevOps work-item comments with trigger-owned cursors."""

    _CURSOR_FILENAME = "trigger-cursors-ado-work-item-comments.json"

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        issue_service = self._issue_service()
        directory_path = trigger.directory_path or self._context_directory_path()
        with issue_service.ado_comment_poll(directory_path=directory_path) as reader:
            return self._poll(trigger, reader, directory_path=directory_path)

    def _poll(
        self,
        trigger: AdoWorkItemCommentCreatedTriggerDefinition,
        issue_service: AdoCommentPollReader,
        *,
        directory_path: str,
    ) -> list[TriggerEvent]:
        result = self._query_work_items(trigger, issue_service, directory_path=directory_path)
        items = result.get("items")
        if not isinstance(items, list):
            return []

        state = self._cursor_store().get(trigger.id)
        streams_state = _normalize_streams_state(state.get("streams"))
        baseline_updates: dict[str, dict[str, Any]] = {}
        events: list[TriggerEvent] = []
        default_project = self._project_from_result_or_trigger(result, trigger)

        for item in items:
            if not isinstance(item, Mapping):
                continue
            work_item_id = _work_item_id(item)
            if not work_item_id:
                continue
            project = _project_from_item(item) or default_project or trigger.project
            work_item = self._get_work_item_detail(
                issue_service,
                directory_path=directory_path,
                project=project,
                work_item_id=work_item_id,
            )
            project = _project_from_item(work_item) or project or ""
            work_item_id = _work_item_id(work_item) or work_item_id
            stream_key = _stream_key(
                directory_path=directory_path,
                project=project,
                work_item_id=work_item_id,
            )
            comments = _normalize_comments(work_item_id=work_item_id, work_item=work_item)
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
                        payload={
                            "provider": "azure_devops",
                            "directory_path": directory_path,
                            "project": project,
                            "work_item_id": work_item_id,
                            "changed_at": _changed_at(work_item),
                            "work_item": dict(work_item),
                            "comment_id": comment.comment_id,
                            "comment_body": comment.body,
                            "comment_created_at": comment.created_at_text,
                            "comment_url": comment.url,
                            "comment_author": {
                                "names": list(comment.author_names),
                                "ids": list(comment.author_ids),
                            },
                            "comment": dict(comment.raw),
                        },
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
        directory_path = str(payload.get("directory_path") or "").strip()
        project = str(payload.get("project") or "").strip()
        work_item_id = str(payload.get("work_item_id") or "").strip()
        comment_id = str(payload.get("comment_id") or "").strip()
        created_at = _parse_dt(payload.get("comment_created_at")) or event.occurred_at
        if not directory_path or not work_item_id or not comment_id or created_at is None:
            return
        stream_key = _stream_key(
            directory_path=directory_path,
            project=project,
            work_item_id=work_item_id,
        )
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_comment(state, stream_key, comment_id, created_at),
        )

    def _query_work_items(
        self,
        trigger: AdoWorkItemCommentCreatedTriggerDefinition,
        issue_service: AdoCommentPollReader,
        *,
        directory_path: str,
    ) -> dict[str, Any]:
        return _coerce_result(
            query_ado_work_items(trigger, issue_service, directory_path=directory_path)
        )

    def _get_work_item_detail(
        self,
        issue_service: AdoCommentPollReader,
        *,
        directory_path: str,
        project: str | None,
        work_item_id: str,
    ) -> dict[str, Any]:
        return _coerce_result(
            issue_service.get_remote_backlog_item_by_id(
                provider="azure_devops",
                directory_path=directory_path,
                backlog_item_id=work_item_id,
                project=project,
                include_comments=True,
                include_relations=True,
                include_updates=False,
            )
        )

    def _issue_service(self) -> IssueService:
        context = self._context()
        if context.provider_config_resolver is None:
            raise RuntimeError(
                "trigger:ado:work-item-comment-created provider_config_resolver is not available"
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
            raise RuntimeError("AdoWorkItemCommentCreatedTriggerProvider has no runtime context")
        return self.runtime_context

    def _trigger(self) -> AdoWorkItemCommentCreatedTriggerDefinition:
        if not isinstance(self.trigger_definition, AdoWorkItemCommentCreatedTriggerDefinition):
            raise RuntimeError(
                "AdoWorkItemCommentCreatedTriggerProvider initialized with wrong definition"
            )
        return self.trigger_definition

    @staticmethod
    def _project_from_result_or_trigger(
        result: Mapping[str, Any], trigger: AdoWorkItemCommentCreatedTriggerDefinition
    ) -> str:
        return str(result.get("project") or trigger.project or "").strip()


def _normalize_comments(
    *, work_item_id: str, work_item: Mapping[str, Any]
) -> list[_NormalizedComment]:
    comments = work_item.get("comments")
    if not isinstance(comments, list):
        return []
    fallback_created_at = _parse_dt(_changed_at(work_item)) or datetime.now(UTC)
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
            work_item_id=work_item_id,
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
                url=_first_text(raw, "url", "web_url") or _nested_web_url(raw),
                author_names=author_names,
                author_ids=author_ids,
                raw=raw,
            )
        )
    normalized.sort(key=lambda comment: (comment.created_at, comment.key))
    return normalized


def _comment_matches_trigger(
    comment: _NormalizedComment,
    trigger: AdoWorkItemCommentCreatedTriggerDefinition,
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


def _baseline_stream_state(comments: list[_NormalizedComment]) -> dict[str, Any]:
    last_created_at = max((comment.created_at for comment in comments), default=None)
    return {
        "initialized": True,
        "last_comment_created_at": (
            last_created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if last_created_at is not None
            else None
        ),
        "recent_comment_keys": merge_cursor_ids(
            [],
            [comment.key for comment in comments],
            limit=_RECENT_COMMENT_LIMIT,
        ),
    }


def _seed_stream_states(
    state: dict[str, Any],
    baseline_updates: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    streams = _normalize_streams_state(state.get("streams"))
    for stream_key, stream_state in baseline_updates.items():
        streams[stream_key] = dict(stream_state)
    state["streams"] = streams
    return state


def _acknowledge_comment(
    state: dict[str, Any],
    stream_key: str,
    comment_key: str,
    occurred_at: datetime,
) -> dict[str, Any]:
    streams = _normalize_streams_state(state.get("streams"))
    stream_state = streams.get(stream_key)
    if not isinstance(stream_state, dict):
        stream_state = {}
    stream_state["initialized"] = True
    stream_state["last_comment_created_at"] = _latest_timestamp_text(
        stream_state.get("last_comment_created_at"),
        occurred_at,
    )
    stream_state["recent_comment_keys"] = merge_cursor_ids(
        stream_state.get("recent_comment_keys"),
        [comment_key],
        limit=_RECENT_COMMENT_LIMIT,
    )
    streams[stream_key] = stream_state
    state["streams"] = streams
    return state


def _comment_is_behind_cursor(
    last_comment_created_at: datetime | None,
    comment_created_at: datetime,
    comment_key: str,
    recent_comment_keys: set[str],
) -> bool:
    if last_comment_created_at is None:
        return False
    occurred_at = comment_created_at.astimezone(UTC)
    cursor = last_comment_created_at.astimezone(UTC)
    if occurred_at < cursor:
        return True
    return occurred_at == cursor and comment_key in recent_comment_keys


def _normalize_streams_state(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    streams: dict[str, dict[str, Any]] = {}
    for key, raw_state in value.items():
        if not isinstance(raw_state, Mapping):
            continue
        stream_key = str(key).strip()
        if stream_key:
            streams[stream_key] = dict(raw_state)
    return streams


def _normalize_text_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _stream_key(*, directory_path: str, project: str | None, work_item_id: str) -> str:
    return "|".join(
        (
            "azure_devops",
            directory_path.strip() or "root",
            str(project or "").strip(),
            work_item_id.strip(),
        )
    )


def _work_item_id(item: Mapping[str, Any]) -> str:
    for key in ("work_item_id", "remote_issue_id", "id"):
        text = str(item.get(key) or "").strip()
        if text:
            return text
    fields = item.get("fields")
    if isinstance(fields, Mapping):
        return str(fields.get("System.Id") or "").strip()
    return ""


def _project_from_item(item: Mapping[str, Any]) -> str:
    for key in ("project", "team_project"):
        text = str(item.get(key) or "").strip()
        if text:
            return text
    fields = item.get("fields")
    if isinstance(fields, Mapping):
        return str(fields.get("System.TeamProject") or "").strip()
    return ""


def _changed_at(item: Mapping[str, Any]) -> str:
    for key in ("changed_at", "updated_at", "updated_date"):
        text = str(item.get(key) or "").strip()
        if text:
            return text
    fields = item.get("fields")
    if isinstance(fields, Mapping):
        return str(fields.get("System.ChangedDate") or "").strip()
    return ""


def _comment_id(comment: Mapping[str, Any]) -> str:
    for key in ("comment_id", "id", "commentId"):
        text = str(comment.get(key) or "").strip()
        if text:
            return text
    return ""


def _comment_body(comment: Mapping[str, Any]) -> str:
    for key in ("comment_body", "body", "text", "content", "raw_text", "html"):
        text = str(comment.get(key) or "").strip()
        if text:
            return text
    return ""


def _comment_created_at(comment: Mapping[str, Any]) -> str:
    for key in ("created_at", "createdDate", "created_date"):
        text = str(comment.get(key) or "").strip()
        if text:
            return text
    return ""


def _comment_search_blob(comment: Mapping[str, Any]) -> str:
    values = [
        str(comment.get(key) or "")
        for key in ("comment_body", "body", "text", "content", "raw_text", "html")
    ]
    return "\n".join(value for value in values if value)


def _comment_author_names(comment: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "created_by",
        "created_by_identity",
        "author",
        "createdBy",
        "revisedBy",
    ):
        value = comment.get(key)
        if isinstance(value, Mapping):
            for nested_key in (
                "displayName",
                "display_name",
                "uniqueName",
                "mailAddress",
                "name",
            ):
                text = str(value.get(nested_key) or "").strip()
                if text:
                    values.append(text)
        elif value is not None:
            text = str(value).strip()
            if text:
                values.append(text)
    return _dedupe(values)


def _comment_author_ids(comment: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("author", "createdBy", "revisedBy", "created_by", "created_by_identity"):
        value = comment.get(key)
        if isinstance(value, Mapping):
            for nested_key in ("id", "descriptor", "uniqueName", "mailAddress"):
                text = str(value.get(nested_key) or "").strip()
                if text:
                    values.append(text)
    return _dedupe(values)


def _nested_web_url(payload: Mapping[str, Any]) -> str:
    links = payload.get("_links")
    if not isinstance(links, Mapping):
        return ""
    web = links.get("web")
    if not isinstance(web, Mapping):
        return ""
    return str(web.get("href") or "").strip()


def _first_text(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _match_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _search_needle(value: str, case_sensitive: bool) -> str:
    needle = str(value or "").strip()
    return needle if case_sensitive else needle.casefold()


def _generated_comment_key(
    *,
    work_item_id: str,
    body: str,
    created_at: str,
    author_names: tuple[str, ...],
    author_ids: tuple[str, ...],
) -> str:
    payload = repr(
        {
            "work_item_id": work_item_id,
            "body": body,
            "created_at": created_at,
            "author_names": author_names,
            "author_ids": author_ids,
        }
    ).encode("utf-8", errors="replace")
    return "generated:" + hashlib.sha256(payload).hexdigest()[:32]


def _latest_timestamp_text(existing: object, candidate: datetime) -> str:
    existing_dt = _parse_dt(existing)
    candidate_dt = candidate.astimezone(UTC)
    if existing_dt is not None and existing_dt > candidate_dt:
        candidate_dt = existing_dt
    return candidate_dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
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
    return {}
