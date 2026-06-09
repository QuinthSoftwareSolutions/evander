from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_sandbox.artifact_spaces import (
    ArtifactSpacePermissionError,
    ArtifactSpaceStore,
    normalize_space_path,
)
from agent_sandbox.events.models import WorkflowNodeEnvelope
from agent_sandbox.events.provider_models import (
    ArtifactSpaceLatestQueryDefinition,
    ArtifactSpaceListQueryDefinition,
)

from .base import QueryProviderBase


class ArtifactSpaceListQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return ArtifactSpaceListQueryDefinition.model_validate(
                {
                    "id": query_definition.id,
                    "display_name": query_definition.display_name,
                    "type": query_definition.type,
                    "outputs": query_definition.outputs,
                    **dict(query_definition.config),
                }
            )
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload("path", "recursive", "subject", "include_versions")
        if resolved_config:
            payload.update(dict(resolved_config))

        state = ArtifactSpaceStore(_runtime_dir(self.runtime_context)).read()
        subject = _optional_text(payload.get("subject"))
        root_path = normalize_space_path(payload.get("path") or "/")
        recursive = _optional_bool(payload.get("recursive"), default=True)
        include_versions = _optional_bool(payload.get("include_versions"), default=False)

        spaces = []
        for space in state.spaces.values():
            if not _space_matches(space.path, root_path=root_path, recursive=recursive):
                continue
            if not space.grants.allows(subject, "read"):
                continue
            spaces.append(space.model_dump(mode="json"))

        versions = []
        if include_versions:
            readable_paths = {space["path"] for space in spaces}
            versions = [
                version.model_dump(mode="json")
                for version in state.versions.values()
                if version.space_path in readable_paths
            ]
            versions.sort(key=lambda item: (item["space_path"], item["artifact_id"], item["version"]))

        spaces.sort(key=lambda item: item["path"])
        return {
            "success": True,
            "revision": state.revision,
            "spaces": spaces,
            "versions": versions,
            "count": len(spaces),
        }

    def _payload(self, *field_names: str) -> dict[str, Any]:
        query = self.query_definition
        return {
            field_name: getattr(query, field_name)
            for field_name in field_names
            if query is not None and hasattr(query, field_name)
        }


class ArtifactSpaceLatestQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return ArtifactSpaceLatestQueryDefinition.model_validate(
                {
                    "id": query_definition.id,
                    "display_name": query_definition.display_name,
                    "type": query_definition.type,
                    "outputs": query_definition.outputs,
                    **dict(query_definition.config),
                }
            )
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload("space_path", "artifact_id", "subject")
        if resolved_config:
            payload.update(dict(resolved_config))

        state = ArtifactSpaceStore(_runtime_dir(self.runtime_context)).read()
        space_path = normalize_space_path(payload.get("space_path"))
        space = state.spaces.get(space_path)
        if space is None:
            raise ValueError(f"artifact space does not exist: {space_path}")
        subject = _optional_text(payload.get("subject"))
        if not space.grants.allows(subject, "read"):
            raise ArtifactSpacePermissionError(
                f"subject {subject or '<anonymous>'} cannot read artifact space {space_path}"
            )
        latest = [
            version.model_dump(mode="json")
            for version in state.latest_versions_for_space(
                space_path,
                artifact_id=_optional_text(payload.get("artifact_id")),
            )
        ]
        return {
            "success": True,
            "revision": state.revision,
            "versions": latest,
            "count": len(latest),
            "artifact_key": latest[0]["artifact_key"] if len(latest) == 1 else None,
        }

    def _payload(self, *field_names: str) -> dict[str, Any]:
        query = self.query_definition
        return {
            field_name: getattr(query, field_name)
            for field_name in field_names
            if query is not None and hasattr(query, field_name)
        }


def _runtime_dir(context: Any) -> Path:
    resolved = getattr(context, "resolved_config", None)
    runtime_dir = getattr(resolved, "runtime_dir", None)
    if runtime_dir is None:
        raise RuntimeError("artifact-space query requires resolved_config.runtime_dir")
    return Path(runtime_dir)


def _space_matches(path: str, *, root_path: str, recursive: bool) -> bool:
    if path == root_path:
        return True
    if not recursive:
        return False
    prefix = root_path.rstrip("/") + "/"
    return path.startswith(prefix)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean value is required")

