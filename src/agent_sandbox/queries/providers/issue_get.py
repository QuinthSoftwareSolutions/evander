from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.events.models import IssueGetQueryDefinition, QueryDefinitionDiscriminated, WorkflowNodeEnvelope

from .base import QueryProviderBase
from .issue_get_schema import IssueGetQueryConfigSchema


class IssueGetQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition: QueryDefinitionDiscriminated) -> QueryDefinitionDiscriminated:
        if isinstance(query_definition, WorkflowNodeEnvelope):
            config = IssueGetQueryConfigSchema.model_validate(query_definition.config).model_dump()
            return IssueGetQueryDefinition.model_validate(
                {
                    "id": query_definition.id,
                    "display_name": query_definition.display_name,
                    "type": query_definition.type,
                    "outputs": query_definition.outputs,
                    **config,
                }
            )
        return super().validate_definition(query_definition)

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))
        issue_id = self._normalize_required_string(payload.get("issue_id"), field_name="issue_id")

        context = self._context()
        query_fn = getattr(context.issue_reader, "get_issue", None)
        if not callable(query_fn):
            raise RuntimeError("AdminService.get_issue is not available")
        result = query_fn(issue_id=issue_id)
        return self._coerce_result(result)

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        if query is not None and hasattr(query, "issue_id"):
            payload["issue_id"] = getattr(query, "issue_id")
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("IssueGetQueryProvider has no runtime context")
        return self.runtime_context

    @staticmethod
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

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _normalize_required_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if not text:
            raise ValueError(f"query:issue:get requires {field_name}")
        return text
