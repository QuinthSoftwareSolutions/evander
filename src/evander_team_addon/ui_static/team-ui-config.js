// Stable UI options, labels, and initial deep links.
import { readAddonDeepLinkParams, readChatDeepLink, readHumanReviewDeepLink } from "./team-ui-state.js";

const API_BASE = "/api/addons/team-addon";

const taskStates = [
  "pending",
  "in_progress",
  "blocked",
  "waiting_for_input",
  "waiting_for_review",
  "finished",
  "accepted",
  "rejected",
  "cancelled",
];

const directlyCancellableTaskStates = new Set([
  "pending",
  "in_progress",
  "blocked",
  "waiting_for_input",
  "waiting_for_review",
  "rejected",
]);

const directlyEditableTaskStates = taskStates.filter(
  (stateValue) => !["accepted", "cancelled"].includes(stateValue),
);

const caseStatuses = [
  "open",
  "triage",
  "in_progress",
  "waiting",
  "resolved",
  "closed",
];

const activeCaseStatuses = new Set(["open", "triage", "in_progress", "waiting"]);

const terminalCaseStatuses = new Set(["resolved", "closed"]);

const casePageSize = 20;

const taskBoardLanes = [
  { id: "planned", label: "Planned", states: ["pending"] },
  { id: "in-progress", label: "In Progress", states: ["in_progress", "blocked"] },
  { id: "waiting-input", label: "Waiting for Input", states: ["waiting_for_input"] },
  { id: "reviewing", label: "Acceptance / Review", states: ["waiting_for_review"] },
  { id: "done", label: "Done", states: ["finished", "accepted", "rejected", "cancelled"] },
];

const taskBoardStateToLane = new Map(
  taskBoardLanes.flatMap((lane) => lane.states.map((state) => [state, lane.id])),
);

const taskBoardReadinessValues = [
  "actionable",
  "waiting_on_children",
  "waiting_on_dependencies",
  "waiting_for_input",
  "waiting_for_acceptance",
  "waiting_for_review",
  "blocked",
  "resolved",
  "invalid",
  "unknown",
];

const taskAssessmentResolutionReasonMessages = Object.freeze({
  active_direct_acceptance: "A direct acceptance decision is still pending.",
  active_formal_review: "A formal review is still in progress.",
  cancelled_by_authorized_disposition:
    "The task was cancelled by an authorized decision.",
  coordination_task_finished: "The coordination task has finished.",
  intrinsic_blocker_open: "An active blocker is preventing this task from progressing.",
  awaiting_external_input: "The task is paused until its workflow receives external input.",
  outcome_deliverable_accepted: "The outcome deliverable was accepted.",
  outcome_evidenced_noop_accepted: "The evidenced no-op outcome was accepted.",
  rejected_requires_recovery: "The rejected task needs recovery work.",
  support_task_finished: "The support task has finished.",
  task_actionable: "The task is ready for its next action.",
  unresolved_descendants: "One or more descendant tasks are still unresolved.",
  unsatisfied_dependencies: "One or more prerequisite tasks are still unresolved.",
});

const taskBoardViewModes = new Set(["board", "hierarchy"]);

const taskBoardLanePageSize = 20;

const taskBoardHierarchyPageSize = 20;

const recordStates = ["pending", "in_progress", "accepted", "rejected", "blocked"];

const agentStatuses = ["active", "paused", "archived"];

const contextClearingPolicies = [
  ["off", "Off", "Never automatically clear context; manual clear still works."],
  ["idle", "When idle", "Clear after the agent becomes idle following work."],
  ["every_wake", "Every wake", "Start each Team wake with a fresh context."],
  ["work_lineage", "Work lineage", "Resume a separate context for each related task lineage."],
];

const contextClearingPolicyLabels = Object.fromEntries(
  contextClearingPolicies.map(([value, label]) => [value, label]),
);

const avatarStylePresets = [
  ["professional", "Professional"],
  ["playful", "Playful"],
  ["minimal", "Minimal"],
];

const realtimeVoiceOptions = [
  ["alloy", "Alloy"],
  ["ash", "Ash"],
  ["ballad", "Ballad"],
  ["coral", "Coral"],
  ["echo", "Echo"],
  ["sage", "Sage"],
  ["shimmer", "Shimmer"],
  ["verse", "Verse"],
  ["marin", "Marin"],
  ["cedar", "Cedar"],
];

const projectStatuses = ["active", "blocked", "completed", "archived"];

const governanceAgendaTypes = [
  "operator_directive",
  "team_escalation",
  "cost_cap_warning",
  "cost_cap_reached",
  "stale_team",
  "blocked_team",
  "scheduled_checkin",
  "policy_decision",
];

const inboxPriorities = ["low", "normal", "high"];

const gitProviders = [
  ["github", "GitHub"],
  ["azure_devops", "Azure DevOps"],
];

const terminalTaskStates = new Set(["finished", "accepted", "rejected", "cancelled"]);

const terminalRecordStates = new Set(["accepted", "rejected", "cancelled"]);

const workspaceAttentionPageSize = 7;

const defaultPlanningTaskStates = new Set([
  "pending",
  "in_progress",
  "blocked",
  "waiting_for_review",
]);

const chatReadStorageKey = "team-addon:chat:read-markers";

const chatNotificationSoundStorageKey = "team-addon:chat:notification-sound-muted";

const noTeamRecordId = "__teamless__";

const noTeamRecordName = "No team";

const teamStateCollectionKeys = [
  "teams",
  "governance_groups",
  "governance_agenda",
  "governance_runtimes",
  "projects",
  "roles",
  "agents",
  "project_assignments",
  "agent_runtimes",
  "cases",
  "tasks",
  "deliverables",
  "review_requests",
  "outcome_kind_definitions",
  "review_profiles",
  "memories",
  "inbox",
  "blockers",
  "blocker_events",
  "action_records",
  "release_packages",
  "dispatches",
  "worktree_cleanup_candidates",
  "git_work_contexts",
  "ephemeral_assignments",
  "git_promotions",
  "stand_downs",
  "conversations",
  "conversation_messages",
];

const COSTS_REFRESH_INTERVAL_MS = 15000;

const initialHumanReviewDeepLink = readHumanReviewDeepLink();

const initialChatDeepLink = readChatDeepLink();

const initialTaskBoardDeepLink = readAddonDeepLinkParams();

const teamRecordPageSize = 6;

const formDraftFocusKey = "__team_form_focus__";

const runtimeFilterChoices = [
  ["all", "All"],
  ["needs_attention", "Needs attention"],
  ["due_leased_agents", "Due / leased agents"],
  ["blocked_waiting_work", "Blocked / waiting work"],
  ["ephemeral_work", "Ephemeral work"],
];

const blockedWorkKinds = new Set([
  "expired_agent_session",
  "blocked_task",
  "waiting_dependency_task",
  "pending_inbox_item",
  "pending_escalation",
  "failed_action_record",
  "failed_git_promotion",
  "blocked_release_package",
  "team_blocker",
]);

const ephemeralWorkKinds = new Set([
  "failed_ephemeral_assignment",
  "cleanup_pending_ephemeral_assignment",
]);

const failedAttentionKinds = new Set([
  "failed_action_record",
  "failed_git_promotion",
  "failed_ephemeral_assignment",
]);

const runtimeAttentionGroups = new Set([
  "active_obligation",
  "failed_attempt",
  "retained_history",
]);

const runtimeAttentionPageSize = 20;

const runtimeAttentionAgeChoices = [
  ["all", "Any age"],
  ["under_24_hours", "Under 24 hours"],
  ["1_to_7_days", "1–7 days"],
  ["8_to_30_days", "8–30 days"],
  ["over_30_days", "Over 30 days"],
  ["unknown", "Age unavailable"],
];

const runtimeFilterLabels = Object.fromEntries(runtimeFilterChoices);

const timelinePageSize = 10;

const projectRecordPageSize = 10;

const chatMessagePageSize = 50;

const chatTopLoadThresholdPx = 48;

const chatBottomStickinessPx = 96;

const chatNotificationSoundThrottleMs = 1500;

const teamRequestTimeoutMs = 60000;

const deliveryStatusOrder = [
  "pending",
  "waiting_for_acceptance",
  "waiting_for_review",
  "accepted",
  "rejected",
  "blocked",
];

const deliveryFilterOptions = new Set(["attention", "all", ...deliveryStatusOrder]);

const deliveryStatusLabels = {
  pending: "Pending",
  waiting_for_acceptance: "Waiting for acceptance",
  waiting_for_review: "Waiting for review",
  accepted: "Accepted",
  rejected: "Rejected",
  blocked: "Blocked",
  all: "All",
  attention: "Needs attention",
};

const releaseHistoryStates = [...recordStates, "cancelled"];

const RELEASE_VERIFICATION_LIVE_POLL_MS = 2000;

const RELEASE_VERIFICATION_LIVE_FOLLOW_THRESHOLD_PX = 48;

const rolePermissionTooltips = {
  create_root_task: "Allows this role to create top-level project tasks instead of only subtasks inside existing work.",
  manage_outcome_kinds: "Allows a team manager or project lead with this role to create, edit, and archive outcome-kind definitions within their authority scope.",
  prepare_bookkeeping_action: "Allows this role to prepare local Bokio bookkeeping actions for review and human approval.",
  request_payment_preparation: "Allows this role to prepare payment work for human approval and BankID signing in Bokio.",
  request_release: "Allows a same-project role holder to request Team release packages from accepted deliverables. Project leads and managers can already do this through authority.",
  review_bookkeeping_action: "Allows this role to review prepared bookkeeping actions before a human approval executes them.",
};

const fieldTooltips = {
  "setup-template-form:template_id": "Template blueprint used to create the team, project, roles, starter tasks, and review profile.",
  "setup-template-form:team_name": "Human-readable name shown in hierarchy, filters, and timeline rows.",
  "setup-template-form:template_existing_agent_id": "Copy session defaults from an existing agent for this template role.",
  "setup-template-form:template_session_id": "Session ID used when this template-created agent is woken or resumed.",
  "setup-template-form:operator_communication_target_id": "Optional external target for manager escalations, such as a chat channel or operator contact.",
  "setup-template-form:escalate_to_governance": "Route manager escalations to governance agenda instead of operator escalation.",
  "setup-template-form:max_cost_usd": "Optional estimated USD cap for each Team budget window. Team wakes stop when the active window reaches the cap.",
  "setup-template-form:cost_warning_ratio": "Governance wakes when this team's estimated cost reaches this fraction of its cap. 0.8 means 80%.",
  "setup-template-form:project_name": "Human-readable project name shown in project cards and filters.",

  "create-team-form:name": "Human-readable team name shown throughout the Team Add-on UI.",
  "create-team-form:manager_existing_agent_id": "Copy session and display defaults from an existing agent without reusing that agent.",
  "create-team-form:manager_session_id": "Session ID used to resume or wake the manager agent.",
  "create-team-form:manager_display_name": "Optional display name. If blank, the generated agent ID is shown.",
  "create-team-form:manager_role_name": "Human-readable role name for the manager role.",
  "create-team-form:operator_communication_target_id": "Optional external target for escalations raised by this team's manager.",
  "create-team-form:escalate_to_governance": "Route manager escalations to governance agenda instead of operator escalation.",
  "create-team-form:max_cost_usd": "Optional estimated USD cap for each Team budget window. Team wakes stop when the active window reaches the cap.",
  "create-team-form:cost_warning_ratio": "Governance wakes when this team's estimated cost reaches this fraction of its cap. 0.8 means 80%.",
  "create-team-form:cost_budget_renewal_mode": "Manual budgets require an operator reset. Monthly budgets renew at 00:00 UTC on the first day of each month without carryover.",

  "create-governance-group-form:name": "Human-readable portfolio or governance group name.",
  "create-governance-group-form:manager_agent_id": "Existing persistent agent that wakes for governance agenda, cost, and blocker signals.",
  "create-governance-group-form:member_team_ids": "Teams supervised by this governance group. Their internal reporting chains stay unchanged.",
  "create-governance-group-form:governance_goal_name": "Required operator-facing name for this governance goal.",
  "create-governance-group-form:governance_goal_description": "Required description of the intended outcome.",
  "create-governance-group-form:governance_goal_state": "Current governance goal state.",
  "create-governance-group-form:max_cost_usd": "Optional advisory lifetime USD cap for the whole group. Per-team caps remain the hard wake guard.",
  "create-governance-agenda-form:governance_group_id": "Governance group whose manager should see this agenda item.",
  "create-governance-agenda-form:item_type": "Governance signal type that should drive the manager wake reason.",
  "create-governance-agenda-form:priority": "Agenda priority. High items are shown first.",
  "create-governance-agenda-form:team_id": "Optional member team related to this governance item.",
  "create-governance-agenda-form:title": "Short agenda title shown to the governance manager.",
  "create-governance-agenda-form:summary": "Governance context for the manager to inspect.",

  "create-project-form:team_id": "Team that owns this project. The project lead and later project agents must belong to the same team.",
  "create-project-form:name": "Human-readable project name shown in project cards, filters, and timelines.",
  "create-project-form:lead_agent_id": "Existing same-team agent that will lead this project. The team manager may also be selected.",
  "create-project-form:directory_path": "Provider-neutral configuration and access scope for this project, including projects with no Git repository.",
  "create-project-form:goal_description": "Optional initial goal for the project backlog.",

  "create-case-form:team_id": "Team that owns this case.",
  "create-case-form:project_id": "Optional project scope. Linked tasks must belong to this project when it is set.",
  "create-case-form:owner_agent_id": "Optional same-team agent responsible for triage or lifecycle follow-up.",
  "create-case-form:status": "Current case lifecycle state. Blank creates an open case.",
  "create-case-form:priority": "Case triage priority used in wake context ordering.",
  "create-case-form:title": "Short case title shown in case lists and agent wake context.",
  "create-case-form:description": "Intake details, ticket summary, or lifecycle context.",
  "create-case-form:source": "Optional external system name such as github, jira, or zendesk.",
  "create-case-form:source_ref": "Optional external ticket, issue, or conversation id.",
  "create-case-form:tags": "Optional tags, one per line or comma-separated.",
  "create-case-form:task_ids": "Optional linked task IDs, one per line or comma-separated.",

  "register-agent-form:existing_agent_id": "Copy defaults from an existing agent. This helps reuse session conventions for the new agent.",
  "register-agent-form:team_id": "Team this persistent agent belongs to.",
  "register-agent-form:project_id": "Project this agent works in. Project agents must belong to the selected team.",
  "register-agent-form:role_id": "First-class role assigned to this agent.",
  "register-agent-form:existing_session_id": "Known core VM or container-worker session to link to this Team agent.",
  "register-agent-form:create_session": "Create a new core VM or container-worker session, then register it as this Team agent.",
  "register-agent-form:session_id": "Session ID used when this agent is woken or resumed.",
  "register-agent-form:display_name": "Optional display name. If blank, the generated agent ID is shown.",
  "register-agent-form:reports_to_agent_id": "Supervisor for routing, permissions, status requests, and hierarchy display.",
  "register-agent-form:max_concurrent": "Maximum ephemeral worker agents this persistent agent may spawn at the same time.",
  "edit:agent:voice": "Base OpenAI Realtime voice used for this agent's browser calls.",
  "edit:agent:speaking_style": "Optional spoken delivery guidance appended to this agent's browser voice sessions.",
  "edit:agent:context_clearing_policy": "Controls when Team wakes start this agent with a fresh context.",
  "edit:agent:context_clearing_idle_grace_minutes": "For idle clearing, keep the current context while new work arrives within this many continuous idle minutes. Zero preserves immediate idle clearing.",
  "edit:agent:auto_context_clearing_enabled": "Legacy setting equivalent to context clearing when idle.",

  "create-task-form:owner_agent_id": "Persistent agent responsible for completing this task.",
  "create-task-form:project_id": "Project that owns this task and constrains parent and dependency links.",
  "create-task-form:case_id": "Optional intake case this execution task belongs to.",
  "create-task-form:priority": "Lower numbers sort earlier in each agent's task queue.",
  "create-task-form:title": "Short task title shown in project and agent task lists.",
  "create-task-form:description": "Work instructions for the owning agent.",
  "create-task-form:task_kind": "Support work may produce a deliverable. Coordination supervises children, finishes directly, and never receives a Git integration context.",
  "create-task-form:created_by_agent_id": "Agent credited as the task creator in task history and timeline attribution.",
  "create-task-form:state": "Optional initial task state. Blank creates a pending task.",
  "create-task-form:parent_task_id": "Optional parent task in the same project.",
  "create-task-form:originating_task_id": "Optional provenance link. Final integration for coordinated work must use the coordination task as its origin while leaving Parent task empty.",
  "create-task-form:git_base_branch": "Optional Git starting branch. Blank inherits the parent task snapshot or the project default when the root task is created.",
  "create-task-form:dependency_task_ids": "Optional dependency task IDs, one per line or comma-separated. Dependencies must be in the same project.",
  "create-task-form:integration_boundary": "Designate this task as the explicit point that integrates and accepts a final deliverable.",
  "create-task-form:require_git_provenance": "Require this task to resolve through an accepted deliverable carrying server-verified Git branch and commit provenance. This can be used with or without an integration boundary.",
  "create-task-form:required_contribution_task_ids": "Child or dependency task IDs whose accepted deliverables must be incorporated exactly.",

  "update-task-form:task_id": "Task to update. Selecting a task loads its current editable fields.",
  "update-task-form:state": "New task state. Leave blank to keep the current state.",
  "update-task-form:case_id": "Case link to set or clear for this task.",
  "update-task-form:priority": "New priority. Lower numbers sort earlier.",
  "update-task-form:title": "New title. Leave blank to keep the current title.",
  "update-task-form:description": "New description. Leave blank to keep the current description.",
  "update-task-form:parent_task_id": "Parent task in the same project. Clear the value to remove the parent link.",
  "update-task-form:dependency_task_ids": "Replacement dependency list, one per line or comma-separated.",
  "update-task-form:integration_boundary": "Designate this task as the explicit integration and final-acceptance boundary.",
  "update-task-form:require_git_provenance": "Require this task to resolve through an accepted deliverable carrying server-verified Git provenance. This can be used with or without an integration boundary.",
  "update-task-form:required_contribution_task_ids": "Child or dependency task IDs whose accepted deliverables must be incorporated exactly.",

  "create-review-profile-form:team_id": "Team that owns this review profile.",
  "create-review-profile-form:project_id": "Optional project scope. Blank makes the profile team-wide.",
  "create-review-profile-form:name": "Human-readable profile name shown when requesting reviews.",
  "create-review-profile-form:description": "Optional summary of when this profile should be used.",
  "create-role-form:team_id": "Team that owns this role.",
  "create-role-form:project_id": "Optional project scope. Blank makes the role team-wide.",
  "create-role-form:name": "Human-readable role name shown in the UI and wake prompts.",
  "create-role-form:description": "Optional role responsibility summary.",
  "create-role-form:permissions": "Selectable permissions granted to agents assigned this role. Hover each permission for what it allows.",
  "create-role-form:max_concurrent": "Default maximum ephemeral worker agents for agents created with this role.",

  "create-review-profile-form:applicable_role_ids": "Optional reviewer roles. Blank allows all reviewer roles.",
  "create-review-profile-form:criterion_label": "Reviewer-facing criterion name.",
  "create-review-profile-form:criterion_description": "Optional notes that clarify what the reviewer should check.",
  "create-review-profile-form:criterion_id": "Optional stable key. Leave blank to generate one from the label.",
  "create-review-profile-form:criterion_required": "Required criteria should be satisfied or explicitly failed in review decisions.",

  "record-deliverable-form:task_id": "Task this deliverable completes or advances.",
  "record-deliverable-form:producer_agent_id": "Agent that produced the deliverable.",
  "record-deliverable-form:summary": "Short summary of the delivered work.",
  "record-deliverable-form:artifact_refs": "Optional artifact references, one per line or comma-separated.",
  "record-deliverable-form:state": "Initial deliverable state. Blank records it as pending.",

  "record-review-form:deliverable_id": "Deliverable that needs review.",
  "record-review-form:reviewer_agent_id": "Agent assigned to review the deliverable.",
  "record-review-form:review_profile_id": "Optional checklist profile applied to this review.",
  "record-review-form:instructions": "Optional reviewer-specific instructions.",
  "record-review-form:findings": "Optional initial findings, one per line or comma-separated.",
  "record-review-form:state": "Initial review state. Blank records it as pending.",

  "decide-review-form:review_request_id": "Review request to decide.",
  "decide-review-form:state": "Decision to apply to the review, deliverable, and related task state.",
  "decide-review-form:findings": "Decision findings, one per line or comma-separated.",
  "decide-review-form:criteria": "Criterion result rows in criterion_id | state | finding | evidence refs format. Separate evidence refs with commas.",

  "edit:team:name": "Rename the team without changing its immutable ID.",
  "edit:team:operator_communication_target_id": "Set or clear the external target used for operator escalations.",
  "edit:team:escalate_to_governance": "Route manager escalations to governance instead of operator escalation when true.",
  "edit:team:max_cost_usd": "Set or clear the estimated USD cap that suspends future Team wakes when the active budget window reaches it.",
  "edit:team:cost_warning_ratio": "Set the portion of this team's cap that produces a governance warning. 0.8 means 80%; clearing resets it to 0.8.",
  "edit:team:cost_budget_renewal_mode": "Choose manual reset or an automatic monthly UTC renewal. Changing this setting does not reset the current budget window.",
  "edit:team:archived": "Archived teams stay in state; archiving a team also archives its agents.",
  "edit:governance-group:name": "Rename the governance group without changing its immutable ID.",
  "edit:governance-group:manager_agent_id": "Persistent agent responsible for governance agenda and portfolio signals. Options lead with display name and role.",
  "edit:governance-group:governance_member_team_search": "Filter the named team checklist. Exact team IDs are also searchable.",
  "edit:governance-group:governance_goal_name": "Required operator-facing name for this governance goal.",
  "edit:governance-group:governance_goal_description": "Required description of the intended outcome.",
  "edit:governance-group:governance_goal_state": "Current governance goal state.",
  "edit:governance-group:max_cost_usd": "Optional advisory lifetime USD cap for the governed portfolio.",
  "edit:governance-group:archived": "Archived governance groups remain retained but leave active views.",
  "edit:project:name": "Rename the project without changing its immutable ID.",
  "edit:project:lead_agent_id": "Transfer project-lead authority to an existing agent in this team. The former lead remains a project member.",
  "edit:project:status": "Manual project status override. Later workflow actions may update this again.",
  "edit:project:goals": "Replace project goals using id | description | state rows.",
  "edit:project:human_review_enabled": "Require an authenticated, assigned Admin Web reviewer to decide the pinned release candidate.",
  "edit:project:human_reviewer_group_ids": "Directory-scoped Admin Web groups whose current eligible users may receive this review.",
  "edit:project:human_review_checklist": "Add, remove, and order the checks a human must complete before approving a release.",
  "edit:project:human_review_test_instructions": "Stable manual test instructions snapshotted onto each release request.",
  "edit:project:human_review_preparation_mode": "Manual uses these instructions directly; workflow requires a prepared preview target before review.",
  "edit:agent:project_ids": "Assign the agent to one or more projects. Project leads remain assigned until the lead is transferred from that project's editor.",
  "edit:agent:role_id": "First-class role assigned to this persistent agent.",
  "edit:agent:session_id": "Session ID used when this agent is woken or resumed.",
  "edit:agent:display_name": "Optional display name. Clear it to fall back to the agent ID.",
  "edit:agent:reports_to_agent_id": "Supervisor in the same team. The backend rejects reporting cycles.",
  "edit:agent:max_concurrent": "Maximum ephemeral worker agents this persistent agent may spawn at once.",
  "edit:agent:status": "Active agents can receive work; paused or archived agents remain in state for audit context.",
  "edit:case:project_id": "Optional project scope. Existing linked tasks that no longer match are unlinked.",
  "edit:case:owner_agent_id": "Optional same-team owner responsible for triage or lifecycle follow-up.",
  "edit:case:status": "Case lifecycle state.",
  "edit:case:priority": "Case triage priority used in wake context ordering.",
  "edit:case:title": "Rename the case without changing its immutable ID.",
  "edit:case:description": "Update case intake or ticket context.",
  "edit:case:source": "External system name such as github, jira, or zendesk. Clear to remove.",
  "edit:case:source_ref": "External ticket, issue, or conversation id. Clear to remove.",
  "edit:case:tags": "Replacement tags, one per line or comma-separated.",
  "edit:case:task_ids": "Replacement linked task IDs, one per line or comma-separated.",
  "edit:task:state": "Task state to save.",
  "edit:task:case_id": "Case link to set or clear for this task.",
  "edit:task:priority": "Lower numbers sort earlier in each agent's task queue.",
  "edit:task:title": "Rename the task without changing its immutable ID.",
  "edit:task:description": "Update the instructions for the owning agent.",
  "edit:task:parent_task_id": "Parent task in the same project. Clear the value to remove the parent link.",
  "edit:task:git_base_branch": "The task's durable Git starting branch. It locks after Git preparation, deliverable submission, or promotion creation.",
  "edit:task:dependency_task_ids": "Replacement dependency list, one per line or comma-separated.",
  "edit:task:integration_boundary": "Designate this task as the explicit integration and final-acceptance boundary.",
  "edit:task:require_git_provenance": "Require this task to resolve through an accepted deliverable carrying server-verified Git provenance. This can be used with or without an integration boundary.",
  "edit:task:required_contribution_task_ids": "Child or dependency task IDs whose accepted deliverables must be incorporated exactly.",
  "edit:review-profile:project_id": "Optional project scope. Clear it to make the profile team-wide.",
  "edit:review-profile:name": "Rename the review profile without changing its immutable ID.",
  "edit:review-profile:description": "Optional summary of when this profile should be used.",
  "edit:role:name": "Rename the role without changing its immutable ID.",
  "edit:role:project_id": "Optional project scope. Clear it to make the role team-wide.",
  "edit:role:description": "Optional role responsibility summary.",
  "edit:role:permissions": "Selectable permissions granted to this role. Hover each permission for what it allows.",
  "edit:role:max_concurrent": "Default maximum ephemeral worker agents for agents created with this role.",
  "edit:role:archived": "Archived roles stay in state but should not be assigned to new agents.",
  "edit:review-profile:applicable_role_ids": "Optional reviewer roles. Blank allows all reviewer roles.",
  "edit:review-profile:criterion_label": "Reviewer-facing criterion name.",
  "edit:review-profile:criterion_description": "Optional notes that clarify what the reviewer should check.",
  "edit:review-profile:criterion_id": "Optional stable key. Leave blank to generate one from the label.",
  "edit:review-profile:criterion_required": "Required criteria should be satisfied or explicitly failed in review decisions.",
  "edit:review-profile:archived": "Archived profiles stay in state but cannot be selected for new review requests.",

  repo_source: "Transient Git URL or local checkout used to register a managed repository.",
  managed_repo_id: "Stable Evander managed repository ID stored with the project.",
  base_branch: "Default base branch captured by newly created root tasks. Existing tasks keep their own snapshot when this changes.",
  canonical_release_branch_prefix: "Prefix used when constructing canonical release branch names.",
  task_author_name: "Name recorded as the author of this task’s future Git commits. Blank inherits on creation and uses the project default after clearing.",
  task_author_email: "Email recorded for this task’s commit author. Child and follow-up tasks copy this identity.",
  agent_author_name: "Human or service identity recorded as the author of Team agent commits.",
  agent_author_email: "Email recorded for the configured Team agent commit author.",
  agent_committer_name: "Automation identity recorded as the committer of Team agent commits.",
  agent_committer_email: "Email recorded for the configured Team agent commit committer.",
};

const decisionStates = ["accepted", "rejected", "blocked"];

const inboxItemLabels = {
  operator_directive: "Operator directive",
  question: "Question",
  status_request: "Status request",
  progress_report: "Progress report",
  deliverable_notice: "Deliverable notice",
  escalation: "Escalation",
};

const timelineTypeOrder = [
  "task",
  "inbox_item",
  "deliverable",
  "review",
  "action",
  "ephemeral_assignment",
  "git_promotion",
  "release_package",
];

const lockedMutationControlSelectors = [
  ["[data-team-copy-open]", "copy teams"],
  ["[data-team-copy-session-create]", "create a core agent session"],
  ["[data-teams-create-team-open]", "create teams"],
  ["[data-project-create-toggle]", "create projects"],
  ["[data-project-review-profile-create-toggle]", "create review profiles"],
  ["[data-governance-create-toggle]", "create governance groups"],
  ["[data-team-inline-create-toggle]", "add Team records"],
  ["[data-core-agent-session-create]", "create a core agent session"],
  ["[data-git-import-button]", "import Git repositories"],
  ["[data-governance-goal-add]", "edit governance goals"],
  ["[data-governance-goal-remove]", "edit governance goals"],
  ["[data-governance-goal-move]", "reorder governance goals"],
  ["[data-governance-member-team-remove]", "change governance member teams"],
  ["[data-review-profile-criterion-add]", "edit review profile criteria"],
  ["[data-review-profile-criterion-remove]", "edit review profile criteria"],
  ["[data-human-review-checklist-add]", "edit human review checklist"],
  ["[data-human-review-checklist-remove]", "edit human review checklist"],
  ["[data-human-review-checklist-move]", "reorder human review checklist"],
  ["[data-human-review-finding-add]", "edit human review findings"],
  ["[data-human-review-finding-remove]", "edit human review findings"],
  ["[data-context-rotation-agent-id]", "clear agent context"],
  ["[data-stand-down-agent-id]", "stand down agents"],
  ["[data-stand-down-revoke-agent-id]:not(:disabled)", "revoke stand-downs"],
  ["[data-task-cancel-id]:not(:disabled)", "cancel tasks"],
  ["[data-dismiss-action-id]", "dismiss failed actions"],
  ["[data-resume-release-recovery-blocker-id]", "resume release recoveries"],
  ["[data-candidate-correction-recovery-id]", "recover candidate correction successors"],
  ["[data-start-dependency-composition-recovery-id]", "start dependency composition recoveries"],
  ["[data-resolve-terminal-blocker-id]", "resolve terminal blockers"],
  ["[data-release-gate-retry]", "retry release gates"],
  ["[data-release-cancel]", "cancel releases"],
  ["[data-release-gate-obligation-waiver]", "waive carried release-gate obligations"],
  ["[data-worktree-cleanup-retry]", "retry worktree cleanup"],
  [
    "[data-repair-cancelled-task-dependencies]",
    "repair cancelled-task dependencies",
  ],
  ["form[data-post-release-report-form] button[type='submit']", "report post-release issues"],
  ["[data-human-review-resume-id]", "resume human reviews"],
  ["[data-human-review-reassign-id]", "reassign human reviews"],
  ["[data-governance-agenda-handle-id]", "mark governance agenda handled"],
  ["[data-avatar-generate-agent-id]", "generate avatar previews"],
  ["[data-avatar-accept-agent-id]", "accept avatar previews"],
  ["[data-avatar-upload-agent-id]", "upload avatars"],
  ["[data-avatar-remove-agent-id]", "remove avatars"],
  ["[data-cost-backfill-refresh]", "refresh transcript costs"],
  ["[data-agent-call-agent-id]", "start voice calls"],
  ["[data-agent-call-mute]", "change voice calls"],
  ["[data-agent-call-end]", "end voice calls"],
  ["button.status-action", "retry saves"],
  ["form[data-endpoint] button[type='submit']", "save changes"],
  ["form[data-template-endpoint] button[type='submit']", "save changes"],
  ["form[data-team-inline-create-form] button[type='submit']", "save changes"],
  ["form[data-delegated-create-form] button[type='submit']", "save changes"],
  ["form[data-org-action-form] button[type='submit']", "save changes"],
  ["form[data-review-decision-form] button[type='submit']", "record review decisions"],
  ["form[data-human-review-decision-form] button[type='submit']", "record human review decisions"],
  ["#update-task-form button[type='submit']", "update tasks"],
  ["#decide-review-form button[type='submit']", "record review decisions"],
  ["#edit-record-form button[type='submit']", "save changes"],
  ["#task-cancellation-form button[type='submit']", "cancel tasks"],
  ["#chat-attachment-policy-form button[type='submit']", "configure Team Chat attachments"],
];

const orgFlowLayout = {
  agentNodeWidth: 352,
  teamNodeWidth: 352,
  governanceNodeWidth: 352,
  leafGap: 452,
  teamGap: 160,
  governanceRowGap: 240,
  teamToAgentGap: 280,
  hierarchyLevelGap: 260,
};

const RELEASE_GATE_LABELS = {
  agent_review: "Agent review",
  verification: "Automated verification",
  human_review: "Human review",
};

export { API_BASE, taskStates, directlyCancellableTaskStates, directlyEditableTaskStates, caseStatuses, activeCaseStatuses, terminalCaseStatuses, casePageSize, taskBoardLanes, taskBoardStateToLane, taskBoardReadinessValues, taskAssessmentResolutionReasonMessages, taskBoardViewModes, taskBoardLanePageSize, taskBoardHierarchyPageSize, recordStates, agentStatuses, contextClearingPolicies, contextClearingPolicyLabels, avatarStylePresets, realtimeVoiceOptions, projectStatuses, governanceAgendaTypes, inboxPriorities, gitProviders, terminalTaskStates, terminalRecordStates, workspaceAttentionPageSize, defaultPlanningTaskStates, chatReadStorageKey, chatNotificationSoundStorageKey, noTeamRecordId, noTeamRecordName, teamStateCollectionKeys, COSTS_REFRESH_INTERVAL_MS, initialHumanReviewDeepLink, initialChatDeepLink, initialTaskBoardDeepLink, teamRecordPageSize, formDraftFocusKey, runtimeFilterChoices, blockedWorkKinds, ephemeralWorkKinds, failedAttentionKinds, runtimeAttentionGroups, runtimeAttentionPageSize, runtimeAttentionAgeChoices, runtimeFilterLabels, timelinePageSize, projectRecordPageSize, chatMessagePageSize, chatTopLoadThresholdPx, chatBottomStickinessPx, chatNotificationSoundThrottleMs, teamRequestTimeoutMs, deliveryStatusOrder, deliveryFilterOptions, deliveryStatusLabels, releaseHistoryStates, RELEASE_VERIFICATION_LIVE_POLL_MS, RELEASE_VERIFICATION_LIVE_FOLLOW_THRESHOLD_PX, rolePermissionTooltips, fieldTooltips, decisionStates, inboxItemLabels, timelineTypeOrder, lockedMutationControlSelectors, orgFlowLayout, RELEASE_GATE_LABELS };
