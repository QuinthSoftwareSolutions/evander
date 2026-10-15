from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import QueryProviderBase, scoped_query_directory_path
from .schemas import WebsiteSessionResolveProfileQueryDefinition


class WebsiteSessionResolveProfileQueryProvider(QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self.query_definition is None or self.runtime_context is None:
            raise RuntimeError("WebsiteSessionResolveProfileQueryProvider has no runtime context")
        payload = {
            field: getattr(self.query_definition, field)
            for field in ("browser_origin", "login_origin", "directory_path")
        }
        if resolved_config:
            payload.update(resolved_config)
        definition = WebsiteSessionResolveProfileQueryDefinition.model_validate({
            "id": self.query_definition.id,
            "display_name": self.query_definition.display_name,
            "type": "query:website-session:resolve-profile",
            **payload,
        })
        if any(isinstance(value, str) and "${{" in value for value in payload.values()):
            raise ValueError("query:website-session:resolve-profile has unresolved bindings")
        directory_path = scoped_query_directory_path(
            self.runtime_context, definition.directory_path
        )
        reader = getattr(self.runtime_context.admin_service, "resolve_website_session_profile", None)
        if not callable(reader):
            raise RuntimeError("query:website-session:resolve-profile requires a website profile reader")
        return reader(directory_path=directory_path, browser_origin=definition.browser_origin,
                      login_origin=definition.login_origin)
