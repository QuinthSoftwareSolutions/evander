from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.semantic.qdrant import QdrantClient
from agent_sandbox.semantic.workflow_helpers import (
    collect_provider_payload,
    embed_text,
    optional_float,
    optional_limit,
    optional_mapping,
    optional_string,
    point_to_output,
    required_string,
    require_collection_exists,
    require_collection_schema,
    resolve_qdrant_api_key,
    resolve_settings,
    semantic_output_base,
)

from .base import QueryProviderBase

NODE_TYPE = "query:semantic:search"


class SemanticSearchQueryProvider(QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = collect_provider_payload(
            self.query_definition,
            (
                "collection_name",
                "query_text",
                "filter",
                "limit",
                "score_threshold",
                "directory_path",
            ),
        )
        if resolved_config:
            payload.update(dict(resolved_config))

        collection_name = required_string(
            payload.get("collection_name"), node_type=NODE_TYPE, field_name="collection_name"
        )
        query_text = required_string(
            payload.get("query_text"), node_type=NODE_TYPE, field_name="query_text"
        )
        filter_payload = optional_mapping(
            payload.get("filter"), node_type=NODE_TYPE, field_name="filter"
        )
        limit = optional_limit(payload.get("limit"), node_type=NODE_TYPE, field_name="limit", default=10)
        score_threshold = optional_float(
            payload.get("score_threshold"),
            node_type=NODE_TYPE,
            field_name="score_threshold",
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
            require_collection_schema(collection, settings)
            vector = embed_text(context, settings, query_text)
            points = client.query_points(
                collection.name,
                query_vector=vector,
                filter=filter_payload,
                limit=limit,
                score_threshold=score_threshold,
            )

        output_points = [point_to_output(point) for point in points]
        return {
            "success": True,
            **semantic_output_base(settings, qdrant_api_key=qdrant_api_key),
            "collection_name": collection.name,
            "query_text": query_text,
            "points": output_points,
            "count": len(output_points),
            "limit": limit,
        }

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("SemanticSearchQueryProvider has no runtime context")
        return self.runtime_context
