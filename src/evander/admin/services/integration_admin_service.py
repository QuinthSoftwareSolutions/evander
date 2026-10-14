"""Read-only operator access to opt-in integration contracts and probes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from evander.addons import AddonContext, LoadedAddonCatalog, addon_runtime_dir
from evander.integration_registry import IntegrationRegistry


class IntegrationAdminService:
    def __init__(
        self,
        *,
        catalog: LoadedAddonCatalog,
        project_dir: Path,
        runtime_dir: Path,
        config: Any,
        services: Any,
        resolve_directory: Callable[[str | None], str],
    ) -> None:
        self.registry = IntegrationRegistry(catalog)
        self._project_dir = project_dir
        self._runtime_dir = runtime_dir
        self._config = config
        self._services = services
        self._resolve_directory = resolve_directory

    def inventory(self) -> dict[str, Any]:
        return {"contract_version": 1, "integrations": self.registry.describe()}

    def readiness(self, addon_id: str, directory_path: str = "root") -> dict[str, Any]:
        return self.registry.readiness(addon_id, self.context(addon_id, directory_path))

    def context(self, addon_id: str, directory_path: str = "root") -> AddonContext:
        # Both directory aliases and malformed paths go through the platform resolver.
        directory_path = self._resolve_directory(directory_path)
        return AddonContext(
            addon_id=addon_id,
            runtime_dir=self._runtime_dir,
            addon_runtime_dir=addon_runtime_dir(self._runtime_dir, addon_id),
            project_dir=self._project_dir,
            config=self._config,
            service=self._services,
            directory_path=directory_path,
        )
