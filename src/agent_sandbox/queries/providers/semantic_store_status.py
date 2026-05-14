from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.semantic.qdrant import QdrantClient, collection_schema_error
from agent_sandbox.semantic.settings import (
    resolve_optional_secret,
    semantic_settings_from_runtime_context,
)

from .base import QueryProviderBase


class SemanticStoreStatusQueryProvider(QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))
        collection_name = self._optional_string(payload.get("collection_name"))
        directory_path = self._optional_string(payload.get("directory_path"))

        context = self._context()
        settings = semantic_settings_from_runtime_context(
            context,
            directory_path=directory_path,
            provider_kind="query",
        )
        api_key = resolve_optional_secret(context, settings.qdrant_api_key_env)
        output: dict[str, Any] = {
            "success": False,
            "provider": settings.semantic_store_provider,
            "reachable": False,
            "qdrant_base_url": settings.qdrant_base_url,
            "api_key_configured": bool(api_key),
            "embedding_provider": settings.semantic_embedding_provider,
            "embedding_model": settings.semantic_embedding_model,
            "embedding_dimensions": settings.semantic_embedding_dimensions,
            "vector_distance": settings.semantic_vector_distance,
            "collection_name": collection_name,
            "collection_exists": None,
            "collection_status": None,
            "collection_vector_size": None,
            "collection_distance": None,
            "schema_match": None,
            "schema_error": None,
        }
        with QdrantClient(settings.qdrant_base_url, api_key=api_key) as client:
            health = client.health()
            output.update(
                {
                    "reachable": health.ok,
                    "health_status_code": health.status_code,
                    "health_detail": health.detail,
                    "health_error": health.error,
                    "success": health.ok,
                }
            )
            if not health.ok or collection_name is None:
                return output
            collection = client.get_collection(collection_name)
        output.update(
            {
                "collection_exists": collection.exists,
                "collection_status": collection.status,
                "collection_vector_size": collection.vector_size,
                "collection_distance": collection.distance,
            }
        )
        if not collection.exists:
            output["schema_match"] = False
            return output
        schema_error = collection_schema_error(
            collection,
            expected_size=settings.semantic_embedding_dimensions,
            expected_distance=settings.semantic_vector_distance,
        )
        if schema_error is None:
            output["schema_match"] = True
        else:
            output["schema_match"] = False
            output["schema_error"] = schema_error
        return output

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in ("collection_name", "directory_path"):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("SemanticStoreStatusQueryProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
