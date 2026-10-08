from __future__ import annotations

from typing import Any

from .base import QueryProviderBase


class PullRequestQueryProviderBase(QueryProviderBase):
    """Shared input handling for queries targeting a single pull request."""

    NODE_TYPE: str

    def _context_directory_path(self) -> str:
        metadata = self._context().metadata
        return self._normalize_optional_string(metadata.get("directory_path")) or "root"

    def _resolve_directory_config(self, directory_path: str):
        resolver = getattr(self._context(), "provider_config_resolver", None)
        if resolver is None:
            raise RuntimeError(f"{self.NODE_TYPE} provider_config_resolver is not available")
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if not callable(resolve_for_directory):
            raise RuntimeError(
                f"{self.NODE_TYPE} provider_config_resolver cannot resolve directories"
            )
        return resolve_for_directory(directory_path)

    @classmethod
    def _require_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if not text:
            raise ValueError(f"{cls.NODE_TYPE} requires {field_name}")
        return text

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        normalized = provider.strip()
        if normalized in {"ado", "azure-devops", "azure_devops"}:
            return "azure_devops"
        return normalized

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
