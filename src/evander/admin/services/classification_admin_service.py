from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from evander.classification.models import ClassificationError, ClassificationSettings, revision
from evander.classification.service import ClassificationService
from evander.models import AuditEvent


class ClassificationRevisionConflict(ValueError):
    pass


def merge_classification(base: dict, overlay: dict) -> dict:
    result = deepcopy(base)
    for key, value in overlay.items():
        if key == "profiles":
            result[key] = {**result.get(key, {}), **deepcopy(value)}
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_classification(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


class ClassificationAdminService:
    def __init__(self, operations, resolver, audit, service: ClassificationService):
        self.operations = operations
        self.resolver = resolver
        self.audit = audit
        self.service = service

    def _overlay(self, scope: str) -> dict:
        result = self.operations.read_directory_domain_config(scope, "providers")
        if not result.ok or result.data is None:
            raise ClassificationError("Could not read classification configuration")
        return dict(result.data.config or {})

    def snapshot(self, scope: str) -> dict[str, Any]:
        overlay = self._overlay(scope)
        settings = self.resolver.resolve_for_directory(scope).classification
        effective = settings.model_dump(mode="json")
        parent = self._parent_config(scope)
        return {
            "directory_path": scope,
            "revision": revision({"overlay": overlay, "effective": effective, "parent": parent}),
            "config": overlay.get("classification", {}),
            "effective_config": effective,
            "parent_config": parent,
            "providers": [
                {
                    "id": name,
                    "supported_question_types": sorted(provider.supported_question_types),
                    "credential_configured": bool(
                        self.service.secret_resolver(settings.providers[name].api_key_env)
                    )
                    if name in settings.providers
                    else False,
                }
                for name, provider in self.service.providers.items()
            ],
        }

    def _parent_config(self, scope: str) -> dict:
        resolution = self.operations.compute_directory_effective_config(scope, "providers")
        if not resolution.ok or resolution.data is None:
            raise ClassificationError("Could not resolve classification configuration")
        parent = ClassificationSettings().model_dump(mode="json")
        for layer in resolution.data.resolution.applied_layers:
            if layer.directory_path != scope:
                parent = merge_classification(parent, layer.overlay.get("classification", {}))
        return parent

    def save(self, scope: str, *, expected_revision: str, config_payload: Any, actor=None):
        if not isinstance(config_payload, dict):
            raise ClassificationError("config must be an object")
        with self.operations.admin_config_mutation_lock():
            if not expected_revision or expected_revision != self.snapshot(scope)["revision"]:
                raise ClassificationRevisionConflict(
                    "Classification configuration changed. Reload before saving."
                )
            parent = self._parent_config(scope)
            try:
                # Validate the overlay separately without materializing its omitted defaults.
                overlay_config = ClassificationSettings.model_validate(config_payload).model_dump(
                    mode="json", exclude_unset=True
                )
                candidate = ClassificationSettings.model_validate(
                    merge_classification(parent, overlay_config)
                )
            except ValidationError as exc:
                details = "; ".join(
                    f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                    for e in exc.errors(include_input=False, include_context=False)
                )
                raise ClassificationError(
                    f"Invalid classification configuration: {details}"
                ) from None
            if set(candidate.providers) - set(self.service.providers):
                raise ClassificationError(
                    "Configuration names an unsupported classification provider"
                )
            if candidate.default_provider not in candidate.providers:
                raise ClassificationError("Default classification provider must be configured")
            for profile in candidate.profiles.values():
                if profile.enabled and profile.provider not in candidate.providers:
                    raise ClassificationError(
                        "Profile references an unconfigured classification provider"
                    )
            overlay = self._overlay(scope)
            overlay["classification"] = overlay_config
            result = self.operations.write_directory_domain_config(scope, "providers", overlay)
            if not result.ok:
                raise ClassificationError("Could not save classification configuration")
            self._audit(scope, "save", actor)
            return self.snapshot(scope)

    def evaluate(self, scope: str, payload: Any, *, actor=None):
        settings = self.resolver.resolve_for_directory(scope).classification
        try:
            result = self.service.evaluate(settings, payload, attribution={"directory_path": scope})
        except Exception:
            self._audit(scope, "test_failed", actor)
            raise
        self._audit(scope, "test", actor)
        return result

    def _audit(self, scope, action, actor):
        self.audit.log(
            AuditEvent(
                event_type="classification_admin",
                directory_path=scope,
                actor_user_id=actor,
                decision="allow",
                result={"action": action},
            )
        )
