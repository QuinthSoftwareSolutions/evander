"""Cost categories are labels, independent of agent/container runtime labels."""

from typing import Any

COST_CATEGORY_SNAPSHOT = "cost_category_snapshot"


def normalize_cost_category(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("cost_category must be a string or null")
    return value.strip() or None


def prompt_cost_category(payload: dict[str, Any], session: Any) -> str | None:
    """A serialized snapshot distinguishes resolved Uncategorized from inheritance."""
    import json

    if COST_CATEGORY_SNAPSHOT in payload:
        return normalize_cost_category(json.loads(payload[COST_CATEGORY_SNAPSHOT]))
    override = payload.get("cost_category")
    return normalize_cost_category(
        override if override is not None else getattr(session, "cost_category", None)
    )


def freeze_prompt_cost_category(payload: dict[str, Any], session: Any) -> str:
    import json

    snapshot = json.dumps(prompt_cost_category(payload, session), ensure_ascii=False)
    payload[COST_CATEGORY_SNAPSHOT] = snapshot
    return snapshot


def dispatch_with_cost_context(
    dispatch,
    *,
    runtime_dir,
    session,
    session_id,
    payload,
    dispatch_id,
    agent_harness="codex",
    model=None,
    effect_id=None,
    workflow_run_id=None,
    execution_backend=None,
):
    """Install immutable attribution before the worker can make its first request."""
    from evander.model_usage.runtime import (
        clear_model_usage_correlation_context,
        register_model_usage_correlation_context,
    )

    if runtime_dir is not None:
        register_model_usage_correlation_context(
            runtime_dir=runtime_dir,
            session_id=session_id,
            dispatch_id=dispatch_id,
            cost_category=prompt_cost_category(payload, session),
            cost_attribution_recorded=True,
            directory_path=getattr(session, "directory_path", None),
            model=model,
            agent_harness=agent_harness,
            effect_id=effect_id,
            workflow_run_id=workflow_run_id,
            execution_backend=execution_backend,
            expires_at=getattr(session, "expires_at", None),
        )
    try:
        result = dispatch()
        success = (
            result.get("success", True)
            if isinstance(result, dict)
            else getattr(result, "success", getattr(result, "returncode", 0) == 0)
        )
        queued = (
            result.get("queued", False)
            if isinstance(result, dict)
            else getattr(result, "queued", False)
        )
        if (not success or queued) and runtime_dir is not None:
            clear_model_usage_correlation_context(runtime_dir=runtime_dir, dispatch_id=dispatch_id)
        return result
    except BaseException:
        if runtime_dir is not None:
            clear_model_usage_correlation_context(runtime_dir=runtime_dir, dispatch_id=dispatch_id)
        raise
