from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evander.artifact_spaces import (
    ArtifactSpaceConflictError,
    ArtifactSpacePermissionError,
    ArtifactSpaceStore,
    artifact_space_state_payload,
    normalize_space_path,
)
from evander.events.models import WorkflowNodeEnvelope
from evander.events.provider_models import (
    ArtifactSpaceCreateEffectDefinition,
    ArtifactSpacePublishVersionEffectDefinition,
)
from evander.published_artifacts import PublishedArtifactStore

from .base import EffectProviderBase


class ArtifactSpaceCreateEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return ArtifactSpaceCreateEffectDefinition.model_validate(
                {
                    "id": effect_definition.id,
                    "display_name": effect_definition.display_name,
                    "type": effect_definition.type,
                    "outputs": effect_definition.outputs,
                    **dict(effect_definition.config),
                }
            )
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(
            "path",
            "display_name",
            "metadata",
            "grants",
            "subject",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        store = ArtifactSpaceStore(_runtime_dir(self.runtime_context))
        try:
            state = store.ensure_space(
                path=_required_text(payload.get("path"), "path"),
                display_name=_optional_text(payload.get("display_name")),
                metadata=_optional_mapping(payload.get("metadata"), "metadata"),
                grants=_optional_mapping(payload.get("grants"), "grants"),
                subject=_optional_text(payload.get("subject")),
                expected_revision=_optional_int(payload.get("expected_revision")),
            )
        except ArtifactSpaceConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        except ArtifactSpacePermissionError as exc:
            return {"success": False, "error": str(exc), "permission_denied": True}
        space = state.spaces[normalize_space_path(payload.get("path"))]
        return {
            "success": True,
            "revision": state.revision,
            "space": space.model_dump(mode="json"),
            "state": artifact_space_state_payload(state),
        }

    def _payload(self, *field_names: str) -> dict[str, Any]:
        effect = self.effect_definition
        return {
            field_name: getattr(effect, field_name)
            for field_name in field_names
            if effect is not None and hasattr(effect, field_name)
        }


class ArtifactSpacePublishVersionEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return ArtifactSpacePublishVersionEffectDefinition.model_validate(
                {
                    "id": effect_definition.id,
                    "display_name": effect_definition.display_name,
                    "type": effect_definition.type,
                    "outputs": effect_definition.outputs,
                    **dict(effect_definition.config),
                }
            )
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(
            "space_path",
            "artifact_id",
            "artifact_key",
            "subject",
            "metadata",
            "expected_revision",
            "require_existing_artifact",
        )
        if resolved_config:
            payload.update(dict(resolved_config))

        runtime_dir = _runtime_dir(self.runtime_context)
        artifact_key = _required_text(payload.get("artifact_key"), "artifact_key")
        require_existing = _optional_bool(payload.get("require_existing_artifact"), default=True)
        if require_existing and not PublishedArtifactStore(runtime_dir).exists(artifact_key):
            return {
                "success": False,
                "error": f"published artifact key does not exist: {artifact_key}",
            }

        store = ArtifactSpaceStore(runtime_dir)
        try:
            state = store.publish_version(
                space_path=_required_text(payload.get("space_path"), "space_path"),
                artifact_id=_required_text(payload.get("artifact_id"), "artifact_id"),
                artifact_key=artifact_key,
                created_by_subject=_optional_text(payload.get("subject")),
                metadata=_optional_mapping(payload.get("metadata"), "metadata"),
                expected_revision=_optional_int(payload.get("expected_revision")),
            )
        except ArtifactSpaceConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        except ArtifactSpacePermissionError as exc:
            return {"success": False, "error": str(exc), "permission_denied": True}
        versions = state.versions_for_space(
            _required_text(payload.get("space_path"), "space_path"),
            artifact_id=_required_text(payload.get("artifact_id"), "artifact_id"),
        )
        version = versions[-1]
        return {
            "success": True,
            "revision": state.revision,
            "version": version.model_dump(mode="json"),
            "state": artifact_space_state_payload(state),
        }

    def _payload(self, *field_names: str) -> dict[str, Any]:
        effect = self.effect_definition
        return {
            field_name: getattr(effect, field_name)
            for field_name in field_names
            if effect is not None and hasattr(effect, field_name)
        }


def _runtime_dir(context: Any) -> Path:
    resolved = getattr(context, "resolved_config", None)
    runtime_dir = getattr(resolved, "runtime_dir", None)
    if runtime_dir is None:
        raise RuntimeError("artifact-space provider requires resolved_config.runtime_dir")
    return Path(runtime_dir)


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("expected_revision must be an integer")
    return int(value)


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


def _optional_mapping(value: object, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)
