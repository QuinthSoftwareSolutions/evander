from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.events.models import TriggerEvent
from evander.events.provider_models import RemoteIssueMatchingTriggerDefinition
from evander.issues.service import IssueService
from evander.provider_runtime.results import coerce_mapping_result as _coerce_result
from evander.triggers.providers._matching_markers import (
    _marker_is_newer as _marker_is_newer,
    _parse_dt as _parse_dt,
)
from evander.triggers.providers._cursor_state import TriggerCursorStore
from evander.triggers.providers.base import TriggerProviderBase


class RemoteIssueMatchingTriggerProvider(TriggerProviderBase):
    _CURSOR_FILENAME = "trigger-cursors-remote-issues.json"

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        result = self._query_issues(trigger)
        items = result.get("items")
        if not isinstance(items, list):
            return []

        state = self._cursor_store().get(trigger.id)
        seen_issues = state.get("issues")
        if not isinstance(seen_issues, dict):
            seen_issues = {}

        events: list[TriggerEvent] = []
        directory_path = trigger.directory_path or self._context_directory_path()
        provider = _normalize_provider(trigger.provider)
        scope = _scope_from_result_or_trigger(result, trigger)
        for item in items:
            if not isinstance(item, Mapping):
                continue
            remote_issue_id = _remote_issue_id(item)
            if not remote_issue_id:
                continue
            updated_at = _updated_at(item)
            marker = updated_at or "__seen__"
            issue_key = _issue_key(provider=provider, scope=scope, remote_issue_id=remote_issue_id)
            previous_marker = str(seen_issues.get(issue_key) or "")
            if not _marker_is_newer(marker, previous_marker):
                continue
            occurred_at = _parse_dt(updated_at) or datetime.now(UTC)
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{issue_key}:{marker}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload=_issue_event_payload(
                        provider=provider,
                        directory_path=directory_path,
                        scope=scope,
                        item=dict(item),
                        remote_issue_id=remote_issue_id,
                        updated_at=updated_at,
                    ),
                )
            )
        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload if isinstance(event.payload, dict) else {}
        provider = _normalize_provider(payload.get("provider") or trigger.provider)
        remote_issue_id = _remote_issue_id(payload)
        if not remote_issue_id:
            return
        scope = _scope_from_payload_or_trigger(payload, trigger)
        marker = str(payload.get("updated_at") or payload.get("changed_at") or "").strip()
        marker = marker or "__seen__"
        issue_key = _issue_key(provider=provider, scope=scope, remote_issue_id=remote_issue_id)
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_issue(state, issue_key, marker),
        )

    def _query_issues(self, trigger: RemoteIssueMatchingTriggerDefinition) -> dict[str, Any]:
        context = self._context()
        if context.provider_config_resolver is None:
            raise RuntimeError(
                "trigger:remote-issue-matching provider_config_resolver is not available"
            )
        issue_service = self._issue_service(context.provider_config_resolver)
        directory_path = trigger.directory_path or self._context_directory_path()
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

    def _issue_service(self, provider_config_resolver: object) -> IssueService:
        return IssueService(provider_config_resolver)  # type: ignore[arg-type]

    def _runtime_dir(self) -> Path:
        return Path(getattr(self._context().resolved_config, "runtime_dir", Path("runtime")))

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / self._CURSOR_FILENAME)

    def _context_directory_path(self) -> str:
        return str(self._context().metadata.get("directory_path") or "root").strip() or "root"

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("RemoteIssueMatchingTriggerProvider has no runtime context")
        return self.runtime_context

    def _trigger(self) -> RemoteIssueMatchingTriggerDefinition:
        if not isinstance(self.trigger_definition, RemoteIssueMatchingTriggerDefinition):
            raise RuntimeError(
                "RemoteIssueMatchingTriggerProvider initialized with wrong definition"
            )
        return self.trigger_definition


def _acknowledge_issue(state: dict[str, Any], issue_key: str, marker: str) -> dict[str, Any]:
    issues = state.get("issues")
    if not isinstance(issues, dict):
        issues = {}
    previous = str(issues.get(issue_key) or "")
    if _marker_is_newer(marker, previous):
        issues[issue_key] = marker
    state["issues"] = issues
    return state


def _issue_event_payload(
    *,
    provider: str,
    directory_path: str,
    scope: str,
    item: dict[str, Any],
    remote_issue_id: str,
    updated_at: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": provider,
        "directory_path": directory_path,
        "remote_issue_id": remote_issue_id,
        "issue_id": str(item.get("issue_id") or remote_issue_id),
        "issue_number": str(item.get("issue_number") or item.get("number") or remote_issue_id),
        "updated_at": updated_at,
        "issue": item,
    }
    if provider == "github":
        payload["repo"] = scope
    if provider == "azure_devops":
        payload["project"] = scope
        payload["work_item_id"] = remote_issue_id
        payload["changed_at"] = updated_at
        payload["work_item"] = item
    return payload


def _scope_from_result_or_trigger(
    result: Mapping[str, Any], trigger: RemoteIssueMatchingTriggerDefinition
) -> str:
    if _normalize_provider(trigger.provider) == "github":
        return str(result.get("repo") or trigger.repo or trigger.project or "").strip()
    return str(result.get("project") or trigger.project or "").strip()


def _scope_from_payload_or_trigger(
    payload: Mapping[str, Any], trigger: RemoteIssueMatchingTriggerDefinition
) -> str:
    if _normalize_provider(payload.get("provider") or trigger.provider) == "github":
        return str(payload.get("repo") or trigger.repo or trigger.project or "").strip()
    return str(payload.get("project") or trigger.project or "").strip()


def _issue_key(*, provider: str, scope: str, remote_issue_id: str) -> str:
    return "|".join([provider, scope, remote_issue_id])


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


def _normalize_provider(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"ado", "azure_devops", "azure-devops"}:
        return "azure_devops"
    return text
