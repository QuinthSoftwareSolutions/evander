from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.ado_query_mode import ADO_QUERY_MODE_MANUAL_WIQL
from evander.events.models import TriggerEvent
from evander.events.provider_models import AdoWorkItemMatchingTriggerDefinition
from evander.issues.service import IssueService
from evander.models import AdoBacklogQueryRequest, AdoCurrentIterationBacklogRequest
from evander.triggers.providers._cursor_state import TriggerCursorStore
from evander.triggers.providers.base import TriggerProviderBase


class AdoWorkItemMatchingTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        result = self._query_work_items(trigger)
        items = result.get("items")
        if not isinstance(items, list):
            return []

        state = self._cursor_store().get(trigger.id)
        seen_items = state.get("work_items")
        if not isinstance(seen_items, dict):
            seen_items = {}

        events: list[TriggerEvent] = []
        directory_path = trigger.directory_path or self._context_directory_path()
        project = self._project_from_result_or_trigger(result, trigger)
        for item in items:
            if not isinstance(item, Mapping):
                continue
            work_item_id = _work_item_id(item)
            if not work_item_id:
                continue
            changed_at = _changed_at(item)
            cursor_marker = changed_at or "__seen__"
            previous_marker = str(seen_items.get(work_item_id) or "")
            if not _marker_is_newer(cursor_marker, previous_marker):
                continue
            occurred_at = _parse_dt(changed_at) or datetime.now(UTC)
            events.append(
                TriggerEvent(
                    event_id=(
                        f"{trigger.id}:azure_devops:{project}:{work_item_id}:{cursor_marker}"
                    ),
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload={
                        "provider": "azure_devops",
                        "directory_path": directory_path,
                        "project": project,
                        "work_item_id": work_item_id,
                        "changed_at": changed_at,
                        "work_item": dict(item),
                    },
                )
            )
        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        work_item_id = _work_item_id(event.payload)
        if not work_item_id:
            return
        changed_at = str(event.payload.get("changed_at") or "").strip()
        marker = changed_at or "__seen__"
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_work_item(state, work_item_id, marker),
        )

    def _query_work_items(self, trigger: AdoWorkItemMatchingTriggerDefinition) -> dict[str, Any]:
        context = self._context()
        if context.provider_config_resolver is None:
            raise RuntimeError(
                "trigger:ado:work-item-matching provider_config_resolver is not available"
            )
        issue_service = IssueService(context.provider_config_resolver)
        directory_path = trigger.directory_path or self._context_directory_path()
        if trigger.current_iteration:
            request = AdoCurrentIterationBacklogRequest.model_validate(
                {
                    "project": trigger.project,
                    "team": trigger.team,
                    "area_path": trigger.area_path,
                    "states": trigger.states,
                    "types": trigger.types,
                    "tags_any": trigger.tags_any,
                    "text_search": trigger.text_search,
                    "top": trigger.top,
                    "skip": 0,
                }
            )
            return _coerce_result(
                issue_service.list_remote_current_iteration_backlog_items(
                    provider="azure_devops",
                    directory_path=directory_path,
                    project=request.project,
                    team=request.team,
                    area_path=request.area_path,
                    states=request.states,
                    types=[item_type.value for item_type in request.types],
                    tags_any=request.tags_any,
                    text_search=request.text_search,
                    top=request.top,
                    skip=request.skip,
                )
            )

        request_payload: dict[str, Any] = {
            "project": trigger.project,
            "query_mode": trigger.query_mode,
            "wiql": trigger.wiql,
            "iteration": trigger.iteration,
            "area_path": trigger.area_path,
            "states": trigger.states,
            "types": trigger.types,
            "tags_any": trigger.tags_any,
            "text_search": trigger.text_search,
            "top": trigger.top,
            "skip": 0,
        }
        if trigger.query_mode == ADO_QUERY_MODE_MANUAL_WIQL:
            request_payload = {
                key: request_payload[key]
                for key in ("project", "query_mode", "wiql", "top", "skip")
                if request_payload.get(key) is not None
            }
        request = AdoBacklogQueryRequest.model_validate(request_payload)
        return _coerce_result(
            issue_service.query_remote_backlog_items(
                provider="azure_devops",
                directory_path=directory_path,
                project=request.project,
                wiql=request.wiql,
                iteration=request.iteration,
                area_path=request.area_path,
                states=request.states,
                types=[item_type.value for item_type in request.types],
                tags_any=request.tags_any,
                text_search=request.text_search,
                top=request.top,
                skip=request.skip,
            )
        )

    def _runtime_dir(self) -> Path:
        return Path(getattr(self._context().resolved_config, "runtime_dir", Path("runtime")))

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / "trigger-cursors-ado-work-items.json")

    def _context_directory_path(self) -> str:
        return str(self._context().metadata.get("directory_path") or "root").strip() or "root"

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AdoWorkItemMatchingTriggerProvider has no runtime context")
        return self.runtime_context

    def _trigger(self) -> AdoWorkItemMatchingTriggerDefinition:
        if not isinstance(self.trigger_definition, AdoWorkItemMatchingTriggerDefinition):
            raise RuntimeError(
                "AdoWorkItemMatchingTriggerProvider initialized with wrong definition"
            )
        return self.trigger_definition

    @staticmethod
    def _project_from_result_or_trigger(
        result: Mapping[str, Any], trigger: AdoWorkItemMatchingTriggerDefinition
    ) -> str:
        return str(result.get("project") or trigger.project or "").strip()


def _acknowledge_work_item(
    state: dict[str, Any], work_item_id: str, marker: str
) -> dict[str, Any]:
    work_items = state.get("work_items")
    if not isinstance(work_items, dict):
        work_items = {}
    previous = str(work_items.get(work_item_id) or "")
    if _marker_is_newer(marker, previous):
        work_items[work_item_id] = marker
    state["work_items"] = work_items
    return state


def _work_item_id(item: Mapping[str, Any]) -> str:
    for key in ("work_item_id", "remote_issue_id", "id"):
        text = str(item.get(key) or "").strip()
        if text:
            return text
    fields = item.get("fields")
    if isinstance(fields, Mapping):
        return str(fields.get("System.Id") or "").strip()
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


def _marker_is_newer(candidate: str, previous: str) -> bool:
    if not previous:
        return True
    candidate_dt = _parse_dt(candidate)
    previous_dt = _parse_dt(previous)
    if candidate_dt is None or previous_dt is None:
        return candidate != previous
    return candidate_dt > previous_dt


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or value == "__seen__":
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
