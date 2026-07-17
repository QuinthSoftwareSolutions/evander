from __future__ import annotations

from typing import Any

from ...git_naming import validate_branch_name
from ...models import Project

from .common import _required_text


GIT_PROMOTION_EVENT_TYPE = "team.git.promotion_requested"


def _project_git_or_error(
    project: Project,
    result: dict[str, Any],
) -> Any:
    if project.git is None:
        result["errors"].append(f"project {project.id} has no git configuration for promotions")
        return None
    return project.git


def _managed_repo_fields(git: Any) -> dict[str, str]:
    return {
        "managed_repo_id": git.managed_repo_id,
        "directory_path": git.directory_path,
    }


def _emit_git_promotion_event(
    result: dict[str, Any],
    *,
    promotion_id: str,
    payload: dict[str, Any],
) -> None:
    result["events"].append(
        {
            "event_type": GIT_PROMOTION_EVENT_TYPE,
            "correlation_key": promotion_id,
            "idempotency_key": f"team-addon:{GIT_PROMOTION_EVENT_TYPE}:{promotion_id}",
            "payload": payload,
        }
    )


def _normalize_agent_branch_ref(value: object) -> str:
    text = _required_text(value, "agent_branch")
    prefix = "refs/heads/"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    branch = validate_branch_name(text)
    if not branch.startswith("agents/"):
        raise ValueError("agent_branch must be under agents/")
    return branch


__all__ = [
    "GIT_PROMOTION_EVENT_TYPE",
    "_project_git_or_error",
    "_managed_repo_fields",
    "_emit_git_promotion_event",
    "_normalize_agent_branch_ref",
]
