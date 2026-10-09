from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.ado_query_mode import ADO_QUERY_MODE_MANUAL_WIQL, validate_ado_query_mode
from evander.issues.service import IssueService
from evander.models import AdoBacklogQueryRequest, AdoCurrentIterationBacklogRequest

from evander.provider_runtime.results import coerce_mapping_result
from .base import QueryProviderBase


class AdoWorkItemsQueryProvider(QueryProviderBase):
    """Query Azure DevOps work items from workflow nodes."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update({key: value for key, value in dict(resolved_config).items()})
        payload = {key: value for key, value in payload.items() if value is not None}

        directory_path = self._normalize_optional_string(payload.pop("directory_path", None))
        if directory_path is None:
            directory_path = self._context_directory_path()

        wiql = self._normalize_optional_string(payload.get("wiql"))
        current_iteration = self._coerce_bool(payload.pop("current_iteration", None), default=False)
        query_mode = validate_ado_query_mode(
            query_mode=payload.get("query_mode"),
            wiql=wiql,
            current_iteration=current_iteration,
            context="query:ado:work-items",
        )

        context = self._context()
        if context.provider_config_resolver is None:
            raise RuntimeError("query:ado:work-items provider_config_resolver is not available")
        issue_service = IssueService(context.provider_config_resolver)

        if current_iteration:
            request = AdoCurrentIterationBacklogRequest.model_validate(payload)
            return self._coerce_result(
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

        request_payload = payload
        request_payload["query_mode"] = query_mode
        if query_mode == ADO_QUERY_MODE_MANUAL_WIQL:
            request_payload = {
                key: request_payload[key]
                for key in ("project", "query_mode", "wiql", "top", "skip")
                if key in request_payload
            }
        request = AdoBacklogQueryRequest.model_validate(request_payload)
        return self._coerce_result(
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

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in (
            "directory_path",
            "project",
            "query_mode",
            "wiql",
            "current_iteration",
            "team",
            "iteration",
            "area_path",
            "states",
            "types",
            "tags_any",
            "text_search",
            "top",
            "skip",
        ):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AdoWorkItemsQueryProvider has no runtime context")
        return self.runtime_context

    def _context_directory_path(self) -> str:
        metadata = self._context().metadata
        return self._normalize_optional_string(metadata.get("directory_path")) or "root"

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError("query:ado:work-items current_iteration must resolve to a boolean")

    @staticmethod
    def _coerce_result(result: Any) -> dict[str, Any]:
        return coerce_mapping_result(result)
