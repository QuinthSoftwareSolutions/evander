from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.semantic.qdrant import QdrantClient
from agent_sandbox.semantic.workflow_helpers import (
    collect_provider_payload,
    optional_string,
    point_to_output,
    required_point_ids,
    required_string,
    require_collection_exists,
    resolve_qdrant_api_key,
    resolve_settings,
    semantic_output_base,
)

from .base import QueryProviderBase

NODE_TYPE = "query:semantic:get"


class SemanticGetQueryProvider(QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = collect_provider_payload(
            self.query_definition,
            ("collection_name", "point_ids", "directory_path"),
        )
        if resolved_config:
            payload.update(dict(resolved_config))

        collection_name = required_string(
            payload.get("collection_name"), node_type=NODE_TYPE, field_name="collection_name"
        )
        point_ids = required_point_ids(
            payload.get("point_ids"), node_type=NODE_TYPE, field_name="point_ids"
        )
        directory_path = optional_string(payload.get("directory_path"))

        context = self._context()
        settings = resolve_settings(
            context,
            directory_path=directory_path,
            provider_kind="query",
        )
        qdrant_api_key = resolve_qdrant_api_key(context, settings)
        with QdrantClient(settings.qdrant_base_url, api_key=qdrant_api_key) as client:
            collection = require_collection_exists(
                client, collection_name, node_type=NODE_TYPE
            )
            points = client.retrieve_points(collection.name, point_ids=point_ids)

        output_points = [point_to_output(point) for point in points]
        found_ids = {point["id"] for point in output_points}
        return {
            "success": True,
            **semantic_output_base(settings, qdrant_api_key=qdrant_api_key),
            "collection_name": collection.name,
            "point_ids": point_ids,
            "points": output_points,
            "count": len(output_points),
            "requested_count": len(point_ids),
            "missing_ids": [point_id for point_id in point_ids if point_id not in found_ids],
        }

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("SemanticGetQueryProvider has no runtime context")
        return self.runtime_context
