from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase, scoped_effect_directory_path
from .schemas import WebsiteSessionEnsureProfileEffectDefinition


class WebsiteSessionEnsureProfileEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self.effect_definition is None or self.runtime_context is None:
            raise RuntimeError("WebsiteSessionEnsureProfileEffectProvider has no runtime context")
        payload = {name: getattr(self.effect_definition, name)
                   for name in ("template_id", "browser_origin", "login_origin", "directory_path")}
        if resolved_config:
            payload.update(resolved_config)
        if any(isinstance(value, str) and "${{" in value for value in payload.values()):
            raise ValueError("effect:website-session:ensure-profile has unresolved bindings")
        definition = WebsiteSessionEnsureProfileEffectDefinition.model_validate({
            "id": self.effect_definition.id,
            "display_name": self.effect_definition.display_name,
            "type": "effect:website-session:ensure-profile",
            **payload,
        })
        scope = scoped_effect_directory_path(self.runtime_context, definition.directory_path)
        ensure = getattr(self.runtime_context.admin_service, "ensure_website_session_profile", None)
        if not callable(ensure):
            raise RuntimeError("effect:website-session:ensure-profile requires a website profile writer")
        return ensure(directory_path=scope, template_id=definition.template_id,
                      browser_origin=definition.browser_origin, login_origin=definition.login_origin)
