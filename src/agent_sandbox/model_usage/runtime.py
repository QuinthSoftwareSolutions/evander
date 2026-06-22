from __future__ import annotations

from functools import lru_cache
from datetime import datetime, timedelta
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from agent_sandbox.model_usage.models import ModelUsageCorrelationContext
from agent_sandbox.model_usage.store import ModelUsageStore
from agent_sandbox.models import utc_now

MODEL_USAGE_AGENT_HARNESS = "codex"
MODEL_USAGE_CORRELATION_TTL = timedelta(hours=2)
MODEL_USAGE_STORE_FILENAME = "model-usage.db"


@lru_cache(maxsize=32)
def _model_usage_store_for_runtime_dir(runtime_dir: str) -> ModelUsageStore:
    return ModelUsageStore(Path(runtime_dir) / MODEL_USAGE_STORE_FILENAME)


def model_usage_store(runtime_dir: Path | str) -> ModelUsageStore:
    return _model_usage_store_for_runtime_dir(str(Path(runtime_dir)))


def resolve_runtime_dir(owner: Any) -> Path | None:
    runtime_dir = getattr(getattr(owner, "config", None), "runtime_dir", None)
    if runtime_dir is not None:
        return Path(runtime_dir)
    runtime_dir = getattr(owner, "runtime_dir", None)
    if runtime_dir is not None:
        return Path(runtime_dir)
    provider_config = getattr(owner, "provider_config", None)
    if isinstance(provider_config, Mapping):
        runtime_dir = provider_config.get("runtime_dir")
        if runtime_dir is not None:
            return Path(runtime_dir)
    resolved_config = getattr(owner, "resolved_config", None)
    runtime_dir = getattr(resolved_config, "runtime_dir", None)
    if runtime_dir is not None:
        return Path(runtime_dir)
    if isinstance(resolved_config, Mapping):
        runtime_dir = resolved_config.get("runtime_dir")
        if runtime_dir is not None:
            return Path(runtime_dir)
    return None


def register_model_usage_correlation_context(
    *,
    runtime_dir: Path | str,
    session_id: str | None,
    dispatch_id: str | None,
    workflow_run_id: str | None = None,
    effect_id: str | None = None,
    node_id: str | None = None,
    execution_backend: str | None = None,
    directory_path: str | None = None,
    model: str | None = None,
    team_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    trace_id: str | None = None,
    agent_harness: str = MODEL_USAGE_AGENT_HARNESS,
    started_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> ModelUsageCorrelationContext | None:
    normalized_session_id = str(session_id or "").strip()
    normalized_dispatch_id = str(dispatch_id or "").strip()
    if not normalized_session_id or not normalized_dispatch_id:
        return None

    started = started_at or utc_now()
    expires = expires_at or (started + MODEL_USAGE_CORRELATION_TTL)
    context = ModelUsageCorrelationContext(
        session_id=normalized_session_id,
        dispatch_id=normalized_dispatch_id,
        workflow_run_id=workflow_run_id,
        effect_id=effect_id,
        node_id=node_id or effect_id,
        agent_harness=agent_harness,
        execution_backend=execution_backend,
        directory_path=directory_path,
        model=model,
        team_id=team_id,
        project_id=project_id,
        agent_id=agent_id,
        trace_id=trace_id,
        started_at=started,
        expires_at=expires,
    )
    try:
        model_usage_store(runtime_dir).upsert_correlation_context(context)
    except Exception:
        return None
    return context


def clear_model_usage_correlation_context(
    *,
    runtime_dir: Path | str,
    dispatch_id: str | None = None,
    session_id: str | None = None,
    agent_harness: str | None = None,
    execution_backend: str | None = None,
) -> int:
    try:
        return model_usage_store(runtime_dir).clear_correlation_context(
            dispatch_id=dispatch_id,
            session_id=session_id,
            agent_harness=agent_harness,
            execution_backend=execution_backend,
        )
    except Exception:
        return 0


__all__ = [
    "MODEL_USAGE_AGENT_HARNESS",
    "MODEL_USAGE_CORRELATION_TTL",
    "MODEL_USAGE_STORE_FILENAME",
    "clear_model_usage_correlation_context",
    "model_usage_store",
    "resolve_runtime_dir",
    "register_model_usage_correlation_context",
]
