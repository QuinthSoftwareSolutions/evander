from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

from evander_azure_devops_addon.builds import AdoBuildClient
from evander.effects.providers.base import EffectProviderBase, EffectProviderExecutionError, scoped_effect_directory_path


class AdoBuildWaitEffectProvider(EffectProviderBase):
    def initialize(self, effect_definition, runtime_context):
        from evander.optional_integrations import require_azure_devops_module
        resolver = runtime_context.provider_config_resolver
        config = getattr(resolver, "config", None) or runtime_context.resolved_config
        require_azure_devops_module(config=config)
        super().initialize(effect_definition, runtime_context)

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self.effect_definition.model_dump()
        payload.update(resolved_config or {})
        directory = scoped_effect_directory_path(
            self.runtime_context, payload.get("directory_path")
        )
        timeout = float(payload.get("timeout_seconds", 3600))
        interval = float(payload.get("poll_interval_seconds", 10))
        if (
            not math.isfinite(timeout)
            or not math.isfinite(interval)
            or timeout <= 0
            or interval <= 0
        ):
            raise ValueError("Build wait timeout and poll interval must be positive finite numbers")
        deadline = time.monotonic() + timeout
        with AdoBuildClient(
            self.runtime_context, directory_path=directory, project=payload.get("project")
        ) as client:
            while True:
                build = client.get_build(payload["run_id"])
                if build["completed"]:
                    if build["success"]:
                        return build
                    raise EffectProviderExecutionError(
                        f"Build {build['build_number']} completed with result {build['result'] or 'unknown'}.",
                        outputs=build,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EffectProviderExecutionError(
                        "Timed out waiting for the selected build.", outputs=build
                    )
                time.sleep(min(interval, remaining))
