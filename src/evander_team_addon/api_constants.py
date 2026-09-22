"""Api constants."""

from __future__ import annotations

from typing import (
    Any,
)

from pydantic_core import (
    to_jsonable_python,
)
from starlette.responses import (
    JSONResponse as StarletteJSONResponse,
)

from .human_review_projection import (
    HUMAN_REVIEW_COLLECTION_KEYS,
)


class TeamJSONResponse(StarletteJSONResponse):
    """JSON response for Team API payloads containing derived model values."""

    def render(self, content: Any) -> bytes:
        return super().render(to_jsonable_python(content))


JSONResponse = TeamJSONResponse

DEFAULT_CHAT_MESSAGE_LIMIT = 50

MAX_CHAT_MESSAGE_LIMIT = 100

DEFAULT_TIMELINE_LIMIT = 50

MAX_TIMELINE_LIMIT = 200

DEFAULT_STATE_HISTORY_LIMIT = 100

MAX_STATE_HISTORY_LIMIT = 500

DEFAULT_INBOX_HISTORY_LIMIT = 50

MAX_INBOX_HISTORY_LIMIT = 200

TEAM_VIEW_IDS = (
    "workspace",
    "operations",
    "delivery",
    "projects",
    "task-board",
    "cases",
    "people",
    "chat",
    "mail",
    "activity",
    "governance",
    "planning",
    "timeline",
    "memory",
    "org",
)

TEAM_STATE_COLLECTION_KEYS = (
    "teams",
    "governance_groups",
    "governance_agenda",
    "governance_runtimes",
    "projects",
    "roles",
    "agents",
    "project_assignments",
    "agent_runtimes",
    "stand_downs",
    "cases",
    "tasks",
    "external_source_evidence_snapshots",
    "deliverables",
    "review_requests",
    "outcome_kind_definitions",
    "review_profiles",
    "inbox",
    "blockers",
    "blocker_events",
    "dependency_composition_recoveries",
    "action_records",
    "memories",
    "release_packages",
    *HUMAN_REVIEW_COLLECTION_KEYS,
    "dispatches",
    "git_preparation_attempts",
    "worktree_cleanup_candidates",
    "ephemeral_assignments",
    "git_promotions",
    "git_work_contexts",
    "conversations",
    "conversation_messages",
)

TEAM_VIEW_COLLECTIONS: dict[str, set[str]] = {
    "operations": {
        "teams",
        "projects",
        "roles",
        "agents",
        "agent_runtimes",
        "stand_downs",
        "tasks",
        "external_source_evidence_snapshots",
        "inbox",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "action_records",
        "deliverables",
        "release_packages",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_work_contexts",
        "ephemeral_assignments",
    },
    "delivery": {
        "teams",
        "projects",
        "roles",
        "agents",
        "tasks",
        "external_source_evidence_snapshots",
        "deliverables",
        "review_requests",
        "outcome_kind_definitions",
        "review_profiles",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "release_packages",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_promotions",
        "git_work_contexts",
    },
    "projects": {
        "teams",
        "projects",
        "roles",
        "agents",
        "tasks",
        "external_source_evidence_snapshots",
        "deliverables",
        "review_requests",
        "outcome_kind_definitions",
        "review_profiles",
        "release_packages",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "cases",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_promotions",
        "git_work_contexts",
    },
    "task-board": {
        "teams",
        "projects",
        "agents",
        "agent_runtimes",
        "tasks",
        "cases",
        "external_source_evidence_snapshots",
        "inbox",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_work_contexts",
    },
    "cases": {
        "teams",
        "projects",
        "agents",
        "cases",
        "tasks",
        "external_source_evidence_snapshots",
    },
    "people": {
        "teams",
        "projects",
        "roles",
        "agents",
        "agent_runtimes",
        "stand_downs",
        "tasks",
        "external_source_evidence_snapshots",
        "inbox",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_work_contexts",
    },
    "governance": {
        "teams",
        "governance_groups",
        "governance_agenda",
        "governance_runtimes",
        "projects",
        "roles",
        "agents",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
    },
    "planning": {
        "teams",
        "projects",
        "agents",
        "agent_runtimes",
        "tasks",
        "external_source_evidence_snapshots",
        "deliverables",
        "review_requests",
        "review_profiles",
        "inbox",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_work_contexts",
    },
    "timeline": {
        "teams",
        "projects",
        "roles",
        "agents",
        "cases",
        "tasks",
        "deliverables",
        "review_requests",
        "outcome_kind_definitions",
        "review_profiles",
        "release_packages",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "inbox",
        "action_records",
        "ephemeral_assignments",
        "git_promotions",
    },
    "org": {
        "teams",
        "governance_groups",
        "governance_agenda",
        "governance_runtimes",
        "projects",
        "roles",
        "agents",
        "agent_runtimes",
        "stand_downs",
        "tasks",
        "inbox",
    },
    "memory": {"teams", "projects", "agents", "memories"},
}

TEAM_VIEW_COLLECTIONS["workspace"] = {
    "teams",
    "governance_groups",
    "governance_agenda",
    "projects",
    "agents",
    "cases",
    "tasks",
    "external_source_evidence_snapshots",
    "deliverables",
    "review_requests",
    "inbox",
    "release_packages",
    "blockers",
    "blocker_events",
    "dependency_composition_recoveries",
    "dispatches",
    "git_preparation_attempts",
    "worktree_cleanup_candidates",
    "git_work_contexts",
    "git_promotions",
}

TEAM_VIEW_COLLECTIONS["chat"] = {"agents"}

TEAM_VIEW_COLLECTIONS["mail"] = {"teams", "projects", "roles", "agents"}

TEAM_VIEW_COLLECTIONS["activity"] = {"teams", "roles", "agents"}

_VIEW_RUNTIME_COLLECTIONS = {
    "teams",
    "projects",
    "roles",
    "agents",
    "project_assignments",
    "agent_runtimes",
    "stand_downs",
    "tasks",
    "deliverables",
    "review_requests",
    "inbox",
    "blockers",
    "dependency_composition_recoveries",
    "action_records",
    "dispatches",
    "release_packages",
    "release_verification_runs",
    "ephemeral_assignments",
    "git_promotions",
    "git_work_contexts",
    "external_source_evidence_snapshots",
}

_CHAT_VIEW_COLLECTIONS = {
    "teams",
    "agents",
    "projects",
    "project_assignments",
    "roles",
    "agent_runtimes",
    "stand_downs",
    "conversations",
    "conversation_messages",
    "chat_attachments",
}

_BOOTSTRAP_COLLECTIONS = {
    "teams",
    "governance_groups",
    "projects",
    "agents",
    "cases",
    "tasks",
    "deliverables",
    "review_requests",
    "conversations",
}

_COST_COLLECTIONS = {
    "teams",
    "governance_groups",
    "agents",
    "project_assignments",
    "ephemeral_assignments",
    "action_records",
}


def _view_read_collections(view: str) -> set[str]:
    if view == "chat":
        return _CHAT_VIEW_COLLECTIONS
    collections = TEAM_VIEW_COLLECTIONS[view] | _VIEW_RUNTIME_COLLECTIONS
    if "release_packages" in TEAM_VIEW_COLLECTIONS[view]:
        collections |= set(HUMAN_REVIEW_COLLECTION_KEYS)
    return collections


TEAM_RESOURCE_TOKEN_COLLECTIONS = (
    "teams",
    "governance_groups",
    "governance_agenda",
    "projects",
    "roles",
    "agents",
    "cases",
    "tasks",
    "external_source_evidence_snapshots",
    "deliverables",
    "review_requests",
    "outcome_kind_definitions",
    "review_profiles",
    "inbox",
    "action_records",
    "memories",
    "release_packages",
    "blockers",
    "dependency_composition_recoveries",
    "stand_downs",
)

TEAM_STATE_HISTORY_COLLECTIONS = (
    "inbox",
    "action_records",
    "release_packages",
    "blockers",
    "blocker_events",
    "dependency_composition_recoveries",
    "ephemeral_assignments",
    "git_promotions",
    "conversation_messages",
)
