from __future__ import annotations

import os
from typing import Any

from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.schemas import (
    SemanticDeleteEffectDefinition,
    SemanticUpsertEffectDefinition,
)
from evander.effects.providers.semantic_delete import SemanticDeleteEffectProvider
from evander.effects.providers.semantic_upsert import SemanticUpsertEffectProvider
from evander.models import SessionManifest
from evander.provider_runtime.adapters import (
    effect_provider_config_blob,
    query_provider_config_blob,
)
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.schemas import (
    SemanticGetQueryDefinition,
    SemanticSearchQueryDefinition,
)
from evander.queries.providers.semantic_get import SemanticGetQueryProvider
from evander.queries.providers.semantic_search import SemanticSearchQueryProvider
from evander.semantic.qdrant import QdrantPointId


class GatewaySemanticAdapter:
    """Session-scoped adapter for exposing workflow semantic providers as MCP tools."""

    def __init__(self, runtime_context: Any) -> None:
        self._runtime_context = runtime_context

    def upsert_text(
        self,
        *,
        session: SessionManifest,
        collection_name: str,
        point_id: QdrantPointId,
        text: str,
        payload: dict[str, Any] | None = None,
        wait: bool | None = None,
    ) -> dict[str, Any]:
        provider = SemanticUpsertEffectProvider()
        provider.initialize(
            SemanticUpsertEffectDefinition(
                id="mcp-semantic-upsert-text",
                display_name="MCP semantic upsert text",
                type="effect:semantic:upsert",
                collection_name=collection_name,
                point_id=point_id,
                text=text,
                payload=payload,
                wait=wait,
            ),
            self._effect_context_for_session(session),
        )
        return provider.execute()

    def search(
        self,
        *,
        session: SessionManifest,
        collection_name: str,
        query_text: str,
        filter: dict[str, Any] | None = None,
        limit: int | None = None,
        score_threshold: float | None = None,
    ) -> dict[str, Any]:
        provider = SemanticSearchQueryProvider()
        provider.initialize(
            SemanticSearchQueryDefinition(
                id="mcp-semantic-search",
                display_name="MCP semantic search",
                type="query:semantic:search",
                collection_name=collection_name,
                query_text=query_text,
                filter=filter,
                limit=limit,
                score_threshold=score_threshold,
            ),
            self._query_context_for_session(session),
        )
        return provider.execute()

    def get_points(
        self,
        *,
        session: SessionManifest,
        collection_name: str,
        point_ids: QdrantPointId | list[QdrantPointId],
    ) -> dict[str, Any]:
        provider = SemanticGetQueryProvider()
        provider.initialize(
            SemanticGetQueryDefinition(
                id="mcp-semantic-get-points",
                display_name="MCP semantic get points",
                type="query:semantic:get",
                collection_name=collection_name,
                point_ids=point_ids,
            ),
            self._query_context_for_session(session),
        )
        return provider.execute()

    def delete_points(
        self,
        *,
        session: SessionManifest,
        collection_name: str,
        point_ids: QdrantPointId | list[QdrantPointId],
        wait: bool | None = None,
    ) -> dict[str, Any]:
        provider = SemanticDeleteEffectProvider()
        provider.initialize(
            SemanticDeleteEffectDefinition(
                id="mcp-semantic-delete-points",
                display_name="MCP semantic delete points",
                type="effect:semantic:delete",
                collection_name=collection_name,
                point_ids=point_ids,
                wait=wait,
            ),
            self._effect_context_for_session(session),
        )
        return provider.execute()

    def _effect_context_for_session(self, session: SessionManifest) -> EffectRuntimeContext:
        config = self._runtime_context.provider_config.resolve_effect_provider_config(
            session.directory_path
        )
        return EffectRuntimeContext(
            provider_config=effect_provider_config_blob(config),
            provider_config_resolver=self._runtime_context.provider_config,
            env=dict(os.environ),
        )

    def _query_context_for_session(self, session: SessionManifest) -> QueryRuntimeContext:
        config = self._runtime_context.provider_config.resolve_query_provider_config(
            session.directory_path
        )
        return QueryRuntimeContext(
            provider_config=query_provider_config_blob(config),
            provider_config_resolver=self._runtime_context.provider_config,
            env=dict(os.environ),
        )
