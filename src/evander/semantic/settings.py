from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


SemanticVectorDistance = Literal["Cosine", "Dot", "Euclid", "Manhattan"]


def normalize_vector_distance(value: object) -> SemanticVectorDistance:
    text = str(value or "").strip()
    aliases = {
        "cosine": "Cosine",
        "dot": "Dot",
        "euclid": "Euclid",
        "euclidean": "Euclid",
        "manhattan": "Manhattan",
    }
    normalized = aliases.get(text.casefold())
    if normalized is None:
        raise ValueError("semantic_vector_distance must be one of: Cosine, Dot, Euclid, Manhattan")
    return normalized  # type: ignore[return-value]


class SemanticProviderSettings(BaseModel):
    semantic_store_provider: Literal["qdrant"] = "qdrant"
    qdrant_base_url: str = "http://127.0.0.1:6333"
    qdrant_api_key_env: str | None = "QDRANT_API_KEY"
    semantic_embedding_provider: Literal["openai"] = "openai"
    semantic_embedding_model: str = Field(default="text-embedding-3-small", min_length=1)
    semantic_embedding_dimensions: int = Field(default=1536, ge=1)
    semantic_vector_distance: SemanticVectorDistance = "Cosine"
    openai_api_base_url: str = "https://api.openai.com/v1"
    openai_api_key_env: str = Field(default="OPENAI_API_KEY", min_length=1)
    openai_relay_timeout_seconds: float = Field(default=300.0, gt=0)

    @field_validator("semantic_vector_distance", mode="before")
    @classmethod
    def normalize_distance(cls, value: object) -> SemanticVectorDistance:
        return normalize_vector_distance(value)

    @field_validator("qdrant_api_key_env", mode="before")
    @classmethod
    def normalize_optional_env_name(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def semantic_settings_from_mapping(config: Mapping[str, Any]) -> SemanticProviderSettings:
    return SemanticProviderSettings.model_validate(dict(config))


def semantic_settings_from_runtime_context(
    context: Any,
    *,
    directory_path: str | None = None,
    provider_kind: Literal["effect", "query"] = "effect",
) -> SemanticProviderSettings:
    config: Mapping[str, Any] = getattr(context, "provider_config", {}) or {}
    normalized_directory = _normalize_optional_string(directory_path)
    resolver = getattr(context, "provider_config_resolver", None)
    if normalized_directory and resolver is not None:
        method_name = f"resolve_{provider_kind}_provider_config"
        resolve_provider_config = getattr(resolver, method_name, None)
        if callable(resolve_provider_config):
            config = _model_dump(resolve_provider_config(normalized_directory))
        else:
            resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
            if callable(resolve_for_directory):
                config = _model_dump(resolve_for_directory(normalized_directory))
    return semantic_settings_from_mapping(config)


def resolve_optional_secret(context: Any, env_name: str | None) -> str | None:
    if not env_name:
        return None
    resolve_secret = getattr(context, "resolve_secret", None)
    if callable(resolve_secret):
        return resolve_secret(env_name)
    env = getattr(context, "env", {}) or {}
    if isinstance(env, Mapping):
        return env.get(env_name)
    return None


def _model_dump(value: Any) -> Mapping[str, Any]:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _normalize_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
