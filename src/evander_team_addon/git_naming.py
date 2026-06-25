"""Deterministic Git branch-name helpers for the Team Add-on.

These helpers build the add-on-owned branch namespace and validate plain branch
names. They never touch a real repository; naming is fully deterministic so the
repository and effect layers can derive the same branch for a given assignment,
task, or release without consulting external state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from evander_team_addon.models import Project

# Default canonical release prefix. ``<project-id>`` is substituted with the
# concrete project id when building a canonical release branch.
DEFAULT_CANONICAL_RELEASE_BRANCH_PREFIX = "releases/<project-id>/"


def normalize_canonical_release_branch_prefix(value: object) -> str:
    """Normalize a canonical release branch prefix to one trailing slash."""

    text = str(value or "").strip() or DEFAULT_CANONICAL_RELEASE_BRANCH_PREFIX
    normalized = f"{text.rstrip('/')}/"
    validate_branch_name(f"{normalized}release")
    return normalized


def validate_branch_name(value: object) -> str:
    """Return ``value`` if it is a plain Git branch name, else raise ``ValueError``.

    Rejects empty values, any whitespace, control characters, ``:``, a leading
    ``/``, the substring ``..``, and a leading ``refs/``. These are the cases the
    add-on can produce or accept from agents, kept conservative on purpose.
    """

    if not isinstance(value, str):
        raise ValueError("branch name must be a string")
    if not value:
        raise ValueError("branch name must be a non-empty string")
    if any(ch.isspace() for ch in value):
        raise ValueError(f"branch name {value!r} must not contain whitespace")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ValueError(f"branch name {value!r} must not contain control characters")
    if ":" in value:
        raise ValueError(f"branch name {value!r} must not contain ':'")
    if value.startswith("/"):
        raise ValueError(f"branch name {value!r} must not start with '/'")
    if ".." in value:
        raise ValueError(f"branch name {value!r} must not contain '..'")
    if value.startswith("refs/"):
        raise ValueError(f"branch name {value!r} must not start with 'refs/'")
    return value


def ephemeral_agent_branch(session_id: str, assignment_id: str) -> str:
    """``agents/<session-id>/<assignment-id>`` — the ephemeral worker's branch."""

    return validate_branch_name(f"agents/{session_id}/{assignment_id}")


def review_branch(team_id: str, assignment_id: str) -> str:
    """``team/<team-id>/reviews/<assignment-id>`` — a captured review branch."""

    return validate_branch_name(f"team/{team_id}/reviews/{assignment_id}")


def feature_branch(team_id: str, task_id: str, agent_id: str) -> str:
    """``team/<team-id>/features/<task-id>-<agent-id>`` — a consolidated feature."""

    return validate_branch_name(f"team/{team_id}/features/{task_id}-{agent_id}")


def release_branch(team_id: str, release_id: str) -> str:
    """``team/<team-id>/releases/<release-id>`` — an internal release branch."""

    return validate_branch_name(f"team/{team_id}/releases/{release_id}")


def canonical_release_branch(project: "Project", release_id: str) -> str:
    """Canonical release branch for ``project``, honoring its git config prefix.

    The prefix defaults to ``releases/<project-id>/`` where ``<project-id>`` is
    replaced with the project's id; operators may override it via
    ``ProjectGitConfig.canonical_release_branch_prefix``.
    """

    prefix = DEFAULT_CANONICAL_RELEASE_BRANCH_PREFIX
    git = getattr(project, "git", None)
    if git is not None and getattr(git, "canonical_release_branch_prefix", None):
        prefix = git.canonical_release_branch_prefix
    prefix = prefix.replace("<project-id>", project.id)
    return validate_branch_name(f"{prefix}{release_id}")
