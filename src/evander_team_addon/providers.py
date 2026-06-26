from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_sandbox.effects.providers.agent_clone_repo import AgentCloneRepoEffectProvider
from agent_sandbox.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from agent_sandbox.effects.providers.artifacts import (
    ArtifactImportPublishedEffectProvider,
    ArtifactPublishArtifactsEffectProvider,
)
from agent_sandbox.effects.providers.base import EffectProviderBase
from agent_sandbox.artifact_spaces import (
    ArtifactSpacePermissionError,
    ArtifactSpaceStore,
    normalize_space_path,
)
from agent_sandbox.events.internal import InternalEventBus, InternalEventSource, internal_event_store_path
from agent_sandbox.events.scheduled import (
    ScheduledInternalEventStore,
    scheduled_internal_event_store_path,
)
from agent_sandbox.events.provider_models import (
    AgentCloneRepoEffectDefinition,
    AgentCopyFilesEffectDefinition,
    ArtifactImportPublishedEffectDefinition,
    ArtifactPublishArtifactsEffectDefinition,
)
from agent_sandbox.events.models import WorkflowNodeEnvelope
from agent_sandbox.models import tool_name_value
from agent_sandbox.model_usage.runtime import register_model_usage_correlation_context
from agent_sandbox.published_artifacts import PublishedArtifactStore
from agent_sandbox.queries.providers.base import QueryProviderBase
from agent_sandbox.workflow.catalog import (
    WorkflowEditorFieldDefinition,
    WorkflowEditorNodeCatalogEntry,
)
from agent_sandbox.workflow.contracts import register_node_output_schema
from agent_sandbox.workflow.plugins import ProviderPluginRegistration

from .git_naming import validate_branch_name
from .costs import TeamCostService
from .mcp import (
    CHAT_TEAM_MCP_TOOL_NAMES,
    GOVERNANCE_TEAM_MCP_TOOL_NAMES,
    MCP_TOOL_NAMES,
    ORDINARY_TEAM_MCP_TOOL_NAMES,
    TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    TEAM_ASK_QUESTION_TOOL,
    TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
    TEAM_CREATE_CASE_TOOL,
    TEAM_CREATE_TASKS_TOOL,
    TEAM_CREATE_TASK_TOOL,
    TEAM_DECIDE_REVIEW_TOOL,
    TEAM_ESCALATE_OPERATOR_TOOL,
    TEAM_MARK_INBOX_HANDLED_TOOL,
    TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
    TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
    TEAM_RECORD_NOTE_TOOL,
    TEAM_RECOVER_RELEASE_TOOL,
    TEAM_REJECT_EPHEMERAL_RESULT_TOOL,
    TEAM_REPLY_TO_CHAT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
    TEAM_REQUEST_RELEASE_TOOL,
    TEAM_REQUEST_REVIEW_TOOL,
    TEAM_REQUEST_STATUS_TOOL,
    TEAM_REVOKE_STAND_DOWN_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_SEND_PROGRESS_REPORT_TOOL,
    TEAM_STAND_DOWN_AGENT_TOOL,
    TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL,
    TEAM_SUBMIT_DELIVERABLE_TOOL,
    TEAM_UPDATE_CASE_TOOL,
    TEAM_UPDATE_FEATURE_BRANCH_TOOL,
    TEAM_UPDATE_GOAL_TOOL,
    TEAM_UPDATE_TASK_TOOL,
)
from .models import (
    RolePermission,
    TeamState,
    team_session_health_payload,
    team_state_payload,
)
from .provider_schemas import (
    TEAM_OUTPUT_SCHEMAS,
    TeamAcceptEphemeralDeliverableEffectDefinition,
    TeamConsolidateGitBranchesEffectDefinition,
    TeamCreateCaseEffectDefinition,
    TeamCreateOperatorDirectiveEffectDefinition,
    TeamFinalizeAgentDispatchEffectDefinition,
    TeamMarkEphemeralCleanedUpEffectDefinition,
    TeamMarkEphemeralSpawnedEffectDefinition,
    TeamMarkReleasePublishedEffectDefinition,
    TeamPublishOutboxEventsEffectDefinition,
    TeamPublishReviewBranchEffectDefinition,
    TeamRecordGitPromotionEffectDefinition,
    TeamRequestEphemeralAgentsEffectDefinition,
    TeamStateQueryDefinition,
    TeamSessionRoutingQueryDefinition,
    TeamUpdateCaseEffectDefinition,
    TeamWakeDueAgentsEffectDefinition,
    TeamWakeGovernanceManagersEffectDefinition,
)
from .repository import TeamStateConflictError, TeamStateRepository

TEAM_AGENT_GUIDANCE_VERSION = 11
TEAM_AGENT_GUIDANCE_PATH = "/home/agent/AGENTS.md"
TEAM_AGENT_GUIDANCE_SECTION_ID = "evander-team-addon"
LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS = ("truman-team-addon",)
TEAM_GOVERNANCE_GUIDANCE_VERSION = 2
TEAM_GOVERNANCE_GUIDANCE_SECTION_ID = "evander-team-addon-governance"
_ARTIFACT_SPACE_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_ARTIFACT_SPACE_ENCODED_SEGMENT_PREFIX = "b64_"
_ARTIFACT_SPACE_REF_PREFIX = "artifact-space:"
_TEAM_REVIEW_ARTIFACT_ID_DEFAULT = "bundle"


def _catalog_field(
    key: str,
    label: str,
    kind: str,
    *,
    required: bool = False,
    bindable: bool = True,
    multiline: bool = False,
    description: str,
    placeholder: str | None = None,
    json_value_type: str | None = None,
    default_value: Any = None,
) -> WorkflowEditorFieldDefinition:
    return WorkflowEditorFieldDefinition(
        key=key,
        label=label,
        kind=kind,
        required=required,
        bindable=bindable,
        multiline=multiline,
        description=description,
        placeholder=placeholder,
        json_value_type=json_value_type,
        default_value=default_value,
    )


def _catalog_entry(
    kind: str,
    node_type: str,
    label: str,
    description: str,
    fields: list[WorkflowEditorFieldDefinition],
) -> WorkflowEditorNodeCatalogEntry:
    return WorkflowEditorNodeCatalogEntry(
        kind=kind,
        type=node_type,
        label=label,
        description=description,
        fields=fields,
    )


def _revision_field() -> WorkflowEditorFieldDefinition:
    return _catalog_field(
        "expected_revision",
        "Expected revision",
        "number",
        required=False,
        description=(
            "Optional Team state revision guard. Bind this when an upstream Team state query "
            "or operator action should reject stale writes."
        ),
        placeholder="42",
    )


def _valid_field() -> WorkflowEditorFieldDefinition:
    return _catalog_field(
        "valid",
        "Valid",
        "boolean",
        required=False,
        description=(
            "Whether the upstream parsed action is valid. Bind this from an action-artifact "
            "query or leave enabled for trusted workflow-created payloads."
        ),
        default_value=True,
    )


_TEAM_WORKFLOW_EDITOR_CATALOG: tuple[WorkflowEditorNodeCatalogEntry, ...] = (
    _catalog_entry(
        "effect",
        "effect:team:wake-due-agents",
        "Wake Team agents",
        "Lease due persistent Team agents, prepare project repos, and resume their sessions.",
        [
            _catalog_field(
                "team_id",
                "Team ID",
                "string",
                description="Limit wake leasing to one Team. Leave blank to evaluate all Teams.",
                placeholder="auto-dev",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Limit wake leasing to agents assigned to one Team project.",
                placeholder="web-app",
            ),
            _catalog_field(
                "agent_id",
                "Agent ID",
                "string",
                description="Limit wake leasing to one persistent Team agent.",
                placeholder="project-lead",
            ),
            _catalog_field(
                "limit",
                "Lease limit",
                "number",
                description="Maximum number of due agents to lease in this wake cycle.",
                default_value=10,
                placeholder="10",
            ),
            _catalog_field(
                "lease_seconds",
                "Lease seconds",
                "number",
                description="How long a Team runtime lease remains active before it can be retried.",
                default_value=900,
                placeholder="900",
            ),
            _catalog_field(
                "wake_interval_seconds",
                "Wake interval seconds",
                "number",
                description="Minimum idle interval before an unchanged agent is considered due again.",
                default_value=900,
                placeholder="900",
            ),
            _catalog_field(
                "max_no_progress_count",
                "Max no-progress count",
                "number",
                description="Stop leasing an agent after this many wake cycles without progress.",
                default_value=3,
                placeholder="3",
            ),
            _catalog_field(
                "low_priority_wake_after_cycles",
                "Low-priority wake cycles",
                "number",
                description="Batch low-priority inbox items until this many wake evaluations pass.",
                default_value=4,
                placeholder="4",
            ),
            _catalog_field(
                "respect_wake_interval",
                "Respect wake interval",
                "boolean",
                description="When false, ready task work can lease before next_wake_at; active leases and no-progress throttles still apply.",
                default_value=True,
            ),
            _catalog_field(
                "resume_mode",
                "Resume mode",
                "string",
                description="Agent resume mode for Team wakes. Leave blank for same-context resumes.",
                default_value="same_context",
                placeholder="same_context",
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:wake-governance-managers",
        "Wake governance managers",
        "Lease due governance group managers for agenda, cost, blocker, and check-in signals.",
        [
            _catalog_field(
                "governance_group_id",
                "Governance group ID",
                "string",
                description="Limit wake leasing to one governance group. Leave blank to evaluate all groups.",
                placeholder="platform-portfolio",
            ),
            _catalog_field(
                "limit",
                "Lease limit",
                "number",
                description="Maximum number of governance managers to lease in this wake cycle.",
                default_value=10,
                placeholder="10",
            ),
            _catalog_field(
                "lease_seconds",
                "Lease seconds",
                "number",
                description="How long a governance runtime lease remains active before it can be retried.",
                default_value=900,
                placeholder="900",
            ),
            _catalog_field(
                "wake_interval_seconds",
                "Wake interval seconds",
                "number",
                description="Minimum delay before the same governance group can wake again for persistent signals.",
                default_value=3600,
                placeholder="3600",
            ),
            _catalog_field(
                "cost_warning_ratio",
                "Cost warning ratio",
                "number",
                description="Member-team cap usage ratio that should wake governance before the cap is reached.",
                default_value=0.8,
                placeholder="0.8",
            ),
            _catalog_field(
                "resume_mode",
                "Resume mode",
                "string",
                description="Resume mode passed to the underlying agent resumer.",
                default_value="same_context",
                placeholder="same_context",
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:publish-outbox-events",
        "Publish Team outbox events",
        "Claim pending Team outbox records and publish their internal runtime events.",
        [
            _catalog_field(
                "limit",
                "Publish limit",
                "number",
                description="Maximum pending Team outbox records to claim on this runtime tick.",
                default_value=25,
                placeholder="25",
            ),
            _catalog_field(
                "intent_types",
                "Intent types",
                "json",
                description="Optional Team event types to publish. Leave blank for all pending records.",
                json_value_type="array",
            ),
            _catalog_field(
                "claim_ttl_seconds",
                "Claim TTL seconds",
                "number",
                description="How long a claimed outbox record remains owned before another worker can retry it.",
                default_value=300,
                placeholder="300",
            ),
            _catalog_field(
                "retry_after_seconds",
                "Retry after seconds",
                "number",
                description="Delay before retrying a record whose event publish fails.",
                default_value=60,
                placeholder="60",
            ),
            _catalog_field(
                "worker_id",
                "Worker ID",
                "string",
                description="Optional stable worker label recorded on claimed outbox rows.",
                placeholder="team-outbox-publisher",
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:finalize-agent-dispatch",
        "Finalize Team dispatch",
        "Close a persistent Team agent dispatch from recorded Team MCP activity.",
        [
            _catalog_field(
                "session_id",
                "Session ID",
                "string",
                required=True,
                description="Persistent Team agent session whose completed dispatch is being finalized.",
            ),
            _catalog_field(
                "dispatch_id",
                "Dispatch ID",
                "string",
                required=True,
                description="Active Team dispatch id to close.",
            ),
            _catalog_field(
                "agent_id",
                "Agent ID",
                "string",
                description="Optional guard requiring the session to resolve to this Team agent.",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:create-operator-directive",
        "Create Team directive",
        "Turn an operator instruction into a durable Team inbox item for the next Team wake.",
        [
            _catalog_field(
                "team_id",
                "Team ID",
                "string",
                required=True,
                description="Team that should receive the operator directive.",
            ),
            _catalog_field(
                "message",
                "Message",
                "string",
                required=True,
                multiline=True,
                description="Operator instruction to deliver through Team state.",
            ),
            _catalog_field(
                "recipient_agent_id",
                "Recipient agent ID",
                "string",
                description="Specific Team agent to receive the directive. Leave blank for manager routing.",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Optional project context for the directive.",
            ),
            _catalog_field(
                "task_id",
                "Task ID",
                "string",
                description="Optional task context for the directive.",
            ),
            _catalog_field(
                "title",
                "Title",
                "string",
                description="Short title shown in Team timeline and inbox views.",
            ),
            _catalog_field(
                "source",
                "Source",
                "string",
                description="Origin label for audit, such as admin, discord, teams, or workflow.",
                placeholder="workflow",
            ),
            _catalog_field(
                "source_message_id",
                "Source message ID",
                "string",
                description="Optional upstream message or event id for traceability.",
            ),
            _catalog_field(
                "created_by",
                "Created by",
                "string",
                description="Operator or workflow actor that created the directive.",
            ),
            _catalog_field(
                "requesting_session_id",
                "Requesting session ID",
                "string",
                description="Optional Truman session that requested the directive.",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:create-case",
        "Create Team case",
        "Create a Team intake or lifecycle case, optionally linked to existing execution tasks.",
        [
            _catalog_field(
                "case_id",
                "Case ID",
                "string",
                description="Optional stable case id. Leave blank to generate one.",
                placeholder="case-customer-ticket",
            ),
            _catalog_field(
                "team_id",
                "Team ID",
                "string",
                required=True,
                description="Team that owns the case.",
                placeholder="team-accounting",
            ),
            _catalog_field(
                "title",
                "Title",
                "string",
                required=True,
                description="Short case title shown in Team case views.",
            ),
            _catalog_field(
                "description",
                "Description",
                "string",
                multiline=True,
                description="Intake details, ticket summary, or lifecycle context.",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Optional Team project scope for the case.",
            ),
            _catalog_field(
                "status",
                "Status",
                "string",
                description=(
                    "Initial case status: open, triage, in_progress, waiting, "
                    "resolved, or closed."
                ),
                default_value="open",
                placeholder="open",
            ),
            _catalog_field(
                "priority",
                "Priority",
                "string",
                description="Initial priority: low, normal, or high.",
                default_value="normal",
                placeholder="normal",
            ),
            _catalog_field(
                "owner_agent_id",
                "Owner agent ID",
                "string",
                description="Optional same-team persistent agent that owns the case.",
            ),
            _catalog_field(
                "source",
                "Source",
                "string",
                description="Optional external system label such as jira, zendesk, or email.",
                placeholder="jira",
            ),
            _catalog_field(
                "source_ref",
                "Source ref",
                "string",
                description="Optional upstream ticket, issue, message, or conversation id.",
            ),
            _catalog_field(
                "tags",
                "Tags",
                "json",
                description="Optional case tags.",
                json_value_type="array",
            ),
            _catalog_field(
                "metadata",
                "Metadata",
                "json",
                description="Optional structured case metadata.",
                json_value_type="object",
            ),
            _catalog_field(
                "task_ids",
                "Task IDs",
                "json",
                description="Existing Team tasks to link to this case.",
                json_value_type="array",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:update-case",
        "Update Team case",
        "Update a Team intake or lifecycle case and synchronize task links.",
        [
            _catalog_field(
                "case_id",
                "Case ID",
                "string",
                required=True,
                description="Team case to update.",
                placeholder="case-customer-ticket",
            ),
            _catalog_field(
                "title",
                "Title",
                "string",
                description="Replacement case title when configured.",
            ),
            _catalog_field(
                "description",
                "Description",
                "string",
                multiline=True,
                description="Replacement case description. Configure blank or null to clear it.",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Replacement project scope. Configure blank or null to clear it.",
            ),
            _catalog_field(
                "status",
                "Status",
                "string",
                description=(
                    "Replacement status: open, triage, in_progress, waiting, "
                    "resolved, or closed."
                ),
                placeholder="in_progress",
            ),
            _catalog_field(
                "priority",
                "Priority",
                "string",
                description="Replacement priority: low, normal, or high.",
                placeholder="high",
            ),
            _catalog_field(
                "owner_agent_id",
                "Owner agent ID",
                "string",
                description="Replacement owner. Configure blank or null to clear it.",
            ),
            _catalog_field(
                "source",
                "Source",
                "string",
                description="Replacement external system label. Configure blank or null to clear it.",
            ),
            _catalog_field(
                "source_ref",
                "Source ref",
                "string",
                description="Replacement external reference. Configure blank or null to clear it.",
            ),
            _catalog_field(
                "tags",
                "Tags",
                "json",
                description="Replacement case tags when configured.",
                json_value_type="array",
            ),
            _catalog_field(
                "metadata",
                "Metadata",
                "json",
                description="Replacement structured case metadata when configured.",
                json_value_type="object",
            ),
            _catalog_field(
                "task_ids",
                "Task IDs",
                "json",
                description="Replacement linked Team task ids when configured.",
                json_value_type="array",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "query",
        "query:team:session-routing",
        "Route Team session completion",
        "Classify a completed session as persistent Team work, ephemeral Team work, or ignored.",
        [
            _catalog_field(
                "session_id",
                "Session ID",
                "string",
                required=True,
                description="Completed Truman session id to classify against Team runtime state.",
            ),
            _catalog_field(
                "dispatch_id",
                "Dispatch ID",
                "string",
                description="Completed dispatch id. Persistent Team actions only route when this matches the active lease.",
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:request-ephemeral-agents",
        "Request Team workers",
        "Create Team ephemeral assignment records that downstream workflow nodes can materialize.",
        [
            _catalog_field(
                "parent_agent_id",
                "Parent agent ID",
                "string",
                required=True,
                description="Persistent Team agent requesting the ephemeral workers.",
            ),
            _catalog_field(
                "requests",
                "Requests",
                "json",
                required=True,
                description="Array of ephemeral worker request objects from a Team action.",
                json_value_type="array",
            ),
            _catalog_field(
                "dispatch_id",
                "Dispatch ID",
                "string",
                description="Dispatch id associated with the parent agent action.",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:mark-ephemeral-spawned",
        "Mark Team worker spawned",
        "Attach a created Truman session to a Team ephemeral assignment.",
        [
            _catalog_field(
                "assignment_id",
                "Assignment ID",
                "string",
                required=True,
                description="Team ephemeral assignment that was materialized.",
            ),
            _catalog_field(
                "session_id",
                "Session ID",
                "string",
                description="Created Truman session id for the ephemeral worker.",
            ),
            _catalog_field(
                "create_result",
                "Create result",
                "json",
                description="Raw result object from the upstream agent-create node.",
                json_value_type="object",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:accept-ephemeral-deliverable",
        "Accept Team worker deliverable",
        "Promote or reject a completed ephemeral worker deliverable in Team state.",
        [
            _catalog_field(
                "assignment_id",
                "Assignment ID",
                "string",
                required=True,
                description="Ephemeral assignment whose deliverable is being decided.",
            ),
            _catalog_field(
                "accepted",
                "Accepted",
                "boolean",
                description="Whether the deliverable should be accepted.",
                default_value=True,
            ),
            _catalog_field(
                "deliverable_id",
                "Deliverable ID",
                "string",
                description="Optional deliverable id to attach to the accepted assignment.",
            ),
            _catalog_field(
                "rejection_reason",
                "Rejection reason",
                "string",
                description="Reason to record when the deliverable is rejected.",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:mark-ephemeral-cleaned-up",
        "Clean up Team worker",
        "Mark a Team ephemeral worker assignment as cleaned up after session destruction.",
        [
            _catalog_field(
                "assignment_id",
                "Assignment ID",
                "string",
                description="Ephemeral assignment to mark cleaned up.",
            ),
            _catalog_field(
                "session_id",
                "Session ID",
                "string",
                description="Destroyed Truman session id for lookup when assignment id is not bound.",
            ),
            _catalog_field(
                "destroy_result",
                "Destroy result",
                "json",
                description="Raw result object from the upstream agent-destroy node.",
                json_value_type="object",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "query",
        "query:team:state",
        "Read Team state",
        "Read filtered Team Add-on state for workflow decisions.",
        [
            _catalog_field(
                "team_id",
                "Team ID",
                "string",
                description="Limit the returned Team state to one Team.",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Limit the returned Team state to records for one project.",
            ),
            _catalog_field(
                "agent_id",
                "Agent ID",
                "string",
                description="Limit the returned Team state to one agent and dependent records.",
            ),
            _catalog_field(
                "include_records",
                "Include records",
                "boolean",
                description="Include matching record maps in addition to revision and success metadata.",
                default_value=True,
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:publish-review-branch",
        "Publish Team review branch",
        "Publish a review branch or commit for a Team Git promotion request.",
        [
            _catalog_field(
                "host_repo_path",
                "Host repo path",
                "string",
                required=True,
                description="Trusted host checkout path used to publish the review branch.",
            ),
            _catalog_field(
                "target_branch",
                "Target branch",
                "string",
                required=True,
                description="Branch name to create or update for review.",
            ),
            _catalog_field(
                "bare_repo_name",
                "Bare repo name",
                "string",
                description="Optional host-local bare repo name when resolving the remote.",
            ),
            _catalog_field(
                "bare_repo_path",
                "Bare repo path",
                "string",
                description="Optional explicit host-local bare repo path.",
            ),
            _catalog_field(
                "source_branch",
                "Source branch",
                "string",
                description="Source branch to publish when no commit SHA is supplied.",
            ),
            _catalog_field(
                "commit_sha",
                "Commit SHA",
                "string",
                description="Specific commit to publish when the promotion selected one.",
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:record-git-promotion",
        "Record Team Git promotion",
        "Record a Team Git promotion result and attach resulting branch metadata to deliverables.",
        [
            _catalog_field(
                "promotion_id",
                "Promotion ID",
                "string",
                required=True,
                description="Team Git promotion request being recorded.",
            ),
            _catalog_field(
                "success",
                "Success",
                "boolean",
                description="Whether the Git promotion succeeded.",
                default_value=True,
            ),
            _catalog_field(
                "result_outputs",
                "Result outputs",
                "json",
                description="Output object from the upstream publish or consolidation step.",
                json_value_type="object",
            ),
            _catalog_field(
                "error",
                "Error",
                "string",
                description="Failure message to store when the promotion failed.",
            ),
            _catalog_field(
                "commit_sha",
                "Commit SHA",
                "string",
                description="Promoted commit SHA when it is known outside the result payload.",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:consolidate-git-branches",
        "Consolidate Team branches",
        "Merge multiple Team source branches into a deterministic target branch.",
        [
            _catalog_field(
                "host_repo_path",
                "Host repo path",
                "string",
                required=True,
                description="Trusted host checkout path used for branch consolidation.",
            ),
            _catalog_field(
                "source_branches",
                "Source branches",
                "json",
                required=True,
                description="Array of branch names to merge in order.",
                json_value_type="array",
            ),
            _catalog_field(
                "target_branch",
                "Target branch",
                "string",
                required=True,
                description="Branch that receives the consolidated result.",
            ),
            _catalog_field(
                "base_branch",
                "Base branch",
                "string",
                description="Optional base branch to reset from before merging source branches.",
            ),
            _catalog_field(
                "bare_repo_path",
                "Bare repo path",
                "string",
                description=(
                    "Optional dropbox bare repository path used to fetch missing source branches."
                ),
            ),
            _catalog_field(
                "bare_repo_name",
                "Bare repo name",
                "string",
                description=(
                    "Optional dropbox bare repository name resolved under AGENT_SANDBOX_GIT_ROOT."
                ),
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:mark-release-published",
        "Mark Team release published",
        "Record a Team release package as published or blocked after release automation.",
        [
            _catalog_field(
                "release_package_id",
                "Release package ID",
                "string",
                required=True,
                description="Team release package being marked.",
            ),
            _catalog_field(
                "success",
                "Success",
                "boolean",
                description="Whether release publication succeeded.",
                default_value=True,
            ),
            _catalog_field(
                "canonical_branch",
                "Canonical branch",
                "string",
                description="Canonical branch that now contains the release, when applicable.",
            ),
            _catalog_field(
                "published_commit_sha",
                "Published commit SHA",
                "string",
                description="Commit SHA published for the release package.",
            ),
            _catalog_field(
                "result_outputs",
                "Result outputs",
                "json",
                description="Output object from the release or Git publication step.",
                json_value_type="object",
            ),
            _catalog_field(
                "error",
                "Error",
                "string",
                description="Failure message to store when release publication failed.",
            ),
            _revision_field(),
        ],
    ),
)


class TeamWakeDueAgentsEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamWakeDueAgentsEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "team_id",
            "project_id",
            "agent_id",
            "limit",
            "lease_seconds",
            "wake_interval_seconds",
            "max_no_progress_count",
            "low_priority_wake_after_cycles",
            "respect_wake_interval",
            "resume_mode",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        context = self.runtime_context
        resumer = getattr(context, "agent_resumer", None)
        if resumer is None or not callable(getattr(resumer, "resume_agent", None)):
            raise RuntimeError("effect:team:wake-due-agents requires agent_resumer")

        repo = TeamStateRepository(_runtime_dir(context))
        current_state = repo.read()
        session_records = _session_records_by_id(_session_repository_from_context(context))
        cost_guard_blocked_teams = _cost_guard_blocked_teams(
            context,
            state=current_state,
            team_id=_optional_text(payload.get("team_id")),
            project_id=_optional_text(payload.get("project_id")),
            agent_id=_optional_text(payload.get("agent_id")),
        )
        blocked_team_ids = {
            _required_text(record["team_id"], "team_id")
            for record in cost_guard_blocked_teams
        }
        resume_mode = _optional_text(payload.get("resume_mode")) or "same_context"
        lease_limit = _optional_int(payload.get("limit"), default=10)
        normalized_limit = max(0, int(lease_limit or 0))
        state, queued_chat_claims = repo.claim_pending_chat_message_dispatches(
            team_id=_optional_text(payload.get("team_id")),
            project_id=_optional_text(payload.get("project_id")),
            agent_id=_optional_text(payload.get("agent_id")),
            limit=normalized_limit,
            blocked_team_ids=blocked_team_ids,
        )
        chat_dispatches: list[dict[str, Any]] = []
        errors: list[str] = []
        state, chat_dispatches, chat_errors = _dispatch_queued_team_chat_messages(
            context,
            repo=repo,
            state=state,
            claims=queued_chat_claims,
            resumer=resumer,
        )
        errors.extend(chat_errors)
        if normalized_limit and len(queued_chat_claims) >= normalized_limit:
            leases = []
        else:
            regular_limit = (
                max(0, normalized_limit - len(queued_chat_claims))
                if normalized_limit
                else 0
            )
            state, leases = repo.lease_due_agents(
                team_id=_optional_text(payload.get("team_id")),
                project_id=_optional_text(payload.get("project_id")),
                agent_id=_optional_text(payload.get("agent_id")),
                limit=regular_limit,
                lease_seconds=_optional_int(payload.get("lease_seconds"), default=900),
                wake_interval_seconds=_optional_int(
                    payload.get("wake_interval_seconds"),
                    default=900,
                ),
                max_no_progress_count=_optional_int(
                    payload.get("max_no_progress_count"),
                    default=3,
                ),
                low_priority_wake_after_cycles=_optional_int(
                    payload.get("low_priority_wake_after_cycles"),
                    default=4,
                ),
                respect_wake_interval=_optional_bool(
                    payload.get("respect_wake_interval"),
                    default=True,
                ),
                blocked_team_ids=blocked_team_ids,
            )
        dispatches: list[dict[str, Any]] = []
        prepared_project_repos: list[dict[str, Any]] = []
        failed_project_repo_preparations: list[dict[str, Any]] = []
        review_artifact_imports: list[dict[str, Any]] = []
        review_artifact_import_errors: list[dict[str, Any]] = []
        for lease in leases:
            agent = lease["agent"]
            try:
                lease = {
                    **lease,
                    "session_health": team_session_health_payload(
                        state,
                        session_records=session_records,
                        agent_ids=_prompt_session_health_agent_ids(state, agent),
                    ),
                }
                tool_grant_result = _set_session_team_mcp_tools(
                    context,
                    session_id=agent["session_id"],
                    tool_names=_ordinary_team_tools_for_agent(agent),
                    dispatch_id=lease["dispatch_id"],
                )
                guidance_result = _seed_team_agent_guidance(context, agent=agent)
                try:
                    project_repo = _prepare_project_repo(context, state=state, agent=agent)
                except Exception as exc:
                    failed_project_repo_preparations.append(
                        _project_repo_prepare_record(
                            agent=agent,
                            dispatch_id=lease["dispatch_id"],
                            error=str(exc),
                        )
                    )
                    raise
                if project_repo is not None:
                    lease = {**lease, "project_repo": project_repo}
                    prepared_project_repos.append(
                        _project_repo_prepare_record(
                            agent=agent,
                            dispatch_id=lease["dispatch_id"],
                            project_repo=project_repo,
                        )
                    )
                review_artifacts = _prepare_review_artifacts_for_lease(
                    context,
                    lease=lease,
                )
                if review_artifacts["review_artifact_imports"] or review_artifacts[
                    "review_artifact_import_errors"
                ]:
                    lease = {**lease, **review_artifacts}
                    review_artifact_imports.extend(
                        review_artifacts["review_artifact_imports"]
                    )
                    review_artifact_import_errors.extend(
                        review_artifacts["review_artifact_import_errors"]
                    )
                runtime_payload = lease.get("runtime")
                context_rotation_pending = (
                    isinstance(runtime_payload, Mapping)
                    and bool(runtime_payload.get("context_rotation_pending"))
                )
                prompt = _build_prompt(lease)
                _register_team_dispatch_model_usage_context(
                    context,
                    agent=agent,
                    dispatch_id=lease["dispatch_id"],
                    effect_id=_optional_text(getattr(self.effect_definition, "id", None)),
                )
                result = resumer.resume_agent(
                    target=agent["session_id"],
                    prompt=prompt,
                    resume_mode="new_context" if context_rotation_pending else resume_mode,
                    dispatch_id=lease["dispatch_id"],
                )
                result_payload = _coerce_mapping(result)
                if _result_reports_failure(result_payload):
                    error = _resume_failure_message(result_payload)
                    state = _record_wake_failure(repo, lease=lease, error=error)
                    errors.append(f"{agent['id']}: {error}")
                    continue
                dispatch_payload = {
                    "agent_id": agent["id"],
                    "session_id": agent["session_id"],
                    "dispatch_id": lease["dispatch_id"],
                    "agent_guidance": guidance_result,
                    "team_mcp_tools": tool_grant_result,
                    "result": result_payload,
                }
                if project_repo is not None:
                    dispatch_payload["project_repo"] = project_repo
                if lease.get("review_artifact_imports"):
                    dispatch_payload["review_artifact_imports"] = lease[
                        "review_artifact_imports"
                    ]
                if lease.get("review_artifact_import_errors"):
                    dispatch_payload["review_artifact_import_errors"] = lease[
                        "review_artifact_import_errors"
                    ]
                if context_rotation_pending:
                    thread_id = _optional_text(result_payload.get("thread_id"))
                    state = repo.record_context_rotation_dispatch_started(
                        agent_id=agent["id"],
                        dispatch_id=lease["dispatch_id"],
                        thread_id=thread_id,
                    )
                    dispatch_payload["context_rotation"] = {
                        "rotated": True,
                        "reason": runtime_payload.get("context_rotation_reason")
                        if isinstance(runtime_payload, Mapping)
                        else None,
                    }
                dispatches.append(dispatch_payload)
                _mirror_session_metadata(
                    context,
                    repo=repo,
                    state=state,
                    agent_id=agent["id"],
                    session_id=agent["session_id"],
                )
            except Exception as exc:  # pragma: no cover - exercised by provider tests with fakes
                error = str(exc)
                try:
                    state = _record_wake_failure(repo, lease=lease, error=error)
                except Exception as release_exc:  # pragma: no cover - defensive diagnostics
                    error = f"{error}; failed to release team runtime lease: {release_exc}"
                errors.append(f"{agent['id']}: {error}")
        return {
            "success": not errors,
            "revision": state.revision,
            "leased_count": len(leases),
            "leases": leases,
            "chat_dispatches": chat_dispatches,
            "dispatches": dispatches,
            "prepared_project_repos": prepared_project_repos,
            "failed_project_repo_preparations": failed_project_repo_preparations,
            "review_artifact_imports": review_artifact_imports,
            "review_artifact_import_errors": review_artifact_import_errors,
            "cost_guard_blocked_team_ids": sorted(blocked_team_ids),
            "cost_guard_blocked_teams": cost_guard_blocked_teams,
            "errors": errors,
        }


def _dispatch_queued_team_chat_messages(
    context: Any,
    *,
    repo: TeamStateRepository,
    state: Any,
    claims: list[dict[str, Any]],
    resumer: Any,
) -> tuple[Any, list[dict[str, Any]], list[str]]:
    dispatches: list[dict[str, Any]] = []
    errors: list[str] = []
    for claim in claims:
        agent_id = _required_text(claim.get("agent_id"), "agent_id")
        message_id = _required_text(claim.get("message_id"), "message_id")
        conversation_id = _required_text(claim.get("conversation_id"), "conversation_id")
        dispatch_id = _required_text(claim.get("dispatch_id"), "dispatch_id")
        agent = state.agents[agent_id]
        message = state.conversation_messages[message_id]
        conversation = state.conversations[conversation_id]
        try:
            agent_payload = agent.model_dump(mode="json")
            mutation_allowed = _team_chat_mutation_allowed(message)
            chat_tool_names = list(CHAT_TEAM_MCP_TOOL_NAMES)
            if mutation_allowed:
                chat_tool_names.extend(_ordinary_team_tools_for_agent(agent_payload))
            tool_grant_result = _set_session_team_mcp_tools(
                context,
                session_id=agent.session_id,
                tool_names=chat_tool_names,
                dispatch_id=dispatch_id,
            )
            guidance_result = _seed_team_agent_guidance(context, agent=agent_payload)
            prompt = _build_team_chat_prompt(
                state=state,
                conversation=conversation,
                message=message,
                mutation_allowed=mutation_allowed,
            )
            result = resumer.resume_agent(
                target=agent.session_id,
                prompt=prompt,
                resume_mode="same_context",
                dispatch_id=dispatch_id,
            )
            result_payload = _coerce_mapping(result)
            if _result_reports_failure(result_payload):
                error = _resume_failure_message(result_payload)
                state, _message = repo.mark_chat_message_dispatch_failed(
                    message_id=message_id,
                    error=error,
                )
                errors.append(f"{agent_id}: {error}")
                continue
            dispatches.append(
                {
                    "agent_id": agent_id,
                    "session_id": agent.session_id,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "dispatch_id": dispatch_id,
                    "agent_guidance": guidance_result,
                    "team_chat_mcp_tools": tool_grant_result,
                    "result": result_payload,
                }
            )
            _mirror_session_metadata(
                context,
                repo=repo,
                state=state,
                agent_id=agent_id,
                session_id=agent.session_id,
            )
        except Exception as exc:  # pragma: no cover - covered through provider tests with fakes
            error = str(exc)
            try:
                state, _message = repo.mark_chat_message_dispatch_failed(
                    message_id=message_id,
                    error=error,
                )
            except Exception as release_exc:  # pragma: no cover - defensive diagnostics
                error = f"{error}; failed to release team chat dispatch: {release_exc}"
            errors.append(f"{agent_id}: {error}")
    return state, dispatches, errors


class TeamWakeGovernanceManagersEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamWakeGovernanceManagersEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "governance_group_id",
            "limit",
            "lease_seconds",
            "wake_interval_seconds",
            "cost_warning_ratio",
            "resume_mode",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        context = self.runtime_context
        resumer = getattr(context, "agent_resumer", None)
        if resumer is None or not callable(getattr(resumer, "resume_agent", None)):
            raise RuntimeError("effect:team:wake-governance-managers requires agent_resumer")

        repo = TeamStateRepository(_runtime_dir(context))
        current_state = repo.read()
        cost_payload = TeamCostService(
            runtime_dir=_runtime_dir(context),
            state=current_state,
            session_repository=_session_repository_from_context(context),
        ).costs_payload(backfill_transcripts=True)
        state, leases = repo.lease_due_governance_managers(
            governance_group_id=_optional_text(payload.get("governance_group_id")),
            limit=_optional_int(payload.get("limit"), default=10),
            lease_seconds=_optional_int(payload.get("lease_seconds"), default=900),
            wake_interval_seconds=_optional_int(
                payload.get("wake_interval_seconds"),
                default=3600,
            ),
            cost_warning_ratio=_optional_text(payload.get("cost_warning_ratio")) or "0.8",
            cost_payload=cost_payload,
        )
        resume_mode = _optional_text(payload.get("resume_mode")) or "same_context"
        dispatches: list[dict[str, Any]] = []
        errors: list[str] = []
        for lease in leases:
            agent = lease["agent"]
            group = lease["governance_group"]
            try:
                guidance_result = _seed_team_governance_guidance(context, agent=agent)
                tool_grant_result = _set_session_team_mcp_tools(
                    context,
                    session_id=agent["session_id"],
                    tool_names=GOVERNANCE_TEAM_MCP_TOOL_NAMES,
                    dispatch_id=lease["dispatch_id"],
                )
                _register_team_dispatch_model_usage_context(
                    context,
                    agent=agent,
                    dispatch_id=lease["dispatch_id"],
                    effect_id=_optional_text(getattr(self.effect_definition, "id", None)),
                )
                result = resumer.resume_agent(
                    target=agent["session_id"],
                    prompt=_build_governance_prompt(lease),
                    resume_mode=resume_mode,
                    dispatch_id=lease["dispatch_id"],
                )
                result_payload = _coerce_mapping(result)
                if _result_reports_failure(result_payload):
                    errors.append(
                        f"{group['id']}/{agent['id']}: {_resume_failure_message(result_payload)}"
                    )
                dispatches.append(
                    {
                        "governance_group_id": group["id"],
                        "agent_id": agent["id"],
                        "session_id": agent["session_id"],
                        "dispatch_id": lease["dispatch_id"],
                        "reason": lease["reason"],
                        "governance_agent_guidance": guidance_result,
                        "governance_mcp_tools": tool_grant_result,
                        "result": result_payload,
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive provider diagnostics
                errors.append(f"{group['id']}/{agent['id']}: {exc}")
            finally:
                state = repo.complete_governance_dispatch(
                    governance_group_id=group["id"],
                    dispatch_id=lease["dispatch_id"],
                )
        return {
            "success": not errors,
            "revision": state.revision,
            "leased_count": len(leases),
            "leases": leases,
            "dispatches": dispatches,
            "errors": errors,
        }


class TeamFinalizeAgentDispatchEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(
                TeamFinalizeAgentDispatchEffectDefinition,
                effect_definition,
            )
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "session_id",
            "dispatch_id",
            "agent_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            state, result = repo.finalize_agent_dispatch_from_activity(
                session_id=_required_text(payload.get("session_id"), "session_id"),
                dispatch_id=_required_text(payload.get("dispatch_id"), "dispatch_id"),
                agent_id=_optional_text(payload.get("agent_id")),
                expected_revision=_optional_int(
                    payload.get("expected_revision"),
                    default=None,
                ),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        agent_id = result.get("agent_id")
        if result.get("finalized") and isinstance(agent_id, str) and agent_id in state.agents:
            _mirror_session_metadata(
                self.runtime_context,
                repo=repo,
                state=state,
                agent_id=agent_id,
                session_id=state.agents[agent_id].session_id,
            )
        published_event_ids = _publish_team_events(
            self.runtime_context,
            result.get("events"),
        )
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        if isinstance(agent_id, str):
            state_payload = _filtered_team_state_payload(
                state,
                team_id=None,
                project_id=None,
                agent_id=agent_id,
            )
        else:
            state_payload = team_state_payload(state, include_agent_avatars=False)
            state_payload["conversations"] = {}
            state_payload["conversation_messages"] = {}
        return {**result, "state": state_payload}


class TeamCreateOperatorDirectiveEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamCreateOperatorDirectiveEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "team_id",
            "message",
            "recipient_agent_id",
            "project_id",
            "task_id",
            "title",
            "source",
            "source_message_id",
            "created_by",
            "requesting_session_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            state, item = repo.create_operator_directive(
                team_id=_required_text(payload.get("team_id"), "team_id"),
                message=_required_text(payload.get("message"), "message"),
                recipient_agent_id=_optional_text(payload.get("recipient_agent_id")),
                project_id=_optional_text(payload.get("project_id")),
                task_id=_optional_text(payload.get("task_id")),
                title=_optional_text(payload.get("title")),
                source=_optional_text(payload.get("source")),
                source_message_id=_optional_text(payload.get("source_message_id")),
                created_by=_optional_text(payload.get("created_by")),
                requesting_session_id=_optional_text(payload.get("requesting_session_id")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "revision": exc.current_revision,
                "inbox_item_id": None,
                "recipient_agent_id": None,
                "team_id": _optional_text(payload.get("team_id")),
                "project_id": _optional_text(payload.get("project_id")),
                "task_id": _optional_text(payload.get("task_id")),
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "revision": repo.read().revision,
                "inbox_item_id": None,
                "recipient_agent_id": None,
                "team_id": _optional_text(payload.get("team_id")),
                "project_id": _optional_text(payload.get("project_id")),
                "task_id": _optional_text(payload.get("task_id")),
                "error": str(exc),
            }
        project_id = item.payload.get("project_id")
        return {
            "success": True,
            "revision": state.revision,
            "inbox_item_id": item.id,
            "recipient_agent_id": item.recipient_agent_id,
            "team_id": item.payload.get("team_id"),
            "project_id": project_id if isinstance(project_id, str) else None,
            "task_id": item.task_id,
            "error": None,
        }


class TeamCreateCaseEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamCreateCaseEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "case_id",
            "team_id",
            "title",
            "description",
            "project_id",
            "status",
            "priority",
            "owner_agent_id",
            "source",
            "source_ref",
            "tags",
            "metadata",
            "task_ids",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        case_id = _optional_text(payload.get("case_id"))
        try:
            state, effective_case_id = repo.create_case_with_id(
                case_id=case_id,
                team_id=_required_text(payload.get("team_id"), "team_id"),
                title=_required_text(payload.get("title"), "title"),
                description=_optional_text(payload.get("description")) or "",
                project_id=_optional_text(payload.get("project_id")),
                status=_optional_text(payload.get("status")) or "open",
                priority=_optional_text(payload.get("priority")) or "normal",
                owner_agent_id=_optional_text(payload.get("owner_agent_id")),
                source=_optional_text(payload.get("source")),
                source_ref=_optional_text(payload.get("source_ref")),
                tags=_string_list(payload.get("tags"), "tags"),
                metadata=_optional_mapping(payload.get("metadata"), "metadata") or {},
                task_ids=_string_list(payload.get("task_ids"), "task_ids"),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return _case_effect_error_result(
                revision=exc.current_revision,
                case_id=case_id,
                team_id=_optional_text(payload.get("team_id")),
                project_id=_optional_text(payload.get("project_id")),
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (TypeError, ValueError) as exc:
            return _case_effect_error_result(
                revision=repo.read().revision,
                case_id=case_id,
                team_id=_optional_text(payload.get("team_id")),
                project_id=_optional_text(payload.get("project_id")),
                error=str(exc),
            )
        return _case_effect_success_result(state, case_id=effective_case_id)


class TeamUpdateCaseEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamUpdateCaseEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "case_id",
            "title",
            "description",
            "project_id",
            "status",
            "priority",
            "owner_agent_id",
            "source",
            "source_ref",
            "tags",
            "metadata",
            "task_ids",
            "expected_revision",
        )
        resolved_fields = set(resolved_config or {})
        definition_fields = _configured_definition_fields(self.effect_definition)
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        case_id = _optional_text(payload.get("case_id"))
        try:
            required_case_id = _required_text(payload.get("case_id"), "case_id")
            state = repo.update_case(
                case_id=required_case_id,
                title=_optional_text(payload.get("title")),
                description=_optional_text(payload.get("description")),
                set_description=_payload_field_configured(
                    "description",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                project_id=_optional_text(payload.get("project_id")),
                set_project_id=_payload_field_configured(
                    "project_id",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                status=_optional_text(payload.get("status")),
                priority=_optional_text(payload.get("priority")),
                owner_agent_id=_optional_text(payload.get("owner_agent_id")),
                set_owner_agent_id=_payload_field_configured(
                    "owner_agent_id",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                source=_optional_text(payload.get("source")),
                set_source=_payload_field_configured(
                    "source",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                source_ref=_optional_text(payload.get("source_ref")),
                set_source_ref=_payload_field_configured(
                    "source_ref",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                tags=(
                    _string_list(payload.get("tags"), "tags")
                    if _payload_field_configured(
                        "tags",
                        definition_fields=definition_fields,
                        resolved_fields=resolved_fields,
                    )
                    and payload.get("tags") is not None
                    else None
                ),
                metadata=(
                    _optional_mapping(payload.get("metadata"), "metadata")
                    if _payload_field_configured(
                        "metadata",
                        definition_fields=definition_fields,
                        resolved_fields=resolved_fields,
                    )
                    and payload.get("metadata") is not None
                    else None
                ),
                task_ids=(
                    _string_list(payload.get("task_ids"), "task_ids")
                    if _payload_field_configured(
                        "task_ids",
                        definition_fields=definition_fields,
                        resolved_fields=resolved_fields,
                    )
                    and payload.get("task_ids") is not None
                    else None
                ),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return _case_effect_error_result(
                revision=exc.current_revision,
                case_id=case_id,
                team_id=None,
                project_id=_optional_text(payload.get("project_id")),
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (TypeError, ValueError) as exc:
            return _case_effect_error_result(
                revision=repo.read().revision,
                case_id=case_id,
                team_id=None,
                project_id=_optional_text(payload.get("project_id")),
                error=str(exc),
            )
        return _case_effect_success_result(state, case_id=required_case_id)


class TeamRequestEphemeralAgentsEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamRequestEphemeralAgentsEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "parent_agent_id",
            "requests",
            "dispatch_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.request_ephemeral_assignments(
                parent_agent_id=_required_text(payload.get("parent_agent_id"), "parent_agent_id"),
                requests=_list_of_mappings(payload.get("requests"), "requests"),
                dispatch_id=_optional_text(payload.get("dispatch_id")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        published_event_ids = _publish_team_events(self.runtime_context, result.get("events"))
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        return result


class TeamMarkEphemeralSpawnedEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamMarkEphemeralSpawnedEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "assignment_id",
            "session_id",
            "create_result",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.mark_ephemeral_spawned(
                assignment_id=_required_text(payload.get("assignment_id"), "assignment_id"),
                session_id=_optional_text(payload.get("session_id")),
                create_result=_optional_mapping(payload.get("create_result"), "create_result"),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result


class TeamPublishOutboxEventsEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamPublishOutboxEventsEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "limit",
            "intent_types",
            "claim_ttl_seconds",
            "retry_after_seconds",
            "worker_id",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        limit = _optional_int(payload.get("limit"), default=25)
        limit = 25 if limit is None else max(0, limit)
        claim_ttl_seconds = _optional_int(payload.get("claim_ttl_seconds"), default=300)
        claim_ttl_seconds = 300 if claim_ttl_seconds is None else max(1, claim_ttl_seconds)
        retry_after_seconds = _optional_int(payload.get("retry_after_seconds"), default=60)
        retry_after_seconds = 60 if retry_after_seconds is None else max(0, retry_after_seconds)
        worker_id = _optional_text(payload.get("worker_id")) or "team-outbox-publisher"
        intent_types = _string_list(payload.get("intent_types"), "intent_types")

        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        claimed = repo.claim_outbox_records(
            worker_id=worker_id,
            limit=limit,
            intent_types=intent_types or None,
            claim_ttl_seconds=claim_ttl_seconds,
        )
        published_event_ids: list[str] = []
        completed_record_ids: list[str] = []
        failed_record_ids: list[str] = []
        errors: list[dict[str, Any]] = []
        for record in claimed:
            try:
                event = dict(record.payload)
                event.setdefault("idempotency_key", record.idempotency_key)
                record_event_ids = _publish_team_events(self.runtime_context, [event])
                if not record_event_ids:
                    raise RuntimeError("outbox record did not publish an internal event")
                repo.complete_outbox_record(
                    record_id=record.id,
                    claim_token=record.claim_token,
                )
                completed_record_ids.append(record.id)
                published_event_ids.extend(record_event_ids)
            except Exception as exc:
                repo.fail_outbox_record(
                    record_id=record.id,
                    error_code="publish_failed",
                    error_message=str(exc),
                    claim_token=record.claim_token,
                    retry_after_seconds=retry_after_seconds,
                )
                failed_record_ids.append(record.id)
                errors.append(
                    {
                        "record_id": record.id,
                        "intent_type": record.intent_type,
                        "error": str(exc),
                    }
                )
        return {
            "success": not errors,
            "claimed_count": len(claimed),
            "published_count": len(completed_record_ids),
            "published_event_ids": published_event_ids,
            "completed_outbox_record_ids": completed_record_ids,
            "failed_outbox_record_ids": failed_record_ids,
            "errors": errors,
        }


class TeamAcceptEphemeralDeliverableEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamAcceptEphemeralDeliverableEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "assignment_id",
            "accepted",
            "deliverable_id",
            "rejection_reason",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.accept_ephemeral_deliverable(
                assignment_id=_required_text(payload.get("assignment_id"), "assignment_id"),
                accepted=_optional_bool(payload.get("accepted"), default=True),
                deliverable_id=_optional_text(payload.get("deliverable_id")),
                rejection_reason=_optional_text(payload.get("rejection_reason")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result


class TeamMarkEphemeralCleanedUpEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamMarkEphemeralCleanedUpEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "assignment_id",
            "session_id",
            "destroy_result",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.mark_ephemeral_cleaned_up(
                assignment_id=_optional_text(payload.get("assignment_id")),
                session_id=_optional_text(payload.get("session_id")),
                destroy_result=_optional_mapping(payload.get("destroy_result"), "destroy_result"),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result


class TeamRecordGitPromotionEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamRecordGitPromotionEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "promotion_id",
            "success",
            "result_outputs",
            "error",
            "commit_sha",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.record_git_promotion_result(
                promotion_id=_required_text(payload.get("promotion_id"), "promotion_id"),
                success=_optional_bool(payload.get("success"), default=True),
                outputs=_optional_mapping(
                    _result_outputs_payload(payload), "result_outputs"
                )
                or {},
                error=_optional_text(payload.get("error")),
                commit_sha=_optional_text(payload.get("commit_sha")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        published_event_ids = _publish_team_events(
            self.runtime_context,
            result.get("events"),
        )
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        return result


class TeamPublishReviewBranchEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamPublishReviewBranchEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "host_repo_path",
            "bare_repo_name",
            "bare_repo_path",
            "source_branch",
            "commit_sha",
            "target_branch",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        try:
            host_repo_path = _required_text(payload.get("host_repo_path"), "host_repo_path")
            target_branch = validate_branch_name(
                _required_text(payload.get("target_branch"), "target_branch")
            )
            bare_repo_path = _resolve_bare_repo_path(
                _optional_text(payload.get("bare_repo_path")),
                _optional_text(payload.get("bare_repo_name")),
            )
            source_branch_value = _optional_text(payload.get("source_branch"))
            source_branch = (
                validate_branch_name(source_branch_value)
                if source_branch_value is not None
                else None
            )
            commit_sha = _optional_text(payload.get("commit_sha"))
            if source_branch is None and commit_sha is None:
                raise ValueError("source_branch or commit_sha is required")
        except ValueError as exc:
            return _review_publish_failure(None, None, str(exc))
        return _publish_review_branch(
            host_repo_path=host_repo_path,
            bare_repo_path=bare_repo_path,
            target_branch=target_branch,
            source_branch=source_branch,
            commit_sha=commit_sha,
        )


class TeamMarkReleasePublishedEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamMarkReleasePublishedEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "release_package_id",
            "success",
            "canonical_branch",
            "published_commit_sha",
            "result_outputs",
            "error",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.mark_release_published(
                release_package_id=_required_text(
                    payload.get("release_package_id"), "release_package_id"
                ),
                success=_optional_bool(payload.get("success"), default=True),
                canonical_branch=_optional_text(payload.get("canonical_branch")),
                published_commit_sha=_optional_text(payload.get("published_commit_sha")),
                outputs=_optional_mapping(
                    _result_outputs_payload(payload), "result_outputs"
                )
                or {},
                error=_optional_text(payload.get("error")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result


class TeamConsolidateGitBranchesEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamConsolidateGitBranchesEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "host_repo_path",
            "source_branches",
            "target_branch",
            "base_branch",
            "bare_repo_path",
            "bare_repo_name",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        host_repo_path = _required_text(payload.get("host_repo_path"), "host_repo_path")
        try:
            source_branches = [
                validate_branch_name(branch)
                for branch in _string_list(payload.get("source_branches"), "source_branches")
            ]
            target_branch = validate_branch_name(
                _required_text(payload.get("target_branch"), "target_branch")
            )
            base_value = _optional_text(payload.get("base_branch"))
            base_branch = validate_branch_name(base_value) if base_value else None
            bare_repo_value = _optional_text(payload.get("bare_repo_path"))
            bare_repo_name = _optional_text(payload.get("bare_repo_name"))
            bare_repo_path = (
                _resolve_bare_repo_path(bare_repo_value, bare_repo_name)
                if bare_repo_value is not None or bare_repo_name is not None
                else None
            )
        except ValueError as exc:
            return _consolidation_failure(None, str(exc))
        if not source_branches:
            return _consolidation_failure(target_branch, "source_branches is required")
        return _consolidate_git_branches(
            host_repo_path=host_repo_path,
            source_branches=source_branches,
            target_branch=target_branch,
            base_branch=base_branch,
            bare_repo_path=bare_repo_path,
        )


class TeamStateQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(TeamStateQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.query_definition,
            "team_id",
            "project_id",
            "agent_id",
            "include_records",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        state = TeamStateRepository(_runtime_dir(self.runtime_context)).read()
        session_records = _session_records_by_id(
            _session_repository_from_context(self.runtime_context)
        )
        team_id = _optional_text(payload.get("team_id"))
        project_id = _optional_text(payload.get("project_id"))
        agent_id = _optional_text(payload.get("agent_id"))
        filtered = _filtered_team_state_payload(
            state,
            team_id=team_id,
            project_id=project_id,
            agent_id=agent_id,
            session_records=session_records,
        )
        return {
            "success": True,
            "revision": state.revision,
            "state": filtered,
            "teams": filtered["teams"],
            "projects": filtered["projects"],
            "agents": filtered["agents"],
            "cases": filtered["cases"],
            "tasks": filtered["tasks"],
        }


class TeamSessionRoutingQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(TeamSessionRoutingQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(self.query_definition, "session_id", "dispatch_id")
        if resolved_config:
            payload.update(dict(resolved_config))
        session_id = _required_text(payload.get("session_id"), "session_id")
        dispatch_id = _optional_text(payload.get("dispatch_id"))
        state = TeamStateRepository(_runtime_dir(self.runtime_context)).read()
        for agent in state.agents.values():
            if agent.session_id == session_id:
                runtime = state.agent_runtimes.get(agent.id)
                expected_dispatch_id = (
                    _optional_text(runtime.active_dispatch_id)
                    if runtime is not None
                    else None
                )
                dispatch_mismatch = (
                    expected_dispatch_id is not None
                    and dispatch_id != expected_dispatch_id
                ) or (
                    expected_dispatch_id is None
                    and dispatch_id is not None
                )
                if dispatch_mismatch:
                    return {
                        "success": True,
                        "session_id": session_id,
                        "dispatch_id": dispatch_id,
                        "kind": "ignored",
                        "agent_id": agent.id,
                        "assignment_id": None,
                        "persistent": False,
                        "ephemeral": False,
                        "ignored": True,
                        "reason": "dispatch_id does not match active team runtime lease",
                        "expected_dispatch_id": expected_dispatch_id,
                    }
                return {
                    "success": True,
                    "session_id": session_id,
                    "dispatch_id": dispatch_id,
                    "kind": "persistent",
                    "agent_id": agent.id,
                    "assignment_id": None,
                    "persistent": True,
                    "ephemeral": False,
                    "ignored": False,
                    "reason": None,
                    "expected_dispatch_id": expected_dispatch_id,
                }
        for assignment in state.ephemeral_assignments.values():
            if assignment.session_id == session_id:
                return {
                    "success": True,
                    "session_id": session_id,
                    "dispatch_id": dispatch_id,
                    "kind": "ephemeral",
                    "agent_id": None,
                    "assignment_id": assignment.id,
                    "persistent": False,
                    "ephemeral": True,
                    "ignored": False,
                    "reason": None,
                    "expected_dispatch_id": None,
                }
        return {
            "success": False,
            "session_id": session_id,
            "dispatch_id": dispatch_id,
            "kind": "unknown",
            "agent_id": None,
            "assignment_id": None,
            "persistent": False,
            "ephemeral": False,
            "ignored": False,
            "reason": None,
            "expected_dispatch_id": None,
        }


def _publish_deliverable_artifact_bundle(
    context: Any,
    *,
    agent: Mapping[str, Any],
    team_id: str,
    project_id: str,
    deliverable_id: str,
    dispatch_id: str,
    source_paths: list[str],
) -> dict[str, Any]:
    runtime_dir = _runtime_dir(context)
    safe_team_id = _artifact_space_path_segment(team_id, "team_id")
    safe_project_id = _artifact_space_path_segment(project_id, "project_id")
    safe_deliverable_id = _artifact_space_path_segment(deliverable_id, "deliverable_id")
    safe_dispatch_id = _artifact_space_path_segment(dispatch_id, "dispatch_id")
    artifact_key = (
        f"team/{safe_team_id}/{safe_project_id}/"
        f"{safe_deliverable_id}/{safe_dispatch_id}/bundle"
    )
    artifact_ref_path = (
        f"/teams/{safe_team_id}/projects/{safe_project_id}/"
        f"deliverables/{safe_deliverable_id}"
    )
    artifact_ref = f"artifact-space:{artifact_ref_path}"
    store = PublishedArtifactStore(runtime_dir)
    if store.exists(artifact_key):
        artifact = store.get(artifact_key)
        publish_result: dict[str, Any] = {
            "success": True,
            "key": artifact.metadata.key,
            "byte_count": artifact.metadata.byte_count,
            "sha256": artifact.metadata.sha256,
            "source_file_count": artifact.metadata.source_file_count,
            "source_byte_count": artifact.metadata.source_byte_count,
            "reused": True,
        }
    else:
        provider = ArtifactPublishArtifactsEffectProvider()
        provider.initialize(
            ArtifactPublishArtifactsEffectDefinition(
                id=f"team-deliverable-artifacts-{deliverable_id}",
                display_name="Publish Team deliverable artifacts",
                type="effect:artifact:publish-artifacts",
                session_id=_required_text(agent.get("session_id"), "session_id"),
                key=artifact_key,
                source_paths=source_paths,
            ),
            context,
        )
        publish_result = provider.execute()
        publish_result["reused"] = False

    version = _ensure_deliverable_artifact_space_version(
        runtime_dir,
        space_path=artifact_ref_path,
        artifact_key=artifact_key,
        team_id=team_id,
        project_id=project_id,
        deliverable_id=deliverable_id,
        agent_id=_required_text(agent.get("id"), "agent_id"),
    )
    return {
        "deliverable_id": deliverable_id,
        "project_id": project_id,
        "team_id": team_id,
        "artifact_key": artifact_key,
        "artifact_ref": artifact_ref,
        "artifact_space_path": artifact_ref_path,
        "artifact_space_version_id": version.get("id"),
        "artifact_space_version": version.get("version"),
        "source_paths": source_paths,
        "publish_result": publish_result,
    }


def _prepare_review_artifacts_for_lease(
    context: Any,
    *,
    lease: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    imports: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    requests = _review_artifact_ref_requests(lease)
    if not requests:
        return {
            "review_artifact_imports": imports,
            "review_artifact_import_errors": errors,
        }

    try:
        artifact_state = ArtifactSpaceStore(_runtime_dir(context)).read()
    except Exception as exc:
        for request in requests:
            errors.append({**_review_artifact_request_payload(request), "error": str(exc)})
        return {
            "review_artifact_imports": imports,
            "review_artifact_import_errors": errors,
        }

    for request in requests:
        artifact_ref = _required_text(request.get("artifact_ref"), "artifact_ref")
        try:
            artifact_space_path = _artifact_space_path_from_ref(artifact_ref)
            _require_review_artifact_space_read(
                artifact_state,
                artifact_space_path=artifact_space_path,
                subject=_required_text(request.get("subject"), "subject"),
            )
            versions = artifact_state.latest_versions_for_space(
                artifact_space_path,
                artifact_id=_TEAM_REVIEW_ARTIFACT_ID_DEFAULT,
            )
            if not versions:
                versions = artifact_state.latest_versions_for_space(artifact_space_path)
            if not versions:
                raise ValueError(
                    "artifact space has no latest published versions: "
                    f"{artifact_space_path}"
                )
        except Exception as exc:
            errors.append(
                {
                    **_review_artifact_request_payload(request),
                    "artifact_space_path": artifact_ref.removeprefix(
                        _ARTIFACT_SPACE_REF_PREFIX
                    ),
                    "error": str(exc),
                }
            )
            continue

        for version in versions:
            try:
                imports.append(
                    _import_review_artifact_version(
                        context,
                        request=request,
                        artifact_space_path=artifact_space_path,
                        artifact_id=version.artifact_id,
                        artifact_key=version.artifact_key,
                        artifact_space_version_id=version.id,
                        artifact_space_version=version.version,
                    )
                )
            except Exception as exc:
                errors.append(
                    {
                        **_review_artifact_request_payload(request),
                        "artifact_space_path": artifact_space_path,
                        "artifact_id": version.artifact_id,
                        "artifact_key": version.artifact_key,
                        "artifact_space_version_id": version.id,
                        "artifact_space_version": version.version,
                        "error": str(exc),
                    }
                )

    return {
        "review_artifact_imports": imports,
        "review_artifact_import_errors": errors,
    }


def _review_artifact_ref_requests(lease: Mapping[str, Any]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    agent = lease.get("agent")
    if not isinstance(agent, Mapping):
        return requests
    agent_id = _optional_text(agent.get("id"))
    if agent_id is None:
        return requests
    session_id = _optional_text(agent.get("session_id"))
    if session_id is None:
        return requests
    subject = f"team-agent:{agent_id}"
    for item in lease.get("inbox") or []:
        if not isinstance(item, Mapping):
            continue
        payload = item.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if _optional_text(payload.get("kind")) != "review_request":
            continue
        review_request_id = (
            _optional_text(payload.get("review_request_id"))
            or _optional_text(item.get("id"))
            or "review"
        )
        deliverable_id = _optional_text(payload.get("deliverable_id")) or "deliverable"
        for artifact_ref in _string_items(payload.get("artifact_refs")):
            if not artifact_ref.startswith(_ARTIFACT_SPACE_REF_PREFIX):
                continue
            key = (review_request_id, deliverable_id, artifact_ref)
            if key in seen:
                continue
            seen.add(key)
            requests.append(
                {
                    "review_request_id": review_request_id,
                    "deliverable_id": deliverable_id,
                    "artifact_ref": artifact_ref,
                    "inbox_item_id": _optional_text(item.get("id")),
                    "session_id": session_id,
                    "subject": subject,
                }
            )
    return requests


def _review_artifact_request_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: request[key]
        for key in (
            "review_request_id",
            "deliverable_id",
            "artifact_ref",
            "inbox_item_id",
        )
        if request.get(key) is not None
    }


def _artifact_space_path_from_ref(artifact_ref: str) -> str:
    if not artifact_ref.startswith(_ARTIFACT_SPACE_REF_PREFIX):
        raise ValueError(f"unsupported artifact ref: {artifact_ref}")
    return normalize_space_path(artifact_ref.removeprefix(_ARTIFACT_SPACE_REF_PREFIX))


def _require_review_artifact_space_read(
    artifact_state: Any,
    *,
    artifact_space_path: str,
    subject: str,
) -> None:
    space = artifact_state.spaces.get(artifact_space_path)
    if space is None:
        raise ValueError(f"artifact space does not exist: {artifact_space_path}")
    if not space.grants.allows(subject, "read"):
        raise ArtifactSpacePermissionError(
            f"subject {subject or '<anonymous>'} cannot read artifact space "
            f"{artifact_space_path}"
        )


def _import_review_artifact_version(
    context: Any,
    *,
    request: Mapping[str, Any],
    artifact_space_path: str,
    artifact_id: str,
    artifact_key: str,
    artifact_space_version_id: str,
    artifact_space_version: int,
) -> dict[str, Any]:
    review_request_id = _required_text(
        request.get("review_request_id"),
        "review_request_id",
    )
    deliverable_id = _required_text(request.get("deliverable_id"), "deliverable_id")
    destination_path = _review_artifact_destination_path(
        review_request_id=review_request_id,
        deliverable_id=deliverable_id,
        artifact_space_path=artifact_space_path,
        artifact_id=artifact_id,
        artifact_space_version_id=artifact_space_version_id,
    )
    provider = ArtifactImportPublishedEffectProvider()
    ref_version_segment = _review_artifact_ref_version_segment(
        artifact_space_path=artifact_space_path,
        artifact_space_version_id=artifact_space_version_id,
    )
    provider.initialize(
        ArtifactImportPublishedEffectDefinition(
            id=(
                "team-review-artifact-import-"
                f"{_artifact_space_path_segment(review_request_id, 'review_request_id')}-"
                f"{_artifact_space_path_segment(artifact_id, 'artifact_id')}-"
                f"{ref_version_segment}"
            ),
            display_name="Import Team review artifact",
            type="effect:artifact:import-published",
            session_id=_required_text(request.get("session_id"), "session_id"),
            key=artifact_key,
            destination_path=destination_path,
            overwrite=True,
        ),
        context,
    )
    import_result = provider.execute()
    return {
        "review_request_id": review_request_id,
        "deliverable_id": deliverable_id,
        "artifact_ref": request.get("artifact_ref"),
        "artifact_space_path": artifact_space_path,
        "artifact_id": artifact_id,
        "artifact_key": artifact_key,
        "artifact_space_version_id": artifact_space_version_id,
        "artifact_space_version": artifact_space_version,
        "destination_path": destination_path,
        "artifact_path": f"/artifacts/{destination_path}",
        "imported_paths": list(import_result.get("imported_paths") or []),
        "imported_file_count": int(import_result.get("imported_file_count") or 0),
    }


def _review_artifact_destination_path(
    *,
    review_request_id: str,
    deliverable_id: str,
    artifact_space_path: str,
    artifact_id: str,
    artifact_space_version_id: str,
) -> str:
    ref_version_segment = _review_artifact_ref_version_segment(
        artifact_space_path=artifact_space_path,
        artifact_space_version_id=artifact_space_version_id,
    )
    return "/".join(
        [
            "team-review-inputs",
            _artifact_space_path_segment(review_request_id, "review_request_id"),
            _artifact_space_path_segment(deliverable_id, "deliverable_id"),
            _artifact_space_path_segment(artifact_id, "artifact_id"),
            ref_version_segment,
        ]
    )


def _review_artifact_ref_version_segment(
    *,
    artifact_space_path: str,
    artifact_space_version_id: str,
) -> str:
    ref_hash = hashlib.sha256(artifact_space_path.encode("utf-8")).hexdigest()[:16]
    version_segment = _artifact_space_path_segment(
        artifact_space_version_id,
        "artifact_space_version_id",
    )
    return f"ref-{ref_hash}-{version_segment}"


def _artifact_space_path_segment(value: str, field_name: str) -> str:
    text = _required_text(value, field_name)
    if (
        _ARTIFACT_SPACE_SAFE_SEGMENT_RE.fullmatch(text)
        and text not in {".", ".."}
        and not text.startswith(_ARTIFACT_SPACE_ENCODED_SEGMENT_PREFIX)
    ):
        return text
    encoded = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{_ARTIFACT_SPACE_ENCODED_SEGMENT_PREFIX}{encoded}"


def _ensure_deliverable_artifact_space_version(
    runtime_dir: Path,
    *,
    space_path: str,
    artifact_key: str,
    team_id: str,
    project_id: str,
    deliverable_id: str,
    agent_id: str,
) -> dict[str, Any]:
    subject = f"team-agent:{agent_id}"
    store = ArtifactSpaceStore(runtime_dir)
    state = store.ensure_space(
        path=space_path,
        display_name=f"Deliverable {deliverable_id}",
        metadata={
            "team_id": team_id,
            "project_id": project_id,
            "deliverable_id": deliverable_id,
            "source": "team_addon",
        },
        subject=subject,
    )
    for version in state.versions_for_space(space_path, artifact_id="bundle"):
        if version.artifact_key == artifact_key:
            return version.model_dump(mode="json")
    state = store.publish_version(
        space_path=space_path,
        artifact_id="bundle",
        artifact_key=artifact_key,
        created_by_subject=subject,
        metadata={
            "team_id": team_id,
            "project_id": project_id,
            "deliverable_id": deliverable_id,
            "source": "team_addon",
        },
    )
    return state.versions_for_space(space_path, artifact_id="bundle")[-1].model_dump(mode="json")


def _append_unique_text(values: list[str], value: str) -> list[str]:
    return values if value in values else [*values, value]


def register_providers() -> ProviderPluginRegistration:
    for node_type, outputs in TEAM_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
    return ProviderPluginRegistration(
        effect_providers={
            "effect:team:wake-due-agents": TeamWakeDueAgentsEffectProvider,
            "effect:team:wake-governance-managers": TeamWakeGovernanceManagersEffectProvider,
            "effect:team:publish-outbox-events": TeamPublishOutboxEventsEffectProvider,
            "effect:team:finalize-agent-dispatch": (
                TeamFinalizeAgentDispatchEffectProvider
            ),
            "effect:team:create-operator-directive": (
                TeamCreateOperatorDirectiveEffectProvider
            ),
            "effect:team:create-case": TeamCreateCaseEffectProvider,
            "effect:team:update-case": TeamUpdateCaseEffectProvider,
            "effect:team:request-ephemeral-agents": TeamRequestEphemeralAgentsEffectProvider,
            "effect:team:mark-ephemeral-spawned": TeamMarkEphemeralSpawnedEffectProvider,
            "effect:team:accept-ephemeral-deliverable": (
                TeamAcceptEphemeralDeliverableEffectProvider
            ),
            "effect:team:mark-ephemeral-cleaned-up": (
                TeamMarkEphemeralCleanedUpEffectProvider
            ),
            "effect:team:record-git-promotion": TeamRecordGitPromotionEffectProvider,
            "effect:team:publish-review-branch": TeamPublishReviewBranchEffectProvider,
            "effect:team:mark-release-published": TeamMarkReleasePublishedEffectProvider,
            "effect:team:consolidate-git-branches": (
                TeamConsolidateGitBranchesEffectProvider
            ),
        },
        query_providers={
            "query:team:state": TeamStateQueryProvider,
            "query:team:session-routing": TeamSessionRoutingQueryProvider,
        },
        node_definition_resolvers={
            "effect:team:wake-due-agents": (
                lambda node: _materialize(TeamWakeDueAgentsEffectDefinition, node)
            ),
            "effect:team:wake-governance-managers": (
                lambda node: _materialize(
                    TeamWakeGovernanceManagersEffectDefinition,
                    node,
                )
            ),
            "effect:team:publish-outbox-events": (
                lambda node: _materialize(TeamPublishOutboxEventsEffectDefinition, node)
            ),
            "effect:team:finalize-agent-dispatch": (
                lambda node: _materialize(
                    TeamFinalizeAgentDispatchEffectDefinition,
                    node,
                )
            ),
            "effect:team:create-operator-directive": (
                lambda node: _materialize(
                    TeamCreateOperatorDirectiveEffectDefinition,
                    node,
                )
            ),
            "effect:team:create-case": (
                lambda node: _materialize(TeamCreateCaseEffectDefinition, node)
            ),
            "effect:team:update-case": (
                lambda node: _materialize(TeamUpdateCaseEffectDefinition, node)
            ),
            "effect:team:request-ephemeral-agents": (
                lambda node: _materialize(TeamRequestEphemeralAgentsEffectDefinition, node)
            ),
            "effect:team:mark-ephemeral-spawned": (
                lambda node: _materialize(TeamMarkEphemeralSpawnedEffectDefinition, node)
            ),
            "effect:team:accept-ephemeral-deliverable": (
                lambda node: _materialize(TeamAcceptEphemeralDeliverableEffectDefinition, node)
            ),
            "effect:team:mark-ephemeral-cleaned-up": (
                lambda node: _materialize(TeamMarkEphemeralCleanedUpEffectDefinition, node)
            ),
            "effect:team:record-git-promotion": (
                lambda node: _materialize(TeamRecordGitPromotionEffectDefinition, node)
            ),
            "effect:team:publish-review-branch": (
                lambda node: _materialize(TeamPublishReviewBranchEffectDefinition, node)
            ),
            "effect:team:mark-release-published": (
                lambda node: _materialize(TeamMarkReleasePublishedEffectDefinition, node)
            ),
            "effect:team:consolidate-git-branches": (
                lambda node: _materialize(TeamConsolidateGitBranchesEffectDefinition, node)
            ),
            "query:team:state": (lambda node: _materialize(TeamStateQueryDefinition, node)),
            "query:team:session-routing": (
                lambda node: _materialize(TeamSessionRoutingQueryDefinition, node)
            ),
        },
        workflow_editor_catalog_entries=_TEAM_WORKFLOW_EDITOR_CATALOG,
    )


def _team_agent_guidance_content(*, agent: Mapping[str, Any]) -> str:
    identity = _agent_guidance_identity(agent)
    display_name = identity["display_name"]
    display_line = (
        f"- Current display name: {json.dumps(display_name)}\n"
        if display_name is not None
        else "- Current display name: not configured; use your agent id when a name is needed.\n"
    )
    return (
        "# Team Add-on Guidance\n\n"
        f"Guidance version: {TEAM_AGENT_GUIDANCE_VERSION}\n\n"
        "Identity:\n"
        f"- Persistent agent id: {json.dumps(identity['agent_id'])}\n"
        f"{display_line}\n"
        "Use the Team MCP tools granted to your session. Tool discovery is the "
        "source of truth for argument shapes; do not write local coordination "
        "files, helper scripts, or finish commands.\n\n"
        "Core MCP surface:\n"
        f"- Use {TEAM_UPDATE_TASK_TOOL} for tasks you own. State, priority, title, "
        "and description edits are owner-only. Superior agents may use the same "
        "tool to claim a subordinate's non-terminal task or reassign it within "
        "the same project. Do not accept or reject someone else's task through "
        f"{TEAM_UPDATE_TASK_TOOL}.\n"
        f"- Use {TEAM_CREATE_CASE_TOOL} and {TEAM_UPDATE_CASE_TOOL} for intake, "
        "source references, priority, owner, lifecycle, and case-task links.\n"
        f"- Use {TEAM_CREATE_TASK_TOOL} or {TEAM_CREATE_TASKS_TOOL} only within "
        "the task creation mandate in your wake prompt.\n"
        f"- Use {TEAM_REQUEST_STATUS_TOOL} only for direct subordinates. Use "
        f"{TEAM_ASK_QUESTION_TOOL} and {TEAM_SEND_PROGRESS_REPORT_TOOL} for "
        "allowed directional coordination with your direct superior.\n"
        f"- Use {TEAM_ESCALATE_OPERATOR_TOOL} when you have no direct superior or "
        "the current issue genuinely needs operator attention.\n"
        f"- Use {TEAM_UPDATE_GOAL_TOOL} for project goal updates.\n"
        f"- Use {TEAM_RECORD_NOTE_TOOL} only to record a useful note; it is not a "
        "completion ritual.\n\n"
        "Each task carries derived graph context: dependency_status, "
        "child_task_ids, blocked_by_task_ids, and subtask_counts. Do not start "
        "tasks whose dependency_status is not 'satisfied'; wait until their "
        "blocked_by_task_ids clear. Decompose large work by creating child tasks "
        f"with {TEAM_CREATE_TASK_TOOL} or {TEAM_CREATE_TASKS_TOOL} instead of ad "
        "hoc checklists in descriptions. Declare ordering between tasks with "
        "dependency_task_ids.\n\n"
        "Deliverables and review:\n"
        f"- Use {TEAM_SUBMIT_DELIVERABLE_TOOL} for finished work that does not "
        "need a separate reviewer. Use "
        f"{TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL} when the deliverable should "
        "immediately be handed to a reviewer.\n"
        "- For non-Git deliverables, stage files under /artifacts and pass paths "
        f"relative to /artifacts to {TEAM_SUBMIT_DELIVERABLE_TOOL} or "
        f"{TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL}. The Team runtime publishes "
        "only those paths and records artifact refs for reviewers.\n"
        f"- Use {TEAM_REQUEST_REVIEW_TOOL} to ask a specific same-team reviewer "
        "to review a deliverable. Review artifact handoff is through "
        f"{TEAM_REQUEST_REVIEW_TOOL} or "
        f"{TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL}; do not pass local artifact "
        "paths through communication tools.\n"
        "- Deliverables may include verification with status passed, failed, "
        "partial, not_run, or unknown plus a short summary. Use not_run when "
        "test tooling or required services were unavailable.\n"
        "- When responding to a review_rejected inbox item, create a revised "
        "deliverable with supersedes_deliverable_id set to the "
        "payload's supersedes_deliverable_id.\n"
        f"- Use {TEAM_DECIDE_REVIEW_TOOL} only for review requests assigned to "
        "you. Accepting a review is what cascades to deliverable/task acceptance "
        "once all attached reviews are accepted.\n"
        "- During manager fallback, routing_gaps take priority. If a gap says an "
        "idle profile-eligible reviewer has review work, create a fresh "
        f"{TEAM_REQUEST_REVIEW_TOOL} call for that reviewer instead of accepting "
        "the review yourself. Use override_routing_gap_reason only when intentionally "
        "bypassing that guardrail.\n"
        f"- If a deliverable should be reviewed by another agent, use "
        f"{TEAM_REQUEST_REVIEW_TOOL}; do not mark the task accepted yourself "
        "unless the task is actually yours.\n\n"
        "Git and release:\n"
        f"- Use {TEAM_PUBLISH_REVIEW_BRANCH_TOOL} for review-branch promotion and "
        f"{TEAM_UPDATE_FEATURE_BRANCH_TOOL} for feature-branch promotion.\n"
        "- Deliverable git_refs are provenance only. They do not publish a review "
        f"branch by themselves; call {TEAM_PUBLISH_REVIEW_BRANCH_TOOL} only when "
        "a durable review branch is needed for reviewer access, feature "
        "promotion, release sequencing, or a superior explicitly asks for one.\n"
        f"- Release requests stand alone. Use {TEAM_REQUEST_RELEASE_TOOL} once "
        "every deliverable is accepted and no release package already exists for "
        "that release. The release request can enqueue prerequisite feature "
        "promotions from accepted deliverable git_refs.review_branch or "
        "git_refs.agent_branch before continuing to the release branch.\n"
        "- If release_packages shows an existing blocked package, do not submit "
        "another release request with that id; follow the package's "
        f"next_action_hint. Use {TEAM_RECOVER_RELEASE_TOOL} only when that hint "
        "identifies a failed release promotion and the resolved commit is pushed "
        "to an allowed agents/... ref.\n\n"
        "Inbox, chat, delegation, and controls:\n"
        "- Inbox items with item_type operator_directive are durable operator "
        "instructions that were relayed from an out-of-band channel. Handle them "
        f"with the appropriate Team MCP tools, then use {TEAM_MARK_INBOX_HANDLED_TOOL} "
        "when the item no longer needs attention.\n"
        "- Inbox items include response_requested. When response_requested is "
        "false, do not send thank-you, acknowledgement, or other no-op replies; "
        f"either take the needed Team action or mark the item handled with "
        f"{TEAM_MARK_INBOX_HANDLED_TOOL}. When response_requested is true, respond "
        "through the normal allowed directional tool if a response is needed.\n"
        "- Inbox messages may include priority low, normal, or high. Use low for "
        "FYI updates that can be batched, normal for ordinary coordination, and "
        "high only for urgent blockers. Low-priority inbox may be delivered with "
        "other work instead of waking the recipient immediately.\n"
        "- Team Chat messages are direct conversations with human operators, not "
        f"agent-to-agent inbox items. Use {TEAM_REPLY_TO_CHAT_TOOL} only during a "
        f"Team Chat wake. Use {TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL} only for "
        "human operators listed in the current Team context as operator chat participants.\n"
        f"- Use {TEAM_REQUEST_EPHEMERAL_AGENT_TOOL} or "
        f"{TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL} for scoped worker delegation, then "
        f"{TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL} or {TEAM_REJECT_EPHEMERAL_RESULT_TOOL} "
        "when a worker result is ready for your decision.\n"
        f"- Use {TEAM_STAND_DOWN_AGENT_TOOL} when you need a direct subordinate and "
        f"that subordinate's subtree to stop future Team work. Use "
        f"{TEAM_REVOKE_STAND_DOWN_TOOL} to clear a stand-down you set. Do not "
        "encode stand-downs as prose-only status requests.\n"
    )


def _team_governance_guidance_content(*, agent: Mapping[str, Any]) -> str:
    identity = _agent_guidance_identity(agent)
    display_name = identity["display_name"]
    display_line = (
        f"- Current display name: {json.dumps(display_name)}\n"
        if display_name is not None
        else "- Current display name: not configured; use your agent id when a name is needed.\n"
    )
    return (
        "# Team Add-on Governance Guidance\n\n"
        f"Guidance version: {TEAM_GOVERNANCE_GUIDANCE_VERSION}\n\n"
        "Identity:\n"
        f"- Persistent agent id: {json.dumps(identity['agent_id'])}\n"
        f"{display_line}\n"
        "Governance role:\n"
        "- You are a Team Add-on governance manager. You may oversee governance "
        "groups even when your persistent agent record has no team_id.\n"
        "- Treat each governance wake prompt as the source of current governance "
        "group, agenda, member-team summaries, cost signals, and blocker "
        "signals. Do not assume stale group or member-team state from memory.\n"
        "- Member teams remain separate execution units. Do not take ownership "
        "of member-team project tasks, deliverables, reviews, releases, or "
        "direct-report chains.\n"
        "- Use governance wakes to make policy, priority, escalation, cost, and "
        "blocked-team decisions. Route directives to the relevant member-team "
        f"manager with the {TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL} MCP tool when "
        "action is needed.\n"
        "- Mark governance agenda items handled with the "
        f"{TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL} MCP tool after you have "
        "made the needed decision, routed any directive, or determined that no "
        "action is needed.\n"
        "- Governance wakes grant governance MCP tools only. Do not use ordinary "
        "Team action tools for member-team project work from a governance wake.\n"
        "- Keep decisions concise. If no governance action is needed, say so "
        "briefly and do not invent member-team project work.\n"
    )


def _team_agent_guidance_inactive_content(*, agent: Mapping[str, Any]) -> str:
    identity = _agent_guidance_identity(agent)
    display_name = identity["display_name"]
    display_line = (
        f"- Current display name: {json.dumps(display_name)}\n"
        if display_name is not None
        else "- Current display name: not configured; use your agent id when a name is needed.\n"
    )
    return (
        "# Team Add-on Guidance\n\n"
        f"Guidance version: {TEAM_AGENT_GUIDANCE_VERSION}\n\n"
        "Identity:\n"
        f"- Persistent agent id: {json.dumps(identity['agent_id'])}\n"
        f"{display_line}\n"
        "Current Team assignment:\n"
        "- This persistent agent is not currently assigned to a Team.\n"
        "- Normal Team-agent task, deliverable, review, release, subordinate, "
        "and ordinary Team MCP tool instructions do not apply unless a future "
        "wake prompt explicitly assigns normal Team work.\n"
        "- Inactive or teamless sessions must not use ordinary Team action tools.\n"
        "- If this agent also manages governance groups, follow the separate "
        f"{TEAM_GOVERNANCE_GUIDANCE_SECTION_ID} section and the current "
        "governance wake prompt instead.\n"
    )


def _build_prompt(lease: Mapping[str, Any]) -> str:
    agent = lease["agent"]
    context = {
        "agent": _prompt_context_payload(_agent_prompt_payload(agent)),
        "reason": lease["reason"],
        "superior_id": lease["superior_agent_id"],
        "subordinate_ids": lease["subordinate_agent_ids"],
        "subordinates": _prompt_context_payload(lease.get("subordinates") or []),
        "session_health": _prompt_context_payload(lease.get("session_health") or []),
        "cases": _prompt_context_payload(lease.get("cases") or []),
        "tasks": _prompt_context_payload(lease["tasks"]),
        "inbox": _prompt_context_payload(lease["inbox"]),
        "release_packages": _prompt_context_payload(
            lease.get("release_packages") or {}
        ),
        "runtime_attention": _prompt_context_payload(
            _prompt_runtime_attention(lease.get("runtime_attention") or [])
        ),
        "operator_chat_participants": _prompt_context_payload(
            lease.get("operator_chat_participants") or []
        ),
    }
    manager_fallback_summary = lease.get("manager_fallback_summary")
    if isinstance(manager_fallback_summary, Mapping):
        prompt_summary = dict(manager_fallback_summary)
        prompt_summary.pop("latest_failed_actions", None)
        context["manager_fallback_summary"] = _prompt_context_payload(prompt_summary)
    project_repo = lease.get("project_repo")
    if isinstance(project_repo, Mapping):
        context["project_repo"] = _prompt_context_payload(dict(project_repo))
    review_artifact_imports = lease.get("review_artifact_imports")
    if isinstance(review_artifact_imports, list) and review_artifact_imports:
        context["review_artifact_imports"] = _prompt_context_payload(
            review_artifact_imports
        )
    review_artifact_import_errors = lease.get("review_artifact_import_errors")
    if isinstance(review_artifact_import_errors, list) and review_artifact_import_errors:
        context["review_artifact_import_errors"] = _prompt_context_payload(
            review_artifact_import_errors
        )
    runtime = lease.get("runtime")
    handoff_summary = (
        _optional_text(runtime.get("last_handoff_summary"))
        if isinstance(runtime, Mapping)
        else None
    )
    handoff_section = (
        "Prior handoff summary from Team state:\n"
        f"{handoff_summary}\n\n"
        if handoff_summary
        else ""
    )
    review_artifact_section = (
        "Review artifact access for this wake:\n"
        "- review_artifact_imports lists published deliverable archives already "
        "imported into this session. Inspect artifact_path or imported_paths under "
        "/artifacts instead of trying to open artifact-space refs directly.\n"
        "- If review_artifact_import_errors is present, treat the review artifact "
        "as unavailable and report the review as blocked or ask the lead to "
        "republish it.\n\n"
        if context.get("review_artifact_imports")
        or context.get("review_artifact_import_errors")
        else ""
    )
    git_release_sequence_section = (
        "Git release sequencing:\n"
        f"- Use {TEAM_REQUEST_RELEASE_TOOL} for accepted deliverables when the "
        "intent is to release. The repository will enqueue prerequisite feature "
        "promotions for accepted deliverables that still only have "
        "git_refs.review_branch or git_refs.agent_branch, then continue the "
        "release after those promotions succeed.\n"
        f"- Do not combine {TEAM_UPDATE_FEATURE_BRANCH_TOOL} with "
        f"{TEAM_REQUEST_RELEASE_TOOL}; use {TEAM_UPDATE_FEATURE_BRANCH_TOOL} only "
        "when you need a feature branch without requesting a release.\n"
        "- Release requests create new release packages only. When release_packages "
        "already contains a blocked package, follow its next_action_hint. Use "
        f"{TEAM_RECOVER_RELEASE_TOOL} only when that hint identifies a failed "
        "release promotion, after the resolved commit is available under an "
        "agents/... ref.\n\n"
    )
    manager_fallback_section = (
        "Manager fallback wake:\n"
        "- This wake means no other team member is currently wakeable while the team "
        "still has open work.\n"
        "- Treat manager_fallback_summary as the compact source of stalled team "
        "and project work, even when this manager's top-level tasks and inbox "
        "arrays are empty.\n"
        "- Start from manager_fallback_summary.routing_gaps when present; those "
        "rows identify likely assignment or review-routing fixes.\n"
        "- For reviewer_without_assigned_review_work gaps, create a fresh review "
        f"request with {TEAM_REQUEST_REVIEW_TOOL} for the listed reviewer. Omit "
        "id unless you are choosing a new unique id; never reuse the existing "
        "review request id.\n"
        "- If a pending review is assigned to you for a deliverable with a routing "
        "gap, route it to the listed reviewer instead of deciding it yourself. "
        f"Only use {TEAM_DECIDE_REVIEW_TOOL} with override_routing_gap_reason when you are "
        "intentionally accepting responsibility for bypassing that reviewer.\n"
        "- If a pending review is assigned to you and no routing gap applies, "
        f"handle it with {TEAM_DECIDE_REVIEW_TOOL}.\n"
        "- Do not return a no-op merely because your own tasks and inbox arrays are "
        "empty; use the team context to route, review, report, or escalate the stall.\n\n"
        if lease.get("reason") == "manager_fallback"
        else ""
    )
    context_json = json.dumps(context, sort_keys=True, indent=2)
    return (
        "Continue your assigned team-agent work.\n"
        f"Follow the Team Add-on guidance in {TEAM_AGENT_GUIDANCE_PATH}, managed "
        f"section {TEAM_AGENT_GUIDANCE_SECTION_ID}, version "
        f"{TEAM_AGENT_GUIDANCE_VERSION}. Use the Team context below and the MCP "
        "tools granted to this session. Tool discovery is authoritative for exact "
        "arguments.\n\n"
        "Role-aware delegation: when creating tasks or requests for subordinates, "
        "use subordinates[].role_name and subordinates[].role_description. "
        f"Reviewer roles should generally receive {TEAM_REQUEST_REVIEW_TOOL} calls "
        "or review-focused tasks, not implementation task ownership.\n\n"
        f"{_task_creation_mandate_guidance(agent)}"
        f"{_deliverable_mechanics_guidance(lease)}"
        f"{_project_repo_prompt_guidance(project_repo)}"
        f"{handoff_section}"
        f"{review_artifact_section}"
        f"{git_release_sequence_section}"
        f"{manager_fallback_section}"
        f"Team context JSON:\n{context_json}\n\n"
        "MCP action surface:\n"
        "- Call the appropriate Team MCP tool for each state change, handoff, "
        "message, review decision, release request, or note.\n"
        f"- {TEAM_RECORD_NOTE_TOOL} is only for a useful note; it is not required "
        "before finishing.\n"
        f"- Use {TEAM_MARK_INBOX_HANDLED_TOOL} for inbox items that are handled "
        "without needing a reply.\n"
    )


def _deliverable_mechanics_guidance(lease: Mapping[str, Any]) -> str:
    tasks = [
        task
        for task in lease.get("tasks") or []
        if isinstance(task, Mapping) and _optional_text(task.get("id")) is not None
    ]
    if not tasks:
        return (
            "Deliverable mechanics for this wake:\n"
            "- If no deliverable is ready, use the appropriate MCP tools for notes, "
            "questions, progress reports, or handled inbox items when useful.\n"
            "- Do not submit a placeholder deliverable or no-op acknowledgement.\n\n"
        )
    task_ids = [_required_text(task.get("id"), "task.id") for task in tasks]
    default_task_id = task_ids[0]
    return (
        "Deliverable mechanics for this wake:\n"
        f"- Valid owned task_id values for deliverable tools are: {', '.join(task_ids)}.\n"
        "- For a report deliverable, write it under /artifacts first, for example "
        f"/artifacts/team-deliverables/{default_task_id}/report.md.\n"
        "- Pass artifact paths relative to /artifacts, for example "
        f"team-deliverables/{default_task_id}/report.md. Do not use absolute paths "
        "there.\n"
        "- A non-Git deliverable must include task_id, summary, and artifact paths. "
        "If you created or updated useful files only in the repo checkout, copy or "
        "summarize the deliverable under /artifacts before reporting it.\n"
        "- Include verification.status when you ran checks or could not run them. "
        "Use verification.status=not_run with verification.summary explaining "
        "missing test tooling or unavailable services.\n"
        f"- Use {TEAM_SUBMIT_DELIVERABLE_TOOL} or "
        f"{TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL}; do not pass local artifact "
        "paths through communication tools.\n"
        "- If no deliverable is ready, use the appropriate MCP tools for notes, "
        "questions, progress reports, or handled inbox items when useful.\n\n"
    )


def _build_team_chat_prompt(
    *,
    state: Any,
    conversation: Any,
    message: Any,
    mutation_allowed: bool,
) -> str:
    agents = _object_mapping_field(state, "agents")
    conversation_messages = _object_mapping_field(state, "conversation_messages")
    agent_id = _required_text(_object_field(conversation, "agent_id"), "conversation.agent_id")
    agent = agents[agent_id]
    conversation_id = _required_text(_object_field(conversation, "id"), "conversation.id")
    operator_user_id = _required_text(
        _object_field(conversation, "operator_user_id"),
        "conversation.operator_user_id",
    )
    operator_label = (
        _optional_text(_object_field(conversation, "operator_display_name"))
        or operator_user_id
    )
    recent_messages = [
        item
        for item in conversation_messages.values()
        if _object_field(item, "conversation_id") == conversation_id
    ]
    recent_messages.sort(
        key=lambda item: (
            _object_field(item, "created_at"),
            _required_text(_object_field(item, "id"), "conversation_message.id"),
        )
    )
    transcript_lines = []
    for item in recent_messages[-12:]:
        body = str(_object_field(item, "body") or "").strip()
        if not body:
            continue
        sender = _object_field(item, "sender")
        if sender == "operator":
            sender_label = f"human operator {operator_label}"
        elif sender == "agent":
            sender_label = _team_chat_agent_display_name(agent)
        else:
            sender_label = "system"
        transcript_lines.append(f"{sender_label}: {body}")
    transcript = "\n".join(transcript_lines)
    agent_display_name = _team_chat_agent_display_name(agent)
    message_id = _required_text(_object_field(message, "id"), "message.id")
    mutation_guidance = (
        "If the operator explicitly asks you to change Team state, you may also "
        "use the ordinary Team MCP tools granted to this chat wake. Do not mutate "
        "Team state for a thank-you, acknowledgement, or no-op reply.\n\n"
        if mutation_allowed
        else "This chat wake is reply-only. Do not mutate Team state; use only "
        f"{TEAM_REPLY_TO_CHAT_TOOL}.\n\n"
    )
    return (
        "You have received a message from a human operator in the Team Add-on Chat tab.\n"
        "This is not an agent-to-agent inbox message. Treat it as the current human "
        f"operator request. Reply with the {TEAM_REPLY_TO_CHAT_TOOL} MCP tool. Do "
        f"not use {TEAM_ASK_QUESTION_TOOL} or {TEAM_SEND_PROGRESS_REPORT_TOOL} "
        "for this human chat reply; those are for agent-to-agent inbox communication. "
        f"{mutation_guidance}"
        f"Current agent: {agent_display_name} ({agent_id})\n"
        f"Human operator: {operator_label} ({operator_user_id})\n"
        f"Conversation ID: {conversation_id}\n"
        f"Human operator message ID: {message_id}\n\n"
        "Recent conversation:\n"
        f"{transcript or '(no earlier messages)'}\n\n"
        "Human operator message:\n"
        f"{_object_field(message, 'body')}"
    )


def _build_governance_prompt(lease: Mapping[str, Any]) -> str:
    agent = lease["agent"]
    context = {
        "agent": _prompt_context_payload(_agent_prompt_payload(agent)),
        "reason": lease["reason"],
        "governance_group": _prompt_context_payload(lease["governance_group"]),
        "agenda": _prompt_context_payload(lease.get("agenda") or []),
        "member_teams": _prompt_context_payload(lease.get("member_teams") or []),
        "cost_signals": _prompt_context_payload(lease.get("cost_signals") or []),
        "blocked_team_signals": _prompt_context_payload(
            lease.get("blocked_team_signals") or []
        ),
    }
    return (
        "Continue as a Team Add-on governance manager.\n"
        "You supervise member teams through governance, escalation, and policy. "
        "Do not take ownership of member-team project tasks, deliverables, "
        "reviews, or releases, and do not assume you are in their direct "
        "reporting chain. Member teams remain separate execution units.\n\n"
        "Use this wake to inspect agenda items, cost-cap signals, blocked-team "
        "signals, and member-team summaries. Respond with concise decisions, "
        "recommended operator actions, and any directives that should be routed. "
        f"The only Team MCP tools granted for this wake are "
        f"{TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL} and "
        f"{TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL}. Route directives to the "
        f"relevant member-team managers with {TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL}. "
        f"Mark agenda items handled with {TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL} "
        "after deciding, routing required follow-up, or determining no action "
        "is needed. Do not invent project work.\n\n"
        f"Governance context JSON:\n{json.dumps(context, sort_keys=True, indent=2)}\n"
    )


def _task_creation_mandate_guidance(agent: Mapping[str, Any]) -> str:
    role_permissions = {
        str(permission)
        for permission in (agent.get("effective_role_permissions") or agent.get("role_permissions") or [])
        if permission is not None
    }
    project_assignments = agent.get("project_assignments") or []
    lead_project_ids = [
        str(item.get("project_id"))
        for item in project_assignments
        if isinstance(item, Mapping) and item.get("kind") == "project_lead" and item.get("project_id")
    ]
    if RolePermission.CREATE_ROOT_TASK.value in role_permissions:
        lead_scope = (
            " as project lead for " + ", ".join(sorted(lead_project_ids))
            if lead_project_ids
            else ""
        )
        return (
            "Task creation mandate for this wake: your effective permissions"
            f"{lead_scope} include {RolePermission.CREATE_ROOT_TASK.value}. You may create top-level "
            f"tasks with {TEAM_CREATE_TASK_TOOL} or {TEAM_CREATE_TASKS_TOOL} by omitting "
            "parent_task_id when you otherwise have authority to create the task. "
            "You may also create child tasks with "
            "parent_task_id.\n\n"
        )
    return (
        "Task creation mandate for this wake: your effective permissions do not "
        f"have {RolePermission.CREATE_ROOT_TASK.value}. You may NOT create "
        f"top-level tasks. Every {TEAM_CREATE_TASK_TOOL} or "
        f"{TEAM_CREATE_TASKS_TOOL} call must set parent_task_id to one of your "
        "own active tasks.\n\n"
    )


def _agent_prompt_payload(agent: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(agent)
    payload.pop("display_name", None)
    return payload


def _prompt_context_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            _prompt_context_key(str(key)): _prompt_context_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_prompt_context_payload(item) for item in value]
    return value


def _prompt_runtime_attention(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if not (isinstance(item, Mapping) and item.get("kind") == "failed_action_record")
    ]


def _prompt_context_key(key: str) -> str:
    exact = {
        "agent_id": "agent",
        "agent_ids": "agents",
        "active_dispatch_id": "active_wake",
        "completed_dispatch_id": "completed_wake",
        "delivery_dispatch_id": "delivery_wake",
        "dispatch_id": "wake",
        "feedback_delivered_by_dispatch_id": "feedback_delivered_by_wake",
        "requested_dispatch_id": "requested_wake",
        "source_dispatch_id": "source_wake",
    }
    if key in exact:
        return exact[key]
    if key.endswith("_agent_ids"):
        return f"{key[:-len('_agent_ids')]}_ids"
    if key.endswith("_agent_id"):
        return f"{key[:-len('_agent_id')]}_id"
    if key.endswith("_dispatch_id"):
        return f"{key[:-len('_dispatch_id')]}_wake"
    if "agent_id" in key:
        return key.replace("agent_id", "agent")
    if "dispatch_id" in key:
        return key.replace("dispatch_id", "wake")
    return key


def _object_field(item: object, field_name: str) -> object:
    if isinstance(item, Mapping):
        return item.get(field_name)
    return getattr(item, field_name, None)


def _object_mapping_field(item: object, field_name: str) -> Mapping[str, Any]:
    value = _object_field(item, field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _team_chat_agent_display_name(agent: object) -> str:
    return (
        _optional_text(_object_field(agent, "display_name"))
        or _required_text(_object_field(agent, "id"), "agent.id")
    )


def _project_repo_prompt_guidance(project_repo: object) -> str:
    if not isinstance(project_repo, Mapping):
        return ""
    target_dir = _optional_text(project_repo.get("target_dir"))
    if target_dir is None:
        return ""
    return (
        f"Project repository: work in the prepared guest checkout at {target_dir}. "
        "The project host checkout is not mounted in the guest; use this guest "
        "repo and its configured Git helpers for code changes.\n\n"
    )


def _prepare_project_repo(
    context: Any,
    *,
    state: Any,
    agent: Mapping[str, Any],
) -> dict[str, Any] | None:
    projects = getattr(state, "projects", None)
    if not isinstance(projects, Mapping):
        return None

    def _value(item: object, field_name: str) -> object:
        if isinstance(item, Mapping):
            return item.get(field_name)
        return getattr(item, field_name, None)

    project_id: str | None = None
    git: object | None = None
    for candidate_project_id in _string_items(agent.get("project_ids")):
        project = projects.get(candidate_project_id)
        if project is None:
            continue
        candidate_git = _value(project, "git")
        if candidate_git is None:
            continue
        project_id = candidate_project_id
        git = candidate_git
        break
    if git is None:
        return None

    session_id = _required_text(agent.get("session_id"), "session_id")
    repo_source = _required_text(_value(git, "host_repo_path"), "project.git.host_repo_path")
    clone_branch = _optional_text(_value(git, "base_branch"))
    provider = AgentCloneRepoEffectProvider()
    provider.initialize(
        AgentCloneRepoEffectDefinition(
            id="team-prepare-project-repo",
            display_name="Prepare Team project repository",
            type="effect:agent:clone-repo",
            session_id=session_id,
            repo_source=repo_source,
            canonical_origin_url=_optional_text(_value(git, "canonical_origin_url")),
            bare_repo_name=_optional_text(_value(git, "bare_repo_name")),
            bare_repo_path=_optional_text(_value(git, "bare_repo_path")),
            clone_branch=clone_branch,
            vm_user="agent",
        ),
        context,
    )
    clone_result = provider.execute()
    if not bool(clone_result.get("success")):
        message = (
            _optional_text(clone_result.get("message"))
            or _optional_text(clone_result.get("error"))
            or "failed to prepare Team project repository"
        )
        raise RuntimeError(message)
    project_repo = _project_repo_payload(
        project_id=project_id,
        clone_result=clone_result,
        base_branch=clone_branch,
    )
    _mark_project_repo_prepared(context, session_id=session_id, project_repo=project_repo)
    return project_repo


def _project_repo_payload(
    *,
    project_id: str,
    clone_result: Mapping[str, Any],
    base_branch: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prepared": True,
        "project_id": project_id,
    }
    for key in (
        "target_dir",
        "guest_repo_name",
        "remote_url",
        "pr_notes_ref",
        "execution_backend",
        "vm_name",
        "message",
    ):
        value = _optional_text(clone_result.get(key))
        if value is not None:
            payload[key] = value
    if base_branch is not None:
        payload["base_branch"] = base_branch
    return payload


def _project_repo_prepare_record(
    *,
    agent: Mapping[str, Any],
    dispatch_id: str,
    project_repo: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "agent_id": agent.get("id"),
        "session_id": agent.get("session_id"),
        "project_ids": agent.get("project_ids") or [],
        "dispatch_id": dispatch_id,
    }
    if project_repo is not None:
        record["project_repo"] = dict(project_repo)
        target_dir = _optional_text(project_repo.get("target_dir"))
        if target_dir is not None:
            record["target_dir"] = target_dir
    if error is not None:
        record["error"] = error
    return record


def _seed_team_agent_guidance(
    context: Any,
    *,
    agent: Mapping[str, Any],
) -> dict[str, Any]:
    session_id = _required_text(agent.get("session_id"), "session_id")
    identity = _agent_guidance_identity(agent)
    metadata = _session_metadata(context, session_id=session_id)
    if _metadata_has_current_agent_guidance(metadata, agent=agent):
        return {
            "seeded": False,
            "reason": "already_seeded",
            "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
            "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
            "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
            **identity,
        }

    provider = AgentCopyFilesEffectProvider()
    provider.initialize(
        AgentCopyFilesEffectDefinition(
            id="team-seed-agent-guidance",
            display_name="Seed Team agent guidance",
            type="effect:agent:copy-files",
            session_id=session_id,
            vm_user="agent",
            files=[
                {
                    "content": _team_agent_guidance_content(agent=agent),
                    "destination_path": TEAM_AGENT_GUIDANCE_PATH,
                    "mode": "0644",
                    "overwrite": True,
                    "merge_strategy": "managed_section",
                    "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
                    "remove_section_ids": list(LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS),
                }
            ],
        ),
        context,
    )
    copy_result = provider.execute()
    if not bool(copy_result.get("success")):
        message = str(copy_result.get("message") or copy_result.get("error") or "").strip()
        raise RuntimeError(message or "failed to seed Team agent guidance")

    copied_vm_ip = _optional_text(copy_result.get("vm_ip"))
    _mark_agent_guidance_seeded(
        context,
        session_id=session_id,
        agent=agent,
        copy_result=copy_result,
        vm_ip=copied_vm_ip,
    )
    return {
        "seeded": True,
        "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
        "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
        "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
        **identity,
        "vm_ip": copied_vm_ip,
        "copied_paths": list(copy_result.get("copied_paths") or []),
    }


def _seed_team_governance_guidance(
    context: Any,
    *,
    agent: Mapping[str, Any],
) -> dict[str, Any]:
    session_id = _required_text(agent.get("session_id"), "session_id")
    identity = _agent_guidance_identity(agent)
    metadata = _session_metadata(context, session_id=session_id)
    if _metadata_has_current_governance_guidance(metadata, agent=agent):
        return {
            "seeded": False,
            "reason": "already_seeded",
            "guidance_version": TEAM_GOVERNANCE_GUIDANCE_VERSION,
            "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
            "section_id": TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
            **identity,
        }

    files: list[dict[str, Any]] = []
    inactive_team_guidance = _optional_text(agent.get("team_id")) is None
    if inactive_team_guidance:
        files.append(
            {
                "content": _team_agent_guidance_inactive_content(agent=agent),
                "destination_path": TEAM_AGENT_GUIDANCE_PATH,
                "mode": "0644",
                "overwrite": True,
                "merge_strategy": "managed_section",
                "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
                "remove_section_ids": list(LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS),
            }
        )
    files.append(
        {
            "content": _team_governance_guidance_content(agent=agent),
            "destination_path": TEAM_AGENT_GUIDANCE_PATH,
            "mode": "0644",
            "overwrite": True,
            "merge_strategy": "managed_section",
            "section_id": TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
        }
    )

    provider = AgentCopyFilesEffectProvider()
    provider.initialize(
        AgentCopyFilesEffectDefinition(
            id="team-seed-governance-guidance",
            display_name="Seed Team governance guidance",
            type="effect:agent:copy-files",
            session_id=session_id,
            vm_user="agent",
            files=files,
        ),
        context,
    )
    copy_result = provider.execute()
    if not bool(copy_result.get("success")):
        message = str(copy_result.get("message") or copy_result.get("error") or "").strip()
        raise RuntimeError(message or "failed to seed Team governance guidance")

    copied_vm_ip = _optional_text(copy_result.get("vm_ip"))
    _mark_governance_guidance_seeded(
        context,
        session_id=session_id,
        agent=agent,
        copy_result=copy_result,
        vm_ip=copied_vm_ip,
        inactive_team_guidance=inactive_team_guidance,
    )
    return {
        "seeded": True,
        "guidance_version": TEAM_GOVERNANCE_GUIDANCE_VERSION,
        "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
        "section_id": TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
        **identity,
        "vm_ip": copied_vm_ip,
        "copied_paths": list(copy_result.get("copied_paths") or []),
    }


def _metadata_has_current_agent_guidance(
    metadata: Mapping[str, Any],
    *,
    agent: Mapping[str, Any],
) -> bool:
    team_addon = metadata.get("team_addon")
    if not isinstance(team_addon, Mapping):
        return False
    guidance = team_addon.get("agent_guidance")
    if not isinstance(guidance, Mapping):
        return False
    if guidance.get("inactive") is True:
        return False
    identity = _agent_guidance_identity(agent)
    return (
        int(guidance.get("guidance_version") or 0) == TEAM_AGENT_GUIDANCE_VERSION
        and guidance.get("guidance_path") == TEAM_AGENT_GUIDANCE_PATH
        and guidance.get("section_id") == TEAM_AGENT_GUIDANCE_SECTION_ID
        and guidance.get("agent_id") == identity["agent_id"]
        and guidance.get("display_name") == identity["display_name"]
        and guidance.get("seeded") is True
    )


def _metadata_has_current_governance_guidance(
    metadata: Mapping[str, Any],
    *,
    agent: Mapping[str, Any],
) -> bool:
    team_addon = metadata.get("team_addon")
    if not isinstance(team_addon, Mapping):
        return False
    guidance = team_addon.get("governance_agent_guidance")
    if not isinstance(guidance, Mapping):
        return False
    identity = _agent_guidance_identity(agent)
    governance_guidance_is_current = (
        int(guidance.get("guidance_version") or 0) == TEAM_GOVERNANCE_GUIDANCE_VERSION
        and guidance.get("guidance_path") == TEAM_AGENT_GUIDANCE_PATH
        and guidance.get("section_id") == TEAM_GOVERNANCE_GUIDANCE_SECTION_ID
        and guidance.get("agent_id") == identity["agent_id"]
        and guidance.get("display_name") == identity["display_name"]
        and guidance.get("seeded") is True
    )
    if not governance_guidance_is_current:
        return False
    if _optional_text(agent.get("team_id")) is None:
        return _metadata_has_current_inactive_agent_guidance(metadata, agent=agent)
    return True


def _metadata_has_current_inactive_agent_guidance(
    metadata: Mapping[str, Any],
    *,
    agent: Mapping[str, Any],
) -> bool:
    team_addon = metadata.get("team_addon")
    if not isinstance(team_addon, Mapping):
        return False
    guidance = team_addon.get("agent_guidance")
    if not isinstance(guidance, Mapping):
        return False
    identity = _agent_guidance_identity(agent)
    return (
        int(guidance.get("guidance_version") or 0) == TEAM_AGENT_GUIDANCE_VERSION
        and guidance.get("guidance_path") == TEAM_AGENT_GUIDANCE_PATH
        and guidance.get("section_id") == TEAM_AGENT_GUIDANCE_SECTION_ID
        and guidance.get("agent_id") == identity["agent_id"]
        and guidance.get("display_name") == identity["display_name"]
        and guidance.get("seeded") is True
        and guidance.get("inactive") is True
        and guidance.get("inactive_reason") == "no_team_assignment"
    )


def _agent_guidance_identity(agent: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        "agent_id": _required_text(agent.get("id"), "agent.id"),
        "display_name": _optional_text(agent.get("display_name")),
    }


def _session_metadata(context: Any, *, session_id: str) -> dict[str, Any]:
    lookup = getattr(context, "session_lookup", None)
    read_all = getattr(lookup, "read_all_sessions", None)
    if not callable(read_all):
        return {}
    sessions = read_all()
    if not isinstance(sessions, Mapping):
        return {}
    session = sessions.get(session_id)
    if session is None:
        return {}
    metadata = None
    if isinstance(session, Mapping):
        metadata = session.get("metadata")
    else:
        metadata = getattr(session, "metadata", None)
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _mark_agent_guidance_seeded(
    context: Any,
    *,
    session_id: str,
    agent: Mapping[str, Any],
    copy_result: Mapping[str, Any],
    vm_ip: str | None,
) -> None:
    updater = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(updater, "session_update_metadata", None)
    if not callable(update_fn):
        return
    team_metadata: dict[str, Any] = {
        "agent_guidance": {
            "seeded": True,
            "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
            "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
            "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
            **_agent_guidance_identity(agent),
            "inactive": False,
            "inactive_reason": None,
            "copied_paths": list(copy_result.get("copied_paths") or []),
        }
    }
    if vm_ip:
        team_metadata["last_known_vm_ip"] = vm_ip
    update_fn(
        target=session_id,
        metadata={"team_addon": team_metadata},
    )


def _mark_governance_guidance_seeded(
    context: Any,
    *,
    session_id: str,
    agent: Mapping[str, Any],
    copy_result: Mapping[str, Any],
    vm_ip: str | None,
    inactive_team_guidance: bool,
) -> None:
    updater = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(updater, "session_update_metadata", None)
    if not callable(update_fn):
        return
    team_metadata: dict[str, Any] = {
        "governance_agent_guidance": {
            "seeded": True,
            "guidance_version": TEAM_GOVERNANCE_GUIDANCE_VERSION,
            "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
            "section_id": TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
            **_agent_guidance_identity(agent),
            "copied_paths": list(copy_result.get("copied_paths") or []),
        }
    }
    if inactive_team_guidance:
        team_metadata["agent_guidance"] = {
            "seeded": True,
            "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
            "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
            "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
            **_agent_guidance_identity(agent),
            "inactive": True,
            "inactive_reason": "no_team_assignment",
            "copied_paths": list(copy_result.get("copied_paths") or []),
        }
    if vm_ip:
        team_metadata["last_known_vm_ip"] = vm_ip
    update_fn(
        target=session_id,
        metadata={"team_addon": team_metadata},
    )


def _mark_project_repo_prepared(
    context: Any,
    *,
    session_id: str,
    project_repo: Mapping[str, Any],
) -> None:
    updater = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(updater, "session_update_metadata", None)
    if not callable(update_fn):
        return
    update_fn(
        target=session_id,
        metadata={"team_addon": {"project_repo": dict(project_repo)}},
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2)


def _mirror_session_metadata(
    context: Any,
    *,
    repo: TeamStateRepository,
    state: Any,
    agent_id: str,
    session_id: str,
) -> None:
    updater = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(updater, "session_update_metadata", None)
    if not callable(update_fn):
        return
    update_fn(
        target=session_id,
        metadata=repo.mirror_session_metadata_payload(state=state, agent_id=agent_id),
    )


def _cost_guard_blocked_teams(
    context: Any,
    *,
    state: Any,
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
) -> list[dict[str, Any]]:
    candidate_team_ids = _cost_guard_candidate_team_ids(
        state,
        team_id=team_id,
        project_id=project_id,
        agent_id=agent_id,
    )
    limited_team_ids = {
        candidate_team_id
        for candidate_team_id in candidate_team_ids
        if _number_or_none(getattr(state.teams.get(candidate_team_id), "max_cost_usd", None))
        is not None
    }
    if not limited_team_ids:
        return []

    service = TeamCostService(
        runtime_dir=_runtime_dir(context),
        state=state,
        session_repository=_session_repository_from_context(context),
    )
    cost_payload = service.costs_payload(backfill_transcripts=True)
    team_costs = cost_payload.get("team_costs")
    team_costs = team_costs if isinstance(team_costs, Mapping) else {}

    blocked: list[dict[str, Any]] = []
    for blocked_team_id in sorted(limited_team_ids):
        team = state.teams.get(blocked_team_id)
        if team is None:
            continue
        cost = team_costs.get(blocked_team_id)
        cost = cost if isinstance(cost, Mapping) else {}
        max_cost = _number_or_none(cost.get("max_cost_usd"))
        if max_cost is None:
            max_cost = _number_or_none(getattr(team, "max_cost_usd", None))
        if max_cost is None:
            continue
        estimated_cost = _number_or_zero(cost.get("estimated_cost"))
        limit_reached = bool(cost.get("cost_limit_reached"))
        if not limit_reached:
            limit_reached = estimated_cost >= max_cost
        if not limit_reached:
            continue
        blocked.append(
            {
                "team_id": blocked_team_id,
                "team_name": getattr(team, "name", blocked_team_id),
                "estimated_cost": estimated_cost,
                "max_cost_usd": max_cost,
                "remaining_cost_usd": _number_or_none(cost.get("remaining_cost_usd")),
                "cost_limit_basis": cost.get("cost_limit_basis") or "lifetime",
                "coverage_status": cost.get("coverage_status"),
                "coverage_reasons": list(cost.get("coverage_reasons") or []),
            }
        )
    return blocked


def _cost_guard_candidate_team_ids(
    state: Any,
    *,
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
) -> set[str]:
    candidate_team_ids = set(state.teams)
    if team_id is not None:
        candidate_team_ids &= {team_id}
    if project_id is not None:
        project = state.projects.get(project_id)
        candidate_team_ids &= {project.team_id} if project is not None else set()
    if agent_id is not None:
        agent = state.agents.get(agent_id)
        candidate_team_ids &= {agent.team_id} if agent is not None else set()
    return candidate_team_ids


def _session_repository_from_context(context: Any) -> Any | None:
    repository = getattr(context, "session_lookup", None)
    if repository is not None:
        return repository
    repository = getattr(context, "session_repository", None)
    if repository is not None:
        return repository
    runtime = getattr(context, "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    if repository is not None:
        return repository
    service = getattr(context, "admin_service", None)
    service_runtime = getattr(service, "runtime", None)
    return getattr(service_runtime, "session_repository", None)


def _session_records_by_id(repository: Any | None) -> dict[str, Any] | None:
    if repository is None:
        return None
    readers = (
        getattr(repository, "read_all_sessions", None),
        getattr(repository, "read_all_safe", None),
        getattr(repository, "read_all", None),
    )
    for reader in readers:
        if not callable(reader):
            continue
        try:
            records = reader()
        except Exception:
            continue
        if isinstance(records, Mapping):
            return {str(key): value for key, value in records.items()}
    list_sessions = getattr(repository, "list_sessions", None)
    if callable(list_sessions):
        try:
            sessions = list_sessions()
        except Exception:
            return None
        return {
            str(getattr(session, "session_id")): session
            for session in sessions
            if getattr(session, "session_id", None)
        }
    return None


def _prompt_session_health_agent_ids(
    state: TeamState,
    agent: Mapping[str, Any],
) -> set[str]:
    agent_id = _optional_text(agent.get("id"))
    if agent_id is None:
        return set()
    visible_ids = {agent_id}
    state_agent = state.agents.get(agent_id)

    subordinate_stack = list(_string_items(agent.get("subordinate_agent_ids")))
    if state_agent is not None:
        subordinate_stack.extend(state_agent.subordinate_agent_ids)
    while subordinate_stack:
        subordinate_id = subordinate_stack.pop()
        if subordinate_id in visible_ids:
            continue
        subordinate = state.agents.get(subordinate_id)
        if subordinate is None:
            continue
        visible_ids.add(subordinate_id)
        subordinate_stack.extend(subordinate.subordinate_agent_ids)

    visible_project_ids = set(_string_items(agent.get("lead_project_ids")))
    managed_team_ids: set[str] = set()
    for team in state.teams.values():
        if team.manager_agent_id == agent_id:
            managed_team_ids.add(team.id)
    for project in state.projects.values():
        if project.lead_agent_id == agent_id or project.team_id in managed_team_ids:
            visible_project_ids.add(project.id)
    if managed_team_ids:
        for candidate in state.agents.values():
            if candidate.team_id in managed_team_ids:
                visible_ids.add(candidate.id)
    for assignment in state.project_assignments.values():
        if assignment.project_id in visible_project_ids:
            visible_ids.add(assignment.agent_id)
    return visible_ids


def _session_repository_for_manifest_updates(context: Any) -> Any | None:
    candidates = [
        getattr(context, "session_repository", None),
        getattr(context, "session_lookup", None),
        getattr(getattr(context, "runtime", None), "session_repository", None),
        getattr(getattr(getattr(context, "admin_service", None), "runtime", None), "session_repository", None),
    ]
    for repository in candidates:
        if repository is None:
            continue
        if callable(getattr(repository, "get_session", None)) and callable(
            getattr(repository, "upsert_session", None)
        ):
            return repository
    return None


def _ordinary_team_tools_for_agent(agent: Mapping[str, Any]) -> list[str]:
    if _optional_text(agent.get("team_id")) is None:
        return []
    return list(ORDINARY_TEAM_MCP_TOOL_NAMES)


def _team_chat_mutation_allowed(message: object) -> bool:
    metadata = _object_field(message, "metadata")
    if not isinstance(metadata, Mapping):
        return True
    for key in (
        "allow_team_mutations",
        "allow_team_mutation",
        "mutation_allowed",
        "team_mutation_allowed",
    ):
        if key in metadata:
            return bool(metadata[key])
    return True


def _set_session_team_mcp_tools(
    context: Any,
    *,
    session_id: object,
    tool_names: list[str] | tuple[str, ...],
    dispatch_id: object | None = None,
) -> dict[str, Any]:
    normalized_session_id = _required_text(session_id, "session_id")
    requested_tools = _unique_requested_tools(tool_names)
    normalized_dispatch_id = _optional_text(dispatch_id)
    result: dict[str, Any] = {
        "session_id": normalized_session_id,
        "requested_tools": requested_tools,
        "already_allowed_tools": [],
        "granted_tools": [],
        "removed_tools": [],
        "error": None,
    }
    repository = _session_repository_for_manifest_updates(context)
    if repository is None:
        result["error"] = "session repository is not available"
        return result
    session = repository.get_session(normalized_session_id)
    if session is None:
        result["error"] = f"session {normalized_session_id} was not found"
        return result
    allowed_values = [tool_name_value(tool) for tool in getattr(session, "allowed_tools", [])]
    managed_tools = set(MCP_TOOL_NAMES)
    existing_team_tools = [tool for tool in allowed_values if tool in managed_tools]
    active_scope_tools = _active_team_mcp_tools_for_other_dispatches(
        context,
        session_id=normalized_session_id,
        dispatch_id=normalized_dispatch_id,
    )
    effective_team_tools = _unique_requested_tools(
        [*requested_tools, *active_scope_tools]
    )
    effective_team_tool_set = set(effective_team_tools)
    preserved_tools: list[str] = []
    seen_preserved: set[str] = set()
    for tool in allowed_values:
        if tool in managed_tools or tool in seen_preserved:
            continue
        preserved_tools.append(tool)
        seen_preserved.add(tool)
    next_allowed_values = preserved_tools + effective_team_tools
    already_allowed = [tool for tool in requested_tools if tool in existing_team_tools]
    granted = [tool for tool in requested_tools if tool not in existing_team_tools]
    removed = [tool for tool in existing_team_tools if tool not in effective_team_tool_set]
    if next_allowed_values != allowed_values:
        if callable(getattr(session, "model_copy", None)):
            updated_session = session.model_copy(update={"allowed_tools": next_allowed_values})
        else:
            setattr(session, "allowed_tools", next_allowed_values)
            updated_session = session
        repository.upsert_session(updated_session)
    result["already_allowed_tools"] = already_allowed
    result["granted_tools"] = granted
    result["removed_tools"] = removed
    return result


def _active_team_mcp_tools_for_other_dispatches(
    context: Any,
    *,
    session_id: str,
    dispatch_id: str | None,
) -> list[str]:
    try:
        state = TeamStateRepository(_runtime_dir(context)).read()
    except Exception:
        return []

    now = datetime.now(UTC)
    tool_names: list[str] = []
    for agent_id, runtime in sorted(state.agent_runtimes.items()):
        active_dispatch_id = _optional_text(getattr(runtime, "active_dispatch_id", None))
        if active_dispatch_id is None or active_dispatch_id == dispatch_id:
            continue
        if not _team_runtime_lease_active(runtime, now=now):
            continue
        agent = state.agents.get(agent_id)
        if agent is None or getattr(agent, "session_id", None) != session_id:
            continue
        if _optional_text(getattr(runtime, "wake_reason", None)) == "team_chat":
            tool_names.extend(CHAT_TEAM_MCP_TOOL_NAMES)
        else:
            tool_names.extend(
                _ordinary_team_tools_for_agent(
                    {"team_id": getattr(agent, "team_id", None)}
                )
            )

    for group_id, runtime in sorted(state.governance_runtimes.items()):
        active_dispatch_id = _optional_text(getattr(runtime, "active_dispatch_id", None))
        if active_dispatch_id is None or active_dispatch_id == dispatch_id:
            continue
        if not _team_runtime_lease_active(runtime, now=now):
            continue
        group = state.governance_groups.get(group_id)
        if group is None:
            continue
        agent = state.agents.get(group.manager_agent_id)
        if agent is None or getattr(agent, "session_id", None) != session_id:
            continue
        tool_names.extend(GOVERNANCE_TEAM_MCP_TOOL_NAMES)

    return _unique_requested_tools(tool_names)


def _team_runtime_lease_active(runtime: Any, *, now: datetime) -> bool:
    wake_leased_until = getattr(runtime, "wake_leased_until", None)
    if wake_leased_until is None:
        return False
    if isinstance(wake_leased_until, str):
        lease_text = wake_leased_until.strip()
        if not lease_text:
            return False
        try:
            wake_leased_until = datetime.fromisoformat(
                lease_text.replace("Z", "+00:00")
            )
        except ValueError:
            return False
    if not isinstance(wake_leased_until, datetime):
        return False
    if wake_leased_until.tzinfo is None:
        wake_leased_until = wake_leased_until.replace(tzinfo=UTC)
    return wake_leased_until > now


def _unique_requested_tools(tool_names: list[str] | tuple[str, ...]) -> list[str]:
    requested: list[str] = []
    seen: set[str] = set()
    for tool in (str(tool_name or "").strip() for tool_name in tool_names):
        if not tool or tool in seen:
            continue
        seen.add(tool)
        requested.append(tool)
    return requested


def _register_team_dispatch_model_usage_context(
    context: Any,
    *,
    agent: Mapping[str, Any],
    dispatch_id: object,
    effect_id: str | None,
) -> None:
    register_model_usage_correlation_context(
        runtime_dir=_runtime_dir(context),
        session_id=_optional_text(agent.get("session_id")),
        dispatch_id=_optional_text(dispatch_id),
        workflow_run_id=_context_metadata_text(
            context,
            "workflow_run_id",
            "workflow_trace_id",
            "trace_id",
        ),
        effect_id=effect_id,
        team_id=_optional_text(agent.get("team_id")),
        project_id=(_string_items(agent.get("project_ids"))[0] if _string_items(agent.get("project_ids")) else None),
        agent_id=_optional_text(agent.get("id")),
        trace_id=_context_metadata_text(context, "trace_id", "workflow_trace_id"),
    )


def _context_metadata_text(context: Any, *keys: str) -> str | None:
    metadata = getattr(context, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    for key in keys:
        text = _optional_text(metadata.get(key))
        if text:
            return text
    return None


def _record_wake_failure(
    repo: TeamStateRepository,
    *,
    lease: Mapping[str, Any],
    error: str,
) -> Any:
    state, _result = repo.apply_agent_action(
        action={},
        session_id=_required_text(lease["agent"].get("session_id"), "session_id"),
        dispatch_id=_required_text(lease.get("dispatch_id"), "dispatch_id"),
        valid=False,
        error=error,
    )
    return state


def _publish_team_events(context: Any, events: Any) -> list[str]:
    if not isinstance(events, list):
        return []
    bus = _internal_event_bus(context)
    scheduled_store = _scheduled_internal_event_store(context)
    published_ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        delay_seconds = _optional_float(event.get("delay_seconds"), default=None)
        if delay_seconds is not None and delay_seconds > 0:
            scheduled, _created = scheduled_store.schedule_event(
                event_type=_required_text(event.get("event_type"), "event_type"),
                payload=dict(payload),
                correlation_key=_optional_text(event.get("correlation_key")),
                run_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                idempotency_key=_optional_text(event.get("idempotency_key")),
                source=InternalEventSource(
                    source_type="team-addon",
                    source_name="team-addon",
                ),
            )
            published_ids.append(scheduled.schedule_id)
            continue
        published = bus.publish(
            event_type=_required_text(event.get("event_type"), "event_type"),
            payload=dict(payload),
            correlation_key=_optional_text(event.get("correlation_key")),
            idempotency_key=_optional_text(event.get("idempotency_key")),
            source=InternalEventSource(source_type="team-addon", source_name="team-addon"),
        )
        published_ids.append(published.event_id)
    return published_ids


def _internal_event_bus(context: Any) -> InternalEventBus:
    runtime_dir = _runtime_dir(context)
    metadata = getattr(context, "metadata", {})
    event_path = None
    journal_path = None
    if isinstance(metadata, Mapping):
        event_path = metadata.get("internal_event_store_path")
        journal_path = metadata.get("workflow_event_journal_path")
    return InternalEventBus(
        Path(event_path) if event_path else internal_event_store_path(runtime_dir),
        journal_path=Path(journal_path) if journal_path else None,
    )


def _scheduled_internal_event_store(context: Any) -> ScheduledInternalEventStore:
    runtime_dir = _runtime_dir(context)
    metadata = getattr(context, "metadata", {})
    store_path = None
    if isinstance(metadata, Mapping):
        store_path = metadata.get("scheduled_internal_event_store_path")
    return ScheduledInternalEventStore(
        Path(store_path) if store_path else scheduled_internal_event_store_path(runtime_dir)
    )


def _materialize(model_class: type[Any], node: WorkflowNodeEnvelope) -> Any:
    config = dict(node.config)
    if (
        "outputs" in config
        and "result_outputs" not in config
        and "result_outputs" in getattr(model_class, "model_fields", {})
    ):
        config["result_outputs"] = config.pop("outputs")
    return model_class.model_validate(
        {
            "id": node.id,
            "display_name": node.display_name,
            "type": node.type,
            "outputs": node.outputs,
            **config,
        }
    )


def _definition_payload(definition: Any, *field_names: str) -> dict[str, Any]:
    return {
        field_name: getattr(definition, field_name)
        for field_name in field_names
        if definition is not None and hasattr(definition, field_name)
    }


def _configured_definition_fields(definition: Any) -> set[str]:
    fields_set = getattr(definition, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(definition, "__fields_set__", None)
    if isinstance(fields_set, set):
        return {str(field) for field in fields_set}
    return set()


def _payload_field_configured(
    field_name: str,
    *,
    definition_fields: set[str],
    resolved_fields: set[str],
) -> bool:
    return field_name in definition_fields or field_name in resolved_fields


def _case_effect_success_result(
    state: TeamState,
    *,
    case_id: str | None,
) -> dict[str, Any]:
    case = state.cases.get(case_id or "")
    case_payload = case.model_dump(mode="json") if case is not None else None
    return {
        "success": case is not None,
        "revision": state.revision,
        "case_id": case.id if case is not None else case_id,
        "team_id": case.team_id if case is not None else None,
        "project_id": case.project_id if case is not None else None,
        "task_ids": list(case.task_ids) if case is not None else [],
        "case": case_payload,
        "error": None if case is not None else "case was not found after mutation",
    }


def _case_effect_error_result(
    *,
    revision: int,
    case_id: str | None,
    team_id: str | None,
    project_id: str | None,
    error: str,
    expected_revision: int | None = None,
    current_revision: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "revision": revision,
        "case_id": case_id,
        "team_id": team_id,
        "project_id": project_id,
        "task_ids": [],
        "case": None,
        "error": error,
    }
    if expected_revision is not None:
        result["expected_revision"] = expected_revision
    if current_revision is not None:
        result["current_revision"] = current_revision
    return result


def _result_outputs_payload(payload: Mapping[str, Any]) -> Any:
    if "result_outputs" in payload:
        return payload.get("result_outputs")
    return payload.get("outputs")


def _runtime_dir(context: Any) -> Path:
    resolved = getattr(context, "resolved_config", None)
    runtime_dir = getattr(resolved, "runtime_dir", None)
    if runtime_dir is None:
        raise RuntimeError("team add-on provider requires resolved_config.runtime_dir")
    return Path(runtime_dir)


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {"result": value}


def _result_reports_failure(result: Mapping[str, Any]) -> bool:
    if "success" not in result:
        return False
    return not _truthy_result_success(result.get("success"))


def _truthy_result_success(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
        return value != 0
    text = str(value).strip().lower()
    if text in {"", "0", "false", "no", "off"}:
        return False
    return True


def _resume_failure_message(result: Mapping[str, Any]) -> str:
    return (
        _optional_text(result.get("message"))
        or _optional_text(result.get("error"))
        or "resume_agent failed"
    )


def _filtered_team_state_payload(
    state: Any,
    *,
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
    session_records: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    filtered = team_state_payload(
        state,
        include_agent_avatars=False,
        session_records=session_records,
    )
    teams = filtered["teams"]
    projects = filtered["projects"]
    agents = filtered["agents"]
    project_assignments = filtered.get("project_assignments", {})

    visible_team_ids = set(teams)
    visible_project_ids = set(projects)
    visible_agent_ids = set(agents)

    if team_id is not None:
        visible_team_ids &= {team_id}
        visible_project_ids = {
            key for key, value in projects.items() if value.get("team_id") in visible_team_ids
        }
        visible_agent_ids = {
            key for key, value in agents.items() if value.get("team_id") in visible_team_ids
        }

    if project_id is not None:
        if project_id in visible_project_ids and project_id in projects:
            visible_project_ids = {project_id}
            visible_team_ids &= {_required_text(projects[project_id].get("team_id"), "team_id")}
            visible_agent_ids = {
                key
                for key in visible_agent_ids
                if any(
                    assignment.get("agent_id") == key
                    and assignment.get("project_id") == project_id
                    for assignment in project_assignments.values()
                )
            }
        else:
            visible_team_ids = set()
            visible_project_ids = set()
            visible_agent_ids = set()

    if agent_id is not None:
        if agent_id in visible_agent_ids and agent_id in agents:
            agent = agents[agent_id]
            visible_agent_ids = {agent_id}
            agent_team_id = _optional_text(agent.get("team_id"))
            visible_team_ids = visible_team_ids & {agent_team_id} if agent_team_id is not None else set()
            agent_project_ids = {
                _required_text(assignment.get("project_id"), "project_id")
                for assignment in project_assignments.values()
                if assignment.get("agent_id") == agent_id and assignment.get("project_id")
            }
            visible_project_ids = visible_project_ids & agent_project_ids
        else:
            visible_team_ids = set()
            visible_project_ids = set()
            visible_agent_ids = set()

    filtered["teams"] = {
        key: {**value, "project_ids": _filter_ids(value.get("project_ids"), visible_project_ids)}
        for key, value in teams.items()
        if key in visible_team_ids
    }
    filtered["projects"] = {
        key: value for key, value in projects.items() if key in visible_project_ids
    }
    filtered["agents"] = {
        key: {
            **value,
            "reports_to_agent_id": value.get("reports_to_agent_id")
            if value.get("reports_to_agent_id") in visible_agent_ids
            else None,
            "subordinate_agent_ids": _filter_ids(
                value.get("subordinate_agent_ids"),
                visible_agent_ids,
            ),
        }
        for key, value in agents.items()
        if key in visible_agent_ids
    }
    filtered["project_assignments"] = {
        key: value
        for key, value in project_assignments.items()
        if value.get("project_id") in visible_project_ids
        and value.get("agent_id") in visible_agent_ids
    }
    visible_role_ids = {
        role_id
        for value in filtered["agents"].values()
        if (role_id := _optional_text(value.get("role_id"))) is not None
    }
    visible_role_ids.update(
        role_id
        for value in filtered["project_assignments"].values()
        if (role_id := _optional_text(value.get("role_id"))) is not None
    )
    filtered["agent_runtimes"] = {
        key: value
        for key, value in filtered.get("agent_runtimes", {}).items()
        if key in visible_agent_ids
    }
    filtered["tasks"] = {
        key: value
        for key, value in filtered["tasks"].items()
        if value.get("project_id") in visible_project_ids
        and value.get("owner_agent_id") in visible_agent_ids
    }
    visible_task_ids = set(filtered["tasks"])
    filtered["cases"] = {
        key: {
            **value,
            "task_ids": _filter_ids(value.get("task_ids"), visible_task_ids),
        }
        for key, value in filtered.get("cases", {}).items()
        if value.get("team_id") in visible_team_ids
        and (
            value.get("project_id") is None
            or value.get("project_id") in visible_project_ids
        )
        and (
            agent_id is None
            or value.get("owner_agent_id") in visible_agent_ids
            or any(task_id in visible_task_ids for task_id in _string_items(value.get("task_ids")))
        )
    }
    visible_case_ids = set(filtered["cases"])
    filtered["deliverables"] = {
        key: value
        for key, value in filtered.get("deliverables", {}).items()
        if value.get("task_id") in visible_task_ids
        and value.get("producer_agent_id") in visible_agent_ids
    }
    visible_deliverable_ids = set(filtered["deliverables"])
    filtered["review_requests"] = {
        key: value
        for key, value in filtered.get("review_requests", {}).items()
        if value.get("deliverable_id") in visible_deliverable_ids
        and value.get("reviewer_agent_id") in visible_agent_ids
    }
    filtered["review_profiles"] = {
        key: {
            **value,
            "applicable_role_ids": _filter_ids(
                value.get("applicable_role_ids"),
                visible_role_ids,
            ),
        }
        for key, value in filtered.get("review_profiles", {}).items()
        if value.get("team_id") in visible_team_ids
        and (
            value.get("project_id") is None
            or value.get("project_id") in visible_project_ids
        )
    }
    for profile in filtered["review_profiles"].values():
        visible_role_ids.update(_string_items(profile.get("applicable_role_ids")))
    filtered["roles"] = {
        key: value
        for key, value in filtered.get("roles", {}).items()
        if key in visible_role_ids
        or (
            value.get("team_id") in visible_team_ids
            and (
                value.get("project_id") is None
                or value.get("project_id") in visible_project_ids
            )
        )
    }
    visible_review_ids = set(filtered["review_requests"])
    filtered["deliverables"] = {
        key: {
            **value,
            "review_request_ids": _filter_ids(value.get("review_request_ids"), visible_review_ids),
        }
        for key, value in filtered["deliverables"].items()
    }
    filtered["tasks"] = {
        key: {
            **value,
            "deliverable_ids": _filter_ids(value.get("deliverable_ids"), visible_deliverable_ids),
            "case_id": value.get("case_id") if value.get("case_id") in visible_case_ids else None,
            # Keep the graph self-consistent within the scoped view: drop edges that
            # point at tasks the caller cannot see. dependency_status and
            # subtask_counts are intentionally left as computed from the full state
            # so they reflect global truth even when some edges are hidden here.
            "parent_task_id": value.get("parent_task_id")
            if value.get("parent_task_id") in visible_task_ids
            else None,
            "dependency_task_ids": _filter_ids(
                value.get("dependency_task_ids"), visible_task_ids
            ),
            "child_task_ids": _filter_ids(value.get("child_task_ids"), visible_task_ids),
            "blocked_by_task_ids": _filter_ids(
                value.get("blocked_by_task_ids"), visible_task_ids
            ),
        }
        for key, value in filtered["tasks"].items()
    }
    filtered["inbox"] = {
        key: value
        for key, value in filtered.get("inbox", {}).items()
        if (
            value.get("recipient_agent_id") in visible_agent_ids
            or value.get("sender_agent_id") in visible_agent_ids
        )
        and (value.get("task_id") is None or value.get("task_id") in visible_task_ids)
    }
    filtered["action_records"] = {
        key: value
        for key, value in filtered.get("action_records", {}).items()
        if value.get("agent_id") in visible_agent_ids
    }
    filtered["conversations"] = {
        key: value
        for key, value in filtered.get("conversations", {}).items()
        if value.get("agent_id") in visible_agent_ids
    }
    visible_conversation_ids = set(filtered["conversations"])
    filtered["conversation_messages"] = {
        key: value
        for key, value in filtered.get("conversation_messages", {}).items()
        if value.get("conversation_id") in visible_conversation_ids
    }
    filtered["release_packages"] = {
        key: {
            **value,
            "deliverable_ids": _filter_ids(value.get("deliverable_ids"), visible_deliverable_ids),
        }
        for key, value in filtered.get("release_packages", {}).items()
        if value.get("project_id") in visible_project_ids
        and all(
            deliverable_id in visible_deliverable_ids
            for deliverable_id in _string_items(value.get("deliverable_ids"))
        )
    }
    visible_release_ids = set(filtered["release_packages"])
    filtered["git_promotions"] = {
        key: value
        for key, value in filtered.get("git_promotions", {}).items()
        if value.get("project_id") in visible_project_ids
        and (
            value.get("task_id") is None
            or value.get("task_id") in visible_task_ids
        )
        and (
            value.get("deliverable_id") is None
            or value.get("deliverable_id") in visible_deliverable_ids
        )
        and (
            value.get("release_package_id") is None
            or value.get("release_package_id") in visible_release_ids
        )
    }
    visible_git_promotion_ids = set(filtered["git_promotions"])
    filtered["deliverables"] = {
        key: _filter_deliverable_git_refs(value, visible_git_promotion_ids)
        for key, value in filtered["deliverables"].items()
    }
    filtered["release_packages"] = {
        key: {
            **value,
            "git_promotion_record_ids": _filter_ids(
                value.get("git_promotion_record_ids"),
                visible_git_promotion_ids,
            ),
        }
        for key, value in filtered["release_packages"].items()
    }
    filtered["ephemeral_assignments"] = {
        key: value
        for key, value in filtered.get("ephemeral_assignments", {}).items()
        if value.get("parent_agent_id") in visible_agent_ids
        and value.get("project_id") in visible_project_ids
        and value.get("task_id") in visible_task_ids
    }
    filtered["runtime_operations"] = _filter_runtime_operations(
        filtered.get("runtime_operations"),
        visible_agent_ids=visible_agent_ids,
        visible_project_ids=visible_project_ids,
        visible_task_ids=visible_task_ids,
        visible_deliverable_ids=visible_deliverable_ids,
        visible_inbox_ids=set(filtered["inbox"]),
        visible_action_ids=set(filtered["action_records"]),
        visible_release_ids=visible_release_ids,
        visible_git_promotion_ids=visible_git_promotion_ids,
        visible_assignment_ids=set(filtered["ephemeral_assignments"]),
    )
    visible_session_ids = {
        session_id
        for value in filtered["agents"].values()
        if (session_id := _optional_text(value.get("session_id"))) is not None
    }
    visible_dispatch_ids = {
        dispatch_id
        for value in filtered.get("agent_runtimes", {}).values()
        if (dispatch_id := _optional_text(value.get("active_dispatch_id"))) is not None
    }
    visible_dispatch_ids.update(
        dispatch_id
        for value in _mapping_items(filtered["runtime_operations"].get("agents"))
        if (dispatch_id := _optional_text(value.get("active_dispatch_id"))) is not None
    )
    visible_stand_down_ids = {
        stand_down_id
        for value in filtered["agents"].values()
        for item in _mapping_items(value.get("effective_stand_downs"))
        if (stand_down_id := _optional_text(item.get("id"))) is not None
    }
    filtered_activity_summaries = _filter_agent_activity_summaries(
        filtered.get("agent_activity_summaries"),
        visible_agent_ids=visible_agent_ids,
        visible_session_ids=visible_session_ids,
        visible_stand_down_ids=visible_stand_down_ids,
        visible_task_ids=visible_task_ids,
        visible_deliverable_ids=visible_deliverable_ids,
        visible_inbox_ids=set(filtered["inbox"]),
        visible_action_ids=set(filtered["action_records"]),
        visible_release_ids=visible_release_ids,
        visible_git_promotion_ids=visible_git_promotion_ids,
        visible_assignment_ids=set(filtered["ephemeral_assignments"]),
        visible_dispatch_ids=visible_dispatch_ids,
    )
    filtered["agent_activity_summaries"] = filtered_activity_summaries
    filtered["agents"] = {
        key: _agent_payload_with_filtered_activity_summary(
            value,
            filtered_activity_summaries.get(key),
        )
        for key, value in filtered["agents"].items()
    }
    return filtered


def _filter_agent_activity_summaries(
    value: object,
    *,
    visible_agent_ids: set[str],
    visible_session_ids: set[str],
    visible_stand_down_ids: set[str],
    visible_task_ids: set[str],
    visible_deliverable_ids: set[str],
    visible_inbox_ids: set[str],
    visible_action_ids: set[str],
    visible_release_ids: set[str],
    visible_git_promotion_ids: set[str],
    visible_assignment_ids: set[str],
    visible_dispatch_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    allowed_by_type = {
        "agent": visible_agent_ids,
        "session": visible_session_ids,
        "stand_down": visible_stand_down_ids,
        "task": visible_task_ids,
        "deliverable": visible_deliverable_ids,
        "inbox": visible_inbox_ids,
        "action": visible_action_ids,
        "release": visible_release_ids,
        "promotion": visible_git_promotion_ids,
        "assignment": visible_assignment_ids,
        "dispatch": visible_dispatch_ids,
    }
    return {
        agent_id: dict(summary)
        for agent_id, summary in value.items()
        if agent_id in visible_agent_ids
        and isinstance(summary, dict)
        and _activity_summary_refs_are_visible(summary.get("reason_refs"), allowed_by_type)
    }


def _activity_summary_refs_are_visible(
    refs: object,
    allowed_by_type: Mapping[str, set[str]],
) -> bool:
    if not isinstance(refs, list) or not refs:
        return False
    for ref in refs:
        if not isinstance(ref, dict):
            return False
        ref_type = _optional_text(ref.get("type"))
        ref_id = _optional_text(ref.get("id"))
        if ref_type is None or ref_id is None:
            return False
        allowed_ids = allowed_by_type.get(ref_type)
        if allowed_ids is None or ref_id not in allowed_ids:
            return False
    return True


def _agent_payload_with_filtered_activity_summary(
    value: dict[str, Any],
    summary: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(value)
    if summary is None:
        payload.pop("activity_summary", None)
    else:
        payload["activity_summary"] = summary
    return payload


def _filter_runtime_operations(
    value: object,
    *,
    visible_agent_ids: set[str],
    visible_project_ids: set[str],
    visible_task_ids: set[str],
    visible_deliverable_ids: set[str],
    visible_inbox_ids: set[str],
    visible_action_ids: set[str],
    visible_release_ids: set[str],
    visible_git_promotion_ids: set[str],
    visible_assignment_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"generated_at": None, "agents": [], "attention": []}
    agents = [
        row
        for row in _mapping_items(value.get("agents"))
        if _optional_text(row.get("id")) in visible_agent_ids
    ]
    attention = [
        row
        for row in _mapping_items(value.get("attention"))
        if _runtime_row_is_visible(
            row,
            visible_agent_ids=visible_agent_ids,
            visible_project_ids=visible_project_ids,
            visible_task_ids=visible_task_ids,
            visible_deliverable_ids=visible_deliverable_ids,
            visible_inbox_ids=visible_inbox_ids,
            visible_action_ids=visible_action_ids,
            visible_release_ids=visible_release_ids,
            visible_git_promotion_ids=visible_git_promotion_ids,
            visible_assignment_ids=visible_assignment_ids,
        )
    ]
    session_health = [
        row
        for row in _mapping_items(value.get("session_health"))
        if _optional_text(row.get("agent_id")) in visible_agent_ids
    ]
    return {
        "generated_at": value.get("generated_at"),
        "agents": agents,
        "attention": attention,
        "session_health": session_health,
    }


def _runtime_row_is_visible(
    row: dict[str, Any],
    *,
    visible_agent_ids: set[str],
    visible_project_ids: set[str],
    visible_task_ids: set[str],
    visible_deliverable_ids: set[str],
    visible_inbox_ids: set[str],
    visible_action_ids: set[str],
    visible_release_ids: set[str],
    visible_git_promotion_ids: set[str],
    visible_assignment_ids: set[str],
) -> bool:
    checks = (
        (_optional_text(row.get("agent_id")), visible_agent_ids),
        (_optional_text(row.get("project_id")), visible_project_ids),
        (_optional_text(row.get("task_id")), visible_task_ids),
        (_optional_text(row.get("deliverable_id")), visible_deliverable_ids),
        (_optional_text(row.get("inbox_item_id")), visible_inbox_ids),
        (_optional_text(row.get("action_record_id")), visible_action_ids),
        (_optional_text(row.get("release_package_id")), visible_release_ids),
        (_optional_text(row.get("promotion_id")), visible_git_promotion_ids),
        (_optional_text(row.get("assignment_id")), visible_assignment_ids),
    )
    if not any(identifier is not None for identifier, _allowed in checks):
        return False
    for identifier, allowed in checks:
        if identifier is not None and identifier not in allowed:
            return False
    deliverable_ids = _string_items(row.get("deliverable_ids"))
    if deliverable_ids and any(
        deliverable_id not in visible_deliverable_ids
        for deliverable_id in deliverable_ids
    ):
        return False
    return True


def _mapping_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _filter_ids(value: object, allowed: set[str]) -> list[str]:
    return [item for item in _string_items(value) if item in allowed]


def _filter_deliverable_git_refs(value: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    git_refs = value.get("git_refs")
    if not isinstance(git_refs, dict):
        return value
    return {
        **value,
        "git_refs": {
            **git_refs,
            "promotion_record_ids": _filter_ids(
                git_refs.get("promotion_record_ids"),
                allowed,
            ),
        },
    }


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object, *, default: int | None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("integer value is required")
    return int(value)


def _optional_float(value: object, *, default: float | None) -> float | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("number value is required")
    return float(value)


def _number_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _number_or_zero(value: object) -> float:
    return _number_or_none(value) or 0.0


def _optional_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean value is required")


def _optional_mapping(value: object, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _list_of_mappings(value: object, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must contain JSON objects")
        records.append(dict(item))
    return records


def _string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _consolidation_failure(target_branch: str | None, error: str) -> dict[str, Any]:
    return {
        "success": False,
        "target_branch": target_branch,
        "merged_branches": [],
        "commit_sha": None,
        "conflict_branch": None,
        "output": None,
        "error": error,
    }


def _review_publish_failure(
    target_branch: str | None,
    host_repo_path: str | None,
    error: str,
) -> dict[str, Any]:
    return {
        "success": False,
        "review_branch": target_branch,
        "target_branch": target_branch,
        "commit_sha": None,
        "host_repo_path": host_repo_path,
        "message": None,
        "error": error,
    }


def _git_text(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def _resolve_bare_repo_path(
    bare_repo_path: str | None,
    bare_repo_name: str | None,
) -> Path:
    if bare_repo_path and bare_repo_name:
        raise ValueError("bare_repo_path and bare_repo_name are mutually exclusive")
    if bare_repo_path:
        return Path(bare_repo_path)
    if not bare_repo_name:
        raise ValueError("bare_repo_path or bare_repo_name is required")
    normalized = bare_repo_name if bare_repo_name.endswith(".git") else f"{bare_repo_name}.git"
    git_root = os.environ.get("AGENT_SANDBOX_GIT_ROOT")
    root = Path(git_root) if git_root else Path(__file__).resolve().parents[2] / "git-remotes"
    return root / normalized


def _publish_review_branch(
    *,
    host_repo_path: str,
    bare_repo_path: Path,
    target_branch: str,
    source_branch: str | None,
    commit_sha: str | None,
) -> dict[str, Any]:
    repo_path = Path(host_repo_path)
    if not (repo_path / ".git").exists():
        return _review_publish_failure(
            target_branch,
            host_repo_path,
            f"{host_repo_path} is not a git repository",
        )
    if not bare_repo_path.exists():
        return _review_publish_failure(
            target_branch,
            host_repo_path,
            f"bare repo not found: {bare_repo_path}",
        )

    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )

    source_ref = commit_sha if commit_sha is not None else f"refs/heads/{source_branch}"
    temp_ref = f"refs/agent-sandbox/team-promotions/{target_branch}"
    target_ref = f"refs/heads/{target_branch}"
    fetch = _git(
        "fetch",
        "--no-tags",
        str(bare_repo_path),
        f"+{source_ref}:{temp_ref}",
    )
    if fetch.returncode != 0:
        return _review_publish_failure(target_branch, host_repo_path, _git_text(fetch))

    resolved = _git("rev-parse", "--verify", f"{temp_ref}^{{commit}}")
    if resolved.returncode != 0:
        _git("update-ref", "-d", temp_ref)
        return _review_publish_failure(target_branch, host_repo_path, _git_text(resolved))

    resolved_commit = resolved.stdout.strip()
    update = _git("update-ref", target_ref, resolved_commit)
    _git("update-ref", "-d", temp_ref)
    if update.returncode != 0:
        return _review_publish_failure(target_branch, host_repo_path, _git_text(update))

    return {
        "success": True,
        "review_branch": target_branch,
        "target_branch": target_branch,
        "commit_sha": resolved_commit,
        "host_repo_path": host_repo_path,
        "message": f"Published {resolved_commit} to {target_branch}",
        "error": None,
    }


def _consolidate_git_branches(
    *,
    host_repo_path: str,
    source_branches: list[str],
    target_branch: str,
    base_branch: str | None,
    bare_repo_path: Path | None = None,
) -> dict[str, Any]:
    """Merge ``source_branches`` into ``target_branch`` inside ``host_repo_path``.

    Branch names are validated by the caller. Merges run in the listed order and
    produce deterministic merge commits. A merge conflict aborts the merge and
    returns ``success=False`` with the conflict output; nothing is auto-resolved.
    """

    repo_path = Path(host_repo_path)
    git_marker = repo_path / ".git"
    if not git_marker.exists():
        return _consolidation_failure(
            target_branch, f"{host_repo_path} is not a git repository"
        )

    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )

    # A linked worktree (``.git`` is a file pointing at the gitdir) is already an
    # isolated checkout, so a dirty tree there is acceptable; a primary worktree
    # must be clean before we move HEAD around.
    isolated_worktree = git_marker.is_file()
    status = _git("status", "--porcelain")
    if status.returncode != 0:
        return _consolidation_failure(target_branch, _git_text(status))
    if status.stdout.strip() and not isolated_worktree:
        failure = _consolidation_failure(
            target_branch, "refusing to merge into a dirty worktree"
        )
        failure["output"] = status.stdout
        return failure
    merge_sources: list[tuple[str, str]] = []
    temp_refs: list[str] = []
    if bare_repo_path is None:
        merge_sources = [(source, source) for source in source_branches]
    else:
        bare_repo_exists = bare_repo_path.exists()
        for source in source_branches:
            resolved = _git(
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/heads/{source}^{{commit}}",
            )
            if not bare_repo_exists:
                if resolved.returncode == 0:
                    merge_sources.append((source, source))
                    continue
                return _consolidation_failure(
                    target_branch,
                    f"bare repo not found: {bare_repo_path}",
                )
            ref_hash = hashlib.sha256(
                f"{target_branch}\0{source}".encode("utf-8")
            ).hexdigest()[:16]
            temp_ref = f"refs/agent-sandbox/team-consolidations/{ref_hash}"
            fetch = _git(
                "fetch",
                "--no-tags",
                str(bare_repo_path),
                f"+refs/heads/{source}:{temp_ref}",
            )
            if fetch.returncode != 0:
                for ref in temp_refs:
                    _git("update-ref", "-d", ref)
                failure = _consolidation_failure(
                    target_branch,
                    f"failed to fetch source branch {source} from {bare_repo_path}",
                )
                failure["output"] = _git_text(fetch)
                failure["conflict_branch"] = source
                return failure
            temp_refs.append(temp_ref)
            merge_sources.append((source, temp_ref))

    def _cleanup_temp_refs() -> None:
        for ref in temp_refs:
            _git("update-ref", "-d", ref)

    try:
        target_ref = f"refs/heads/{target_branch}"
        target_exists = (
            _git("rev-parse", "--verify", "--quiet", target_ref).returncode == 0
        )
        if target_exists:
            checkout = _git("checkout", target_branch)
        elif base_branch is not None:
            checkout = _git("checkout", "-B", target_branch, base_branch)
        else:
            checkout = _git("checkout", "-B", target_branch)
        if checkout.returncode != 0:
            return _consolidation_failure(target_branch, _git_text(checkout))

        merged: list[str] = []
        for source, merge_ref in merge_sources:
            merge = _git(
                "merge",
                "--no-ff",
                "-m",
                f"Merge {source} into {target_branch}",
                merge_ref,
            )
            if merge.returncode != 0:
                _git("merge", "--abort")
                failure = _consolidation_failure(
                    target_branch,
                    f"merge conflict while merging {source} into {target_branch}",
                )
                failure["merged_branches"] = merged
                failure["conflict_branch"] = source
                failure["output"] = _git_text(merge)
                return failure
            merged.append(source)

        head = _git("rev-parse", "HEAD")
        commit_sha = head.stdout.strip() if head.returncode == 0 else None
        return {
            "success": True,
            "target_branch": target_branch,
            "merged_branches": merged,
            "commit_sha": commit_sha,
            "conflict_branch": None,
            "output": None,
            "error": None,
        }
    finally:
        _cleanup_temp_refs()
