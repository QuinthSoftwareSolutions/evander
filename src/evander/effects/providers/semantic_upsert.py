from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.semantic.qdrant import QdrantClient
from evander.semantic.workflow_helpers import (
    SEMANTIC_TEXT_PAYLOAD_KEY,
    collect_provider_payload,
    embed_text,
    optional_bool,
    optional_mapping,
    optional_string,
    required_point_id,
    required_string,
    resolve_qdrant_api_key,
    resolve_settings,
    semantic_output_base,
)

from .base import EffectProviderBase

NODE_TYPE = "effect:semantic:upsert"


class SemanticUpsertEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = collect_provider_payload(
            self.effect_definition,
            ("collection_name", "point_id", "text", "payload", "directory_path", "wait"),
        )
        if resolved_config:
            payload.update(dict(resolved_config))

        collection_name = required_string(
            payload.get("collection_name"), node_type=NODE_TYPE, field_name="collection_name"
        )
        point_id = required_point_id(
            payload.get("point_id"), node_type=NODE_TYPE, field_name="point_id"
        )
        text = required_string(payload.get("text"), node_type=NODE_TYPE, field_name="text")
        caller_payload = (
            optional_mapping(payload.get("payload"), node_type=NODE_TYPE, field_name="payload")
            or {}
        )
        if SEMANTIC_TEXT_PAYLOAD_KEY in caller_payload:
            raise ValueError(
                f"{NODE_TYPE} payload may not contain reserved key {SEMANTIC_TEXT_PAYLOAD_KEY!r}"
            )
        wait = optional_bool(payload.get("wait"), node_type=NODE_TYPE, field_name="wait")
        directory_path = optional_string(payload.get("directory_path"))

        context = self._context()
        settings = resolve_settings(
            context,
            directory_path=directory_path,
            provider_kind="effect",
        )
        qdrant_api_key = resolve_qdrant_api_key(context, settings)
        stored_payload = {
            **caller_payload,
            SEMANTIC_TEXT_PAYLOAD_KEY: text,
        }

        with QdrantClient(settings.qdrant_base_url, api_key=qdrant_api_key) as client:
            collection_result = client.ensure_collection(
                collection_name,
                vector_size=settings.semantic_embedding_dimensions,
                distance=settings.semantic_vector_distance,
            )
            vector = embed_text(context, settings, text)
            operation = client.upsert_point(
                collection_result.collection.name,
                point_id=point_id,
                vector=vector,
                payload=stored_payload,
                wait=wait,
            )

        return {
            "success": True,
            **semantic_output_base(settings, qdrant_api_key=qdrant_api_key),
            "collection_name": collection_result.collection.name,
            "point_id": point_id,
            "payload": stored_payload,
            "text": text,
            "created_collection": collection_result.created,
            "operation_status": operation.status,
            "operation_id": operation.operation_id,
            "wait": bool(wait) if wait is not None else False,
        }

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("SemanticUpsertEffectProvider has no runtime context")
        return self.runtime_context
