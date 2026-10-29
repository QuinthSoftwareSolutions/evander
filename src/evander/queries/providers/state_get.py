from __future__ import annotations

from pathlib import Path
from typing import Any

from evander.events.models import QueryDefinitionDiscriminated, WorkflowNodeEnvelope
from evander.events.provider_models import StateGetQueryDefinition
from evander.workflow.state_store import WorkflowStateStore

from .base import QueryProviderBase


class StateGetQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(
        cls, query_definition: QueryDefinitionDiscriminated
    ) -> QueryDefinitionDiscriminated:
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return StateGetQueryDefinition.model_validate(
                {
                    "id": query_definition.id,
                    "display_name": query_definition.display_name,
                    "type": query_definition.type,
                    "outputs": query_definition.outputs,
                    **dict(query_definition.config),
                }
            )
        return super().validate_definition(query_definition)

    def execute(self, *, resolved_config: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        namespace = self._require_text(payload.get("namespace"), field_name="namespace")
        key = self._require_text(payload.get("key"), field_name="key")
        directory_path, graph_id = self._runtime_scope()
        store = self._state_store()
        result = store.get(directory_path, graph_id, namespace, key)
        record = result.record
        if record is None:
            value = None
            version = 0
            created_at = None
            updated_at = None
            found = False
        else:
            serialized = record.model_dump(mode="json")
            value = serialized.get("value")
            version = serialized.get("version")
            created_at = serialized.get("created_at")
            updated_at = serialized.get("updated_at")
            found = True

        return {
            "success": True,
            "found": found,
            "directory_path": directory_path,
            "graph_id": graph_id,
            "namespace": namespace,
            "key": key,
            "value": value,
            "version": version,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        if query is None:
            return payload
        for field_name in ("namespace", "key"):
            if hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _state_store(self) -> WorkflowStateStore:
        context = self._context()
        runtime_dir = Path(context.config_path("runtime_dir"))
        return WorkflowStateStore(runtime_dir)

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("StateGetQueryProvider has no runtime context")
        self._runtime_scope()
        return self.runtime_context

    def _runtime_scope(self) -> tuple[str, str]:
        if self.runtime_context is None:
            raise RuntimeError("StateGetQueryProvider has no runtime context")
        metadata = dict(self.runtime_context.metadata or {})
        return (
            self._require_text(metadata.get("directory_path"), field_name="directory_path"),
            self._require_text(metadata.get("graph_id"), field_name="graph_id"),
        )

    @staticmethod
    def _require_text(value: Any, *, field_name: str) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            raise RuntimeError(
                "query:state:get requires graph_id and directory_path runtime metadata"
            )
        return text
