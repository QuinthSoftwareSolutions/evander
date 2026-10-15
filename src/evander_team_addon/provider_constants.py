"""Provider constants."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("evander_team_addon.providers")

TEAM_AGENT_GUIDANCE_VERSION = 46

TEAM_AGENT_GUIDANCE_PATH = "/home/agent/AGENTS.md"

TEAM_AGENT_GUIDANCE_SECTION_ID = "evander-team-addon"

LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS: tuple[str, ...] = ()

TEAM_GOVERNANCE_GUIDANCE_VERSION = 2

TEAM_GOVERNANCE_GUIDANCE_SECTION_ID = "evander-team-addon-governance"

TEAM_WAKE_STATE_CONFLICT_MAX_ATTEMPTS = 3

TEAM_WAKE_STATE_CONFLICT_RETRY_DELAY_SECONDS = 1.0

TEAM_WAKE_OUTPUT_MAX_LEASE_SUMMARIES = 50

TEAM_WAKE_OUTPUT_MAX_IDS_PER_FIELD = 10

TEAM_WAKE_OUTPUT_MAX_ID_CHARS = 200

TEAM_WAKE_OUTPUT_MAX_ERROR_CHARS = 1_000

_TEAM_CODE_INDEX_TOOL_NAMES = ("code_index_status", "code_search", "code_navigate")

_ARTIFACT_SPACE_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_ARTIFACT_SPACE_ENCODED_SEGMENT_PREFIX = "seg_"

_ARTIFACT_SPACE_COMPONENT_MAX_BYTES = 96

_ARTIFACT_SPACE_REF_PREFIX = "artifact-space:"

_TEAM_REVIEW_ARTIFACT_ID_DEFAULT = "bundle"

_TEAM_WAKE_DEFINITION_PAYLOAD_FIELDS = (
    "team_id",
    "project_id",
    "agent_id",
    "limit",
    "lease_seconds",
    "wake_interval_seconds",
    "max_no_progress_count",
    "low_priority_wake_after_cycles",
    "respect_wake_interval",
    "run_global_maintenance",
    "worktree_cleanup_limit",
    "worktree_cleanup_grace_seconds",
    "worktree_cleanup_retry_seconds",
    "worktree_cleanup_recovery_seconds",
    "worktree_cleanup_dry_run",
    "resume_mode",
)

_TEAM_GOVERNANCE_WAKE_DEFINITION_PAYLOAD_FIELDS = (
    "governance_group_id",
    "limit",
    "lease_seconds",
    "wake_interval_seconds",
    "resume_mode",
)
