"""Workflow editor fields, provider catalog entries, and Team event metadata."""

from __future__ import annotations

from typing import (
    Any,
)

from evander.workflow.catalog import (
    WorkflowEditorFieldDefinition,
    WorkflowEditorNodeCatalogEntry,
    WorkflowInternalEventCatalogEntry,
)

from .services.commands.outbox import (
    TEAM_OUTBOX_PUBLISH_REQUESTED_EVENT_TYPE,
)
from .services.domain.acceptance_verification import (
    DELIVERABLE_ACCEPTANCE_VERIFICATION_REQUESTED_EVENT_TYPE,
)
from .services.domain.case_events import (
    TEAM_CASE_CLOSED_EVENT_TYPE,
)
from .services.domain.chat import (
    CHAT_MESSAGE_QUEUED_EVENT_TYPE,
    EXTERNAL_CHAT_REPLY_REQUESTED_EVENT_TYPE,
)
from .services.domain.ephemeral import (
    EPHEMERAL_COMPLETED_EVENT_TYPE,
    EPHEMERAL_REQUESTED_EVENT_TYPE,
)
from .services.domain.git_common import (
    GIT_PROMOTION_EVENT_TYPE,
)
from .services.domain.human_reviews import (
    HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE,
    HUMAN_REVIEW_REQUESTED_EVENT_TYPE,
)
from .services.domain.operator_escalation import (
    OPERATOR_ESCALATION_EVENT_TYPE,
)
from .services.domain.release_events import (
    TEAM_RELEASE_APPROVED_EVENT_TYPE,
)
from .services.domain.release_gates import (
    RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE,
)
from .services.domain.release_packages import (
    GIT_RELEASE_RECOVERY_EVENT_TYPE,
)
from .services.domain.wake import (
    AGENT_WAKE_REQUESTED_EVENT_TYPE,
)
from .services.memory_service import (
    DEFAULT_MEMORY_LIMIT,
)


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
        "trigger",
        "trigger:team:deliverable-created",
        "Team deliverable created",
        "Start a workflow when a Team deliverable is created.",
        [
            _catalog_field(
                "team_id",
                "Team ID",
                "string",
                description="Only emit deliverables from this Team. Leave blank for all Teams.",
                placeholder="auto-dev",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Only emit deliverables from this Team project. Leave blank for all projects.",
                placeholder="web-app",
            ),
            _catalog_field(
                "producer_agent_id",
                "Producer agent ID",
                "string",
                description=(
                    "Only emit deliverables produced by this persistent Team agent. "
                    "Leave blank for any producer."
                ),
                placeholder="implementer",
            ),
        ],
    ),
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
                "run_global_maintenance",
                "Run global maintenance",
                "boolean",
                description=(
                    "Run correction, human-review assignment, and worktree cleanup "
                    "reconciliation before leasing agents. Disable for targeted immediate wakes."
                ),
                default_value=True,
            ),
            _catalog_field(
                "worktree_cleanup_limit",
                "Worktree cleanup limit",
                "number",
                description=(
                    "Maximum resolved or reassigned task worktrees reconciled in this cycle."
                ),
                default_value=10,
                placeholder="10",
            ),
            _catalog_field(
                "worktree_cleanup_grace_seconds",
                "Cleanup grace seconds",
                "number",
                description=(
                    "Delay after a task checkout becomes cleanup-eligible before "
                    "the first safety pass."
                ),
                default_value=300,
                placeholder="300",
            ),
            _catalog_field(
                "worktree_cleanup_retry_seconds",
                "Cleanup retry seconds",
                "number",
                description=(
                    "Delay before a deferred or recovery-required worktree is "
                    "eligible for another reconciliation attempt."
                ),
                default_value=300,
                placeholder="300",
            ),
            _catalog_field(
                "worktree_cleanup_recovery_seconds",
                "Cleanup recovery seconds",
                "number",
                description=(
                    "Age after which an interrupted processing claim is recovered for retry."
                ),
                default_value=900,
                placeholder="900",
            ),
            _catalog_field(
                "worktree_cleanup_dry_run",
                "Cleanup dry run",
                "boolean",
                description=(
                    "Leave unset to audit the first safe pass and remove on a "
                    "later eligible pass after revalidation. Enable to keep "
                    "cleanup dry-run-only, or disable to permit removal on the "
                    "first eligible pass."
                ),
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
                "outbox_record_ids",
                "Outbox record IDs",
                "json",
                description=(
                    "Optional exact Team outbox record IDs to publish. Leave blank to claim "
                    "the globally oldest pending records."
                ),
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
        "effect:team:enqueue-operator-chat-message",
        "Enqueue external Team Chat message",
        "Persist and queue a communication message through the normal Team Chat dispatch path.",
        [
            _catalog_field(
                "conversation_id",
                "Conversation ID",
                "string",
                description="Fixed Team conversation. Configure this or Agent ID, not both.",
            ),
            _catalog_field(
                "agent_id",
                "Agent ID",
                "string",
                description="Persistent Team agent used to resolve or create the configured operator conversation.",
            ),
            _catalog_field(
                "operator_user_id",
                "Operator user ID",
                "string",
                required=True,
                description="Configured Team operator identity; never bind this from external message content.",
            ),
            _catalog_field(
                "operator_display_name",
                "Operator display name",
                "string",
                description="Display name when the effect creates the Team conversation.",
            ),
            _catalog_field(
                "body",
                "Message body",
                "string",
                required=True,
                multiline=True,
                description="External message text to place in Team Chat.",
            ),
            _catalog_field(
                "provider",
                "Provider",
                "string",
                required=True,
                description="Authenticated communication provider from the inbound trigger.",
                placeholder="discord",
            ),
            _catalog_field(
                "communication_target_id",
                "Communication target",
                "string",
                required=True,
                description="Read-capable source target; also becomes the external reply destination.",
            ),
            _catalog_field(
                "source_message_id",
                "Source message ID",
                "string",
                required=True,
                description="Provider message ID used as the durable ingress idempotency key.",
            ),
            _catalog_field(
                "source_thread_id",
                "Source thread ID",
                "string",
                description="Optional provider thread identifier preserved for reply-capable transports.",
            ),
            _catalog_field(
                "source_author_id",
                "Source author ID",
                "string",
                required=True,
                description="Authenticated provider-side sender identifier.",
            ),
            _catalog_field(
                "source_author_name",
                "Source author name",
                "string",
                description="Optional provider display name retained in message provenance.",
            ),
            _catalog_field(
                "import_attachments",
                "Import attachments",
                "boolean",
                description=(
                    "Import supported provider files into private Team Chat attachment storage."
                ),
                default_value=False,
            ),
            _catalog_field(
                "allow_team_mutations",
                "Allow Team mutations",
                "boolean",
                description="Grant ordinary Team mutation tools only after the configured provider author allowlist passes.",
                default_value=False,
            ),
            _catalog_field(
                "authorized_author_ids",
                "Authorized author IDs",
                "json",
                description="Provider author allowlist required when Team mutations are enabled.",
                json_value_type="array",
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:record-external-chat-delivery",
        "Record external Team Chat delivery",
        "Record the outcome of the communication send without changing the persisted Team reply.",
        [
            _catalog_field(
                "delivery_id",
                "Delivery ID",
                "string",
                required=True,
                description="External delivery intent emitted by team_reply_to_chat.",
            ),
            _catalog_field(
                "success",
                "Send succeeded",
                "boolean",
                required=True,
                description="Bind from the communication send result.",
            ),
            _catalog_field(
                "provider_message_id",
                "Provider message ID",
                "string",
                description="Provider message ID returned after a successful send.",
            ),
            _catalog_field(
                "error",
                "Error",
                "string",
                description="Send error to display on the Team reply when delivery fails.",
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:record-operator-escalation-delivery",
        "Record operator escalation delivery",
        "Persist whether an operator escalation reached its communication target.",
        [
            _catalog_field(
                "escalation_id",
                "Escalation ID",
                "string",
                required=True,
                description="Operator escalation identifier emitted by team_escalate_operator.",
            ),
            _catalog_field(
                "success",
                "Send succeeded",
                "boolean",
                required=True,
                description="Bind from the communication send result or set false on its error branch.",
            ),
            _catalog_field(
                "provider_message_id",
                "Provider message ID",
                "string",
                description="Provider message ID returned after a successful send.",
            ),
            _catalog_field(
                "error",
                "Error",
                "string",
                description="Delivery error retained for the manager when every send attempt fails.",
            ),
            _catalog_field(
                "fallback_used",
                "Fallback used",
                "boolean",
                description="Whether a host-owned fallback sender delivered the escalation.",
                default_value=False,
            ),
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
                description="Optional Evander session that requested the directive.",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:create-review-request",
        "Create Team review request",
        "Create a Team review request for a deliverable and wake the assigned reviewer.",
        [
            _catalog_field(
                "actor_agent_id",
                "Actor agent ID",
                "string",
                required=True,
                description=(
                    "Team agent whose authority is used to request the review. "
                    "Must be the producer, a supervisor, project lead, or manager."
                ),
                placeholder="project-lead",
            ),
            _catalog_field(
                "deliverable_id",
                "Deliverable ID",
                "string",
                required=True,
                description="Deliverable that needs this review request.",
                placeholder="${{team-deliverable-created.deliverable_id}}",
            ),
            _catalog_field(
                "reviewer_agent_id",
                "Reviewer agent ID",
                "string",
                required=True,
                description="Persistent Team agent assigned to decide the review.",
                placeholder="code-reviewer",
            ),
            _catalog_field(
                "review_request_id",
                "Review request ID",
                "string",
                description="Optional stable review request id. Leave blank to generate one.",
                placeholder="code-review-${{team-deliverable-created.deliverable_id}}",
            ),
            _catalog_field(
                "review_profile_id",
                "Review profile ID",
                "string",
                description="Optional Team review profile rubric to attach to the request.",
                placeholder="code-review-profile",
            ),
            _catalog_field(
                "instructions",
                "Instructions",
                "string",
                multiline=True,
                description="Reviewer-specific instructions included in the review inbox item.",
                placeholder="Review correctness, tests, risk, and release readiness.",
            ),
            _catalog_field(
                "skip_if_existing",
                "Skip if existing",
                "boolean",
                description=(
                    "Treat an active review request for the same deliverable and reviewer as success "
                    "instead of failing the workflow."
                ),
                default_value=True,
            ),
            _catalog_field(
                "publish_wake_events",
                "Publish wake events",
                "boolean",
                description="Publish reviewer wake events created by this request immediately.",
                default_value=True,
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:create-review-cohort",
        "Create Team review cohort",
        (
            "Atomically create and seal every required review request for one "
            "deliverable, then wake all assigned reviewers."
        ),
        [
            _catalog_field(
                "actor_agent_id",
                "Actor agent ID",
                "string",
                required=True,
                description=(
                    "Team agent whose authority is used to request the complete review cohort."
                ),
                placeholder="${{team-deliverable-created.producer_agent_id}}",
            ),
            _catalog_field(
                "deliverable_id",
                "Deliverable ID",
                "string",
                required=True,
                description="Immutable deliverable reviewed by the cohort.",
                placeholder="${{team-deliverable-created.deliverable_id}}",
            ),
            _catalog_field(
                "reviews",
                "Reviews",
                "json",
                required=True,
                description=(
                    "Complete reviewer list. Each object requires reviewer_agent_id "
                    "and may include review_profile_id, review_request_id, and "
                    "instructions."
                ),
                json_value_type="array",
            ),
            _catalog_field(
                "publish_wake_events",
                "Publish wake events",
                "boolean",
                description="Publish all reviewer wake events after the cohort commits.",
                default_value=True,
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:create-task",
        "Create Team task",
        "Create or update an operator-authored Team task for a project agent.",
        [
            _catalog_field(
                "team_id",
                "Team ID",
                "string",
                required=True,
                description="Team that owns the project receiving the task.",
                placeholder="team-accounting",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                required=True,
                description="Team project that owns the task.",
                placeholder="project-month-end",
            ),
            _catalog_field(
                "owner_agent_id",
                "Owner agent ID",
                "string",
                required=True,
                description="Persistent Team agent responsible for the task.",
            ),
            _catalog_field(
                "priority",
                "Priority",
                "number",
                required=True,
                description="Task queue priority; lower numbers sort earlier.",
                default_value=1,
                placeholder="1",
            ),
            _catalog_field(
                "title",
                "Title",
                "string",
                required=True,
                description="Short task title shown in project and agent task lists.",
            ),
            _catalog_field(
                "description",
                "Description",
                "string",
                required=True,
                multiline=True,
                description="Work instructions for the owning agent.",
            ),
            _catalog_field(
                "task_id",
                "Task ID",
                "string",
                description="Optional stable task id. Existing ids update the matching task.",
            ),
            _catalog_field(
                "case_id",
                "Case ID",
                "string",
                description="Optional Team case to link to the task.",
            ),
            _catalog_field(
                "state",
                "Initial state",
                "string",
                description="Initial task state for new tasks. Leave blank for pending.",
                default_value="pending",
                placeholder="pending",
            ),
            _catalog_field(
                "task_kind",
                "Task kind",
                "string",
                description=(
                    "support for ordinary work, outcome for contracted outcomes, or "
                    "coordination for supervision that must never own a deliverable."
                ),
                default_value="support",
                placeholder="support",
            ),
            _catalog_field(
                "parent_task_id",
                "Parent task ID",
                "string",
                description="Optional parent task in the same project.",
            ),
            _catalog_field(
                "ado_work_item_id",
                "ADO work item ID",
                "string",
                description="Capture Assigned To from this work item on initial task creation; inherited by follow-ups.",
            ),
            _catalog_field(
                "ado_project",
                "ADO project",
                "string",
                description="Optional Azure DevOps project for work-item intake.",
            ),
            _catalog_field(
                "authored_by",
                "Task commit author",
                "json",
                description=(
                    "Optional {name, email}; inherited by child and follow-up tasks. "
                    "Unset uses the project author."
                ),
            ),
            _catalog_field(
                "originating_task_id",
                "Originating task ID",
                "string",
                description="Optional source task for follow-up author and work lineage.",
            ),
            _catalog_field(
                "git_base_branch",
                "Git base branch",
                "string",
                description=(
                    "Optional durable task starting branch. Blank inherits the "
                    "parent snapshot or the project default for a new root task."
                ),
                placeholder="main",
            ),
            _catalog_field(
                "dependency_task_ids",
                "Dependency task IDs",
                "json",
                description="Optional same-project task ids that must be satisfied first.",
                json_value_type="array",
            ),
            _catalog_field(
                "integration_boundary",
                "Integration boundary",
                "boolean",
                description=(
                    "Designate this task as the explicit final integration and acceptance boundary."
                ),
                default_value=False,
            ),
            _catalog_field(
                "deliverable_contract",
                "Deliverable contract",
                "json",
                description=(
                    "Contract for an integration boundary. May require Git "
                    "provenance and explicitly name required child or dependency "
                    "contribution task ids."
                ),
                json_value_type="object",
            ),
            _catalog_field(
                "created_by",
                "Created by",
                "string",
                description="Operator or workflow actor recorded as the task creator.",
                placeholder="workflow",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:set-task-work-state",
        "Set Team task work state",
        (
            "Set a task to pending, in progress, or waiting for external input. "
            "waiting_for_input is non-terminal and suppresses ordinary owner wakes "
            "and manager fallback until a workflow resumes the task."
        ),
        [
            _catalog_field(
                "task_id",
                "Task ID",
                "string",
                required=True,
                description="Existing Team task whose work lifecycle should change.",
            ),
            _catalog_field(
                "state",
                "Work state",
                "string",
                required=True,
                description=(
                    "pending or in_progress makes owner work due; "
                    "waiting_for_input pauses ordinary wake and fallback activity."
                ),
                placeholder="waiting_for_input",
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
                "reuse_existing",
                "Reuse existing source",
                "boolean",
                description="Reuse a case with the same Team, project, source and source reference without changing it. Requires source and source_ref.",
                default_value=False,
            ),
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
                    "Initial case status: open, triage, in_progress, waiting, resolved, or closed."
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
                    "Replacement status: open, triage, in_progress, waiting, resolved, or closed."
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
        "effect",
        "effect:team:register-agent",
        "Register Team agent",
        "Attach an existing in-scope core session as a durable ordinary Team agent.",
        [
            _catalog_field(
                "session_id",
                "Session ID",
                "string",
                required=True,
                description="Existing unexpired core session to attach to Team state.",
            ),
            _catalog_field(
                "team_id",
                "Team ID",
                "string",
                required=True,
                description="Team that will own the persistent agent.",
            ),
            _catalog_field(
                "role_id",
                "Role ID",
                "string",
                required=True,
                description="Active Team role compatible with the selected Team and project.",
            ),
            _catalog_field(
                "agent_id",
                "Agent ID",
                "string",
                description="Optional deterministic Team agent id. Leave blank to generate one.",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Optional Team project membership for the new agent.",
            ),
            _catalog_field(
                "reports_to_agent_id",
                "Reports to agent ID",
                "string",
                description="Optional active same-Team manager for the reporting edge.",
            ),
            _catalog_field(
                "display_name",
                "Display name",
                "string",
                description="Optional durable display name for Team roster and audit history.",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:restore-agent",
        "Restore Team agent",
        "Reattach a fully archived Team agent to a distinct, unbound core session.",
        [
            _catalog_field(
                "agent_id",
                "Agent ID",
                "string",
                required=True,
                description="Fully archived persistent Team agent to restore.",
            ),
            _catalog_field(
                "new_session_id",
                "New session ID",
                "string",
                required=True,
                description=(
                    "Existing unexpired and unbound core session for the restored generation."
                ),
            ),
            _catalog_field(
                "expected_old_session_id",
                "Expected old session ID",
                "string",
                required=True,
                description="Archived session-binding guard for safe restoration.",
            ),
            _catalog_field(
                "expected_team_id",
                "Expected Team ID",
                "string",
                required=True,
                description="Retained Team identity guard and target Team for restoration.",
            ),
            _catalog_field(
                "role_id",
                "Role ID",
                "string",
                required=True,
                description="Active Team role for the restored agent.",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Optional Team project membership to recreate.",
            ),
            _catalog_field(
                "reports_to_agent_id",
                "Reports to agent ID",
                "string",
                description="Optional active same-Team manager to reattach.",
            ),
            _revision_field(),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:retire-agent",
        "Retire Team agent",
        "Detach and archive a Team agent after its children and active work are retired.",
        [
            _catalog_field(
                "agent_id",
                "Agent ID",
                "string",
                required=True,
                description="Persistent Team agent to detach and archive.",
            ),
            _catalog_field(
                "expected_session_id",
                "Expected session ID",
                "string",
                required=True,
                description="Backing core session identity guard for safe retirement.",
            ),
            _catalog_field(
                "expected_team_id",
                "Expected Team ID",
                "string",
                required=True,
                description="Current or retained Team identity guard for safe retirement retries.",
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
                description="Completed Evander session id to classify against Team runtime state.",
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
        "Attach a created Evander session to a Team ephemeral assignment.",
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
                description="Created Evander session id for the ephemeral worker.",
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
                description="Destroyed Evander session id for lookup when assignment id is not bound.",
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
                "managed_repo_id",
                "Managed repository ID",
                "string",
                required=True,
                description="Evander-managed repository containing the pinned source commit.",
            ),
            _catalog_field(
                "directory_path",
                "Directory path",
                "string",
                description="Owning directory scope for the managed repository.",
            ),
            _catalog_field(
                "target_branch",
                "Target branch",
                "string",
                required=True,
                description="Branch name to create or update for review.",
            ),
            _catalog_field(
                "source_branch",
                "Source branch",
                "string",
                required=True,
                description="Agent branch containing the pinned source commit.",
            ),
            _catalog_field(
                "expected_commit_sha",
                "Expected commit SHA",
                "string",
                required=True,
                description="Exact source commit approved for promotion.",
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
        "effect:team:record-human-review-preparation-result",
        "Record human review preparation result",
        (
            "Return safe preview target metadata or a preparation failure for the "
            "exact pinned human-review generation."
        ),
        [
            _catalog_field(
                "human_review_request_id",
                "Human review request ID",
                "string",
                required=True,
                description="Pinned human review request being prepared.",
            ),
            _catalog_field(
                "candidate_commit_sha",
                "Candidate commit SHA",
                "string",
                required=True,
                description="Exact candidate commit received from the preparation event.",
            ),
            _catalog_field(
                "candidate_tree_hash",
                "Candidate tree hash",
                "string",
                required=True,
                description="Exact candidate tree received from the preparation event.",
            ),
            _catalog_field(
                "preparation_generation",
                "Preparation generation",
                "integer",
                required=True,
                description="Preparation generation received from the event.",
            ),
            _catalog_field(
                "target",
                "Safe target metadata",
                "json",
                description=(
                    "URL or alias, expiry, instructions, and evidence only. "
                    "Credentials and secret-bearing fields are rejected."
                ),
                json_value_type="object",
            ),
            _catalog_field(
                "error",
                "Preparation error",
                "string",
                description="Failure explanation when no target was prepared.",
            ),
            _catalog_field(
                "request_id",
                "Idempotency key",
                "string",
                description="Optional stable workflow result key.",
            ),
            _catalog_field(
                "recorded_by",
                "Workflow actor",
                "string",
                description="Host-owned workflow actor label used in audit records.",
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:consolidate-git-branches",
        "Consolidate Team branches",
        "Merge multiple Team source branches into a deterministic target branch.",
        [
            _catalog_field(
                "managed_repo_id",
                "Managed repository ID",
                "string",
                required=True,
                description="Evander-managed repository used for consolidation.",
            ),
            _catalog_field(
                "directory_path",
                "Directory path",
                "string",
                description="Owning directory scope for the managed repository.",
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
                "base_commit_sha",
                "Base commit SHA",
                "string",
                description=(
                    "Optional exact immutable base commit. Successor releases use "
                    "the predecessor candidate SHA."
                ),
            ),
            _catalog_field(
                "expected_source_commits",
                "Expected source commits",
                "json",
                required=True,
                description="Exact approved commit SHA for every source branch.",
                json_value_type="object",
            ),
            _catalog_field(
                "commit_message",
                "Commit message",
                "string",
                description="Optional message for a consolidation merge commit.",
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
    _catalog_entry(
        "query",
        "query:team:memory",
        "Search Team memory",
        "Read compact Team memory records from canonical state or the Team Qdrant collection.",
        [
            _catalog_field(
                "team_id",
                "Team ID",
                "string",
                description="Team whose memories should be searched. Required for semantic search.",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Optional project filter; Team-global memories remain visible.",
            ),
            _catalog_field(
                "codebase_id",
                "Codebase ID",
                "string",
                description="Optional codebase filter; Team-global memories remain visible.",
            ),
            _catalog_field(
                "agent_id",
                "Agent ID",
                "string",
                description="Optional agent-private memory owner filter.",
            ),
            _catalog_field(
                "query_text",
                "Query text",
                "string",
                description="Text filter or semantic query.",
            ),
            _catalog_field(
                "semantic",
                "Semantic",
                "boolean",
                description="Use the Team Qdrant collection when query text and team id are set.",
                default_value=False,
            ),
            _catalog_field(
                "kinds",
                "Kinds",
                "json",
                description="Optional memory kind filter.",
                json_value_type="array",
            ),
            _catalog_field(
                "statuses",
                "Statuses",
                "json",
                description="Optional memory status filter.",
                json_value_type="array",
            ),
            _catalog_field(
                "tags_any",
                "Tags any",
                "json",
                description="Optional tag-any filter.",
                json_value_type="array",
            ),
            _catalog_field(
                "limit",
                "Limit",
                "number",
                description="Maximum compact memory records to return.",
                default_value=DEFAULT_MEMORY_LIMIT,
            ),
            _catalog_field(
                "directory_path",
                "Directory path",
                "string",
                description="Optional directory config used for semantic provider settings.",
            ),
            _catalog_field(
                "score_threshold",
                "Score threshold",
                "number",
                description="Optional minimum semantic score.",
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:index-memory",
        "Index Team memory",
        "Index active Team memory records into that Team's Qdrant collection.",
        [
            _catalog_field(
                "memory_id",
                "Memory ID",
                "string",
                description="Single memory record to index or delete from the semantic index.",
            ),
            _catalog_field(
                "memory_ids",
                "Memory IDs",
                "json",
                description="Memory records to index or delete from the semantic index.",
                json_value_type="array",
            ),
            _catalog_field(
                "team_id",
                "Team ID",
                "string",
                description="Team filter used when memory ids are not provided.",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Optional project filter; Team-global memories are included.",
            ),
            _catalog_field(
                "directory_path",
                "Directory path",
                "string",
                description="Optional directory config used for semantic provider settings.",
            ),
            _catalog_field(
                "wait",
                "Wait",
                "boolean",
                description="Wait for Qdrant write operations to apply.",
                default_value=False,
            ),
        ],
    ),
    _catalog_entry(
        "effect",
        "effect:team:reindex-memory",
        "Reindex Team memory",
        "Refresh a Team's Qdrant collection from canonical Team memory state.",
        [
            _catalog_field(
                "team_id",
                "Team ID",
                "string",
                required=True,
                description="Team whose memory collection should be refreshed.",
            ),
            _catalog_field(
                "project_id",
                "Project ID",
                "string",
                description="Optional project filter; Team-global memories are included.",
            ),
            _catalog_field(
                "directory_path",
                "Directory path",
                "string",
                description="Optional directory config used for semantic provider settings.",
            ),
            _catalog_field(
                "wait",
                "Wait",
                "boolean",
                description="Wait for Qdrant write operations to apply.",
                default_value=False,
            ),
        ],
    ),
)


def _team_internal_event(
    event_type: str, label: str, description: str
) -> WorkflowInternalEventCatalogEntry:
    return WorkflowInternalEventCatalogEntry(
        event_type=event_type,
        label=label,
        description=description,
        source="Team Add-on",
    )


_TEAM_INTERNAL_EVENT_CATALOG: tuple[WorkflowInternalEventCatalogEntry, ...] = (
    _team_internal_event(
        "team.role_queue.wake_requested",
        "Team role queue wake requested",
        "Ready role work or available capacity should trigger a project-scoped dispatch scan.",
    ),
    _team_internal_event(
        "team.task.role_assigned",
        "Team role task assigned",
        "A role task and its concrete owner were reserved atomically with a dispatch and candidate scores.",
    ),
    _team_internal_event(
        CHAT_MESSAGE_QUEUED_EVENT_TYPE,
        "Team chat message queued",
        "Published when an operator chat message is queued for a Team agent.",
    ),
    _team_internal_event(
        EXTERNAL_CHAT_REPLY_REQUESTED_EVENT_TYPE,
        "Team external chat reply requested",
        "Published after an externally originated Team Chat reply is persisted and ready to send.",
    ),
    _team_internal_event(
        AGENT_WAKE_REQUESTED_EVENT_TYPE,
        "Team agent wake requested",
        "Published when a Team agent should be considered for an explicit wake.",
    ),
    _team_internal_event(
        EPHEMERAL_REQUESTED_EVENT_TYPE,
        "Team ephemeral assignment requested",
        "Published when a persistent Team agent requests ephemeral execution work.",
    ),
    _team_internal_event(
        EPHEMERAL_COMPLETED_EVENT_TYPE,
        "Team ephemeral assignment completed",
        "Published when an ephemeral Team assignment returns a result.",
    ),
    _team_internal_event(
        OPERATOR_ESCALATION_EVENT_TYPE,
        "Team operator escalation requested",
        "Published when Team automation needs an operator escalation notification.",
    ),
    _team_internal_event(
        TEAM_OUTBOX_PUBLISH_REQUESTED_EVENT_TYPE,
        "Team outbox publication requested",
        "Published after Team work is committed to the outbox so delivery can begin immediately.",
    ),
    _team_internal_event(
        TEAM_CASE_CLOSED_EVENT_TYPE,
        "Team case closed",
        "Published exactly once when a non-closed Team case transitions to closed.",
    ),
    _team_internal_event(
        GIT_PROMOTION_EVENT_TYPE,
        "Team Git promotion requested",
        "Published when Team release automation requests a Git branch promotion.",
    ),
    _team_internal_event(
        GIT_RELEASE_RECOVERY_EVENT_TYPE,
        "Team Git release recovery requested",
        "Published when Team release automation requests release recovery promotion.",
    ),
    _team_internal_event(
        RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE,
        "Team release verification requested",
        "Published after a release candidate commit is pinned and ready for CI verification.",
    ),
    _team_internal_event(
        TEAM_RELEASE_APPROVED_EVENT_TYPE,
        "Team release approved",
        (
            "Published after every configured gate accepts the pinned candidate, "
            "whether or not canonical branch publication is enabled."
        ),
    ),
    _team_internal_event(
        DELIVERABLE_ACCEPTANCE_VERIFICATION_REQUESTED_EVENT_TYPE,
        "Team deliverable acceptance verification requested (deprecated)",
        ("Legacy event accepted as a no-op for queued work; new Team actions do not publish it."),
    ),
    _team_internal_event(
        HUMAN_REVIEW_REQUESTED_EVENT_TYPE,
        "Team human release review requested",
        "Published when a pinned release candidate is ready for an assigned human reviewer.",
    ),
    _team_internal_event(
        HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE,
        "Team human review preparation requested",
        (
            "Published when workflow-owned preview preparation is required for a "
            "pinned human review request."
        ),
    ),
)
