from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from agent_sandbox.semantic.openai_embeddings import OpenAIEmbeddingClient
from agent_sandbox.semantic.qdrant import (
    QdrantClient,
    QdrantCollectionInfo,
    QdrantPoint,
    QdrantPointId,
    collection_schema_error,
    normalize_point_id,
)
from agent_sandbox.semantic.settings import (
    SemanticProviderSettings,
    resolve_optional_secret,
    semantic_settings_from_runtime_context,
)

SEMANTIC_TEXT_PAYLOAD_KEY = "_semantic_text"


def collect_provider_payload(definition: Any, field_names: tuple[str, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name in field_names:
        if definition is not None and hasattr(definition, field_name):
            payload[field_name] = getattr(definition, field_name)
    return payload


def resolve_settings(
    context: Any,
    *,
    directory_path: str | None,
    provider_kind: Literal["effect", "query"],
) -> SemanticProviderSettings:
    return semantic_settings_from_runtime_context(
        context,
        directory_path=directory_path,
        provider_kind=provider_kind,
    )


def resolve_qdrant_api_key(context: Any, settings: SemanticProviderSettings) -> str | None:
    return resolve_optional_secret(context, settings.qdrant_api_key_env)


def embed_text(
    context: Any,
    settings: SemanticProviderSettings,
    text: str,
) -> list[float]:
    api_key = resolve_optional_secret(context, settings.openai_api_key_env)
    with OpenAIEmbeddingClient(
        base_url=settings.openai_api_base_url,
        api_key=api_key,
        model=settings.semantic_embedding_model,
        dimensions=settings.semantic_embedding_dimensions,
        timeout=settings.openai_relay_timeout_seconds,
    ) as client:
        return client.embed_text(text)


def require_collection_exists(
    client: QdrantClient,
    collection_name: str,
    *,
    node_type: str,
) -> QdrantCollectionInfo:
    collection = client.get_collection(collection_name)
    if not collection.exists:
        raise ValueError(f"{node_type} collection {collection.name!r} does not exist")
    return collection


def require_collection_schema(
    collection: QdrantCollectionInfo,
    settings: SemanticProviderSettings,
) -> None:
    schema_error = collection_schema_error(
        collection,
        expected_size=settings.semantic_embedding_dimensions,
        expected_distance=settings.semantic_vector_distance,
    )
    if schema_error is not None:
        raise ValueError(schema_error)


def point_to_output(point: QdrantPoint) -> dict[str, Any]:
    output: dict[str, Any] = {
        "id": point.id,
        "payload": dict(point.payload or {}),
    }
    if point.score is not None:
        output["score"] = point.score
    if point.version is not None:
        output["version"] = point.version
    return output


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def required_string(value: Any, *, node_type: str, field_name: str) -> str:
    text = optional_string(value)
    if not text:
        raise ValueError(f"{node_type} requires {field_name}")
    return text


def optional_bool(value: Any, *, node_type: str, field_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    raise ValueError(f"{node_type} {field_name} must be a boolean")


def optional_mapping(
    value: Any,
    *,
    node_type: str,
    field_name: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{node_type} {field_name} must be a JSON object")
    return dict(value)


def optional_limit(value: Any, *, node_type: str, field_name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{node_type} {field_name} must be a positive integer")
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{node_type} {field_name} must be a positive integer") from exc
    if limit <= 0:
        raise ValueError(f"{node_type} {field_name} must be a positive integer")
    return limit


def optional_float(value: Any, *, node_type: str, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{node_type} {field_name} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{node_type} {field_name} must be a number") from exc


def required_point_id(value: Any, *, node_type: str, field_name: str) -> QdrantPointId:
    try:
        return normalize_point_id(value)
    except ValueError as exc:
        raise ValueError(f"{node_type} {field_name}: {exc}") from exc


def required_point_ids(value: Any, *, node_type: str, field_name: str) -> list[QdrantPointId]:
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = [value]
    try:
        ids = [normalize_point_id(item) for item in raw_values]
    except ValueError as exc:
        raise ValueError(
            f"{node_type} {field_name}: each point ID must be an unsigned "
            f"64-bit integer or UUID string: {exc}"
        ) from exc
    if not ids:
        raise ValueError(f"{node_type} requires {field_name}")
    return ids


def semantic_output_base(
    settings: SemanticProviderSettings,
    *,
    qdrant_api_key: str | None,
) -> dict[str, Any]:
    return {
        "provider": settings.semantic_store_provider,
        "embedding_provider": settings.semantic_embedding_provider,
        "embedding_model": settings.semantic_embedding_model,
        "embedding_dimensions": settings.semantic_embedding_dimensions,
        "qdrant_base_url": settings.qdrant_base_url,
        "api_key_configured": bool(qdrant_api_key),
    }
