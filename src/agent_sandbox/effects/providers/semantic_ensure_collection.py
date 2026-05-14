from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.semantic.qdrant import QdrantClient
from agent_sandbox.semantic.settings import (
    resolve_optional_secret,
    semantic_settings_from_runtime_context,
)

from .base import EffectProviderBase


class SemanticEnsureCollectionEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))
        collection_name = self._required_string(
            payload.get("collection_name"), field_name="collection_name"
        )
        directory_path = self._optional_string(payload.get("directory_path"))

        context = self._context()
        settings = semantic_settings_from_runtime_context(
            context,
            directory_path=directory_path,
            provider_kind="effect",
        )
        api_key = resolve_optional_secret(context, settings.qdrant_api_key_env)
        with QdrantClient(settings.qdrant_base_url, api_key=api_key) as client:
            result = client.ensure_collection(
                collection_name,
                vector_size=settings.semantic_embedding_dimensions,
                distance=settings.semantic_vector_distance,
            )
        collection = result.collection
        return {
            "success": True,
            "provider": settings.semantic_store_provider,
            "collection_name": collection.name,
            "action": "created" if result.created else "exists",
            "created": result.created,
            "schema_match": True,
            "status": collection.status,
            "vector_size": collection.vector_size,
            "distance": collection.distance,
            "embedding_provider": settings.semantic_embedding_provider,
            "embedding_model": settings.semantic_embedding_model,
            "embedding_dimensions": settings.semantic_embedding_dimensions,
            "qdrant_base_url": settings.qdrant_base_url,
            "api_key_configured": bool(api_key),
        }

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in ("collection_name", "directory_path"):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("SemanticEnsureCollectionEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _required_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._optional_string(value)
        if not text:
            raise ValueError(f"effect:semantic:ensure-collection requires {field_name}")
        return text
