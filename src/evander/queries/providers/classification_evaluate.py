from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.classification.models import PreparedEvaluation
from evander.classification.runtime import service_for_runtime, settings_for_query
from evander.queries.providers.base import QueryProviderBase


class ClassificationEvaluateQueryProvider(QueryProviderBase):
    def prepare(self, *, resolved_config: Mapping[str, Any]) -> dict[str, Any]:
        context = self.runtime_context
        service = service_for_runtime(context)
        prepared = service.prepare(settings_for_query(context), dict(resolved_config))
        return {"_classification_snapshot": prepared.model_dump(mode="json")}

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        context = self.runtime_context
        payload = dict(resolved_config or {})
        if "_classification_snapshot" not in payload:
            # Direct provider use follows the same validation as orchestrated calls.
            if not payload and self.query_definition is not None:
                payload = self.query_definition.model_dump(
                    exclude={"id", "display_name", "type", "outputs", "config"}, exclude_none=True
                )
            payload = self.prepare(resolved_config=payload)
        prepared = PreparedEvaluation.model_validate(payload["_classification_snapshot"])
        return service_for_runtime(context).execute(
            settings_for_query(context),
            prepared,
            attribution=context.metadata,
        )
