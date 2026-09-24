from __future__ import annotations

from pathlib import Path
from typing import Any

from evander.classification.models import ClassificationSettings
from evander.classification.service import ClassificationService
from evander.model_usage.runtime import model_usage_store


def service_for_runtime(runtime: Any) -> ClassificationService:
    config = getattr(runtime, "config", None)
    runtime_dir = getattr(config, "runtime_dir", None)
    if runtime_dir is None:
        runtime_dir = runtime.provider_config.get("runtime_dir")
    return ClassificationService(
        Path(runtime_dir) if runtime_dir else None,
        secret_resolver=getattr(runtime, "resolve_secret", None),
    )


def settings_for_query(context: Any) -> ClassificationSettings:
    scope = str(context.metadata.get("directory_path") or "root")
    resolver = context.provider_config_resolver
    if resolver is not None:
        return resolver.resolve_for_directory(scope).classification
    return ClassificationSettings.model_validate(context.provider_config.get("classification", {}))


def session_attribution(runtime: Any, session: Any) -> dict[str, Any]:
    attribution = {"session_id": session.session_id, "directory_path": session.directory_path}
    store = model_usage_store(runtime.config.runtime_dir)
    correlation = store.get_active_correlation_context(session_id=session.session_id)
    if correlation is not None:
        attribution.update(
            {
                key: value
                for key, value in correlation.model_dump().items()
                if key
                in {
                    "dispatch_id",
                    "workflow_run_id",
                    "node_id",
                    "effect_id",
                    "trace_id",
                    "agent_harness",
                    "execution_backend",
                }
            }
        )
    return attribution
