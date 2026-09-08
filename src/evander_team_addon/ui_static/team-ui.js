import { createTeamMailController } from "./team-mail.js";
import { createTeamActivityController } from "./team-activity.js";

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
const terminalRecordStates = new Set(["accepted", "rejected"]);
const workspaceAttentionPageSize = 7;
const defaultPlanningTaskStates = new Set([
  "pending",
  "in_progress",
  "blocked",
  "waiting_for_review",
]);
const defaultTeamView = "people";
const teamViewStorageKey = "team-addon:active-view";
const chatReadStorageKey = "team-addon:chat:read-markers";
const chatNotificationSoundStorageKey = "team-addon:chat:notification-sound-muted";
const noTeamRecordId = "__teamless__";
const noTeamRecordName = "No team";
const teamViewIds = new Set([
  "workspace",
  "operations",
  "delivery",
  "memory",
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
  "org",
]);
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

let state = emptyState();
let taskPreparationInvalidationByTaskId = new Map();
let costState = emptyCostState();
let runtimeFilter = null;
let runtimeFilterTouched = false;
let templates = [];
let communicationTargets = [];
let timelineState = emptyTimelineState();
let chatState = emptyChatState();
let chatRequestId = 0;
let chatNewPickerOpen = false;
let selectedChatOperatorUserId = null;
let chatSending = false;
const pendingChatAttachments = new Map();
const chatComposerDrafts = new Map();
let chatStream = null;
let chatStreamConversationId = null;
let chatStreamErrorRefreshPending = false;
let chatNotificationSoundMuted = readChatNotificationSoundMuted();
let chatNotificationAudioContext = null;
let lastChatNotificationSoundAt = 0;
let bootstrapLoaded = false;
const loadedTeamViews = new Set();
const loadingTeamViews = new Map();
const pendingForcedTeamViewLoads = new Map();
let teamViewCacheGeneration = 0;
let stateStream = null;
let stateStreamErrorRefreshPending = false;
let chatReadMarkers = readChatReadMarkers();
let timelineLoading = false;
let timelineRequestId = 0;
let timelinePage = 1;
let costsRequestId = 0;
let costBackfillRequestId = 0;
let costBackfillLoading = false;
let showArchived = false;
let showHandledGovernanceAgenda = false;
const initialHumanReviewDeepLink = readHumanReviewDeepLink();
const initialChatDeepLink = readChatDeepLink();
let selectedChatConversationId = initialChatDeepLink.conversationId || null;
let chatMobileConversationListOpen = !selectedChatConversationId;
let activeTeamView =
  initialHumanReviewDeepLink.enabled
    ? "delivery"
    : initialChatDeepLink.enabled
      ? "chat"
      : readStoredTeamView();
let teamPrivilegeUnlocked = readTeamPrivilegedUnlockedFromLocation();
const lockedMutationControlStates = new WeakMap();
let selectedPlanningTaskStates = new Set(defaultPlanningTaskStates);
// Native disclosure state belongs to the DOM, so keep its intentional state
// separately whenever a view renderer replaces that DOM. Rendered keys include
// their Team view so shared renderers cannot couple hidden view copies.
const openTeamDisclosures = new Set();
let selectedOrgTeamId = "all";
let selectedProjectFocusId = null;
let projectCreateFormOpen = false;
let projectReviewProfileCreateProjectId = null;
const projectRecordSectionPages = {};
const projectRecordShowRejected = new Set();
let selectedTeamRecordKind = "agent";
let selectedTeamRecordId = null;
const expandedTeamRecordGroupIds = new Set();
const teamRecordGroupTabs = {};
const teamRecordGroupPages = {};
const openTeamInlineCreateForms = new Set();
const teamRecordPageSize = 6;
let selectedTaskBoardTeamId = "";
let selectedTaskBoardProjectId = "";
let taskBoardViewMode = "board";
let selectedTaskBoardTaskId = "";
let taskBoardShowCompleted = false;
let taskBoardQuery = "";
let taskBoardAssigneeId = "";
let taskBoardStateFilter = "";
let taskBoardReadinessFilter = "";
let taskBoardRootTaskId = "";
let taskBoardCompletedSince = "";
let taskBoardCompletedThrough = "";
const taskBoardLanePages = new Map();
const expandedTaskBoardTaskIds = new Set();
const taskBoardHierarchyChildLimits = new Map();
let taskBoardHierarchyRootLimit = taskBoardHierarchyPageSize;
let taskBoardHierarchyInitialized = false;
let caseShowHistory = false;
let caseQuery = "";
let caseStatusFilter = "active";
const caseTeamPages = new Map();
let activeAgentVoiceCall = null;
let teamsCreateTeamPanelOpen = false;
let governanceCreateGroupPanelOpen = false;
let orgContextMenuState = null;
const formDraftRegistry = new Map();
const formDraftFocusKey = "__team_form_focus__";
let pendingFormFocusDraft = null;
let pendingConflictRetry = null;
let coreAgentSessions = [];
let coreAgentSessionsDirectoryPath = "";
let coreAgentSessionsLoading = null;
const pendingTeamAgentCreateForms = new Map();

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
let pendingChatScroll = null;
const chatOlderMessageLoads = new Set();

const elements = {
  revisionBadge: document.querySelector("#revision-badge"),
  statusMessage: document.querySelector("#status-message"),
  runtimeOperationsView: document.querySelector("#runtime-operations-view"),
  runtimeFilterButtons: Array.from(document.querySelectorAll("[data-runtime-filter]")),
  reviewDeliveryView: document.querySelector("#review-delivery-view"),
  memoryView: document.querySelector("[data-memory-view]"),
  hierarchyView: document.querySelector("#hierarchy-view"),
  agentsView: document.querySelector("#agents-view"),
  rolesView: document.querySelector("#roles-view"),
  projectsView: document.querySelector("#projects-view"),
  reviewProfilesView: document.querySelector("#review-profiles-view"),
  releasesView: document.querySelector("#releases-view"),
  templateSelect: document.querySelector("#template-select"),
  templatePreview: document.querySelector("#template-preview"),
  templateRoleFields: document.querySelector("[data-template-role-fields]"),
  templateGitFields: document.querySelector("[data-template-git-fields]"),
  timelineSummary: document.querySelector("#timeline-summary"),
  timelineStatus: document.querySelector("#timeline-status"),
  timelineView: document.querySelector("#timeline-view"),
  timelineTeamFilter: document.querySelector("#timeline-team-filter"),
  timelineProjectFilter: document.querySelector("#timeline-project-filter"),
  timelineAgentFilter: document.querySelector("#timeline-agent-filter"),
  timelineTypeFilter: document.querySelector("#timeline-type-filter"),
  timelineStatusFilter: document.querySelector("#timeline-status-filter"),
  timelineAttentionFilter: document.querySelector("#timeline-attention-filter"),
  projectsFocusList: document.querySelector("[data-projects-focus-list]"),
  projectsFocusDetail: document.querySelector("[data-projects-focus-detail]"),
  showArchivedToggle: document.querySelector("#show-archived-toggle"),
  showHandledGovernanceAgendaToggle: document.querySelector("#show-handled-governance-agenda-toggle"),
  teamViewButtons: Array.from(document.querySelectorAll("[data-team-view]")),
  teamViewPanels: Array.from(document.querySelectorAll("[data-team-view-panel]")),
  teamViewActionButtons: Array.from(document.querySelectorAll("[data-team-view-action]")),
  orgFocusSummary: document.querySelector("#org-focus-summary"),
  orgFocusTeamTabs: document.querySelector("#org-focus-team-tabs"),
  orgFocusView: document.querySelector("#org-focus-view"),
  chatPage: document.querySelector("#team-chat-page"),
  chatConversationsView: document.querySelector("#chat-conversations-view"),
  chatThreadView: document.querySelector("#chat-thread-view"),
  chatNewPicker: document.querySelector("#chat-new-picker"),
  chatOperatorView: document.querySelector("#chat-operator-view"),
  mailTeamFilter: document.querySelector("#mail-team-filter"),
  mailAgentFilter: document.querySelector("#mail-agent-filter"),
  mailDirectionFilter: document.querySelector("#mail-direction-filter"),
  mailTypeFilter: document.querySelector("#mail-type-filter"),
  mailSearchInput: document.querySelector("#mail-search-input"),
  mailStatus: document.querySelector("#mail-status"),
  mailList: document.querySelector("#mail-list"),
  mailPagination: document.querySelector("#mail-pagination"),
  mailReader: document.querySelector("#mail-reader"),
  mailDirectiveOpen: document.querySelector("[data-mail-directive-open]"),
  mailDirectiveForm: document.querySelector("[data-mail-directive-form]"),
  mailDirectiveTeam: document.querySelector("[data-mail-directive-team]"),
  mailDirectiveProject: document.querySelector("[data-mail-directive-project]"),
  mailDirectiveTitle: document.querySelector("[data-mail-directive-title]"),
  mailDirectiveMessage: document.querySelector("[data-mail-directive-message]"),
  mailDirectiveRecipient: document.querySelector("[data-mail-directive-recipient]"),
  mailDirectiveStatus: document.querySelector("[data-mail-directive-status]"),
  mailDirectiveSubmit: document.querySelector("[data-mail-directive-submit]"),
  activityTeamFilter: document.querySelector("#activity-team-filter"),
  activityAgentFilter: document.querySelector("#activity-agent-filter"),
  activityStatusFilter: document.querySelector("#activity-status-filter"),
  activityDispatchFilter: document.querySelector("#activity-dispatch-filter"),
  activitySearchInput: document.querySelector("#activity-search-input"),
  activityStatus: document.querySelector("#activity-status"),
  activityList: document.querySelector("#activity-list"),
  activityPagination: document.querySelector("#activity-pagination"),
  teamsCreateTeamPanel: document.querySelector("#teams-create-team-panel"),
  teamsCreateTeamForm: document.querySelector("#create-team-from-teams-form"),
  governanceCreateGroupPanel: document.querySelector("#governance-create-panel"),
  governanceCreateGroupForm: document.querySelector("#create-governance-group-form"),
  governanceCreateGroupToggle: document.querySelector("[data-governance-create-toggle]"),
  editModal: document.querySelector("#edit-modal"),
  editModalTitle: document.querySelector("#edit-modal-title"),
  editForm: document.querySelector("#edit-record-form"),
  editFormBody: document.querySelector("#edit-form-body"),
  taskCancellationModal: document.querySelector("#task-cancellation-modal"),
  taskCancellationModalTitle: document.querySelector("#task-cancellation-modal-title"),
  taskCancellationSubject: document.querySelector("#task-cancellation-subject"),
  taskCancellationForm: document.querySelector("#task-cancellation-form"),
  chatAttachmentPolicyModal: document.querySelector("#chat-attachment-policy-modal"),
  chatAttachmentPolicyForm: document.querySelector("#chat-attachment-policy-form"),
};

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

let selectedDeliveryFilter = "attention";
let selectedDeliveryProjectId = "";
let selectedReleaseHistoryState = "";
let releaseHistoryDateFrom = "";
let releaseHistoryDateThrough = "";
let workspaceAttentionPage = 1;
let myHumanReviewsOnly = initialHumanReviewDeepLink.enabled;
let focusedHumanReviewRequestId = initialHumanReviewDeepLink.requestId;
let humanReviewQueue = {
  loaded: false,
  loading: false,
  error: null,
  requests: [],
};
const releaseDetailCache = new Map();
const expandedReleaseDetailIds = new Set();
const releaseVerificationLiveCache = new Map();
const releaseVerificationLivePollTimers = new Map();
const RELEASE_VERIFICATION_LIVE_POLL_MS = 2000;
const postReleaseReportResults = new Map();
const runtimeAttentionFilters = {
  kind: "all",
  age: "all",
  team: "all",
  project: "all",
  agent: "all",
};
let runtimeAttentionShowHistory = false;
const runtimeAttentionGroupPages = new Map();
let tooltipCounter = 0;

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
  "create-task-form:created_by_agent_id": "Agent credited as the task creator in task history and timeline attribution.",
  "create-task-form:state": "Optional initial task state. Blank creates a pending task.",
  "create-task-form:parent_task_id": "Optional parent task in the same project.",
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
  "edit:agent:project_id": "Move the agent within its team. Project leads must remain assigned to their project.",
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

document.addEventListener("DOMContentLoaded", () => {
  initializeGovernanceCreateGroupGoals();
  bindTopLevelTabs();
  bindForms();
  bindRecordEditing();
  bindExistingAgentPickers();
  bindTeamAgentSessionControls();
  bindProjectLeadSelects();
  bindAgentEditTeamScopedSelects();
  bindRuntimeFilters();
  bindWorkspaceAttentionPagination();
  bindDeliveryFilters();
  bindPlanningFilters();
  bindTeamDisclosures();
  bindProjectFocus();
  bindTaskBoardSelectors();
  bindCaseFilters();
  bindOrgFocusTabs();
  bindOrgFlowEvents();
  bindArchivedVisibilityToggle();
  bindGovernanceAgendaVisibilityToggle();
  bindInboxPopoverPositioning();
  mailController.bind();
  activityController.bind();
  elements.templateSelect?.addEventListener("change", () => {
    renderTemplateSetupFields();
  });
  bindTimelineFilters();
  bindTimelinePagination();
  bindPageLifecycle();
  bindPrivilegeMessages();
  decorateFieldTooltips(document);
  void bootTeamAddon();
});

function emptyState() {
  return {
    revision: 0,
    teams: {},
    governance_groups: {},
    governance_agenda: {},
    governance_runtimes: {},
    projects: {},
    roles: {},
    agents: {},
    project_assignments: {},
    agent_runtimes: {},
    cases: {},
    tasks: {},
    deliverables: {},
    review_requests: {},
    outcome_kind_definitions: {},
    review_profiles: {},
    inbox: {},
    blockers: {},
    blocker_events: {},
    dependency_composition_recoveries: {},
    action_records: {},
    release_packages: {},
    dispatches: {},
    worktree_cleanup_candidates: {},
    git_work_contexts: {},
    ephemeral_assignments: {},
    git_promotions: {},
    stand_downs: {},
    runtime_operations: {
      generated_at: null,
      agents: [],
      attention: [],
    },
    resource_tokens: {},
    available_role_permissions: [],
  };
}

function emptyTimelineState() {
  return {
    revision: 0,
    derived: true,
    audit_log: false,
    source: "derived_from_current_team_state",
    description:
      "Activity reconstructed from records retained in the current Team snapshot; this is not an audit log.",
    filters: {},
    timestampless_count: 0,
    total_count: 0,
    limit: 10,
    offset: 0,
    has_more: false,
    facets: {
      types: [],
      statuses: [],
    },
    entries: [],
  };
}

function emptyCostState() {
  return {
    generated_at: null,
    coverage_status: "partial",
    coverage_reasons: ["costs_not_loaded"],
    team_costs: {},
    governance_group_costs: {},
    project_costs: {},
    unattributed_costs: null,
    source_counts: {},
    events_count: 0,
    priced_events_count: 0,
    unpriced_events_count: 0,
    backfill: null,
    error: null,
  };
}

function emptyChatState() {
  return {
    revision: 0,
    selected_conversation_id: null,
    current_operator: null,
    selected_operator: null,
    can_view_all_operators: false,
    read_only: false,
    operator_options: [],
    eligible_agents: [],
    conversations: [],
    loading: false,
    error: null,
  };
}

const decisionStates = ["accepted", "rejected", "blocked"];
const inboxItemLabels = {
  operator_directive: "Operator directive",
  question: "Question",
  status_request: "Status request",
  progress_report: "Progress report",
  deliverable_notice: "Deliverable notice",
  escalation: "Escalation",
};

const mailController = createTeamMailController({
  apiBase: API_BASE,
  documentRoot: document,
  elements,
  getState: () => state,
  isActive: () => activeTeamView === "mail",
  teamFetch,
  responseText,
  text,
  values,
  byId,
  agentLabel,
  renderSelectOptions,
  renderAgentAvatar,
  escapeHtml,
  escapeAttr,
  humanizeToken,
  taskStateLabel,
  inboxItemLabels,
  disclosureAttributes: teamDisclosureAttributes,
  captureDisclosures: rememberOpenTeamDisclosures,
  canMutate: guardTeamMutation,
  mutationLocked: teamMutationLocked,
  lockedMessage: lockedMutationMessage,
  setStatus,
  mutationBody: (payload) =>
    mutationBodyWithConflictMetadata("/operator-directives", "POST", payload),
  applyMutationResult: applyMutationResultPayload,
  reloadActiveView: reloadActiveTeamView,
});

const activityController = createTeamActivityController({
  apiBase: API_BASE,
  documentRoot: document,
  elements,
  getState: () => state,
  isActive: () => activeTeamView === "activity",
  teamFetch,
  responseText,
  text,
  values,
  byId,
  agentLabel,
  renderSelectOptions,
  escapeHtml,
  escapeAttr,
  humanizeToken,
});

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

function bindTopLevelTabs() {
  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-team-view], [data-team-link-view]");
    if (!button) {
      return;
    }
    event.preventDefault();
    setTeamView(button.dataset.teamView || button.dataset.teamLinkView || defaultTeamView);
  });
  updateTeamViewTabs();
}

function bindWorkspaceAttentionPagination() {
  document.addEventListener("click", (event) => {
    const pageButton = event.target?.closest?.("[data-workspace-attention-page]");
    if (!pageButton) {
      return;
    }
    event.preventDefault();
    workspaceAttentionPage = Math.max(1, Number(pageButton.dataset.workspaceAttentionPage || 1));
    renderWorkspaceDashboard();
    document.querySelector(".workspace-attention-panel")?.scrollIntoView({
      block: "start",
      behavior: "auto",
    });
  });
}

function normalizedTeamView(view) {
  return teamViewIds.has(view) ? view : defaultTeamView;
}

function readStoredTeamView() {
  try {
    const storedView = window.localStorage.getItem(teamViewStorageKey);
    const normalizedView = normalizedTeamView(storedView);
    if (storedView && storedView !== normalizedView) {
      window.localStorage.setItem(teamViewStorageKey, normalizedView);
    }
    return normalizedView;
  } catch {
    return defaultTeamView;
  }
}

function readAddonDeepLinkParams() {
  const params = new URLSearchParams(window.location.search);
  const hash = String(window.location.hash || "");
  const hashQueryIndex = hash.indexOf("?");
  if (hashQueryIndex >= 0) {
    const hashParams = new URLSearchParams(hash.slice(hashQueryIndex + 1));
    hashParams.forEach((value, key) => {
      if (!params.has(key)) {
        params.set(key, value);
      }
    });
  }
  return params;
}

function readHumanReviewDeepLink() {
  const params = readAddonDeepLinkParams();
  const enabled = params.get("view") === "my-human-reviews";
  return {
    enabled,
    requestId: enabled
      ? String(params.get("human_review_request_id") || "").trim()
      : "",
  };
}

function readChatDeepLink() {
  const params = readAddonDeepLinkParams();
  const enabled = params.get("view") === "chat";
  return {
    enabled,
    conversationId: enabled
      ? String(params.get("conversation_id") || "").trim()
      : "",
  };
}

function storeTeamView(view) {
  try {
    window.localStorage.setItem(teamViewStorageKey, normalizedTeamView(view));
  } catch {
    // Storage can be unavailable in hardened browser contexts.
  }
}

function bindPageLifecycle() {
  window.addEventListener("beforeunload", () => {
    closeChatStream();
    closeStateStream();
  });
  window.addEventListener("pagehide", () => {
    closeChatStream();
    closeStateStream();
  });
  window.addEventListener("pageshow", () => {
    ensureStateStream();
    syncChatStreamForActiveView({ refresh: true });
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      closeChatStream();
      closeStateStream();
      return;
    }
    ensureStateStream();
    syncChatStreamForActiveView({ refresh: true });
  });
}

function setTeamView(view) {
  const nextView = normalizedTeamView(view);
  if (nextView === "people" && activeTeamView !== "people") {
    clearSelectedTeamRecord();
  }
  const enteringChat = activeTeamView !== "chat" && nextView === "chat";
  const leavingChat = activeTeamView === "chat" && nextView !== "chat";
  activeTeamView = nextView;
  storeTeamView(activeTeamView);
  if (enteringChat && selectedChatConversationId) {
    markChatConversationRead(selectedChatConversationId, { rerender: false });
    scheduleChatScrollToBottom(selectedChatConversationId);
  }
  renderShell();
  if (loadedTeamViews.has(activeTeamView)) {
    renderActiveView(activeTeamView);
  } else {
    renderActiveViewLoading(activeTeamView);
  }
  if (leavingChat) {
    closeChatStream();
  }
  if (!loadedTeamViews.has(activeTeamView)) {
    void loadView(activeTeamView, { force: true });
  } else if (enteringChat) {
    syncChatStreamForActiveView({ refresh: true });
  }
}

function updateTeamViewTabs() {
  const unreadChatCount = unreadChatConversationCount();
  document.querySelectorAll("[data-team-view]").forEach((button) => {
    const active = (button.dataset.teamView || defaultTeamView) === activeTeamView;
    if (!button.dataset.teamViewBaseLabel) {
      button.dataset.teamViewBaseLabel = text(button.textContent);
    }
    const baseLabel = button.dataset.teamViewBaseLabel || text(button.textContent);
    if ((button.dataset.teamView || "") === "chat" && unreadChatCount > 0) {
      button.classList.add("has-unread");
      button.innerHTML = `${escapeHtml(baseLabel)} <span class="team-view-tab-badge">${escapeHtml(unreadChatCount)}</span>`;
      button.setAttribute("aria-label", `${baseLabel}, ${unreadChatCount} unread`);
    } else {
      button.classList.remove("has-unread");
      button.textContent = baseLabel;
      button.removeAttribute("aria-label");
    }
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll("[data-team-view-panel]").forEach((panel) => {
    const active = (panel.dataset.teamViewPanel || defaultTeamView) === activeTeamView;
    panel.hidden = !active;
  });
  syncTeamViewActions();
}

function syncTeamViewActions() {
  elements.teamViewActionButtons.forEach((button) => {
    const actionView = button.dataset.teamViewAction || "";
    button.hidden = actionView !== activeTeamView;
  });
}

function panelForTarget(target) {
  return target?.closest?.("[data-team-view-panel]") || null;
}

function targetIsActive(target) {
  const panel = panelForTarget(target);
  return !panel || !panel.hidden;
}

function renderIntoActiveTargets(selector, html) {
  const drafts = captureFormDrafts();
  rememberOpenTeamDisclosures();
  const targets = Array.from(document.querySelectorAll(selector));
  targets.forEach((target) => {
    if (targetIsActive(target)) {
      target.innerHTML = html;
    } else {
      target.innerHTML = "";
    }
  });
  restoreFormDrafts(drafts);
  return targets.filter(targetIsActive).length;
}

function bindInboxPopoverPositioning() {
  document.addEventListener("pointerover", (event) => {
    scheduleInboxPopoverPosition(event.target);
  });
  document.addEventListener("focusin", (event) => {
    scheduleInboxPopoverPosition(event.target);
  });
  window.addEventListener("resize", () => {
    document.querySelectorAll(".agent-inbox-indicator").forEach((indicator) => {
      scheduleInboxPopoverPosition(indicator);
    });
  });
}

function scheduleInboxPopoverPosition(target) {
  if (!(target instanceof Element)) {
    return;
  }
  const indicator = target.closest(".agent-inbox-indicator");
  const popover = indicator?.querySelector(".agent-inbox-popover");
  if (!indicator || !popover) {
    return;
  }

  indicator.style.setProperty("--agent-inbox-popover-offset-x", "0px");
  popover.style.setProperty("--agent-inbox-popover-offset-x", "0px");
  window.requestAnimationFrame(() => {
    clampInboxPopoverToViewport(indicator, popover);
  });
}

function clampInboxPopoverToViewport(indicator, popover) {
  if (!indicator.isConnected || !popover.isConnected) {
    return;
  }

  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const viewportPadding = 12;
  const rect = popover.getBoundingClientRect();
  let offsetX = 0;
  const maxRight = viewportWidth - viewportPadding;

  if (rect.right > maxRight) {
    offsetX -= rect.right - maxRight;
  }
  if (rect.left + offsetX < viewportPadding) {
    offsetX += viewportPadding - (rect.left + offsetX);
  }

  const offsetValue = `${Math.round(offsetX)}px`;
  indicator.style.setProperty("--agent-inbox-popover-offset-x", offsetValue);
  popover.style.setProperty("--agent-inbox-popover-offset-x", offsetValue);
}

function openTeamsCreateTeamPanel() {
  teamsCreateTeamPanelOpen = true;
  syncTeamsCreateTeamPanel();
  window.requestAnimationFrame(() => {
    elements.teamsCreateTeamForm?.elements.name?.focus();
  });
}

function closeTeamsCreateTeamPanel({ reset = false } = {}) {
  teamsCreateTeamPanelOpen = false;
  if (reset) {
    elements.teamsCreateTeamForm?.reset();
  }
  syncTeamsCreateTeamPanel();
}

function syncTeamsCreateTeamPanel() {
  if (!elements.teamsCreateTeamPanel) {
    return;
  }
  elements.teamsCreateTeamPanel.hidden = !teamsCreateTeamPanelOpen || activeTeamView !== "people";
}

function openGovernanceCreateGroupPanel() {
  governanceCreateGroupPanelOpen = true;
  syncGovernanceCreateGroupPanel();
  window.requestAnimationFrame(() => {
    elements.governanceCreateGroupForm?.elements.name?.focus();
  });
}

function closeGovernanceCreateGroupPanel({ reset = false } = {}) {
  governanceCreateGroupPanelOpen = false;
  if (reset) {
    elements.governanceCreateGroupForm?.reset();
    initializeGovernanceCreateGroupGoals({ reset: true });
  }
  syncGovernanceCreateGroupPanel();
}

function initializeGovernanceCreateGroupGoals({ reset = false } = {}) {
  const form = elements.governanceCreateGroupForm;
  const host = form?.querySelector?.("[data-governance-create-goals]");
  if (!host || (!reset && host.querySelector("[data-governance-goals-editor]"))) {
    return;
  }
  host.innerHTML = renderGovernanceGoalsEditor([]);
  decorateFieldTooltips(host);
  syncGovernanceGoalEditor(host.querySelector("[data-governance-goals-editor]"));
}

function toggleGovernanceCreateGroupPanel() {
  if (governanceCreateGroupPanelOpen) {
    closeGovernanceCreateGroupPanel();
    return;
  }
  openGovernanceCreateGroupPanel();
}

function syncGovernanceCreateGroupPanel() {
  if (!elements.governanceCreateGroupPanel) {
    return;
  }
  const open = governanceCreateGroupPanelOpen && activeTeamView === "governance";
  elements.governanceCreateGroupPanel.hidden = !open;
  elements.governanceCreateGroupToggle?.setAttribute("aria-expanded", open ? "true" : "false");
}

function orgContextPoint(detail) {
  return {
    x: Number(detail?.clientX) || 0,
    y: Number(detail?.clientY) || 0,
  };
}

function openOrgCanvasContextMenu(detail) {
  const point = orgContextPoint(detail);
  openOrgContextMenu({
    kind: "canvas",
    point,
    items: [
      {
        action: "create-team",
        label: "Create team",
        disabled: teamMutationLocked(),
        disabledReason: lockedMutationMessage("create teams"),
      },
    ],
  });
}

function openOrgAgentContextMenu(detail) {
  const agentId = text(detail?.agentId);
  const agent = state.agents?.[agentId];
  const point = orgContextPoint(detail);
  openOrgContextMenu({
    kind: "agent",
    point,
    agentId,
    items: [
      {
        action: "open-session",
        label: "Open session",
        disabled: !text(agent?.session_id),
        disabledReason: "This agent has no linked session.",
      },
      {
        action: "add-subordinate-agent",
        label: "Add subordinate agent",
        disabled: teamMutationLocked() || !agent,
        disabledReason: teamMutationLocked()
          ? lockedMutationMessage("add subordinate agents")
          : "Agent is no longer available.",
      },
    ],
  });
}

function openOrgGovernanceContextMenu(detail) {
  const groupId = text(detail?.groupId);
  const group = state.governance_groups?.[groupId];
  const point = orgContextPoint(detail);
  openOrgContextMenu({
    kind: "governance",
    point,
    groupId,
    items: [
      {
        action: "governance-add-team",
        label: "Add team to oversight",
        disabled: teamMutationLocked() || !group || governanceOversightTeamOptions(group, "add").length === 0,
        disabledReason: teamMutationLocked()
          ? lockedMutationMessage("edit governance oversight")
          : "No visible teams are available to add.",
      },
      {
        action: "governance-remove-team",
        label: "Remove team from oversight",
        disabled: teamMutationLocked() || !group || governanceOversightTeamOptions(group, "remove").length === 0,
        disabledReason: teamMutationLocked()
          ? lockedMutationMessage("edit governance oversight")
          : "No visible member teams are available to remove.",
      },
    ],
  });
}

function ensureOrgContextMenu() {
  let menu = document.querySelector("#org-context-menu");
  if (menu) {
    return menu;
  }
  menu = document.createElement("div");
  menu.id = "org-context-menu";
  menu.className = "org-context-menu";
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", "Org chart actions");
  menu.hidden = true;
  menu.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-org-context-action]");
    if (!button) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (button.disabled) {
      return;
    }
    const action = text(button.dataset.orgContextAction);
    const context = orgContextMenuState ? { ...orgContextMenuState } : {};
    closeOrgContextMenu();
    handleOrgContextAction(action, context);
  });
  document.body.append(menu);
  return menu;
}

function openOrgContextMenu({ kind, point, items, agentId = "", groupId = "" }) {
  const menu = ensureOrgContextMenu();
  orgContextMenuState = { kind, agentId: text(agentId), groupId: text(groupId) };
  menu.innerHTML = items
    .map(
      (item) => `
        <button
          type="button"
          role="menuitem"
          class="org-context-menu-item"
          data-org-context-action="${escapeAttr(item.action)}"
          ${item.disabled ? "disabled" : ""}
          ${item.disabledReason ? `title="${escapeAttr(item.disabledReason)}"` : ""}
        >${escapeHtml(item.label)}</button>
      `,
    )
    .join("");
  menu.hidden = false;
  positionOrgContextMenu(menu, point);
  window.requestAnimationFrame(() => {
    menu.querySelector("button:not(:disabled)")?.focus({ preventScroll: true });
  });
}

function positionOrgContextMenu(menu, point) {
  const viewportPadding = 8;
  menu.style.left = "0px";
  menu.style.top = "0px";
  const rect = menu.getBoundingClientRect();
  const maxLeft = Math.max(viewportPadding, window.innerWidth - rect.width - viewportPadding);
  const maxTop = Math.max(viewportPadding, window.innerHeight - rect.height - viewportPadding);
  const left = Math.min(Math.max(viewportPadding, Number(point?.x) || 0), maxLeft);
  const top = Math.min(Math.max(viewportPadding, Number(point?.y) || 0), maxTop);
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
}

function closeOrgContextMenu() {
  const menu = document.querySelector("#org-context-menu");
  if (menu) {
    menu.hidden = true;
    menu.innerHTML = "";
  }
  orgContextMenuState = null;
}

function handleOrgContextAction(action, context) {
  if (action === "open-session") {
    navigateToAgentSession(state.agents?.[context.agentId]?.session_id);
    return;
  }
  if (action === "create-team") {
    if (!guardTeamMutation("create teams")) {
      return;
    }
    openOrgCreateTeamDialog();
    return;
  }
  if (action === "governance-add-team") {
    if (!guardTeamMutation("edit governance oversight")) {
      return;
    }
    openOrgGovernanceOversightDialog(context.groupId, "add");
    return;
  }
  if (action === "governance-remove-team") {
    if (!guardTeamMutation("edit governance oversight")) {
      return;
    }
    openOrgGovernanceOversightDialog(context.groupId, "remove");
    return;
  }
  if (action === "add-subordinate-agent") {
    if (!guardTeamMutation("add subordinate agents")) {
      return;
    }
    void openOrgSubordinateAgentDialog(context.agentId);
  }
}

function ensureOrgActionDialog() {
  let modal = document.querySelector("#org-action-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "org-action-modal";
    modal.className = "edit-modal org-action-modal";
    modal.hidden = true;
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "org-action-modal-title");
    modal.innerHTML = `
      <div class="edit-modal-backdrop" data-org-action-close></div>
      <section class="edit-modal-panel org-action-modal-panel">
        <div class="edit-modal-header">
          <div>
            <p class="section-label">Org chart</p>
            <h2 id="org-action-modal-title">Org chart action</h2>
          </div>
          <button type="button" class="record-action" data-org-action-close>Close</button>
        </div>
        <div id="org-action-modal-body" class="org-action-modal-body"></div>
      </section>
    `;
    document.body.append(modal);
  }
  return {
    modal,
    title: modal.querySelector("#org-action-modal-title"),
    body: modal.querySelector("#org-action-modal-body"),
  };
}

function showOrgActionDialog({ title, bodyHtml }) {
  const dialog = ensureOrgActionDialog();
  dialog.title.textContent = title;
  dialog.body.innerHTML = bodyHtml;
  fillSelectsIn(dialog.body);
  decorateFieldTooltips(dialog.body);
  applyLockedMutationControls(dialog.modal);
  dialog.modal.hidden = false;
  window.requestAnimationFrame(() => {
    dialog.body.querySelector("input:not([readonly]), select, textarea, button")?.focus();
  });
}

function closeOrgActionDialog() {
  const dialog = ensureOrgActionDialog();
  const form = dialog.body.querySelector("form");
  if (form) {
    clearFormDraft(form);
  }
  dialog.modal.hidden = true;
  dialog.body.innerHTML = "";
}

function openOrgCreateTeamDialog() {
  showOrgActionDialog({
    title: "Create team",
    bodyHtml: `
      <form
        id="org-create-team-form"
        class="org-action-form"
        data-org-action-form
        data-org-action="create-team"
        data-form-id="create-team-from-teams-form"
        data-endpoint="/teams"
        data-method="POST"
      >
        <label>Name <input name="name" required /></label>
        <label>
          Manager existing agent
          <select
            name="manager_existing_agent_id"
            data-options="agents"
            data-placeholder="Select existing agent"
            data-agent-source-picker
            data-session-field="manager_session_id"
            data-display-field="manager_display_name"
            data-suggested-role="manager"
          ></select>
        </label>
        <label>Manager session ID <input name="manager_session_id" required /></label>
        <label>Manager display name <input name="manager_display_name" /></label>
        <label>Manager role name <input name="manager_role_name" value="Manager" /></label>
        <label>Operator communication target id <input name="operator_communication_target_id" /></label>
        <label class="checkbox-label">
          <input name="escalate_to_governance" type="checkbox" /> Escalate team escalations to governance
        </label>
        <label>Estimated max cost USD <input name="max_cost_usd" type="number" min="0" step="0.01" /></label>
        <label>Governance cost warning threshold <input name="cost_warning_ratio" type="number" min="0.01" max="1" step="0.01" value="0.8" /></label>
        <label>Budget renewal <select name="cost_budget_renewal_mode"><option value="manual">Manual reset</option><option value="monthly_utc">Monthly (UTC)</option></select></label>
        <div class="org-action-form-actions">
          <button type="submit">Create team</button>
          <button type="button" class="secondary" data-org-action-close>Cancel</button>
        </div>
      </form>
    `,
  });
}

function openOrgGovernanceOversightDialog(groupId, mode) {
  const group = state.governance_groups?.[text(groupId)];
  if (!group) {
    setStatus("Governance group is no longer available.", "error");
    return;
  }
  const normalizedMode = mode === "remove" ? "remove" : "add";
  const options = governanceOversightTeamOptions(group, normalizedMode);
  const isRemove = normalizedMode === "remove";
  const title = `${isRemove ? "Remove" : "Add"} team oversight`;
  const emptyMessage = isRemove
    ? "This governance group has no visible member teams to remove."
    : "All visible teams are already in this governance group.";
  const bodyHtml = options.length
    ? `
      <form
        id="org-governance-oversight-form"
        class="org-action-form"
        data-org-action-form
        data-org-action="governance-${normalizedMode}-team"
        data-group-id="${escapeAttr(group.id)}"
      >
        <p class="meta-line">${escapeHtml(group.name)} (${escapeHtml(group.id)})</p>
        <label>Team
          <select name="team_id" required>
            ${renderSelectOptions(options, { placeholder: "Select team" })}
          </select>
        </label>
        <div class="org-action-form-actions">
          <button type="submit">${isRemove ? "Remove team" : "Add team"}</button>
          <button type="button" class="secondary" data-org-action-close>Cancel</button>
        </div>
      </form>
    `
    : `
      <div class="org-action-empty">
        <p class="empty-state">${escapeHtml(emptyMessage)}</p>
        <div class="org-action-form-actions">
          <button type="button" data-org-action-close>Close</button>
        </div>
      </div>
    `;
  showOrgActionDialog({ title, bodyHtml });
}

async function openOrgSubordinateAgentDialog(agentId) {
  const superior = state.agents?.[text(agentId)];
  if (!superior) {
    setStatus("Agent is no longer available.", "error");
    return;
  }
  const teamId = text(superior.team_id);
  if (!teamId) {
    setStatus("Teamless agents cannot have subordinate Team agents.", "error");
    return;
  }
  const directoryPath = teamAgentDirectoryPath();
  await loadCoreAgentSessions({ directoryPath, force: false, rerender: false });
  showOrgActionDialog({
    title: "Add subordinate agent",
    bodyHtml: renderOrgSubordinateAgentForm(superior, directoryPath),
  });
}

function renderOrgSubordinateAgentForm(superior, directoryPath) {
  const teamId = text(superior.team_id);
  const selectedProjectId = agentProjectIds(superior)[0] || "";
  const superiorLabel = agentLabel(superior);
  const defaultsOptions = teamAgentOptions(teamId).filter(([agentId]) => text(agentId) !== text(superior.id));
  const createSessionDisabled = teamPrivilegedUnlocked()
    ? ""
    : lockedAttrs("create a core agent session");
  return `
    <form
      id="org-register-agent-form"
      class="org-action-form"
      data-org-action-form
      data-org-action="add-subordinate-agent"
      data-form-id="register-agent-form"
      data-endpoint="/agents"
      data-method="POST"
      data-directory-path="${escapeAttr(directoryPath)}"
    >
      <input type="hidden" name="team_id" value="${escapeAttr(teamId)}" />
      <input type="hidden" name="reports_to_agent_id" value="${escapeAttr(superior.id)}" />
      <label>Reports to <input value="${escapeAttr(superiorLabel)} (${escapeAttr(superior.id)})" readonly /></label>
      <label>
        Existing agent defaults
        <select
          name="existing_agent_id"
          data-agent-source-picker
          data-display-field="display_name"
          data-role-field="role_id"
          data-project-field="project_id"
          data-max-concurrent-field="max_concurrent"
        >${renderSelectOptions(defaultsOptions, { placeholder: "Select existing agent" })}</select>
      </label>
      <label>Project
        <select name="project_id">${renderSelectOptions(teamProjectOptions(teamId), { selectedValue: selectedProjectId, placeholder: "No project" })}</select>
      </label>
      <label>Role
        <select name="role_id" required>${renderSelectOptions(teamRoleOptions(teamId), { placeholder: "Select role" })}</select>
      </label>
      <label>Existing session
        <select
          name="existing_session_id"
          data-core-agent-session-picker
          data-session-field="session_id"
        >${renderCoreAgentSessionOptions()}</select>
      </label>
      <div class="org-agent-session-actions">
        <button type="button" class="record-action" data-core-agent-session-refresh>Refresh sessions</button>
        <button type="button" class="record-action" data-core-agent-session-create ${createSessionDisabled}>Create new session</button>
      </div>
      <label>Session ID <input name="session_id" required /></label>
      <label>Display name <input name="display_name" /></label>
      <label>Max ephemeral agents <input name="max_concurrent" type="number" min="0" value="0" /></label>
      <div class="org-action-form-actions">
        <button type="submit">Register agent</button>
        <button type="button" class="secondary" data-org-action-close>Cancel</button>
      </div>
    </form>
  `;
}

function governanceOversightTeamOptions(group, mode) {
  const memberTeamIds = new Set(uniqueTextList(group?.member_team_ids || []));
  const includeMember = mode === "remove";
  return values(state.teams)
    .filter(isVisibleTeam)
    .filter((team) => memberTeamIds.has(text(team.id)) === includeMember)
    .sort(comparePeopleTeams)
    .map((team) => [team.id, `${team.name || team.id} (${team.id})`]);
}

async function submitOrgActionForm(form) {
  if (!guardTeamMutation("save changes")) {
    return false;
  }
  const action = text(form.dataset.orgAction);
  if (action === "create-team" || action === "add-subordinate-agent") {
    return await submitOrgCreateForm(form);
  }
  if (action === "governance-add-team" || action === "governance-remove-team") {
    return await submitOrgGovernanceOversightForm(form, action === "governance-remove-team");
  }
  return false;
}

async function submitOrgCreateForm(form) {
  const data = formData(form);
  const endpoint = endpointForForm(form, data);
  const method = form.dataset.method || "POST";
  if (!endpoint) {
    return false;
  }
  const saved = await submitMutation(form, endpoint, method, payloadForForm(form, data));
  if (saved) {
    closeOrgActionDialog();
  }
  return saved;
}

async function submitOrgGovernanceOversightForm(form, removeTeam) {
  const groupId = text(form.dataset.groupId);
  const teamId = text(formData(form).team_id);
  const group = state.governance_groups?.[groupId];
  if (!group) {
    setStatus("Governance group is no longer available.", "error");
    return false;
  }
  if (!teamId) {
    setStatus("Team is required.", "error");
    return false;
  }
  const currentTeamIds = uniqueTextList(group.member_team_ids || []);
  const nextTeamIds = removeTeam
    ? currentTeamIds.filter((memberTeamId) => memberTeamId !== teamId)
    : uniqueTextList([...currentTeamIds, teamId]);
  const saved = await submitMutation(
    form,
    `/governance-groups/${encodeURIComponent(groupId)}`,
    "PATCH",
    { member_team_ids: nextTeamIds },
  );
  if (saved) {
    closeOrgActionDialog();
  }
  return saved;
}

function bindForms() {
  document.addEventListener("input", rememberFormDraftFromEvent);
  document.addEventListener("change", rememberFormDraftFromEvent);

  document.querySelectorAll("form[data-endpoint], form[data-template-endpoint]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitCreateForm(form);
    });
  });

  const updateTaskForm = document.querySelector("#update-task-form");
  updateTaskForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitTaskUpdate(updateTaskForm);
  });
  updateTaskForm?.elements.task_id?.addEventListener("change", () => {
    syncTaskUpdateForm(updateTaskForm);
  });

  const decideReviewForm = document.querySelector("#decide-review-form");
  decideReviewForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitReviewDecision(decideReviewForm);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target?.closest?.("[data-chat-message-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    void submitChatMessage(form);
  });

  document.addEventListener("keydown", (event) => {
    const chatComposer = event.target?.closest?.(
      '[data-chat-message-form] textarea[name="body"]',
    );
    if (
      !(chatComposer instanceof HTMLTextAreaElement) ||
      event.key !== "Enter" ||
      event.shiftKey ||
      event.isComposing
    ) {
      return;
    }
    const form = chatComposer.closest("form[data-chat-message-form]");
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    event.preventDefault();
    form.requestSubmit();
  });

  elements.chatAttachmentPolicyForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitChatAttachmentPolicy(elements.chatAttachmentPolicyForm);
  });

  document.addEventListener("click", (event) => {
    const openButton = event.target?.closest?.("[data-chat-attachment-policy-open]");
    if (openButton) {
      event.preventDefault();
      void openChatAttachmentPolicy();
      return;
    }
    if (event.target?.closest?.("[data-chat-attachment-policy-close]")) {
      event.preventDefault();
      closeChatAttachmentPolicy();
    }
  });

  document.addEventListener("submit", (event) => {
    const form = event.target?.closest?.("form[data-team-inline-create-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    void submitCreateForm(form);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target?.closest?.("form[data-delegated-create-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    void submitCreateForm(form);
  });

  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-git-import-button]");
    if (!button) {
      return;
    }
    event.preventDefault();
    if (!guardTeamMutation("import Git repositories")) {
      return;
    }
    const form = button.closest("form");
    if (!form) {
      setStatus("Git import form is unavailable.", "error");
      return;
    }
    void importGitRepoForForm(form, button);
  });

  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-release-gate-retry]");
    if (!button) {
      return;
    }
    event.preventDefault();
    if (!guardTeamMutation("retry release gates")) {
      return;
    }
    const rejectedReviewException = button.dataset.rejectedReviewException === "true";
    const rejectedVerificationRetry = button.dataset.rejectedVerificationRetry === "true";
    const reason = window.prompt(
      rejectedVerificationRetry
        ? "Add a comment for retrying verification on this candidate. Its unused generated correction task will be cancelled."
        : rejectedReviewException
          ? "Why is an operator exception justified for this rejected review on the unchanged candidate?"
          : "What concrete recovery occurred since this gate failed? Cancel and escalate if none.",
      "",
    );
    if (!text(reason)) {
      setStatus("A retry reason is required.", "error");
      return;
    }
    void retryReleaseGate(button, text(reason));
  });

  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-worktree-cleanup-retry]");
    if (!button) {
      return;
    }
    event.preventDefault();
    if (!guardTeamMutation("retry worktree cleanup")) {
      return;
    }
    const reason = window.prompt(
      "What changed since cleanup was halted? The retry will run every safety check again.",
      "",
    );
    if (!text(reason)) {
      setStatus("A cleanup retry reason is required.", "error");
      return;
    }
    void retryWorktreeCleanup(button, text(reason));
  });

  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.(
      "[data-repair-cancelled-task-dependencies]",
    );
    if (!button) {
      return;
    }
    event.preventDefault();
    if (!guardTeamMutation("repair cancelled-task dependencies")) {
      return;
    }
    const reason = window.prompt(
      "Why is this graph repair authorized? The original cancellation will remain unchanged.",
      "",
    );
    if (!text(reason)) {
      setStatus("A dependency repair reason is required.", "error");
      return;
    }
    void repairCancelledTaskDependencies(button, text(reason));
  });

  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-team-cost-budget-reset]");
    if (!button) {
      return;
    }
    event.preventDefault();
    if (!guardTeamMutation("reset a team cost budget")) {
      return;
    }
    const reason = window.prompt("Why is a new budget window authorized?", "");
    if (!text(reason)) {
      setStatus("A budget reset reason is required.", "error");
      return;
    }
    void resetTeamCostBudget(button, text(button.dataset.teamCostBudgetReset), text(reason));
  });

  document.addEventListener("click", (event) => {
    const governanceGoalAdd = event.target?.closest?.("[data-governance-goal-add]");
    if (governanceGoalAdd) {
      event.preventDefault();
      if (!guardTeamMutation("edit governance goals")) {
        return;
      }
      addGovernanceGoalRow(
        governanceGoalAdd.closest("[data-governance-goals-editor]"),
      );
      return;
    }

    const governanceGoalRemove = event.target?.closest?.("[data-governance-goal-remove]");
    if (governanceGoalRemove) {
      event.preventDefault();
      if (!guardTeamMutation("edit governance goals")) {
        return;
      }
      removeGovernanceGoalRow(governanceGoalRemove);
      return;
    }

    const governanceGoalMove = event.target?.closest?.("[data-governance-goal-move]");
    if (governanceGoalMove) {
      event.preventDefault();
      if (!guardTeamMutation("reorder governance goals")) {
        return;
      }
      moveGovernanceGoalRow(governanceGoalMove);
      return;
    }

    const governanceMemberRemove = event.target?.closest?.("[data-governance-member-team-remove]");
    if (governanceMemberRemove) {
      event.preventDefault();
      if (!guardTeamMutation("change governance member teams")) {
        return;
      }
      removeGovernanceMemberTeam(governanceMemberRemove);
      return;
    }

    const checklistAdd = event.target?.closest?.("[data-human-review-checklist-add]");
    if (checklistAdd) {
      event.preventDefault();
      if (!guardTeamMutation("edit human review checklist")) {
        return;
      }
      addHumanReviewChecklistRow(
        checklistAdd.closest("[data-human-review-checklist-editor]"),
      );
      return;
    }

    const checklistRemove = event.target?.closest?.("[data-human-review-checklist-remove]");
    if (checklistRemove) {
      event.preventDefault();
      if (!guardTeamMutation("edit human review checklist")) {
        return;
      }
      removeHumanReviewChecklistRow(checklistRemove);
      return;
    }

    const checklistMove = event.target?.closest?.("[data-human-review-checklist-move]");
    if (checklistMove) {
      event.preventDefault();
      if (!guardTeamMutation("reorder human review checklist")) {
        return;
      }
      moveHumanReviewChecklistRow(checklistMove);
      return;
    }

    const humanReviewApprove = event.target?.closest?.(
      '[data-human-review-decision="approved"]',
    );
    if (humanReviewApprove) {
      humanReviewApprove
        .closest("[data-human-review-decision-form]")
        ?.querySelectorAll?.(
          '[name="human_finding_text"], [name="human_finding_target_release_candidate"]',
        )
        .forEach((field) => field.setCustomValidity(""));
    }

    const findingAdd = event.target?.closest?.("[data-human-review-finding-add]");
    if (findingAdd) {
      event.preventDefault();
      if (!guardTeamMutation("edit human review findings")) {
        return;
      }
      addHumanReviewFindingRow(
        findingAdd.closest("[data-human-review-findings-editor]"),
      );
      return;
    }

    const findingRemove = event.target?.closest?.("[data-human-review-finding-remove]");
    if (findingRemove) {
      event.preventDefault();
      if (!guardTeamMutation("edit human review findings")) {
        return;
      }
      removeHumanReviewFindingRow(findingRemove);
      return;
    }

    const addButton = event.target?.closest?.("[data-review-profile-criterion-add]");
    if (addButton) {
      event.preventDefault();
      if (!guardTeamMutation("edit review profile criteria")) {
        return;
      }
      addReviewProfileCriterionRow(addButton.closest("[data-review-profile-criteria-editor]"));
      return;
    }

    const removeButton = event.target?.closest?.("[data-review-profile-criterion-remove]");
    if (!removeButton) {
      return;
    }
    event.preventDefault();
    if (!guardTeamMutation("edit review profile criteria")) {
      return;
    }
    removeReviewProfileCriterionRow(removeButton);
  });

  document.addEventListener("change", (event) => {
    const governanceMember = event.target?.closest?.("[data-governance-member-team-id]");
    if (governanceMember instanceof HTMLInputElement) {
      syncGovernanceMemberTeamEditor(
        governanceMember.closest("[data-governance-member-teams-editor]"),
      );
      rememberFormDraft(governanceMember.form);
      return;
    }

    const soundToggle = event.target?.closest?.("[data-chat-notification-sound]");
    if (soundToggle instanceof HTMLInputElement) {
      chatNotificationSoundMuted = !soundToggle.checked;
      storeChatNotificationSoundMuted();
      return;
    }

    const attachmentInput = event.target?.closest?.("[data-chat-attachment-input]");
    if (attachmentInput instanceof HTMLInputElement) {
      const conversationId = text(attachmentInput.dataset.chatAttachmentInput);
      pendingChatAttachments.set(conversationId, [...(attachmentInput.files || [])]);
      renderChat();
      return;
    }

    const select = event.target?.closest?.("[data-chat-operator-select]");
    if (!select) {
      return;
    }
    rememberCurrentChatComposerDraft();
    selectedChatOperatorUserId = text(select.value) || null;
    selectedChatConversationId = null;
    chatNewPickerOpen = false;
    chatMobileConversationListOpen = true;
    closeChatStream();
    void loadConversations({ preserveSelection: false });
  });

  document.addEventListener("input", (event) => {
    const chatComposer = event.target?.closest?.(
      '[data-chat-message-form] textarea[name="body"]',
    );
    if (chatComposer instanceof HTMLTextAreaElement) {
      rememberCurrentChatComposerDraft();
      return;
    }
    const governanceGoalField = event.target?.closest?.(
      '[name="governance_goal_name"], [name="governance_goal_description"], [name="governance_goal_state"]',
    );
    if (
      governanceGoalField instanceof HTMLInputElement ||
      governanceGoalField instanceof HTMLTextAreaElement ||
      governanceGoalField instanceof HTMLSelectElement
    ) {
      governanceGoalField.form
        ?.querySelectorAll?.(
          '[name="governance_goal_name"], [name="governance_goal_description"], [name="governance_goal_state"]',
        )
        .forEach((field) => field.setCustomValidity(""));
    }
    const checklistField = event.target?.closest?.(
      '[name="human_review_checklist_id"], [name="human_review_checklist_label"], [name="human_review_checklist_description"]',
    );
    if (
      checklistField instanceof HTMLInputElement ||
      checklistField instanceof HTMLTextAreaElement
    ) {
      const editor = checklistField.closest("[data-human-review-checklist-editor]");
      editor
        ?.querySelectorAll?.(
          '[name="human_review_checklist_id"], [name="human_review_checklist_label"]',
        )
        .forEach((field) => field.setCustomValidity(""));
      syncHumanReviewChecklistEditor(editor);
    }
    const humanReviewFindingField = event.target?.closest?.(
      '[name="human_finding_text"], [name="human_finding_target_release_candidate"], [name="human_finding_target_deliverable_ids"], [name="human_finding_evidence_refs"]',
    );
    if (
      humanReviewFindingField instanceof HTMLTextAreaElement
      || humanReviewFindingField instanceof HTMLInputElement
    ) {
      const findingRow = humanReviewFindingField.closest(
        "[data-human-review-finding-row]",
      );
      const releaseTarget = findingRow?.querySelector?.(
        '[name="human_finding_target_release_candidate"]',
      );
      if (
        humanReviewFindingField.matches(
          '[name="human_finding_target_release_candidate"]',
        )
        && humanReviewFindingField.checked
      ) {
        findingRow
          ?.querySelectorAll?.(
            '[name="human_finding_target_deliverable_ids"]',
          )
          .forEach((field) => {
            field.checked = false;
          });
      } else if (
        humanReviewFindingField.matches(
          '[name="human_finding_target_deliverable_ids"]',
        )
        && humanReviewFindingField.checked
        && releaseTarget
      ) {
        releaseTarget.checked = false;
      }
      releaseTarget?.setCustomValidity("");
      findingRow
        ?.querySelector?.('[name="human_finding_text"]')
        ?.setCustomValidity("");
    }
    const governanceTeamSearch = event.target?.closest?.("[data-governance-team-search]");
    if (!(governanceTeamSearch instanceof HTMLInputElement)) {
      return;
    }
    filterGovernanceMemberTeams(
      governanceTeamSearch.closest("[data-governance-member-teams-editor]"),
      governanceTeamSearch.value,
    );
  });

  document.addEventListener("click", (event) => {
    const removeAttachment = event.target?.closest?.("[data-chat-attachment-remove]");
    if (!removeAttachment) {
      return;
    }
    event.preventDefault();
    const conversationId = text(removeAttachment.dataset.chatAttachmentConversationId);
    const index = Number(removeAttachment.dataset.chatAttachmentRemove);
    const files = [...(pendingChatAttachments.get(conversationId) || [])];
    if (Number.isInteger(index)) {
      files.splice(index, 1);
    }
    pendingChatAttachments.set(conversationId, files);
    renderChat();
  });
}

function bindRecordEditing() {
  document.addEventListener("pointerdown", (event) => {
    if (
      event.target?.closest?.(
        ".chat-conversation-row[data-chat-conversation-id]",
      )
    ) {
      rememberCurrentChatComposerDraft();
    }
  });
  document.addEventListener("click", (event) => {
    const openCreateTeamButton = event.target.closest("[data-teams-create-team-open]");
    if (openCreateTeamButton) {
      event.preventDefault();
      if (!guardTeamMutation("create teams")) {
        return;
      }
      openTeamsCreateTeamPanel();
      return;
    }

    const closeCreateTeamButton = event.target.closest("[data-teams-create-team-close]");
    if (closeCreateTeamButton) {
      event.preventDefault();
      closeTeamsCreateTeamPanel();
      return;
    }

    const governanceCreateGroupButton = event.target.closest("[data-governance-create-toggle]");
    if (governanceCreateGroupButton) {
      event.preventDefault();
      if (!guardTeamMutation("create governance groups")) {
        return;
      }
      toggleGovernanceCreateGroupPanel();
      return;
    }

    const teamGroupToggle = event.target.closest("[data-team-record-toggle]");
    if (teamGroupToggle) {
      event.preventDefault();
      const teamRecordId = text(teamGroupToggle.dataset.teamRecordToggle);
      if (teamRecordId === noTeamRecordId) {
        toggleTeamRecordGroup(teamRecordId);
        clearSelectedTeamRecord();
        renderAgents();
        return;
      }
      selectTeamRecord("team", teamRecordId);
      toggleTeamRecordGroup(teamRecordId);
      return;
    }

    const teamRecordTab = event.target.closest("[data-team-record-tab]");
    if (teamRecordTab) {
      event.preventDefault();
      setTeamRecordGroupTab(teamRecordTab.dataset.teamRecordGroupId, teamRecordTab.dataset.teamRecordTab);
      return;
    }

    const teamRecordPage = event.target.closest("[data-team-record-page]");
    if (teamRecordPage) {
      event.preventDefault();
      setTeamRecordGroupPage(
        teamRecordPage.dataset.teamRecordGroupId,
        teamRecordPage.dataset.teamRecordKind,
        Number(teamRecordPage.dataset.teamRecordPage),
      );
      return;
    }

    const inlineCreateToggle = event.target.closest("[data-team-inline-create-toggle]");
    if (inlineCreateToggle) {
      event.preventDefault();
      if (!guardTeamMutation("add Team records")) {
        return;
      }
      toggleTeamInlineCreateForm(
        inlineCreateToggle.dataset.teamRecordGroupId,
        inlineCreateToggle.dataset.teamRecordKind,
      );
      return;
    }

    const inlineCreateClose = event.target.closest("[data-team-inline-create-close]");
    if (inlineCreateClose) {
      event.preventDefault();
      closeTeamInlineCreateForm(
        inlineCreateClose.dataset.teamRecordGroupId,
        inlineCreateClose.dataset.teamRecordKind,
      );
      return;
    }

    const selectAgentButton = event.target.closest("[data-select-agent-id]");
    if (selectAgentButton) {
      event.preventDefault();
      selectPeopleAgent(selectAgentButton.dataset.selectAgentId);
      return;
    }

    const openSessionButton = event.target.closest("[data-open-session-id]");
    if (openSessionButton) {
      event.preventDefault();
      navigateToAgentSession(openSessionButton.dataset.openSessionId);
      return;
    }

    const selectProjectButton = event.target.closest("[data-select-project-id]");
    if (selectProjectButton) {
      event.preventDefault();
      selectTeamRecord("project", selectProjectButton.dataset.selectProjectId);
      return;
    }

    const selectRoleButton = event.target.closest("[data-select-role-id]");
    if (selectRoleButton) {
      event.preventDefault();
      selectTeamRecord("role", selectRoleButton.dataset.selectRoleId);
      return;
    }

    const agentCallButton = event.target.closest("[data-agent-call-agent-id]");
    if (agentCallButton) {
      event.preventDefault();
      if (!guardTeamMutation("start voice calls")) {
        return;
      }
      void startAgentVoiceCall(agentCallButton, agentCallButton.dataset.agentCallAgentId);
      return;
    }

    const chatNewButton = event.target.closest("[data-chat-new]");
    if (chatNewButton) {
      event.preventDefault();
      if (chatState.read_only) {
        setStatus("This chat history is read-only.", "error");
        return;
      }
      chatNewPickerOpen = !chatNewPickerOpen;
      renderChat();
      return;
    }

    const chatMobileConversationsButton = event.target.closest("[data-chat-mobile-conversations]");
    if (chatMobileConversationsButton) {
      event.preventDefault();
      rememberCurrentChatComposerDraft();
      chatMobileConversationListOpen = true;
      renderChat();
      return;
    }

    const chatAgentButton = event.target.closest("[data-chat-agent-id]");
    if (chatAgentButton) {
      event.preventDefault();
      void createOrFocusChat(chatAgentButton.dataset.chatAgentId);
      return;
    }

    const chatConversationButton = event.target.closest(".chat-conversation-row[data-chat-conversation-id]");
    if (chatConversationButton) {
      event.preventDefault();
      rememberCurrentChatComposerDraft();
      selectedChatConversationId = chatConversationButton.dataset.chatConversationId || null;
      chatMobileConversationListOpen = false;
      markChatConversationRead(selectedChatConversationId, { rerender: false });
      renderChat();
      if (selectedChatConversationId) {
        ensureChatStream(selectedChatConversationId);
        void loadConversationDetail(selectedChatConversationId);
      }
      return;
    }

    const chatRefreshButton = event.target.closest("[data-chat-refresh]");
    if (chatRefreshButton) {
      event.preventDefault();
      void loadConversations({ preserveSelection: true });
      return;
    }

    const costBackfillButton = event.target.closest("[data-cost-backfill-refresh]");
    if (costBackfillButton) {
      event.preventDefault();
      if (!guardTeamMutation("refresh transcript costs")) {
        return;
      }
      void refreshCostBackfill();
      return;
    }

    const agentCallMuteButton = event.target.closest("[data-agent-call-mute]");
    if (agentCallMuteButton) {
      event.preventDefault();
      if (!guardTeamMutation("change voice calls")) {
        return;
      }
      toggleAgentVoiceMute(agentCallMuteButton);
      return;
    }

    const agentCallEndButton = event.target.closest("[data-agent-call-end]");
    if (agentCallEndButton) {
      event.preventDefault();
      if (!guardTeamMutation("end voice calls")) {
        return;
      }
      void endAgentVoiceCall(agentCallEndButton);
      return;
    }

    const contextRotationButton = event.target.closest("[data-context-rotation-agent-id]");
    if (contextRotationButton) {
      event.preventDefault();
      if (!guardTeamMutation("clear agent context")) {
        return;
      }
      void requestContextRotation(
        contextRotationButton,
        contextRotationButton.dataset.contextRotationAgentId,
      );
      return;
    }

    const standDownButton = event.target.closest("[data-stand-down-agent-id]");
    if (standDownButton) {
      event.preventDefault();
      if (!guardTeamMutation("stand down agents")) {
        return;
      }
      void requestAgentStandDown(
        standDownButton,
        standDownButton.dataset.standDownAgentId,
        standDownButton.dataset.standDownScope,
      );
      return;
    }

    const standDownRevokeButton = event.target.closest("[data-stand-down-revoke-agent-id]");
    if (standDownRevokeButton) {
      event.preventDefault();
      if (
        standDownRevokeButton.matches(":disabled")
        || standDownRevokeButton.getAttribute("aria-disabled") === "true"
      ) {
        return;
      }
      if (!guardTeamMutation("revoke stand-downs")) {
        return;
      }
      void revokeAgentStandDown(
        standDownRevokeButton,
        standDownRevokeButton.dataset.standDownRevokeAgentId,
      );
      return;
    }

    const dismissActionButton = event.target.closest("[data-dismiss-action-id]");
    if (dismissActionButton) {
      event.preventDefault();
      if (!guardTeamMutation("dismiss failed actions")) {
        return;
      }
      void dismissActionRecordAttention(
        dismissActionButton,
        dismissActionButton.dataset.dismissActionId,
      );
      return;
    }

    const resumeReleaseRecoveryButton = event.target.closest(
      "[data-resume-release-recovery-blocker-id]",
    );
    if (resumeReleaseRecoveryButton) {
      event.preventDefault();
      if (!guardTeamMutation("resume release recoveries")) {
        return;
      }
      void resumeReleaseRecovery(
        resumeReleaseRecoveryButton,
        resumeReleaseRecoveryButton.dataset.resumeReleaseRecoveryBlockerId,
        resumeReleaseRecoveryButton.dataset.resumeReleaseRecoveryTaskId,
      );
      return;
    }

    const candidateCorrectionRecoveryTaskButton = event.target.closest(
      "[data-candidate-correction-recovery-task-id]",
    );
    if (candidateCorrectionRecoveryTaskButton) {
      event.preventDefault();
      openCandidateCorrectionRecoveryTask(
        candidateCorrectionRecoveryTaskButton.dataset.candidateCorrectionRecoveryTaskId,
      );
      return;
    }

    const candidateCorrectionRecoveryButton = event.target.closest(
      "[data-candidate-correction-recovery-id]",
    );
    if (candidateCorrectionRecoveryButton) {
      event.preventDefault();
      const action = text(
        candidateCorrectionRecoveryButton.dataset.candidateCorrectionRecoveryAction,
      );
      let reason = null;
      let instruction = null;
      if (action === "create_verified_replacement") {
        instruction = window.prompt(
          "Add instructions for the verified replacement task (optional). These become part of its durable task scope.",
          "",
        );
        if (instruction === null) {
          return;
        }
      }
      void recoverCandidateCorrectionSuccessor(
        candidateCorrectionRecoveryButton,
        candidateCorrectionRecoveryButton.dataset.candidateCorrectionRecoveryId,
        action,
        text(reason) || null,
        text(instruction) || null,
      );
      return;
    }

    const dependencyCompositionTaskButton = event.target.closest(
      "[data-dependency-composition-recovery-task-id]",
    );
    if (dependencyCompositionTaskButton) {
      event.preventDefault();
      openCandidateCorrectionRecoveryTask(
        dependencyCompositionTaskButton.dataset.dependencyCompositionRecoveryTaskId,
      );
      return;
    }

    const dependencyCompositionStartButton = event.target.closest(
      "[data-start-dependency-composition-recovery-id]",
    );
    if (dependencyCompositionStartButton) {
      event.preventDefault();
      const container = dependencyCompositionStartButton.closest(
        ".dependency-composition-recovery-status",
      );
      const resolverAgentId = text(
        container?.querySelector("[data-dependency-composition-resolver]")?.value,
      );
      if (!resolverAgentId) {
        setStatus("Select an active project agent to resolve the merge.", "error");
        return;
      }
      void startDependencyCompositionRecovery(
        dependencyCompositionStartButton,
        dependencyCompositionStartButton.dataset.startDependencyCompositionRecoveryId,
        resolverAgentId,
      );
      return;
    }

    const resolveBlockerButton = event.target.closest("[data-resolve-terminal-blocker-id]");
    if (resolveBlockerButton) {
      event.preventDefault();
      if (!guardTeamMutation("resolve terminal blockers")) {
        return;
      }
      const note = window.prompt(
        "Why is this blocker resolved? Include the evidence or external change that cleared it.",
        "The blocking condition has been verified as resolved.",
      );
      if (!text(note)) {
        setStatus("A blocker resolution explanation is required.", "error");
        return;
      }
      void resolveTerminalBlocker(
        resolveBlockerButton,
        resolveBlockerButton.dataset.resolveTerminalBlockerId,
        text(note),
      );
      return;
    }

    const governanceAgendaHandleButton = event.target.closest("[data-governance-agenda-handle-id]");
    if (governanceAgendaHandleButton) {
      event.preventDefault();
      if (!guardTeamMutation("mark governance agenda handled")) {
        return;
      }
      void markGovernanceAgendaHandled(
        governanceAgendaHandleButton,
        governanceAgendaHandleButton.dataset.governanceAgendaHandleId,
      );
      return;
    }

    const transferProjectLeadButton = event.target.closest("[data-transfer-project-lead]");
    if (transferProjectLeadButton) {
      event.preventDefault();
      void transferProjectLead(transferProjectLeadButton.form);
      return;
    }

    const avatarGenerateButton = event.target.closest("[data-avatar-generate-agent-id]");
    if (avatarGenerateButton) {
      event.preventDefault();
      if (!guardTeamMutation("generate avatar previews")) {
        return;
      }
      void generateAvatarPreview(avatarGenerateButton);
      return;
    }

    const avatarAcceptButton = event.target.closest("[data-avatar-accept-agent-id]");
    if (avatarAcceptButton) {
      event.preventDefault();
      if (!guardTeamMutation("accept avatar previews")) {
        return;
      }
      void acceptAvatarPreview(avatarAcceptButton);
      return;
    }

    const avatarUploadButton = event.target.closest("[data-avatar-upload-agent-id]");
    if (avatarUploadButton) {
      event.preventDefault();
      if (!guardTeamMutation("upload avatars")) {
        return;
      }
      void uploadAgentAvatar(avatarUploadButton);
      return;
    }

    const avatarRemoveButton = event.target.closest("[data-avatar-remove-agent-id]");
    if (avatarRemoveButton) {
      event.preventDefault();
      if (!guardTeamMutation("remove avatars")) {
        return;
      }
      void removeAgentAvatar(avatarRemoveButton);
      return;
    }

    const taskCancellationClose = event.target.closest("[data-task-cancellation-close]");
    if (taskCancellationClose) {
      if (
        taskCancellationClose.classList.contains("edit-modal-backdrop") &&
        event.target !== taskCancellationClose
      ) {
        return;
      }
      closeTaskCancellationDialog();
      return;
    }

    if (elements.taskCancellationModal?.contains(event.target)) {
      return;
    }

    const taskCancellationControl = event.target.closest("[data-task-cancel-id]");
    if (taskCancellationControl) {
      event.preventDefault();
      if (
        taskCancellationControl.matches(":disabled") ||
        taskCancellationControl.getAttribute("aria-disabled") === "true"
      ) {
        return;
      }
      openTaskCancellationDialog(taskCancellationControl.dataset.taskCancelId);
      return;
    }

    const closeTarget = event.target.closest("[data-edit-close]");
    if (closeTarget) {
      if (closeTarget.classList.contains("edit-modal-backdrop") && event.target !== closeTarget) {
        return;
      }
      closeEditDialog();
      return;
    }

    if (elements.editModal?.contains(event.target)) {
      return;
    }

    const editButton = event.target.closest("[data-edit-type][data-edit-id]");
    if (!editButton) {
      return;
    }
    if (!guardTeamMutation("edit Team records")) {
      return;
    }
    openEditDialog(editButton.dataset.editType, editButton.dataset.editId);
  });

  document.addEventListener("keydown", (event) => {
    if (
      event.key === "Escape" &&
      elements.taskCancellationModal &&
      !elements.taskCancellationModal.hidden
    ) {
      closeTaskCancellationDialog();
      return;
    }
    if (event.key === "Escape" && elements.editModal && !elements.editModal.hidden) {
      closeEditDialog();
      return;
    }
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    if (elements.editModal?.contains(event.target)) {
      return;
    }
    const editTarget = event.target.closest("[data-edit-type][data-edit-id]");
    if (!editTarget || editTarget.tagName === "BUTTON") {
      return;
    }
    event.preventDefault();
    if (!guardTeamMutation("edit Team records")) {
      return;
    }
    openEditDialog(editTarget.dataset.editType, editTarget.dataset.editId);
  });

  elements.editForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitEditForm(elements.editForm);
  });
  elements.taskCancellationForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitTaskCancellation(elements.taskCancellationForm);
  });
}

function bindExistingAgentPickers() {
  document.addEventListener("change", (event) => {
    const select = event.target?.closest?.("[data-agent-source-picker]");
    if (!select) {
      return;
    }
    applyExistingAgentDefaults(select);
  });
}

function bindTeamAgentSessionControls() {
  document.addEventListener("change", (event) => {
    const select = event.target?.closest?.("[data-core-agent-session-picker]");
    if (!select) {
      return;
    }
    applyCoreAgentSessionSelection(select);
  });

  document.addEventListener("click", (event) => {
    const refreshButton = event.target?.closest?.("[data-core-agent-session-refresh]");
    if (refreshButton) {
      event.preventDefault();
      const form = refreshButton.closest("form");
      void refreshCoreAgentSessionsForForm(form, { force: true });
      return;
    }

    const createButton = event.target?.closest?.("[data-core-agent-session-create]");
    if (!createButton) {
      return;
    }
    event.preventDefault();
    if (!teamPrivilegedUnlocked()) {
      setStatus("Unlock privileged operations to create a core agent session.", "warning");
      return;
    }
    const form = createButton.closest("form");
    if (!form) {
      setStatus("Agent registration form is unavailable.", "error");
      return;
    }
    openCoreAgentCreateDialogForForm(form);
  });

  window.addEventListener("team-agent-create:created", (event) => {
    void completeTeamAgentCreateRegistration(event.detail || {});
  });
}

function applyExistingAgentDefaults(select) {
  const sourceAgent = state.agents[text(select.value)];
  const form = select.form;
  if (!sourceAgent || !form) {
    return;
  }

  setFieldValue(form, select.dataset.sessionField, sourceAgent.session_id);
  setFieldValue(form, select.dataset.displayField, sourceAgent.display_name || agentLabel(sourceAgent));
  setFieldValue(form, select.dataset.roleField, sourceAgent.role_id);
  setFieldValue(form, select.dataset.projectField, agentProjectIds(sourceAgent)[0] || "");
  setFieldValue(form, select.dataset.reportsToField, sourceAgent.reports_to_agent_id || "");
  if (select.dataset.maxConcurrentField) {
    setFieldValue(form, select.dataset.maxConcurrentField, String(ephemeralCapacity(sourceAgent)));
  }

}

function applyCoreAgentSessionSelection(select) {
  const form = select.form;
  if (!form) {
    return;
  }
  const sessionId = text(select.value);
  setFieldValue(form, select.dataset.sessionField || "session_id", sessionId);
  if (!sessionId) {
    return;
  }
  const session = coreAgentSessions.find((item) => item.session_id === sessionId);
  if (session && !fieldText(form, "display_name")) {
    setFieldValue(form, "display_name", session.display_name || session.target_name || session.session_id);
  }
}

function validateTeamAgentSessionCreateRegistration(form) {
  if (formSemanticIdentifier(form) !== "register-agent-form") {
    return true;
  }
  const data = formData(form);
  const teamId = normalizedAgentRegistrationTeamId(data.team_id);
  if (!teamId) {
    return true;
  }
  if (!state.teams?.[teamId]) {
    return rejectCoreAgentSessionCreate(form, "team_id", "Select a valid team before creating a core agent session.");
  }
  const roleId = text(data.role_id);
  if (!roleId) {
    return rejectCoreAgentSessionCreate(form, "role_id", "Select a role before creating a core agent session.");
  }
  const role = state.roles?.[roleId];
  if (!role || text(role.team_id) !== teamId) {
    return rejectCoreAgentSessionCreate(
      form,
      "role_id",
      "Select a valid role for this team before creating a core agent session.",
    );
  }
  const projectId = text(data.project_id);
  if (projectId) {
    const project = state.projects?.[projectId];
    if (!project || text(project.team_id) !== teamId) {
      return rejectCoreAgentSessionCreate(
        form,
        "project_id",
        "Select a valid project for this team before creating a core agent session.",
      );
    }
  }
  const roleProjectId = text(role.project_id);
  if (roleProjectId && roleProjectId !== projectId) {
    return rejectCoreAgentSessionCreate(
      form,
      "project_id",
      "Select the project required by the selected role before creating a core agent session.",
    );
  }
  const reportsToAgentId = text(data.reports_to_agent_id);
  if (reportsToAgentId) {
    const superior = state.agents?.[reportsToAgentId];
    if (!superior || text(superior.team_id) !== teamId) {
      return rejectCoreAgentSessionCreate(
        form,
        "reports_to_agent_id",
        "Select a valid superior agent for this team before creating a core agent session.",
      );
    }
  }
  return true;
}

function normalizedAgentRegistrationTeamId(teamId) {
  const normalizedTeamId = text(teamId);
  return normalizedTeamId === noTeamRecordId ? "" : normalizedTeamId;
}

function rejectCoreAgentSessionCreate(form, fieldName, message) {
  setStatus(message, "error");
  const field = form.elements?.[fieldName];
  if (field) {
    field.focus?.();
  }
  return false;
}

function openCoreAgentCreateDialogForForm(form) {
  if (!validateTeamAgentSessionCreateRegistration(form)) {
    return;
  }
  const contextId = formIdentifier(form) || `register-agent-${Date.now()}`;
  if (!form.id) {
    form.id = contextId;
  }
  pendingTeamAgentCreateForms.set(contextId, form.id);
  const detail = {
    contextId,
    directoryPath: teamAgentDirectoryPath(form),
  };
  if (typeof window.openTeamAgentCreateDialog === "function") {
    window.openTeamAgentCreateDialog(detail);
    return;
  }
  window.dispatchEvent(new CustomEvent("team-agent-create:open", { detail }));
}

async function completeTeamAgentCreateRegistration(detail) {
  const contextId = text(detail.contextId);
  const sessionId = text(detail.sessionId);
  if (!sessionId) {
    setStatus("Created agent session did not return a session ID.", "error");
    return;
  }
  const formId = pendingTeamAgentCreateForms.get(contextId);
  const form = formId ? document.getElementById(formId) : null;
  pendingTeamAgentCreateForms.delete(contextId);
  if (!form) {
    setStatus(`Created session ${sessionId}, but the Team registration form is no longer open.`, "warning");
    return;
  }
  setFieldValue(form, "session_id", sessionId);
  const existingSessionSelect = form.elements?.existing_session_id;
  if (existingSessionSelect) {
    existingSessionSelect.dataset.currentValue = sessionId;
  }
  await loadCoreAgentSessions({
    directoryPath: teamAgentDirectoryPath(form),
    force: true,
    rerender: false,
  });
  refreshCoreAgentSessionPicker(form, sessionId);
  setStatus(`Created session ${sessionId}; registering Team agent.`, "loading");
  const result = await submitCreateForm(form, { detailedResult: true });
  if (!result.saved && result.failureReason !== "conflict") {
    setStatus(`Created session ${sessionId}; review the Team registration error and submit again.`, "warning");
  }
}

function teamAgentDirectoryPath(form = null) {
  const fromForm = text(form?.dataset?.directoryPath);
  if (fromForm) {
    return fromForm;
  }
  const fromQuery = new URLSearchParams(window.location.search).get("directory_path");
  return text(fromQuery) || "root";
}

function readTeamPrivilegedUnlockedFromLocation() {
  return new URLSearchParams(window.location.search).get("privileged_unlocked") === "true";
}

function teamPrivilegedUnlocked() {
  return teamPrivilegeUnlocked;
}

function setTeamPrivilegedUnlocked(unlocked) {
  const nextUnlocked = Boolean(unlocked);
  if (teamPrivilegeUnlocked === nextUnlocked) {
    return;
  }
  const drafts = captureFormDrafts();
  teamPrivilegeUnlocked = nextUnlocked;
  render();
  restoreFormDrafts(drafts);
}

function setTeamTheme(theme) {
  const normalizedTheme = text(theme);
  if (normalizedTheme !== "light" && normalizedTheme !== "dark") {
    return;
  }
  document.documentElement.dataset.theme = normalizedTheme;
  document.documentElement.style.colorScheme = normalizedTheme;
}

function bindPrivilegeMessages() {
  window.addEventListener("message", (event) => {
    if (
      event.origin !== window.location.origin
      || event.source !== window.parent
    ) {
      return;
    }
    const data = event.data || {};
    if (data.type === "admin-addon-context") {
      setTeamTheme(data.theme);
      setTeamPrivilegedUnlocked(Boolean(data.privileged_operations?.unlocked));
      return;
    }
    if (data.type === "admin-privileged-operations") {
      setTeamPrivilegedUnlocked(Boolean(data.privileged_operations?.unlocked));
    }
  });
  if (window.parent !== window) {
    window.parent.postMessage(
      { type: "admin-addon-ready" },
      window.location.origin,
    );
  }
}

function teamMutationLocked() {
  return !teamPrivilegedUnlocked();
}

function lockedMutationMessage(action = "make changes") {
  const normalizedAction = text(action) || "make changes";
  return `Unlock privileged operations to ${normalizedAction}.`;
}

function lockedAttrs(action = "make changes") {
  if (!teamMutationLocked()) {
    return "";
  }
  const message = lockedMutationMessage(action);
  return `disabled aria-disabled="true" title="${escapeAttr(message)}"`;
}

function guardTeamMutation(action = "make changes") {
  if (!teamMutationLocked()) {
    return true;
  }
  setStatus(lockedMutationMessage(action), "warning");
  return false;
}

const lockedMutationControlSelectors = [
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

function applyLockedMutationControls(root = document) {
  if (!teamMutationLocked()) {
    const controls = [];
    if (root?.matches?.(".is-locked")) {
      controls.push(root);
    }
    root?.querySelectorAll?.(".is-locked").forEach((control) => controls.push(control));
    controls.forEach(unlockMutationControl);
    return;
  }
  for (const [selector, action] of lockedMutationControlSelectors) {
    const controls = [];
    if (root?.matches?.(selector)) {
      controls.push(root);
    }
    root?.querySelectorAll?.(selector).forEach((control) => controls.push(control));
    controls.forEach((control) => lockMutationControl(control, action));
  }
}

function lockMutationControl(control, action = "make changes") {
  if (!control) {
    return;
  }
  if (!lockedMutationControlStates.has(control)) {
    lockedMutationControlStates.set(control, {
      disabled:
        control instanceof HTMLButtonElement ||
        control instanceof HTMLInputElement ||
        control instanceof HTMLSelectElement ||
        control instanceof HTMLTextAreaElement
          ? control.disabled
          : null,
      ariaDisabled: control.getAttribute("aria-disabled"),
      title: control.getAttribute("title"),
    });
  }
  const message = lockedMutationMessage(action);
  if (
    control instanceof HTMLButtonElement ||
    control instanceof HTMLInputElement ||
    control instanceof HTMLSelectElement ||
    control instanceof HTMLTextAreaElement
  ) {
    control.disabled = true;
  }
  control.setAttribute("aria-disabled", "true");
  control.setAttribute("title", message);
  control.classList?.add?.("is-locked");
}

function unlockMutationControl(control) {
  const previous = lockedMutationControlStates.get(control);
  if (!control || !previous) {
    return;
  }
  if (previous.disabled !== null) {
    control.disabled = previous.disabled;
  }
  restoreOptionalAttribute(control, "aria-disabled", previous.ariaDisabled);
  restoreOptionalAttribute(control, "title", previous.title);
  control.classList?.remove?.("is-locked");
  lockedMutationControlStates.delete(control);
}

function restoreOptionalAttribute(control, name, value) {
  if (value === null) {
    control.removeAttribute(name);
  } else {
    control.setAttribute(name, value);
  }
}

function registeredTeamSessionIds() {
  return new Set(values(state.agents).map((agent) => text(agent.session_id)).filter(Boolean));
}

function normalizeCoreAgentSession(item) {
  const sessionId = text(item?.session_id);
  if (!sessionId) {
    return null;
  }
  const backend = text(item?.backend);
  const targetName = text(item?.target_name) || text(item?.agent_id) || text(item?.vm_name) || sessionId;
  return {
    session_id: sessionId,
    agent_id: text(item?.agent_id),
    backend,
    target_name: targetName,
    status: text(item?.status),
    directory_path: text(item?.directory_path),
    display_name: [targetName, backendLabelForCoreAgentSession(backend)].filter(Boolean).join(" - "),
  };
}

function backendLabelForCoreAgentSession(backend) {
  if (backend === "container_worker_vm") {
    return "container";
  }
  if (backend === "libvirt_vm") {
    return "VM";
  }
  return backend;
}

async function loadCoreAgentSessions({ directoryPath = "root", force = false, rerender = false } = {}) {
  const normalizedDirectoryPath = text(directoryPath) || "root";
  if (
    !force &&
    coreAgentSessionsDirectoryPath === normalizedDirectoryPath &&
    coreAgentSessions.length > 0
  ) {
    return coreAgentSessions;
  }
  if (coreAgentSessionsLoading) {
    return coreAgentSessionsLoading;
  }
  const query = new URLSearchParams({
    directory_path: normalizedDirectoryPath,
    direct_only: "true",
    include_inactive: "true",
    backend: "all",
    summary_only: "false",
    reconcile_containers: "true",
    omit_vm_state: "false",
  });
  coreAgentSessionsLoading = (async () => {
    const response = await teamFetch(`/api/admin/agents?${query.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    coreAgentSessions = (payload.items || [])
      .map(normalizeCoreAgentSession)
      .filter(Boolean)
      .sort((left, right) =>
        text(left.target_name).localeCompare(text(right.target_name), undefined, {
          numeric: true,
          sensitivity: "base",
        }) || text(left.session_id).localeCompare(text(right.session_id)),
      );
    coreAgentSessionsDirectoryPath = normalizedDirectoryPath;
    return coreAgentSessions;
  })();
  try {
    return await coreAgentSessionsLoading;
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Agent sessions could not be loaded.", "error");
    return coreAgentSessions;
  } finally {
    coreAgentSessionsLoading = null;
    if (rerender) {
      renderAgents();
      decorateFieldTooltips(document);
    }
  }
}

async function refreshCoreAgentSessionsForForm(form, { force = false } = {}) {
  if (!form) {
    setStatus("Agent registration form is unavailable.", "error");
    return;
  }
  await loadCoreAgentSessions({
    directoryPath: teamAgentDirectoryPath(form),
    force,
    rerender: false,
  });
  refreshCoreAgentSessionPicker(form);
}

function refreshCoreAgentSessionPicker(form, selectedValue = "") {
  const select = form?.elements?.existing_session_id;
  if (!select?.matches?.("[data-core-agent-session-picker]")) {
    return;
  }
  const nextValue = text(selectedValue) || text(select.value) || text(select.dataset.currentValue);
  select.innerHTML = renderCoreAgentSessionOptions(nextValue);
  if (nextValue && Array.from(select.options).some((option) => option.value === nextValue)) {
    select.value = nextValue;
  }
}

function renderCoreAgentSessionOptions(selectedValue = "") {
  const registeredSessions = registeredTeamSessionIds();
  const options = coreAgentSessions
    .filter((session) => session.session_id === selectedValue || !registeredSessions.has(session.session_id))
    .map((session) => [
      session.session_id,
      `${session.target_name} (${session.session_id})${session.backend ? ` - ${backendLabelForCoreAgentSession(session.backend)}` : ""}`,
    ]);
  if (coreAgentSessionsLoading) {
    return renderSelectOptions(options, { selectedValue, placeholder: "Loading sessions..." });
  }
  return renderSelectOptions(options, {
    selectedValue,
    placeholder: options.length ? "Select existing session" : "No unregistered sessions found",
  });
}

function bindProjectLeadSelects() {
  document.addEventListener("change", (event) => {
    const teamSelect = event.target?.closest?.('form [name="team_id"]');
    if (!teamSelect) {
      return;
    }
    if (formSemanticIdentifier(teamSelect.form) !== "create-project-form") {
      return;
    }
    refreshProjectLeadSelect(teamSelect.form);
  });
}

function bindAgentEditTeamScopedSelects() {
  document.addEventListener("change", (event) => {
    const contextPolicySelect = event.target?.closest?.(
      'select[name="context_clearing_policy"]',
    );
    if (
      contextPolicySelect?.form === elements.editForm
      && text(elements.editForm?.dataset.editType) === "agent"
    ) {
      syncAgentEditContextIdleGraceField(contextPolicySelect.form, {
        suggestDefaultGrace: true,
      });
    }
    const teamSelect = event.target?.closest?.('select[name="team_id"]');
    if (!teamSelect || teamSelect.form !== elements.editForm) {
      return;
    }
    if (text(elements.editForm?.dataset.editType) !== "agent") {
      return;
    }
    refreshAgentEditTeamScopedSelects(teamSelect.form);
  });
}

function syncAgentEditContextIdleGraceField(form, { suggestDefaultGrace = false } = {}) {
  const contextPolicySelect = form?.elements?.context_clearing_policy;
  const idleGraceField = form?.querySelector?.("[data-agent-context-idle-grace]");
  if (!contextPolicySelect || !idleGraceField) {
    return;
  }
  const isIdle = text(contextPolicySelect.value) === "idle";
  idleGraceField.hidden = !isIdle;
  if (!isIdle || !suggestDefaultGrace) {
    return;
  }
  const graceInput = form.elements.context_clearing_idle_grace_minutes;
  if (graceInput && numberOrZero(graceInput.value) === 0) {
    graceInput.value = "60";
  }
}

function refreshAgentEditTeamScopedSelects(form) {
  if (!form || text(form.dataset.editType) !== "agent") {
    return;
  }
  const options = buildSelectOptionSets();
  form.querySelectorAll('select[data-options^="agent-edit-"]').forEach((select) => {
    fillSelect(select, optionsForSelect(select, options));
  });
}

function refreshProjectLeadSelect(form) {
  const select = form?.elements?.lead_agent_id;
  if (!select?.matches?.('select[data-options="project-lead-agents"]')) {
    return;
  }
  fillSelect(select, projectLeadAgentOptions(select));
}

function decorateFieldTooltips(root = document) {
  root.querySelectorAll("form label").forEach((label) => {
    if (label.dataset.tooltipBound === "true") {
      return;
    }
    const field = label.querySelector("input[name], select[name], textarea[name]");
    if (!field) {
      return;
    }
    const tooltip = tooltipForField(label, field);
    if (!tooltip) {
      return;
    }
    label.dataset.tooltipBound = "true";
    label.classList.add("has-tooltip");
    insertTooltip(label, field, tooltip);
  });
}

function tooltipForField(label, field) {
  const form = field.form;
  const formId = formSemanticIdentifier(form);
  const aliasedFormId = formId === "create-team-from-teams-form" ? "create-team-form" : "";
  const fieldName = text(field.name);
  const tooltipKey = text(field.dataset.tooltipKey);
  const editType = text(form?.dataset?.editType);
  const keys = [];
  if (editType) {
    keys.push(`edit:${editType}:${fieldName}`);
  }
  if (formId) {
    keys.push(`${formId}:${fieldName}`);
    if (tooltipKey) {
      keys.push(`${formId}:${tooltipKey}`);
    }
  }
  if (aliasedFormId) {
    keys.push(`${aliasedFormId}:${fieldName}`);
    if (tooltipKey) {
      keys.push(`${aliasedFormId}:${tooltipKey}`);
    }
  }
  keys.push(fieldName);
  if (tooltipKey) {
    keys.push(tooltipKey);
  }
  const labelText = text(label.textContent);
  if (labelText) {
    keys.push(labelText.toLowerCase());
  }
  for (const key of keys) {
    if (fieldTooltips[key]) {
      return fieldTooltips[key];
    }
  }
  return "";
}

function insertTooltip(label, field, tooltip) {
  const tooltipNode = createTooltipNode(tooltip);
  if (field.type === "checkbox" || label.classList.contains("checkbox-label")) {
    label.append(" ");
    label.appendChild(tooltipNode);
    return;
  }

  const labelRow = document.createElement("span");
  labelRow.className = "field-label-row";
  const labelText = document.createElement("span");
  labelText.className = "field-label-text";
  while (label.firstChild && label.firstChild !== field) {
    labelText.appendChild(label.firstChild);
  }
  labelText.textContent = text(labelText.textContent);
  labelRow.appendChild(labelText);
  labelRow.appendChild(tooltipNode);
  label.insertBefore(labelRow, field);
}

function createTooltipNode(tooltip) {
  const wrapper = document.createElement("span");
  wrapper.className = "field-tooltip";
  wrapper.tabIndex = 0;
  wrapper.setAttribute("role", "button");
  wrapper.setAttribute("aria-label", `Help: ${tooltip}`);

  const icon = document.createElement("span");
  icon.className = "field-tooltip-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "i";

  const bubble = document.createElement("span");
  bubble.className = "field-tooltip-bubble";
  bubble.setAttribute("role", "tooltip");
  bubble.id = `team-field-tooltip-${++tooltipCounter}`;
  bubble.textContent = tooltip;
  wrapper.setAttribute("aria-describedby", bubble.id);

  wrapper.appendChild(icon);
  wrapper.appendChild(bubble);
  return wrapper;
}

function bindWorkspaceReviewDecisionForms() {
  document.querySelectorAll("form[data-review-decision-form]").forEach((form) => {
    if (form.dataset.boundReviewDecision === "true") {
      return;
    }
    form.dataset.boundReviewDecision = "true";
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitReviewDecision(form);
    });
  });
}

function bindDeliveryFilters() {
  document.querySelectorAll(".delivery-filter").forEach((button) => {
    if (button.matches("[data-my-human-reviews]")) {
      return;
    }
    button.addEventListener("click", () => {
      const filter = button.dataset.filter || "all";
      setDeliveryFilter(filter);
    });
  });
  document.querySelector("[data-my-human-reviews]")?.addEventListener("click", () => {
    myHumanReviewsOnly = !myHumanReviewsOnly;
    if (!myHumanReviewsOnly) {
      focusedHumanReviewRequestId = "";
    }
    updateMyHumanReviewsFilterButton();
    if (myHumanReviewsOnly) {
      void loadMyHumanReviews();
      return;
    }
    renderReleases();
  });
  document.addEventListener("change", (event) => {
    const releaseHistoryFilter = event.target?.closest?.("[data-release-history-filter]");
    if (releaseHistoryFilter) {
      const value = text(releaseHistoryFilter.value);
      switch (releaseHistoryFilter.dataset.releaseHistoryFilter) {
        case "state":
          selectedReleaseHistoryState = releaseHistoryStates.includes(value) ? value : "";
          break;
        case "date-from":
          releaseHistoryDateFrom = value;
          break;
        case "date-through":
          releaseHistoryDateThrough = value;
          break;
        default:
          return;
      }
      renderReleases();
      return;
    }
    const projectSelect = event.target?.closest?.("[data-delivery-project-filter]");
    if (!projectSelect) {
      return;
    }
    selectedDeliveryProjectId = text(projectSelect.value);
    if (selectedDeliveryProjectId) {
      openTeamDisclosures.add(
        teamDisclosureStateKey(
          teamDisclosureKey("delivery", "project", selectedDeliveryProjectId),
        ),
      );
    }
    renderReviewDeliveryWorkspace();
  });
  document.addEventListener("click", (event) => {
    const copyButton = event.target?.closest?.("[data-copy-identifier]");
    if (copyButton) {
      event.preventDefault();
      void copyTechnicalIdentifier(copyButton);
      return;
    }
    const retryButton = event.target?.closest?.("[data-my-human-reviews-retry]");
    if (retryButton) {
      event.preventDefault();
      void loadMyHumanReviews();
      return;
    }
    const pipelineDetailButton = event.target?.closest?.("[data-delivery-pipeline-detail-id]");
    if (pipelineDetailButton) {
      event.preventDefault();
      const releaseId = text(pipelineDetailButton.dataset.deliveryPipelineDetailId);
      if (releaseId) {
        void loadReleaseDetail(releaseId);
      }
      return;
    }
    const detailButton = event.target?.closest?.("[data-release-detail-id]");
    if (detailButton) {
      event.preventDefault();
      void toggleReleaseDetail(text(detailButton.dataset.releaseDetailId));
      return;
    }
    if (event.target?.closest?.("[data-clear-release-history-filters]")) {
      event.preventDefault();
      resetReleaseHistoryFilters();
      renderReleases();
      return;
    }
    const resumeButton = event.target?.closest?.("[data-human-review-resume-id]");
    if (resumeButton) {
      event.preventDefault();
      if (guardTeamMutation("resume human reviews")) {
        void resumeHumanReview(resumeButton, text(resumeButton.dataset.humanReviewResumeId));
      }
      return;
    }
    const reassignButton = event.target?.closest?.("[data-human-review-reassign-id]");
    if (!reassignButton) {
      return;
    }
    event.preventDefault();
    if (!guardTeamMutation("reassign human reviews")) {
      return;
    }
    const userIds = window.prompt(
      "Assign this human review to Admin Web user IDs (comma or newline separated).",
      "",
    );
    if (userIds === null) {
      return;
    }
    void reassignHumanReview(
      reassignButton,
      text(reassignButton.dataset.humanReviewReassignId),
      splitList(userIds),
    );
  });
  document.addEventListener("submit", (event) => {
    const postReleaseReportForm = event.target?.closest?.("[data-post-release-report-form]");
    if (postReleaseReportForm) {
      event.preventDefault();
      void submitPostReleaseReport(postReleaseReportForm, event.submitter);
      return;
    }
    const humanReviewForm = event.target?.closest?.("[data-human-review-decision-form]");
    if (!humanReviewForm) {
      return;
    }
    event.preventDefault();
    void submitHumanReviewDecision(humanReviewForm, event.submitter);
  });
}

function bindPlanningFilters() {
  document.querySelectorAll("[data-planning-task-status]").forEach((input) => {
    input.addEventListener("change", () => {
      const status = input.dataset.planningTaskStatus;
      if (!taskStates.includes(status)) {
        return;
      }
      if (input.checked) {
        selectedPlanningTaskStates.add(status);
      } else {
        selectedPlanningTaskStates.delete(status);
      }
      renderProjects();
    });
  });
  updatePlanningFilterControls();
}

function bindTeamDisclosures() {
  document.addEventListener("toggle", (event) => {
    const disclosure = event.target;
    if (!disclosure?.matches?.("details[data-team-disclosure-key]")) {
      return;
    }
    const key = text(disclosure.dataset.teamDisclosureKey);
    if (!key) {
      return;
    }
    if (disclosure.open) {
      openTeamDisclosures.add(key);
    } else {
      openTeamDisclosures.delete(key);
    }
  }, true);
}

function bindProjectFocus() {
  document.addEventListener("click", (event) => {
    const createToggle = event.target?.closest?.("[data-project-create-toggle]");
    if (createToggle) {
      event.preventDefault();
      if (!guardTeamMutation("create projects")) {
        return;
      }
      projectCreateFormOpen = !projectCreateFormOpen;
      syncProjectCreatePanel();
      if (projectCreateFormOpen) {
        const form = document.querySelector("[data-project-create-form]");
        if (form) {
          fillSelectsIn(form);
          decorateFieldTooltips(form);
        }
        form?.querySelector("select, input, textarea")?.focus();
      }
      return;
    }

    const createClose = event.target?.closest?.("[data-project-create-close]");
    if (createClose) {
      event.preventDefault();
      projectCreateFormOpen = false;
      syncProjectCreatePanel({ reset: true });
      return;
    }

    const button = event.target?.closest?.("[data-project-focus-id]");
    if (button) {
      event.preventDefault();
      selectedProjectFocusId = text(button.dataset.projectFocusId) || null;
      projectReviewProfileCreateProjectId = null;
      renderProjectFocus();
      return;
    }

    const reviewProfileCreateToggle = event.target?.closest?.("[data-project-review-profile-create-toggle]");
    if (reviewProfileCreateToggle) {
      event.preventDefault();
      if (!guardTeamMutation("create review profiles")) {
        return;
      }
      const projectId = text(reviewProfileCreateToggle.dataset.projectId);
      projectReviewProfileCreateProjectId =
        text(projectReviewProfileCreateProjectId) === projectId ? null : projectId;
      renderProjectFocus();
      const form = document.querySelector("[data-project-review-profile-create-form]");
      if (form) {
        fillSelectsIn(form);
        decorateFieldTooltips(form);
        form.querySelector("input, select, textarea")?.focus();
      }
      return;
    }

    const reviewProfileCreateClose = event.target?.closest?.("[data-project-review-profile-create-close]");
    if (reviewProfileCreateClose) {
      event.preventDefault();
      projectReviewProfileCreateProjectId = null;
      renderProjectFocus();
      return;
    }

    const rejectedToggle = event.target?.closest?.("[data-project-section-show-rejected]");
    if (rejectedToggle) {
      event.preventDefault();
      const key = projectRecordSectionKey(
        rejectedToggle.dataset.projectId,
        rejectedToggle.dataset.projectSection,
      );
      if (projectRecordShowRejected.has(key)) {
        projectRecordShowRejected.delete(key);
      } else {
        projectRecordShowRejected.add(key);
      }
      projectRecordSectionPages[key] = 1;
      openTeamDisclosures.add(teamDisclosureStateKey(key));
      renderProjectFocus();
      return;
    }

    const pageButton = event.target?.closest?.("[data-project-section-page]");
    if (pageButton) {
      event.preventDefault();
      const key = projectRecordSectionKey(
        pageButton.dataset.projectId,
        pageButton.dataset.projectSection,
      );
      projectRecordSectionPages[key] = Math.max(1, Math.floor(Number(pageButton.dataset.projectSectionPage) || 1));
      openTeamDisclosures.add(teamDisclosureStateKey(key));
      renderProjectFocus();
    }
  });
}

function bindTaskBoardSelectors() {
  const teamSelect = document.querySelector("[data-task-board-team]");
  const projectSelect = document.querySelector("[data-task-board-project]");
  const updateTeam = () => {
    const nextTeamId = text(teamSelect.value);
    if (nextTeamId === selectedTaskBoardTeamId) {
      return;
    }
    selectedTaskBoardTeamId = nextTeamId;
    selectedTaskBoardProjectId = "";
    resetTaskBoardProjectState();
    renderTaskBoard();
  };
  const updateProject = () => {
    const nextProjectId = text(projectSelect.value);
    if (nextProjectId === selectedTaskBoardProjectId) {
      return;
    }
    selectedTaskBoardProjectId = nextProjectId;
    resetTaskBoardProjectState();
    renderTaskBoard();
  };
  teamSelect?.addEventListener("input", updateTeam);
  teamSelect?.addEventListener("change", updateTeam);
  projectSelect?.addEventListener("input", updateProject);
  projectSelect?.addEventListener("change", updateProject);

  document.addEventListener("submit", (event) => {
    const createForm = event.target?.closest?.("[data-task-board-create-form]");
    if (createForm) {
      event.preventDefault();
      void submitTaskBoardCreateForm(createForm);
      return;
    }
    const form = event.target?.closest?.("[data-task-board-search-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    taskBoardQuery = text(new FormData(form).get("task_board_query"));
    resetTaskBoardPagination();
    renderTaskBoard();
  });

  document.addEventListener("change", (event) => {
    const completedToggle = event.target?.closest?.("[data-task-board-show-completed]");
    if (completedToggle instanceof HTMLInputElement) {
      taskBoardShowCompleted = completedToggle.checked;
      resetTaskBoardPagination();
      renderTaskBoard();
      return;
    }
    const filter = event.target?.closest?.("[data-task-board-filter]");
    if (!filter) {
      return;
    }
    const value = text(filter.value);
    switch (filter.dataset.taskBoardFilter) {
      case "assignee":
        taskBoardAssigneeId = value;
        break;
      case "state":
        taskBoardStateFilter = taskStates.includes(value) ? value : "";
        if (terminalTaskStates.has(taskBoardStateFilter)) {
          taskBoardShowCompleted = true;
        }
        break;
      case "readiness":
        taskBoardReadinessFilter = taskBoardReadinessValues.includes(value) ? value : "";
        break;
      case "root":
        taskBoardRootTaskId = value;
        break;
      case "completed-since":
        taskBoardCompletedSince = value;
        break;
      case "completed-through":
        taskBoardCompletedThrough = value;
        break;
      default:
        return;
    }
    resetTaskBoardPagination();
    renderTaskBoard();
  });

  document.addEventListener("click", (event) => {
    const modeButton = event.target?.closest?.("[data-task-board-mode]");
    if (modeButton) {
      event.preventDefault();
      const nextMode = text(modeButton.dataset.taskBoardMode);
      if (taskBoardViewModes.has(nextMode)) {
        taskBoardViewMode = nextMode;
        prepareSelectedTaskBoardTaskForDisplay();
        renderTaskBoard();
        focusSelectedTaskBoardTask();
      }
      return;
    }
    const selectButton = event.target?.closest?.("[data-task-board-select-task-id]");
    if (selectButton) {
      event.preventDefault();
      selectTaskBoardTask(text(selectButton.dataset.taskBoardSelectTaskId), {
        reveal: selectButton.hasAttribute("data-task-board-reveal-task"),
      });
      return;
    }
    if (event.target?.closest?.("[data-task-board-clear-focus]")) {
      event.preventDefault();
      selectedTaskBoardTaskId = "";
      renderTaskBoard();
      return;
    }
    if (event.target?.closest?.("[data-task-board-clear-filters]")) {
      event.preventDefault();
      resetTaskBoardFilters();
      renderTaskBoard();
      return;
    }
    if (event.target?.closest?.("[data-task-board-reveal-completed]")) {
      event.preventDefault();
      taskBoardShowCompleted = true;
      resetTaskBoardPagination();
      renderTaskBoard();
      return;
    }
    const hierarchyToggle = event.target?.closest?.("[data-task-board-toggle-children-id]");
    if (hierarchyToggle) {
      event.preventDefault();
      const taskId = text(hierarchyToggle.dataset.taskBoardToggleChildrenId);
      if (expandedTaskBoardTaskIds.has(taskId)) {
        expandedTaskBoardTaskIds.delete(taskId);
      } else {
        expandedTaskBoardTaskIds.add(taskId);
      }
      renderTaskBoard();
      focusTaskBoardControl(taskId, "[data-task-board-toggle-children-id]");
      return;
    }
    const lanePageButton = event.target?.closest?.("[data-task-board-lane-page]");
    if (lanePageButton) {
      event.preventDefault();
      const laneId = text(lanePageButton.dataset.taskBoardLaneId);
      const page = Math.max(1, Math.floor(Number(lanePageButton.dataset.taskBoardLanePage) || 1));
      taskBoardLanePages.set(laneId, page);
      renderTaskBoard();
      focusTaskBoardControl(laneId, "[data-task-board-lane-id]");
      return;
    }
    if (event.target?.closest?.("[data-task-board-more-roots]")) {
      event.preventDefault();
      taskBoardHierarchyRootLimit += taskBoardHierarchyPageSize;
      renderTaskBoard();
      return;
    }
    const moreChildrenButton = event.target?.closest?.("[data-task-board-more-children-id]");
    if (moreChildrenButton) {
      event.preventDefault();
      const taskId = text(moreChildrenButton.dataset.taskBoardMoreChildrenId);
      const currentLimit =
        taskBoardHierarchyChildLimits.get(taskId) || taskBoardHierarchyPageSize;
      taskBoardHierarchyChildLimits.set(taskId, currentLimit + taskBoardHierarchyPageSize);
      renderTaskBoard();
      focusTaskBoardControl(taskId, "[data-task-board-toggle-children-id]");
    }
  });
}

async function submitTaskBoardCreateForm(form) {
  const result = await submitCreateForm(form);
  if (mutationResultSaved(result)) {
    renderTaskBoard();
  }
}

function resetTaskBoardProjectState() {
  resetTaskBoardFilters();
  selectedTaskBoardTaskId = "";
  expandedTaskBoardTaskIds.clear();
  taskBoardHierarchyChildLimits.clear();
  taskBoardHierarchyInitialized = false;
}

function resetTaskBoardFilters() {
  taskBoardShowCompleted = false;
  taskBoardQuery = "";
  taskBoardAssigneeId = "";
  taskBoardStateFilter = "";
  taskBoardReadinessFilter = "";
  taskBoardRootTaskId = "";
  taskBoardCompletedSince = "";
  taskBoardCompletedThrough = "";
  resetTaskBoardPagination();
}

function resetTaskBoardPagination() {
  taskBoardLanePages.clear();
  taskBoardHierarchyRootLimit = taskBoardHierarchyPageSize;
  taskBoardHierarchyChildLimits.clear();
}

function bindOrgFocusTabs() {
  elements.orgFocusTeamTabs?.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-org-team-id]");
    if (!button) {
      return;
    }
    selectedOrgTeamId = button.dataset.orgTeamId || "all";
    renderFocusedOrgChart();
  });
}

function bindOrgFlowEvents() {
  window.addEventListener("team-org-flow:edit-agent", (event) => {
    const agentId = text(event.detail?.agentId);
    if (!agentId) {
      return;
    }
    openEditDialog("agent", agentId);
  });
  window.addEventListener("team-org-flow:canvas-menu", (event) => {
    openOrgCanvasContextMenu(event.detail);
  });
  window.addEventListener("team-org-flow:agent-menu", (event) => {
    openOrgAgentContextMenu(event.detail);
  });
  window.addEventListener("team-org-flow:governance-menu", (event) => {
    openOrgGovernanceContextMenu(event.detail);
  });
  window.addEventListener("team-org-flow:dismiss-menu", () => {
    closeOrgContextMenu();
  });

  document.addEventListener("submit", (event) => {
    const form = event.target?.closest?.("form[data-org-action-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    void submitOrgActionForm(form);
  });

  document.addEventListener("click", (event) => {
    const closeTarget = event.target?.closest?.("[data-org-action-close]");
    if (closeTarget) {
      if (closeTarget.classList.contains("edit-modal-backdrop") && event.target !== closeTarget) {
        return;
      }
      event.preventDefault();
      closeOrgActionDialog();
      return;
    }

    const menu = document.querySelector("#org-context-menu");
    if (menu && !menu.hidden && !menu.contains(event.target)) {
      closeOrgContextMenu();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") {
      return;
    }
    const actionDialog = document.querySelector("#org-action-modal");
    if (actionDialog && !actionDialog.hidden) {
      closeOrgActionDialog();
      return;
    }
    const menu = document.querySelector("#org-context-menu");
    if (menu && !menu.hidden) {
      closeOrgContextMenu();
    }
  });
}

function updatePlanningFilterControls() {
  document.querySelectorAll("[data-planning-task-status]").forEach((input) => {
    const status = input.dataset.planningTaskStatus;
    input.checked = selectedPlanningTaskStates.has(status);
  });
}

async function bootTeamAddon() {
  setStatus("Loading team view.", "loading");
  renderShell();
  renderActiveViewLoading(activeTeamView);
  const bootstrapPromise = loadBootstrap();
  const viewPromise = loadView(activeTeamView, { force: true });
  const results = await Promise.allSettled([bootstrapPromise, viewPromise]);
  const rejection = results.find((result) => result.status === "rejected");
  if (rejection) {
    const reason = rejection.reason;
    setStatus(reason instanceof Error ? reason.message : "Team view failed to load.", "error");
    return;
  }
  if (initialHumanReviewDeepLink.enabled) {
    const focused = await openHumanReviewDeepLink(
      initialHumanReviewDeepLink.requestId,
    );
    if (!focused && initialHumanReviewDeepLink.requestId) {
      setStatus(
        `Human review ${initialHumanReviewDeepLink.requestId} is not assigned or visible; showing your assigned reviews instead.`,
        "warning",
      );
      scheduleIdleViewPrefetch();
      return;
    }
  }
  setStatus("", "");
  scheduleIdleViewPrefetch();
}

async function loadBootstrap() {
  const response = await teamFetch(`${API_BASE}/bootstrap`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(await responseText(response));
  }
  const payload = await response.json();
  bootstrapLoaded = true;
  state = normalizeState({ ...state, revision: payload.revision ?? state.revision });
  communicationTargets = normalizeCommunicationTargetOptions(payload.communication_targets);
  selectedChatOperatorUserId = text(payload.selected_operator?.user_id) || selectedChatOperatorUserId;
  chatState = normalizeChat({ ...chatState, ...chatScopeFields(payload), revision: payload.revision ?? chatState.revision });
  renderShell();
  if (activeTeamView === "chat") {
    renderChat();
  }
  return payload;
}

async function loadView(view, { force = false, preserveForms = false } = {}) {
  const normalizedView = normalizedTeamView(view);
  if (!force && loadedTeamViews.has(normalizedView)) {
    renderShell();
    renderActiveView(normalizedView);
    return null;
  }
  if (loadingTeamViews.has(normalizedView)) {
    const existingLoad = loadingTeamViews.get(normalizedView);
    if (force) {
      const pending = pendingForcedTeamViewLoads.get(normalizedView) || { preserveForms: false };
      pending.preserveForms = pending.preserveForms || preserveForms;
      pendingForcedTeamViewLoads.set(normalizedView, pending);
      return existingLoad.finally(() => {
        const pendingLoad = pendingForcedTeamViewLoads.get(normalizedView);
        if (!pendingLoad) {
          return null;
        }
        pendingForcedTeamViewLoads.delete(normalizedView);
        return loadView(normalizedView, { force: true, preserveForms: pendingLoad.preserveForms });
      });
    }
    return existingLoad;
  }
  if (normalizedView === "chat") {
    const loadGeneration = teamViewCacheGeneration;
    const promise = loadChatView({ force, preserveForms, loadGeneration }).finally(() => {
      loadingTeamViews.delete(normalizedView);
    });
    loadingTeamViews.set(normalizedView, promise);
    return promise;
  }
  const loadGeneration = teamViewCacheGeneration;
  const promise = (async () => {
    const query = normalizedView === "timeline" ? timelineSearchParams() : new URLSearchParams();
    const response = await teamFetch(`${API_BASE}/views/${encodeURIComponent(normalizedView)}${query.toString() ? `?${query.toString()}` : ""}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    if (loadGeneration !== teamViewCacheGeneration) {
      return payload;
    }
    applyViewPayload(payload);
    loadedTeamViews.add(normalizedView);
    if (normalizedView === activeTeamView) {
      const drafts = preserveForms ? captureFormDrafts() : null;
      render();
      if (drafts) {
        restoreFormDrafts(drafts);
      }
      scheduleAuxiliaryLoadsForView(normalizedView);
    }
    return payload;
  })().finally(() => {
    loadingTeamViews.delete(normalizedView);
  });
  loadingTeamViews.set(normalizedView, promise);
  return promise;
}

async function loadChatView({
  force = false,
  preserveForms = false,
  loadGeneration = teamViewCacheGeneration,
} = {}) {
  if (!force && loadedTeamViews.has("chat")) {
    renderShell();
    renderChat();
    syncChatStreamForActiveView();
    return null;
  }
  const requestId = ++chatRequestId;
  chatState = { ...chatState, loading: true, error: null };
  if (activeTeamView === "chat") {
    const drafts = preserveForms ? captureFormDrafts() : null;
    scheduleChatScrollForRender(selectedChatConversationId);
    renderChat();
    if (drafts) {
      restoreFormDrafts(drafts);
    }
  }
  try {
    const query = new URLSearchParams({ include_selected_messages: "1" });
    if (selectedChatConversationId) {
      query.set("selected_conversation_id", selectedChatConversationId);
    }
    const response = await teamFetch(`${API_BASE}/views/chat?${query.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    if (requestId !== chatRequestId) {
      return payload;
    }
    if (loadGeneration !== teamViewCacheGeneration) {
      return payload;
    }
    const drafts = preserveForms && activeTeamView === "chat" ? captureFormDrafts() : null;
    applyViewPayload(payload);
    applyChatCollectionPayload(payload.data?.chat || {}, requestId, {
      selectedConversationId: selectedChatConversationId,
      preserveSelection: true,
    });
    loadedTeamViews.add("chat");
    if (drafts) {
      restoreFormDrafts(drafts);
    }
    return payload;
  } catch (error) {
    if (requestId !== chatRequestId) {
      return null;
    }
    chatState = {
      ...chatState,
      loading: false,
      error: error instanceof Error ? error.message : "Chat view failed to load.",
    };
    if (activeTeamView === "chat") {
      const drafts = preserveForms ? captureFormDrafts() : null;
      renderChat();
      if (drafts) {
        restoreFormDrafts(drafts);
      }
    }
    throw error;
  }
}

function applyViewPayload(payload) {
  const data = payload?.data || {};
  if (data.state) {
    applyStateSlice(data.state, payload.resource_tokens, {
      mergeCollections: payload?.view === "chat" ? new Set(["agents"]) : null,
    });
  } else if (payload?.revision !== undefined && payload?.revision !== null) {
    state = normalizeState({ ...state, revision: payload.revision });
  }
  if (Array.isArray(data.templates)) {
    templates = normalizeTemplates({ templates: data.templates });
  }
  if (data.timeline) {
    timelineState = { ...emptyTimelineState(), ...data.timeline };
    timelineLoading = false;
  }
}

function applyStateSlice(slice, envelopeResourceTokens = null, { mergeCollections = null } = {}) {
  const next = { ...state };
  for (const [key, value] of Object.entries(slice || {})) {
    if (teamStateCollectionKeys.includes(key) || key === "runtime_operations" || key === "agent_activity_summaries") {
      if (mergeCollections?.has(key) && value && typeof value === "object" && !Array.isArray(value)) {
        next[key] = { ...(next[key] || {}), ...value };
        continue;
      }
      next[key] = value;
    } else if (key === "resource_tokens") {
      next.resource_tokens = mergeResourceTokens(next.resource_tokens, value);
    } else {
      next[key] = value;
    }
  }
  if (envelopeResourceTokens) {
    next.resource_tokens = mergeResourceTokens(next.resource_tokens, envelopeResourceTokens);
  }
  state = normalizeState(next);
}

function mergeResourceTokens(existing, incoming) {
  const merged = { ...(existing || {}) };
  Object.entries(incoming || {}).forEach(([collection, values]) => {
    merged[collection] = { ...(merged[collection] || {}), ...(values || {}) };
  });
  return merged;
}

function scheduleAuxiliaryLoadsForView(view) {
  if (view === "mail") {
    void mailController.load();
  }
  if (view === "activity") {
    void activityController.load();
  }
  if (view !== "chat") {
    void loadCosts();
  }
}

function scheduleIdleViewPrefetch() {
  const prefetch = () => {
    if (activeTeamView !== "chat" || loadedTeamViews.has("chat")) {
      return;
    }
    void loadView("chat");
  };
  if (typeof window.requestIdleCallback === "function") {
    window.requestIdleCallback(prefetch, { timeout: 5000 });
    return;
  }
  window.setTimeout(prefetch, 1500);
}

async function loadState({ preserveForms = false } = {}) {
  const drafts = preserveForms ? captureFormDrafts() : null;
  setStatus("Loading team state.", "loading");
  timelineLoading = true;
  try {
    const [stateResponse, templatesResponse] = await Promise.all([
      teamFetch(`${API_BASE}/state`, {
        headers: { Accept: "application/json" },
      }),
      teamFetch(`${API_BASE}/templates`, {
        headers: { Accept: "application/json" },
      }),
    ]);
    if (!stateResponse.ok) {
      throw new Error(await responseText(stateResponse));
    }
    if (!templatesResponse.ok) {
      throw new Error(await responseText(templatesResponse));
    }
    state = normalizeState(await stateResponse.json());
    templates = normalizeTemplates(await templatesResponse.json());
    render();
    if (drafts) {
      restoreFormDrafts(drafts);
    } else {
      renderTemplatePreview();
    }
    setStatus("", "");
    void loadCosts();
    void loadConversations({ preserveSelection: true });
    void loadTimeline();
    ensureStateStream();
  } catch (error) {
    timelineLoading = false;
    setStatus(error instanceof Error ? error.message : "Team state failed to load.", "error");
  }
}

async function reloadActiveTeamView({ preserveForms = false } = {}) {
  invalidateLoadedTeamViews();
  await loadView(activeTeamView, { force: true, preserveForms });
}

function invalidateLoadedTeamViews() {
  teamViewCacheGeneration += 1;
  loadedTeamViews.clear();
}

function applyStateSnapshot(payload) {
  if (!payload || payload.revision === undefined || payload.revision === null) {
    return;
  }
  if (Number(payload.revision) <= Number(state.revision || 0)) {
    return;
  }
  const drafts = captureFormDrafts();
  state = normalizeState(payload);
  render();
  restoreFormDrafts(drafts);
  void loadCosts();
  void loadConversations({ preserveSelection: true });
  void loadTimeline();
}

function handleStateStreamSnapshot(payload) {
  if (!payload || payload.revision === undefined || payload.revision === null) {
    return;
  }
  if (payload.response_scope === "revision" || !payload.teams) {
    if (Number(payload.revision) <= Number(state.revision || 0)) {
      return;
    }
    state = normalizeState({ ...state, revision: payload.revision });
    teamViewCacheGeneration += 1;
    loadedTeamViews.clear();
    void loadView(activeTeamView, { force: true, preserveForms: true });
    return;
  }
  applyStateSnapshot(payload);
}

function ensureStateStream() {
  if (stateStream || typeof EventSource === "undefined") {
    return;
  }
  stateStreamErrorRefreshPending = false;
  const stream = new EventSource(`${API_BASE}/state/stream`);
  stateStream = stream;
  stream.addEventListener("snapshot", (event) => {
    if (stateStream !== stream) {
      return;
    }
    try {
      handleStateStreamSnapshot(JSON.parse(event.data || "{}"));
      stateStreamErrorRefreshPending = false;
    } catch {
      // Ignore malformed stream events; EventSource will keep the connection alive.
    }
  });
  stream.onerror = () => {
    if (stateStream !== stream || stateStreamErrorRefreshPending) {
      return;
    }
    stateStreamErrorRefreshPending = true;
    void loadView(activeTeamView, { force: true, preserveForms: true }).finally(() => {
      if (stateStream === stream) {
        stateStreamErrorRefreshPending = false;
      }
    });
  };
}

function closeStateStream() {
  if (stateStream) {
    stateStream.close();
  }
  stateStream = null;
  stateStreamErrorRefreshPending = false;
}

async function loadCosts() {
  const requestId = ++costsRequestId;
  try {
    const response = await teamFetch(`${API_BASE}/costs?backfill=false`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    if (requestId !== costsRequestId) {
      return;
    }
    const drafts = captureFormDrafts();
    costState = normalizeCosts(payload);
    render();
    restoreFormDrafts(drafts);
  } catch (error) {
    if (requestId !== costsRequestId) {
      return;
    }
    const drafts = captureFormDrafts();
    costState = normalizeCosts({
      ...emptyCostState(),
      error: error instanceof Error ? error.message : "Cost data failed to load.",
    });
    render();
    restoreFormDrafts(drafts);
  }
}

async function refreshCostBackfill() {
  if (costBackfillLoading) {
    return;
  }
  const requestId = ++costBackfillRequestId;
  let drafts = captureFormDrafts();
  costBackfillLoading = true;
  setStatus("Refreshing transcript costs.", "loading");
  render();
  restoreFormDrafts(drafts);
  try {
    const response = await teamFetch(`${API_BASE}/costs/backfill`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    if (requestId !== costBackfillRequestId) {
      return;
    }
    drafts = captureFormDrafts();
    costBackfillLoading = false;
    costState = normalizeCosts(payload);
    render();
    restoreFormDrafts(drafts);
    setStatus("", "");
  } catch (error) {
    if (requestId !== costBackfillRequestId) {
      return;
    }
    drafts = captureFormDrafts();
    costBackfillLoading = false;
    costState = normalizeCosts({
      ...costState,
      error: error instanceof Error ? error.message : "Cost transcript refresh failed.",
    });
    render();
    restoreFormDrafts(drafts);
    setStatus(error instanceof Error ? error.message : "Cost transcript refresh failed.", "error");
  }
}

function normalizeState(payload) {
  const normalized = { ...emptyState(), ...(payload || {}) };
  taskPreparationInvalidationByTaskId = indexTaskPreparationInvalidations(normalized.inbox);
  return normalized;
}

function indexTaskPreparationInvalidations(inbox) {
  const latestByTaskId = new Map();
  values(inbox).forEach((item) => {
    const taskId = text(item.task_id);
    if (!taskId || text(item.payload?.kind) !== "task_preparation_invalidated") {
      return;
    }
    const current = latestByTaskId.get(taskId);
    if (
      !current ||
      String(item.created_at || "") > String(current.created_at || "") ||
      (
        String(item.created_at || "") === String(current.created_at || "") &&
        byId(item, current) < 0
      )
    ) {
      latestByTaskId.set(taskId, item);
    }
  });
  return latestByTaskId;
}

function normalizeTemplates(payload) {
  return Array.isArray(payload?.templates) ? payload.templates : [];
}

function normalizeCosts(payload) {
  return { ...emptyCostState(), ...(payload || {}) };
}

function normalizeChat(payload) {
  const normalized = { ...emptyChatState(), ...(payload || {}) };
  normalized.eligible_agents = Array.isArray(normalized.eligible_agents)
    ? normalized.eligible_agents
    : [];
  normalized.operator_options = Array.isArray(normalized.operator_options)
    ? normalized.operator_options.map(normalizeChatOperatorOption).filter((item) => item.user_id)
    : [];
  normalized.current_operator = normalizeChatOperatorOption(normalized.current_operator);
  normalized.selected_operator = normalizeChatOperatorOption(normalized.selected_operator);
  normalized.can_view_all_operators = Boolean(normalized.can_view_all_operators);
  normalized.read_only = Boolean(normalized.read_only);
  normalized.conversations = Array.isArray(normalized.conversations)
    ? normalized.conversations.map(normalizeChatConversation)
    : [];
  return normalized;
}

function normalizeChatOperatorOption(operator) {
  const userId = text(operator?.user_id);
  const displayName = text(operator?.display_name) || userId;
  return {
    user_id: userId,
    display_name: displayName,
  };
}

function syncTeamStateFromChatPayload(payload) {
  if (!payload || payload.revision === undefined || payload.revision === null) {
    return;
  }
  const conversations = { ...(state.conversations || {}) };
  const conversationMessages = { ...(state.conversation_messages || {}) };
  const mergeConversation = (conversation) => {
    if (!conversation?.id) {
      return;
    }
    const { messages, agent: _agent, ...conversationPayload } = conversation;
    conversations[conversation.id] = {
      ...(conversations[conversation.id] || {}),
      ...conversationPayload,
    };
    if (Array.isArray(messages)) {
      messages.forEach((message) => {
        if (message?.id) {
          conversationMessages[message.id] = {
            ...(conversationMessages[message.id] || {}),
            ...message,
          };
        }
      });
    }
  };
  if (Array.isArray(payload.conversations)) {
    payload.conversations.forEach(mergeConversation);
  }
  mergeConversation(payload.conversation);
  const messagePayload = payload.message || payload.conversation_message;
  if (messagePayload?.id) {
    conversationMessages[messagePayload.id] = {
      ...(conversationMessages[messagePayload.id] || {}),
      ...messagePayload,
    };
  }
  state = normalizeState({
    ...state,
    revision: state.revision,
    conversations,
    conversation_messages: conversationMessages,
  });
}

function chatApiUrl(path, query = null) {
  const params = query instanceof URLSearchParams ? new URLSearchParams(query) : new URLSearchParams();
  const operatorUserId = text(selectedChatOperatorUserId);
  if (operatorUserId) {
    params.set("operator_user_id", operatorUserId);
  }
  const suffix = params.toString();
  return `${API_BASE}${path}${suffix ? `?${suffix}` : ""}`;
}

function chatScopeFields(payload) {
  return {
    current_operator: payload?.current_operator ?? chatState.current_operator,
    selected_operator: payload?.selected_operator ?? chatState.selected_operator,
    can_view_all_operators: payload?.can_view_all_operators ?? chatState.can_view_all_operators,
    read_only: payload?.read_only ?? chatState.read_only,
    operator_options: payload?.operator_options ?? chatState.operator_options,
  };
}

async function loadConversations({ selectedConversationId = null, preserveSelection = true } = {}) {
  const requestId = ++chatRequestId;
  const candidateId = selectedConversationId || (preserveSelection ? selectedChatConversationId : null);
  chatState = { ...chatState, loading: true, error: null };
  scheduleChatScrollForRender(candidateId);
  renderChat();
  try {
    const query = new URLSearchParams({ include_selected_messages: "1" });
    if (candidateId) {
      query.set("selected_conversation_id", candidateId);
    }
    const response = await teamFetch(chatApiUrl("/conversations", query), {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    if (requestId !== chatRequestId) {
      return;
    }
    applyChatCollectionPayload(payload, requestId, {
      selectedConversationId,
      preserveSelection,
      candidateId,
    });
    loadedTeamViews.add("chat");
  } catch (error) {
    if (requestId !== chatRequestId) {
      return;
    }
    chatState = {
      ...chatState,
      loading: false,
      error: error instanceof Error ? error.message : "Conversations failed to load.",
    };
    renderChat();
  }
}

function applyChatCollectionPayload(
  payload,
  requestId,
  { selectedConversationId = null, preserveSelection = true, candidateId = null } = {},
) {
  if (requestId !== chatRequestId) {
    return;
  }
  const previousSelectedConversationId = text(selectedChatConversationId);
  const previousConversations = new Map(
    chatState.conversations.map((conversation) => [text(conversation.id), conversation]),
  );
  rememberCurrentChatComposerDraft();
  chatState = normalizeChat(payload);
  maybePlayChatCollectionNotificationSound(previousConversations, chatState.conversations);
  selectedChatOperatorUserId = text(chatState.selected_operator?.user_id) || selectedChatOperatorUserId;
  syncStateAgentsFromChat(chatState);
  const responseCandidateId = selectedConversationId || (preserveSelection ? selectedChatConversationId : null);
  const fallbackCandidateId = candidateId || responseCandidateId;
  selectedChatConversationId =
    selectExistingConversationId(responseCandidateId) ||
    selectExistingConversationId(text(payload.selected_conversation_id) || fallbackCandidateId) ||
    firstConversationId();
  const selectedMessagesLoaded = chatConversationHasLoadedMessages(selectedChatConversationId);
  if (
    selectedMessagesLoaded &&
    (previousSelectedConversationId !== text(selectedChatConversationId) ||
      !chatMessageListElement(selectedChatConversationId))
  ) {
    scheduleChatScrollToBottom(selectedChatConversationId);
  }
  if (activeTeamView === "chat") {
    renderChat();
  }
  if (selectedChatConversationId) {
    if (chatStreamShouldBeOpen()) {
      ensureChatStream(selectedChatConversationId);
      if (!selectedMessagesLoaded) {
        void loadConversationDetail(selectedChatConversationId, requestId);
      }
    } else {
      closeChatStream();
    }
  } else {
    closeChatStream();
  }
}

function syncStateAgentsFromChat(chatPayload) {
  const agents = { ...(state.agents || {}) };
  (chatPayload.eligible_agents || []).forEach((agent) => {
    if (agent?.id) {
      agents[agent.id] = { ...(agents[agent.id] || {}), ...agent };
    }
  });
  (chatPayload.conversations || []).forEach((conversation) => {
    const agent = conversation?.agent;
    if (agent?.id) {
      agents[agent.id] = { ...(agents[agent.id] || {}), ...agent };
    }
  });
  state = normalizeState({ ...state, revision: chatPayload.revision ?? state.revision, agents });
}

async function loadConversationDetail(
  conversationId,
  requestId = chatRequestId,
  { preserveScroll = false } = {},
) {
  const normalizedConversationId = text(conversationId);
  if (!normalizedConversationId) {
    return;
  }
  try {
    const response = await teamFetch(chatApiUrl(`/conversations/${encodeURIComponent(normalizedConversationId)}`), {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    if (requestId !== chatRequestId) {
      return;
    }
    replaceChatConversation(payload.conversation);
    chatState = { ...chatState, loading: false, error: null, revision: payload.revision ?? chatState.revision };
    if (activeTeamView === "chat" && text(selectedChatConversationId) === normalizedConversationId) {
      markChatConversationRead(normalizedConversationId, { rerender: false });
    }
    const shouldStickToBottom =
      !preserveScroll || chatIsScrolledNearBottom(normalizedConversationId);
    const scrollSnapshot = shouldStickToBottom
      ? null
      : captureChatScroll(normalizedConversationId);
    if (shouldStickToBottom) {
      scheduleChatScrollToBottom(normalizedConversationId);
    } else if (scrollSnapshot) {
      scheduleChatScrollPreserve(normalizedConversationId, scrollSnapshot);
    }
    renderChat();
  } catch (error) {
    if (requestId !== chatRequestId) {
      return;
    }
    chatState = {
      ...chatState,
      loading: false,
      error: error instanceof Error ? error.message : "Conversation failed to load.",
    };
    renderChat();
  }
}

async function loadOlderChatMessages(conversationId) {
  const normalizedConversationId = text(conversationId);
  const conversation = chatConversationById(normalizedConversationId);
  if (!conversation || !conversation.has_older || chatOlderMessageLoads.has(normalizedConversationId)) {
    return;
  }
  const messages = conversationMessages(conversation);
  const beforeMessageId = text(conversation.oldest_message_id) || text(messages[0]?.id);
  if (!beforeMessageId) {
    return;
  }
  const scrollSnapshot = captureChatScroll(normalizedConversationId);
  chatOlderMessageLoads.add(normalizedConversationId);
  if (scrollSnapshot) {
    scheduleChatScrollPreserve(normalizedConversationId, scrollSnapshot);
  }
  renderChat();
  try {
    const query = new URLSearchParams({
      before: beforeMessageId,
      limit: String(chatMessagePageSize),
    });
    const response = await teamFetch(
      chatApiUrl(`/conversations/${encodeURIComponent(normalizedConversationId)}/messages`, query),
      { headers: { Accept: "application/json" } },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    if (text(payload.conversation_id) !== normalizedConversationId) {
      return;
    }
    applyChatMessagePage(normalizedConversationId, payload);
    chatState = { ...chatState, revision: payload.revision ?? chatState.revision };
    if (scrollSnapshot) {
      scheduleChatScrollPreservePrepend(normalizedConversationId, scrollSnapshot);
    }
  } catch (error) {
    chatState = {
      ...chatState,
      error: error instanceof Error ? error.message : "Older messages failed to load.",
    };
  } finally {
    chatOlderMessageLoads.delete(normalizedConversationId);
    renderChat();
  }
}

function selectExistingConversationId(conversationId) {
  const normalizedConversationId = text(conversationId);
  if (!normalizedConversationId) {
    return null;
  }
  return chatState.conversations.some((conversation) => text(conversation.id) === normalizedConversationId)
    ? normalizedConversationId
    : null;
}

function firstConversationId() {
  return text(chatState.conversations[0]?.id) || null;
}

function chatConversationHasLoadedMessages(conversationId) {
  const conversation = chatConversationById(conversationId);
  return conversation?.messages_loaded === true;
}

function replaceChatConversation(conversation) {
  if (!conversation?.id) {
    return;
  }
  const existing = chatConversationById(conversation.id);
  const nextConversation = mergeChatConversation(existing, conversation);
  const nextConversations = chatState.conversations.filter((item) => text(item.id) !== text(conversation.id));
  nextConversations.push(nextConversation);
  nextConversations.sort(compareChatConversations);
  chatState = { ...chatState, conversations: nextConversations };
}

function chatStreamShouldBeOpen() {
  return activeTeamView === "chat" && document.visibilityState !== "hidden";
}

function syncChatStreamForActiveView({ refresh = false } = {}) {
  if (!chatStreamShouldBeOpen()) {
    closeChatStream();
    return;
  }
  if (!selectedChatConversationId) {
    return;
  }
  ensureChatStream(selectedChatConversationId);
  if (refresh) {
    void loadConversationDetail(selectedChatConversationId, chatRequestId, { preserveScroll: true });
  }
}

function ensureChatStream(conversationId) {
  const normalizedConversationId = text(conversationId);
  if (!normalizedConversationId || typeof EventSource === "undefined") {
    return;
  }
  if (!chatStreamShouldBeOpen()) {
    closeChatStream();
    return;
  }
  if (chatStream && chatStreamConversationId === normalizedConversationId) {
    return;
  }
  closeChatStream();
  chatStreamConversationId = normalizedConversationId;
  chatStreamErrorRefreshPending = false;
  const stream = new EventSource(
    chatApiUrl(`/conversations/${encodeURIComponent(normalizedConversationId)}/stream`),
  );
  chatStream = stream;
  stream.addEventListener("snapshot", (event) => {
    if (chatStream !== stream || chatStreamConversationId !== normalizedConversationId) {
      return;
    }
    try {
      const payload = JSON.parse(event.data || "{}");
      applyChatSnapshot(payload, normalizedConversationId);
    } catch {
      // Ignore malformed stream events; EventSource will keep the connection alive.
    }
  });
  stream.onerror = () => {
    if (chatStream !== stream || chatStreamConversationId !== normalizedConversationId) {
      return;
    }
    if (chatStreamErrorRefreshPending) {
      return;
    }
    chatStreamErrorRefreshPending = true;
    void loadConversationDetail(normalizedConversationId, chatRequestId, { preserveScroll: true }).finally(() => {
      if (chatStream === stream) {
        chatStreamErrorRefreshPending = false;
      }
    });
  };
}

function closeChatStream() {
  if (chatStream) {
    chatStream.close();
  }
  chatStream = null;
  chatStreamConversationId = null;
  chatStreamErrorRefreshPending = false;
}

function applyChatSnapshot(payload, expectedConversationId) {
  const conversation = payload?.conversation;
  if (!conversation?.id || text(conversation.id) !== text(expectedConversationId)) {
    return;
  }
  const previousConversation = chatConversationById(expectedConversationId);
  chatState = normalizeChat({ ...chatState, ...chatScopeFields(payload) });
  const scrollSnapshot = captureChatScroll(expectedConversationId);
  const shouldStickToBottom =
    (pendingChatScroll?.mode === "bottom" &&
      text(pendingChatScroll.conversationId) === text(expectedConversationId)) ||
    chatIsScrolledNearBottom(expectedConversationId);
  syncTeamStateFromChatPayload(payload);
  replaceChatConversation(conversation);
  maybePlayChatNotificationSound(
    previousConversation,
    chatConversationById(expectedConversationId),
  );
  chatState = {
    ...chatState,
    loading: false,
    error: null,
    revision: payload.revision ?? chatState.revision,
  };
  if (activeTeamView === "chat" && text(selectedChatConversationId) === text(expectedConversationId)) {
    markChatConversationRead(expectedConversationId, { rerender: false });
  }
  if (shouldStickToBottom) {
    scheduleChatScrollToBottom(expectedConversationId);
  } else if (scrollSnapshot) {
    scheduleChatScrollPreserve(expectedConversationId, scrollSnapshot);
  }
  renderChat();
}

async function createOrFocusChat(agentId) {
  const normalizedAgentId = text(agentId);
  if (!normalizedAgentId) {
    return;
  }
  setStatus("Opening chat.", "loading");
  rememberCurrentChatComposerDraft();
  try {
    const response = await teamFetch(`${API_BASE}/conversations`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        agent_id: normalizedAgentId,
      }),
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    syncTeamStateFromChatPayload(payload);
    chatState = normalizeChat(payload);
    selectedChatOperatorUserId = text(chatState.selected_operator?.user_id) || selectedChatOperatorUserId;
    selectedChatConversationId = text(payload.selected_conversation_id) || firstConversationId();
    chatNewPickerOpen = false;
    chatMobileConversationListOpen = false;
    const selectedMessagesLoaded = chatConversationHasLoadedMessages(selectedChatConversationId);
    if (selectedMessagesLoaded) {
      scheduleChatScrollToBottom(selectedChatConversationId);
    }
    renderChat();
    if (selectedChatConversationId) {
      ensureChatStream(selectedChatConversationId);
      if (!selectedMessagesLoaded) {
        await loadConversationDetail(selectedChatConversationId);
      }
    }
    setStatus("", "");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Chat could not be opened.", "error");
  }
}

async function submitChatMessage(form) {
  const conversation = selectedChatConversation();
  if (!conversation || chatSending) {
    return;
  }
  if (chatState.read_only || conversation.read_only) {
    setStatus("This chat history is read-only.", "error");
    return;
  }
  const formData = new FormData(form);
  const body = text(formData.get("body"));
  const attachments = [...(pendingChatAttachments.get(text(conversation.id)) || [])];
  if (!body && !attachments.length) {
    setStatus("Message or attachment is required.", "error");
    return;
  }
  chatSending = true;
  scheduleChatScrollForRender(conversation.id);
  renderChat();
  setStatus("Sending chat message.", "loading");
  try {
    const conversationId = text(conversation.id);
    let requestBody = JSON.stringify({ body });
    const requestHeaders = { Accept: "application/json", "Content-Type": "application/json" };
    if (attachments.length) {
      requestBody = new FormData();
      requestBody.set("body", body);
      attachments.forEach((file) => requestBody.append("files", file, file.name));
      delete requestHeaders["Content-Type"];
    }
    const response = await teamFetch(`${API_BASE}/conversations/${encodeURIComponent(conversation.id)}/messages`, {
      method: "POST",
      headers: requestHeaders,
      body: requestBody,
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    syncTeamStateFromChatPayload(payload);
    chatState = normalizeChat({ ...chatState, ...chatScopeFields(payload) });
    selectedChatOperatorUserId = text(chatState.selected_operator?.user_id) || selectedChatOperatorUserId;
    replaceChatConversation(payload.conversation);
    selectedChatConversationId = payload.conversation?.id || selectedChatConversationId;
    chatState = { ...chatState, revision: payload.revision ?? chatState.revision };
    if (selectedChatConversationId) {
      ensureChatStream(selectedChatConversationId);
    }
    const activeForm = elements.chatThreadView?.querySelector("[data-chat-message-form]");
    const activeConversationId = text(
      activeForm?.closest(".chat-thread")?.querySelector("[data-chat-scroll-conversation-id]")
        ?.dataset?.chatScrollConversationId,
    );
    const activeComposer = activeForm?.querySelector('textarea[name="body"]');
    if (text(activeConversationId) === conversationId) {
      if (activeComposer instanceof HTMLTextAreaElement) {
        activeComposer.value = "";
      }
      pendingChatAttachments.delete(conversationId);
    }
    chatComposerDrafts.delete(conversationId);
    if (payload.dispatch?.success === false && !payload.dispatch?.queued) {
      setStatus(payload.dispatch.message || "Chat message saved, but dispatch failed.", "warning");
    } else {
      setStatus("", "");
    }
    if (selectedChatConversationId) {
      scheduleChatScrollToBottom(selectedChatConversationId);
    }
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Chat message failed.", "error");
  } finally {
    chatSending = false;
    renderChat();
  }
}

async function openChatAttachmentPolicy() {
  const form = elements.chatAttachmentPolicyForm;
  if (!form || !elements.chatAttachmentPolicyModal) return;
  const directoryPath = text(form.elements.directory_path?.value) || "root";
  setStatus("Loading Team Chat attachment policy.", "loading");
  try {
    const response = await teamFetch(
      `${API_BASE}/chat/attachment-policy?directory_path=${encodeURIComponent(directoryPath)}`,
      { headers: { Accept: "application/json" } },
    );
    if (!response.ok) throw new Error(await responseText(response));
    const payload = await response.json();
    fillChatAttachmentPolicyForm(form, payload.effective || {}, payload.directory_path || directoryPath);
    elements.chatAttachmentPolicyModal.hidden = false;
    form.elements.directory_path?.focus();
    setStatus("", "");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Attachment policy could not be loaded.", "error");
  }
}

function closeChatAttachmentPolicy() {
  if (elements.chatAttachmentPolicyModal) elements.chatAttachmentPolicyModal.hidden = true;
}

function fillChatAttachmentPolicyForm(form, policy, directoryPath) {
  form.elements.directory_path.value = directoryPath || "root";
  form.elements.operator_to_agent_enabled.checked = Boolean(policy.operator_to_agent_enabled);
  form.elements.agent_to_operator_enabled.checked = Boolean(policy.agent_to_operator_enabled);
  form.elements.agent_send_requires_approval.checked = Boolean(policy.agent_send_requires_approval);
  form.elements.max_attachment_bytes.value = policy.max_attachment_bytes ?? 10485760;
  form.elements.max_attachments_per_message.value = policy.max_attachments_per_message ?? 5;
  form.elements.allowed_mime_types.value = (policy.allowed_mime_types || []).join("\n");
  form.elements.allowed_extensions.value = (policy.allowed_extensions || []).join("\n");
  form.elements.blocked_extensions.value = (policy.blocked_extensions || []).join("\n");
}

function chatAttachmentPolicyList(value) {
  return String(value || "").split(/[\n,]+/).map(text).filter(Boolean);
}

async function submitChatAttachmentPolicy(form) {
  if (!guardTeamMutation("configure Team Chat attachments")) return;
  const directoryPath = text(form.elements.directory_path.value) || "root";
  const policy = {
    operator_to_agent_enabled: form.elements.operator_to_agent_enabled.checked,
    agent_to_operator_enabled: form.elements.agent_to_operator_enabled.checked,
    agent_send_requires_approval: form.elements.agent_send_requires_approval.checked,
    max_attachment_bytes: Number(form.elements.max_attachment_bytes.value),
    max_attachments_per_message: Number(form.elements.max_attachments_per_message.value),
    allowed_mime_types: chatAttachmentPolicyList(form.elements.allowed_mime_types.value),
    allowed_extensions: chatAttachmentPolicyList(form.elements.allowed_extensions.value),
    blocked_extensions: chatAttachmentPolicyList(form.elements.blocked_extensions.value),
  };
  setStatus("Saving Team Chat attachment policy.", "loading");
  try {
    const response = await teamFetch(`${API_BASE}/chat/attachment-policy`, {
      method: "PUT",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        directory_path: directoryPath,
        expected_revision: Number(chatState.revision ?? state.revision),
        policy,
      }),
    });
    if (!response.ok) throw new Error(await responseText(response));
    const payload = await response.json();
    state.revision = payload.revision ?? state.revision;
    chatState.revision = payload.revision ?? chatState.revision;
    fillChatAttachmentPolicyForm(form, payload.effective || policy, directoryPath);
    closeChatAttachmentPolicy();
    renderShell();
    setStatus("Team Chat attachment policy saved.", "success");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Attachment policy could not be saved.", "error");
  }
}

function renderShell() {
  elements.revisionBadge.textContent = `Revision ${state.revision}`;
  updateArchivedVisibilityToggle();
  updateGovernanceAgendaVisibilityToggle();
  updateTeamViewTabs();
  updatePlanningFilterControls();
  syncTeamsCreateTeamPanel();
  syncGovernanceCreateGroupPanel();
  applyLockedMutationControls();
}

function render() {
  rememberOpenTeamDisclosures();
  renderShell();
  renderActiveView(activeTeamView);
}

function renderActiveView(view = activeTeamView) {
  switch (normalizedTeamView(view)) {
    case "workspace":
      renderWorkspaceDashboard();
      decorateActiveViewTooltips();
      break;
    case "operations":
      renderRuntimeOperations();
      decorateActiveViewTooltips();
      break;
    case "delivery":
      renderReviewDeliveryWorkspace();
      renderReviewProfiles();
      renderReleases();
      decorateActiveViewTooltips();
      break;
    case "memory":
      renderMemoryView();
      decorateActiveViewTooltips();
      break;
    case "projects":
      renderProjectFocus();
      refreshActiveViewSelects();
      decorateActiveViewTooltips();
      break;
    case "task-board":
      renderTaskBoard();
      decorateActiveViewTooltips();
      break;
    case "cases":
      renderCases();
      decorateActiveViewTooltips();
      break;
    case "people":
      renderAgents();
      refreshActiveViewSelects();
      decorateActiveViewTooltips();
      break;
    case "chat":
      renderChat();
      decorateActiveViewTooltips();
      break;
    case "mail":
      mailController.render();
      decorateActiveViewTooltips();
      break;
    case "governance":
      renderGovernance();
      refreshActiveViewSelects();
      decorateActiveViewTooltips();
      break;
    case "planning":
      renderProjects();
      updatePlanningFilterControls();
      decorateActiveViewTooltips();
      break;
    case "timeline":
      refreshActiveViewSelects();
      renderTimeline();
      decorateActiveViewTooltips();
      break;
    case "org":
      renderFocusedOrgChartIfActive();
      decorateActiveViewTooltips();
      break;
    default:
      break;
  }
  applyLockedMutationControls();
}

function renderActiveViewLoading(view = activeTeamView) {
  const drafts = captureFormDrafts();
  const panel = document.querySelector(`[data-team-view-panel="${normalizedTeamView(view)}"]`);
  const target =
    panel?.querySelector("[data-chat-thread-view]") ||
    panel?.querySelector("[data-mail-list]") ||
    panel?.querySelector("[data-agent-detail-view]") ||
    panel?.querySelector("[data-projects-focus-detail]") ||
    panel?.querySelector("[data-workspace-attention-column]") ||
    panel?.querySelector("[data-runtime-operations-view]") ||
    panel?.querySelector("[data-review-delivery-view]") ||
    panel?.querySelector("[data-memory-view]") ||
    panel?.querySelector("[data-projects-view]") ||
    panel?.querySelector("[data-cases-view]") ||
    panel?.querySelector("[data-governance-view]") ||
    panel?.querySelector("[data-timeline-view]") ||
    panel?.querySelector("#org-focus-view");
  if (target) {
    target.innerHTML = `<p class="empty-state">Loading ${escapeHtml(humanizeToken(view))}.</p>`;
  }
  restoreFormDrafts(drafts);
}

function activeViewPanel() {
  return document.querySelector(`[data-team-view-panel="${activeTeamView}"]`);
}

function refreshActiveViewSelects() {
  const panel = activeViewPanel();
  if (panel) {
    fillSelectsIn(panel);
  }
}

function decorateActiveViewTooltips() {
  const panel = activeViewPanel();
  if (panel) {
    decorateFieldTooltips(panel);
  }
}

function renderFocusedOrgChartIfActive() {
  if (activeTeamView === "org") {
    renderFocusedOrgChart();
    return;
  }
  if (elements.orgFocusSummary) {
    elements.orgFocusSummary.innerHTML = "";
  }
  if (elements.orgFocusTeamTabs) {
    elements.orgFocusTeamTabs.innerHTML = "";
  }
  if (elements.orgFocusView) {
    elements.orgFocusView.innerHTML = "";
  }
}

function setDeliveryFilter(filter) {
  const nextFilter = deliveryFilterOptions.has(filter) ? filter : "attention";
  if (selectedDeliveryFilter === nextFilter) {
    updateDeliveryFilterButtons();
    return;
  }
  const drafts = captureFormDrafts();
  selectedDeliveryFilter = nextFilter;
  renderReviewDeliveryWorkspace();
  restoreFormDrafts(drafts);
}

function updateDeliveryFilterButtons() {
  document.querySelectorAll(".delivery-filter").forEach((button) => {
    if (button.matches("[data-my-human-reviews]")) {
      return;
    }
    const active = (button.dataset.filter || "all") === selectedDeliveryFilter;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  updateMyHumanReviewsFilterButton();
}

function updateMyHumanReviewsFilterButton() {
  const button = document.querySelector("[data-my-human-reviews]");
  if (!button) {
    return;
  }
  button.classList.toggle("is-active", myHumanReviewsOnly);
  button.setAttribute("aria-pressed", myHumanReviewsOnly ? "true" : "false");
  const count = humanReviewQueue.loaded ? humanReviewQueue.requests.length : null;
  button.textContent =
    count === null ? "My Human Reviews" : `My Human Reviews (${count})`;
}

function renderReviewDeliveryWorkspace() {
  if (!document.querySelector("[data-review-delivery-view]")) {
    return;
  }
  updateDeliveryFilterButtons();
  const workspaceRows = buildReviewDeliveryRows();
  const statusRows =
    selectedDeliveryFilter === "all"
      ? workspaceRows
      : selectedDeliveryFilter === "attention"
        ? workspaceRows.filter(deliveryRowNeedsAttention)
        : workspaceRows.filter((row) => row.effectiveStatus === selectedDeliveryFilter);
  const visibleRows = selectedDeliveryProjectId
    ? statusRows.filter((row) => text(row.project?.id) === selectedDeliveryProjectId)
    : statusRows;
  const controls = renderDeliveryWorkspaceControls(workspaceRows, statusRows, visibleRows);

  if (!visibleRows.length) {
    renderIntoActiveTargets("[data-review-delivery-view]", `
      ${controls}
      <p class="empty-state">
        No deliverables match the selected project and ${escapeHtml(deliveryStatusLabels[selectedDeliveryFilter] || selectedDeliveryFilter)} filter.
      </p>
    `);
    bindWorkspaceReviewDecisionForms();
    return;
  }

  const teamGroups = groupReviewDeliveryRows(visibleRows);
  const bucketStatuses =
    ["all", "attention"].includes(selectedDeliveryFilter)
      ? deliveryStatusOrder
      : [selectedDeliveryFilter];

  renderIntoActiveTargets(
    "[data-review-delivery-view]",
    `
      ${controls}
      <div class="delivery-focused-results">
        ${teamGroups.map((teamGroup) => renderDeliveryTeamGroup(teamGroup, bucketStatuses)).join("")}
      </div>
    `,
  );
  bindWorkspaceReviewDecisionForms();
}

function deliveryRowNeedsAttention(row) {
  if (!isCurrentDeliverable(row.deliverable)) {
    return false;
  }
  if (text(row.deliverable?.initial_release_disposition?.state) === "pending") {
    return true;
  }
  if (
    currentDeliveryReleasePackages(row.releasePackages).some(
      (release) => !["accepted", "cancelled"].includes(text(release.state)),
    )
  ) {
    return true;
  }
  if (
    ["accepted", "cancelled"].includes(text(row.task?.state)) &&
    ["rejected", "blocked"].includes(row.effectiveStatus)
  ) {
    return false;
  }
  return row.effectiveStatus !== "accepted";
}

function renderDeliveryWorkspaceControls(allRows, statusRows, visibleRows) {
  const projects = new Map();
  allRows.forEach((row) => {
    const projectId = text(row.project?.id);
    if (!projectId) {
      return;
    }
    let entry = projects.get(projectId);
    if (!entry) {
      entry = { project: row.project, rows: [], total: 0, attention: 0 };
      projects.set(projectId, entry);
    }
    entry.rows.push(row);
    entry.total += 1;
    if (deliveryRowNeedsAttention(row)) {
      entry.attention += 1;
    }
  });
  if (selectedDeliveryProjectId && !projects.has(selectedDeliveryProjectId)) {
    selectedDeliveryProjectId = "";
  }
  const projectOptions = [...projects.entries()]
    .sort((left, right) => projectSortKey(left[1].project).localeCompare(projectSortKey(right[1].project)))
    .map(([projectId, entry]) => {
      const count =
        selectedDeliveryFilter === "attention"
          ? entry.attention
          : selectedDeliveryFilter === "all"
            ? entry.total
            : entry.rows.filter((row) => row.effectiveStatus === selectedDeliveryFilter).length;
      const countLabel = `${count} matching`;
      return `<option value="${escapeAttr(projectId)}"${selectedDeliveryProjectId === projectId ? " selected" : ""}>${escapeHtml(entry.project?.name || projectId)} (${escapeHtml(countLabel)})</option>`;
    })
    .join("");
  const activeCount = visibleRows.filter(deliveryRowNeedsAttention).length;
  return `
    <div class="delivery-focus-bar">
      <label class="delivery-project-filter">
        <span>Focus project</span>
        <select data-delivery-project-filter>
          <option value="">All projects (collapsed)</option>
          ${projectOptions}
        </select>
      </label>
      <div class="delivery-focus-summary" role="status">
        <strong>${visibleRows.length} matching deliverable${visibleRows.length === 1 ? "" : "s"}</strong>
        <span>${activeCount} still need attention. ${selectedDeliveryProjectId ? "The selected project is expanded." : "Open a project to inspect its deliverables."}</span>
      </div>
    </div>
  `;
}

function renderMemoryView() {
  const allMemories = values(state.memories).sort(compareMemories);
  const visibleMemories = allMemories.filter(isVisibleMemory);
  if (!allMemories.length) {
    renderIntoActiveTargets("[data-memory-view]", `<p class="empty-state">No Team memories recorded.</p>`);
    return;
  }
  if (!visibleMemories.length) {
    renderIntoActiveTargets("[data-memory-view]", `<p class="empty-state">No visible Team memories.</p>`);
    return;
  }
  renderIntoActiveTargets(
    "[data-memory-view]",
    groupMemoriesByTeam(visibleMemories).map(renderMemoryTeamGroup).join(""),
  );
}

function groupMemoriesByTeam(memories) {
  const groups = new Map();
  memories.forEach((memory) => {
    const teamId = text(memory.team_id) || "";
    if (!groups.has(teamId)) {
      groups.set(teamId, []);
    }
    groups.get(teamId).push(memory);
  });
  return [...groups.entries()]
    .map(([teamId, items]) => ({ teamId, team: state.teams[teamId] || null, memories: items }))
    .sort((left, right) => {
      const leftLabel = left.team?.name || left.teamId;
      const rightLabel = right.team?.name || right.teamId;
      return leftLabel.localeCompare(rightLabel) || left.teamId.localeCompare(right.teamId);
    });
}

function renderMemoryTeamGroup(group) {
  const activeCount = group.memories.filter((memory) => text(memory.status || "active") === "active").length;
  return `
    <article class="project-block">
      <div class="project-header">
        <div>
          <strong>${escapeHtml(group.team?.name || group.teamId || "Unknown team")}</strong>
          <div class="meta-line">${escapeHtml(group.teamId || "team missing")} - ${activeCount} active memory ${activeCount === 1 ? "record" : "records"}</div>
        </div>
      </div>
      <ul class="task-list">
        ${group.memories.map(renderMemoryItem).join("")}
      </ul>
    </article>
  `;
}

function renderMemoryItem(memory) {
  const project = memory.project_id ? state.projects[memory.project_id] : null;
  const agent = memory.agent_id ? state.agents[memory.agent_id] : null;
  const metaParts = [
    memory.id,
    humanizeToken(memory.kind || "other"),
    humanizeToken(memory.scope || "team_global"),
    project ? project.name : memory.project_id ? `project ${memory.project_id}` : "Team-wide",
    memory.codebase_id ? `codebase ${memory.codebase_id}` : "",
    agent ? `agent ${agentLabel(agent)}` : memory.agent_id ? `agent ${memory.agent_id}` : "",
  ].filter(Boolean);
  const refs = [
    uniqueTextList(memory.source_refs).length ? `${uniqueTextList(memory.source_refs).length} source ref(s)` : "",
    uniqueTextList(memory.artifact_refs).length ? `${uniqueTextList(memory.artifact_refs).length} artifact ref(s)` : "",
  ].filter(Boolean);
  return `
    <li class="task-item">
      <div class="task-line">
        <strong>${escapeHtml(memory.title || memory.id)}</strong>
        <div class="record-actions">
          <span class="state-pill ${escapeHtml(memory.status || "active")}">${escapeHtml(humanizeToken(memory.status || "active"))}</span>
        </div>
      </div>
      <div class="meta-line">${escapeHtml(metaParts.join(" - "))}</div>
      ${memory.summary ? `<div class="meta-line">${escapeHtml(memory.summary)}</div>` : ""}
      ${renderMemoryTags(memory)}
      ${refs.length ? `<div class="meta-line">${escapeHtml(refs.join(" - "))}</div>` : ""}
      ${memory.updated_at ? `<div class="meta-line">Updated ${escapeHtml(memory.updated_at)}</div>` : ""}
    </li>
  `;
}

function renderMemoryTags(memory) {
  const tags = uniqueTextList(memory.tags);
  if (!tags.length) {
    return "";
  }
  return `
    <ul class="inline-list">
      ${tags.map((tag) => `<li><span class="state-pill neutral">${escapeHtml(tag)}</span></li>`).join("")}
    </ul>
  `;
}

function isVisibleMemory(memory) {
  if (!showArchived && text(memory.status || "active") === "archived") {
    return false;
  }
  const team = memory.team_id ? state.teams[memory.team_id] : null;
  if (team && !isVisibleTeam(team)) {
    return false;
  }
  const project = memory.project_id ? state.projects[memory.project_id] : null;
  if (project && !isVisibleProject(project)) {
    return false;
  }
  return true;
}

function compareMemories(left, right) {
  return (
    memoryStatusRank(left.status) - memoryStatusRank(right.status) ||
    String(right.updated_at || "").localeCompare(String(left.updated_at || "")) ||
    byId(left, right)
  );
}

function memoryStatusRank(status) {
  const order = {
    active: 0,
    superseded: 1,
    archived: 2,
  };
  return order[text(status || "active")] ?? 99;
}

function renderWorkspaceDashboard() {
  const panel = document.querySelector("#team-workspace-page");
  if (!panel || panel.hidden) {
    return;
  }
  const model = buildWorkspaceDashboardModel();
  const subtitle = panel.querySelector("[data-workspace-dashboard-subtitle]");
  if (subtitle) {
    subtitle.textContent = workspaceDashboardSubtitle(model);
  }
  renderWorkspaceTarget(panel, "[data-workspace-dashboard-actions]", renderWorkspaceDashboardActions());
  renderWorkspaceTarget(panel, "[data-workspace-dashboard-metrics]", renderWorkspaceMetricCards(model));
  renderWorkspaceTarget(
    panel,
    "[data-workspace-attention-column]",
    renderWorkspaceAttentionPanel(model),
  );
  renderWorkspaceTarget(
    panel,
    "[data-workspace-status-column]",
    [
      renderWorkspaceTeamStatusPanel(model),
      renderWorkspaceWorkStatusPanel(model),
      renderWorkspaceDeliveryPanel(model),
    ].join(""),
  );
}

function renderWorkspaceTarget(panel, selector, html) {
  const target = panel.querySelector(selector);
  if (target) {
    rememberOpenTeamDisclosures();
    target.innerHTML = html;
  }
}

function buildWorkspaceDashboardModel() {
  const visibleTeams = values(state.teams).filter(isVisibleTeam).sort(comparePeopleTeams);
  const visibleTeamIds = new Set(visibleTeams.map((team) => text(team.id)));
  const visibleProjects = values(state.projects)
    .filter(isVisibleProject)
    .filter((project) => !project.team_id || visibleTeamIds.has(text(project.team_id)))
    .sort(compareTeamProjects);
  const visibleProjectIds = new Set(visibleProjects.map((project) => text(project.id)));
  const visibleAgents = values(state.agents)
    .filter(isVisibleAgent)
    .filter((agent) => !agent.team_id || visibleTeamIds.has(text(agent.team_id)))
    .sort(comparePeopleAgents);
  const visibleAgentIds = new Set(visibleAgents.map((agent) => text(agent.id)));
  const openTasks = values(state.tasks)
    .filter((task) => !terminalTaskStates.has(task.state))
    .filter((task) => workspaceTaskIsVisible(task, visibleProjectIds))
    .sort(comparePlanningTasks);
  const blockedTasks = openTasks.filter(isTaskWaitingOrBlocked);
  const runtime = state.runtime_operations || { agents: [], attention: [] };
  const runtimeInboxIds = new Set(
    (runtime.attention || []).map((row) => text(row.inbox_item_id)).filter(Boolean),
  );
  const pendingInbox = values(state.inbox)
    .filter((item) => text(item.state) !== "handled")
    .filter((item) => !runtimeInboxIds.has(text(item.id)))
    .filter((item) => visibleAgentIds.has(text(item.recipient_agent_id)))
    .sort(compareWorkspaceInboxItems);
  const attentionInbox = pendingInbox.filter(workspaceInboxItemNeedsAttention);
  const pendingReviews = values(state.review_requests)
    .filter((review) => !terminalRecordStates.has(review.state))
    .filter(workspaceReviewNeedsAttention)
    .filter((review) => workspaceReviewIsVisible(review, visibleProjectIds))
    .sort(compareWorkspaceReviews);
  const pendingGovernanceAgenda = values(state.governance_agenda)
    .filter((item) => text(item.state || "pending") === "pending")
    .filter((item) => workspaceGovernanceAgendaIsVisible(item, visibleTeamIds, visibleProjectIds))
    .sort(compareWorkspaceGovernanceAgenda);
  const runtimeAttention = (runtime.attention || [])
    .filter(isVisibleRuntimeRow)
    .filter((row) => runtimeAttentionGroup(row) !== "retained_history")
    .filter(workspaceRuntimeAttentionNeedsAttention)
    .sort(runtimeSortRows);
  const dueAgents = dueLeasedAgentsForFilter(runtime.agents || []);
  const deliveryRows = buildReviewDeliveryRows();
  const costAttention = workspaceCostAttentionRows(visibleTeamIds, visibleProjectIds);
  const attentionItems = workspaceAttentionItems({
    runtimeAttention,
    costAttention,
    blockedTasks,
    pendingReviews,
    pendingInbox: attentionInbox,
    pendingGovernanceAgenda,
  });
  return {
    visibleTeams,
    visibleProjects,
    visibleAgents,
    openTasks,
    blockedTasks,
    pendingInbox,
    attentionInbox,
    pendingReviews,
    pendingGovernanceAgenda,
    runtimeAttention,
    dueAgents,
    deliveryRows,
    costAttention,
    attentionItems,
    generatedAt: runtime.generated_at || null,
  };
}

function workspaceDashboardSubtitle(model) {
  const attention = model.attentionItems.length;
  if (attention) {
    return `${attention} item${attention === 1 ? "" : "s"} need attention.`;
  }
  if (!model.visibleTeams.length) {
    return "No active teams are registered.";
  }
  return "No active blockers detected in the current Team state.";
}

function renderWorkspaceDashboardActions() {
  return [
    workspaceViewButton("operations", "Operations"),
    workspaceViewButton("delivery", "Delivery"),
    workspaceViewButton("task-board", "Task board"),
    workspaceViewButton("people", "Teams"),
  ].join("");
}

function renderWorkspaceMetricCards(model) {
  const metrics = [
    {
      label: "Needs attention",
      value: model.attentionItems.length,
      detail: "Blockers, runtime alerts, pending reviews",
      view: "operations",
      tone: model.attentionItems.some((item) => item.severity === "critical") ? "critical" : model.attentionItems.length ? "warning" : "ok",
    },
    {
      label: "Blocked / waiting",
      value: model.blockedTasks.length,
      detail: `${model.openTasks.length} open task${model.openTasks.length === 1 ? "" : "s"}`,
      view: "task-board",
      tone: model.blockedTasks.length ? "warning" : "ok",
    },
    {
      label: "Pending reviews",
      value: model.pendingReviews.length,
      detail: `${model.deliveryRows.length} deliverable${model.deliveryRows.length === 1 ? "" : "s"}`,
      view: "delivery",
      tone: model.pendingReviews.length ? "warning" : "ok",
    },
    {
      label: "Due / leased agents",
      value: model.dueAgents.length,
      detail: `${model.visibleAgents.length} visible agent${model.visibleAgents.length === 1 ? "" : "s"}`,
      view: "operations",
      tone: model.dueAgents.length ? "warning" : "ok",
    },
    {
      label: "Active teams",
      value: model.visibleTeams.length,
      detail: `${model.visibleProjects.length} project${model.visibleProjects.length === 1 ? "" : "s"}`,
      view: "people",
      tone: model.visibleTeams.length ? "info" : "warning",
    },
    {
      label: "Cost alerts",
      value: model.costAttention.length,
      detail: costState.error ? "cost data unavailable" : "team caps and portfolio roll-ups",
      view: "governance",
      tone: model.costAttention.length || costState.error ? "warning" : "ok",
    },
  ];
  return metrics.map(renderWorkspaceMetricCard).join("");
}

function renderWorkspaceMetricCard(metric) {
  return `
    <button
      type="button"
      class="workspace-metric-card workspace-tone-${escapeAttr(metric.tone || "info")}"
      data-team-link-view="${escapeAttr(metric.view)}"
    >
      <span>${escapeHtml(metric.label)}</span>
      <strong>${escapeHtml(metric.value)}</strong>
      <small>${escapeHtml(metric.detail)}</small>
    </button>
  `;
}

function renderWorkspaceAttentionPanel(model) {
  const total = model.attentionItems.length;
  const pageCount = Math.max(1, Math.ceil(total / workspaceAttentionPageSize));
  const page = Math.min(workspaceAttentionPage, pageCount);
  workspaceAttentionPage = page;
  const offset = (page - 1) * workspaceAttentionPageSize;
  const visibleItems = model.attentionItems.slice(offset, offset + workspaceAttentionPageSize);
  const body = visibleItems.length
    ? `
      <ul class="workspace-attention-list">
        ${visibleItems.map(renderWorkspaceAttentionItem).join("")}
      </ul>
      ${renderWorkspaceAttentionPagination(total, page, pageCount)}
    `
    : `<p class="empty-state">No runtime blockers, pending reviews, actionable inbox items, or governance agenda items need attention.</p>`;
  return `
    <section class="workspace-dashboard-panel workspace-attention-panel" aria-labelledby="workspace-attention-heading">
      <div class="workspace-panel-header">
        <div>
          <p class="section-label">Attention</p>
          <h3 id="workspace-attention-heading">Needs attention now</h3>
        </div>
        ${workspaceViewButton("operations", "Open operations")}
      </div>
      ${body}
      ${model.generatedAt ? `<div class="meta-line">Runtime generated ${escapeHtml(model.generatedAt)}</div>` : ""}
    </section>
  `;
}

function renderWorkspaceAttentionPagination(total, page, pageCount) {
  if (pageCount <= 1) {
    return "";
  }
  const first = (page - 1) * workspaceAttentionPageSize + 1;
  const last = Math.min(total, page * workspaceAttentionPageSize);
  return `
    <nav class="workspace-attention-pagination" aria-label="Workspace attention pages">
      <span>Showing ${first}-${last} of ${total}</span>
      <div class="record-actions">
        <button type="button" class="record-action secondary" data-workspace-attention-page="${page - 1}"${page <= 1 ? " disabled" : ""}>Previous</button>
        <span>Page ${page} of ${pageCount}</span>
        <button type="button" class="record-action secondary" data-workspace-attention-page="${page + 1}"${page >= pageCount ? " disabled" : ""}>Next</button>
      </div>
    </nav>
  `;
}

function renderWorkspaceAttentionItem(item) {
  return `
    <li class="workspace-attention-item workspace-tone-${escapeAttr(item.severity || "info")}">
      <div class="workspace-attention-copy">
        <strong>${escapeHtml(item.title)}</strong>
        ${item.meta ? `<span>${escapeHtml(item.meta)}</span>` : ""}
      </div>
      <button type="button" class="record-action" data-team-link-view="${escapeAttr(item.view)}">${escapeHtml(item.action)}</button>
    </li>
  `;
}

function renderWorkspaceDeliveryPanel(model) {
  const counts = workspaceDeliveryCounts(model.deliveryRows);
  const total = model.deliveryRows.length;
  return `
    <section class="workspace-dashboard-panel" aria-labelledby="workspace-delivery-heading">
      <div class="workspace-panel-header">
        <div>
          <p class="section-label">Delivery</p>
          <h3 id="workspace-delivery-heading">Review &amp; delivery</h3>
        </div>
        ${workspaceViewButton("delivery", "Open delivery")}
      </div>
      ${
        total
          ? `
            <div class="workspace-status-strip">
              ${deliveryStatusOrder
                .map((status) => renderWorkspaceStatusCount(deliveryStatusLabels[status] || status, counts[status] || 0, status))
                .join("")}
            </div>
          `
          : `<p class="empty-state">No deliverables recorded.</p>`
      }
    </section>
  `;
}

function renderWorkspaceTeamStatusPanel(model) {
  const teamRows = model.visibleTeams.slice(0, 4);
  const remaining = Math.max(0, model.visibleTeams.length - teamRows.length);
  return `
    <section class="workspace-dashboard-panel" aria-labelledby="workspace-team-status-heading">
      <div class="workspace-panel-header">
        <div>
          <p class="section-label">Teams</p>
          <h3 id="workspace-team-status-heading">Team status</h3>
        </div>
        ${workspaceViewButton("people", "Open teams")}
      </div>
      ${
        teamRows.length
          ? `
            <ul class="workspace-team-list">
              ${teamRows.map(renderWorkspaceTeamStatusRow).join("")}
            </ul>
            ${remaining ? `<div class="workspace-more-row">${remaining} more team${remaining === 1 ? "" : "s"}.</div>` : ""}
          `
          : `<p class="empty-state">No active team.</p>`
      }
    </section>
  `;
}

function renderWorkspaceTeamStatusRow(team) {
  const teamAgents = values(state.agents).filter((agent) => isVisibleAgent(agent) && text(agent.team_id) === text(team.id));
  const teamProjectIds = new Set(
    values(state.projects)
      .filter((project) => isVisibleProject(project) && text(project.team_id) === text(team.id))
      .map((project) => text(project.id)),
  );
  const teamTasks = values(state.tasks).filter((task) => teamProjectIds.has(text(task.project_id)));
  const openTasks = teamTasks.filter((task) => !terminalTaskStates.has(task.state));
  const blockedTasks = openTasks.filter(isTaskWaitingOrBlocked);
  const inboxCount = values(state.inbox).filter((item) => {
    const agent = state.agents[text(item.recipient_agent_id)];
    return (
      text(item.state) !== "handled" &&
      pendingInboxItemIsCurrent(item) &&
      agent &&
      text(agent.team_id) === text(team.id)
    );
  }).length;
  const dueCount = (state.runtime_operations?.agents || []).filter((row) => {
    return text(row.team_id) === text(team.id) && isDueOrLeasedAgent(row);
  }).length;
  const attention = blockedTasks.length + inboxCount + dueCount;
  return `
    <li class="workspace-team-row${attention ? " workspace-team-row-attention" : ""}">
      <div>
        <strong>${escapeHtml(team.name || team.id)}</strong>
        <span>${escapeHtml(team.id)}</span>
      </div>
      <div class="workspace-team-row-meta">
        <span>${teamAgents.length} agent${teamAgents.length === 1 ? "" : "s"}</span>
        <span>${openTasks.length} open</span>
        ${blockedTasks.length ? `<span class="state-pill blocked">${blockedTasks.length} blocked</span>` : ""}
        ${inboxCount ? `<span class="state-pill warning">${inboxCount} inbox</span>` : ""}
        ${dueCount ? `<span class="state-pill warning">${dueCount} due</span>` : ""}
      </div>
    </li>
  `;
}

function renderWorkspaceWorkStatusPanel(model) {
  const openCases = values(state.cases).filter(
    (item) =>
      isVisibleCase(item) &&
      activeCaseStatuses.has(text(item.status) || "open"),
  );
  const pendingGovernance = model.pendingGovernanceAgenda.length;
  return `
    <section class="workspace-dashboard-panel" aria-labelledby="workspace-work-status-heading">
      <div class="workspace-panel-header">
        <div>
          <p class="section-label">Work</p>
          <h3 id="workspace-work-status-heading">Current workload</h3>
        </div>
        ${workspaceViewButton("task-board", "Open board")}
      </div>
      <div class="workspace-status-strip workspace-status-strip-compact">
        ${renderWorkspaceStatusCount("Open tasks", model.openTasks.length, model.blockedTasks.length ? "blocked" : "info")}
        ${renderWorkspaceStatusCount("Open cases", openCases.length, openCases.length ? "warning" : "info")}
        ${renderWorkspaceStatusCount("Inbox", model.pendingInbox.length, model.pendingInbox.length ? "warning" : "info")}
        ${renderWorkspaceStatusCount("Governance", pendingGovernance, pendingGovernance ? "warning" : "info")}
      </div>
    </section>
  `;
}

function renderWorkspaceStatusCount(label, value, tone = "info") {
  return `
    <div class="workspace-status-count workspace-tone-${escapeAttr(tone || "info")}">
      <strong>${escapeHtml(value)}</strong>
      <span>${escapeHtml(label)}</span>
    </div>
  `;
}

function workspaceViewButton(view, label) {
  return `<button type="button" class="workspace-view-link" data-team-link-view="${escapeAttr(view)}">${escapeHtml(label)}</button>`;
}

function workspaceDeliveryCounts(rows) {
  return rows.reduce((counts, row) => {
    counts[row.effectiveStatus] = (counts[row.effectiveStatus] || 0) + 1;
    return counts;
  }, {});
}

function workspaceTaskIsVisible(task, visibleProjectIds) {
  const projectId = text(task.project_id);
  return !projectId || visibleProjectIds.has(projectId);
}

function workspaceReviewIsVisible(review, visibleProjectIds) {
  const deliverable = state.deliverables[text(review.deliverable_id)];
  const task = deliverable ? state.tasks[text(deliverable.task_id)] : null;
  return !task || workspaceTaskIsVisible(task, visibleProjectIds);
}

function isCurrentDeliverable(deliverable) {
  return !text(deliverable?.superseded_by_deliverable_id);
}

function isCurrentReviewRequest(review) {
  return !review?.retired_at && !text(review?.superseded_by_review_request_id);
}

function workspaceReviewNeedsAttention(review) {
  const deliverable = state.deliverables?.[text(review.deliverable_id)];
  return isCurrentReviewRequest(review) && isCurrentDeliverable(deliverable);
}

function workspaceGovernanceAgendaIsVisible(item, visibleTeamIds, visibleProjectIds) {
  const group = state.governance_groups[text(item.governance_group_id)];
  if (group && !isVisibleGovernanceGroup(group)) {
    return false;
  }
  const teamId = text(item.team_id);
  const projectId = text(item.project_id);
  return (!teamId || visibleTeamIds.has(teamId)) && (!projectId || visibleProjectIds.has(projectId));
}

function compareWorkspaceInboxItems(left, right) {
  return (
    prioritySortValue(left.priority) - prioritySortValue(right.priority) ||
    String(left.created_at || "").localeCompare(String(right.created_at || "")) ||
    byId(left, right)
  );
}

function compareWorkspaceReviews(left, right) {
  return (
    recordStateAttentionRank(left.state) - recordStateAttentionRank(right.state) ||
    String(left.created_at || "").localeCompare(String(right.created_at || "")) ||
    byId(left, right)
  );
}

function compareWorkspaceGovernanceAgenda(left, right) {
  return (
    prioritySortValue(left.priority) - prioritySortValue(right.priority) ||
    String(left.created_at || "").localeCompare(String(right.created_at || "")) ||
    byId(left, right)
  );
}

function recordStateAttentionRank(stateValue) {
  const order = { blocked: 0, rejected: 1, pending: 2, in_progress: 3, accepted: 4 };
  return order[text(stateValue)] ?? 9;
}

function workspaceCostAttentionRows(visibleTeamIds, visibleProjectIds) {
  const rows = [];
  Object.entries(costState.team_costs || {}).forEach(([teamId, cost]) => {
    if (cost?.cost_limit_reached && visibleTeamIds.has(text(teamId))) {
      const team = state.teams[text(teamId)];
      rows.push({
        title: `${team?.name || teamId} cost cap reached`,
        meta: `${formatUsd(cost.estimated_cost)} estimated`,
        severity: "warning",
        view: "operations",
        action: "Open operations",
      });
    }
  });
  Object.entries(costState.project_costs || {}).forEach(([projectId, cost]) => {
    if (cost?.cost_limit_reached && visibleProjectIds.has(text(projectId))) {
      const project = state.projects[text(projectId)];
      rows.push({
        title: `${project?.name || projectId} cost cap reached`,
        meta: `${formatUsd(cost.estimated_cost)} estimated`,
        severity: "warning",
        view: "projects",
        action: "Open projects",
      });
    }
  });
  Object.entries(costState.governance_group_costs || {}).forEach(([groupId, cost]) => {
    const group = state.governance_groups[text(groupId)];
    if (!group || !isVisibleGovernanceGroup(group)) {
      return;
    }
    if (cost?.cost_limit_reached || (cost?.member_team_cap_reached_ids || []).length) {
      const memberTeamCount = governanceCostMemberTeamCount(cost);
      rows.push({
        title: `${group.name || groupId} portfolio cost attention`,
        meta: cost?.cost_limit_reached
          ? `${formatUsd(cost.estimated_cost)} governed roll-up across ${memberTeamCount} member team${memberTeamCount === 1 ? "" : "s"}`
          : `${(cost.member_team_cap_reached_ids || []).length} member cap${(cost.member_team_cap_reached_ids || []).length === 1 ? "" : "s"} reached`,
        severity: "warning",
        view: "governance",
        action: "Open governance",
      });
    }
  });
  if (costState.error) {
    rows.push({
      title: "Cost data unavailable",
      meta: costState.error,
      severity: "warning",
      view: "operations",
      action: "Open operations",
    });
  }
  return rows.sort((left, right) => left.title.localeCompare(right.title));
}

function workspaceAttentionItems({
  runtimeAttention,
  costAttention,
  blockedTasks,
  pendingReviews,
  pendingInbox,
  pendingGovernanceAgenda,
}) {
  const items = [];
  const runtimeTaskIds = new Set(
    runtimeAttention.flatMap((row) =>
      uniqueTextList([
        row.task_id,
        ...(row.links?.task_ids || []),
      ]),
    ),
  );
  runtimeAttention.forEach((row) => {
    items.push({
      title: row.title || row.id,
      meta: workspaceRuntimeAttentionMeta(row),
      severity: row.severity || "info",
      view: "operations",
      action: "Open operations",
    });
  });
  costAttention.forEach((row) => items.push(row));
  blockedTasks.forEach((task) => {
    if (runtimeTaskIds.has(text(task.id))) {
      return;
    }
    const project = state.projects[text(task.project_id)];
    const owner = state.agents[text(task.owner_agent_id)];
    items.push({
      title: task.title || task.id,
      meta: [
        taskStateLabel(task),
        project ? project.name || project.id : text(task.project_id),
        taskAssignmentLabel(task),
      ].filter(Boolean).join(" - "),
      severity: task.state === "blocked" ? "warning" : "info",
      view: "task-board",
      action: "Open board",
    });
  });
  pendingReviews.forEach((review) => {
    const deliverable = state.deliverables[text(review.deliverable_id)];
    const reviewer = state.agents[text(review.reviewer_agent_id)];
    items.push({
      title: `Review ${review.id}`,
      meta: [
        humanizeToken(review.state || "pending"),
        deliverable ? deliverable.summary || deliverable.id : text(review.deliverable_id),
        reviewer ? agentLabel(reviewer) : text(review.reviewer_agent_id),
      ].filter(Boolean).join(" - "),
      severity: review.state === "blocked" ? "warning" : "info",
      view: "delivery",
      action: "Open delivery",
    });
  });
  pendingInbox.forEach((item) => {
    const recipient = state.agents[text(item.recipient_agent_id)];
    items.push({
      title: inboxItemLabels[item.item_type] || humanizeToken(item.item_type || "inbox item"),
      meta: [
        inboxItemDetail(item) || item.id,
        recipient ? agentLabel(recipient) : text(item.recipient_agent_id),
      ].filter(Boolean).join(" - "),
      severity: item.priority === "high" ? "warning" : "info",
      view: "people",
      action: "Open teams",
    });
  });
  pendingGovernanceAgenda.forEach((item) => {
    items.push({
      title: item.title || item.id,
      meta: [
        humanizeToken(item.item_type || "agenda"),
        item.priority || "normal",
        item.team_id ? `team ${item.team_id}` : "",
      ].filter(Boolean).join(" - "),
      severity: item.priority === "high" ? "warning" : "info",
      view: "governance",
      action: "Open governance",
    });
  });
  return items.sort(compareWorkspaceAttentionItems);
}

function workspaceInboxItemNeedsAttention(item) {
  return text(item.item_type) !== "operator_directive";
}

function workspaceRuntimeAttentionNeedsAttention(row) {
  const inboxItemId = text(row.inbox_item_id);
  return !inboxItemId || workspaceInboxItemNeedsAttention(state.inbox?.[inboxItemId] || {});
}

function workspaceRuntimeAttentionMeta(row) {
  return [
    humanizeToken(row.kind || "attention"),
    row.state ? humanizeToken(row.state) : "",
    row.agent_id ? `agent ${row.agent_id}` : "",
    row.project_id ? `project ${row.project_id}` : "",
    row.route ? `route ${humanizeToken(row.route)}` : "",
    row.delegate_agent_id ? `delegate ${row.delegate_agent_id}` : "",
  ].filter(Boolean).join(" - ");
}

function compareWorkspaceAttentionItems(left, right) {
  return (
    runtimeSeverityRank(left.severity) - runtimeSeverityRank(right.severity) ||
    text(left.title).localeCompare(text(right.title)) ||
    text(left.meta).localeCompare(text(right.meta))
  );
}

function renderTeamCostCounter(teamId) {
  const cost = teamCostForTeam(teamId);
  if (!cost) {
    return costState.error
      ? renderCostCounter(null, { label: "Estimated team cost", compact: false, error: costState.error })
      : "";
  }
  return renderCostCounter(cost, { label: "Estimated team cost", compact: false });
}

function renderCostBackfillControl() {
  const buttonAttrs = lockedAttrs("refresh transcript costs") || (costBackfillLoading ? "disabled" : "");
  const label = costBackfillLoading ? "Refreshing transcript costs" : "Refresh transcript costs";
  const backfill = costState.backfill || null;
  const detail = backfill
    ? `${text(backfill.coverage_status || "partial")} coverage, ${numberOrZero(backfill.records_imported)} imported, ${numberOrZero(backfill.duplicate_records)} duplicate`
    : "Manual refresh checks transcript-backed Claude usage.";
  return `
    <div class="record-actions cost-refresh-actions">
      <button type="button" data-cost-backfill-refresh${buttonAttrs ? ` ${buttonAttrs}` : ""}>${escapeHtml(label)}</button>
      <span class="meta-line">${escapeHtml(detail)}</span>
    </div>
  `;
}

function teamCostForTeam(teamId) {
  return costState.team_costs?.[teamId] || null;
}

function renderTeamCostBlockedPill(teamId) {
  const cost = teamCostForTeam(teamId);
  if (!cost?.cost_limit_reached) {
    return "";
  }
  return `
    <span class="state-pill warning team-cost-blocked-pill" title="Team wakes are suspended until this budget is reset, renewed, raised, or cleared.">
      cost blocked
    </span>
  `;
}

function renderTeamCostBlockedNotice(teamId) {
  const cost = teamCostForTeam(teamId);
  if (!cost?.cost_limit_reached) {
    return "";
  }
  const maxCost = numberOrNull(cost.max_cost_usd);
  const capLabel = maxCost === null ? "configured cap" : formatUsd(maxCost);
  const coverage = text(cost.coverage_status || costState.coverage_status);
  const coverageNote = coverage && coverage !== "complete" ? " Cost coverage is partial." : "";
  return `
    <div class="team-cost-blocked-notice" role="status">
      <strong>Blocked: estimated cost cap reached</strong>
      <span>Team wakes are suspended until this budget is reset, renewed, raised, or cleared. ${escapeHtml(formatUsd(cost.budget_window_estimated_cost ?? cost.estimated_cost))} estimated used of ${escapeHtml(capLabel)} ${escapeHtml(text(cost.cost_limit_basis) === "budget_window" ? "budget window" : "lifetime cap")}.${escapeHtml(coverageNote)}</span>
    </div>
  `;
}

function renderGovernance() {
  if (!document.querySelector("[data-governance-view]")) {
    return;
  }
  const groups = values(state.governance_groups)
    .filter(isVisibleGovernanceGroup)
    .sort(compareGovernanceGroups);
  if (!groups.length) {
    renderIntoActiveTargets("[data-governance-view]", `
      <p class="empty-state">No governance groups registered.</p>
    `);
    return;
  }
  renderIntoActiveTargets(
    "[data-governance-view]",
    `<ul class="record-list governance-list">${groups.map(renderGovernanceGroup).join("")}</ul>`,
  );
}

function renderGovernanceGroup(group) {
  const manager = state.agents?.[group.manager_agent_id] || null;
  const memberTeams = (group.member_team_ids || [])
    .map((teamId) => state.teams?.[teamId])
    .filter(Boolean)
    .sort(comparePeopleTeams);
  const agenda = governanceAgendaForGroup(group.id);
  const pendingAgenda = agenda.filter((item) => item.state === "pending");
  const visibleAgenda = showHandledGovernanceAgenda
    ? agenda
    : agenda.filter((item) => item.state !== "handled");
  const cost = governanceCostForGroup(group.id);
  const capReached = cost?.cost_limit_reached || (cost?.member_team_cap_reached_ids || []).length > 0;
  return `
    <li class="record-item governance-item">
      <div class="record-line">
        <strong>${escapeHtml(group.name)}</strong>
        <div class="record-actions">
          ${group.archived ? `<span class="state-pill blocked">archived</span>` : ""}
          ${capReached ? `<span class="state-pill warning">cost attention</span>` : ""}
          ${editButton("governance-group", group.id, "Edit")}
        </div>
      </div>
      <div class="meta-line">Manager ${escapeHtml(manager ? agentLabel(manager) : "Unavailable")} · ${escapeHtml(manager ? agentRoleLabel(manager) : "Role unavailable")}</div>
      ${cost ? renderGovernanceCostCounter(cost) : ""}
      ${renderGovernanceGoals(group.goals || [])}
      ${renderGovernanceMemberTeams(memberTeams, cost)}
      ${renderGovernanceAgenda(group, visibleAgenda, pendingAgenda)}
      ${renderTechnicalDetails([
        ["Governance group ID", group.id],
        ["Manager agent ID", group.manager_agent_id],
      ], {
        disclosureKey: teamDisclosureKey("governance-group", group.id, "technical"),
      })}
    </li>
  `;
}

function renderGovernanceMemberTeams(memberTeams, cost) {
  if (!memberTeams.length) {
    return `<p class="empty-state">No member teams.</p>`;
  }
  return `
    <div class="governance-subsection">
      <div class="meta-line">Member teams</div>
      <ul class="compact-record-list">
        ${memberTeams
          .map((team) => {
            const teamCost = cost?.member_team_costs?.[team.id] || teamCostForTeam(team.id);
            const costLabel = teamCost ? ` - ${formatUsd(teamCost.estimated_cost)}` : "";
            const cap = teamCost?.cost_limit_reached ? " - cap reached" : "";
            return `<li><strong>${escapeHtml(team.name)}</strong> <span>${escapeHtml(`${costLabel}${cap}`.replace(/^ - /, ""))}</span></li>`;
          })
          .join("")}
      </ul>
    </div>
  `;
}

function renderGovernanceGoals(goals) {
  if (!goals.length) {
    return "";
  }
  return `
    <div class="governance-subsection governance-goals-subsection">
      <div class="meta-line">Goals</div>
      <ul class="compact-record-list governance-goal-list">
        ${goals
          .map((goal) => {
            const goalName =
              text(goal.name) && text(goal.name) !== text(goal.id)
                ? text(goal.name)
                : text(goal.description) || humanizeToken(goal.id);
            return `
            <li>
              <strong>${escapeHtml(goalName)}</strong>
              ${text(goal.description) && text(goal.description) !== goalName ? `<span>${escapeHtml(goal.description)}</span>` : ""}
            </li>
          `;
          })
          .join("")}
      </ul>
    </div>
  `;
}

function renderGovernanceAgenda(group, agenda, pendingAgenda) {
  if (!agenda.length) {
    return `
      <div class="governance-subsection">
        <div class="meta-line">Agenda - ${escapeHtml(pendingAgenda.length)} pending</div>
        <p class="empty-state">No active governance agenda items.</p>
      </div>
    `;
  }
  return `
    <div class="governance-subsection">
      <div class="meta-line">Agenda - ${escapeHtml(pendingAgenda.length)} pending</div>
      <ul class="compact-record-list">
        ${agenda
          .map((item) => {
            const related = [item.team_id && `team ${item.team_id}`, item.project_id && `project ${item.project_id}`, item.agent_id && `agent ${item.agent_id}`]
              .filter(Boolean)
              .join(" - ");
            return `
              <li>
                <strong>${escapeHtml(item.title)}</strong>
                <span>${escapeHtml(humanizeToken(item.item_type))} - ${escapeHtml(item.priority || "normal")} - ${escapeHtml(item.state || "pending")}${related ? ` - ${escapeHtml(related)}` : ""}</span>
                <span>${escapeHtml(item.summary)}</span>
                ${
                  item.state === "pending"
                    ? `<button class="record-action" type="button" data-governance-agenda-handle-id="${escapeAttr(item.id)}">Mark handled</button>`
                    : ""
                }
              </li>
            `;
          })
          .join("")}
      </ul>
    </div>
  `;
}

function governanceAgendaForGroup(groupId) {
  return values(state.governance_agenda)
    .filter((item) => item.governance_group_id === groupId)
    .sort((left, right) => {
      return (
        prioritySortValue(left.priority) - prioritySortValue(right.priority) ||
        String(left.created_at || "").localeCompare(String(right.created_at || "")) ||
        String(left.id || "").localeCompare(String(right.id || ""))
      );
    });
}

function governanceCostForGroup(groupId) {
  return costState.governance_group_costs?.[groupId] || null;
}

function governanceCostMemberTeamCount(cost) {
  const memberTeamIds = Array.isArray(cost?.member_team_ids)
    ? cost.member_team_ids.map(text).filter(Boolean)
    : [];
  if (memberTeamIds.length) {
    return new Set(memberTeamIds).size;
  }
  return Object.keys(cost?.member_team_costs || {}).length;
}

function prioritySortValue(priority) {
  const order = { high: 0, normal: 1, low: 2 };
  return order[text(priority)] ?? 1;
}

function renderProjectCostCounter(projectId) {
  const cost = costState.project_costs?.[projectId];
  if (!cost) {
    return "";
  }
  return renderCostCounter(cost, { label: "Estimated project cost", compact: true });
}

function renderGovernanceCostCounter(cost) {
  const memberTeamCount = governanceCostMemberTeamCount(cost);
  return renderCostCounter(cost, {
    label: "Governed portfolio roll-up",
    compact: true,
    aggregate: true,
    context: `Sum of ${memberTeamCount} member team${memberTeamCount === 1 ? "" : "s"}; not governance manager spend.`,
  });
}

function renderCostCounter(
  cost,
  { label, compact = false, aggregate = false, context = "", error = "" } = {},
) {
  if (!cost) {
    return `
      <div class="cost-counter cost-counter--warning${compact ? " cost-counter--compact" : ""}${aggregate ? " cost-counter--aggregate" : ""}">
        <strong>${escapeHtml(label || "Cost")}</strong>
        <span>${escapeHtml(error || "unavailable")}</span>
      </div>
    `;
  }
  const coverageStatus = text(cost.coverage_status || costState.coverage_status);
  const unpriced = numberOrZero(cost.unpriced_events_count);
  const events = numberOrZero(cost.events_count);
  const limitReached = Boolean(cost.cost_limit_reached);
  const className =
    coverageStatus === "complete" && !unpriced && !limitReached
      ? "cost-counter"
      : "cost-counter cost-counter--warning";
  const parts = [];
  const maxCost = numberOrNull(cost.max_cost_usd);
  const budgetWindowCost = numberOrNull(cost.budget_window_estimated_cost);
  const displayedCost = budgetWindowCost === null ? numberOrZero(cost.estimated_cost) : budgetWindowCost;
  if (maxCost !== null) {
    parts.push(
      limitReached
        ? `estimated limit reached ${formatUsd(maxCost)}`
        : `${formatUsd(cost.remaining_cost_usd)} remaining`,
    );
  }
  if (events) {
    parts.push(`${formatCount(events)} event${events === 1 ? "" : "s"}`);
  }
  if (budgetWindowCost !== null && numberOrNull(cost.estimated_cost) !== null) {
    parts.push(`lifetime ${formatUsd(cost.estimated_cost)}`);
  }
  if (unpriced) {
    parts.push(`${formatCount(unpriced)} unpriced`);
  }
  if (coverageStatus && coverageStatus !== "complete") {
    parts.push("partial pricing coverage");
  }
  const sourceSummary = costSourceSummary(cost.source_counts || {});
  if (sourceSummary) {
    parts.push(sourceSummary);
  }
  return `
    <div class="${className}${compact ? " cost-counter--compact" : ""}${aggregate ? " cost-counter--aggregate" : ""}">
      <span>${escapeHtml(label || "Cost")}</span>
      <strong>${escapeHtml(formatUsd(displayedCost))}</strong>
      ${context ? `<small class="cost-counter-context">${escapeHtml(context)}</small>` : ""}
      ${parts.length ? `<small>${escapeHtml(parts.join(" - "))}</small>` : ""}
    </div>
  `;
}

function costSourceSummary(sourceCounts) {
  const entries = Object.entries(sourceCounts || {})
    .filter(([, count]) => numberOrZero(count) > 0)
    .sort(([left], [right]) => left.localeCompare(right));
  if (!entries.length) {
    return "";
  }
  return entries
    .map(([source, count]) => `${source}:${formatCount(count)}`)
    .join(", ");
}

function formatCount(value) {
  return numberOrZero(value).toLocaleString(undefined, {
    maximumFractionDigits: 0,
  });
}

function formatUsd(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "$0.00";
  }
  if (Math.abs(amount) >= 1000) {
    return amount.toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    });
  }
  if (Math.abs(amount) >= 10) {
    return amount.toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  return amount.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  });
}

function bindArchivedVisibilityToggle() {
  elements.showArchivedToggle?.addEventListener("change", () => {
    const drafts = captureFormDrafts();
    showArchived = Boolean(elements.showArchivedToggle?.checked);
    render();
    restoreFormDrafts(drafts);
  });
}

function updateArchivedVisibilityToggle() {
  if (elements.showArchivedToggle) {
    elements.showArchivedToggle.checked = showArchived;
  }
}

function bindGovernanceAgendaVisibilityToggle() {
  elements.showHandledGovernanceAgendaToggle?.addEventListener("change", () => {
    const drafts = captureFormDrafts();
    showHandledGovernanceAgenda = Boolean(elements.showHandledGovernanceAgendaToggle?.checked);
    render();
    restoreFormDrafts(drafts);
  });
}

function updateGovernanceAgendaVisibilityToggle() {
  if (elements.showHandledGovernanceAgendaToggle) {
    elements.showHandledGovernanceAgendaToggle.checked = showHandledGovernanceAgenda;
  }
}

function isArchivedTeam(team) {
  return Boolean(team?.archived);
}

function isArchivedGovernanceGroup(group) {
  return Boolean(group?.archived);
}

function isArchivedProject(project) {
  return text(project?.status) === "archived";
}

function isArchivedAgent(agent) {
  return agentStatus(agent) === "archived";
}

function isArchivedReviewProfile(profile) {
  return Boolean(profile?.archived);
}

function isArchivedRole(role) {
  return Boolean(role?.archived);
}

function isVisibleTeam(team) {
  return showArchived || !isArchivedTeam(team);
}

function isVisibleGovernanceGroup(group) {
  return showArchived || !isArchivedGovernanceGroup(group);
}

function isVisibleProject(project) {
  return showArchived || !isArchivedProject(project);
}

function isVisibleAgent(agent) {
  return showArchived || !isArchivedAgent(agent);
}

function isVisibleRole(role) {
  return showArchived || !isArchivedRole(role);
}

function isVisibleReviewProfile(profile) {
  return showArchived || !isArchivedReviewProfile(profile);
}

function isVisibleCase(item) {
  const team = item?.team_id ? state.teams[item.team_id] : null;
  const project = item?.project_id ? state.projects[item.project_id] : null;
  return (!team || isVisibleTeam(team)) && (!project || isVisibleProject(project));
}

function isVisibleRuntimeRow(row) {
  if (showArchived) {
    return true;
  }
  const team = row.team_id ? state.teams[row.team_id] : null;
  const project = row.project_id ? state.projects[row.project_id] : null;
  const agentId = text(row.agent_id || row.id);
  const agent = agentId ? state.agents[agentId] : null;
  return !isArchivedTeam(team) && !isArchivedProject(project) && !isArchivedAgent(agent);
}

function runtimeSortRows(left, right) {
  return (
    runtimeAttentionGroupRank(runtimeAttentionGroup(left)) -
      runtimeAttentionGroupRank(runtimeAttentionGroup(right)) ||
    runtimeAttentionOccurredTime(right) - runtimeAttentionOccurredTime(left) ||
    runtimeSeverityRank(left.severity) - runtimeSeverityRank(right.severity) ||
    String(left.kind || "").localeCompare(String(right.kind || "")) ||
    String(left.id || "").localeCompare(String(right.id || ""))
  );
}

function runtimeAttentionIsPassiveCorrespondence(row) {
  return (
    ["pending_inbox_item", "pending_escalation"].includes(text(row?.kind)) &&
    !row?.reconciliation_required &&
    (
      text(row?.attention_reason) === "pending_without_response_obligation" ||
      row?.response_requested === false
    )
  );
}

function runtimeAttentionGroup(row) {
  const explicit = text(row?.attention_group);
  if (runtimeAttentionGroups.has(explicit)) {
    return explicit;
  }
  if (explicit === "active") {
    return "active_obligation";
  }
  if (explicit === "history") {
    return "retained_history";
  }
  if (failedAttentionKinds.has(text(row?.kind))) {
    return "failed_attempt";
  }
  if (
    row?.reconciliation_required ||
    (Array.isArray(row?.terminal_context) &&
      row.terminal_context.length &&
      ["pending_inbox_item", "pending_escalation"].includes(text(row.kind)))
  ) {
    return "retained_history";
  }
  if (runtimeAttentionIsPassiveCorrespondence(row)) {
    return "retained_history";
  }
  return "active_obligation";
}

function runtimeAttentionGroupRank(group) {
  return {
    active_obligation: 0,
    failed_attempt: 1,
    retained_history: 2,
  }[group] ?? 9;
}

function runtimeAttentionOccurredAt(row) {
  const direct = text(row?.occurred_at);
  if (direct) {
    return direct;
  }
  const kind = text(row?.kind);
  if (kind === "expired_agent_session") {
    return text(row.session_expires_at);
  }
  if (kind === "team_blocker") {
    return text(state.blockers?.[row.blocker_id || row.id]?.created_at);
  }
  if (["blocked_task", "waiting_dependency_task"].includes(kind)) {
    const task = state.tasks?.[row.task_id || row.id];
    return text(task?.state_entered_at || task?.updated_at || task?.created_at);
  }
  if (["pending_inbox_item", "pending_escalation"].includes(kind)) {
    return text(state.inbox?.[row.inbox_item_id || row.id]?.created_at);
  }
  if (kind === "failed_action_record") {
    return text(state.action_records?.[row.action_record_id || row.id]?.created_at);
  }
  if (kind === "failed_git_promotion") {
    const promotion = state.git_promotions?.[row.promotion_id || row.id];
    return text(promotion?.updated_at || promotion?.created_at);
  }
  if (kind === "blocked_release_package") {
    const release = state.release_packages?.[row.release_package_id || row.id];
    return text(release?.updated_at || release?.created_at);
  }
  if (["failed_ephemeral_assignment", "cleanup_pending_ephemeral_assignment"].includes(kind)) {
    const assignment = state.ephemeral_assignments?.[row.assignment_id || row.id];
    return text(assignment?.updated_at || assignment?.created_at);
  }
  return "";
}

function runtimeAttentionOccurredTime(row) {
  const timestamp = runtimeAttentionOccurredAt(row);
  const parsed = timestamp ? Date.parse(timestamp) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function runtimeAttentionAgeSeconds(row) {
  const provided = Number(row?.age_seconds);
  if (Number.isFinite(provided) && provided >= 0) {
    return provided;
  }
  const occurred = runtimeAttentionOccurredTime(row);
  const generated = Date.parse(text(state.runtime_operations?.generated_at));
  const reference = Number.isFinite(generated) ? generated : Date.now();
  if (!Number.isFinite(occurred)) {
    return null;
  }
  return Math.max(0, Math.floor((reference - occurred) / 1000));
}

function runtimeAttentionAgeBucket(row) {
  const provided = text(row?.age_bucket);
  if (runtimeAttentionAgeChoices.some(([value]) => value === provided)) {
    return provided;
  }
  const seconds = runtimeAttentionAgeSeconds(row);
  if (seconds === null) {
    return "unknown";
  }
  if (seconds < 86400) {
    return "under_24_hours";
  }
  if (seconds <= 7 * 86400) {
    return "1_to_7_days";
  }
  if (seconds <= 30 * 86400) {
    return "8_to_30_days";
  }
  return "over_30_days";
}

function runtimeAttentionAgeLabel(row) {
  const seconds = runtimeAttentionAgeSeconds(row);
  if (seconds === null) {
    return "age unavailable";
  }
  if (seconds < 60) {
    return "just now";
  }
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.floor(seconds / 86400);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function runtimeSeverityRank(severity) {
  const order = {
    critical: 0,
    warning: 1,
    info: 2,
  };
  return order[String(severity || "")] ?? 9;
}

function runtimeAgentsForFilter(agents) {
  return [...agents].filter(isVisibleRuntimeRow).sort(byId);
}

function dueLeasedAgentsForFilter(agents) {
  return runtimeAgentsForFilter(agents).filter((agent) => isDueOrLeasedAgent(agent));
}

function runtimeAttentionForFilter(attention, filter) {
  const sorted = [...attention].sort(runtimeSortRows);
  if (filter === "blocked_waiting_work") {
    return sorted.filter((row) => blockedWorkKinds.has(row.kind));
  }
  if (filter === "ephemeral_work") {
    return sorted.filter((row) => ephemeralWorkKinds.has(row.kind));
  }
  return sorted;
}

function runtimeAttentionFacetValues(row, field) {
  if (field === "age") {
    return [runtimeAttentionAgeBucket(row)];
  }
  if (field === "agent") {
    return uniqueTextList([row.agent_id, row.owner_agent_id, row.delegate_agent_id]);
  }
  if (field === "team" || field === "project") {
    const linkedValues = row.links?.[`${field}_ids`];
    return uniqueTextList([
      row[`${field}_id`],
      ...(Array.isArray(linkedValues) ? linkedValues : []),
    ]);
  }
  return uniqueTextList([row[`${field}_id`], row[field]]);
}

function runtimeAttentionMatchesFacets(row) {
  for (const field of ["kind", "age", "team", "project", "agent"]) {
    const selected = runtimeAttentionFilters[field];
    if (selected !== "all" && !runtimeAttentionFacetValues(row, field).includes(selected)) {
      return false;
    }
  }
  return true;
}

function runtimeAttentionFacetOptionRows(rows, field) {
  const counts = new Map();
  rows.forEach((row) => {
    runtimeAttentionFacetValues(row, field).forEach((value) => {
      counts.set(value, (counts.get(value) || 0) + 1);
    });
  });
  return [...counts.entries()].sort(([left], [right]) => {
    if (field === "age") {
      const order = runtimeAttentionAgeChoices.map(([value]) => value);
      return order.indexOf(left) - order.indexOf(right);
    }
    return runtimeAttentionFacetLabel(field, left).localeCompare(runtimeAttentionFacetLabel(field, right));
  });
}

function runtimeAttentionFacetLabel(field, value) {
  if (field === "kind") {
    return humanizeToken(value);
  }
  if (field === "age") {
    return runtimeAttentionAgeChoices.find(([choice]) => choice === value)?.[1] || humanizeToken(value);
  }
  if (field === "team") {
    return state.teams?.[value]?.name || value;
  }
  if (field === "project") {
    return state.projects?.[value]?.name || value;
  }
  if (field === "agent") {
    const agent = state.agents?.[value];
    return agent ? agentLabel(agent) : value;
  }
  return value;
}

function renderRuntimeAttentionFacetSelect(rows, field, label) {
  const options = runtimeAttentionFacetOptionRows(rows, field);
  return `
    <label>
      <span>${escapeHtml(label)}</span>
      <select data-runtime-attention-filter="${escapeAttr(field)}">
        <option value="all">All ${escapeHtml(label.toLowerCase())} (${rows.length})</option>
        ${options.map(([value, count]) => `<option value="${escapeAttr(value)}"${runtimeAttentionFilters[field] === value ? " selected" : ""}>${escapeHtml(runtimeAttentionFacetLabel(field, value))} (${count})</option>`).join("")}
      </select>
    </label>
  `;
}

function renderRuntimeAttentionControls(rows) {
  const groupCounts = {
    active_obligation: rows.filter((row) => runtimeAttentionGroup(row) === "active_obligation").length,
    failed_attempt: rows.filter((row) => runtimeAttentionGroup(row) === "failed_attempt").length,
    retained_history: rows.filter((row) => runtimeAttentionGroup(row) === "retained_history").length,
  };
  const reconciliationCount = rows.filter((row) => row.reconciliation_required).length;
  const terminalCount = rows.filter((row) => Array.isArray(row.terminal_context) && row.terminal_context.length).length;
  const hasFilters = Object.values(runtimeAttentionFilters).some((value) => value !== "all");
  return `
    <section class="runtime-attention-controls" aria-label="Attention filters">
      <div class="runtime-attention-counts" data-runtime-attention-counts>
        <span><strong>${groupCounts.active_obligation}</strong> active</span>
        <span><strong>${groupCounts.failed_attempt}</strong> failed attempts</span>
        <span><strong>${groupCounts.retained_history}</strong> retained history</span>
        ${terminalCount ? `<span>${terminalCount} terminal-linked</span>` : ""}
        ${reconciliationCount ? `<span class="runtime-reconciliation-count">${reconciliationCount} need reconciliation</span>` : ""}
      </div>
      <div class="runtime-attention-facets">
        ${renderRuntimeAttentionFacetSelect(rows, "kind", "Kind")}
        ${renderRuntimeAttentionFacetSelect(rows, "age", "Age")}
        ${renderRuntimeAttentionFacetSelect(rows, "team", "Team")}
        ${renderRuntimeAttentionFacetSelect(rows, "project", "Project")}
        ${renderRuntimeAttentionFacetSelect(rows, "agent", "Agent")}
      </div>
      <div class="runtime-attention-filter-actions">
        <p>Age provides context; linked lifecycle state determines whether work is still actionable.</p>
        ${hasFilters ? `<button type="button" class="record-action secondary" data-runtime-attention-clear>Clear filters</button>` : ""}
      </div>
    </section>
  `;
}

function renderRuntimeAttentionGroupedSections(attention) {
  const groups = [
    {
      id: "active_obligation",
      title: "Active blockers / awaiting response",
      empty: "No active blockers or response obligations match these filters.",
    },
    {
      id: "failed_attempt",
      title: "Failed attempts",
      empty: "No failed attempts match these filters.",
    },
  ];
  const markup = groups.map((group) => {
    const rows = attention.filter((row) => runtimeAttentionGroup(row) === group.id);
    return renderRuntimeAttentionGroupSection(group, rows);
  });
  const historyRows = attention.filter((row) => runtimeAttentionGroup(row) === "retained_history");
  markup.push(`
    <section class="runtime-section runtime-attention-group runtime-attention-history">
      <div class="runtime-section-header">
        <div>
          <p class="subheading">Retained context</p>
          <h3>Retained history</h3>
        </div>
        <div class="record-actions">
          <span class="runtime-count">${historyRows.length} visible</span>
          <button
            type="button"
            class="record-action"
            data-runtime-attention-history-toggle
            aria-pressed="${runtimeAttentionShowHistory ? "true" : "false"}"
          >${runtimeAttentionShowHistory ? "Hide retained history" : `Show retained history (${historyRows.length})`}</button>
        </div>
      </div>
      ${runtimeAttentionShowHistory ? renderRuntimeAttentionGroupRows("retained_history", historyRows, "No retained history matches these filters.") : `<p class="empty-state">Historical and terminal-linked records remain durable without being presented as active obligations.</p>`}
    </section>
  `);
  return markup.join("");
}

function renderRuntimeAttentionGroupSection(group, rows) {
  return `
    <section class="runtime-section runtime-attention-group" data-attention-group="${escapeAttr(group.id)}">
      <div class="runtime-section-header">
        <div>
          <p class="subheading">${group.id === "failed_attempt" ? "Failure history" : "Operational obligations"}</p>
          <h3>${escapeHtml(group.title)}</h3>
        </div>
        <div class="runtime-count">${rows.length} visible</div>
      </div>
      ${renderRuntimeAttentionGroupRows(group.id, rows, group.empty)}
    </section>
  `;
}

function renderRuntimeAttentionGroupRows(groupId, rows, emptyMessage) {
  if (!rows.length) {
    return `<p class="empty-state">${escapeHtml(emptyMessage)}</p>`;
  }
  const pageCount = Math.max(1, Math.ceil(rows.length / runtimeAttentionPageSize));
  const requestedPage = runtimeAttentionGroupPages.get(groupId) || 1;
  const page = Math.min(requestedPage, pageCount);
  runtimeAttentionGroupPages.set(groupId, page);
  const offset = (page - 1) * runtimeAttentionPageSize;
  const pageRows = rows.slice(offset, offset + runtimeAttentionPageSize);
  return `
    ${renderRuntimeAttentionRows(pageRows, emptyMessage)}
    ${renderRuntimeAttentionPagination(groupId, rows.length, page, pageCount)}
  `;
}

function renderRuntimeAttentionPagination(groupId, total, page, pageCount) {
  if (pageCount <= 1) {
    return "";
  }
  const first = (page - 1) * runtimeAttentionPageSize + 1;
  const last = Math.min(total, page * runtimeAttentionPageSize);
  return `
    <nav class="runtime-attention-pagination" aria-label="${escapeAttr(humanizeToken(groupId))} pages">
      <span>Showing ${first}–${last} of ${total}</span>
      <div class="record-actions">
        <button type="button" class="record-action secondary" data-runtime-attention-page-group="${escapeAttr(groupId)}" data-runtime-attention-page="${page - 1}"${page <= 1 ? " disabled" : ""}>Previous</button>
        <span>Page ${page} of ${pageCount}</span>
        <button type="button" class="record-action secondary" data-runtime-attention-page-group="${escapeAttr(groupId)}" data-runtime-attention-page="${page + 1}"${page >= pageCount ? " disabled" : ""}>Next</button>
      </div>
    </nav>
  `;
}

function renderRuntimeAttentionRows(attention, emptyMessage = "No current blockers.") {
  if (!attention.length) {
    return `<p class="empty-state">${escapeHtml(emptyMessage)}</p>`;
  }
  return `
    <ul class="runtime-list runtime-attention-list">
      ${attention.map((row) => renderRuntimeAttentionRow(row)).join("")}
    </ul>
  `;
}

function runtimeAttentionCategory(row) {
  const kind = text(row.kind);
  if (["team_blocker", "blocked_task", "waiting_dependency_task", "blocked_release_package"].includes(kind)) {
    return { id: "blocker", label: "Blocker", icon: "!" };
  }
  if (failedAttentionKinds.has(kind)) {
    return { id: "failed-operation", label: "Failed operation", icon: "×" };
  }
  if (runtimeAttentionIsPassiveCorrespondence(row)) {
    return { id: "passive-correspondence", label: "No response requested", icon: "•" };
  }
  if (["pending_inbox_item", "pending_escalation"].includes(kind)) {
    return { id: "awaiting-response", label: "Awaiting agent response", icon: "↗" };
  }
  if (kind === "expired_agent_session") {
    return { id: "session", label: "Session attention", icon: "◷" };
  }
  if (kind === "cleanup_pending_ephemeral_assignment") {
    return { id: "cleanup", label: "Cleanup pending", icon: "↻" };
  }
  return { id: "other", label: humanizeToken(kind || "attention"), icon: "•" };
}

function runtimeAttentionOwner(row) {
  const linkedAgentIds = Array.isArray(row.links?.agent_ids) ? row.links.agent_ids : [];
  const agentId = text(
    row.delegate_agent_id ||
      row.owner_agent_id ||
      row.agent_id ||
      linkedAgentIds[0],
  );
  const agent = agentId ? state.agents?.[agentId] : null;
  return {
    id: agentId,
    label: agent ? agentLabel(agent) : agentId || "Operator",
  };
}

function runtimeAttentionWhere(row) {
  const projectId = text(row.project_id || row.links?.project_ids?.[0]);
  const taskId = text(row.task_id || row.links?.task_ids?.[0]);
  const deliverableId = text(row.deliverable_id || row.links?.deliverable_ids?.[0]);
  const project = projectId ? state.projects?.[projectId] : null;
  const task = taskId ? state.tasks?.[taskId] : null;
  const deliverable = deliverableId ? state.deliverables?.[deliverableId] : null;
  return [
    project?.name,
    task?.title,
    deliverable ? deliverableDisplaySubject(deliverable) : "",
  ].filter(Boolean);
}

function runtimeAttentionTerminalRows(row) {
  const rows = Array.isArray(row.terminal_context)
    ? row.terminal_context
    : Array.isArray(row.terminal_links)
      ? row.terminal_links
      : [];
  return rows.filter((item) => item && typeof item === "object");
}

function renderRuntimeAttentionContextBadges(row) {
  const terminalRows = runtimeAttentionTerminalRows(row);
  if (!terminalRows.length && !row.reconciliation_required) {
    return "";
  }
  return `
    <div class="runtime-attention-context-badges">
      ${terminalRows.map((item) => `<span class="state-pill neutral">Terminal ${escapeHtml(humanizeToken(item.kind || "work"))}: ${escapeHtml(humanizeToken(item.state || "terminal"))}</span>`).join("")}
      ${row.reconciliation_required ? `<span class="state-pill warning">Reconciliation required</span>` : ""}
    </div>
  `;
}

function renderRuntimeAttentionTime(row) {
  const timestamp = runtimeAttentionOccurredAt(row);
  if (!timestamp) {
    return `<span class="runtime-attention-age">Time unavailable · ${escapeHtml(runtimeAttentionAgeLabel(row))}</span>`;
  }
  return `
    <span class="runtime-attention-age">
      <time datetime="${escapeAttr(timestamp)}">${escapeHtml(formatTimelineTimestamp(timestamp))}</time>
      · ${escapeHtml(runtimeAttentionAgeLabel(row))}
    </span>
  `;
}

function runtimeAttentionDisplayTitle(row) {
  const kind = text(row.kind);
  if (kind === "failed_action_record") {
    const action = state.action_records?.[row.action_record_id || row.id]?.action || {};
    const actionType = text(action.type || action.kind);
    return actionType ? `${humanizeToken(actionType)} action failed` : "Agent action failed";
  }
  if (kind === "failed_git_promotion") {
    const promotion = state.git_promotions?.[row.promotion_id || row.id];
    return `${humanizeToken(promotion?.promotion_kind || row.promotion_kind || "Git")} promotion failed`;
  }
  if (kind === "expired_agent_session") {
    return `Session expired for ${runtimeAttentionOwner(row).label}`;
  }
  if (kind === "blocked_release_package") {
    const release = state.release_packages?.[row.release_package_id || row.id];
    return release ? `${releaseDisplayLabel(release)} is blocked` : "Release is blocked";
  }
  if (["failed_ephemeral_assignment", "cleanup_pending_ephemeral_assignment"].includes(kind)) {
    const assignment = state.ephemeral_assignments?.[row.assignment_id || row.id];
    return assignment?.title
      ? `${assignment.title}: ${humanizeToken(row.state || kind)}`
      : humanizeToken(kind);
  }
  return text(row.title) || humanizeToken(kind || "attention record");
}

function runtimeAttentionProblem(row) {
  if (row.error) {
    return text(row.error);
  }
  const kind = text(row.kind);
  if (["pending_inbox_item", "pending_escalation"].includes(kind)) {
    const item = state.inbox?.[row.inbox_item_id || row.id];
    return text(item ? inboxItemDetail(item) : row.detail) || runtimeAttentionDisplayTitle(row);
  }
  if (kind === "team_blocker") {
    const blocker = state.blockers?.[row.blocker_id || row.id];
    return text(blocker?.details || blocker?.summary) || runtimeAttentionDisplayTitle(row);
  }
  if (["blocked_task", "waiting_dependency_task"].includes(kind)) {
    const task = state.tasks?.[row.task_id || row.id];
    return text(task?.description) || runtimeAttentionDisplayTitle(row);
  }
  if (kind === "expired_agent_session") {
    return "The agent session has expired and cannot safely receive new dispatch work.";
  }
  return runtimeAttentionDisplayTitle(row);
}

function runtimeAttentionImpact(row) {
  const terminalRows = runtimeAttentionTerminalRows(row);
  if (row.reconciliation_required) {
    return "The correspondence is still pending even though linked work is terminal; reconcile it explicitly in Agent Mail.";
  }
  if (runtimeAttentionIsPassiveCorrespondence(row)) {
    return "No response was requested. This pending correspondence is retained for context rather than presented as an active obligation.";
  }
  if (
    terminalRows.length &&
    runtimeAttentionGroup(row) === "retained_history"
  ) {
    return "Linked work is terminal. This retained record provides context but does not by itself prove an active operational issue.";
  }
  if (runtimeAttentionGroup(row) === "failed_attempt") {
    return row.kind === "failed_action_record"
      ? "The attempt failed and remains visible until it is retried or explicitly dismissed."
      : "The attempt failed and remains visible as operational history. Recovery follows the owning workflow for this operation.";
  }
  if (runtimeAttentionCategory(row).id === "blocker") {
    return "Current work cannot proceed normally until this blocker or dependency is resolved.";
  }
  if (runtimeAttentionCategory(row).id === "awaiting-response") {
    return "An agent response or explicit inbox reconciliation is still outstanding.";
  }
  return "This record affects current runtime readiness or follow-up.";
}

function runtimeAttentionRecommendedAction(row) {
  if (runtimeAttentionIsPassiveCorrespondence(row)) {
    return "Review it in Agent Mail if more context is useful; no response is required.";
  }
  if (row.next_action_hint) {
    return text(row.next_action_hint);
  }
  const category = runtimeAttentionCategory(row).id;
  if (category === "awaiting-response") {
    return row.reconciliation_required
      ? "Open Agent Mail, review the terminal linked work, then reply or mark the message handled."
      : "Open Agent Mail and respond or mark the correspondence handled.";
  }
  if (row.kind === "failed_action_record") {
    return "Inspect the failed attempt, retry if it remains relevant, or dismiss it when no follow-up is needed.";
  }
  if (row.kind === "failed_git_promotion") {
    return "Inspect the promotion target and candidate, then retry through the release recovery path.";
  }
  if (row.kind === "failed_ephemeral_assignment") {
    return "Inspect the assignment error, then reconcile or clean up the assignment through its owning ephemeral workflow.";
  }
  if (row.kind === "team_blocker" && row.blocker_id) {
    const blocker = state.blockers?.[row.blocker_id];
    if (
      text(blocker?.source_kind) === "release_package" &&
      releaseRecoveryTaskForBlocker(blocker)
    ) {
      return "Resume the existing release-recovery task. The assigned agent must submit the resolved ref through the release recovery lifecycle; do not resolve this blocker generically.";
    }
  }
  if (category === "blocker") {
    return "Resolve the underlying blocker, dependency, or release gate before continuing.";
  }
  if (row.kind === "expired_agent_session") {
    return "Renew or replace the agent session before dispatching more work.";
  }
  return "Inspect the related work and choose the appropriate explicit follow-up.";
}

function renderRuntimeAttentionRelatedWork(row) {
  const related = [];
  uniqueTextList([row.project_id, ...(row.links?.project_ids || [])]).forEach((id) => {
    const project = state.projects?.[id];
    if (project) related.push(["Project", project.name]);
  });
  uniqueTextList([row.task_id, ...(row.links?.task_ids || [])]).forEach((id) => {
    const task = state.tasks?.[id];
    if (task) related.push(["Task", task.title]);
  });
  uniqueTextList([row.deliverable_id, ...(row.links?.deliverable_ids || [])]).forEach((id) => {
    const deliverable = state.deliverables?.[id];
    if (deliverable) related.push(["Deliverable", deliverableDisplaySubject(deliverable)]);
  });
  uniqueTextList([row.release_package_id, ...(row.links?.release_package_ids || [])]).forEach((id) => {
    const release = state.release_packages?.[id];
    if (release) related.push(["Release", releaseDisplayLabel(release)]);
  });
  if (!related.length) {
    return `<p class="empty-state">No named work is linked.</p>`;
  }
  return `<dl class="runtime-related-work">${related.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
}

function renderRuntimeAttentionTechnicalDetails(row) {
  return renderTechnicalDetails([
    ["Attention ID", row.id],
    ["Team IDs", uniqueTextList([row.team_id, ...(row.links?.team_ids || [])])],
    ["Agent IDs", uniqueTextList([
      row.agent_id,
      row.owner_agent_id,
      row.delegate_agent_id,
      ...(row.links?.agent_ids || []),
    ])],
    ["Project IDs", uniqueTextList([row.project_id, ...(row.links?.project_ids || [])])],
    ["Task IDs", uniqueTextList([row.task_id, ...(row.links?.task_ids || [])])],
    ["Deliverable IDs", uniqueTextList([row.deliverable_id, ...(row.links?.deliverable_ids || [])])],
    ["Inbox IDs", uniqueTextList([row.inbox_item_id, ...(row.links?.inbox_item_ids || [])])],
    ["Action ID", row.action_record_id],
    ["Assignment IDs", uniqueTextList([row.assignment_id, ...(row.links?.assignment_ids || [])])],
    ["Promotion IDs", uniqueTextList([row.promotion_id, ...(row.links?.promotion_ids || [])])],
    ["Release IDs", uniqueTextList([row.release_package_id, ...(row.links?.release_package_ids || [])])],
    ["Review IDs", uniqueTextList([row.review_request_id, ...(row.links?.review_request_ids || [])])],
    ["Blocker IDs", uniqueTextList([row.blocker_id, ...(row.links?.blocker_ids || [])])],
    ["Dispatch IDs", uniqueTextList([row.dispatch_id || row.active_dispatch_id, ...(row.links?.dispatch_ids || [])])],
    ["Session IDs", uniqueTextList([row.session_id, ...(row.links?.session_ids || [])])],
    ["Source branch", row.source_branch],
    ["Target branch", row.target_branch],
    ["Commit", row.commit_sha || row.candidate_commit_sha],
  ], {
    summary: "Identifiers, branches, and hashes",
    disclosureKey: teamDisclosureKey("runtime-attention", row.id, "technical"),
  });
}

function renderRuntimeAttentionActions(row) {
  const actions = [];
  let blockerAction = "";
  if (["pending_inbox_item", "pending_escalation"].includes(text(row.kind))) {
    actions.push(`<button type="button" class="record-action" data-team-link-view="mail">Open Agent Mail</button>`);
  }
  if (row.kind === "failed_action_record" && row.action_record_id) {
    actions.push(`
      <button
        type="button"
        class="record-action"
        data-dismiss-action-id="${escapeAttr(row.action_record_id)}"
      >Dismiss failed attempt</button>
    `);
  } else if (row.kind === "team_blocker" && row.blocker_id) {
    blockerAction = renderTerminalBlockerResolveAction(
      state.blockers?.[row.blocker_id],
      "runtime-attention-actions",
    );
  }
  return `${actions.length ? `<div class="record-actions runtime-attention-actions">${actions.join("")}</div>` : ""}${blockerAction}`;
}

function renderRuntimeAttentionDetailBody(row) {
  const owner = runtimeAttentionOwner(row);
  return `
    <div class="runtime-attention-detail-grid">
      <section>
        <h4>Problem</h4>
        <p>${escapeHtml(runtimeAttentionProblem(row))}</p>
      </section>
      <section>
        <h4>Impact</h4>
        <p>${escapeHtml(runtimeAttentionImpact(row))}</p>
        ${renderRuntimeAttentionContextBadges(row)}
      </section>
      <section>
        <h4>Owner</h4>
        <p>${escapeHtml(owner.label)}</p>
      </section>
      <section>
        <h4>Recommended next action</h4>
        <p>${escapeHtml(runtimeAttentionRecommendedAction(row))}</p>
        ${renderRuntimeAttentionActions(row)}
      </section>
      <section class="runtime-related-section">
        <h4>Related work</h4>
        ${renderRuntimeAttentionRelatedWork(row)}
      </section>
      <section class="runtime-technical-section">
        <h4>Technical details</h4>
        ${renderRuntimeAttentionTechnicalDetails(row)}
      </section>
    </div>
  `;
}

function renderRuntimeAttentionRow(row) {
  if (activeTeamView === "operations") {
    return renderCompactRuntimeAttentionRow(row);
  }
  const category = runtimeAttentionCategory(row);
  const owner = runtimeAttentionOwner(row);
  const where = runtimeAttentionWhere(row);
  return `
    <li class="record-item runtime-item runtime-attention-item runtime-attention-kind-${escapeAttr(category.id)} runtime-severity-${escapeAttr(row.severity || "info")}">
      <div class="record-line">
        <span class="runtime-attention-title">
          <span class="runtime-attention-icon" aria-hidden="true">${escapeHtml(category.icon)}</span>
          <strong>${escapeHtml(runtimeAttentionDisplayTitle(row))}</strong>
        </span>
        <span class="state-pill ${escapeAttr(row.severity || "info")}">${escapeHtml(humanizeToken(row.severity || "info"))} urgency</span>
      </div>
      <div class="runtime-attention-glance">
        <span class="runtime-attention-kind-label">${escapeHtml(category.label)}</span>
        ${where.length ? `<span>${escapeHtml(where.join(" · "))}</span>` : `<span>General runtime context</span>`}
        <span>Owner: ${escapeHtml(owner.label)}</span>
        ${renderRuntimeAttentionTime(row)}
      </div>
      ${renderRuntimeAttentionDetailBody(row)}
    </li>
  `;
}

function renderCompactRuntimeAttentionRow(row) {
  const category = runtimeAttentionCategory(row);
  const owner = runtimeAttentionOwner(row);
  const where = runtimeAttentionWhere(row);
  return `
    <li class="record-item runtime-item runtime-item-compact runtime-attention-card runtime-attention-kind-${escapeAttr(category.id)} runtime-severity-${escapeAttr(row.severity || "info")}" data-attention-id="${escapeAttr(row.id)}">
      <details ${teamDisclosureAttributes(teamDisclosureKey("operations", "attention", row.id, "detail"))}>
        <summary class="runtime-summary compact-record-summary">
          <span class="runtime-attention-summary-icon" aria-hidden="true">${escapeHtml(category.icon)}</span>
          <span class="compact-record-main runtime-attention-summary-main">
            <strong>${escapeHtml(runtimeAttentionDisplayTitle(row))}</strong>
            <span>${escapeHtml(where.join(" · ") || "General runtime context")} · Owner: ${escapeHtml(owner.label)}</span>
            ${renderRuntimeAttentionTime(row)}
          </span>
          <span class="runtime-summary-meta">
            <span class="runtime-attention-kind-label">${escapeHtml(category.label)}</span>
            <span class="state-pill ${escapeAttr(row.severity || "info")}">${escapeHtml(humanizeToken(row.severity || "info"))} urgency</span>
          </span>
        </summary>
        <div class="compact-record-body">
          ${renderRuntimeAttentionDetailBody(row)}
        </div>
      </details>
    </li>
  `;
}

function renderRuntimeAgentRows(agents, emptyMessage = "No agent runtime data yet.") {
  if (!agents.length) {
    return `<p class="empty-state">${escapeHtml(emptyMessage)}</p>`;
  }
  return `
    <ul class="runtime-list runtime-agent-list">
      ${agents.map((agent) => renderRuntimeAgentRow(agent)).join("")}
    </ul>
  `;
}

function renderRuntimeAgentRow(agent) {
  if (activeTeamView === "operations") {
    return renderCompactRuntimeAgentRow(agent);
  }
  const status = runtimeAgentStatus(agent);
  const refs = runtimeReferenceParts(agent);
  const sessionStatus = normalizeAgentSessionHealth(agent);
  const sessionSummary = runtimeAgentSessionSummary(agent);
  const wakeReason = runtimeText(agent.wake_reason);
  const lastWake = runtimeText(agent.last_wake_at);
  const nextWake = runtimeText(agent.next_wake_at);
  const wakeLease = runtimeText(agent.wake_leased_until);
  const dispatchId = runtimeText(agent.active_dispatch_id);
  const contextPending = Boolean(agent.context_rotation_pending);
  const handoffPreparing = Boolean(agent.context_handoff_preparation_pending);
  const handoffPreparationStatus = runtimeText(agent.context_handoff_preparation_status);
  const contextPolicy = contextClearingPolicyValue(agent);
  const lastRotated = runtimeText(agent.last_context_rotated_at);
  const idleRotationDue = runtimeText(agent.context_idle_rotation_due_at);
  const handoffSummary = text(agent.last_handoff_summary);
  return `
    <li class="record-item runtime-item runtime-agent-item">
      <div class="record-line">
        <strong>${escapeHtml(agent.id)}</strong>
        <div class="record-actions">
          <span class="state-pill ${escapeHtml(status)}">${escapeHtml(status)}</span>
          ${sessionStatus.isExpired ? `<span class="state-pill critical">session expired</span>` : ""}
          ${renderAgentStandDownControls(agent.id, agent)}
          ${["every_wake", "work_lineage"].includes(contextPolicy) ? "" : `<button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(agent.id)}">Clear context</button>`}
        </div>
      </div>
      <div class="meta-line">${escapeHtml(agentRoleLabel(agent))} - ${escapeHtml(sessionSummary)}${agentProjectSummary(agent) ? ` - projects ${escapeHtml(agentProjectSummary(agent))}` : ""}</div>
      <div class="meta-line">Last wake ${escapeHtml(lastWake)} - Next wake ${escapeHtml(nextWake)} - Wake lease ${escapeHtml(wakeLease)}</div>
      <div class="meta-line">Wake reason ${escapeHtml(wakeReason)} - No progress ${escapeHtml(agent.no_progress_count ?? 0)} - Active dispatch ${escapeHtml(dispatchId)}</div>
      <div class="meta-line">${contextPolicy === "work_lineage" ? "Context routing Work lineage" : `Context clearing ${escapeHtml(contextClearingPolicyLabel(contextPolicy))} - ${escapeHtml(handoffPreparing ? "preparing handoff" : contextPending ? "queued" : idleRotationDue ? `idle grace until ${idleRotationDue}` : "not pending")}${contextPolicy === "every_wake" ? "" : ` - Last rotated ${escapeHtml(lastRotated)}`}${handoffPreparationStatus ? ` - Handoff ${escapeHtml(handoffPreparationStatus)}` : ""}`}</div>
      ${
        handoffSummary
          ? `<details class="runtime-handoff" ${teamDisclosureAttributes(teamDisclosureKey(activeTeamView, "runtime-agent", agent.id, "handoff"))}><summary>Handoff summary</summary><pre>${escapeHtml(handoffSummary)}</pre></details>`
          : ""
      }
      <div class="meta-line">Flags ${escapeHtml(runtimeBooleanLabel(agent.wake_due, "wake due"))} - ${escapeHtml(runtimeBooleanLabel(agent.lease_active, "lease active"))}${agent.stand_down_active ? " - stood down" : ""}</div>
      ${refs ? `<div class="meta-line">${refs}</div>` : ""}
    </li>
  `;
}

function renderCompactRuntimeAgentRow(agent) {
  const status = runtimeAgentStatus(agent);
  const refs = runtimeReferenceParts(agent);
  const sessionStatus = normalizeAgentSessionHealth(agent);
  const sessionSummary = runtimeAgentSessionSummary(agent);
  const wakeReason = runtimeText(agent.wake_reason);
  const lastWake = runtimeText(agent.last_wake_at);
  const nextWake = runtimeText(agent.next_wake_at);
  const wakeLease = runtimeText(agent.wake_leased_until);
  const dispatchId = runtimeText(agent.active_dispatch_id);
  const contextPending = Boolean(agent.context_rotation_pending);
  const contextPolicy = contextClearingPolicyValue(agent);
  const lastRotated = runtimeText(agent.last_context_rotated_at);
  const idleRotationDue = runtimeText(agent.context_idle_rotation_due_at);
  const handoffSummary = text(agent.last_handoff_summary);
  const flags = [
    sessionStatus.isExpired ? "session expired" : "",
    agent.wake_due ? "wake due" : "",
    agent.lease_active ? "lease active" : "",
    agent.active_dispatch_id ? `dispatch ${agent.active_dispatch_id}` : "",
    agent.stand_down_active ? "stood down" : "",
  ].filter(Boolean);
  return `
    <li class="record-item runtime-item runtime-item-compact runtime-agent-item">
      <details ${teamDisclosureAttributes(teamDisclosureKey("operations", "agent", agent.id, "detail"))}>
        <summary class="runtime-summary compact-record-summary">
          <span class="compact-record-main">
            <strong>${escapeHtml(agent.id)}</strong>
            <span>${escapeHtml(agentRoleLabel(agent))} - ${escapeHtml(sessionSummary)}${agentProjectSummary(agent) ? ` - projects ${escapeHtml(agentProjectSummary(agent))}` : ""}</span>
          </span>
          <span class="runtime-summary-meta">
            <span class="state-pill ${escapeHtml(status)}">${escapeHtml(status)}</span>
            <span>${escapeHtml(flags.join(" - ") || "idle")}</span>
          </span>
        </summary>
        <div class="compact-record-body">
          <div class="record-actions">
            ${renderAgentStandDownControls(agent.id, agent)}
            ${["every_wake", "work_lineage"].includes(contextPolicy) ? "" : `<button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(agent.id)}">Clear context</button>`}
          </div>
          <div class="meta-line">Last wake ${escapeHtml(lastWake)} - Next wake ${escapeHtml(nextWake)} - Wake lease ${escapeHtml(wakeLease)}</div>
          <div class="meta-line">Wake reason ${escapeHtml(wakeReason)} - No progress ${escapeHtml(agent.no_progress_count ?? 0)} - Active dispatch ${escapeHtml(dispatchId)}</div>
          <div class="meta-line">${contextPolicy === "work_lineage" ? "Context routing Work lineage" : `Context clearing ${escapeHtml(contextClearingPolicyLabel(contextPolicy))} - ${escapeHtml(contextPending ? "pending" : idleRotationDue ? `idle grace until ${idleRotationDue}` : "not pending")}${contextPolicy === "every_wake" ? "" : ` - Last rotated ${escapeHtml(lastRotated)}`}`}</div>
          ${
            handoffSummary
              ? `<details class="runtime-handoff" ${teamDisclosureAttributes(teamDisclosureKey("operations", "agent", agent.id, "handoff"))}><summary>Handoff summary</summary><pre>${escapeHtml(handoffSummary)}</pre></details>`
              : ""
          }
          <div class="meta-line">Flags ${escapeHtml(runtimeBooleanLabel(agent.wake_due, "wake due"))} - ${escapeHtml(runtimeBooleanLabel(agent.lease_active, "lease active"))}${agent.stand_down_active ? " - stood down" : ""}</div>
          ${refs ? `<div class="meta-line">${refs}</div>` : ""}
        </div>
      </details>
    </li>
  `;
}

function runtimeAgentStatus(agent) {
  if (Boolean(agent.session_is_expired)) {
    return "critical";
  }
  if (isDueOrLeasedAgent(agent)) {
    return "warning";
  }
  return "info";
}

function isDueOrLeasedAgent(agent) {
  return Boolean(
    agent.session_is_expired ||
      agent.wake_due ||
      agent.lease_active ||
      agent.active_dispatch_id,
  );
}

function runtimeAgentSessionSummary(agent) {
  const health = normalizeAgentSessionHealth(agent);
  return sessionHealthRuntimeLine(health, agent.session_id) || text(agent.session_id);
}

function runtimeReferenceParts(row) {
  const refs = [];
  const entries = [
    ["team", row.team_id],
    ["agent", row.agent_id],
    ["project", row.project_id],
    ["task", row.task_id],
    ["deliverable", row.deliverable_id],
    ["deliverable", row.deliverable_ids],
    ["inbox", row.inbox_item_id],
    ["action", row.action_record_id],
    ["assignment", row.assignment_id],
    ["promotion", row.promotion_id],
    ["release", row.release_package_id],
    ["review", row.review_request_id],
    ["blocker", row.blocker_id],
    ["source", row.source_id],
    ["owner", row.owner_agent_id],
    ["delegate", row.delegate_agent_id],
    ["dispatch", row.dispatch_id],
    ["session", row.session_id],
  ];
  entries.forEach(([label, value]) => {
    if (Array.isArray(value)) {
      if (value.length) {
        refs.push(`${label}s ${escapeHtml(value.join(", "))}`);
      }
      return;
    }
    if (value) {
      refs.push(`${label} ${escapeHtml(value)}`);
    }
  });
  return refs.length ? `Refs: ${refs.join(" - ")}` : "";
}

function runtimeBooleanLabel(value, label) {
  return `${label}: ${value ? "yes" : "no"}`;
}

function runtimeText(value) {
  const formatted = text(value);
  return formatted || "-";
}

function runtimeFilterLabel(filter) {
  return runtimeFilterLabels[filter] || runtimeFilterLabels.all;
}

function bindRuntimeFilters() {
  elements.runtimeFilterButtons.forEach((button) => {
    button.addEventListener("click", () => {
      runtimeFilter = button.dataset.runtimeFilter || "all";
      runtimeFilterTouched = true;
      renderRuntimeOperations();
    });
  });
  document.addEventListener("change", (event) => {
    const field = event.target?.closest?.("[data-runtime-attention-filter]");
    if (!field) {
      return;
    }
    const facet = text(field.dataset.runtimeAttentionFilter);
    if (!(facet in runtimeAttentionFilters)) {
      return;
    }
    runtimeAttentionFilters[facet] = text(field.value) || "all";
    runtimeAttentionGroupPages.clear();
    renderRuntimeOperations();
  });
  document.addEventListener("click", (event) => {
    const historyButton = event.target?.closest?.("[data-runtime-attention-history-toggle]");
    if (historyButton) {
      event.preventDefault();
      runtimeAttentionShowHistory = !runtimeAttentionShowHistory;
      runtimeAttentionGroupPages.delete("retained_history");
      renderRuntimeOperations();
      return;
    }
    const clearButton = event.target?.closest?.("[data-runtime-attention-clear]");
    if (clearButton) {
      event.preventDefault();
      Object.keys(runtimeAttentionFilters).forEach((facet) => {
        runtimeAttentionFilters[facet] = "all";
      });
      runtimeAttentionGroupPages.clear();
      renderRuntimeOperations();
      return;
    }
    const pageButton = event.target?.closest?.("[data-runtime-attention-page-group]");
    if (!pageButton) {
      return;
    }
    event.preventDefault();
    const groupId = text(pageButton.dataset.runtimeAttentionPageGroup);
    const page = Math.max(1, Number(pageButton.dataset.runtimeAttentionPage || 1));
    runtimeAttentionGroupPages.set(groupId, page);
    renderRuntimeOperations();
    document
      .querySelector(`[data-attention-group="${cssEscape(groupId)}"]`)
      ?.scrollIntoView({ block: "start", behavior: "auto" });
  });
}

function renderRuntimeOperations() {
  if (!document.querySelector("[data-runtime-operations-view]")) {
    return;
  }

  const runtime = state.runtime_operations || {
    generated_at: null,
    agents: [],
    attention: [],
  };
  if (!runtimeFilterTouched || !runtimeFilterChoices.some(([value]) => value === runtimeFilter)) {
    runtimeFilter = runtime.attention.some((row) => runtimeAttentionGroup(row) !== "retained_history")
      ? "needs_attention"
      : "all";
  }

  const agents = runtimeAgentsForFilter(runtime.agents || []);
  const dueLeasedAgents = dueLeasedAgentsForFilter(runtime.agents || []);
  const visibleRuntimeAttention = (runtime.attention || []).filter(isVisibleRuntimeRow);
  const scopedAttention = runtimeAttentionForFilter(visibleRuntimeAttention, runtimeFilter);
  const attention = scopedAttention.filter(runtimeAttentionMatchesFacets);
  const allAttention = [...(runtime.attention || [])].sort(runtimeSortRows);
  const attentionCount = allAttention.length;
  const visibleAttentionCount = attention.length;

  const sections = [];

  if (runtimeFilter === "all") {
    sections.push(
      renderRuntimeSection({
        title: "Persistent runtime state",
        count: `${agents.length} visible`,
        emptyMessage: "No agent runtime data yet.",
        body: renderRuntimeAgentRows(agents, "No agent runtime data yet."),
      }),
    );
  } else if (runtimeFilter === "due_leased_agents") {
    sections.push(
      renderRuntimeSection({
        title: "Due / leased agents",
        count: `${dueLeasedAgents.length} visible`,
        emptyMessage: "No due or leased agents.",
        body: renderRuntimeAgentRows(dueLeasedAgents, "No due or leased agents."),
      }),
    );
  }

  elements.runtimeFilterButtons.forEach((button) => {
    const active = (button.dataset.runtimeFilter || "all") === runtimeFilter;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });

  if (runtimeFilter === "all" || runtimeFilter === "needs_attention") {
    sections.push(renderRuntimeAttentionControls(visibleRuntimeAttention));
    sections.push(renderRuntimeAttentionGroupedSections(attention));
  } else if (runtimeFilter === "blocked_waiting_work") {
    sections.push(
      renderRuntimeSection({
        title: "Blocked / waiting work",
        count: `${visibleAttentionCount} visible / ${attentionCount} total`,
        emptyMessage: "No blocked or waiting work.",
        body: renderRuntimeAttentionRows(attention, "No blocked or waiting work."),
      }),
    );
  } else if (runtimeFilter === "ephemeral_work") {
    sections.push(
      renderRuntimeSection({
        title: "Ephemeral work",
        count: `${visibleAttentionCount} visible / ${attentionCount} total`,
        emptyMessage: "No ephemeral work.",
        body: renderRuntimeAttentionRows(attention, "No ephemeral work."),
      }),
    );
  }

  renderIntoActiveTargets("[data-runtime-operations-view]", `
    <div class="runtime-filter-summary">Showing ${escapeHtml(runtimeFilterLabel(runtimeFilter))}.</div>
    ${sections.join("")}
    ${
      runtime.generated_at
        ? `<div class="meta-line runtime-generated-at">Runtime generated ${escapeHtml(runtime.generated_at)}</div>`
        : ""
    }
  `);
}

function renderRuntimeSection({ title, count, emptyMessage, body }) {
  return `
    <section class="runtime-section">
      <div class="runtime-section-header">
        <div>
          <p class="subheading">Runtime</p>
          <h3>${escapeHtml(title)}</h3>
        </div>
        <div class="runtime-count">${escapeHtml(count)}</div>
      </div>
      ${body || `<p class="empty-state">${escapeHtml(emptyMessage)}</p>`}
    </section>
  `;
}

function renderFocusedOrgChart() {
  if (!elements.orgFocusView) {
    return;
  }

  const payload = buildFocusedOrgChartPayload();
  const { teams, visibleTeams, focusedTeams, visibleAgents } = payload;
  if (selectedOrgTeamId !== "all" && !visibleTeams.some((team) => text(team.id) === text(selectedOrgTeamId))) {
    selectedOrgTeamId = "all";
  }
  const awakeCount = visibleAgents.filter((agent) => agentAwakeState(agentCardData(agent)).kind === "awake").length;
  const attentionCount = visibleAgents.filter((agent) => {
    const details = agentCardData(agent);
    return details.taskStats.blockedWaitingCount || details.inboxCount || agentRuntimeAttentionRows(agent.id).length;
  }).length;
  const dueCount = visibleAgents.filter((agent) => agentAwakeState(agentCardData(agent)).kind === "due").length;

  if (elements.orgFocusSummary) {
    elements.orgFocusSummary.innerHTML = [
      ["Teams", focusedTeams.length],
      ["Agents", visibleAgents.length],
      ["Awake", awakeCount],
      ["Due", dueCount],
      ["Attention", attentionCount],
    ]
      .map(
        ([label, value]) => `
          <div class="org-focus-metric">
            <strong>${escapeHtml(value)}</strong>
            <span>${escapeHtml(label)}</span>
          </div>
        `,
      )
      .join("");
  }

  syncOrgFocusTeamTabs(visibleTeams);

  if (!teams.length) {
    elements.orgFocusView.innerHTML = `<p class="empty-state">No teams registered.</p>`;
    return;
  }
  if (!visibleTeams.length) {
    elements.orgFocusView.innerHTML = `<p class="empty-state">No active team.</p>`;
    return;
  }

  renderTeamOrgFlow(payload.graph);
}

function syncOrgFocusTeamTabs(visibleTeams) {
  if (!elements.orgFocusTeamTabs) {
    return;
  }

  if (visibleTeams.length <= 1) {
    elements.orgFocusTeamTabs.innerHTML = "";
    if (elements.orgFocusTeamTabs.isConnected) {
      elements.orgFocusTeamTabs.remove();
    }
    return;
  }

  if (!elements.orgFocusTeamTabs.isConnected && elements.orgFocusSummary?.parentElement) {
    elements.orgFocusSummary.insertAdjacentElement("afterend", elements.orgFocusTeamTabs);
  }

  elements.orgFocusTeamTabs.innerHTML = renderOrgFocusTeamTabs(visibleTeams);
}

function buildFocusedOrgChartPayload() {
  const teams = values(state.teams);
  const visibleTeams = teams.filter(isVisibleTeam).sort(comparePeopleTeams);
  if (selectedOrgTeamId !== "all" && !visibleTeams.some((team) => text(team.id) === text(selectedOrgTeamId))) {
    selectedOrgTeamId = "all";
  }
  const focusedTeams =
    selectedOrgTeamId === "all"
      ? visibleTeams
      : visibleTeams.filter((team) => text(team.id) === text(selectedOrgTeamId));
  const focusedTeamIds = new Set(focusedTeams.map((team) => text(team.id)));
  const visibleAgents = values(state.agents).filter(
    (agent) => isVisibleAgent(agent) && focusedTeamIds.has(text(agent.team_id)),
  );
  return {
    teams,
    visibleTeams,
    focusedTeams,
    visibleAgents,
    graph: buildOrgFlowGraph(focusedTeams),
  };
}

function renderTeamOrgFlow(graph) {
  let root = elements.orgFocusView.querySelector("#team-org-flow-root");
  if (!root) {
    elements.orgFocusView.innerHTML = `<div id="team-org-flow-root" class="team-org-flow-root"></div>`;
    root = elements.orgFocusView.querySelector("#team-org-flow-root");
  }
  window.__TEAM_ORG_FLOW_PAYLOAD__ = graph;
  if (typeof window.renderTeamOrgFlow === "function") {
    window.renderTeamOrgFlow(graph);
    return;
  }
  window.dispatchEvent(new CustomEvent("team-org-flow:render", { detail: graph }));
}

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

function buildOrgFlowGraph(focusedTeams) {
  const nodes = [];
  const edges = [];
  const focusedTeamIds = new Set(focusedTeams.map((team) => text(team.id)));
  const governanceGroups = values(state.governance_groups)
    .filter(isVisibleGovernanceGroup)
    .filter((group) => (group.member_team_ids || []).some((teamId) => focusedTeamIds.has(text(teamId))))
    .sort(compareGovernanceGroups);
  const teamLayouts = [];
  let nextX = 0;
  const governanceRows = governanceGroups.length;
  const teamY = governanceRows ? governanceRows * orgFlowLayout.governanceRowGap + 48 : 0;
  const agentStartY = teamY + orgFlowLayout.teamToAgentGap;

  focusedTeams.forEach((team) => {
    const hierarchy = teamHierarchyGraph(team);
    const rootLeaves = Math.max(
      1,
      [...hierarchy.roots, ...hierarchy.orphanRoots]
        .map(hierarchyLeafCount)
        .reduce((total, count) => total + count, 0),
    );
    const width = Math.max(orgFlowLayout.leafGap, rootLeaves * orgFlowLayout.leafGap);
    const centerX = nextX + width / 2;
    const teamNodeId = orgFlowTeamNodeId(team.id);
    const teamAgents = values(state.agents).filter(
      (agent) => text(agent.team_id) === text(team.id) && isVisibleAgent(agent),
    );
    nodes.push({
      id: teamNodeId,
      type: "teamOrgNode",
      position: {
        x: centerX - orgFlowLayout.teamNodeWidth / 2,
        y: teamY,
      },
      sourcePosition: "bottom",
      targetPosition: "top",
      data: orgTeamNodeData(team, teamAgents),
    });
    layoutHierarchyRoots({
      roots: hierarchy.roots,
      nodes,
      edges,
      teamId: text(team.id),
      parentNodeId: teamNodeId,
      startY: agentStartY,
      leftX: centerX - (rootLeaves * orgFlowLayout.leafGap) / 2,
      edgeKind: "manager",
    });
    const normalRootLeaves = hierarchy.roots
      .map(hierarchyLeafCount)
      .reduce((total, count) => total + count, 0);
    layoutHierarchyRoots({
      roots: hierarchy.orphanRoots,
      nodes,
      edges,
      teamId: text(team.id),
      parentNodeId: teamNodeId,
      startY: agentStartY,
      leftX: centerX - (rootLeaves * orgFlowLayout.leafGap) / 2 + normalRootLeaves * orgFlowLayout.leafGap,
      edgeKind: "orphan",
    });
    teamLayouts.push({
      team,
      nodeId: teamNodeId,
      centerX,
    });
    nextX += width + orgFlowLayout.teamGap;
  });

  governanceGroups.forEach((group, index) => {
    const memberLayouts = teamLayouts.filter((layout) =>
      (group.member_team_ids || []).some((teamId) => text(teamId) === text(layout.team.id)),
    );
    if (!memberLayouts.length) {
      return;
    }
    const centerX =
      memberLayouts.reduce((total, layout) => total + layout.centerX, 0) / memberLayouts.length;
    const governanceNodeId = orgFlowGovernanceNodeId(group.id);
    nodes.push({
      id: governanceNodeId,
      type: "teamOrgNode",
      position: {
        x: centerX - orgFlowLayout.governanceNodeWidth / 2,
        y: index * orgFlowLayout.governanceRowGap,
      },
      sourcePosition: "bottom",
      targetPosition: "top",
      data: orgGovernanceNodeData(group, memberLayouts.length),
    });
    memberLayouts.forEach((layout) => {
      edges.push(orgFlowEdge(governanceNodeId, layout.nodeId, "governance"));
    });
  });

  return {
    nodes,
    edges,
    messageEvents: orgFlowMessageEvents(nodes),
    fitKey: orgFlowLayoutFitKey(nodes, edges),
  };
}

function orgFlowMessageEvents(nodes) {
  const visibleAgentIds = new Set(
    nodes
      .filter((node) => node.data?.kind === "agent")
      .map((node) => text(node.data.agentId))
      .filter(Boolean),
  );
  const events = [];
  visibleAgentIds.forEach((recipientAgentId) => {
    pendingInboxForAgent(recipientAgentId).forEach((item) => {
      const itemType = text(item.item_type);
      const senderAgentId = text(item.sender_agent_id);
      if (
        !["status_request", "progress_report"].includes(itemType) ||
        !senderAgentId ||
        senderAgentId === recipientAgentId ||
        !visibleAgentIds.has(senderAgentId)
      ) {
        return;
      }
      events.push({
        id: text(item.id),
        sourceNodeId: `agent:${senderAgentId}`,
        targetNodeId: `agent:${recipientAgentId}`,
        sourceLabel: agentLabel(state.agents[senderAgentId]),
        targetLabel: agentLabel(state.agents[recipientAgentId]),
        itemType,
        label: inboxItemDetail(item) || humanizeToken(itemType),
      });
    });
  });
  return events.filter((event) => event.id);
}

function orgFlowLayoutFitKey(nodes, edges) {
  return JSON.stringify({
    scope: selectedOrgTeamId,
    archived: showArchived,
    nodes: nodes.map((node) => [
      node.id,
      Math.round(Number(node.position?.x) || 0),
      Math.round(Number(node.position?.y) || 0),
    ]),
    edges: edges.map((edge) => [edge.source, edge.target, edge.className || ""]),
  });
}

function layoutHierarchyRoots({ roots, nodes, edges, teamId, parentNodeId, startY, leftX, edgeKind }) {
  let cursorX = leftX;
  roots.forEach((root) => {
    const leaves = hierarchyLeafCount(root);
    const rootNodeId = layoutHierarchyNode({
      node: root,
      nodes,
      edges,
      teamId,
      depth: 0,
      leftX: cursorX,
      startY,
      edgeKind,
    });
    if (rootNodeId) {
      edges.push(orgFlowEdge(parentNodeId, rootNodeId, edgeKind));
    }
    cursorX += leaves * orgFlowLayout.leafGap;
  });
}

function layoutHierarchyNode({ node, nodes, edges, teamId, depth, leftX, startY, edgeKind }) {
  if (!node) {
    return "";
  }
  const leaves = hierarchyLeafCount(node);
  const nodeId = orgFlowHierarchyNodeId(node, teamId);
  const nodeWidth = orgFlowLayout.agentNodeWidth;
  nodes.push({
    id: nodeId,
    type: "teamOrgNode",
    position: {
      x: leftX + (leaves * orgFlowLayout.leafGap) / 2 - nodeWidth / 2,
      y: startY + depth * orgFlowLayout.hierarchyLevelGap,
    },
    sourcePosition: "bottom",
    targetPosition: "top",
    data: orgHierarchyNodeData(node),
  });

  let childCursorX = leftX;
  (node.children || []).forEach((child) => {
    const childLeaves = hierarchyLeafCount(child);
    const childNodeId = layoutHierarchyNode({
      node: child,
      nodes,
      edges,
      teamId,
      depth: depth + 1,
      leftX: childCursorX,
      startY,
      edgeKind,
    });
    if (childNodeId) {
      edges.push(orgFlowEdge(nodeId, childNodeId, child.kind === "agent" ? "manager" : "warning"));
    }
    childCursorX += childLeaves * orgFlowLayout.leafGap;
  });
  return nodeId;
}

function hierarchyLeafCount(node) {
  const children = (node?.children || []).filter(Boolean);
  if (!children.length) {
    return 1;
  }
  return Math.max(
    1,
    children.map(hierarchyLeafCount).reduce((total, count) => total + count, 0),
  );
}

function orgFlowHierarchyNodeId(node, teamId) {
  if (node.kind === "agent") {
    return `agent:${text(node.id)}`;
  }
  return `warning:${text(teamId)}:${text(node.kind)}:${text(node.id)}:${text(node.parentId)}`;
}

function orgFlowTeamNodeId(teamId) {
  return `team:${text(teamId)}`;
}

function orgFlowGovernanceNodeId(groupId) {
  return `governance:${text(groupId)}`;
}

function orgFlowEdge(source, target, kind) {
  return {
    id: `edge:${source}:${target}:${kind}`,
    source,
    target,
    type: "smoothstep",
    className: `org-flow-edge--${kind}`,
    animated: false,
  };
}

function orgTeamNodeData(team, teamAgents) {
  const awakeCount = teamAgents.filter((agent) => agentAwakeState(agentCardData(agent)).kind === "awake").length;
  const attentionCount = teamAgents.filter((agent) => {
    const details = agentCardData(agent);
    return details.taskStats.blockedWaitingCount || details.inboxCount || agentRuntimeAttentionRows(agent.id).length;
  }).length;
  const cost = teamCostForTeam(team.id);
  return {
    kind: "team",
    teamId: text(team.id),
    name: text(team.name) || text(team.id),
    archived: Boolean(team.archived),
    awakeCount,
    attentionCount,
    costHtml: renderTeamCostCounter(team.id),
    costBlockedNoticeHtml: renderTeamCostBlockedNotice(team.id),
    costBlocked: Boolean(cost?.cost_limit_reached),
  };
}

function orgGovernanceNodeData(group, visibleMemberTeamCount) {
  const manager = state.agents?.[group.manager_agent_id] || null;
  const agenda = governanceAgendaForGroup(group.id);
  const pendingAgenda = agenda.filter((item) => item.state === "pending");
  const cost = governanceCostForGroup(group.id);
  const capReached = cost?.cost_limit_reached || (cost?.member_team_cap_reached_ids || []).length > 0;
  return {
    kind: "governance",
    groupId: text(group.id),
    name: text(group.name) || text(group.id),
    archived: Boolean(group.archived),
    managerLabel: manager ? agentLabel(manager) : text(group.manager_agent_id) || "Unassigned",
    managerAvatar: manager ? orgAgentAvatarData(agentCardData(manager)) : null,
    memberTeamCount: visibleMemberTeamCount,
    pendingAgendaCount: pendingAgenda.length,
    costAttention: Boolean(capReached),
    costHtml: cost ? renderGovernanceCostCounter(cost) : "",
  };
}

function orgHierarchyNodeData(node) {
  if (node.kind === "agent") {
    return orgAgentNodeData(node.agentData);
  }
  const detailLines = [];
  if (node.parentId) {
    detailLines.push(`Referenced by ${node.parentId}`);
  }
  if (node.teamId) {
    detailLines.push(`Team ${node.teamId}`);
  }
  return {
    kind: "warning",
    label: node.label,
    detailLines,
  };
}

function orgAgentNodeData(agentData) {
  const awake = agentAwakeState(agentData);
  const work = orgNodeWorkSummaryData(agentData);
  const activityTone = activityToneClass(agentData.activitySummary?.tone);
  const cardClasses = [
    "hierarchy-node-card",
    "org-focus-node-card",
    `org-focus-node-card--${awake.kind}`,
    activityTone ? `org-focus-node-card--activity-${activityTone}` : "",
    agentData.taskStats.blockedWaitingCount || agentData.inboxCount ? "org-focus-node-card--attention" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const indicatorItems = agentData.inboxIndicatorItems || {};
  const statusRequestItems = indicatorItems.status_request || [];
  const progressReportItems = indicatorItems.progress_report || [];
  return {
    kind: "agent",
    agentId: agentData.id,
    label: agentData.label,
    idLabel: agentData.label !== agentData.id ? agentData.id : null,
    role: agentData.role,
    status: agentData.status,
    cardClassName: cardClasses,
    avatar: orgAgentAvatarData(agentData),
    awake,
    inboxIndicators: [
      {
        itemType: "status_request",
        direction: "down",
        label: "Status requests",
        count: statusRequestItems.length,
        items: statusRequestItems.map(orgInboxIndicatorItemData),
      },
      {
        itemType: "progress_report",
        direction: "up",
        label: "Progress reports",
        count: progressReportItems.length,
        items: progressReportItems.map(orgInboxIndicatorItemData),
      },
    ],
    work,
    activity: {
      headline: text(agentData.activitySummary?.headline) || work.primary,
      tone: activityTone || (awake.kind === "awake" ? "working" : awake.kind),
    },
    detailLines: hierarchyDetailLines(agentData, { focused: true }),
  };
}

function orgInboxIndicatorItemData(item) {
  const metaParts = [];
  if (item.task_id) {
    metaParts.push(`task ${item.task_id}`);
  }
  if (item.sender_agent_id) {
    metaParts.push(`from ${item.sender_agent_id}`);
  }
  metaParts.push(inboxPriorityLabel(item));
  metaParts.push(inboxReplyIntentLabel(item));
  return {
    detail: inboxItemDetail(item) || item.id,
    meta: metaParts.join(" - "),
  };
}

function orgAgentAvatarData(agentData) {
  const label = agentLabel(agentData);
  const src = avatarUrl(agentData);
  return {
    src,
    alt: src ? `${label} avatar` : "",
    initials: agentInitials(agentData),
  };
}

function renderOrgFocusTeamTabs(visibleTeams) {
  if (visibleTeams.length <= 1) {
    return "";
  }
  const buttons = [
    renderOrgFocusTeamTab({
      id: "all",
      label: "All teams",
      count: visibleTeams.length,
      selected: selectedOrgTeamId === "all",
    }),
    ...visibleTeams.map((team) => {
      const teamAgents = values(state.agents).filter(
        (agent) => text(agent.team_id) === text(team.id) && isVisibleAgent(agent),
      );
      return renderOrgFocusTeamTab({
        id: team.id,
        label: team.name || team.id,
        count: teamAgents.length,
        selected: text(selectedOrgTeamId) === text(team.id),
      });
    }),
  ];
  return buttons.join("");
}

function renderOrgFocusTeamTab({ id, label, count, selected }) {
  return `
    <button
      type="button"
      class="org-focus-team-tab${selected ? " active" : ""}"
      role="tab"
      aria-selected="${selected ? "true" : "false"}"
      data-org-team-id="${escapeAttr(id)}"
    >
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(count)}</strong>
    </button>
  `;
}

function renderHierarchy() {
  const teams = values(state.teams);
  const visibleTeams = teams.filter(isVisibleTeam);
  if (!teams.length) {
    renderIntoActiveTargets("[data-hierarchy-view]", `<p class="empty-state">No teams registered.</p>`);
    return;
  }
  if (!visibleTeams.length) {
    renderIntoActiveTargets("[data-hierarchy-view]", `<p class="empty-state">No active team.</p>`);
    return;
  }

  renderIntoActiveTargets(
    "[data-hierarchy-view]",
    visibleTeams
      .map((team) => {
        const hierarchy = teamHierarchyGraph(team);
        return `
        <article class="hierarchy-team">
            <div class="hierarchy-team-header">
              <div>
                <p class="section-label">${escapeHtml(team.id)}</p>
                <h3>${escapeHtml(team.name)}</h3>
                ${renderTeamCostCounter(team.id)}
                ${renderTeamCostBlockedNotice(team.id)}
              </div>
              <div class="record-actions">
                ${team.archived ? `<span class="state-pill archived">archived</span>` : ""}
                ${renderTeamCostBlockedPill(team.id)}
                ${editButton("team", team.id)}
              </div>
            </div>
          <div class="hierarchy-chart">
            ${renderHierarchyForest(hierarchy.roots)}
            ${
              hierarchy.orphanRoots.length
                ? `
                  <section class="hierarchy-fallbacks" aria-label="Unreached agents">
                    <p class="subheading">Unreached agents</p>
                    ${renderHierarchyForest(hierarchy.orphanRoots)}
                  </section>
                `
                : ""
            }
          </div>
        </article>
      `;
      })
      .join(""),
  );
}

function renderHierarchyForest(nodes, options = {}) {
  if (!nodes.length) {
    return `<p class="empty-state">No reporting tree.</p>`;
  }
  return `
    <ol class="hierarchy-tree">
      ${nodes.map((node) => renderHierarchyNode(node, options)).join("")}
    </ol>
  `;
}

function renderHierarchyNode(node, options = {}) {
  if (!node) {
    return "";
  }
  if (node.kind === "agent") {
    const children = (node.children || []).length
      ? `<ol class="hierarchy-children">${node.children.map((child) => renderHierarchyNode(child, options)).join("")}</ol>`
      : "";
    const focused = Boolean(options.focused);
    const descriptionId = `team-${focused ? "org" : "hierarchy"}-agent-${domIdPart(node.agentData.id || node.agentData.label)}-description`;
    const cardClasses = [
      "hierarchy-node-card",
      focused ? "org-focus-node-card" : "",
      focused ? `org-focus-node-card--${agentAwakeState(node.agentData).kind}` : "",
      focused && activityToneClass(node.agentData.activitySummary?.tone)
        ? `org-focus-node-card--activity-${activityToneClass(node.agentData.activitySummary?.tone)}`
        : "",
      node.agentData.taskStats.blockedWaitingCount || node.agentData.inboxCount ? "org-focus-node-card--attention" : "",
    ]
      .filter(Boolean)
      .join(" ");
    const actionAttrs = focused
      ? `role="button" data-edit-type="agent" data-edit-id="${escapeAttr(node.agentData.id)}" aria-label="Edit ${escapeAttr(node.agentData.label)}"`
      : "";
    return `
      <li class="hierarchy-branch">
        <article class="${cardClasses}" tabindex="0" aria-describedby="${escapeAttr(descriptionId)}" ${actionAttrs}>
          <div class="hierarchy-node-summary">
            <span class="hierarchy-node-avatar-wrap">
              ${renderAgentAvatar(node.agentData)}
              <span class="hierarchy-node-avatar-card" aria-hidden="true">
                <strong class="hierarchy-node-avatar-name">${escapeHtml(node.agentData.label)}</strong>
                ${
                  node.agentData.label !== node.agentData.id
                    ? `<span class="hierarchy-node-avatar-id">${escapeHtml(node.agentData.id)}</span>`
                    : ""
                }
                <span class="hierarchy-node-avatar-role">${escapeHtml(node.agentData.role)}</span>
              </span>
            </span>
            <div class="hierarchy-node-title">
              <strong>${escapeHtml(node.agentData.label)}</strong>
            </div>
            <div class="hierarchy-node-subtitle">
              <span class="hierarchy-node-role">${escapeHtml(node.agentData.role)}</span>
              ${renderAgentRosterStatusPill(node.agentData)}
              ${focused ? renderAgentAwakePill(node.agentData) : ""}
              ${renderAgentInboxHeaderIndicators(node.agentData)}
            </div>
          </div>
          <span id="${escapeAttr(descriptionId)}" class="visually-hidden">Agent ID ${escapeHtml(node.agentData.id)}. Role ${escapeHtml(node.agentData.role)}.</span>
          ${focused ? renderOrgNodeWorkSummary(node.agentData) : ""}
          <div class="hierarchy-node-details${focused ? " org-node-hover-panel" : ""}">${renderHierarchyDetails(node.agentData, { focused })}</div>
        </article>
        ${children}
      </li>
    `;
  }
  if (node.kind === "missing-manager" || node.kind === "missing-subordinate" || node.kind === "cycle") {
    const detailLines = [];
    if (node.parentId) {
      detailLines.push(`Referenced by ${node.parentId}`);
    }
    if (node.teamId) {
      detailLines.push(`Team ${node.teamId}`);
    }
    return `
      <li class="hierarchy-branch">
        <article class="hierarchy-node-card hierarchy-node-card--warning" tabindex="0">
          <div class="hierarchy-node-summary">
            <div class="hierarchy-node-title">
              <strong>${escapeHtml(node.label)}</strong>
            </div>
            <div class="hierarchy-node-subtitle">
              <span class="state-pill warning">warning</span>
            </div>
          </div>
          ${
            detailLines.length
              ? `<div class="hierarchy-node-details">${detailLines
                  .map((line) => `<div class="meta-line">${escapeHtml(line)}</div>`)
                  .join("")}</div>`
              : ""
          }
        </article>
      </li>
    `;
  }
  return "";
}

function renderAgents() {
  try {
    const allAgents = values(state.agents).sort(comparePeopleAgents);
    const agents = allAgents.filter(isVisibleAgent);
    if (activeTeamView === "people") {
      renderPeopleAgents(allAgents, agents);
      return;
    }
    renderIntoActiveTargets("[data-agent-detail-view]", "");
    renderIntoActiveTargets("[data-teams-records-view]", "");
    if (!allAgents.length) {
      renderIntoActiveTargets("[data-agents-view]", `<p class="empty-state">No agents registered.</p>`);
      return;
    }
    if (!agents.length) {
      renderIntoActiveTargets("[data-agents-view]", `<p class="empty-state">No visible agents.</p>`);
      return;
    }
    renderIntoActiveTargets("[data-agents-view]", `
      <ul class="agent-list">
        ${agents
          .map((agent) => {
            const details = agentCardData(agent);
            return `
                <li class="agent-item">
                  <div class="agent-line">
                    <div class="agent-title-with-avatar">
                      ${renderAgentAvatar(details)}
                      <strong>${escapeHtml(details.label)}</strong>
                    </div>
                    <div class="record-actions">
                      ${renderAgentRosterStatusPill(details)}
                      ${renderAgentStandDownControls(details.id, details)}
                      ${renderOpenAgentSessionButton(details.sessionId)}
                      ${["every_wake", "work_lineage"].includes(details.runtime.contextClearingPolicy) ? "" : `<button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(details.id)}">Clear context</button>`}
                      ${editButton("agent", details.id)}
                    </div>
                  </div>
                <div class="meta-line">${escapeHtml(details.role)} - ${escapeHtml(details.id)}</div>
                <div class="meta-line">Reports to ${escapeHtml(details.reportsToLabel)} - ${details.taskStats.openCount} open task(s) - max ephemeral ${details.ephemeralCapacity}</div>
                ${details.standDownActive ? `<div class="meta-line">${escapeHtml(agentStandDownSummary(details))}</div>` : ""}
                ${renderAgentOpenTasks(details.taskStats.openTasks)}
                ${renderAgentInbox(agent.id)}
              </li>
            `;
          })
          .join("")}
      </ul>
    `);
  } finally {
    applyLockedMutationControls();
  }
}

function renderPeopleAgents(allAgents, agents) {
  renderIntoActiveTargets("[data-agents-view]", "");
  const visibleTeams = peopleViewTeams();
  const visibleTeamIds = new Set(visibleTeams.map((team) => text(team.id)));
  const teamAgents = agents.filter((agent) => isTeamInPeopleScope(agent, visibleTeamIds));
  const allProjects = values(state.projects).sort(compareTeamProjects);
  const projects = allProjects.filter((project) => isVisibleProject(project) && visibleTeamIds.has(text(project.team_id)));
  const allRoles = values(state.roles).sort(compareTeamRoles);
  const roles = allRoles.filter((role) => isVisibleRole(role) && visibleTeamIds.has(text(role.team_id)));

  if (!visibleTeams.length) {
    clearSelectedTeamRecord();
    renderIntoActiveTargets("[data-teams-records-view]", `<p class="empty-state">No visible teams.</p>`);
    renderIntoActiveTargets("[data-agent-detail-view]", renderTeamRecordDetail(null));
    return;
  }

  if (!selectedTeamRecordId) {
    const firstTeam = visibleTeams.find((team) => text(team.id) !== noTeamRecordId);
    if (firstTeam) {
      selectTeamRecord("team", firstTeam.id, { rerender: false });
    }
  }

  const scopedTeams = visibleTeams;
  const scopedTeamIds = visibleTeamIds;
  const scopedRecords = {
    agents: teamAgents.sort(comparePeopleAgents),
    projects: projects.filter((project) => scopedTeamIds.has(text(project.team_id))).sort(compareTeamProjects),
    roles: roles.filter((role) => scopedTeamIds.has(text(role.team_id))).sort(compareTeamRoles),
  };
  const selectedRecord = selectedTeamRecordFrom(scopedRecords);
  const hasRecords = scopedRecords.agents.length || scopedRecords.projects.length || scopedRecords.roles.length;

  if (!hasRecords && selectedRecord?.kind !== "team") {
    clearSelectedTeamRecord();
    renderIntoActiveTargets(
      "[data-teams-records-view]",
      "<p class=\"empty-state\">No visible records.</p>",
    );
    renderIntoActiveTargets("[data-agent-detail-view]", renderTeamRecordDetail(null));
    return;
  }

  renderIntoActiveTargets("[data-teams-records-view]", `
    <div class="team-record-groups">
      ${scopedTeams.map((team) => renderTeamRecordGroup(team, scopedRecords)).join("")}
    </div>
  `);
  renderIntoActiveTargets("[data-agent-detail-view]", renderTeamRecordDetail(selectedRecord));
}

function renderTeamRecordGroup(team, scopedRecords) {
  const teamId = text(team.id);
  const teamLabel = isNoTeamRecord(team) ? noTeamRecordName : teamId;
  const teamTitle = isNoTeamRecord(team) ? noTeamRecordName : text(team.name || teamId);
  const isNoTeam = isNoTeamRecord(team);
  const activeKind = isNoTeam ? "agent" : normalizedTeamRecordKind(teamRecordGroupTabs[teamId] || "agent");
  teamRecordGroupTabs[teamId] = activeKind;
  const teamRecords = teamRecordsForTeam(teamId, scopedRecords);
  const expanded = expandedTeamRecordGroupIds.has(teamId);
  const selected = selectedTeamRecordKind === "team" && text(selectedTeamRecordId) === text(teamId);
  const counts = [
    `${teamRecords.agents.length} agent${teamRecords.agents.length === 1 ? "" : "s"}`,
  ];
  if (!isNoTeam) {
    counts.push(
      `${teamRecords.projects.length} project${teamRecords.projects.length === 1 ? "" : "s"}`,
      `${teamRecords.roles.length} role${teamRecords.roles.length === 1 ? "" : "s"}`,
    );
  }
  return `
    <article class="team-record-group${expanded ? " is-expanded" : " is-collapsed"}${selected ? " is-selected" : ""}" data-team-record-group-id="${escapeAttr(teamId)}">
      <button
        class="team-record-group-header"
        type="button"
        aria-expanded="${expanded ? "true" : "false"}"
        aria-pressed="${selected ? "true" : "false"}"
        data-team-record-toggle="${escapeAttr(teamId)}"
      >
        <span class="team-record-group-title">
          <span class="section-label">${escapeHtml(teamLabel)}</span>
          <strong>${escapeHtml(teamTitle)}</strong>
        </span>
        <span class="team-record-group-counts">
          ${counts.map((count) => `<span class="state-pill neutral">${escapeHtml(count)}</span>`).join("")}
        </span>
      </button>
      ${
        expanded
          ? `<div class="team-record-group-body">
              ${renderTeamRecordKindTabs(teamId, teamRecords, activeKind)}
              ${renderTeamRecordSection(activeKind, teamRecords)}
            </div>`
          : ""
      }
    </article>
  `;
}

function renderTeamRecordKindTabs(teamId, teamRecords, activeKind) {
  const isNoTeam = isNoTeamRecord({ id: text(teamId) });
  const tabs = isNoTeam
    ? [["agent", "Agents", teamRecords.agents.length]]
    : [
      ["agent", "Agents", teamRecords.agents.length],
      ["project", "Projects", teamRecords.projects.length],
      ["role", "Roles", teamRecords.roles.length],
    ];
  return `
    <div class="team-record-kind-tabs" role="tablist" aria-label="${escapeAttr(`Records for ${teamId}`)}">
      ${tabs
        .map(([kind, label, count]) => {
          const active = kind === activeKind;
          return `
            <button
              type="button"
              class="team-record-kind-tab${active ? " active" : ""}"
              role="tab"
              aria-selected="${active ? "true" : "false"}"
              data-team-record-group-id="${escapeAttr(teamId)}"
              data-team-record-tab="${escapeAttr(kind)}"
            >
              <span>${escapeHtml(label)}</span>
              <strong>${escapeHtml(count)}</strong>
            </button>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderTeamRecordSection(kind, teamRecords) {
  const config = teamRecordSectionConfig(kind);
  const records = teamRecords[config.collection] || [];
  const pageKey = teamRecordPageKey(teamRecords.teamId, kind);
  const totalPages = Math.max(1, Math.ceil(records.length / teamRecordPageSize));
  const page = clampPage(teamRecordGroupPages[pageKey] || 1, totalPages);
  teamRecordGroupPages[pageKey] = page;
  const pageRecords = records.slice((page - 1) * teamRecordPageSize, page * teamRecordPageSize);
  const isNoTeam = isNoTeamRecord({ id: teamRecords.teamId });
  const canCreate = config.createLabel && (!isNoTeam || normalizedTeamRecordKind(kind) === "agent");
  const createOpen = canCreate && isTeamInlineCreateFormOpen(teamRecords.teamId, kind);
  return `
    <section class="team-record-section" aria-label="${escapeAttr(config.title)}">
      <div class="team-record-section-header">
        <div class="team-record-section-title">
          <h3>${escapeHtml(config.title)}</h3>
          <span>${escapeHtml(records.length)}</span>
        </div>
        ${
          canCreate
            ? `<button
                type="button"
                class="team-inline-create-toggle"
                aria-expanded="${createOpen ? "true" : "false"}"
                data-team-inline-create-toggle
                data-team-record-group-id="${escapeAttr(teamRecords.teamId)}"
                data-team-record-kind="${escapeAttr(kind)}"
              >${escapeHtml(createOpen ? "Close" : config.createLabel)}</button>`
            : ""
        }
      </div>
      ${renderTeamInlineCreateForm(teamRecords.teamId, kind)}
      ${
        records.length
          ? `<ul class="team-record-list compact-record-list">${pageRecords.map((record) => config.render(record)).join("")}</ul>
             ${renderTeamRecordPager(teamRecords.teamId, kind, page, totalPages, records.length)}`
          : `<p class="empty-state">${escapeHtml(config.emptyMessage)}</p>`
      }
    </section>
  `;
}

function teamRecordSectionConfig(kind) {
  if (kind === "project") return { title: "Projects", collection: "projects", render: renderSelectableProject, emptyMessage: "No projects.", createLabel: "Add project" };
  if (kind === "role") return { title: "Roles", collection: "roles", render: renderSelectableRole, emptyMessage: "No roles.", createLabel: "Add role" };
  return { title: "Agents", collection: "agents", render: renderSelectableAgent, emptyMessage: "No agents.", createLabel: "Add agent" };
}

function renderTeamInlineCreateForm(teamId, kind) {
  const normalizedTeamId = text(teamId);
  const normalizedKind = normalizedTeamRecordKind(kind);
  if (!normalizedTeamId || !isTeamInlineCreateFormOpen(normalizedTeamId, normalizedKind)) {
    return "";
  }
  if (normalizedKind === "project") {
    return renderTeamInlineProjectForm(normalizedTeamId);
  }
  if (normalizedKind === "role") {
    return renderTeamInlineRoleForm(normalizedTeamId);
  }
  return renderTeamInlineAgentForm(normalizedTeamId);
}

function renderTeamInlineProjectForm(teamId) {
  const formId = teamInlineCreateDomId(teamId, "project");
  return `
    <form
      id="${escapeAttr(formId)}"
      class="team-inline-create-form"
      data-team-inline-create-form
      data-form-id="create-project-form"
      data-endpoint="/projects"
      data-method="POST"
      data-team-record-group-id="${escapeAttr(teamId)}"
      data-team-record-kind="project"
    >
      <input type="hidden" name="team_id" value="${escapeAttr(teamId)}" />
      <div class="team-inline-create-fields">
        <label>Name <input name="name" required /></label>
        <label>Project lead <select name="lead_agent_id" required data-options="project-lead-agents" data-placeholder="Select team agent">${renderSelectOptions(
          projectLeadAgentOptionsForTeam(teamId),
          { placeholder: "Select unassigned team agent" },
        )}</select></label>
        <label>Directory scope <input name="directory_path" value="root" required /></label>
        <label>Goal <textarea name="goal_description" rows="2"></textarea></label>
      </div>
      <details class="team-inline-create-advanced" ${teamDisclosureAttributes(teamDisclosureKey("people", "inline-project-create", teamId, "git"))}>
        <summary>Git config</summary>
        <div class="team-inline-create-fields">
          <label>Repository source <input name="repo_source" placeholder="Git URL or local checkout" /></label>
          ${gitImportControls()}
          <label>Managed repository ID <input name="managed_repo_id" placeholder="mr_..." /></label>
          <label>Default base branch for new tasks <input name="base_branch" placeholder="main" /></label>
          <label>Canonical release branch prefix <input name="canonical_release_branch_prefix" /></label>
          <label>Agent commit author name <input name="agent_author_name" placeholder="Evander Bot" /></label>
          <label>Agent commit author email <input name="agent_author_email" placeholder="evander@quinth.se" /></label>
          <label>Agent commit committer name <input name="agent_committer_name" placeholder="Evander Bot" /></label>
          <label>Agent commit committer email <input name="agent_committer_email" placeholder="evander@quinth.se" /></label>
        </div>
      </details>
      <details class="team-inline-create-advanced" ${teamDisclosureAttributes(teamDisclosureKey("people", "inline-project-create", teamId, "release-gates"))}>
        <summary>Release gates</summary>
        <label>Automated and agent-review policy JSON<textarea name="release_policy_json" rows="10" placeholder='{"verification_commands": [...], "review_requirements": [...]}'></textarea></label>
        ${renderHumanReviewPolicyEditor()}
        <p class="field-help">Git releases fail closed until this policy defines disposable verification commands and/or release reviewers.</p>
      </details>
      ${renderTeamInlineCreateActions(teamId, "project", "Create project")}
    </form>
  `;
}

function renderTeamInlineRoleForm(teamId) {
  const formId = teamInlineCreateDomId(teamId, "role");
  return `
    <form
      id="${escapeAttr(formId)}"
      class="team-inline-create-form"
      data-team-inline-create-form
      data-form-id="create-role-form"
      data-endpoint="/roles"
      data-method="POST"
      data-team-record-group-id="${escapeAttr(teamId)}"
      data-team-record-kind="role"
    >
      <input type="hidden" name="team_id" value="${escapeAttr(teamId)}" />
      <div class="team-inline-create-fields">
        <label>Project <select name="project_id">${renderSelectOptions(teamProjectOptions(teamId), { placeholder: "Team-wide" })}</select></label>
        <label>Name <input name="name" required /></label>
        <label>Description <textarea name="description" rows="2"></textarea></label>
        <label>Permissions <select name="permissions" multiple size="3">${renderMultiSelectOptions(rolePermissionOptions())}</select></label>
        <label>Max ephemeral agents <input name="max_concurrent" type="number" min="0" value="0" /></label>
      </div>
      ${renderTeamInlineCreateActions(teamId, "role", "Create role")}
    </form>
  `;
}

function renderTeamInlineAgentForm(teamId) {
  const formId = teamInlineCreateDomId(teamId, "agent");
  const normalizedTeamId = text(teamId);
  const visibleTeamId = isNoTeamRecord({ id: normalizedTeamId }) ? "" : normalizedTeamId;
  const createSessionDisabled = teamPrivilegedUnlocked()
    ? ""
    : lockedAttrs("create a core agent session");
  return `
    <form
      id="${escapeAttr(formId)}"
      class="team-inline-create-form"
      data-team-inline-create-form
      data-form-id="register-agent-form"
      data-endpoint="/agents"
      data-method="POST"
      data-team-record-group-id="${escapeAttr(teamId)}"
      data-team-record-kind="agent"
    >
      <input type="hidden" name="team_id" value="${escapeAttr(visibleTeamId)}" />
      <div class="team-inline-create-fields">
        <label>
          Existing agent defaults
          <select
            name="existing_agent_id"
            data-agent-source-picker
            data-session-field="session_id"
            data-display-field="display_name"
            data-role-field="role_id"
            data-project-field="project_id"
            data-reports-to-field="reports_to_agent_id"
            data-max-concurrent-field="max_concurrent"
          >${renderSelectOptions(teamAgentOptions(teamId), { placeholder: "Select existing agent" })}</select>
        </label>
        <label>Project <select name="project_id">${renderSelectOptions(teamProjectOptions(teamId), { placeholder: "No project" })}</select></label>
        <label>Role <select name="role_id">${renderSelectOptions(teamRoleOptions(teamId), { placeholder: "No role" })}</select></label>
        <div class="team-agent-session-controls">
          <label>
            Agent session
            <select
              name="existing_session_id"
              data-core-agent-session-picker
              data-session-field="session_id"
            >${renderCoreAgentSessionOptions()}</select>
          </label>
          <div class="team-agent-session-actions">
            <button
              type="button"
              class="secondary"
              data-core-agent-session-refresh
              data-tooltip-key="existing_session_id"
            >Refresh sessions</button>
            <button
              type="button"
              class="secondary"
              data-core-agent-session-create
              data-tooltip-key="create_session"
              ${createSessionDisabled}
            >Create new session</button>
          </div>
        </div>
        <label>Manual session ID <input name="session_id" required placeholder="Select or create a session" /></label>
        <label>Display name <input name="display_name" /></label>
        <label>Reports to <select name="reports_to_agent_id">${renderSelectOptions(teamAgentOptions(teamId), { placeholder: "No superior" })}</select></label>
        <label>Max ephemeral agents <input name="max_concurrent" type="number" min="0" value="0" /></label>
      </div>
      ${renderTeamInlineCreateActions(teamId, "agent", "Register agent")}
    </form>
  `;
}

function renderTeamInlineCreateActions(teamId, kind, submitLabel) {
  return `
    <div class="team-inline-create-actions">
      <button type="submit">${escapeHtml(submitLabel)}</button>
      <button
        type="button"
        class="secondary"
        data-team-inline-create-close
        data-team-record-group-id="${escapeAttr(teamId)}"
        data-team-record-kind="${escapeAttr(kind)}"
      >Cancel</button>
    </div>
  `;
}

function renderSelectOptions(options, { selectedValue = "", placeholder = "Default", includePlaceholder = true } = {}) {
  const selected = text(selectedValue);
  return `${includePlaceholder ? `<option value="">${escapeHtml(placeholder)}</option>` : ""}${options
    .map(([value, label, tooltip]) => {
      const optionValue = text(value);
      const optionTooltip = text(tooltip);
      return `<option value="${escapeAttr(optionValue)}"${optionTooltip ? ` title="${escapeAttr(optionTooltip)}"` : ""}${optionValue === selected ? " selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("")}`;
}

function renderMultiSelectOptions(options, selectedValues = []) {
  const selected = new Set(splitList(selectedValues));
  return options
    .map(([value, label, tooltip]) => {
      const optionValue = text(value);
      const optionTooltip = text(tooltip);
      return `<option value="${escapeAttr(optionValue)}"${optionTooltip ? ` title="${escapeAttr(optionTooltip)}"` : ""}${selected.has(optionValue) ? " selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");
}

function teamProjectOptions(teamId) {
  const normalizedTeamId = text(teamId);
  return values(state.projects)
    .filter((project) => isVisibleProject(project) && text(project.team_id) === normalizedTeamId)
    .sort(compareTeamProjects)
    .map((project) => [project.id, `${projectLabel(project)} (${project.id})`]);
}

function teamRoleOptions(teamId) {
  const normalizedTeamId = text(teamId);
  return values(state.roles)
    .filter((role) => isVisibleRole(role) && text(role.team_id) === normalizedTeamId)
    .sort(compareTeamRoles)
    .map((role) => [role.id, `${roleLabel(role)} (${role.id})`]);
}

function teamAgentOptions(teamId) {
  const normalizedTeamId = text(teamId);
  const agentFilter = isNoTeamRecord({ id: normalizedTeamId })
    ? (agent) => isVisibleAgent(agent) && !text(agent.team_id)
    : (agent) => isVisibleAgent(agent) && text(agent.team_id) === normalizedTeamId;
  return values(state.agents)
    .filter(agentFilter)
    .sort(comparePeopleAgents)
    .map((agent) => [agent.id, `${agentLabel(agent)} (${agent.id})`]);
}

function rolePermissionOptions() {
  return (state.available_role_permissions || []).map((permission) => [
    permission,
    permissionLabel(permission),
    rolePermissionTooltip(permission),
  ]);
}

function contextClearingPolicyValue(runtime) {
  const policy = text(runtime?.context_clearing_policy);
  if (policy) {
    return policy;
  }
  return Boolean(runtime?.auto_context_clearing_enabled) ? "idle" : "off";
}

function contextClearingPolicyLabel(policy) {
  return contextClearingPolicyLabels[text(policy)] || "Off";
}

function contextClearingIdleGraceMinutesForEdit(runtime) {
  const configuredSeconds = numberOrZero(runtime?.context_clearing_idle_grace_seconds);
  return configuredSeconds / 60;
}

function teamInlineCreateKey(teamId, kind) {
  return `${text(teamId)}:${normalizedTeamRecordKind(kind)}`;
}

function teamInlineCreateDomId(teamId, kind) {
  return `team-inline-create-${normalizedTeamRecordKind(kind)}-${text(teamId).replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

function isTeamInlineCreateFormOpen(teamId, kind) {
  return openTeamInlineCreateForms.has(teamInlineCreateKey(teamId, kind));
}

function toggleTeamInlineCreateForm(teamId, kind) {
  const normalizedTeamId = text(teamId);
  const normalizedKind = normalizedTeamRecordKind(kind);
  if (!normalizedTeamId) {
    return;
  }
  const key = teamInlineCreateKey(normalizedTeamId, normalizedKind);
  let refreshAgentSessions = false;
  if (openTeamInlineCreateForms.has(key)) {
    openTeamInlineCreateForms.delete(key);
  } else {
    openTeamInlineCreateForms.add(key);
    expandedTeamRecordGroupIds.add(normalizedTeamId);
    teamRecordGroupTabs[normalizedTeamId] = normalizedKind;
    if (normalizedKind === "agent") {
      refreshAgentSessions = true;
    }
  }
  renderAgents();
  decorateFieldTooltips(document);
  if (refreshAgentSessions) {
    void refreshCoreAgentSessionsForForm(
      document.getElementById(teamInlineCreateDomId(normalizedTeamId, "agent")),
      { force: false },
    );
  }
}

function closeTeamInlineCreateForm(teamId, kind, { rerender = true } = {}) {
  openTeamInlineCreateForms.delete(teamInlineCreateKey(teamId, kind));
  if (rerender) {
    renderAgents();
    decorateFieldTooltips(document);
  }
}

function closeTeamInlineCreateFormFor(form, { rerender = true } = {}) {
  if (!form?.hasAttribute?.("data-team-inline-create-form")) {
    return;
  }
  closeTeamInlineCreateForm(
    form.dataset.teamRecordGroupId,
    form.dataset.teamRecordKind,
    { rerender },
  );
}

function renderTeamRecordPager(teamId, kind, page, totalPages, totalRecords) {
  if (totalPages <= 1) {
    return `<div class="team-record-pager-summary">Showing ${escapeHtml(totalRecords)} ${escapeHtml(kind)}${totalRecords === 1 ? "" : "s"}</div>`;
  }
  return `
    <div class="team-record-pager" aria-label="${escapeAttr(kind)} pages">
      <button type="button" data-team-record-group-id="${escapeAttr(teamId)}" data-team-record-kind="${escapeAttr(kind)}" data-team-record-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button>
      <span>Page ${escapeHtml(page)} of ${escapeHtml(totalPages)} · ${escapeHtml(totalRecords)} total</span>
      <button type="button" data-team-record-group-id="${escapeAttr(teamId)}" data-team-record-kind="${escapeAttr(kind)}" data-team-record-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>Next</button>
    </div>
  `;
}

function toggleTeamRecordGroup(teamId) {
  const normalizedTeamId = text(teamId);
  if (!normalizedTeamId) return;
  if (expandedTeamRecordGroupIds.has(normalizedTeamId)) {
    expandedTeamRecordGroupIds.delete(normalizedTeamId);
  } else {
    expandedTeamRecordGroupIds.add(normalizedTeamId);
  }
  renderAgents();
}

function setTeamRecordGroupTab(teamId, kind) {
  const normalizedTeamId = text(teamId);
  if (!normalizedTeamId) return;
  teamRecordGroupTabs[normalizedTeamId] = normalizedTeamRecordKind(kind);
  teamRecordGroupPages[teamRecordPageKey(normalizedTeamId, teamRecordGroupTabs[normalizedTeamId])] = 1;
  renderAgents();
}

function setTeamRecordGroupPage(teamId, kind, page) {
  const normalizedTeamId = text(teamId);
  if (!normalizedTeamId || !Number.isFinite(page)) return;
  teamRecordGroupPages[teamRecordPageKey(normalizedTeamId, normalizedTeamRecordKind(kind))] = Math.max(1, Math.floor(page));
  renderAgents();
}

function normalizedTeamRecordKind(kind) {
  if (kind === "project" || kind === "role") return kind;
  return "agent";
}

function teamRecordPageKey(teamId, kind) {
  return `${text(teamId)}:${normalizedTeamRecordKind(kind)}`;
}

function clampPage(page, totalPages) {
  return Math.min(Math.max(1, Number(page) || 1), Math.max(1, totalPages));
}

function teamRecordsForTeam(teamId, records) {
  const normalizedTeamId = text(teamId);
  if (isNoTeamRecord({ id: normalizedTeamId })) {
    return {
      teamId: normalizedTeamId,
      agents: (records.agents || []).filter((agent) => !text(agent.team_id)),
      projects: [],
      roles: [],
    };
  }
  return {
    teamId: normalizedTeamId,
    agents: (records.agents || []).filter((agent) => text(agent.team_id) === normalizedTeamId),
    projects: (records.projects || []).filter((project) => text(project.team_id) === normalizedTeamId),
    roles: (records.roles || []).filter((role) => text(role.team_id) === normalizedTeamId),
  };
}

function isNoTeamRecord(team) {
  return text(team?.id) === noTeamRecordId;
}

function isTeamInPeopleScope(agent, visibleTeamIds) {
  const teamId = text(agent?.team_id);
  if (!teamId) {
    return visibleTeamIds.has(noTeamRecordId);
  }
  return visibleTeamIds.has(teamId);
}

function peopleViewTeams() {
  const visibleTeams = values(state.teams).filter(isVisibleTeam).sort(comparePeopleTeams);
  const visibleAgents = values(state.agents).filter(isVisibleAgent);
  const hasNoTeamAgents = visibleAgents.some((agent) => !text(agent.team_id));
  if (!hasNoTeamAgents) {
    return visibleTeams;
  }
  return [...visibleTeams, { id: noTeamRecordId, name: noTeamRecordName, archived: false }];
}

function teamRecordCount(records) {
  return (records.agents || []).length + (records.projects || []).length + (records.roles || []).length;
}

function selectedTeamRecordFrom(records) {
  if (!selectedTeamRecordId) {
    return null;
  }
  if (selectedTeamRecordKind === "team") {
    const selectedTeam = state.teams[selectedTeamRecordId];
    if (!selectedTeam || !isVisibleTeam(selectedTeam)) {
      clearSelectedTeamRecord();
      return null;
    }
    return {
      kind: selectedTeamRecordKind,
      record: selectedTeam,
    };
  }
  const selectedRecord = teamRecordCollection(records, selectedTeamRecordKind)
    .find((record) => text(record.id) === text(selectedTeamRecordId));
  if (!selectedRecord) {
    clearSelectedTeamRecord();
    return null;
  }
  return {
    kind: selectedTeamRecordKind,
    record: selectedRecord,
  };
}

function teamRecordCollection(records, kind) {
  if (kind === "team") {
    return [];
  }
  if (kind === "project") {
    return records.projects || [];
  }
  if (kind === "role") {
    return records.roles || [];
  }
  return records.agents || [];
}

function clearSelectedTeamRecord() {
  selectedTeamRecordKind = "agent";
  selectedTeamRecordId = null;
}

function comparePeopleTeams(left, right) {
  return teamSortKey(left).localeCompare(teamSortKey(right)) || byId(left, right);
}

function compareGovernanceGroups(left, right) {
  return (
    text(left?.name || left?.id).localeCompare(text(right?.name || right?.id)) ||
    byId(left, right)
  );
}

function comparePeopleAgents(left, right) {
  return (
    teamRecordSortKey(left).localeCompare(teamRecordSortKey(right)) ||
    agentLabel(left).localeCompare(agentLabel(right)) ||
    byId(left, right)
  );
}

function teamRecordSortKey(record) {
  const teamId = text(record?.team_id);
  const team = state.teams[teamId];
  return teamSortKey(team || { id: teamId });
}

function compareTeamProjects(left, right) {
  return (
    teamRecordSortKey(left).localeCompare(teamRecordSortKey(right)) ||
    projectSortKey(left).localeCompare(projectSortKey(right)) ||
    byId(left, right)
  );
}

function compareTeamRoles(left, right) {
  return (
    teamRecordSortKey(left).localeCompare(teamRecordSortKey(right)) ||
    roleLabel(left).localeCompare(roleLabel(right)) ||
    byId(left, right)
  );
}

function compareReviewProfiles(left, right) {
  const leftProject = state.projects?.[text(left?.project_id)] || { id: text(left?.project_id) };
  const rightProject = state.projects?.[text(right?.project_id)] || { id: text(right?.project_id) };
  return (
    teamRecordSortKey(left).localeCompare(teamRecordSortKey(right)) ||
    projectSortKey(leftProject).localeCompare(projectSortKey(rightProject)) ||
    text(left?.name || left?.id).localeCompare(text(right?.name || right?.id)) ||
    byId(left, right)
  );
}

function selectPeopleAgent(agentId) {
  selectTeamRecord("agent", agentId);
}

function selectTeamRecord(kind, recordId, { rerender = true } = {}) {
  const normalizedRecordId = text(recordId);
  if (!normalizedRecordId || !teamRecordExists(kind, normalizedRecordId)) {
    return;
  }
  selectedTeamRecordKind = kind;
  selectedTeamRecordId = normalizedRecordId;
  if (rerender) {
    renderAgents();
  }
}

function teamRecordExists(kind, recordId) {
  if (kind === "team") {
    const team = state.teams[recordId];
    return Boolean(team && isVisibleTeam(team));
  }
  if (kind === "project") {
    return Boolean(state.projects[recordId]);
  }
  if (kind === "role") {
    return Boolean(state.roles[recordId]);
  }
  return Boolean(state.agents[recordId]);
}

function renderSelectableAgent(agent) {
  const details = agentCardData(agent);
  const selected = selectedTeamRecordKind === "agent" && text(selectedTeamRecordId) === details.id;
  return `
    <li class="agent-item agent-item-compact people-agent-card${selected ? " is-selected" : ""}" data-agent-id="${escapeAttr(details.id)}">
      <button
        class="agent-summary compact-record-summary people-agent-select"
        type="button"
        data-select-agent-id="${escapeAttr(details.id)}"
        aria-controls="people-agent-detail-view"
        aria-pressed="${selected ? "true" : "false"}"
      >
          ${renderAgentAvatar(details)}
          <span class="compact-record-main">
            <strong>${escapeHtml(details.label)}</strong>
            <span class="people-agent-role">${escapeHtml(details.role)}</span>
          </span>
      </button>
    </li>
  `;
}

function renderSelectableProject(project) {
  const selected = selectedTeamRecordKind === "project" && text(selectedTeamRecordId) === text(project.id);
  const taskCount = values(state.tasks).filter((task) => task.project_id === project.id).length;
  const lead = state.agents[project.lead_agent_id];
  const leadLabel = lead ? agentLabel(lead) : text(project.lead_agent_id) || "No lead";
  return `
    <li class="team-record-card${selected ? " is-selected" : ""}" data-project-id="${escapeAttr(project.id)}">
      <button
        class="team-record-select"
        type="button"
        data-select-project-id="${escapeAttr(project.id)}"
        aria-controls="people-agent-detail-view"
        aria-pressed="${selected ? "true" : "false"}"
      >
        <span class="team-record-main">
          <strong>${escapeHtml(project.name || project.id)}</strong>
          <span>Lead ${escapeHtml(leadLabel)} - ${taskCount} task${taskCount === 1 ? "" : "s"}</span>
        </span>
        <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
      </button>
    </li>
  `;
}

function renderSelectableRole(role) {
  const selected = selectedTeamRecordKind === "role" && text(selectedTeamRecordId) === text(role.id);
  const assignedCount = agentsForRole(role.id).length;
  const scope = role.project_id ? projectLabel(role.project_id) : "Team role";
  return `
    <li class="team-record-card${selected ? " is-selected" : ""}" data-role-id="${escapeAttr(role.id)}">
      <button
        class="team-record-select"
        type="button"
        data-select-role-id="${escapeAttr(role.id)}"
        aria-controls="people-agent-detail-view"
        aria-pressed="${selected ? "true" : "false"}"
      >
        <span class="team-record-main">
          <strong>${escapeHtml(role.name || role.id)}</strong>
          <span>${escapeHtml(scope)} - ${assignedCount} agent${assignedCount === 1 ? "" : "s"}</span>
        </span>
        <span class="people-agent-role">${escapeHtml(role.archived ? "Archived" : "Role")}</span>
      </button>
    </li>
  `;
}

function renderTeamRecordDetail(selectedRecord) {
  if (!selectedRecord) {
    return renderPeopleAgentDetail(null, "Select a team, agent, project, or role.");
  }
  if (selectedRecord.kind === "team") {
    return renderTeamRecordDetailContent(selectedRecord.record);
  }
  if (selectedRecord.kind === "project") {
    return renderTeamProjectDetail(selectedRecord.record);
  }
  if (selectedRecord.kind === "role") {
    return renderTeamRoleDetail(selectedRecord.record);
  }
  return renderPeopleAgentDetail(selectedRecord.record);
}

function renderTeamProjectDetail(project) {
  const team = state.teams[project.team_id] || null;
  const lead = state.agents[project.lead_agent_id] || null;
  const projectTasks = values(state.tasks)
    .filter((task) => task.project_id === project.id)
    .sort(byPriority);
  const openTasks = projectTasks.filter((task) => !terminalTaskStates.has(task.state));
  const projectDeliverables = values(state.deliverables).filter((deliverable) => {
    const task = state.tasks[deliverable.task_id];
    return task?.project_id === project.id;
  });
  return `
    <div class="agent-detail team-record-detail">
      <div class="agent-detail-header">
        <div class="agent-detail-title">
          <h3>${escapeHtml(project.name || project.id)}</h3>
          <div class="meta-line">${escapeHtml(project.id)}</div>
        </div>
        <div class="agent-detail-badges">
          <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
        </div>
      </div>
      <div class="record-actions agent-detail-actions">
        ${editButton("project", project.id)}
      </div>
      <div class="agent-detail-grid">
        ${renderAgentDetailField("Team", team ? `${team.name || team.id} (${team.id})` : project.team_id || "None")}
        ${renderAgentDetailField("Lead", lead ? `${agentLabel(lead)} (${lead.id})` : project.lead_agent_id || "None")}
        ${renderAgentDetailField("Directory scope", project.directory_path || "root")}
        ${renderAgentDetailField("Open tasks", `${openTasks.length} / ${projectTasks.length}`)}
        ${renderAgentDetailField("Deliverables", projectDeliverables.length)}
      </div>
      ${renderProjectCostCounter(project.id)}
      ${renderProjectGit(project.git)}
      ${renderProjectReleasePolicy(project.release_policy)}
      <section class="agent-detail-section">
        <h3>Goals</h3>
        ${renderGoals(project.id, project.goals || [], { compact: true })}
      </section>
      <section class="agent-detail-section">
        <h3>Tasks</h3>
        ${renderTasks(projectTasks, { compact: true })}
      </section>
      <section class="agent-detail-section">
        <h3>Deliverables</h3>
        ${renderDeliverables(projectDeliverables, { compact: true })}
      </section>
    </div>
  `;
}

function renderTeamRecordDetailContent(team) {
  const manager = state.agents[team.manager_agent_id] || null;
  const teamRecords = teamRecordsForTeam(team.id, {
    teamId: text(team.id),
    agents: values(state.agents).filter((agent) => isVisibleAgent(agent) && text(agent.team_id) === text(team.id)),
    projects: values(state.projects).filter((project) => isVisibleProject(project) && text(project.team_id) === text(team.id)),
    roles: values(state.roles).filter((role) => isVisibleRole(role) && text(role.team_id) === text(team.id)),
  });
  const teamCost = teamCostForTeam(team.id);
  const renewalLabel = text(team.cost_budget_renewal_mode) === "monthly_utc"
    ? "Monthly at 00:00 UTC"
    : "Manual reset";
  const budgetWindows = Array.isArray(team.cost_budget_windows) ? team.cost_budget_windows : [];
  const budgetWindowHistory = budgetWindows.length
    ? `<ul class="record-list">${[...budgetWindows]
      .sort((left, right) => text(right.started_at).localeCompare(text(left.started_at)))
      .map((window) => `<li><strong>${escapeHtml(humanizeToken(window.kind) || "budget window")}</strong> ${escapeHtml(formatChatTimestamp(window.started_at))} — ${escapeHtml(text(window.reason) || "No reason recorded")} <span class="meta-line">${escapeHtml(text(window.actor) || "unknown actor")}</span></li>`)
      .join("")}</ul>`
    : `<p class="empty-state">This Team retains its legacy lifetime cap until an operator resets its budget.</p>`;
  return `
    <div class="agent-detail team-record-detail">
      <div class="agent-detail-header">
        <div class="agent-detail-title">
          <h3>${escapeHtml(team.name || team.id)}</h3>
          <div class="meta-line">${escapeHtml(team.id)}</div>
        </div>
        <div class="agent-detail-badges">
          ${team.archived ? `<span class="state-pill archived">archived</span>` : `<span class="state-pill accepted">active</span>`}
          ${renderTeamCostBlockedPill(team.id)}
        </div>
      </div>
      <div class="record-actions agent-detail-actions">
        ${editButton("team", team.id)}
        ${team.max_cost_usd !== null && team.max_cost_usd !== undefined ? `<button type="button" class="record-action" data-team-cost-budget-reset="${escapeAttr(team.id)}">Reset budget</button>` : ""}
      </div>
      <div class="agent-detail-grid">
        ${renderAgentDetailField("Manager", manager ? `${agentLabel(manager)} (${manager.id})` : text(team.manager_agent_id) || "None")}
        ${renderAgentDetailField("Agents", teamRecords.agents.length)}
        ${renderAgentDetailField("Projects", teamRecords.projects.length)}
        ${renderAgentDetailField("Roles", teamRecords.roles.length)}
        ${renderAgentDetailField("Escalate to governance", Boolean(team.escalate_to_governance) ? "Enabled" : "Disabled")}
        ${renderAgentDetailField("Operator communication target", text(team.operator_communication_target_id) || "None")}
        ${renderAgentDetailField("Estimated max cost USD", text(team.max_cost_usd) || "None")}
        ${renderAgentDetailField("Governance cost warning", `${Math.round((numberOrNull(team.cost_warning_ratio) ?? 0.8) * 100)}% of cap`)}
        ${renderAgentDetailField("Budget renewal", renewalLabel)}
        ${renderAgentDetailField("Budget window start", teamCost?.budget_window_started_at ? formatChatTimestamp(teamCost.budget_window_started_at) : "Lifetime legacy cap")}
      </div>
      <section class="agent-detail-section">
        <h3>Team cost</h3>
        ${renderTeamCostCounter(team.id) || "<p class=\"empty-state\">No cost estimate available.</p>"}
        ${renderCostBackfillControl()}
      </section>
      <section class="agent-detail-section">
        <h3>Budget history</h3>
        ${budgetWindowHistory}
      </section>
    </div>
  `;
}

function renderTeamRoleDetail(role) {
  const team = state.teams[role.team_id] || null;
  const project = role.project_id ? state.projects[role.project_id] || null : null;
  const assignedAgents = agentsForRole(role.id);
  const permissions = splitList(role.permissions);
  return `
    <div class="agent-detail team-record-detail">
      <div class="agent-detail-header">
        <div class="agent-detail-title">
          <h3>${escapeHtml(role.name || role.id)}</h3>
          <div class="meta-line">${escapeHtml(role.id)}</div>
        </div>
        <div class="agent-detail-badges">
          <span class="state-pill ${role.archived ? "blocked" : "accepted"}">${role.archived ? "archived" : "active"}</span>
        </div>
      </div>
      <div class="record-actions agent-detail-actions">
        ${editButton("role", role.id)}
      </div>
      <div class="agent-detail-grid">
        ${renderAgentDetailField("Team", team ? `${team.name || team.id} (${team.id})` : role.team_id || "None")}
        ${renderAgentDetailField("Project", project ? `${project.name || project.id} (${project.id})` : role.project_id || "None")}
        ${renderAgentDetailField("Assigned agents", assignedAgents.length)}
        ${renderAgentDetailField("Max ephemeral", role.ephemeral_policy?.max_concurrent ?? 0)}
      </div>
      ${
        role.description
          ? `<section class="agent-detail-section"><h3>Description</h3><p class="meta-line role-description">${escapeHtml(role.description)}</p></section>`
          : ""
      }
      <section class="agent-detail-section">
        <h3>Permissions</h3>
        ${
          permissions.length
            ? `<ul class="inline-list agent-detail-list">${permissions.map((permission) => `<li>${renderRolePermissionToken(permission)}</li>`).join("")}</ul>`
            : `<p class="empty-state">No special permissions.</p>`
        }
      </section>
      <section class="agent-detail-section">
        <h3>Agents</h3>
        ${
          assignedAgents.length
            ? `<ul class="inline-list agent-detail-list">${assignedAgents
                .map((agent) => `<li>${escapeHtml(agentLabel(agent))} (${escapeHtml(agent.id)})</li>`)
                .join("")}</ul>`
            : `<p class="empty-state">No agents assigned.</p>`
        }
      </section>
    </div>
  `;
}

function agentsForRole(roleId) {
  const normalizedRoleId = text(roleId);
  return values(state.agents)
    .filter((agent) => text(agent.role_id) === normalizedRoleId && isVisibleAgent(agent))
    .sort(comparePeopleAgents);
}

function renderChat() {
  renderChatOperatorView();
  renderChatConversationList();
  renderChatThread();
  syncChatMobilePane();
  updateTeamViewTabs();
  applyLockedMutationControls();
}

function syncChatMobilePane() {
  if (!elements.chatPage) {
    return;
  }
  const showConversationList = chatMobileConversationListOpen || !selectedChatConversation();
  elements.chatPage.classList.toggle("is-conversation-list-open", showConversationList);
}

function readChatReadMarkers() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(chatReadStorageKey) || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function storeChatReadMarkers() {
  try {
    window.localStorage.setItem(chatReadStorageKey, JSON.stringify(chatReadMarkers));
  } catch {
    // Storage can be unavailable in hardened browser contexts.
  }
}

function readChatNotificationSoundMuted() {
  try {
    return window.localStorage.getItem(chatNotificationSoundStorageKey) === "true";
  } catch {
    return false;
  }
}

function storeChatNotificationSoundMuted() {
  try {
    window.localStorage.setItem(chatNotificationSoundStorageKey, String(chatNotificationSoundMuted));
  } catch {
    // Storage can be unavailable in hardened browser contexts.
  }
}

function maybePlayChatNotificationSound(previousConversation, nextConversation) {
  if (chatNotificationSoundMuted || chatWindowIsFocused()) {
    return;
  }
  if (!previousConversation || !nextConversation) {
    return;
  }
  const previousNewestMessageId = chatConversationNewestMessageId(previousConversation);
  const nextNewestMessageId = chatConversationNewestMessageId(nextConversation);
  if (!nextNewestMessageId || previousNewestMessageId === nextNewestMessageId) {
    return;
  }
  if (chatConversationNewestSender(nextConversation) === "operator") {
    return;
  }
  const now = Date.now();
  if (now - lastChatNotificationSoundAt < chatNotificationSoundThrottleMs) {
    return;
  }
  lastChatNotificationSoundAt = now;
  void playChatNotificationSound().catch(() => {
    // Browsers may block audio until the operator has interacted with the page.
  });
}

function maybePlayChatCollectionNotificationSound(previousConversations, nextConversations) {
  if (!(previousConversations instanceof Map) || !Array.isArray(nextConversations)) {
    return;
  }
  nextConversations.forEach((conversation) => {
    const conversationId = text(conversation?.id);
    if (conversationId) {
      maybePlayChatNotificationSound(previousConversations.get(conversationId), conversation);
    }
  });
}

function chatWindowIsFocused() {
  return typeof document.hasFocus === "function"
    ? document.hasFocus()
    : document.visibilityState === "visible";
}

async function playChatNotificationSound() {
  const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
  if (typeof AudioContextConstructor !== "function") {
    return;
  }
  if (!chatNotificationAudioContext || chatNotificationAudioContext.state === "closed") {
    chatNotificationAudioContext = new AudioContextConstructor();
  }
  if (chatNotificationAudioContext.state === "suspended") {
    await chatNotificationAudioContext.resume();
  }
  const audioTime = chatNotificationAudioContext.currentTime;
  const oscillator = chatNotificationAudioContext.createOscillator();
  const gain = chatNotificationAudioContext.createGain();
  oscillator.type = "sine";
  oscillator.frequency.setValueAtTime(880, audioTime);
  oscillator.frequency.exponentialRampToValueAtTime(1320, audioTime + 0.08);
  gain.gain.setValueAtTime(0.0001, audioTime);
  gain.gain.exponentialRampToValueAtTime(0.12, audioTime + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, audioTime + 0.18);
  oscillator.connect(gain);
  gain.connect(chatNotificationAudioContext.destination);
  oscillator.onended = () => {
    oscillator.disconnect();
    gain.disconnect();
  };
  oscillator.start(audioTime);
  oscillator.stop(audioTime + 0.2);
}

function chatConversationNewestMessageId(conversation) {
  return text(conversation?.newest_message_id) || text(conversationMessages(conversation).at(-1)?.id);
}

function chatConversationNewestSender(conversation) {
  return text(conversation?.newest_message_sender) || text(conversationMessages(conversation).at(-1)?.sender);
}

function chatConversationHasUnread(conversation) {
  const conversationId = text(conversation?.id);
  const newestMessageId = chatConversationNewestMessageId(conversation);
  const newestSender = chatConversationNewestSender(conversation);
  if (!conversationId || !newestMessageId || newestSender === "operator") {
    return false;
  }
  return text(chatReadMarkers[conversationId]) !== newestMessageId;
}

function unreadChatConversationCount() {
  return chatState.conversations.filter(chatConversationHasUnread).length;
}

function markChatConversationRead(conversationId, { rerender = true } = {}) {
  const normalizedConversationId = text(conversationId);
  if (!normalizedConversationId) {
    return false;
  }
  const conversation = chatConversationById(normalizedConversationId);
  const newestMessageId = chatConversationNewestMessageId(conversation);
  const newestSender = chatConversationNewestSender(conversation);
  if (newestMessageId && newestSender !== "operator") {
    markAdminChatConversationNotificationsRead(normalizedConversationId);
  }
  if (!newestMessageId || text(chatReadMarkers[normalizedConversationId]) === newestMessageId) {
    return false;
  }
  chatReadMarkers = {
    ...chatReadMarkers,
    [normalizedConversationId]: newestMessageId,
  };
  storeChatReadMarkers();
  if (rerender) {
    renderChat();
  }
  return true;
}

function markAdminChatConversationNotificationsRead(conversationId) {
  const normalizedConversationId = text(conversationId);
  if (!normalizedConversationId) {
    return;
  }
  void teamFetch("/api/admin/user-notifications/read", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      source: "team-chat",
      metadata: {
        conversation_id: normalizedConversationId,
      },
    }),
  })
    .then((response) => {
      if (response.ok && window.parent && window.parent !== window) {
        window.parent.postMessage({ type: "admin-user-notifications:changed" }, window.location.origin);
      }
    })
    .catch(() => {});
}

function renderChatOperatorView() {
  if (!elements.chatOperatorView) {
    return;
  }
  const current = chatState.current_operator || {};
  const selected = chatState.selected_operator || current;
  const currentUserId = text(current.user_id);
  const selectedUserId = text(selected.user_id) || text(selectedChatOperatorUserId) || currentUserId;
  const selectedLabel = text(selected.display_name) || selectedUserId || "Operator";
  const options = chatState.operator_options.length
    ? chatState.operator_options
    : selectedUserId
      ? [selected]
      : [];
  const modeLabel = chatState.read_only ? "Read-only inspection" : "My chats";
  elements.chatOperatorView.innerHTML = `
    <div class="chat-operator-scope">
      <span class="state-pill ${chatState.read_only ? "paused" : "active"}">${escapeHtml(modeLabel)}</span>
      ${
        chatState.can_view_all_operators && options.length
          ? `
            <label>
              <span>Operator</span>
              <select data-chat-operator-select>
                ${options.map((option) => {
                  const value = text(option.user_id);
                  const label = text(option.display_name) || value;
                  return `<option value="${escapeAttr(value)}" ${value === selectedUserId ? "selected" : ""}>${escapeHtml(label)}</option>`;
                }).join("")}
              </select>
            </label>
          `
          : `<span class="meta-line">${escapeHtml(selectedLabel)}</span>`
      }
    </div>
  `;
  document.querySelectorAll("[data-chat-new]").forEach((button) => {
    button.disabled = Boolean(chatState.read_only);
  });
}

function renderChatConversationList() {
  if (!elements.chatConversationsView) {
    return;
  }
  if (chatState.error && !chatState.conversations.length) {
    elements.chatConversationsView.innerHTML = `<p class="empty-state">${escapeHtml(chatState.error)}</p>`;
  } else if (chatState.loading && !chatState.conversations.length) {
    elements.chatConversationsView.innerHTML = `<p class="empty-state">Loading conversations.</p>`;
  } else if (!chatState.conversations.length) {
    elements.chatConversationsView.innerHTML = `<p class="empty-state">No conversations.</p>`;
  } else {
    elements.chatConversationsView.innerHTML = `
      <ul class="chat-conversation-list">
        ${chatState.conversations.map(renderChatConversationRow).join("")}
      </ul>
    `;
  }
  renderChatNewPicker();
}

function renderChatNewPicker() {
  if (!elements.chatNewPicker) {
    return;
  }
  if (chatState.read_only) {
    elements.chatNewPicker.hidden = true;
    elements.chatNewPicker.innerHTML = "";
    return;
  }
  elements.chatNewPicker.hidden = !chatNewPickerOpen;
  if (!chatNewPickerOpen) {
    elements.chatNewPicker.innerHTML = "";
    return;
  }
  const agents = [...chatState.eligible_agents].sort((left, right) => {
    return text(left.display_name || left.id).localeCompare(text(right.display_name || right.id));
  });
  elements.chatNewPicker.innerHTML = agents.length
    ? `
      <div class="chat-agent-picker">
        ${agents.map(renderChatAgentPickerRow).join("")}
      </div>
    `
    : `<p class="empty-state">No available agents.</p>`;
}

function renderChatConversationRow(conversation) {
  const selected = text(conversation.id) === text(selectedChatConversationId);
  const unread = chatConversationHasUnread(conversation);
  const agent = conversation.agent || {};
  const label = text(agent.display_name) || text(agent.id) || text(conversation.agent_id);
  const preview = text(conversation.last_message_preview) || "No messages yet.";
  return `
    <li>
      <button
        type="button"
        class="chat-conversation-row${selected ? " is-selected" : ""}${unread ? " has-unread" : ""}"
        data-chat-conversation-id="${escapeAttr(conversation.id)}"
        aria-pressed="${selected ? "true" : "false"}"
        aria-label="${escapeAttr(`${label}${unread ? ", unread" : ""}`)}"
      >
        ${renderChatAvatar(agent)}
        <span class="chat-conversation-main">
          <strong>${escapeHtml(label)}</strong>
          <span>${escapeHtml(preview)}</span>
        </span>
        <span class="chat-conversation-meta">
          ${unread ? `<span class="chat-unread-badge" aria-hidden="true">New</span>` : ""}
          <time>${escapeHtml(formatChatTimestamp(conversation.last_interaction_at))}</time>
        </span>
      </button>
    </li>
  `;
}

function renderChatAgentPickerRow(agent) {
  const conversation = conversationForAgent(agent.id);
  const label = text(agent.display_name) || text(agent.id);
  const details = agentCardData(agent);
  return `
    <button type="button" class="chat-agent-picker-row" data-chat-agent-id="${escapeAttr(agent.id)}">
      ${renderChatAvatar(agent)}
      <span class="chat-conversation-main">
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(conversation ? "Open conversation" : agent.session_id || agent.id)}</span>
      </span>
      ${renderAgentRosterStatusPill(details)}
    </button>
  `;
}

function renderChatThread() {
  if (!elements.chatThreadView) {
    return;
  }
  const conversation = selectedChatConversation();
  if (!conversation) {
    elements.chatThreadView.innerHTML = `
      <div class="chat-empty">
        <p class="empty-state">${chatState.loading ? "Loading conversations." : "Select or start a chat."}</p>
      </div>
    `;
    return;
  }
  const composerState =
    captureChatComposerState(conversation.id)
    || chatComposerDrafts.get(text(conversation.id))
    || null;
  const agent = conversation.agent || {};
  const messages = conversationMessages(conversation);
  const label = text(agent.display_name) || text(agent.id) || text(conversation.agent_id);
  const olderLoading = chatOlderMessageLoads.has(text(conversation.id));
  const callStrip = renderChatCallStrip(conversation);
  const readOnly = Boolean(chatState.read_only || conversation.read_only);
  const listSignature = chatMessageListSignature(conversation, messages, olderLoading);
  const existingThread = elements.chatThreadView.querySelector(".chat-thread");
  const canPatchThread =
    existingThread instanceof HTMLElement &&
    text(existingThread.dataset.chatThreadConversationId) === text(conversation.id) &&
    existingThread.dataset.chatThreadCallStrip === (callStrip ? "1" : "0") &&
    existingThread.dataset.chatThreadReadOnly === (readOnly ? "1" : "0");
  if (
    canPatchThread &&
    patchChatThread(existingThread, {
      conversation,
      agent,
      messages,
      label,
      olderLoading,
      callStrip,
      readOnly,
      listSignature,
    })
  ) {
    restoreChatComposerState(conversation.id, composerState);
    applyPendingChatScroll(conversation.id);
    return;
  }
  elements.chatThreadView.innerHTML = `
    ${renderChatThreadMarkup({
      conversation,
      agent,
      messages,
      label,
      olderLoading,
      callStrip,
      readOnly,
    })}
  `;
  setChatMessageListSignature(conversation.id, listSignature);
  restoreChatComposerState(conversation.id, composerState);
  bindChatMessageListScroll(conversation.id);
  applyPendingChatScroll(conversation.id);
}

function renderChatThreadMarkup({
  conversation,
  agent,
  messages,
  label,
  olderLoading,
  callStrip,
  readOnly,
}) {
  return `
    <div
      class="chat-thread${callStrip ? " has-call-strip" : ""}"
      data-chat-thread-conversation-id="${escapeAttr(conversation.id)}"
      data-chat-thread-call-strip="${callStrip ? "1" : "0"}"
      data-chat-thread-read-only="${readOnly ? "1" : "0"}"
    >
      ${renderChatThreadHeader(conversation, agent, label, readOnly)}
      ${renderChatReadonlyBanner(conversation, readOnly)}
      ${callStrip}
      ${renderChatMessageList(conversation, messages, olderLoading)}
      ${renderChatComposer(conversation, label, readOnly)}
    </div>
  `;
}

function patchChatThread(
  thread,
  {
    conversation,
    agent,
    messages,
    label,
    olderLoading,
    callStrip,
    readOnly,
    listSignature,
  },
) {
  const header = thread.querySelector(".chat-thread-header");
  const list = thread.querySelector(".chat-message-list");
  const composer = thread.querySelector(".chat-composer");
  if (!header || !list || !composer) {
    return false;
  }
  header.outerHTML = renderChatThreadHeader(conversation, agent, label, readOnly);
  const banner = thread.querySelector(".chat-readonly-banner");
  if (readOnly && banner) {
    banner.outerHTML = renderChatReadonlyBanner(conversation, readOnly);
  }
  const callStripElement = thread.querySelector(".chat-call-strip");
  if (callStrip && callStripElement) {
    callStripElement.outerHTML = callStrip;
  }
  if (list.dataset.chatMessageSignature !== listSignature) {
    list.innerHTML = renderChatMessageListContents(conversation, messages, olderLoading);
    list.dataset.chatMessageSignature = listSignature;
  }
  composer.outerHTML = renderChatComposer(conversation, label, readOnly);
  return true;
}

function renderChatThreadHeader(conversation, agent, label, readOnly) {
  return `
    <header class="chat-thread-header">
      <div class="chat-thread-title">
        <button
          type="button"
          class="chat-mobile-conversations-button"
          data-chat-mobile-conversations
          aria-label="Back to conversations"
        >Chats</button>
        ${renderChatAvatar(agent)}
        <div>
          <h2>${escapeHtml(label)}</h2>
          <div class="meta-line">${escapeHtml(agent.session_id || conversation.agent_id || "")}</div>
        </div>
      </div>
      <div class="chat-thread-actions">
        <button type="button" data-chat-attachment-policy-open title="Configure add-on-owned file rules by directory">Files</button>
        <label
          class="chat-sound-toggle"
          title="Play a short sound when agent messages arrive and this window is not focused."
        >
          <input
            type="checkbox"
            data-chat-notification-sound
            ${chatNotificationSoundMuted ? "" : "checked"}
          />
          <span>Sound</span>
        </label>
        <button
          type="button"
          data-agent-call-agent-id="${escapeAttr(conversation.agent_id)}"
          data-agent-call-conversation-id="${escapeAttr(conversation.id)}"
          ${readOnly ? "disabled" : ""}
        >Call</button>
        <button type="button" data-chat-refresh>Refresh</button>
      </div>
    </header>
  `;
}

function renderChatReadonlyBanner(conversation, readOnly) {
  return readOnly
    ? `<div class="chat-readonly-banner">Viewing ${escapeHtml(conversation.operator_display_name || conversation.operator_user_id || "another operator")} read-only.</div>`
    : "";
}

function renderChatMessageList(conversation, messages, olderLoading) {
  return `
    <div class="chat-message-list" role="log" aria-live="polite" data-chat-scroll-conversation-id="${escapeAttr(conversation.id)}">
      ${renderChatMessageListContents(conversation, messages, olderLoading)}
    </div>
  `;
}

function renderChatMessageListContents(conversation, messages, olderLoading) {
  return `
    ${
      conversation.has_older || olderLoading
        ? `<div class="chat-history-sentinel" data-chat-history-sentinel>${olderLoading ? "Loading older messages." : ""}</div>`
        : ""
    }
    ${
      messages.length
        ? messages.map(renderChatMessage).join("")
        : `<p class="empty-state">No messages.</p>`
    }
  `;
}

function renderChatComposer(conversation, label, readOnly) {
  const conversationId = text(selectedChatConversationId);
  const attachments = [...(pendingChatAttachments.get(conversationId) || [])];
  const mutationEnabled = !readOnly && text(conversation?.mutation_mode) === "project_scoped";
  const modeLabel = mutationEnabled ? "Mutation enabled" : "Reply only";
  const modeTitle = mutationEnabled
    ? "This message can authorize Team mutations within your directory-scoped project permissions."
    : "This message can receive an agent reply but cannot authorize Team mutations.";
  return `
    <form class="chat-composer" data-chat-message-form>
      <textarea name="body" rows="3" placeholder="${readOnly ? "Read-only chat history" : `Message ${escapeAttr(label)}`}" ${chatSending || readOnly ? "disabled" : ""}></textarea>
      <div class="chat-attachment-drafts">
        ${attachments.map((file, index) => `
          <span class="chat-attachment-chip">
            <span>${escapeHtml(file.name)} · ${escapeHtml(formatFileSize(file.size))}</span>
            <button type="button" aria-label="Remove ${escapeAttr(file.name)}" data-chat-attachment-remove="${index}" data-chat-attachment-conversation-id="${escapeAttr(conversationId)}">×</button>
          </span>
        `).join("")}
      </div>
      <div class="chat-composer-actions">
        <span class="chat-mutation-mode ${mutationEnabled ? "is-enabled" : "is-reply-only"}" title="${escapeAttr(modeTitle)}">${escapeHtml(modeLabel)}</span>
        <label class="chat-attach-button ${readOnly ? "is-disabled" : ""}">
          <input type="file" multiple data-chat-attachment-input="${escapeAttr(conversationId)}" ${chatSending || readOnly ? "disabled" : ""} />
          <span>Attach</span>
        </label>
        <button type="submit" ${chatSending || readOnly ? "disabled" : ""}>Send</button>
      </div>
    </form>
  `;
}

function captureChatComposerState(expectedConversationId) {
  const form = elements.chatThreadView?.querySelector("[data-chat-message-form]");
  const textarea = form?.querySelector('textarea[name="body"]');
  if (!form || !(textarea instanceof HTMLTextAreaElement)) {
    return null;
  }
  const messageList = elements.chatThreadView?.querySelector("[data-chat-scroll-conversation-id]");
  if (text(messageList?.dataset.chatScrollConversationId) !== text(expectedConversationId)) {
    return null;
  }
  return {
    conversationId: text(expectedConversationId),
    value: textarea.value,
    hadFocus: document.activeElement === textarea,
    selectionStart: typeof textarea.selectionStart === "number" ? textarea.selectionStart : 0,
    selectionEnd: typeof textarea.selectionEnd === "number" ? textarea.selectionEnd : 0,
  };
}

function rememberCurrentChatComposerDraft() {
  const conversationId = text(
    elements.chatThreadView
      ?.querySelector("[data-chat-scroll-conversation-id]")
      ?.dataset?.chatScrollConversationId,
  );
  if (!conversationId) {
    return null;
  }
  const composerState = captureChatComposerState(conversationId);
  if (composerState) {
    const previous = chatComposerDrafts.get(conversationId);
    chatComposerDrafts.set(
      conversationId,
      previous?.hadFocus && !composerState.hadFocus
        ? {
            ...composerState,
            hadFocus: true,
            selectionStart: previous.selectionStart,
            selectionEnd: previous.selectionEnd,
          }
        : composerState,
    );
  }
  return composerState;
}

function restoreChatComposerState(expectedConversationId, composerState) {
  if (!composerState || composerState.conversationId !== text(expectedConversationId)) {
    return;
  }
  const form = elements.chatThreadView?.querySelector("[data-chat-message-form]");
  const textarea = form?.querySelector('textarea[name="body"]');
  if (!(textarea instanceof HTMLTextAreaElement)) {
    return;
  }
  textarea.value = composerState.value ?? "";
  if (!composerState.hadFocus) {
    return;
  }
  textarea.focus();
  if (
    typeof composerState.selectionStart === "number" &&
    typeof composerState.selectionEnd === "number"
  ) {
    const start = Math.min(Math.max(composerState.selectionStart, 0), textarea.value.length);
    const end = Math.min(Math.max(composerState.selectionEnd, start), textarea.value.length);
    textarea.setSelectionRange(start, end);
  }
}

function renderChatCallStrip(conversation) {
  const call = activeAgentVoiceCall;
  if (!call || agentVoiceCallIsTerminal(call) || call.agentId !== conversation.agent_id) {
    return "";
  }
  return `
    <section class="chat-call-strip">
      <span class="state-pill ${escapeAttr(agentVoiceCallPillClass(call))}">${escapeHtml(agentVoiceCallStatusLabel(call))}</span>
      <button type="button" data-agent-call-mute>${call.muted ? "Unmute" : "Mute"}</button>
      <button type="button" data-agent-call-end>End</button>
    </section>
  `;
}

function renderChatMessage(message) {
  const sender = text(message.sender) || "system";
  const metadata = message.metadata || {};
  const status = text(message.status);
  const type = text(message.message_type) || "text";
  const attachments = Array.isArray(message.attachments) ? message.attachments : [];
  const externalDelivery = metadata.external_delivery && typeof metadata.external_delivery === "object"
    ? metadata.external_delivery
    : null;
  const attachmentImport = metadata.external_attachment_import && typeof metadata.external_attachment_import === "object"
    ? metadata.external_attachment_import
    : null;
  const mutationMode = sender === "operator" ? text(message.mutation_mode) : "";
  const mutationModeLabel = mutationMode === "project_scoped"
    ? "Mutation enabled"
    : mutationMode === "reply_only"
      ? "Reply only"
      : "";
  return `
    <article class="chat-message chat-message--${escapeAttr(sender)} chat-message--${escapeAttr(type)}">
      <div class="chat-message-bubble">
        ${message.body ? `<p>${escapeHtml(message.body)}</p>` : ""}
        ${attachments.length ? `<div class="chat-message-attachments">${attachments.map(renderChatAttachment).join("")}</div>` : ""}
        ${attachmentImportWarning(attachmentImport)}
        <div class="chat-message-meta">
          <span>${escapeHtml(chatMessageSenderLabel(sender, metadata))}</span>
          <time>${escapeHtml(formatChatTimestamp(message.created_at))}</time>
          ${status ? `<span>${escapeHtml(chatStatusLabel(status))}</span>` : ""}
          ${mutationModeLabel ? `<span class="chat-mutation-mode ${mutationMode === "project_scoped" ? "is-enabled" : "is-reply-only"}">${escapeHtml(mutationModeLabel)}</span>` : ""}
          ${externalDelivery ? `<span title="${escapeAttr(externalChatDeliveryTitle(externalDelivery))}">${escapeHtml(externalChatDeliveryLabel(externalDelivery))}</span>` : ""}
        </div>
        ${message.error ? `<div class="chat-message-error">${escapeHtml(message.error)}</div>` : ""}
      </div>
    </article>
  `;
}

function attachmentImportWarning(summary) {
  const status = text(summary?.status);
  if (!["partial", "failed"].includes(status)) return "";
  const imported = Number(summary?.imported_count || 0);
  const failed = Number(summary?.failed_count || 0);
  const label = status === "partial"
    ? `${imported} external attachment${imported === 1 ? "" : "s"} imported; ${failed} could not be imported.`
    : `External attachments could not be imported (${failed || "unknown"} failure${failed === 1 ? "" : "s"}).`;
  const details = Array.isArray(summary?.failures)
    ? summary.failures.map((item) => `${text(item?.filename) || text(item?.attachment_id) || "attachment"}: ${text(item?.message) || text(item?.code) || "failed"}`).join(" · ")
    : "";
  return `<div class="chat-message-error"${details ? ` title="${escapeAttr(details)}"` : ""}>${escapeHtml(label)}</div>`;
}

function externalChatDeliveryLabel(delivery) {
  const state = text(delivery?.state) || "pending";
  const provider = text(delivery?.provider);
  return `${provider ? `${provider} ` : "External "}${state}`;
}

function externalChatDeliveryTitle(delivery) {
  const parts = [externalChatDeliveryLabel(delivery)];
  const attempts = Number(delivery?.attempt_count || 0);
  if (attempts > 0) parts.push(`attempt ${attempts}`);
  if (text(delivery?.last_error)) parts.push(text(delivery.last_error));
  return parts.join(" · ");
}

function renderChatAttachment(attachment) {
  const url = text(attachment.download_url);
  const mimeType = text(attachment.mime_type);
  const filename = text(attachment.filename) || "attachment";
  const image = ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(mimeType);
  const linkAttributes = image
    ? ' target="_blank" rel="noopener"'
    : ` download="${escapeAttr(filename)}"`;
  return `
    <a class="chat-message-attachment${image ? " is-image" : ""}" href="${escapeAttr(url)}"${linkAttributes}>
      ${image ? `<img src="${escapeAttr(url)}" alt="${escapeAttr(filename)}" loading="lazy" />` : `<span class="chat-file-icon" aria-hidden="true">↧</span>`}
      <span><strong>${escapeHtml(filename)}</strong><small>${escapeHtml(formatFileSize(attachment.byte_count))}${mimeType ? ` · ${escapeHtml(mimeType)}` : ""}</small></span>
    </a>
  `;
}

function formatFileSize(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes < 1024) return `${Math.max(0, bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function chatMessageListSignature(conversation, messages, olderLoading) {
  const messageSignature = messages
    .map((message) =>
      [
        message.id,
        message.sender,
        message.message_type,
        message.body,
        message.status,
        message.error,
        JSON.stringify(message.metadata || {}),
        JSON.stringify(message.attachments || []),
        message.created_at,
        message.updated_at,
      ].map(text).join("\x1f"),
    )
    .join("\x1e");
  return [
    conversation.has_older ? "older" : "no-older",
    olderLoading ? "loading" : "idle",
    messageSignature,
  ].join("\x1d");
}

function setChatMessageListSignature(conversationId, signature) {
  const list = chatMessageListElement(conversationId);
  if (list) {
    list.dataset.chatMessageSignature = signature;
  }
}

function applyChatMessagePage(conversationId, payload) {
  const conversation = chatConversationById(conversationId);
  if (!conversation) {
    return;
  }
  replaceChatConversation({
    ...conversation,
    messages: Array.isArray(payload.messages) ? payload.messages : [],
    message_count: payload.message_count ?? conversation.message_count,
    has_older: Boolean(payload.has_older),
    oldest_message_id: payload.oldest_message_id || null,
    newest_message_id: payload.newest_message_id || null,
  });
}

function mergeChatConversation(existing, incoming) {
  if (!existing) {
    return normalizeChatConversation(incoming);
  }
  const incomingMessages = conversationMessages(incoming);
  const existingMessages = conversationMessages(existing);
  const mergedMessages = incomingMessages.length
    ? mergeChatMessages(existingMessages, incomingMessages)
    : existingMessages;
  const next = {
    ...existing,
    ...incoming,
    messages: mergedMessages,
  };
  if (existingMessages.length && incomingMessages.length) {
    const existingOldest = existingMessages[0];
    const incomingOldest = incomingMessages[0];
    if (compareChatMessages(existingOldest, incomingOldest) < 0) {
      next.has_older = existing.has_older;
    }
  }
  const normalized = normalizeChatConversation(next);
  if (!incomingMessages.length) {
    const incomingNewestMessageId = text(incoming?.newest_message_id);
    const incomingNewestMessageSender = text(incoming?.newest_message_sender);
    if (incomingNewestMessageId) {
      normalized.newest_message_id = incomingNewestMessageId;
    }
    if (incomingNewestMessageSender) {
      normalized.newest_message_sender = incomingNewestMessageSender;
    }
  }
  return normalized;
}

function normalizeChatConversation(conversation) {
  const messagesLoaded = Array.isArray(conversation?.messages) || conversation?.messages_loaded === true;
  const messages = conversationMessages(conversation);
  const newestMessage = messages[messages.length - 1] || null;
  return {
    ...conversation,
    messages,
    messages_loaded: messagesLoaded,
    has_older: Boolean(conversation?.has_older),
    oldest_message_id: text(messages[0]?.id) || text(conversation?.oldest_message_id) || null,
    newest_message_id: text(newestMessage?.id) || text(conversation?.newest_message_id) || null,
    newest_message_sender: text(newestMessage?.sender) || text(conversation?.newest_message_sender) || null,
  };
}

function conversationMessages(conversation) {
  return Array.isArray(conversation?.messages)
    ? [...conversation.messages].sort(compareChatMessages)
    : [];
}

function mergeChatMessages(existingMessages, incomingMessages) {
  const byId = new Map();
  [...existingMessages, ...incomingMessages].forEach((message) => {
    if (message?.id) {
      byId.set(text(message.id), {
        ...(byId.get(text(message.id)) || {}),
        ...message,
      });
    }
  });
  return Array.from(byId.values()).sort(compareChatMessages);
}

function compareChatMessages(left, right) {
  const leftTime = Date.parse(left?.created_at || "");
  const rightTime = Date.parse(right?.created_at || "");
  if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
    return leftTime - rightTime;
  }
  const leftCreated = text(left?.created_at);
  const rightCreated = text(right?.created_at);
  if (leftCreated !== rightCreated) {
    return leftCreated.localeCompare(rightCreated);
  }
  return text(left?.id).localeCompare(text(right?.id));
}

function renderChatAvatar(agent) {
  const label = text(agent?.display_name || agent?.id || "?");
  const avatarAsset = text(agent?.avatar?.asset_id);
  if (avatarAsset) {
    return `<img class="chat-avatar" src="${API_BASE}/avatars/${encodeURIComponent(avatarAsset)}" alt="" />`;
  }
  return `<span class="chat-avatar chat-avatar-fallback" aria-hidden="true">${escapeHtml(label.slice(0, 1).toUpperCase() || "?")}</span>`;
}

function selectedChatConversation() {
  return chatConversationById(selectedChatConversationId);
}

function chatConversationById(conversationId) {
  const normalizedConversationId = text(conversationId);
  if (!normalizedConversationId) {
    return null;
  }
  return chatState.conversations.find((conversation) => text(conversation.id) === normalizedConversationId) || null;
}

function conversationForAgent(agentId) {
  const normalizedAgentId = text(agentId);
  return chatState.conversations.find((conversation) => text(conversation.agent_id) === normalizedAgentId) || null;
}

function chatMessageListElement(conversationId = selectedChatConversationId) {
  const normalizedConversationId = text(conversationId);
  if (!normalizedConversationId || !elements.chatThreadView) {
    return null;
  }
  return Array.from(elements.chatThreadView.querySelectorAll(".chat-message-list")).find((list) => {
    return text(list.dataset.chatScrollConversationId) === normalizedConversationId;
  }) || null;
}

function captureChatScroll(conversationId = selectedChatConversationId) {
  const list = chatMessageListElement(conversationId);
  if (!list) {
    return null;
  }
  return {
    scrollTop: list.scrollTop,
    scrollHeight: list.scrollHeight,
  };
}

function chatIsScrolledNearBottom(conversationId = selectedChatConversationId) {
  const list = chatMessageListElement(conversationId);
  if (!list) {
    return true;
  }
  return list.scrollHeight - list.clientHeight - list.scrollTop <= chatBottomStickinessPx;
}

function scheduleChatScrollToBottom(conversationId) {
  pendingChatScroll = { conversationId: text(conversationId), mode: "bottom" };
}

function scheduleChatScrollForRender(conversationId = selectedChatConversationId, { preferBottom = false } = {}) {
  const normalizedConversationId = text(conversationId);
  if (!normalizedConversationId) {
    return;
  }
  if (
    pendingChatScroll?.mode === "bottom" &&
    text(pendingChatScroll.conversationId) === normalizedConversationId
  ) {
    return;
  }
  const snapshot = captureChatScroll(normalizedConversationId);
  if (!snapshot) {
    return;
  }
  if (preferBottom || chatIsScrolledNearBottom(normalizedConversationId)) {
    scheduleChatScrollToBottom(normalizedConversationId);
    return;
  }
  scheduleChatScrollPreserve(normalizedConversationId, snapshot);
}

function scheduleChatScrollPreserve(conversationId, snapshot) {
  pendingChatScroll = {
    conversationId: text(conversationId),
    mode: "preserve",
    scrollTop: snapshot.scrollTop,
  };
}

function scheduleChatScrollPreservePrepend(conversationId, snapshot) {
  pendingChatScroll = {
    conversationId: text(conversationId),
    mode: "preserve-prepend",
    scrollTop: snapshot.scrollTop,
    scrollHeight: snapshot.scrollHeight,
  };
}

function bindChatMessageListScroll(conversationId) {
  const list = chatMessageListElement(conversationId);
  if (!list) {
    return;
  }
  list.addEventListener("scroll", () => {
    if (list.scrollTop <= chatTopLoadThresholdPx) {
      void loadOlderChatMessages(conversationId);
    }
  });
}

function applyPendingChatScroll(conversationId) {
  const pending = pendingChatScroll;
  if (!pending || text(pending.conversationId) !== text(conversationId)) {
    return;
  }
  const applyScroll = () => {
    const list = chatMessageListElement(conversationId);
    if (!list || pendingChatScroll !== pending) {
      return false;
    }
    if (pending.mode === "bottom") {
      list.scrollTop = list.scrollHeight;
    } else if (pending.mode === "preserve-prepend") {
      list.scrollTop = Math.max(0, list.scrollHeight - pending.scrollHeight + pending.scrollTop);
    } else {
      list.scrollTop = pending.scrollTop;
    }
    return true;
  };
  window.requestAnimationFrame(() => {
    if (!applyScroll()) {
      return;
    }
    window.requestAnimationFrame(() => {
      applyScroll();
      if (pendingChatScroll === pending) {
        pendingChatScroll = null;
      }
    });
  });
}

function compareChatConversations(left, right) {
  return (
    text(right.last_interaction_at).localeCompare(text(left.last_interaction_at)) ||
    byId(left, right)
  );
}

function chatMessageSenderLabel(sender, metadata = {}) {
  if (sender === "operator") {
    const label = text(metadata.operator_display_name) || text(metadata.operator_user_id);
    return label ? `Human operator: ${label}` : "Human operator";
  }
  if (sender === "agent") {
    return "Agent";
  }
  return "System";
}

function chatStatusLabel(status) {
  const labels = {
    created: "Created",
    dispatching: "Dispatching",
    queued: "Queued",
    running: "Sent",
    delivered_in_dispatch: "Delivered",
    acknowledged: "Handled",
    replied: "Replied",
    failed: "Failed",
    sent: "Sent",
    ended: "Ended",
  };
  return labels[status] || humanizeToken(status);
}

function formatChatTimestamp(value) {
  const raw = text(value);
  if (!raw) {
    return "";
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return raw;
  }
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderPeopleAgentDetail(agent, emptyMessage = "No agent selected.") {
  if (!agent) {
    return `
      <div class="agent-detail-empty">
        <p class="empty-state">${escapeHtml(emptyMessage)}</p>
      </div>
    `;
  }

  const details = agentCardData(agent);
  const snapshot = agentWorkSnapshot(details);
  const waitingRows = snapshot.waiting.length
    ? snapshot.waiting.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
    : `<li>Clear</li>`;
  const runtimeRows = agentRuntimeDetailRows(details);
  const projectValue = details.projectIds.length ? details.projectLabel || details.projectIds.join(", ") : "None";

  return `
    <div class="agent-detail">
      <div class="agent-detail-header">
        ${renderAgentAvatar(details, "agent-avatar-large")}
        <div class="agent-detail-title">
          <h3>${escapeHtml(details.label)}</h3>
          <div class="meta-line">${escapeHtml(details.role)} - ${escapeHtml(details.id)}</div>
        </div>
        <div class="agent-detail-badges">
          ${renderAgentRosterStatusPill(details)}
          ${renderAgentSessionPill(details)}
          ${renderAgentAwakePill(details)}
          ${renderAgentInboxHeaderIndicators(details)}
        </div>
      </div>
      <div class="record-actions agent-detail-actions">
        ${renderAgentStandDownControls(details.id, details)}
        ${renderOpenAgentSessionButton(details.sessionId)}
        ${["every_wake", "work_lineage"].includes(details.runtime.contextClearingPolicy) ? "" : `<button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(details.id)}">Clear context</button>`}
        ${editButton("agent", details.id)}
      </div>
      ${renderAgentVoiceCallPanel(details)}
      <div class="agent-detail-grid">
        ${renderAgentDetailField("Session", details.sessionId || "None")}
        ${renderAgentDetailField("Reports to", details.reportsToLabel)}
        ${renderAgentDetailField("Project", projectValue)}
        ${renderAgentDetailField("Subordinates", details.directSubordinateCount)}
        ${renderAgentDetailField("Open tasks", `${details.taskStats.openCount} / ${details.taskStats.totalCount}`)}
        ${renderAgentDetailField("Ephemeral", `${details.ephemeralCurrentCount} / ${details.ephemeralCapacity}`)}
      </div>
      <section class="agent-detail-section">
        <h3>Work</h3>
        <div class="agent-detail-work-line">
          <span>Now</span>
          <strong>${escapeHtml(snapshot.primary)}</strong>
        </div>
        <div class="meta-line">Waiting</div>
        <ul class="inline-list agent-detail-list">
          ${waitingRows}
        </ul>
        ${renderAgentOpenTasks(details.taskStats.openTasks)}
      </section>
      <section class="agent-detail-section">
        <h3>Runtime</h3>
        <ul class="inline-list agent-detail-list">
          ${runtimeRows.map((row) => `<li>${escapeHtml(row)}</li>`).join("")}
        </ul>
      </section>
      <section class="agent-detail-section">
        <h3>Inbox</h3>
        ${renderAgentInbox(details.id) || `<p class="empty-state">No pending inbox.</p>`}
      </section>
    </div>
  `;
}

function renderAgentVoiceCallPanel(details) {
  const call = activeAgentVoiceCall?.agentId === details.id ? activeAgentVoiceCall : null;
  const liveCallForOtherAgent =
    activeAgentVoiceCall && !agentVoiceCallIsTerminal(activeAgentVoiceCall) && activeAgentVoiceCall.agentId !== details.id;
  const callDisabled = details.status === "archived" || liveCallForOtherAgent;
  const voiceProfileLabel = agentVoiceProfileLabel(details.voice_profile);
  const transcriptRows = (call?.transcript || [])
    .slice(-4)
    .map((entry) => {
      const speaker = text(entry.speaker) || "voice";
      return `<li><span>${escapeHtml(speaker)}</span>${escapeHtml(entry.text)}</li>`;
    })
    .join("");
  if (!call || agentVoiceCallIsTerminal(call)) {
    return `
      <section class="agent-detail-section agent-voice-call-panel">
        <div class="agent-voice-call-header">
          <h3>Voice</h3>
          <span class="state-pill neutral">idle</span>
        </div>
        <div class="record-actions agent-voice-call-controls">
          <button class="record-action" type="button" data-agent-call-agent-id="${escapeAttr(details.id)}" ${callDisabled ? "disabled" : ""}>Call</button>
        </div>
        <div class="meta-line">Voice: ${escapeHtml(voiceProfileLabel)}</div>
        ${call?.error ? `<div class="meta-line agent-voice-error">${escapeHtml(call.error)}</div>` : ""}
      </section>
    `;
  }
  const retryEnd = call.cleanupRequested || call.status === "end_failed";
  const endingDisabled = call.status === "ending" ? "disabled" : "";
  return `
    <section class="agent-detail-section agent-voice-call-panel">
      <div class="agent-voice-call-header">
        <h3>Voice</h3>
        <span class="state-pill ${escapeAttr(agentVoiceCallPillClass(call))}">${escapeHtml(agentVoiceCallStatusLabel(call))}</span>
      </div>
      <div class="record-actions agent-voice-call-controls">
        ${retryEnd ? "" : `<button class="record-action" type="button" data-agent-call-mute>${call.muted ? "Unmute" : "Mute"}</button>`}
        <button class="record-action" type="button" data-agent-call-end ${endingDisabled}>${retryEnd ? "Retry end" : "End"}</button>
      </div>
      <div class="meta-line">Voice: ${escapeHtml(voiceProfileLabel)}</div>
      ${call.error ? `<div class="meta-line agent-voice-error">${escapeHtml(call.error)}</div>` : ""}
      ${transcriptRows ? `<ol class="agent-voice-transcript">${transcriptRows}</ol>` : ""}
    </section>
  `;
}

function agentVoiceProfileLabel(profile) {
  const voice = text(profile?.voice) || "Default";
  const style = text(profile?.speaking_style);
  return style ? `${voice} - custom style` : voice;
}

function agentVoiceCallIsTerminal(call) {
  return !call || call.status === "ended" || call.status === "failed";
}

function agentVoiceCallStatusLabel(call) {
  const status = text(call?.status) || "idle";
  if (status === "active") {
    return "live";
  }
  if (status === "querying") {
    return "querying";
  }
  if (status === "ending") {
    return "ending";
  }
  if (status === "end_failed") {
    return "end failed";
  }
  return status;
}

function agentVoiceCallPillClass(call) {
  const status = text(call?.status);
  if (status === "active" || status === "querying") {
    return "accepted";
  }
  if (status === "failed") {
    return "rejected";
  }
  if (status === "end_failed") {
    return "rejected";
  }
  if (status === "connecting" || status === "ending") {
    return "blocked";
  }
  return "neutral";
}

function renderAgentDetailField(label, value) {
  return `
    <div class="agent-detail-field">
      <span class="agent-detail-label">${escapeHtml(label)}</span>
      <strong class="agent-detail-value">${escapeHtml(value)}</strong>
    </div>
  `;
}

function renderOpenAgentSessionButton(sessionId) {
  const normalizedSessionId = text(sessionId);
  if (!normalizedSessionId) {
    return "";
  }
  return `<button class="record-action" type="button" data-open-session-id="${escapeAttr(normalizedSessionId)}">Open session</button>`;
}

function navigateToAgentSession(sessionId) {
  const normalizedSessionId = text(sessionId);
  if (!normalizedSessionId || !window.parent?.postMessage) {
    return;
  }
  window.parent.postMessage(
    {
      type: "admin-navigate-to-session",
      session_id: normalizedSessionId,
    },
    window.location.origin,
  );
}

function agentRuntimeDetailRows(details) {
  const sessionLine = sessionHealthRuntimeLine(details.sessionHealth, details.sessionId);
  const runtime = details.runtime || {};
  if (!runtime.present) {
    return [sessionLine, "No runtime snapshot"].filter(Boolean);
  }
  const rows = [sessionLine, runtime.summary || runtime.status || "Runtime idle"].filter(Boolean);
  if (runtime.lastHandoffSummary) {
    rows.push(`Last handoff ${runtime.lastHandoffSummary}`);
  }
  if (runtime.contextClearingPolicy !== "off" || runtime.contextRotationPending) {
    rows.push(runtime.contextClearingPolicy === "work_lineage"
      ? "Context routing: Work lineage"
      : `Context clearing ${contextClearingPolicyLabel(runtime.contextClearingPolicy)}${
        runtime.contextRotationPending ? " - pending" : ""
      }${runtime.contextIdleRotationDueAt ? ` - idle grace until ${runtime.contextIdleRotationDueAt}` : ""}`,
    );
  }
  if (!["every_wake", "work_lineage"].includes(runtime.contextClearingPolicy)) {
    if (!runtime.lastContextRotatedAt) {
      rows.push("Latest context reset: no completed context reset yet");
    } else {
      rows.push(`Latest context reset: ${formatTimelineTimestamp(runtime.lastContextRotatedAt)}`);
      rows.push(
        runtime.inputTokensSinceContextReset === null
          ? "Input tokens since latest reset: unavailable"
          : `Input tokens since latest reset: ${formatCount(runtime.inputTokensSinceContextReset)}`,
      );
    }
  }
  return rows;
}

function renderAgentOpenTasks(openTasks) {
  if (!openTasks.length) {
    return "";
  }
  return `
    <div class="meta-line">Open tasks:</div>
    <ul class="inline-list">
      ${openTasks
        .map((task) => {
          return `<li>${escapeHtml(task.title)} <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(taskStateLabel(task))}</span></li>`;
        })
        .join("")}
    </ul>
  `;
}

function renderRoles() {
  if (!document.querySelector("[data-roles-view]")) {
    return;
  }
  const allRoles = values(state.roles).sort(compareTeamRoles);
  const roles = allRoles.filter(isVisibleRole);
  if (!allRoles.length) {
    renderIntoActiveTargets("[data-roles-view]", `<p class="empty-state">No roles registered.</p>`);
    return;
  }
  if (!roles.length) {
    renderIntoActiveTargets("[data-roles-view]", `<p class="empty-state">No visible roles.</p>`);
    return;
  }
  renderIntoActiveTargets("[data-roles-view]", `
    <ul class="review-list">
      ${roles
        .map((role) => {
          const scope = role.project_id ? `project ${role.project_id}` : `team ${role.team_id}`;
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(role.name)}</strong>
                <div class="record-actions">
                  <span class="state-pill ${role.archived ? "blocked" : "accepted"}">${role.archived ? "archived" : "active"}</span>
                  ${editButton("role", role.id)}
                </div>
              </div>
              <div class="meta-line">${escapeHtml(role.id)} - ${escapeHtml(scope)} - ${renderRolePermissionsInline(role.permissions)}</div>
              ${role.description ? `<div class="meta-line">${escapeHtml(role.description)}</div>` : ""}
              <div class="meta-line">Max ephemeral ${escapeHtml(role.ephemeral_policy?.max_concurrent ?? 0)}</div>
            </li>
          `;
        })
        .join("")}
    </ul>
  `);
}

function pendingInboxForAgent(agentId) {
  return values(state.inbox)
    .filter(
      (item) =>
        item.recipient_agent_id === agentId &&
        item.state !== "handled" &&
        pendingInboxItemIsCurrent(item),
    )
    .sort(compareWorkspaceInboxItems);
}

function pendingInboxItemIsCurrent(item) {
  const runtimeRow = (state.runtime_operations?.attention || []).find(
    (row) => text(row.inbox_item_id) === text(item.id),
  );
  if (runtimeRow) {
    return runtimeAttentionGroup(runtimeRow) === "active_obligation";
  }
  const task = item.task_id ? state.tasks?.[item.task_id] : null;
  if (task && terminalTaskStates.has(text(task.state))) {
    return false;
  }
  return item.response_requested !== false;
}

function agentTaskStats(agentId) {
  const tasks = orderedTasksForAgent(agentId);
  const openTasks = tasks.filter((task) => !terminalTaskStates.has(task.state));
  return {
    tasks,
    openTasks,
    openCount: openTasks.length,
    blockedWaitingCount: openTasks.filter((task) => task.state === "blocked" || task.state === "waiting_for_review").length,
    totalCount: tasks.length,
  };
}

function ephemeralCapacity(agent) {
  return numberOrZero(agent?.ephemeral_policy?.max_concurrent);
}

function projectLabel(projectOrId) {
  const projectId = text(projectOrId?.id ?? projectOrId);
  const project =
    projectOrId && typeof projectOrId === "object"
      ? projectOrId
      : state.projects[projectId];
  if (!project) {
    return projectId;
  }
  return text(project.name) || text(project.id) || projectId;
}

function agentProjectIds(agent) {
  const directIds = uniqueTextList(agent?.project_ids);
  if (directIds.length) {
    return directIds;
  }
  const assignmentIds = Array.isArray(agent?.project_assignments)
    ? agent.project_assignments.map((assignment) => text(assignment?.project_id)).filter(Boolean)
    : [];
  if (assignmentIds.length) {
    return uniqueTextList(assignmentIds);
  }
  const legacyProjectId = text(agent?.project_id);
  return legacyProjectId ? [legacyProjectId] : [];
}

function agentEditProjectId(agent) {
  const memberAssignmentIds = Array.isArray(agent?.project_assignments)
    ? agent.project_assignments
        .filter((assignment) => text(assignment?.kind || "project_member") === "project_member")
        .map((assignment) => text(assignment?.project_id))
        .filter(Boolean)
    : [];
  if (memberAssignmentIds.length) {
    return uniqueTextList(memberAssignmentIds)[0];
  }
  return agentProjectIds(agent)[0] || "";
}

function agentProjectSummary(agent) {
  return agentProjectIds(agent).map(projectLabel).filter(Boolean).join(", ");
}

function agentStatus(agent) {
  return text(agent?.status) || "active";
}

function agentSessionBackend(agentData) {
  const health = agentData?.sessionHealth || {};
  return text(health.agentExecutionBackend || health.executionBackend || health.vmProvider).toLowerCase();
}

function agentUsesContainerBackend(agentData) {
  return agentSessionBackend(agentData) === "container_worker_vm";
}

function agentHasActiveRuntime(agentData) {
  const runtimeRow = runtimeOperationForAgent(agentData?.id);
  const activeDispatchId = text(runtimeRow?.active_dispatch_id || agentData?.runtime?.activeDispatchId);
  if (activeDispatchId) {
    return true;
  }
  const health = agentData?.sessionHealth || {};
  const activeExecutionCount = Number(health.activeExecutionCount);
  if (Number.isFinite(activeExecutionCount) && activeExecutionCount > 0) {
    return true;
  }
  const executionStatus = text(health.agentExecutionStatus).toLowerCase();
  return ["preparing", "starting", "running"].includes(executionStatus);
}

function renderAgentRosterStatusPill(agentData) {
  const status = text(agentData?.status) || "active";
  if (!status) {
    return "";
  }
  if (status === "active" && agentUsesContainerBackend(agentData) && !agentHasActiveRuntime(agentData)) {
    return "";
  }
  return `<span class="state-pill ${escapeHtml(status)}">${escapeHtml(status)}</span>`;
}

function agentCardData(agent) {
  const taskStats = agentTaskStats(agent.id);
  const runtime = runtimeSummary(agent.id);
  const directSubordinateIds = uniqueTextList(agent?.subordinate_agent_ids);
  const projectIds = agentProjectIds(agent);
  const projectName = projectIds.map(projectLabel).join(", ");
  const reportsToId = text(agent?.reports_to_agent_id);
  const ephemeralCurrentCount = activeEphemeralAssignmentsForAgent(agent.id).length;
  const inboxItems = pendingInboxForAgent(agent.id);
  const standDowns = effectiveStandDowns(agent);
  const inboxIndicatorItems = {
    status_request: inboxItems.filter((item) => text(item.item_type) === "status_request"),
    progress_report: inboxItems.filter((item) => text(item.item_type) === "progress_report"),
  };
  const activitySummary = normalizeActivitySummary(
    agent?.activity_summary || state.agent_activity_summaries?.[text(agent?.id)],
  );

  return {
    id: text(agent?.id),
    label: agentLabel(agent),
    role: agentRoleLabel(agent),
    roleId: text(agent?.role_id),
    avatar: agent?.avatar || null,
    voice_profile: agent?.voice_profile || null,
    status: agentStatus(agent),
    sessionId: text(agent?.session_id),
    projectId: projectIds[0] || null,
    projectIds,
    projectLabel: projectName || null,
    reportsToLabel: reportsToId || "operator",
    directSubordinateCount: directSubordinateIds.length,
    taskStats,
    inboxCount: inboxItems.length,
    inboxIndicatorItems,
    runtime,
    sessionHealth: agentSessionHealth(agent.id),
    activitySummary,
    effectiveStandDowns: standDowns,
    standDownActive: Boolean(agent?.stand_down_active) || standDowns.length > 0,
    ephemeralCapacity: ephemeralCapacity(agent),
    ephemeralCurrentCount,
  };
}

function normalizeActivitySummary(summary) {
  if (!summary || typeof summary !== "object") {
    return null;
  }
  const headline = text(summary.headline);
  const workingOn = text(summary.working_on);
  const waitingFor = text(summary.waiting_for);
  if (!headline && !workingOn && !waitingFor) {
    return null;
  }
  return {
    headline,
    working_on: workingOn,
    waiting_for: waitingFor,
    tone: activityToneClass(summary.tone),
    source: text(summary.source) || "derived",
    generated_at: text(summary.generated_at),
    reason_refs: Array.isArray(summary.reason_refs) ? summary.reason_refs : [],
  };
}

function activityToneClass(tone) {
  const normalized = text(tone).toLowerCase();
  return ["working", "waiting", "blocked", "idle", "attention", "offline"].includes(normalized)
    ? normalized
    : "";
}

function effectiveStandDowns(record) {
  const values = Array.isArray(record?.effective_stand_downs)
    ? record.effective_stand_downs
    : Array.isArray(record?.effectiveStandDowns)
      ? record.effectiveStandDowns
      : [];
  return values.filter((item) => item && typeof item === "object");
}

function revocableRootStandDown(agentId, record) {
  const normalizedAgentId = text(agentId);
  return effectiveStandDowns(record).find((item) => {
    return ["operator", "superior"].includes(text(item.source))
      && text(item.agent_id) === normalizedAgentId
      && text(item.root_agent_id || item.agent_id) === normalizedAgentId
      && !Boolean(item.inherited);
  }) || null;
}

function renderAgentStandDownControls(agentId, record) {
  const normalizedAgentId = text(agentId);
  if (!normalizedAgentId) {
    return "";
  }
  const standDowns = effectiveStandDowns(record);
  const active = Boolean(record?.standDownActive || record?.stand_down_active) || standDowns.length > 0;
  if (!active) {
    return `
      <button class="record-action" type="button" data-stand-down-agent-id="${escapeAttr(normalizedAgentId)}" data-stand-down-scope="agent">Stand down</button>
      <button class="record-action" type="button" data-stand-down-agent-id="${escapeAttr(normalizedAgentId)}" data-stand-down-scope="subtree">Stand down subtree</button>
    `;
  }
  const revocableRoot = revocableRootStandDown(normalizedAgentId, record);
  const summary = agentStandDownSummary({ effectiveStandDowns: standDowns });
  const unavailableReason = standDownRevokeUnavailableReason(normalizedAgentId, standDowns);
  return `
    <span class="state-pill blocked" title="${escapeAttr(summary)}">stood down</span>
    ${
      revocableRoot
        ? `<button class="record-action" type="button" data-stand-down-revoke-agent-id="${escapeAttr(normalizedAgentId)}">Revoke stand-down</button>`
        : `<button class="record-action" type="button" data-stand-down-revoke-agent-id="${escapeAttr(normalizedAgentId)}" disabled aria-disabled="true" title="${escapeAttr(unavailableReason)}">Revoke stand-down</button>`
    }
  `;
}

function standDownRevokeUnavailableReason(agentId, standDowns) {
  const primary = standDowns[standDowns.length - 1];
  if (!primary) {
    return "This stand-down cannot be revoked here because its source details are unavailable.";
  }
  const rootAgentId = text(primary.root_agent_id || primary.agent_id);
  const rootAgent = state.agents?.[rootAgentId];
  const rootLabel = rootAgent ? agentLabel(rootAgent) : rootAgentId || "another agent";
  const inherited = Boolean(primary.inherited) || (rootAgentId && rootAgentId !== text(agentId));
  if (text(primary.source) === "superior") {
    return inherited
      ? `This stand-down is inherited from ${rootLabel}. Revoke it on ${rootLabel}.`
      : "This stand-down cannot be revoked here. Revoke it at its source.";
  }
  if (inherited) {
    return `This stand-down is inherited from ${rootLabel}. Revoke the stand-down on that agent.`;
  }
  return "This stand-down cannot be revoked here. Revoke it at its source.";
}

function agentStandDownSummary(record) {
  const standDowns = effectiveStandDowns(record);
  const primary = standDowns[standDowns.length - 1] || {};
  const source = text(primary.source) || "stand-down";
  const scope = text(primary.scope) || "subtree";
  const root = text(primary.root_agent_id || primary.agent_id);
  const reason = text(primary.reason);
  const inherited = Boolean(primary.inherited) && root ? ` inherited from ${root}` : "";
  const direct = scope === "agent" ? " (agent only)" : "";
  return `${humanizeToken(source)} stand-down${direct}${inherited}${reason ? `: ${reason}` : ""}`;
}

function runtimeSummary(agentId) {
  const runtime = state.agent_runtimes?.[agentId];
  if (!runtime) {
    return {
      present: false,
      status: "",
      summary: "",
      activeDispatchId: null,
      wakeReason: null,
      nextWakeAt: null,
      wakeLeasedUntil: null,
      lastWakeAt: null,
      lowPriorityBatchStartedAt: null,
      lowPriorityBatchCycleCount: 0,
      lowPriorityBatchDueAfterCycles: null,
      contextClearingPolicy: "off",
      contextClearingIdleGraceSeconds: 0,
      autoContextClearingEnabled: false,
      contextRotationPending: false,
      contextIdleRotationDueAt: null,
      lastContextRotatedAt: null,
      inputTokensSinceContextReset: null,
      lastHandoffSummary: null,
    };
  }
  const activeDispatchId = text(runtime.active_dispatch_id);
  const wakeReason = text(runtime.wake_reason);
  const nextWakeAt = text(runtime.next_wake_at);
  const wakeLeasedUntil = text(runtime.wake_leased_until);
  const lastWakeAt = text(runtime.last_wake_at);
  const lowPriorityBatchStartedAt = text(runtime.low_priority_batch_started_at);
  const lowPriorityBatchCycleCount = numberOrZero(runtime.low_priority_batch_cycle_count);
  const lowPriorityBatchDueAfterCycles = numberOrNull(runtime.low_priority_batch_due_after_cycles);
  const contextRotationPending = Boolean(runtime.context_rotation_pending);
  const contextClearingPolicy = contextClearingPolicyValue(runtime);
  const contextClearingIdleGraceSeconds = numberOrZero(
    runtime.context_clearing_idle_grace_seconds,
  );
  const contextIdleRotationDueAt = text(runtime.context_idle_rotation_due_at);
  const autoContextClearingEnabled = contextClearingPolicy !== "off";
  const lastContextRotatedAt = text(runtime.last_context_rotated_at);
  const inputTokensSinceContextReset = numberOrNull(
    runtime.context_reset_usage?.input_tokens_since_reset,
  );
  const parts = [];
  if (activeDispatchId) {
    parts.push(`dispatch ${activeDispatchId}`);
  }
  if (wakeReason) {
    parts.push(`wake ${wakeReason}`);
  }
  if (nextWakeAt) {
    parts.push(`next wake ${nextWakeAt}`);
  }
  if (wakeLeasedUntil) {
    parts.push(`leased until ${wakeLeasedUntil}`);
  }
  if (lastWakeAt) {
    parts.push(`last wake ${lastWakeAt}`);
  }
  if (lowPriorityBatchStartedAt && lowPriorityBatchDueAfterCycles !== null) {
    parts.push(`low-priority batch ${lowPriorityBatchCycleCount}/${lowPriorityBatchDueAfterCycles}`);
  }
  if (contextRotationPending) {
    parts.push("context clearing pending");
  } else if (contextIdleRotationDueAt) {
    parts.push(`idle context grace until ${contextIdleRotationDueAt}`);
  }
  if (lastContextRotatedAt && !["every_wake", "work_lineage"].includes(contextClearingPolicy)) {
    parts.push(`last context reset ${lastContextRotatedAt}`);
  }
  return {
    present: true,
    status: activeDispatchId ? "busy" : wakeReason ? `wake ${wakeReason}` : "idle",
    summary: parts.join(" - "),
    activeDispatchId: activeDispatchId || null,
    wakeReason: wakeReason || null,
    nextWakeAt: nextWakeAt || null,
    wakeLeasedUntil: wakeLeasedUntil || null,
    lastWakeAt: lastWakeAt || null,
    lowPriorityBatchStartedAt: lowPriorityBatchStartedAt || null,
    lowPriorityBatchCycleCount,
    lowPriorityBatchDueAfterCycles,
    contextClearingPolicy,
    contextClearingIdleGraceSeconds,
    autoContextClearingEnabled,
    contextRotationPending,
    contextIdleRotationDueAt: contextIdleRotationDueAt || null,
    lastContextRotatedAt: lastContextRotatedAt || null,
    inputTokensSinceContextReset,
    lastHandoffSummary: text(runtime.last_handoff_summary) || null,
  };
}

function runtimeOperationForAgent(agentId) {
  return (state.runtime_operations?.agents || []).find((row) => {
    return text(row?.id || row?.agent_id) === text(agentId);
  }) || null;
}

function agentSessionHealth(agentId) {
  const normalizedAgentId = text(agentId);
  const health = (state.runtime_operations?.session_health || []).find((row) => {
    return text(row?.agent_id || row?.id) === normalizedAgentId;
  });
  if (health) {
    return normalizeAgentSessionHealth(health);
  }
  const runtimeRow = runtimeOperationForAgent(normalizedAgentId);
  if (runtimeRow && runtimeRow.session_is_expired !== undefined) {
    return normalizeAgentSessionHealth(runtimeRow);
  }
  return {
    available: false,
    present: null,
    isExpired: false,
    expiresAt: "",
    status: "",
  };
}

function normalizeAgentSessionHealth(row) {
  const status = text(row?.status || row?.session_status);
  return {
    available: true,
    present: row?.session_present === undefined ? null : Boolean(row.session_present),
    isExpired: Boolean(row?.is_expired || row?.session_is_expired || status === "expired"),
    expiresAt: text(row?.expires_at || row?.session_expires_at),
    vmProvider: text(row?.vm_provider || row?.vmProvider),
    executionBackend: text(row?.execution_backend || row?.executionBackend),
    containerLifecycleState: text(row?.container_lifecycle_state || row?.containerLifecycleState),
    agentExecutionBackend: text(row?.agent_execution_backend || row?.agentExecutionBackend),
    agentExecutionStatus: text(row?.agent_execution_status || row?.agentExecutionStatus),
    activeExecutionCount: row?.agent_active_execution_count ?? row?.agentActiveExecutionCount ?? null,
    latestExecutionId: text(row?.agent_latest_execution_id || row?.agentLatestExecutionId),
    status,
  };
}

function sessionHealthRuntimeLine(health, sessionId) {
  if (!health?.available) {
    return "";
  }
  const sessionLabel = text(sessionId) || "session";
  if (health.isExpired) {
    return `Session ${sessionLabel} expired${health.expiresAt ? ` at ${health.expiresAt}` : ""}`;
  }
  if (health.present === false) {
    return `Session ${sessionLabel} not found in the session store`;
  }
  if (health.expiresAt) {
    return `Session ${sessionLabel} expires ${health.expiresAt}`;
  }
  return "";
}

function renderAgentSessionPill(agentData) {
  const health = agentData.sessionHealth || {};
  if (!health.available || !health.isExpired) {
    return "";
  }
  const detail = sessionHealthRuntimeLine(health, agentData.sessionId) || "Session expired";
  return `<span class="state-pill critical" title="${escapeAttr(detail)}">session expired</span>`;
}

function agentAwakeState(agentData) {
  if (agentData.sessionHealth?.isExpired) {
    return {
      kind: "expired",
      label: "Expired",
      detail: sessionHealthRuntimeLine(agentData.sessionHealth, agentData.sessionId) || "session expired",
    };
  }
  const executionState = agentExecutionAwakeState(agentData);
  const runtimeRow = runtimeOperationForAgent(agentData.id);
  const activeDispatchId = text(runtimeRow?.active_dispatch_id || agentData.runtime?.activeDispatchId);
  if (executionState.known && !executionState.active && activeDispatchId) {
    return {
      kind: "idle",
      label: "Idle",
      detail: `${executionState.detail} - stale dispatch ${activeDispatchId}`,
    };
  }
  if (activeDispatchId) {
    return {
      kind: "awake",
      label: "Awake",
      detail: `dispatch ${activeDispatchId}`,
    };
  }
  if (executionState.active) {
    return {
      kind: "awake",
      label: "Awake",
      detail: executionState.detail,
    };
  }
  if (Boolean(runtimeRow?.lease_active) || isFutureTimestamp(agentData.runtime?.wakeLeasedUntil)) {
    if (executionState.known && !executionState.active) {
      return {
        kind: "idle",
        label: "Idle",
        detail: `${executionState.detail} - stale lease`,
      };
    }
    return {
      kind: "awake",
      label: "Awake",
      detail: "lease active",
    };
  }
  if (Boolean(runtimeRow?.wake_due)) {
    return {
      kind: "due",
      label: "Due",
      detail: "wake due",
    };
  }
  if (agentData.runtime?.present) {
    return {
      kind: "idle",
      label: "Idle",
      detail: "runtime idle",
    };
  }
  return {
    kind: "unknown",
    label: "No runtime",
    detail: "no runtime snapshot",
  };
}

function agentExecutionAwakeState(agentData) {
  const health = agentData?.sessionHealth || {};
  const activeExecutionCount = Number(health.activeExecutionCount);
  if (Number.isFinite(activeExecutionCount) && activeExecutionCount > 0) {
    return {
      known: true,
      active: true,
      detail: `${activeExecutionCount} active execution${activeExecutionCount === 1 ? "" : "s"}`,
    };
  }
  const executionStatus = text(health.agentExecutionStatus).toLowerCase();
  if (!executionStatus) {
    return { known: false, active: false, detail: "" };
  }
  if (["preparing", "starting", "running"].includes(executionStatus)) {
    return {
      known: true,
      active: true,
      detail: `execution ${executionStatus}`,
    };
  }
  return {
    known: true,
    active: false,
    detail: `execution ${executionStatus}`,
  };
}

function isFutureTimestamp(value) {
  const raw = text(value);
  if (!raw) {
    return false;
  }
  const timestamp = Date.parse(raw);
  return Number.isFinite(timestamp) && timestamp > Date.now();
}

function renderAgentAwakePill(agentData) {
  const awake = agentAwakeState(agentData);
  return `
    <span class="awake-indicator awake-indicator--${escapeAttr(awake.kind)}" title="${escapeAttr(awake.detail)}">
      <span class="awake-dot" aria-hidden="true"></span>
      ${escapeHtml(awake.label)}
    </span>
  `;
}

function renderAgentInboxHeaderIndicators(agentData, { interactive = true } = {}) {
  const indicatorItems = agentData.inboxIndicatorItems || {};
  return `
    <span class="agent-inbox-indicators" aria-label="Pending inbox summary">
      ${renderAgentInboxHeaderIndicator({
        itemType: "status_request",
        direction: "down",
        label: "Status requests",
        items: indicatorItems.status_request || [],
        interactive,
      })}
      ${renderAgentInboxHeaderIndicator({
        itemType: "progress_report",
        direction: "up",
        label: "Progress reports",
        items: indicatorItems.progress_report || [],
        interactive,
      })}
    </span>
  `;
}

function renderAgentInboxHeaderIndicator({ itemType, direction, label, items, interactive = true }) {
  const rows = Array.isArray(items) ? items : [];
  const count = rows.length;
  const stateClass = count ? "agent-inbox-indicator--active" : "agent-inbox-indicator--empty";
  return `
    <span
      class="agent-inbox-indicator agent-inbox-indicator--${escapeAttr(itemType)} ${stateClass}"
      ${interactive ? `tabindex="0"` : ""}
      aria-label="${escapeAttr(`${label}: ${count}`)}"
    >
      ${renderAgentInboxIndicatorIcon(direction)}
      <strong>${count}</strong>
      ${count ? renderAgentInboxIndicatorPopover(label, rows) : ""}
    </span>
  `;
}

function renderAgentInboxIndicatorIcon(direction) {
  const arrow =
    direction === "up"
      ? `
        <path d="M10 8V3"></path>
        <path d="M7.3 5.7 10 3l2.7 2.7"></path>
      `
      : `
        <path d="M10 3v5"></path>
        <path d="M7.3 5.3 10 8l2.7-2.7"></path>
      `;
  return `
    <svg class="agent-inbox-icon agent-inbox-icon--${escapeAttr(direction)}" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      ${arrow}
      <path d="M5.5 8.2h9a2.2 2.2 0 0 1 2.2 2.2v2.6a2.2 2.2 0 0 1-2.2 2.2H9.3L5 18v-2.8a2.2 2.2 0 0 1-1.7-2.1v-2.7a2.2 2.2 0 0 1 2.2-2.2Z"></path>
    </svg>
  `;
}

function renderAgentInboxIndicatorPopover(label, items) {
  return `
    <span class="agent-inbox-popover" role="tooltip" aria-label="${escapeAttr(label)}">
      ${items.map((item) => renderAgentInboxBubble(item)).join("")}
    </span>
  `;
}

function renderAgentInboxBubble(item) {
  const detail = inboxItemDetail(item) || item.id;
  const metaParts = [];
  if (item.task_id) {
    metaParts.push(`task ${item.task_id}`);
  }
  if (item.sender_agent_id) {
    metaParts.push(`from ${item.sender_agent_id}`);
  }
  metaParts.push(inboxPriorityLabel(item));
  metaParts.push(inboxReplyIntentLabel(item));
  return `
    <span class="agent-inbox-bubble">
      <strong>${escapeHtml(detail)}</strong>
      ${metaParts.length ? `<small>${escapeHtml(metaParts.join(" - "))}</small>` : ""}
    </span>
  `;
}

function agentWorkSnapshot(agentData) {
  const activitySummary = agentData.activitySummary || null;
  const openTasks = agentData.taskStats.openTasks || [];
  const waitingTasks = openTasks.filter(isTaskWaitingOrBlocked);
  const currentTask = openTasks.find((task) => !isTaskWaitingOrBlocked(task)) || openTasks[0] || null;
  const inboxItems = pendingInboxForAgent(agentData.id);
  const runtimeAttention = agentRuntimeAttentionRows(agentData.id);
  const activeAssignments = activeEphemeralAssignmentsForAgent(agentData.id);
  const primary = text(activitySummary?.headline) || text(activitySummary?.working_on) || (currentTask
    ? `${currentTask.title} (${taskStateLabel(currentTask)})`
    : inboxItems[0]
      ? `${inboxItemLabels[inboxItems[0].item_type] || inboxItems[0].item_type}: ${inboxItemDetail(inboxItems[0]) || inboxItems[0].id}`
      : activeAssignments[0]
        ? `${activeAssignments[0].title || activeAssignments[0].id} (${humanizeToken(activeAssignments[0].state)})`
        : "No open work");
  const waiting = [];
  const summaryWaiting = text(activitySummary?.waiting_for);
  if (summaryWaiting && summaryWaiting.toLowerCase() !== "clear") {
    waiting.push(summaryWaiting);
  }
  waitingTasks.slice(0, 2).forEach((task) => {
    const label = `${task.title} (${taskStateLabel(task)})`;
    if (!waiting.includes(label)) {
      waiting.push(label);
    }
  });
  runtimeAttention.slice(0, 2).forEach((row) => {
    const label = row.title || row.id;
    if (label && !waiting.includes(label)) {
      waiting.push(`${label} (${humanizeToken(row.kind)})`);
    }
  });
  return {
    primary,
    waiting,
    inboxItems,
    runtimeAttention,
    activeAssignments,
  };
}

function isTaskWaitingOrBlocked(task) {
  return (
    task.state === "blocked" ||
    task.state === "waiting_for_input" ||
    task.state === "waiting_for_review" ||
    task.dependency_status === "waiting" ||
    task.dependency_status === "blocked" ||
    Boolean((task.blocked_by_task_ids || []).length)
  );
}

function agentRuntimeAttentionRows(agentId) {
  return (state.runtime_operations?.attention || []).filter((row) => {
    return (
      text(row.agent_id) === text(agentId) &&
      runtimeAttentionGroup(row) === "active_obligation"
    );
  });
}

function renderOrgNodeWorkSummary(agentData) {
  const summary = orgNodeWorkSummaryData(agentData);
  return `
    <div class="org-node-work">
      <div class="org-node-work-line org-node-work-line--now">
        <span>Now</span>
        <strong>${escapeHtml(summary.primary)}</strong>
      </div>
      <div class="org-node-work-line org-node-work-line--waiting">
        <span>Waiting</span>
        <strong>${escapeHtml(summary.waiting)}</strong>
      </div>
    </div>
  `;
}

function orgNodeWorkSummaryData(agentData) {
  const snapshot = agentWorkSnapshot(agentData);
  return {
    primary: snapshot.primary,
    waiting: snapshot.waiting.length
      ? snapshot.waiting[0]
      : snapshot.inboxItems.length
        ? `${snapshot.inboxItems.length} pending inbox`
        : "Clear",
  };
}

function hierarchyDetailLines(agentData, { focused = false } = {}) {
  const lines = [];
  const awake = agentAwakeState(agentData);
  const snapshot = agentWorkSnapshot(agentData);

  if (focused) {
    if (agentData.activitySummary?.source) {
      lines.push(`Activity summary ${agentData.activitySummary.source} - ${agentData.activitySummary.tone || "unknown"}`);
    }
    lines.push(`Awake status ${awake.label} - ${awake.detail}`);
    lines.push(`Current work ${snapshot.primary}`);
    if (snapshot.waiting.length) {
      lines.push(`Waiting on ${snapshot.waiting.join("; ")}`);
    }
    if (snapshot.inboxItems.length) {
      lines.push(
        `Inbox ${snapshot.inboxItems
          .slice(0, 2)
          .map((item) => `${inboxItemLabels[item.item_type] || item.item_type}: ${inboxPriorityLabel(item)}: ${inboxReplyIntentLabel(item)}: ${inboxItemDetail(item) || item.id}`)
          .join("; ")}`,
      );
    }
  }

  if (agentData.id) {
    lines.push(`Agent ${agentData.id}`);
  }
  if (agentData.sessionId) {
    lines.push(
      sessionHealthRuntimeLine(agentData.sessionHealth, agentData.sessionId) ||
        `Session ${agentData.sessionId}`,
    );
  }
  if (agentData.projectIds.length) {
    lines.push(`Projects ${agentData.projectLabel || agentData.projectIds.join(", ")}`);
  }
  lines.push(`Reports to ${agentData.reportsToLabel || "operator"}`);
  lines.push(`Direct subordinates ${agentData.directSubordinateCount}`);
  lines.push(`Open tasks ${agentData.taskStats.openCount}`);
  lines.push(`Blocked/waiting ${agentData.taskStats.blockedWaitingCount}`);
  lines.push(`Pending inbox ${agentData.inboxCount}`);
  if (agentData.runtime?.present) {
    const runtimeParts = [`Runtime ${agentData.runtime.status}`];
    if (agentData.runtime.summary) {
      runtimeParts.push(agentData.runtime.summary);
    }
    lines.push(runtimeParts.join(" - "));
  }
  lines.push(
    `Context clearing ${contextClearingPolicyLabel(agentData.runtime?.contextClearingPolicy || "off")}${
      agentData.runtime?.contextRotationPending ? " - pending" : ""
    }`,
  );
  lines.push(`Ephemeral ${agentData.ephemeralCurrentCount} / ${agentData.ephemeralCapacity}`);

  return lines;
}

function renderHierarchyDetails(agentData, { focused = false } = {}) {
  return hierarchyDetailLines(agentData, { focused })
    .map((line) => `<div class="meta-line">${escapeHtml(line)}</div>`)
    .join("");
}

function teamHierarchyGraph(team) {
  const teamId = text(team?.id);
  const managerId = text(team?.manager_agent_id);
  const teamAgents = values(state.agents)
    .filter((agent) => text(agent?.team_id) === teamId)
    .sort(comparePeopleAgents);
  const seen = new Set();

  function buildNode(agentId, ancestry = {}) {
    const agent = state.agents?.[agentId];
    if (!agent) {
      return {
        kind: "missing-subordinate",
        id: agentId,
        parentId: ancestry.parentId || null,
        teamId,
        label: `Missing subordinate ${agentId}`,
      };
    }
    if (ancestry.path?.has(agentId) || seen.has(agentId)) {
      return {
        kind: "cycle",
        id: agentId,
        parentId: ancestry.parentId || null,
        teamId,
        label: `Cycle detected at ${agentLabel(agent)}`,
      };
    }

    seen.add(agentId);

    const nextPath = new Set(ancestry.path || []);
    nextPath.add(agentId);
    const childIds = uniqueTextList(agent.subordinate_agent_ids);
    const children = [];
    childIds.forEach((childId) => {
      const child = state.agents?.[childId];
      if (!child) {
        children.push({
          kind: "missing-subordinate",
          id: childId,
          parentId: agentId,
          teamId,
          label: `Missing subordinate ${childId}`,
        });
        return;
      }
      if (text(child.team_id) !== teamId) {
        children.push({
          kind: "missing-subordinate",
          id: childId,
          parentId: agentId,
          teamId,
          label: `Subordinate ${childId} belongs to another team`,
        });
        return;
      }
      if (!showArchived && isArchivedAgent(child)) {
        return;
      }
      const childNode = buildNode(childId, { path: nextPath, parentId: agentId });
      if (childNode) {
        children.push(childNode);
      }
    });

    return {
      kind: "agent",
      id: agent.id,
      agentData: agentCardData(agent),
      children,
      directSubordinateCount: childIds.length,
      reachableSubordinateCount: children.filter((child) => child?.kind === "agent").length,
    };
  }

  const roots = [];
  const orphanRoots = [];
  if (managerId) {
    const manager = state.agents?.[managerId];
    if (!manager || text(manager.team_id) !== teamId) {
      roots.push({
        kind: "missing-manager",
        id: managerId,
        teamId,
        label: `Missing manager ${managerId}`,
      });
    } else if (showArchived || !isArchivedAgent(manager)) {
      const root = buildNode(managerId, { path: new Set(), parentId: null });
      if (root) {
        roots.push(root);
      }
    }
  } else {
    roots.push({
      kind: "missing-manager",
      id: "",
      teamId,
      label: "Missing manager",
    });
  }

  teamAgents.forEach((agent) => {
    if (!showArchived && isArchivedAgent(agent)) {
      return;
    }
    if (!seen.has(agent.id) && agent.id !== managerId) {
      const root = buildNode(agent.id, { path: new Set(), parentId: null });
      if (root) {
        orphanRoots.push(root);
      }
    }
  });

  return {
    teamId,
    managerId: managerId || null,
    roots,
    orphanRoots,
  };
}

function uniqueTextList(value) {
  const items = Array.isArray(value) ? value : [];
  const seen = new Set();
  const result = [];
  items.forEach((item) => {
    const entry = text(item);
    if (!entry || seen.has(entry)) {
      return;
    }
    seen.add(entry);
    result.push(entry);
  });
  return result;
}

function activeEphemeralAssignmentsForAgent(agentId) {
  return values(state.ephemeral_assignments).filter((assignment) => {
    return (
      assignment?.parent_agent_id === agentId &&
      ["requested", "spawn_pending", "running"].includes(text(assignment?.state))
    );
  });
}

function renderAgentInbox(agentId) {
  const items = pendingInboxForAgent(agentId);
  if (!items.length) {
    return "";
  }
  return `
    <div class="meta-line">Inbox:</div>
    <ul class="inline-list">
      ${items
        .map((item) => {
          const label = inboxItemLabels[item.item_type] || item.item_type;
          const detail = inboxItemDetail(item);
          const sender = item.sender_agent_id ? ` from ${escapeHtml(item.sender_agent_id)}` : "";
          const replyIntent = inboxReplyIntentLabel(item);
          return `
            <li>
              <span class="state-pill ${escapeHtml(item.item_type)}">${escapeHtml(label)}</span>
              <span class="state-pill neutral">${escapeHtml(inboxPriorityLabel(item))}</span>
              <span class="state-pill ${item.response_requested === false ? "neutral" : "info"}">${escapeHtml(replyIntent)}</span>
              ${detail ? `<span> ${escapeHtml(detail)}</span>` : ""}${sender}
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function inboxItemDetail(item) {
  const payload = item.payload || {};
  return text(payload.question || payload.message || payload.title || payload.summary || "");
}

function inboxReplyIntentLabel(item) {
  return item.response_requested === false ? "no reply needed" : "reply requested";
}

function inboxPriorityLabel(item) {
  const priority = text(item.priority) || "normal";
  return `${humanizeToken(priority)} priority`;
}

function renderProjects() {
  rememberOpenTeamDisclosures();
  const allProjects = values(state.projects).sort(compareTeamProjects);
  const projects = allProjects.filter(isVisibleProject);
  if (!allProjects.length) {
    renderIntoActiveTargets("[data-projects-view]", `<p class="empty-state">No projects registered.</p>`);
    return;
  }
  if (!projects.length) {
    renderIntoActiveTargets("[data-projects-view]", `<p class="empty-state">No visible projects.</p>`);
    return;
  }

  if (activeTeamView === "planning") {
    updatePlanningFilterControls();
    const projectCards = projects
      .map((project) => {
        const projectTasks = values(state.tasks)
          .filter((task) => task.project_id === project.id)
          .sort(byPriority);
        const visibleTasks = planningTasksForProject(projectTasks);
        if (!visibleTasks.length) {
          return null;
        }
        const visibleTaskIds = new Set(visibleTasks.map((task) => task.id));
        const projectDeliverables = values(state.deliverables).filter((deliverable) => {
          return visibleTaskIds.has(deliverable.task_id);
        });
        return {
          project,
          projectTasks,
          visibleTasks,
          projectDeliverables,
        };
      })
      .filter(Boolean)
      .sort(comparePlanningProjectCards);
    if (!projectCards.length) {
      renderIntoActiveTargets(
        "[data-projects-view]",
        `<p class="empty-state">No project task cards match the selected statuses.</p>`,
      );
      return;
    }
    renderIntoActiveTargets(
      "[data-projects-view]",
      projectCards
        .map(({ project, projectTasks, visibleTasks, projectDeliverables }) =>
          renderPlanningProjectBlock(project, projectTasks, visibleTasks, projectDeliverables),
        )
        .join(""),
    );
    return;
  }

  renderIntoActiveTargets(
    "[data-projects-view]",
    projects
      .map((project) => {
        const projectTasks = values(state.tasks)
          .filter((task) => task.project_id === project.id)
          .sort(byPriority);
        const projectDeliverables = values(state.deliverables).filter((deliverable) => {
          const task = state.tasks[deliverable.task_id];
          return task?.project_id === project.id;
        });
        return `
        <article class="project-block">
            <div class="project-header">
              <div>
                <strong>${escapeHtml(project.name)}</strong>
                <div class="meta-line">${escapeHtml(project.id)} - lead ${escapeHtml(project.lead_agent_id)}</div>
                ${renderProjectCostCounter(project.id)}
                ${renderProjectGit(project.git)}
                ${renderProjectReleasePolicy(project.release_policy)}
              </div>
              <div class="record-actions">
                <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
                ${editButton("project", project.id)}
              </div>
            </div>
          <div class="project-columns">
            <div>
              <p class="subheading">Goals</p>
              ${renderGoals(project.id, project.goals || [])}
              <p class="subheading">Deliverables</p>
              ${renderDeliverables(projectDeliverables)}
            </div>
            <div>
              <p class="subheading">Tasks</p>
              ${renderTasks(projectTasks)}
            </div>
          </div>
        </article>
      `;
      })
      .join(""),
  );
}

function renderProjectFocus() {
  const drafts = captureFormDrafts();
  rememberOpenTeamDisclosures();
  if (!document.querySelector("[data-projects-focus-list]")) {
    return;
  }
  const projectTargets = Array.from(
    document.querySelectorAll(
      "[data-projects-focus-list], [data-projects-focus-detail]",
    ),
  );
  if (!projectTargets.some(targetIsActive)) {
    projectTargets.forEach((target) => {
      target.innerHTML = "";
    });
    return;
  }
  syncProjectCreatePanel();
  const allProjects = values(state.projects).sort(compareTeamProjects);
  const projects = allProjects.filter(isVisibleProject);
  if (!projects.some((project) => project.id === selectedProjectFocusId)) {
    selectedProjectFocusId = projects[0]?.id || null;
    projectReviewProfileCreateProjectId = null;
  }
  const selectedProject = selectedProjectFocusId ? state.projects[selectedProjectFocusId] : null;

  document.querySelectorAll("[data-projects-focus-list]").forEach((target) => {
    if (!allProjects.length) {
      target.innerHTML = `<p class="empty-state">No projects registered.</p>`;
      return;
    }
    if (!projects.length) {
      target.innerHTML = `<p class="empty-state">No visible projects.</p>`;
      return;
    }
    target.innerHTML = `
      <div class="projects-focus-list-header">
        <span>${projects.length} visible project${projects.length === 1 ? "" : "s"}</span>
        ${showArchived ? `<span>Archived included</span>` : ""}
      </div>
      <div class="projects-focus-list-items">
        ${projects.map((project) => renderProjectFocusListItem(project)).join("")}
      </div>
    `;
  });

  document.querySelectorAll("[data-projects-focus-detail]").forEach((target) => {
    target.innerHTML = selectedProject
      ? renderProjectFocusDetail(selectedProject)
      : `<p class="empty-state">Select or create a project.</p>`;
    fillSelectsIn(target);
    decorateFieldTooltips(target);
  });
  syncProjectCreatePanel();
  applyLockedMutationControls();
  restoreFormDrafts(drafts);
}

function renderProjectFocusListItem(project) {
  const active = project.id === selectedProjectFocusId;
  const team = state.teams[project.team_id];
  return `
    <button
      type="button"
      class="projects-focus-row${active ? " active" : ""}"
      role="tab"
      data-project-focus-id="${escapeAttr(project.id)}"
      aria-selected="${active ? "true" : "false"}"
    >
      <span class="projects-focus-row-primary">
        <strong>${escapeHtml(project.name)}</strong>
        <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
      </span>
      ${team ? `<span class="projects-focus-row-team">${escapeHtml(team.name)}</span>` : ""}
    </button>
  `;
}

function renderProjectFocusDetail(project) {
  const team = state.teams[project.team_id];
  const leadAgent = state.agents[project.lead_agent_id];
  const tasks = values(state.tasks)
    .filter((task) => task.project_id === project.id)
    .sort(byPriority);
  const taskIds = new Set(tasks.map((task) => task.id));
  const deliverables = values(state.deliverables)
    .filter((deliverable) => taskIds.has(deliverable.task_id))
    .sort(compareProjectDeliverablesLatest);
  const releases = values(state.release_packages)
    .filter((release) => release.project_id === project.id)
    .sort(compareProjectReleasesLatest);
  const projectProfiles = projectSpecificReviewProfiles(project);
  const teamProfiles = teamWideReviewProfilesForProject(project);
  const outcomeKinds = outcomeKindDefinitionsForProject(project);
  const projectAgents = values(state.agents)
    .filter(isVisibleAgent)
    .filter((agent) => agentProjectIds(agent).includes(project.id))
    .sort(comparePeopleAgents);
  const projectRoles = values(state.roles)
    .filter(isVisibleRole)
    .filter((role) => role.project_id === project.id)
    .sort(compareTeamRoles);

  return `
    <div class="projects-detail-header">
      <div>
        <p class="section-label">Selected project</p>
        <h2>${escapeHtml(project.name)}</h2>
        <div class="meta-line">${escapeHtml(project.id)} - ${escapeHtml(team ? `${team.name} (${team.id})` : project.team_id)}</div>
      </div>
      <div class="record-actions">
        <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
        ${editButton("project", project.id)}
      </div>
    </div>
    <div class="project-focus-metrics">
      ${renderProjectFocusMetric("Lead", leadAgent ? agentLabel(leadAgent) : project.lead_agent_id)}
      ${renderProjectFocusMetric("Tasks", tasks.length)}
      ${renderProjectFocusMetric("Deliverables", deliverables.length)}
      ${renderProjectFocusMetric("Review profiles", projectProfiles.length)}
      ${renderProjectFocusMetric("Agents", projectAgents.length)}
      ${renderProjectFocusMetric("Releases", releases.length)}
    </div>
    ${renderProjectCostCounter(project.id)}
    ${renderProjectGit(project.git)}
    ${renderProjectReleasePolicy(project.release_policy)}
    <div class="projects-detail-grid">
      <section>
        <h3>Goals</h3>
        ${renderGoals(project.id, project.goals || [], { compact: true })}
      </section>
      <section>
        <h3>Project roles</h3>
        ${renderProjectFocusRoles(projectRoles)}
      </section>
      <section>
        <h3>Project agents</h3>
        ${renderProjectFocusAgents(projectAgents)}
      </section>
    </div>
    <div class="project-record-sections">
      ${renderProjectRecordSection(project, "deliverables", "Deliverables", deliverables, renderProjectDeliverablePage)}
      ${renderProjectRecordSection(project, "releases", "Releases", releases, renderProjectReleasePage)}
    </div>
    <section class="project-review-profile-panel">
      <div class="projects-detail-section-header">
        <div>
          <p class="section-label">Outcome catalog</p>
          <h3>Defined project outcomes</h3>
        </div>
      </div>
      ${renderProjectOutcomeKinds(outcomeKinds)}
      ${renderProjectOutcomeKindCreateForm(project)}
    </section>
    <section class="project-review-profile-panel">
      <div class="projects-detail-section-header">
        <div>
          <p class="section-label">Review profile</p>
          <h3>Project review profiles</h3>
        </div>
      </div>
      ${renderProjectReviewProfiles(projectProfiles, teamProfiles)}
      ${renderProjectReviewProfileCreatePanel(project)}
    </section>
  `;
}

function effectiveOutcomeKindsForProject(project) {
  const byKind = new Map();
  values(state.outcome_kind_definitions)
    .filter((definition) => definition.team_id === project.team_id && !definition.project_id)
    .sort(byId)
    .forEach((definition) => {
      if (!definition.archived) byKind.set(definition.kind, { ...definition, inherited: true });
    });
  values(state.outcome_kind_definitions)
    .filter((definition) => definition.project_id === project.id)
    .sort(byId)
    .forEach((definition) => {
      if (definition.archived) byKind.delete(definition.kind);
      else byKind.set(definition.kind, { ...definition, inherited: false });
    });
  return Array.from(byKind.values()).sort((left, right) =>
    text(left.kind).localeCompare(text(right.kind)) || byId(left, right),
  );
}

function outcomeKindDefinitionsForProject(project) {
  const effectiveIds = new Set(
    effectiveOutcomeKindsForProject(project).map((definition) => definition.id),
  );
  return values(state.outcome_kind_definitions)
    .filter((definition) =>
      definition.team_id === project.team_id
      && (!definition.project_id || definition.project_id === project.id)
    )
    .map((definition) => ({
      ...definition,
      inherited: !definition.project_id,
      effective: effectiveIds.has(definition.id),
    }))
    .sort((left, right) =>
      text(left.kind).localeCompare(text(right.kind))
      || Number(left.inherited) - Number(right.inherited)
      || byId(left, right),
    );
}

function renderProjectOutcomeKinds(definitions) {
  if (!definitions.length) {
    return `<p class="empty-state">No outcome kinds are defined for this project.</p>`;
  }
  return `
    <ul class="review-list compact-record-list">
      ${definitions.map((definition) => `
        <li class="record-item compact-record-card">
          <details ${teamDisclosureAttributes(teamDisclosureKey("projects", "outcome-kind", definition.id, "detail"))}>
            <summary class="compact-record-summary">
              <span class="compact-record-main">
                <strong>${escapeHtml(definition.name)}</strong>
                <span>
                  ${escapeHtml(definition.kind)}
                  - ${definition.inherited ? "team-wide" : "project"}
                  - ${definition.archived ? "archived" : definition.effective ? "effective" : "overridden"}
                </span>
              </span>
            </summary>
            <div class="compact-record-body">
              <div class="record-actions">${editButton("outcome-kind", definition.id)}</div>
              ${definition.description ? `<div class="meta-line">${escapeHtml(definition.description)}</div>` : ""}
              ${definition.reference_requirements ? `<div class="meta-line"><strong>References:</strong> ${escapeHtml(definition.reference_requirements)}</div>` : ""}
              ${definition.evidence_requirements ? `<div class="meta-line"><strong>Evidence:</strong> ${escapeHtml(definition.evidence_requirements)}</div>` : ""}
            </div>
          </details>
        </li>
      `).join("")}
    </ul>
  `;
}

function renderProjectOutcomeKindCreateForm(project) {
  return `
    <form
      class="project-review-profile-form"
      data-form-id="create-outcome-kind-form"
      data-form-draft-scope="${escapeAttr(project.id)}"
      data-endpoint="/outcome-kinds"
      data-method="POST"
      data-delegated-create-form
    >
      <input type="hidden" name="team_id" value="${escapeAttr(project.team_id)}" />
      <input type="hidden" name="project_id" value="${escapeAttr(project.id)}" />
      <div class="project-review-profile-fields">
        <label>Kind <input name="kind" required placeholder="durable_result_kind" /></label>
        <label>Name <input name="name" required /></label>
        <label class="checkbox-label"><input type="checkbox" name="team_wide" /><span>Team-wide definition</span></label>
        <label>Description <textarea name="description" rows="2" required></textarea></label>
        <label>Reference requirements <textarea name="reference_requirements" rows="2" required></textarea></label>
        <label>Evidence requirements <textarea name="evidence_requirements" rows="2"></textarea></label>
      </div>
      <button type="submit">Add outcome kind</button>
    </form>
  `;
}

function syncProjectCreatePanel({ reset = false } = {}) {
  const form = document.querySelector("[data-project-create-form]");
  const button = document.querySelector("[data-project-create-toggle]");
  if (form) {
    form.hidden = !projectCreateFormOpen;
    if (reset) {
      form.reset();
      restoreHumanReviewChecklistDraft(form, []);
    }
    if (projectCreateFormOpen) {
      fillSelectsIn(form);
      decorateFieldTooltips(form);
      syncHumanReviewChecklistEditor(
        form.querySelector("[data-human-review-checklist-editor]"),
      );
    }
  }
  if (button) {
    button.setAttribute("aria-expanded", projectCreateFormOpen ? "true" : "false");
  }
}

function renderProjectRecordSection(project, section, title, records, renderPageRecords) {
  const key = projectRecordSectionKey(project.id, section);
  const rejectedRecords = records.filter(isRejectedProjectRecord);
  const showRejected = projectRecordShowRejected.has(key);
  const visibleRecords = showRejected
    ? records
    : records.filter((record) => !isRejectedProjectRecord(record));
  const totalPages = Math.max(1, Math.ceil(visibleRecords.length / projectRecordPageSize));
  const page = clampPage(projectRecordSectionPages[key] || 1, totalPages);
  projectRecordSectionPages[key] = page;
  const start = (page - 1) * projectRecordPageSize;
  const pageRecords = visibleRecords.slice(start, start + projectRecordPageSize);
  const summaryParts = [
    `${visibleRecords.length} shown`,
    rejectedRecords.length && !showRejected ? `${rejectedRecords.length} rejected hidden` : "",
    visibleRecords.length > projectRecordPageSize ? `page ${page} of ${totalPages}` : "",
  ].filter(Boolean);
  return `
    <details
      class="project-record-section"
      data-project-record-section
      data-project-id="${escapeAttr(project.id)}"
      data-project-section="${escapeAttr(section)}"
      ${teamDisclosureAttributes(key)}
    >
      <summary class="project-record-section-summary">
        <span class="compact-record-main">
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(summaryParts.join(" - ") || "No records")}</span>
        </span>
      </summary>
      <div class="project-record-section-body">
        <div class="project-record-section-controls">
          ${
            rejectedRecords.length
              ? `<button
                  type="button"
                  class="record-action"
                  aria-pressed="${showRejected ? "true" : "false"}"
                  data-project-id="${escapeAttr(project.id)}"
                  data-project-section="${escapeAttr(section)}"
                  data-project-section-show-rejected
                >${showRejected ? "Hide rejected" : "Show rejected"}</button>`
              : ""
          }
        </div>
        ${renderPageRecords(pageRecords)}
        ${renderProjectRecordPager(project.id, section, page, totalPages, visibleRecords.length, start, pageRecords.length)}
      </div>
    </details>
  `;
}

function renderProjectDeliverablePage(deliverables) {
  return renderDeliverables(deliverables, { compact: true });
}

function renderProjectReleasePage(releases) {
  if (!releases.length) {
    return `<p class="empty-state">No release packages.</p>`;
  }
  return `<ul class="release-list compact-record-list">${releases.map(renderCompactRelease).join("")}</ul>`;
}

function renderProjectRecordPager(projectId, section, page, totalPages, totalRecords, start, pageLength) {
  if (totalRecords <= projectRecordPageSize) {
    return `<div class="team-record-pager-summary">Showing ${escapeHtml(totalRecords)} ${escapeHtml(section)}</div>`;
  }
  const first = start + 1;
  const last = start + pageLength;
  return `
    <div class="team-record-pager" aria-label="${escapeAttr(section)} pages">
      <button
        type="button"
        data-project-id="${escapeAttr(projectId)}"
        data-project-section="${escapeAttr(section)}"
        data-project-section-page="${page - 1}"
        ${page <= 1 ? "disabled" : ""}
      >Previous</button>
      <span>${escapeHtml(first)}-${escapeHtml(last)} of ${escapeHtml(totalRecords)}</span>
      <button
        type="button"
        data-project-id="${escapeAttr(projectId)}"
        data-project-section="${escapeAttr(section)}"
        data-project-section-page="${page + 1}"
        ${page >= totalPages ? "disabled" : ""}
      >Next</button>
    </div>
  `;
}

function projectRecordSectionKey(projectId, section) {
  return teamDisclosureKey("projects", "record-section", projectId, section);
}

function isRejectedProjectRecord(record) {
  return text(record?.state) === "rejected";
}

function compareProjectDeliverablesLatest(left, right) {
  return compareProjectRecordsLatest(left, right, ["created_at"]);
}

function compareProjectReleasesLatest(left, right) {
  return compareProjectRecordsLatest(left, right, ["published_at", "updated_at", "created_at"]);
}

function compareReviewRequestsLatest(left, right) {
  return compareProjectRecordsLatest(left, right, ["updated_at", "created_at"]);
}

function compareProjectRecordsLatest(left, right, fields) {
  const leftTime = projectRecordSortTime(left, fields);
  const rightTime = projectRecordSortTime(right, fields);
  if (leftTime !== rightTime) {
    return rightTime - leftTime;
  }
  for (const field of fields) {
    const leftText = text(left?.[field]);
    const rightText = text(right?.[field]);
    if (leftText !== rightText) {
      return rightText.localeCompare(leftText);
    }
  }
  return text(right?.id).localeCompare(text(left?.id));
}

function projectRecordSortTime(record, fields) {
  for (const field of fields) {
    const value = text(record?.[field]);
    if (!value) {
      continue;
    }
    const parsed = Date.parse(normalizeTimelineTimestampForSort(value));
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return Number.NEGATIVE_INFINITY;
}

function renderProjectFocusMetric(label, value) {
  return `
    <div class="project-focus-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function renderProjectFocusRoles(roles) {
  if (!roles.length) {
    return `<p class="empty-state">No project-specific roles.</p>`;
  }
  return `
    <ul class="compact-record-list">
      ${roles
        .map((role) => `
          <li class="record-item compact-record-card">
            <div class="record-line">
              <strong>${escapeHtml(role.name)}</strong>
              <div class="record-actions">${editButton("role", role.id)}</div>
            </div>
            <div class="meta-line">${escapeHtml(role.id)}</div>
            ${role.description ? `<div class="meta-line">${escapeHtml(role.description)}</div>` : ""}
          </li>
        `)
        .join("")}
    </ul>
  `;
}

function renderProjectFocusAgents(agents) {
  if (!agents.length) {
    return `<p class="empty-state">No project agents.</p>`;
  }
  return `
    <ul class="compact-record-list">
      ${agents
        .map((agent) => `
          <li class="record-item compact-record-card">
            <div class="record-line">
              <strong>${escapeHtml(agentLabel(agent))}</strong>
              <div class="record-actions">${editButton("agent", agent.id)}</div>
            </div>
            <div class="meta-line">${escapeHtml(agent.id)} - ${escapeHtml(agentRoleLabel(agent))}</div>
          </li>
        `)
        .join("")}
    </ul>
  `;
}

function renderCases() {
  const allCases = values(state.cases).sort(compareCases);
  const visibleCases = allCases.filter(isVisibleCase);
  if (!allCases.length) {
    renderIntoActiveTargets("[data-cases-view]", `<p class="empty-state">No cases registered.</p>`);
    return;
  }
  if (!visibleCases.length) {
    renderIntoActiveTargets("[data-cases-view]", `<p class="empty-state">No visible cases.</p>`);
    return;
  }
  const grouped = groupCasesByTeam(visibleCases);
  renderIntoActiveTargets(
    "[data-cases-view]",
    `
      ${renderCaseFilters(visibleCases)}
      <div class="case-team-groups">
        ${grouped.map(renderCaseTeamGroup).join("")}
      </div>
    `,
  );
}

function bindCaseFilters() {
  document.addEventListener("submit", (event) => {
    const form = event.target?.closest?.("[data-case-search-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    caseQuery = text(new FormData(form).get("query"));
    caseTeamPages.clear();
    renderCases();
  });
  document.addEventListener("change", (event) => {
    const statusFilter = event.target?.closest?.("[data-case-status-filter]");
    if (!statusFilter) {
      return;
    }
    caseStatusFilter = text(statusFilter.value) || "active";
    caseShowHistory =
      caseStatusFilter === "all" ||
      terminalCaseStatuses.has(caseStatusFilter);
    caseTeamPages.clear();
    renderCases();
  });
  document.addEventListener("click", (event) => {
    const historyButton = event.target?.closest?.("[data-case-history-toggle]");
    if (historyButton) {
      event.preventDefault();
      caseShowHistory = !caseShowHistory;
      caseStatusFilter = caseShowHistory ? "all" : "active";
      caseTeamPages.clear();
      renderCases();
      return;
    }
    const pageButton = event.target?.closest?.("[data-case-page-team]");
    if (pageButton) {
      event.preventDefault();
      const teamId = text(pageButton.dataset.casePageTeam);
      const page = Math.max(1, Number(pageButton.dataset.casePage || 1));
      caseTeamPages.set(teamId, page);
      renderCases();
      document
        .querySelector(`[data-case-team-id="${cssEscape(teamId)}"]`)
        ?.scrollIntoView({ block: "start", behavior: "auto" });
      return;
    }
    const clearButton = event.target?.closest?.("[data-case-filters-clear]");
    if (!clearButton) {
      return;
    }
    event.preventDefault();
    caseQuery = "";
    caseStatusFilter = caseShowHistory ? "all" : "active";
    caseTeamPages.clear();
    renderCases();
  });
}

function renderCaseFilters(cases) {
  const activeCount = cases.filter((item) => activeCaseStatuses.has(text(item.status) || "open")).length;
  const resolvedCount = cases.filter((item) => text(item.status) === "resolved").length;
  const closedCount = cases.filter((item) => text(item.status) === "closed").length;
  const historyCount = resolvedCount + closedCount;
  return `
    <section class="case-history-toolbar" aria-label="Case filters">
      <div class="case-history-summary">
        <strong>${activeCount} active</strong>
        <span>${resolvedCount} resolved</span>
        <span>${closedCount} closed</span>
      </div>
      <form class="case-history-search" data-case-search-form data-form-id="case-history-search-form">
        <label>
          <span>Search cases</span>
          <input name="query" type="search" value="${escapeAttr(caseQuery)}" placeholder="Title, description, source, tag, or ID" />
        </label>
        <button type="submit" class="record-action">Search</button>
      </form>
      <label class="case-history-status">
        <span>Status</span>
        <select data-case-status-filter>
          ${[
            ["active", "Active cases"],
            ["all", "All statuses"],
            ...caseStatuses.map((status) => [status, humanizeToken(status)]),
          ].map(([value, label]) => `<option value="${escapeAttr(value)}"${caseStatusFilter === value ? " selected" : ""}>${escapeHtml(label)}</option>`).join("")}
        </select>
      </label>
      <div class="record-actions">
        <button
          type="button"
          class="record-action"
          data-case-history-toggle
          aria-pressed="${caseShowHistory ? "true" : "false"}"
        >${caseShowHistory ? "Hide case history" : `Show case history (${historyCount})`}</button>
        ${caseQuery || !["active", "all"].includes(caseStatusFilter) ? `<button type="button" class="record-action secondary" data-case-filters-clear>Clear filters</button>` : ""}
      </div>
    </section>
  `;
}

function groupCasesByTeam(items) {
  const groups = new Map();
  items.forEach((item) => {
    const teamId = text(item.team_id) || "";
    if (!groups.has(teamId)) {
      groups.set(teamId, []);
    }
    groups.get(teamId).push(item);
  });
  return [...groups.entries()]
    .map(([teamId, cases]) => ({ teamId, team: state.teams[teamId] || null, cases }))
    .sort((left, right) => {
      const leftLabel = left.team?.name || left.teamId;
      const rightLabel = right.team?.name || right.teamId;
      return leftLabel.localeCompare(rightLabel) || left.teamId.localeCompare(right.teamId);
    });
}

function renderCaseTeamGroup(group) {
  const activeCount = group.cases.filter((item) => activeCaseStatuses.has(text(item.status) || "open")).length;
  const resolvedCount = group.cases.filter((item) => text(item.status) === "resolved").length;
  const closedCount = group.cases.filter((item) => text(item.status) === "closed").length;
  const matchingCases = group.cases.filter(caseMatchesCurrentFilters);
  const pageCount = Math.max(1, Math.ceil(matchingCases.length / casePageSize));
  const requestedPage = caseTeamPages.get(group.teamId) || 1;
  const page = Math.min(requestedPage, pageCount);
  caseTeamPages.set(group.teamId, page);
  const offset = (page - 1) * casePageSize;
  const pageCases = matchingCases.slice(offset, offset + casePageSize);
  const hiddenHistoryCount = resolvedCount + closedCount;
  return `
    <article class="project-block case-team-group" data-case-team-id="${escapeAttr(group.teamId)}">
      <div class="project-header">
        <div>
          <strong>${escapeHtml(group.team?.name || group.teamId || "Unknown team")}</strong>
          <div class="case-team-counts">
            <span><strong>${activeCount}</strong> active</span>
            <span>${resolvedCount} resolved</span>
            <span>${closedCount} closed</span>
          </div>
        </div>
      </div>
      ${
        pageCases.length
          ? `
            <ul class="task-list" data-case-page-list>
              ${pageCases.map(renderCaseItem).join("")}
            </ul>
            ${renderCasePagination(group.teamId, matchingCases.length, page, pageCount)}
          `
          : `<p class="empty-state">${caseShowHistory || caseQuery || caseStatusFilter !== "active" ? "No cases match these filters." : hiddenHistoryCount ? `${hiddenHistoryCount} resolved or closed case${hiddenHistoryCount === 1 ? " is" : "s are"} retained in case history.` : "No active cases."}</p>`
      }
    </article>
  `;
}

function caseMatchesCurrentFilters(item) {
  const status = text(item.status) || "open";
  if (!caseShowHistory && terminalCaseStatuses.has(status)) {
    return false;
  }
  if (caseStatusFilter === "active" && !activeCaseStatuses.has(status)) {
    return false;
  }
  if (caseStatusFilter !== "active" && caseStatusFilter !== "all" && status !== caseStatusFilter) {
    return false;
  }
  if (!caseQuery) {
    return true;
  }
  const query = caseQuery.toLowerCase();
  const project = item.project_id ? state.projects[item.project_id] : null;
  const owner = item.owner_agent_id ? state.agents[item.owner_agent_id] : null;
  const taskLabels = uniqueTextList(item.task_ids).flatMap((taskId) => {
    const task = state.tasks[taskId];
    return [taskId, task?.title];
  });
  return [
    item.id,
    item.title,
    item.description,
    item.source,
    item.source_ref,
    item.status,
    item.priority,
    project?.name,
    owner ? agentLabel(owner) : item.owner_agent_id,
    ...uniqueTextList(item.tags),
    ...taskLabels,
  ].some((value) => text(value).toLowerCase().includes(query));
}

function renderCasePagination(teamId, total, page, pageCount) {
  if (pageCount <= 1) {
    return "";
  }
  const first = (page - 1) * casePageSize + 1;
  const last = Math.min(total, page * casePageSize);
  return `
    <nav class="case-history-pagination" aria-label="Case pages">
      <span>Showing ${first}–${last} of ${total}</span>
      <div class="record-actions">
        <button type="button" class="record-action secondary" data-case-page-team="${escapeAttr(teamId)}" data-case-page="${page - 1}"${page <= 1 ? " disabled" : ""}>Previous</button>
        <span>Page ${page} of ${pageCount}</span>
        <button type="button" class="record-action secondary" data-case-page-team="${escapeAttr(teamId)}" data-case-page="${page + 1}"${page >= pageCount ? " disabled" : ""}>Next</button>
      </div>
    </nav>
  `;
}

function renderCaseItem(item) {
  const project = item.project_id ? state.projects[item.project_id] : null;
  const owner = item.owner_agent_id ? state.agents[item.owner_agent_id] : null;
  const metaParts = [
    item.id,
    item.priority ? `${humanizeToken(item.priority)} priority` : "",
    project ? project.name : item.project_id ? `project ${item.project_id}` : "Team-wide",
    owner ? `owner ${agentLabel(owner)}` : item.owner_agent_id ? `owner ${item.owner_agent_id}` : "No owner",
  ].filter(Boolean);
  const source = [item.source, item.source_ref].filter(Boolean).join(" ");
  return `
    <li class="task-item" data-case-id="${escapeAttr(item.id)}">
      <div class="task-line">
        <strong>${escapeHtml(item.title)}</strong>
        <div class="record-actions">
          <span class="state-pill ${escapeHtml(item.status || "open")}">${escapeHtml(humanizeToken(item.status || "open"))}</span>
          ${editButton("case", item.id)}
        </div>
      </div>
      <div class="meta-line">${escapeHtml(metaParts.join(" - "))}</div>
      ${source ? `<div class="meta-line">Source ${escapeHtml(source)}</div>` : ""}
      ${renderCaseTags(item)}
      ${renderCaseTaskLinks(item)}
      ${item.description ? `<div class="meta-line">${escapeHtml(item.description)}</div>` : ""}
    </li>
  `;
}

function renderCaseTags(item) {
  const tags = uniqueTextList(item.tags);
  if (!tags.length) {
    return "";
  }
  return `
    <ul class="inline-list">
      ${tags.map((tag) => `<li><span class="state-pill neutral">${escapeHtml(tag)}</span></li>`).join("")}
    </ul>
  `;
}

function renderCaseTaskLinks(item) {
  const taskIds = uniqueTextList(item.task_ids);
  if (!taskIds.length) {
    return "";
  }
  const labels = taskIds.map((taskId) => {
    const task = state.tasks[taskId];
    return task ? `${task.title} (${task.id}, ${taskStateLabel(task)})` : taskId;
  });
  return `<div class="meta-line">Tasks ${escapeHtml(labels.join(" - "))}</div>`;
}

function caseLabel(item) {
  const team = item?.team_id ? state.teams[item.team_id] : null;
  const project = item?.project_id ? state.projects[item.project_id] : null;
  const parts = [
    item?.title || item?.id,
    item?.id,
    item?.status ? humanizeToken(item.status) : "",
    team?.name || item?.team_id,
    project?.name || item?.project_id,
  ].filter(Boolean);
  return parts.join(" - ");
}

function compareCases(left, right) {
  return (
    caseStatusRank(left.status) - caseStatusRank(right.status) ||
    prioritySortValue(left.priority) - prioritySortValue(right.priority) ||
    String(right.updated_at || "").localeCompare(String(left.updated_at || "")) ||
    byId(left, right)
  );
}

function caseStatusRank(status) {
  const order = {
    triage: 0,
    open: 1,
    in_progress: 2,
    waiting: 3,
    resolved: 4,
    closed: 5,
  };
  return order[text(status)] ?? 99;
}

function renderProjectReviewProfileCreatePanel(project) {
  const open = text(projectReviewProfileCreateProjectId) === text(project.id);
  if (!open) {
    return `
      <div class="project-review-profile-create-actions">
        <button
          type="button"
          class="record-action"
          aria-expanded="false"
          data-project-id="${escapeAttr(project.id)}"
          data-project-review-profile-create-toggle
        >
          Add review profile
        </button>
      </div>
    `;
  }
  return renderProjectReviewProfileCreateForm(project);
}

function renderProjectReviewProfileCreateForm(project) {
  return `
    <form
      id="create-review-profile-for-project-form"
      class="project-review-profile-form"
      data-form-id="create-review-profile-form"
      data-form-draft-scope="${escapeAttr(project.id)}"
      data-endpoint="/review-profiles"
      data-method="POST"
      data-delegated-create-form
      data-project-review-profile-create-form
    >
      <div class="project-review-profile-form-header">
        <div>
          <p class="section-label">Review profile</p>
          <h3>Add review profile</h3>
        </div>
        <button type="button" class="record-action" data-project-review-profile-create-close>Close</button>
      </div>
      <input type="hidden" name="team_id" value="${escapeAttr(project.team_id)}" />
      <input type="hidden" name="project_id" value="${escapeAttr(project.id)}" />
      <div class="project-review-profile-fields">
        <label>Name <input name="name" required /></label>
        <label>Description <textarea name="description" rows="2"></textarea></label>
        <label>Applicable roles <select name="applicable_role_ids" multiple size="4" data-options="project-review-roles"></select></label>
        <label>Required outcome kinds <select name="required_outcome_kinds" multiple size="4" data-options="project-outcome-kinds"></select></label>
      </div>
      ${renderReviewProfileCriteriaEditor()}
      <button type="submit">Create profile</button>
    </form>
  `;
}

function renderProjectReviewProfiles(projectProfiles, teamProfiles) {
  const projectList = projectProfiles.length
    ? `<ul class="review-list compact-record-list">${projectProfiles.map(renderCompactReviewProfile).join("")}</ul>`
    : `<p class="empty-state">No project-specific review profiles.</p>`;
  const teamList = teamProfiles.length
    ? `
      <details class="team-wide-review-profiles" ${teamDisclosureAttributes(teamDisclosureKey("projects", "team-review-profiles", project.id))}>
        <summary>Team-wide profiles available to this project</summary>
        <ul class="review-list compact-record-list">${teamProfiles.map(renderCompactReviewProfile).join("")}</ul>
      </details>
    `
    : "";
  return `
    <div class="project-review-profile-list">
      ${projectList}
      ${teamList}
    </div>
  `;
}

function projectSpecificReviewProfiles(project) {
  return values(state.review_profiles)
    .filter(isVisibleReviewProfile)
    .filter((profile) => profile.project_id === project.id)
    .sort(compareReviewProfiles);
}

function teamWideReviewProfilesForProject(project) {
  return values(state.review_profiles)
    .filter(isVisibleReviewProfile)
    .filter((profile) => !profile.project_id && profile.team_id === project.team_id)
    .sort(compareReviewProfiles);
}

function renderTaskBoard() {
  const visibleTeams = values(state.teams).filter(isVisibleTeam).sort(comparePeopleTeams);
  const selectedTeam = visibleTeams.find((team) => text(team.id) === text(selectedTaskBoardTeamId)) || null;
  if (selectedTaskBoardTeamId && !selectedTeam) {
    selectedTaskBoardTeamId = "";
    resetTaskBoardProjectState();
  }
  const visibleProjectsForTeam = selectedTaskBoardTeamId
    ? values(state.projects)
        .filter((project) => isVisibleProject(project) && text(project.team_id) === text(selectedTaskBoardTeamId))
        .sort(compareTeamProjects)
    : [];
  const selectedProject =
    visibleProjectsForTeam.find((project) => text(project.id) === text(selectedTaskBoardProjectId)) || null;
  if (selectedTaskBoardProjectId && !selectedProject) {
    selectedTaskBoardProjectId = "";
    resetTaskBoardProjectState();
  }
  syncTaskBoardSelectors(visibleTeams, visibleProjectsForTeam);

  if (!visibleTeams.length) {
    renderIntoActiveTargets("[data-task-board-view]", `<p class="empty-state">No visible teams.</p>`);
    return;
  }
  if (!selectedTaskBoardTeamId) {
    renderIntoActiveTargets(
      "[data-task-board-view]",
      `<p class="empty-state">Select a team and project to view its task board.</p>`,
    );
    return;
  }
  if (!visibleProjectsForTeam.length) {
    renderIntoActiveTargets(
      "[data-task-board-view]",
      `<p class="empty-state">No visible projects for the selected team.</p>`,
    );
    return;
  }
  if (!selectedTaskBoardProjectId) {
    renderIntoActiveTargets(
      "[data-task-board-view]",
      `<p class="empty-state">Select a project to view its task board.</p>`,
    );
    return;
  }

  const projectTasks = values(state.tasks)
    .filter((task) => text(task.project_id) === text(selectedTaskBoardProjectId))
    .filter((task) => taskBoardLaneForTask(task) !== null);
  const hierarchy = buildTaskBoardHierarchyIndex(projectTasks);
  if (taskBoardRootTaskId && !hierarchy.roots.some((task) => text(task.id) === taskBoardRootTaskId)) {
    taskBoardRootTaskId = "";
  }
  if (selectedTaskBoardTaskId && !hierarchy.byId.has(selectedTaskBoardTaskId)) {
    selectedTaskBoardTaskId = "";
  }
  const matchingTasks = projectTasks
    .filter((task) => taskMatchesTaskBoardFilters(task, hierarchy))
    .sort(compareTaskBoardTasks);
  const focus = taskBoardFocusContext(hierarchy);
  const activeCount = projectTasks.filter((task) => !terminalTaskStates.has(text(task.state))).length;
  const terminalCount = projectTasks.length - activeCount;
  const blockedCount = projectTasks.filter(isTaskBoardBlocked).length;
  const dependencyWaitCount = projectTasks.filter(taskBoardWaitsOnDependencies).length;
  const boardMeta = [
    `${projectTasks.length} task${projectTasks.length === 1 ? "" : "s"}`,
    `${activeCount} active`,
    `${terminalCount} completed`,
    `${blockedCount} blocked`,
    `${dependencyWaitCount} waiting on dependencies`,
  ];
  renderIntoActiveTargets(
    "[data-task-board-view]",
    `
      <div class="task-board-context">
        <strong>${escapeHtml(selectedProject.name || selectedProject.id)}</strong>
        <span>${escapeHtml(selectedTeam.name || selectedTeam.id)} - ${escapeHtml(boardMeta.join(" - "))}</span>
      </div>
      ${renderTaskBoardCreateForm(selectedProject, projectTasks)}
      ${renderTaskBoardControls(projectTasks, hierarchy, terminalCount)}
      ${renderTaskBoardFocusSummary(focus)}
      ${
        taskBoardViewMode === "hierarchy"
          ? renderTaskBoardHierarchy(projectTasks, matchingTasks, hierarchy, focus)
          : renderTaskBoardLanes(matchingTasks, focus)
      }
    `,
  );
  document.querySelectorAll("[data-task-board-view]").forEach((target) => {
    if (!targetIsActive(target)) return;
    decorateFieldTooltips(target);
  });
  applyLockedMutationControls();
}

function renderTaskBoardCreateForm(project, projectTasks) {
  const ownerOptions = values(state.agents)
    .filter(isVisibleAgent)
    .filter((agent) => agentProjectIds(agent).includes(project.id))
    .sort(comparePeopleAgents)
    .map((agent) => [agent.id, agentLabel(agent)]);
  const caseOptions = values(state.cases)
    .filter((teamCase) => (
      text(teamCase.project_id) === text(project.id)
      || (!text(teamCase.project_id) && text(teamCase.team_id) === text(project.team_id))
    ))
    .sort(byId)
    .map((teamCase) => [teamCase.id, text(teamCase.title) || teamCase.id]);
  const taskOptions = projectTasks
    .slice()
    .sort(compareTaskBoardTasks)
    .map((task) => [task.id, taskBoardTaskReferenceLabel(task.id)]);
  return `
    <details
      class="task-board-create"
      ${teamDisclosureAttributes(teamDisclosureKey("task-board", project.id, "create-task"))}
    >
      <summary>Create task</summary>
      <form
        class="team-inline-create-form task-board-create-form"
        data-task-board-create-form
        data-form-id="create-task-form"
        data-form-draft-scope="${escapeAttr(project.id)}"
        data-endpoint="/tasks"
        data-method="POST"
      >
        <input type="hidden" name="project_id" value="${escapeAttr(project.id)}" />
        <div class="team-inline-create-fields task-board-create-fields">
          <label>Owner
            <select name="owner_agent_id" required>${renderSelectOptions(ownerOptions, { placeholder: "Select project agent" })}</select>
          </label>
          <label>Case
            <select name="case_id">${renderSelectOptions(caseOptions, { placeholder: "No case" })}</select>
          </label>
          <label>Priority <input name="priority" type="number" min="0" value="0" required /></label>
          <label>Title <input name="title" required /></label>
          <label>Description <textarea name="description" rows="3" required></textarea></label>
          <label>Commit author name <input name="task_author_name" placeholder="Inherit task or project author" /></label>
          <label>Commit author email <input name="task_author_email" type="email" placeholder="Inherit task or project author" /></label>
          <label>Git base branch
            <input name="git_base_branch" placeholder="Inherit parent or ${escapeAttr(project.git?.base_branch || "project default")}" />
          </label>
          <label>Parent task
            <select name="parent_task_id">${renderSelectOptions(taskOptions, { placeholder: "No parent" })}</select>
          </label>
          <label>Dependency task IDs
            <textarea name="dependency_task_ids" rows="2" placeholder="One per line or comma-separated"></textarea>
          </label>
          <label class="checkbox-label"><input name="integration_boundary" type="checkbox" /> Integration boundary</label>
          <label class="checkbox-label"><input name="require_git_provenance" type="checkbox" /> Require Git provenance</label>
          <label>Required contribution task IDs
            <textarea name="required_contribution_task_ids" rows="2" placeholder="One per line or comma-separated"></textarea>
          </label>
        </div>
        <button type="submit" ${lockedAttrs("create tasks")}>Create task</button>
      </form>
    </details>
  `;
}

function taskAssignmentKey(task) {
  return text(task.owner_agent_id) || (task.assignment_mode === "role" ? `role:${task.assignment_role_id}` : "");
}

function taskAssignmentLabel(task) {
  const owner = state.agents[text(task.owner_agent_id)];
  if (owner) return agentLabel(owner);
  if (task.assignment_mode === "role" && !task.owner_agent_id) {
    const roleName = task.assignment_role_name || state.roles?.[task.assignment_role_id]?.name || task.assignment_role_id;
    return `Waiting for ${roleName}`;
  }
  return text(task.owner_agent_id) || "Unavailable assignee";
}

function renderRoleTaskQueueStatus(task) {
  if (task.owner_agent_id || !task.assignment_wait_reason || task.state !== "pending") return "";
  const reasons = {
    waiting_for_dependencies: "Waiting for dependencies",
    waiting_for_execution_gates: "Waiting for execution prerequisites",
    waiting_for_assignment_authority: "Waiting for an authorized assignment",
    waiting_for_eligible_role_holder: "No eligible role holder",
    waiting_for_execution_capacity: "Waiting for an available agent",
  };
  const reason = reasons[task.assignment_wait_reason] || humanizeToken(task.assignment_wait_reason);
  return `<span class="state-pill neutral">${escapeHtml(reason)}</span>`;
}

function renderTaskBoardControls(projectTasks, hierarchy, terminalCount) {
  const assignees = [...new Map(projectTasks
    .filter((task) => taskAssignmentKey(task))
    .map((task) => [taskAssignmentKey(task), taskAssignmentLabel(task)])).entries()]
    .sort((left, right) => left[1].localeCompare(right[1]) || left[0].localeCompare(right[0]));
  const rootOptions = hierarchy.roots.map((task) => [
    task.id,
    `${taskBoardTaskReferenceLabel(task.id)} - ${taskStateLabel(task)}`,
  ]);
  const completionFiltersEnabled = taskBoardViewMode === "hierarchy" || taskBoardShowCompleted;
  return `
    <div class="task-board-toolbar">
      <div class="task-board-mode-switch" role="tablist" aria-label="Task board view">
        ${["board", "hierarchy"].map((mode) => `
          <button
            type="button"
            class="runtime-filter-button${taskBoardViewMode === mode ? " active" : ""}"
            role="tab"
            aria-selected="${taskBoardViewMode === mode ? "true" : "false"}"
            data-task-board-mode="${mode}"
          >${mode === "board" ? "Board" : "Hierarchy"}</button>
        `).join("")}
      </div>
      ${
        taskBoardViewMode === "board"
          ? `
            <label class="task-board-completed-toggle">
              <input type="checkbox" data-task-board-show-completed${taskBoardShowCompleted ? " checked" : ""} />
              Show completed (${terminalCount})
            </label>
          `
          : `<span class="task-board-history-note">${terminalCount} completed task${terminalCount === 1 ? "" : "s"} included in hierarchy</span>`
      }
    </div>
    <form class="task-board-filters" data-task-board-search-form data-form-id="task-board-search-form" data-form-draft-scope="${escapeAttr(`${selectedTaskBoardTeamId}:${selectedTaskBoardProjectId}`)}" role="search">
      <label class="task-board-search-filter">
        Search
        <span class="task-board-search-controls">
          <input
            type="search"
            name="task_board_query"
            value="${escapeAttr(taskBoardQuery)}"
            placeholder="Title, description, assignee, task ID…"
          />
          <button type="submit">Search</button>
        </span>
      </label>
      <label>
        Assignee
        <select data-task-board-filter="assignee">
          ${renderSelectOptions(assignees, {
            selectedValue: taskBoardAssigneeId,
            placeholder: "Any assignee",
          })}
        </select>
      </label>
      <label>
        State
        <select data-task-board-filter="state">
          ${renderSelectOptions(
            taskStates.map((taskState) => [taskState, humanizeToken(taskState)]),
            { selectedValue: taskBoardStateFilter, placeholder: "Any state" },
          )}
        </select>
      </label>
      <label>
        Readiness
        <select data-task-board-filter="readiness">
          ${renderSelectOptions(
            taskBoardReadinessValues.map((readiness) => [readiness, humanizeToken(readiness)]),
            { selectedValue: taskBoardReadinessFilter, placeholder: "Any readiness" },
          )}
        </select>
      </label>
      <label>
        Hierarchy root
        <select data-task-board-filter="root">
          ${renderSelectOptions(rootOptions, {
            selectedValue: taskBoardRootTaskId,
            placeholder: "Any hierarchy",
          })}
        </select>
      </label>
      <label>
        Completed from
        <input
          type="date"
          value="${escapeAttr(taskBoardCompletedSince)}"
          data-task-board-filter="completed-since"
          ${completionFiltersEnabled ? "" : "disabled"}
        />
      </label>
      <label>
        Completed through
        <input
          type="date"
          value="${escapeAttr(taskBoardCompletedThrough)}"
          data-task-board-filter="completed-through"
          ${completionFiltersEnabled ? "" : "disabled"}
        />
      </label>
      <button type="button" class="secondary task-board-clear-filters" data-task-board-clear-filters>
        Clear filters
      </button>
    </form>
  `;
}

function taskMatchesTaskBoardFilters(task, hierarchy) {
  if (taskBoardAssigneeId && taskAssignmentKey(task) !== taskBoardAssigneeId) {
    return false;
  }
  if (taskBoardStateFilter && text(task.state) !== taskBoardStateFilter) {
    return false;
  }
  if (taskBoardReadinessFilter && taskBoardReadiness(task) !== taskBoardReadinessFilter) {
    return false;
  }
  if (taskBoardRootTaskId && hierarchy.rootByTaskId.get(text(task.id)) !== taskBoardRootTaskId) {
    return false;
  }
  if (taskBoardCompletedSince || taskBoardCompletedThrough) {
    if (!terminalTaskStates.has(text(task.state))) {
      return false;
    }
    const completedAt = taskBoardCompletionTime(task);
    const from = dateInputLocalBoundary(taskBoardCompletedSince, false);
    const through = dateInputLocalBoundary(taskBoardCompletedThrough, true);
    if (!Number.isFinite(completedAt)) {
      return false;
    }
    if (Number.isFinite(from) && completedAt < from) {
      return false;
    }
    if (Number.isFinite(through) && completedAt > through) {
      return false;
    }
  }
  if (!taskBoardQuery) {
    return true;
  }
  const parent = hierarchy.byId.get(hierarchy.parentById.get(text(task.id))) || null;
  const root = hierarchy.byId.get(hierarchy.rootByTaskId.get(text(task.id))) || null;
  const linkedCase = state.cases[text(task.case_id)] || null;
  const relatedTaskTitles = [
    ...(task.blocked_by_task_ids || []),
    ...(task.dependency_task_ids || []),
  ].map(taskBoardTaskReferenceLabel);
  const haystack = [
    task.id,
    task.title,
    task.description,
    task.owner_agent_id,
    task.assignment_role_id,
    taskAssignmentLabel(task),
    parent?.title,
    root?.title,
    linkedCase?.title,
    ...relatedTaskTitles,
  ]
    .map(text)
    .join("\n")
    .toLocaleLowerCase();
  return haystack.includes(taskBoardQuery.toLocaleLowerCase());
}

function taskBoardReadiness(task) {
  return text(task?.readiness || task?.assessment?.readiness) || "unknown";
}

function taskBoardCompletionTime(task) {
  const timestamp =
    (text(task?.state) === "cancelled" ? task?.cancellation?.cancelled_at : null) ||
    task?.state_entered_at ||
    task?.updated_at ||
    task?.created_at;
  if (!timestamp) {
    return Number.NaN;
  }
  const parsed = Date.parse(normalizeTimelineTimestampForSort(timestamp));
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

function dateInputLocalBoundary(value, endOfDay) {
  const parts = text(value).split("-").map(Number);
  if (
    parts.length !== 3 ||
    !Number.isInteger(parts[0]) ||
    !Number.isInteger(parts[1]) ||
    !Number.isInteger(parts[2])
  ) {
    return Number.NaN;
  }
  return new Date(
    parts[0],
    parts[1] - 1,
    parts[2],
    endOfDay ? 23 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 999 : 0,
  ).getTime();
}

function taskBoardHasFilters() {
  return Boolean(
    taskBoardQuery ||
      taskBoardAssigneeId ||
      taskBoardStateFilter ||
      taskBoardReadinessFilter ||
      taskBoardRootTaskId ||
      taskBoardCompletedSince ||
      taskBoardCompletedThrough
  );
}

function renderTaskBoardFocusSummary(focus) {
  if (!focus.task) {
    return "";
  }
  const parts = [
    `${focus.ancestors.size} ancestor${focus.ancestors.size === 1 ? "" : "s"}`,
    `${focus.directChildren.size} direct child${focus.directChildren.size === 1 ? "" : "ren"}`,
    `${focus.descendants.size} descendant${focus.descendants.size === 1 ? "" : "s"}`,
  ];
  return `
    <div class="task-board-focus-summary" role="status">
      <span><strong>Focused:</strong> ${escapeHtml(taskBoardTaskReferenceLabel(focus.task.id))} - ${escapeHtml(parts.join(" - "))}</span>
      <button type="button" class="secondary" data-task-board-clear-focus>Clear focus</button>
    </div>
  `;
}

function buildTaskBoardHierarchyIndex(tasks) {
  const orderedTasks = [...tasks].sort(compareTaskBoardTasks);
  const byId = new Map(orderedTasks.map((task) => [text(task.id), task]));
  const parentById = new Map();
  const warnings = new Map();
  const addWarning = (taskId, warning) => {
    const taskWarnings = warnings.get(taskId) || [];
    if (!taskWarnings.includes(warning)) {
      taskWarnings.push(warning);
      warnings.set(taskId, taskWarnings);
    }
  };
  orderedTasks.forEach((task) => {
    const taskId = text(task.id);
    const parentId = text(task.parent_task_id);
    if (!parentId) {
      parentById.set(taskId, "");
      return;
    }
    if (parentId === taskId) {
      parentById.set(taskId, "");
      addWarning(taskId, "Task refers to itself as its parent.");
      return;
    }
    if (!byId.has(parentId)) {
      parentById.set(taskId, "");
      addWarning(taskId, "Parent task is unavailable in this project.");
      return;
    }
    parentById.set(taskId, parentId);
  });

  const resolved = new Set();
  orderedTasks.forEach((task) => {
    const startId = text(task.id);
    if (resolved.has(startId)) {
      return;
    }
    const path = [];
    const positionById = new Map();
    let currentId = startId;
    while (currentId) {
      if (positionById.has(currentId)) {
        const cycleIds = path.slice(positionById.get(currentId));
        cycleIds.forEach((taskId) => addWarning(taskId, "Task hierarchy contains a cycle."));
        const breakId = [...cycleIds].sort()[0];
        parentById.set(breakId, "");
        break;
      }
      if (resolved.has(currentId)) {
        break;
      }
      positionById.set(currentId, path.length);
      path.push(currentId);
      currentId = parentById.get(currentId) || "";
    }
    path.forEach((taskId) => resolved.add(taskId));
  });

  const childrenById = new Map(orderedTasks.map((task) => [text(task.id), []]));
  orderedTasks.forEach((task) => {
    const taskId = text(task.id);
    const parentId = parentById.get(taskId);
    if (parentId) {
      childrenById.get(parentId).push(task);
    }
  });
  childrenById.forEach((children) => children.sort(compareTaskBoardTasks));
  const roots = orderedTasks.filter((task) => !parentById.get(text(task.id)));
  const rootByTaskId = new Map();
  const assignRoot = (taskId, rootId) => {
    if (rootByTaskId.has(taskId)) {
      return;
    }
    rootByTaskId.set(taskId, rootId);
    (childrenById.get(taskId) || []).forEach((child) => assignRoot(text(child.id), rootId));
  };
  roots.forEach((task) => assignRoot(text(task.id), text(task.id)));
  return { byId, parentById, childrenById, roots, rootByTaskId, warnings };
}

function taskBoardFocusContext(hierarchy) {
  const task = hierarchy.byId.get(selectedTaskBoardTaskId) || null;
  const ancestors = new Set();
  const directChildren = new Set();
  const descendants = new Set();
  if (!task) {
    return { task: null, ancestors, directChildren, descendants };
  }
  let parentId = hierarchy.parentById.get(text(task.id)) || "";
  while (parentId && !ancestors.has(parentId)) {
    ancestors.add(parentId);
    parentId = hierarchy.parentById.get(parentId) || "";
  }
  (hierarchy.childrenById.get(text(task.id)) || []).forEach((child) => {
    directChildren.add(text(child.id));
  });
  const visitDescendants = (taskId) => {
    (hierarchy.childrenById.get(taskId) || []).forEach((child) => {
      const childId = text(child.id);
      if (descendants.has(childId)) {
        return;
      }
      descendants.add(childId);
      visitDescendants(childId);
    });
  };
  visitDescendants(text(task.id));
  return { task, ancestors, directChildren, descendants };
}

function taskBoardRelationClasses(taskId, focus) {
  const normalizedTaskId = text(taskId);
  const classes = [];
  if (text(focus.task?.id) === normalizedTaskId) {
    classes.push("task-board-task-selected");
  } else if (focus.ancestors.has(normalizedTaskId)) {
    classes.push("task-board-task-ancestor");
  } else if (focus.directChildren.has(normalizedTaskId)) {
    classes.push("task-board-task-direct-child");
  } else if (focus.descendants.has(normalizedTaskId)) {
    classes.push("task-board-task-descendant");
  }
  return classes.join(" ");
}

function taskBoardRelationLabel(taskId, focus) {
  const normalizedTaskId = text(taskId);
  if (text(focus.task?.id) === normalizedTaskId) {
    return "Focused";
  }
  if (focus.ancestors.has(normalizedTaskId)) {
    return "Ancestor";
  }
  if (focus.directChildren.has(normalizedTaskId)) {
    return "Direct child";
  }
  if (focus.descendants.has(normalizedTaskId)) {
    return "Descendant";
  }
  return "";
}

function renderTaskBoardLanes(matchingTasks, focus) {
  const lanes = taskBoardLanes.map((lane) => {
    const matchingLaneTasks = matchingTasks
      .filter((task) => taskBoardLaneForTask(task) === lane.id)
      .sort(compareTaskBoardTasks);
    const hiddenCompleted = lane.id === "done" && !taskBoardShowCompleted;
    return {
      ...lane,
      matchingTasks: matchingLaneTasks,
      tasks: hiddenCompleted ? [] : matchingLaneTasks,
      hiddenCompletedCount: hiddenCompleted ? matchingLaneTasks.length : 0,
    };
  });
  return `
    <div class="task-board-lanes" aria-label="Task board lanes">
      ${lanes.map((lane) => renderTaskBoardLane(lane, focus)).join("")}
    </div>
  `;
}

function taskBoardHierarchyDisplayContext(projectTasks, matchingTasks, hierarchy, focus) {
  const displayIds = taskBoardHasFilters()
    ? new Set(matchingTasks.map((task) => text(task.id)))
    : new Set(projectTasks.map((task) => text(task.id)));
  const forceExpandedIds = new Set();
  const includeAncestors = (taskId, forceExpanded) => {
    let parentId = hierarchy.parentById.get(taskId) || "";
    while (parentId) {
      displayIds.add(parentId);
      if (forceExpanded) {
        forceExpandedIds.add(parentId);
      }
      parentId = hierarchy.parentById.get(parentId) || "";
    }
  };
  if (taskBoardHasFilters()) {
    matchingTasks.forEach((task) => includeAncestors(text(task.id), true));
  }
  if (focus.task) {
    const focusedTaskId = text(focus.task.id);
    displayIds.add(focusedTaskId);
    includeAncestors(focusedTaskId, true);
    focus.directChildren.forEach((taskId) => displayIds.add(taskId));
  }
  return { displayIds, forceExpandedIds };
}

function renderTaskBoardHierarchy(projectTasks, matchingTasks, hierarchy, focus) {
  if (!taskBoardHierarchyInitialized) {
    hierarchy.roots
      .slice(0, taskBoardHierarchyRootLimit)
      .forEach((task) => expandedTaskBoardTaskIds.add(text(task.id)));
    taskBoardHierarchyInitialized = true;
  }
  const display = taskBoardHierarchyDisplayContext(projectTasks, matchingTasks, hierarchy, focus);
  const roots = hierarchy.roots.filter((task) => display.displayIds.has(text(task.id)));
  if (!roots.length) {
    return `<p class="empty-state">No tasks match the current hierarchy filters.</p>`;
  }
  const visibleRoots = roots.slice(0, taskBoardHierarchyRootLimit);
  return `
    <section class="task-board-hierarchy" aria-label="Task hierarchy">
      <div class="task-board-hierarchy-summary">
        <span>${matchingTasks.length} matching task${matchingTasks.length === 1 ? "" : "s"} across ${roots.length} hierarchy root${roots.length === 1 ? "" : "s"}.</span>
        <span>Completed history is included.</span>
      </div>
      <ol class="task-board-hierarchy-tree">
        ${visibleRoots
          .map((task) =>
            renderTaskBoardHierarchyNode(task, hierarchy, focus, display, new Set()),
          )
          .join("")}
      </ol>
      ${
        visibleRoots.length < roots.length
          ? `<button type="button" class="secondary task-board-more-hierarchy" data-task-board-more-roots>Show ${Math.min(taskBoardHierarchyPageSize, roots.length - visibleRoots.length)} more roots</button>`
          : ""
      }
    </section>
  `;
}

function renderTaskBoardHierarchyNode(task, hierarchy, focus, display, path) {
  const taskId = text(task.id);
  if (path.has(taskId)) {
    return "";
  }
  const nextPath = new Set(path);
  nextPath.add(taskId);
  const childTasks = (hierarchy.childrenById.get(taskId) || []).filter((child) =>
    display.displayIds.has(text(child.id)),
  );
  const expanded =
    childTasks.length > 0 &&
    (expandedTaskBoardTaskIds.has(taskId) || display.forceExpandedIds.has(taskId));
  const childLimit = taskBoardHierarchyChildLimits.get(taskId) || taskBoardHierarchyPageSize;
  const visibleChildren = expanded ? childTasks.slice(0, childLimit) : [];
  const assignedTo = taskAssignmentLabel(task);
  const readiness = taskBoardReadiness(task);
  const assessment = task.assessment || {};
  const resolution = text(task.resolution_status || assessment.resolution_status) || "unknown";
  const integrationReady = Boolean(task.integration_ready ?? assessment.integration_ready);
  const blockerCount = taskBoardBlockerCount(task);
  const upstreamBlockerCount = inheritedTaskBlockers(task).length;
  const relation = taskBoardRelationLabel(taskId, focus);
  const relationClasses = taskBoardRelationClasses(taskId, focus);
  const warnings = hierarchy.warnings.get(taskId) || [];
  const worktreeContexts = taskGitWorkContexts(task);
  const unresolvedIds = assessment.unresolved_descendant_ids || [];
  return `
    <li class="task-board-hierarchy-branch ${escapeAttr(relationClasses)}" data-task-board-task-id="${escapeAttr(taskId)}">
      <article class="task-board-hierarchy-node ${escapeAttr(relationClasses)}">
        <div class="task-board-hierarchy-node-header">
          ${
            childTasks.length
              ? `
                <button
                  type="button"
                  class="task-board-hierarchy-toggle"
                  aria-expanded="${expanded ? "true" : "false"}"
                  aria-label="${expanded ? "Collapse" : "Expand"} children of ${escapeAttr(taskBoardTaskReferenceLabel(taskId))}"
                  data-task-board-toggle-children-id="${escapeAttr(taskId)}"
                >${expanded ? "−" : "+"}</button>
              `
              : `<span class="task-board-hierarchy-toggle-spacer" aria-hidden="true"></span>`
          }
          <strong>
            <button
              type="button"
              class="task-board-task-link task-board-task-title"
              data-task-board-select-task-id="${escapeAttr(taskId)}"
              data-task-board-primary-task-id="${escapeAttr(taskId)}"
              ${text(focus.task?.id) === taskId ? 'aria-current="true"' : ""}
            >${escapeHtml(taskBoardTaskReferenceLabel(taskId))}</button>
          </strong>
          <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(taskStateLabel(task))}</span>
        </div>
        <div class="task-board-hierarchy-badges">
          <span class="state-pill ${escapeAttr(taskReadinessPill(readiness))}">Readiness: ${escapeHtml(humanizeToken(readiness))}</span>
          <span class="state-pill neutral">Assignee: ${escapeHtml(assignedTo)}</span>
          <span class="state-pill ${escapeAttr(taskResolutionPill(resolution))}">Resolution: ${escapeHtml(humanizeToken(resolution))}</span>
          <span class="state-pill ${integrationReady ? "accepted" : "neutral"}">Integration: ${integrationReady ? "ready" : "not ready"}</span>
          ${renderTaskBoardDependencyWaitBadge(task)}
        ${renderRoleTaskQueueStatus(task)}
          ${
            blockerCount
              ? `<span class="state-pill blocked">${blockerCount} assigned blocker${blockerCount === 1 ? "" : "s"}</span>`
              : ""
          }
          ${
            upstreamBlockerCount
              ? `<span class="state-pill warning">${upstreamBlockerCount} upstream blocker${upstreamBlockerCount === 1 ? "" : "s"}</span>`
              : ""
          }
          ${relation ? `<span class="task-board-relation-badge">${escapeHtml(relation)}</span>` : ""}
        </div>
        ${warnings.length ? `<div class="task-board-hierarchy-warning">${escapeHtml(warnings.join(" "))}</div>` : ""}
        ${renderPlanningExecutionStatus(task)}
        ${renderTaskBoardBrief(task)}
        ${renderTaskBoardBlockerLine(task)}
        ${renderTaskBoardTechnicalDetails(task, unresolvedIds, worktreeContexts)}
      </article>
      ${
        visibleChildren.length
          ? `
            <ol class="task-board-hierarchy-children">
              ${visibleChildren
                .map((child) =>
                  renderTaskBoardHierarchyNode(child, hierarchy, focus, display, nextPath),
                )
                .join("")}
              ${
                visibleChildren.length < childTasks.length
                  ? `
                    <li class="task-board-hierarchy-more">
                      <button
                        type="button"
                        class="secondary"
                        data-task-board-more-children-id="${escapeAttr(taskId)}"
                      >Show ${Math.min(taskBoardHierarchyPageSize, childTasks.length - visibleChildren.length)} more children</button>
                    </li>
                  `
                  : ""
              }
            </ol>
          `
          : ""
      }
    </li>
  `;
}

function taskBoardBlockerCount(task) {
  const blockerKeys = new Set();
  activeBlockersForRecord("task", task.id, task.id).forEach((blocker) =>
    blockerKeys.add(`blocker:${text(blocker.id)}`),
  );
  return blockerKeys.size;
}

function selectedTaskBoardProjectTasks() {
  return values(state.tasks)
    .filter((task) => text(task.project_id) === text(selectedTaskBoardProjectId))
    .filter((task) => taskBoardLaneForTask(task) !== null);
}

function selectTaskBoardTask(taskId, { reveal = false } = {}) {
  const projectTasks = selectedTaskBoardProjectTasks();
  let hierarchy = buildTaskBoardHierarchyIndex(projectTasks);
  const task = hierarchy.byId.get(text(taskId)) || null;
  if (!task) {
    return;
  }
  if (reveal && !taskMatchesTaskBoardFilters(task, hierarchy)) {
    resetTaskBoardFilters();
    hierarchy = buildTaskBoardHierarchyIndex(projectTasks);
  }
  selectedTaskBoardTaskId = text(task.id);
  revealTaskBoardHierarchyPath(task, hierarchy);
  revealTaskBoardLanePage(task, projectTasks, hierarchy);
  renderTaskBoard();
  focusSelectedTaskBoardTask();
}

function prepareSelectedTaskBoardTaskForDisplay() {
  if (!selectedTaskBoardTaskId) {
    return;
  }
  const projectTasks = selectedTaskBoardProjectTasks();
  const hierarchy = buildTaskBoardHierarchyIndex(projectTasks);
  const task = hierarchy.byId.get(selectedTaskBoardTaskId) || null;
  if (!task) {
    selectedTaskBoardTaskId = "";
    return;
  }
  revealTaskBoardHierarchyPath(task, hierarchy);
  revealTaskBoardLanePage(task, projectTasks, hierarchy);
}

function revealTaskBoardHierarchyPath(task, hierarchy) {
  const taskId = text(task.id);
  expandedTaskBoardTaskIds.add(taskId);
  const rootId = hierarchy.rootByTaskId.get(taskId);
  if (rootId) {
    const rootIndex = hierarchy.roots.findIndex((root) => text(root.id) === rootId);
    if (rootIndex >= taskBoardHierarchyRootLimit) {
      taskBoardHierarchyRootLimit =
        (Math.floor(rootIndex / taskBoardHierarchyPageSize) + 1) *
        taskBoardHierarchyPageSize;
    }
  }
  let childId = taskId;
  let parentId = hierarchy.parentById.get(childId) || "";
  while (parentId) {
    expandedTaskBoardTaskIds.add(parentId);
    const siblings = hierarchy.childrenById.get(parentId) || [];
    const childIndex = siblings.findIndex((child) => text(child.id) === childId);
    const currentLimit =
      taskBoardHierarchyChildLimits.get(parentId) || taskBoardHierarchyPageSize;
    if (childIndex >= currentLimit) {
      taskBoardHierarchyChildLimits.set(
        parentId,
        (Math.floor(childIndex / taskBoardHierarchyPageSize) + 1) *
          taskBoardHierarchyPageSize,
      );
    }
    childId = parentId;
    parentId = hierarchy.parentById.get(parentId) || "";
  }
}

function revealTaskBoardLanePage(task, projectTasks, hierarchy) {
  if (terminalTaskStates.has(text(task.state))) {
    taskBoardShowCompleted = true;
  }
  if (!taskMatchesTaskBoardFilters(task, hierarchy)) {
    return;
  }
  const laneId = taskBoardLaneForTask(task);
  if (!laneId) {
    return;
  }
  const laneTasks = projectTasks
    .filter((candidate) => taskMatchesTaskBoardFilters(candidate, hierarchy))
    .filter((candidate) => taskBoardLaneForTask(candidate) === laneId)
    .sort(compareTaskBoardTasks);
  const taskIndex = laneTasks.findIndex((candidate) => text(candidate.id) === text(task.id));
  if (taskIndex >= 0) {
    taskBoardLanePages.set(laneId, Math.floor(taskIndex / taskBoardLanePageSize) + 1);
  }
}

function focusSelectedTaskBoardTask() {
  if (!selectedTaskBoardTaskId) {
    return;
  }
  window.requestAnimationFrame(() => {
    const primary = [...document.querySelectorAll("[data-task-board-primary-task-id]")].find(
      (element) => text(element.dataset.taskBoardPrimaryTaskId) === selectedTaskBoardTaskId,
    );
    const fallback = [...document.querySelectorAll("[data-task-board-select-task-id]")].find(
      (element) => text(element.dataset.taskBoardSelectTaskId) === selectedTaskBoardTaskId,
    );
    const target = primary || fallback;
    target?.focus({ preventScroll: true });
    target
      ?.closest("[data-task-board-task-id]")
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  });
}

function focusTaskBoardControl(value, selector) {
  window.requestAnimationFrame(() => {
    const target = [...document.querySelectorAll(selector)].find((element) =>
      Object.values(element.dataset).some((candidate) => text(candidate) === text(value)),
    );
    target?.focus({ preventScroll: true });
  });
}

function syncTaskBoardSelectors(teams, projects) {
  const teamSelect = document.querySelector("[data-task-board-team]");
  const projectSelect = document.querySelector("[data-task-board-project]");
  if (teamSelect) {
    teamSelect.dataset.currentValue = selectedTaskBoardTeamId;
    teamSelect.dataset.placeholder = "Select team";
    fillSelect(
      teamSelect,
      teams.map((team) => [team.id, `${team.name || team.id} (${team.id})`]),
    );
  }
  if (projectSelect) {
    projectSelect.dataset.currentValue = selectedTaskBoardProjectId;
    projectSelect.dataset.placeholder = selectedTaskBoardTeamId ? "Select project" : "Select team first";
    projectSelect.disabled = !selectedTaskBoardTeamId;
    fillSelect(
      projectSelect,
      projects.map((project) => [project.id, `${project.name || project.id} (${project.id})`]),
    );
  }
}

function renderTaskBoardLane(lane, focus) {
  const total = lane.tasks.length;
  const pageCount = Math.max(1, Math.ceil(total / taskBoardLanePageSize));
  const requestedPage = taskBoardLanePages.get(lane.id) || 1;
  const page = Math.min(pageCount, Math.max(1, requestedPage));
  taskBoardLanePages.set(lane.id, page);
  const startIndex = (page - 1) * taskBoardLanePageSize;
  const pageTasks = lane.tasks.slice(startIndex, startIndex + taskBoardLanePageSize);
  return `
    <section
      class="task-board-lane task-board-lane-${escapeAttr(lane.id)}"
      aria-label="${escapeAttr(lane.label)}"
      data-task-board-lane-id="${escapeAttr(lane.id)}"
    >
      <div class="task-board-lane-header">
        <h3>${escapeHtml(lane.label)}</h3>
        <span>${lane.matchingTasks.length}</span>
      </div>
      ${
        lane.hiddenCompletedCount
          ? `
            <div class="task-board-completed-hidden">
              <p>${lane.hiddenCompletedCount} completed task${lane.hiddenCompletedCount === 1 ? "" : "s"} hidden.</p>
              <button type="button" class="secondary" data-task-board-reveal-completed>Show completed</button>
            </div>
          `
          : pageTasks.length
            ? `
              <ul class="task-board-card-list">
                ${pageTasks.map((task) => renderTaskBoardCard(task, focus)).join("")}
              </ul>
              ${renderTaskBoardLanePager(lane.id, page, pageCount, startIndex, total)}
            `
            : `<p class="empty-state task-board-empty">No tasks.</p>`
      }
    </section>
  `;
}

function renderTaskBoardLanePager(laneId, page, pageCount, startIndex, total) {
  if (!total) {
    return "";
  }
  const endIndex = Math.min(total, startIndex + taskBoardLanePageSize);
  return `
    <nav class="task-board-lane-pagination" aria-label="${escapeAttr(humanizeToken(laneId))} lane pages">
      <span>Showing ${startIndex + 1}–${endIndex} of ${total}</span>
      <div>
        <button
          type="button"
          class="secondary"
          data-task-board-lane-id="${escapeAttr(laneId)}"
          data-task-board-lane-page="${page - 1}"
          ${page <= 1 ? "disabled" : ""}
        >Previous</button>
        <span>Page ${page} of ${pageCount}</span>
        <button
          type="button"
          class="secondary"
          data-task-board-lane-id="${escapeAttr(laneId)}"
          data-task-board-lane-page="${page + 1}"
          ${page >= pageCount ? "disabled" : ""}
        >Next</button>
      </div>
    </nav>
  `;
}

function renderTaskBoardCard(task, focus) {
  const assignedTo = taskAssignmentLabel(task);
  const blocked = isTaskBoardBlocked(task);
  const stateLabel = taskStateLabel(task);
  const laneEntered = taskBoardLaneEnteredText(task);
  const depth = taskHierarchyDepth(task);
  const assessment = task.assessment || {};
  const unresolvedIds = assessment.unresolved_descendant_ids || [];
  const worktreeContexts = taskGitWorkContexts(task);
  const relation = taskBoardRelationLabel(task.id, focus);
  const relationClasses = taskBoardRelationClasses(task.id, focus);
  return `
    <li
      class="task-board-card${blocked ? " task-board-card-blocked" : ""}${relationClasses ? ` ${escapeAttr(relationClasses)}` : ""}"
      data-task-board-task-id="${escapeAttr(task.id)}"
    >
      <div class="task-board-card-title-row">
        <strong>
          <button
            type="button"
            class="task-board-task-link task-board-task-title"
            data-task-board-select-task-id="${escapeAttr(task.id)}"
            data-task-board-primary-task-id="${escapeAttr(task.id)}"
            ${text(focus.task?.id) === text(task.id) ? 'aria-current="true"' : ""}
          >${escapeHtml(task.title)}</button>
        </strong>
        <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(stateLabel)}</span>
      </div>
      <div class="task-board-card-badges">
        ${blocked && task.state !== "blocked" ? `<span class="state-pill blocked">Blocked</span>` : ""}
        ${renderTaskBoardDependencyWaitBadge(task)}
        ${renderRoleTaskQueueStatus(task)}
        ${relation ? `<span class="task-board-relation-badge">${escapeHtml(relation)}</span>` : ""}
      </div>
      <div class="task-board-card-meta">Priority ${Number(task.priority ?? 0)}</div>
      ${task.parent_task_id ? `<div class="task-board-card-meta">Parent: ${renderTaskBoardTaskLink(task.parent_task_id, { reveal: true })}</div>` : ""}
      ${task.originating_task_id ? `<div class="task-board-card-meta">Provenance: ${renderTaskBoardTaskLink(task.originating_task_id, { reveal: true })}</div>` : ""}
      <div class="task-board-card-meta">Assigned to: ${escapeHtml(assignedTo || "Unassigned")}</div>
      <div class="task-board-card-meta">Entered lane: ${escapeHtml(laneEntered)}</div>
      <div class="task-board-card-meta">Readiness: ${escapeHtml(task.readiness || assessment.readiness || "unknown")} - resolution: ${escapeHtml(task.resolution_status || assessment.resolution_status || "unknown")} - integration ${task.integration_ready ?? assessment.integration_ready ? "ready" : "not ready"}</div>
      ${renderPlanningExecutionStatus(task)}
      ${renderTaskBoardBrief(task)}
      ${renderTaskBoardUnresolvedDescendants(task.id, unresolvedIds)}
      ${worktreeContexts.length ? `<div class="task-board-card-meta">Worktree: ${escapeHtml(worktreeContexts.map((row) => row.worktree_path || "Control-plane context").join(", "))}</div>` : ""}
      ${task.cancellation ? renderTaskCancellation(task.cancellation, task) : ""}
      ${renderTaskBoardBlockerLine(task)}
      ${renderTaskBoardTechnicalDetails(task, unresolvedIds, worktreeContexts)}
      <div class="record-actions">
        ${editButton("task", task.id)}
        ${taskCancellationButton(task)}
      </div>
    </li>
  `;
}

function renderTaskBoardTaskLink(taskId, { reveal = false } = {}) {
  const normalizedTaskId = text(taskId);
  const linkedTask = state.tasks[normalizedTaskId] || null;
  if (!linkedTask) {
    return `<span>Unavailable task</span>`;
  }
  return `
    <button
      type="button"
      class="task-board-task-link task-board-relationship-link"
      data-task-board-select-task-id="${escapeAttr(normalizedTaskId)}"
      ${reveal ? "data-task-board-reveal-task" : ""}
    >${escapeHtml(taskBoardTaskReferenceLabel(normalizedTaskId))}</button>
  `;
}

function taskBoardTaskReferenceLabel(taskId) {
  const linkedTask = state.tasks[text(taskId)];
  return text(linkedTask?.title) || "Unavailable task";
}

function renderTaskBoardUnresolvedDescendants(taskId, unresolvedIds) {
  const ids = [...new Set((unresolvedIds || []).map(text).filter(Boolean))];
  if (!ids.length) {
    return "";
  }
  const descendants = ids.map((id) => {
    const task = state.tasks[id] || null;
    return {
      id,
      label: text(task?.title) || "Unavailable descendant",
      state: text(task?.state),
      stateLabel: task ? taskStateLabel(task) : "Unavailable",
    };
  });
  const previewLabels = descendants.slice(0, 3).map((descendant) => descendant.label);
  if (descendants.length > previewLabels.length) {
    previewLabels.push(`+${descendants.length - previewLabels.length} more`);
  }
  return `
    <div class="task-board-card-descendants">
      <div class="task-board-card-descendant-summary">
        <strong>${descendants.length} unresolved descendant${descendants.length === 1 ? "" : "s"}</strong>
        <span>${escapeHtml(previewLabels.join(", "))}</span>
      </div>
      <details class="task-board-descendant-details" ${teamDisclosureAttributes(teamDisclosureKey("task-board", "task", taskId, "descendants"))}>
        <summary>View all ${descendants.length} descendant${descendants.length === 1 ? "" : "s"}</summary>
        <ul class="task-board-descendant-list">
          ${descendants.map((descendant) => `
            <li>
              ${
                state.tasks[descendant.id] && taskBoardLaneForTask(state.tasks[descendant.id])
                  ? renderTaskBoardTaskLink(descendant.id, { reveal: true })
                  : `<span>${escapeHtml(descendant.label)}</span>`
              }
              <span class="state-pill ${escapeAttr(descendant.state || "neutral")}">${escapeHtml(descendant.stateLabel)}</span>
            </li>
          `).join("")}
        </ul>
      </details>
    </div>
  `;
}

function renderTaskBoardTechnicalDetails(task, unresolvedIds, worktreeContexts) {
  const worktreeContextIds = worktreeContexts
    .map((row) => text(row.id || row.branch_context_id))
    .filter(Boolean);
  const blockerIds = [
    ...activeBlockersForRecord("task", task.id, task.id).map((blocker) => text(blocker.id)),
    ...inheritedTaskBlockers(task).map((blocker) => text(blocker.blocker_id)),
  ].filter(Boolean);
  const rows = [
    ["Task ID", text(task.id)],
    ["Hierarchy depth", String(taskHierarchyDepth(task))],
    ["Parent task ID", text(task.parent_task_id)],
    ["Originating task ID", text(task.originating_task_id)],
    ["Commit author", task.authored_by ? `${task.authored_by.name} <${task.authored_by.email}>` : "Project default"],
    ["Git base branch", text(task.git_base_branch)],
    ["Git base commit pin", text(task.git_base_commit_sha)],
    ["Dependency task IDs", (task.dependency_task_ids || []).map(text).filter(Boolean).join(", ")],
    ["Integration boundary", task.integration_boundary ? "Yes" : ""],
    ["Requires Git provenance", task.deliverable_contract?.require_git_provenance ? "Yes" : ""],
    ["Required contribution task IDs", (task.deliverable_contract?.required_contribution_task_ids || []).map(text).filter(Boolean).join(", ")],
    ["Unresolved dependency task IDs", taskBoardDependencyWaitIds(task).join(", ")],
    ["Blocker IDs", [...new Set(blockerIds)].join(", ")],
    ["Worktree context IDs", worktreeContextIds.join(", ")],
    ["Unresolved descendant IDs", (unresolvedIds || []).map(text).filter(Boolean).join(", ")],
  ].filter(([, value]) => value);
  return `
    <details class="technical-details task-board-technical-details" ${teamDisclosureAttributes(teamDisclosureKey("task-board", "task", task.id, "technical"))}>
      <summary>Technical details</summary>
      <dl class="technical-details-list">
        ${rows.map(([label, value]) => `
          <div>
            <dt>${escapeHtml(label)}</dt>
            <dd>${escapeHtml(value)}</dd>
          </div>
        `).join("")}
      </dl>
    </details>
  `;
}

function taskHierarchyDepth(task) {
  let depth = 0;
  let current = task;
  const seen = new Set([text(task?.id)]);
  while (current?.parent_task_id && depth < 32) {
    const parentId = text(current.parent_task_id);
    if (!parentId || seen.has(parentId) || !state.tasks[parentId]) {
      break;
    }
    seen.add(parentId);
    depth += 1;
    current = state.tasks[parentId];
  }
  return depth;
}

function taskBoardLaneForTask(task) {
  return taskBoardStateToLane.get(task.state) || null;
}

function compareTaskBoardTasks(left, right) {
  const leftPriority = Number(left.priority ?? 0);
  const rightPriority = Number(right.priority ?? 0);
  if (leftPriority !== rightPriority) {
    return leftPriority - rightPriority;
  }
  const leftEntered = taskBoardSortTime(left);
  const rightEntered = taskBoardSortTime(right);
  if (leftEntered !== rightEntered) {
    return rightEntered - leftEntered;
  }
  return (
    String(left.title || "").localeCompare(String(right.title || "")) ||
    byId(left, right)
  );
}

function taskBoardSortTime(task) {
  const timestamp = task.state_entered_at || task.updated_at || task.created_at;
  if (!timestamp) {
    return Number.NEGATIVE_INFINITY;
  }
  const parsed = Date.parse(normalizeTimelineTimestampForSort(timestamp));
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function taskBoardLaneEnteredText(task) {
  const timestamp = task.state_entered_at || task.updated_at || task.created_at;
  return timestamp ? formatTimelineTimestamp(timestamp) : "Unknown";
}

function renderTaskBoardBrief(task) {
  const brief = text(task?.description);
  if (!brief) {
    return "";
  }
  const isLong = brief.length > 240 || brief.split(/\r?\n/).length > 3;
  if (!isLong) {
    return `<div class="task-board-task-brief"><strong>Task brief:</strong> ${escapeHtml(brief)}</div>`;
  }
  return `
    <details class="task-board-task-brief task-board-task-brief-details" ${teamDisclosureAttributes(teamDisclosureKey("task-board", "brief", task.id))}>
      <summary>Show full task brief: ${escapeHtml(readablePreview(brief, 160))}</summary>
      <div class="task-board-task-brief-content">${escapeHtml(brief)}</div>
    </details>
  `;
}

function isTaskBoardBlocked(task) {
  const readiness = taskBoardReadiness(task);
  return (
    task.state === "blocked" ||
    readiness === "blocked" ||
    readiness === "invalid" ||
    Boolean(activeBlockersForRecord("task", task.id, task.id).length)
  );
}

function taskBoardDependencyWaitIds(task) {
  const assessmentIds = task?.assessment?.unsatisfied_dependency_ids || [];
  const derivedIds = task?.blocked_by_task_ids || [];
  return [...new Set([...assessmentIds, ...derivedIds].map(text).filter(Boolean))];
}

function taskBoardBlockedDependencyIds(task) {
  return taskBoardDependencyWaitIds(task).filter((taskId) =>
    ["blocked", "rejected"].includes(text(state.tasks[taskId]?.state)),
  );
}

function taskBoardWaitsOnDependencies(task) {
  const readiness = taskBoardReadiness(task);
  const dependencyStatus = text(task?.dependency_status);
  const resolution = text(task?.resolution_status || task?.assessment?.resolution_status);
  if (terminalTaskStates.has(text(task?.state)) || resolution === "resolved") {
    return false;
  }
  return (
    readiness === "waiting_on_dependencies" ||
    ((dependencyStatus === "waiting" || dependencyStatus === "blocked") &&
      taskBoardDependencyWaitIds(task).length > 0)
  );
}

function renderTaskBoardDependencyWaitBadge(task) {
  if (!taskBoardWaitsOnDependencies(task)) {
    return "";
  }
  const count = taskBoardDependencyWaitIds(task).length;
  const dependencyLabel = count === 1 ? "dependency" : "dependencies";
  const blockedCount = taskBoardBlockedDependencyIds(task).length;
  const blockedDetail = blockedCount
    ? `${blockedCount} blocked`
    : text(task.dependency_status) === "blocked"
      ? "at least one blocked"
      : "";
  const label = `${count ? `Waiting on ${count} ${dependencyLabel}` : "Waiting on dependencies"}${
    blockedDetail ? `; ${blockedDetail}` : ""
  }`;
  return `<span class="state-pill warning">${escapeHtml(label)}</span>`;
}

function renderTaskBoardBlockerLine(task) {
  const blockers = [];
  const dependencyWaitIds = taskBoardDependencyWaitIds(task);
  if (taskBoardWaitsOnDependencies(task)) {
    const waitLabel = "Waiting on dependencies:";
    blockers.push(
      dependencyWaitIds.length
        ? `<span><strong>${waitLabel}</strong> ${dependencyWaitIds
            .map((taskId) => renderTaskBoardTaskLink(taskId, { reveal: true }))
            .join(", ")}</span>`
        : `<span><strong>${waitLabel}</strong> Prerequisite work is not yet resolved.</span>`,
    );
  }
  const routedBlockers = activeBlockersForRecord("task", task.id, task.id);
  routedBlockers.forEach((blocker) => {
    const summary =
      text(blocker.summary || blocker.reason || blocker.details) ||
      humanizeToken(blocker.reason_code || "Open blocker");
    const owner = state.agents[text(blocker.owner_agent_id)] || null;
    const ownerLabel = owner ? agentLabel(owner) : "";
    blockers.push(
      `<span><strong>Assigned blocker:</strong> ${escapeHtml(summary)}${ownerLabel ? ` - owner ${escapeHtml(ownerLabel)}` : ""}</span>`,
    );
  });
  inheritedTaskBlockers(task).forEach((blocker) => {
    const relationship = humanizeToken(blocker.relationship || "upstream");
    const reason = text(blocker.reason || blocker.summary) || "No reason supplied";
    blockers.push(
      `<span><strong>Blocker on ${escapeHtml(relationship.toLocaleLowerCase())}:</strong> ${renderTaskBoardTaskLink(
        blocker.upstream_task_id,
        { reveal: true },
      )} <span aria-hidden="true">—</span> <span class="task-board-blocker-reason">${escapeHtml(reason)}</span></span>`,
    );
  });
  if (!blockers.length) {
    return "";
  }
  return `<div class="task-board-card-blockers">${blockers.join("")}</div>`;
}

function inheritedTaskBlockers(task) {
  return Array.isArray(task?.inherited_blockers)
    ? task.inherited_blockers.filter((blocker) => blocker && blocker.blocker_id)
    : [];
}

function inheritedTaskBlockerLabel(blocker) {
  const relationship = text(blocker.relationship) || "upstream";
  const upstreamTaskId = text(blocker.upstream_task_id) || "unknown task";
  const blockerId = text(blocker.blocker_id) || "unknown blocker";
  const reasonCode = text(blocker.reason_code) || "unspecified";
  const reason = text(blocker.reason || blocker.summary) || "No reason supplied";
  return `${relationship} ${upstreamTaskId}: blocker ${blockerId} [${reasonCode}] ${reason}`;
}

function activeBlockersForRecord(sourceKind, sourceId, taskId = null) {
  return values(state.blockers)
    .filter((blocker) => {
      if (text(blocker.state) !== "open") {
        return false;
      }
      if (text(blocker.source_kind) === sourceKind && text(blocker.source_id) === sourceId) {
        return true;
      }
      return Boolean(taskId && text(blocker.task_id) === taskId);
    })
    .sort(byId);
}

function blockerRoutingLabel(blocker) {
  const parts = [
    `blocker ${text(blocker.id)}`,
    blocker.owner_agent_id ? `owner ${text(blocker.owner_agent_id)}` : "",
    blocker.delegate_agent_id ? `delegate ${text(blocker.delegate_agent_id)}` : "",
    blocker.route ? `route ${humanizeToken(blocker.route)}` : "",
    blocker.resolution_scope ? `scope ${humanizeToken(blocker.resolution_scope)}` : "",
    Number.isFinite(Number(blocker.escalation_depth))
      ? `level ${Number(blocker.escalation_depth)}`
      : "",
  ].filter(Boolean);
  return parts.join(" - ");
}

function releaseRecoveryTaskForBlocker(blocker) {
  if (
    !blocker ||
    text(blocker.source_kind) !== "release_package" ||
    !["governance", "operator"].includes(text(blocker.route))
  ) {
    return null;
  }
  const terminalStates = new Set(["finished", "accepted", "rejected", "cancelled"]);
  const candidates = uniqueTextList([
    blocker.task_id,
    ...(blocker.affected_task_ids || []),
  ])
    .map((taskId) => state.tasks?.[taskId])
    .filter(
      (task) =>
        task &&
        text(task.created_by_actor) === "system:git-merge-recovery" &&
        !terminalStates.has(text(task.state)),
    );
  return candidates.length === 1 ? candidates[0] : null;
}

function candidateCorrectionRecoveryTaskForBlocker(blocker) {
  if (!blocker || text(blocker.source_kind) !== "candidate_correction_successor") {
    return null;
  }
  const correctionTaskIds = new Set(uniqueTextList([
    blocker.task_id,
    ...(blocker.affected_task_ids || []),
  ]));
  return values(state.tasks)
    .filter(
      (task) =>
        text(task.created_by_actor) === "system:candidate-correction-recovery" &&
        correctionTaskIds.has(text(task.originating_task_id)) &&
        (!blocker.project_id || text(task.project_id) === text(blocker.project_id)),
    )
    .sort(
      (left, right) =>
        String(right.created_at || "").localeCompare(String(left.created_at || "")) ||
        byId(left, right),
    )[0] || null;
}

function dependencyCompositionRecoveryForBlocker(blocker) {
  return values(state.dependency_composition_recoveries)
    .filter((recovery) => text(recovery.blocker_id) === text(blocker?.id))
    .sort(
      (left, right) =>
        String(right.created_at || "").localeCompare(String(left.created_at || "")) ||
        byId(left, right),
    )[0] || null;
}

function renderDependencyCompositionRecovery(blocker, recovery, extraClass = "") {
  const recoveryTask = state.tasks?.[text(recovery.recovery_task_id)];
  const projectAgents = values(state.agents)
    .filter((agent) =>
      text(agent.status) === "active" &&
      agentProjectIds(agent).includes(text(recovery.project_id)),
    )
    .sort(comparePeopleAgents);
  const sourceSummary = (recovery.dependency_git_sources || [])
    .map((source) => `${text(source.dependency_task_id)}@${text(source.commit_sha).slice(0, 12)}`)
    .join(", ");
  const conflictSummary = uniqueTextList(recovery.conflict_paths).join(", ") || "paths unavailable";
  return `
    <div class="dependency-composition-recovery-status" role="status">
      <div>
        <strong>Dependency merge recovery</strong>
        <span class="state-pill ${escapeAttr(recovery.state || "awaiting_assignment")}">${escapeHtml(humanizeToken(recovery.state || "awaiting_assignment"))}</span>
      </div>
      <div class="meta-line">Pinned base ${escapeHtml(text(recovery.base_commit_sha).slice(0, 12))} - conflict ${escapeHtml(conflictSummary)}</div>
      <div class="meta-line">Exact inputs: ${escapeHtml(sourceSummary)}</div>
      ${
        recoveryTask
          ? `<div class="record-actions ${escapeAttr(extraClass)}">
              <button type="button" class="record-action"
                data-dependency-composition-recovery-task-id="${escapeAttr(recoveryTask.id)}"
              >View merge recovery task</button>
            </div>`
          : `<div class="record-actions ${escapeAttr(extraClass)}">
              <label>
                Resolver
                <select data-dependency-composition-resolver>
                  <option value="">Select project agent</option>
                  ${projectAgents.map((agent) => `<option value="${escapeAttr(agent.id)}">${escapeHtml(agentLabel(agent))}</option>`).join("")}
                </select>
              </label>
              <button type="button" class="record-action"
                data-start-dependency-composition-recovery-id="${escapeAttr(blocker.id)}"
                ${lockedAttrs("start dependency composition recoveries")}
              >Create merge recovery task</button>
            </div>`
      }
    </div>
  `;
}

function latestDispatchForTask(taskId) {
  const normalizedTaskId = text(taskId);
  return values(state.dispatches)
    .filter((dispatch) =>
      (dispatch.task_ids || []).some((candidateId) => text(candidateId) === normalizedTaskId),
    )
    .sort(
      (left, right) =>
        String(right.created_at || "").localeCompare(String(left.created_at || "")) ||
        byId(left, right),
    )[0] || null;
}

function latestDispatchForReview(reviewId) {
  const normalizedReviewId = text(reviewId);
  return values(state.dispatches)
    .filter((dispatch) =>
      (dispatch.review_request_ids || []).some(
        (candidateId) => text(candidateId) === normalizedReviewId,
      ),
    )
    .sort(
      (left, right) =>
        String(right.created_at || "").localeCompare(String(left.created_at || "")) ||
        byId(left, right),
    )[0] || null;
}

function latestTaskPreparationInvalidation(taskId) {
  return taskPreparationInvalidationByTaskId.get(text(taskId)) || null;
}

function renderPlanningExecutionStatus(task) {
  const blockers = activeBlockersForRecord("task", task.id, task.id);
  if (blockers.length) {
    return `<div class="planning-execution-status" role="status"><strong>Blocked</strong><span>${escapeHtml(blockers[0].summary || "Resolve the active task blocker.")}</span></div>`;
  }
  const invalidation = latestTaskPreparationInvalidation(task.id);
  const dispatch = latestDispatchForTask(task.id);
  const dispatchIsFresh = Boolean(
    dispatch &&
    (!invalidation || String(dispatch.created_at || "") >= String(invalidation.created_at || "")),
  );
  if (
    dispatchIsFresh &&
    dispatch &&
    ["preparing", "leased"].includes(text(dispatch.status))
  ) {
    const runtime = state.agent_runtimes?.[dispatch.agent_id];
    const lease = runtime?.wake_leased_until
      ? ` Lease until ${formatTimelineTimestamp(runtime.wake_leased_until)}.`
      : "";
    return `<div class="planning-execution-status" role="status"><strong>Agent active</strong><span>${escapeHtml(`${humanizeToken(dispatch.status)}.${lease}`)}</span></div>`;
  }
  if (invalidation && text(invalidation.state) === "pending") {
    const dispatchLabel = dispatch
      ? `Latest dispatch: ${humanizeToken(dispatch.status || "unknown")}.`
      : "Fresh preparation is queued.";
    return `<div class="planning-execution-status" role="status"><strong>Repreparing after task edit</strong><span>${escapeHtml(dispatchLabel)}</span></div>`;
  }
  if (dispatch && ["preparing", "leased"].includes(text(dispatch.status))) {
    const runtime = state.agent_runtimes?.[dispatch.agent_id];
    const lease = runtime?.wake_leased_until
      ? ` Lease until ${formatTimelineTimestamp(runtime.wake_leased_until)}.`
      : "";
    return `<div class="planning-execution-status" role="status"><strong>Agent active</strong><span>${escapeHtml(`${humanizeToken(dispatch.status)}.${lease}`)}</span></div>`;
  }
  return "";
}

function renderCandidateCorrectionRecoveryStatus(task) {
  const dispatch = latestDispatchForTask(task.id);
  const assignee = state.agents?.[text(task.owner_agent_id)];
  const details = [
    `Task ${text(task.id)}`,
    `assigned to ${assignee ? agentLabel(assignee) : text(task.owner_agent_id) || "unassigned"}`,
    task.git_base_commit_sha ? `pinned commit ${text(task.git_base_commit_sha)}` : "",
    dispatch ? `dispatch ${humanizeToken(dispatch.status || "unknown")}` : "awaiting dispatch",
  ].filter(Boolean);
  return `
    <div class="candidate-correction-recovery-status" role="status">
      <div>
        <strong>Verified replacement task</strong>
        <span class="state-pill ${escapeAttr(task.state || "pending")}">${escapeHtml(humanizeToken(task.state || "pending"))}</span>
      </div>
      <div>${escapeHtml(task.title || task.id)}</div>
      <div class="meta-line">${escapeHtml(details.join(" - "))}</div>
    </div>
  `;
}

function openCandidateCorrectionRecoveryTask(taskId) {
  const task = state.tasks?.[text(taskId)];
  const project = state.projects?.[text(task?.project_id)];
  if (!task || !project) {
    setStatus("The verified replacement task is not visible in the current scope.", "warning");
    return;
  }
  resetTaskBoardProjectState();
  selectedTaskBoardTeamId = text(project.team_id);
  selectedTaskBoardProjectId = text(project.id);
  selectedTaskBoardTaskId = text(task.id);
  if (terminalTaskStates.has(text(task.state))) {
    taskBoardShowCompleted = true;
  }
  prepareSelectedTaskBoardTaskForDisplay();
  setTeamView("task-board");
  window.requestAnimationFrame(() => focusSelectedTaskBoardTask());
}

function renderTerminalBlockerResolveAction(blocker, extraClass = "") {
  const dependencyRecovery = dependencyCompositionRecoveryForBlocker(blocker);
  if (
    blocker &&
    text(blocker.state) === "open" &&
    dependencyRecovery &&
    ["awaiting_assignment", "in_progress"].includes(text(dependencyRecovery.state))
  ) {
    return renderDependencyCompositionRecovery(blocker, dependencyRecovery, extraClass);
  }
  if (
    blocker &&
    text(blocker.state) === "open" &&
    text(blocker.source_kind) === "candidate_correction_successor"
  ) {
    const recoveryTask = candidateCorrectionRecoveryTaskForBlocker(blocker);
    if (recoveryTask) {
      const replacementStillActive = !terminalTaskStates.has(text(recoveryTask.state));
      return `
        ${renderCandidateCorrectionRecoveryStatus(recoveryTask)}
        <div class="record-actions ${escapeAttr(extraClass)}">
          <button type="button" class="record-action"
            data-candidate-correction-recovery-task-id="${escapeAttr(recoveryTask.id)}"
          >View replacement task</button>
          ${
            replacementStillActive
              ? ""
              : `<button type="button" class="record-action"
                  data-candidate-correction-recovery-id="${escapeAttr(blocker.id)}"
                  data-candidate-correction-recovery-action="retry"
                  ${lockedAttrs("retry candidate correction recovery")}
                >Retry packaging</button>`
          }
        </div>
      `;
    }
    return `
      <div class="record-actions ${escapeAttr(extraClass)}">
        <button type="button" class="record-action"
          data-candidate-correction-recovery-id="${escapeAttr(blocker.id)}"
          data-candidate-correction-recovery-action="retry"
          ${lockedAttrs("retry candidate correction recovery")}
        >Retry packaging</button>
        <button type="button" class="record-action"
          data-candidate-correction-recovery-id="${escapeAttr(blocker.id)}"
          data-candidate-correction-recovery-action="create_verified_replacement"
          ${lockedAttrs("create verified replacement tasks")}
        >Create verified replacement</button>
      </div>
    `;
  }
  if (
    !blocker ||
    text(blocker.state) !== "open" ||
    !["governance", "operator"].includes(text(blocker.route))
  ) {
    return "";
  }
  if (text(blocker.source_kind) === "release_package") {
    const recoveryTask = releaseRecoveryTaskForBlocker(blocker);
    if (!recoveryTask) {
      return "";
    }
    const pendingDirective = Object.values(state.inbox || {}).find(
      (item) =>
        text(item.item_type) === "operator_directive" &&
        text(item.state) === "pending" &&
        text(item.task_id) === text(recoveryTask.id) &&
        text(item.payload?.source) === "admin_ui_release_recovery",
    );
    return `
      <div class="record-actions ${escapeAttr(extraClass)}">
        <button
          type="button"
          class="record-action"
          data-resume-release-recovery-blocker-id="${escapeAttr(blocker.id)}"
          data-resume-release-recovery-task-id="${escapeAttr(recoveryTask.id)}"
          ${pendingDirective ? "disabled aria-disabled=\"true\"" : lockedAttrs("resume release recoveries")}
        >${pendingDirective ? "Recovery wake queued" : "Resume release recovery"}</button>
      </div>
    `;
  }
  return `
    <div class="record-actions ${escapeAttr(extraClass)}">
      <button
        type="button"
        class="record-action"
        data-resolve-terminal-blocker-id="${escapeAttr(blocker.id)}"
        ${lockedAttrs("resolve terminal blockers")}
      >Resolve blocker</button>
    </div>
  `;
}

function renderRecordBlockers(sourceKind, sourceId, taskId = null) {
  const blockers = activeBlockersForRecord(sourceKind, sourceId, taskId);
  if (!blockers.length) {
    return "";
  }
  return blockers
    .map((blocker) => `
      <div class="meta-line team-blocker-line">
        <span>${escapeHtml(blockerRoutingLabel(blocker))}</span>
        ${renderTerminalBlockerResolveAction(blocker)}
      </div>
    `)
    .join("");
}

function planningTasksForProject(projectTasks) {
  return projectTasks
    .filter((task) => selectedPlanningTaskStates.has(task.state))
    .sort(comparePlanningTasks);
}

function comparePlanningTasks(left, right) {
  return (
    planningTaskStatusRank(left.state) - planningTaskStatusRank(right.state) ||
    byPriority(left, right)
  );
}

function planningTaskStatusRank(status) {
  const order = {
    blocked: 0,
    waiting_for_input: 1,
    waiting_for_review: 2,
    in_progress: 3,
    pending: 4,
    finished: 5,
    accepted: 6,
    rejected: 7,
    cancelled: 8,
  };
  return order[status] ?? 99;
}

function comparePlanningProjectCards(left, right) {
  const leftBlocked = left.visibleTasks.filter(isTaskWaitingOrBlocked).length;
  const rightBlocked = right.visibleTasks.filter(isTaskWaitingOrBlocked).length;
  return (
    rightBlocked - leftBlocked ||
    right.visibleTasks.length - left.visibleTasks.length ||
    projectSortKey(left.project).localeCompare(projectSortKey(right.project))
  );
}

function renderPlanningProjectBlock(project, projectTasks, visibleTasks, projectDeliverables) {
  const openTasks = visibleTasks.filter((task) => !terminalTaskStates.has(task.state));
  const blockedTasks = visibleTasks.filter((task) => task.state === "blocked");
  const waitingForInputTasks = visibleTasks.filter(
    (task) => task.state === "waiting_for_input",
  );
  const waitingTasks = visibleTasks.filter((task) => task.state === "waiting_for_review");
  const goals = project.goals || [];
  const hiddenCount = projectTasks.length - visibleTasks.length;
  const detailBits = [
    `${visibleTasks.length} shown`,
    `${openTasks.length} open`,
    `${projectDeliverables.length} deliverable${projectDeliverables.length === 1 ? "" : "s"}`,
  ];
  if (blockedTasks.length) {
    detailBits.push(`${blockedTasks.length} blocked`);
  }
  if (waitingForInputTasks.length) {
    detailBits.push(`${waitingForInputTasks.length} waiting input`);
  }
  if (waitingTasks.length) {
    const acceptanceCount = waitingTasks.filter(
      (task) => task.waiting_reason === "superior_acceptance",
    ).length;
    const reviewCount = waitingTasks.length - acceptanceCount;
    if (acceptanceCount) {
      detailBits.push(`${acceptanceCount} waiting acceptance`);
    }
    if (reviewCount) {
      detailBits.push(`${reviewCount} waiting review`);
    }
  }
  if (hiddenCount > 0) {
    detailBits.push(`${hiddenCount} hidden`);
  }
  return `
    <article class="project-block planning-project-card" data-planning-project-id="${escapeAttr(project.id)}">
      <details ${planningDisclosureAttributes("project", project.id)}>
        <summary class="planning-project-summary">
          <span class="planning-project-main">
            <strong>${escapeHtml(project.name)}</strong>
            <span>${escapeHtml(project.id)} - lead ${escapeHtml(project.lead_agent_id)}</span>
          </span>
          <span class="planning-project-meta">
            <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
            <span>${escapeHtml(detailBits.join(" - "))}</span>
          </span>
        </summary>
        <div class="planning-project-body">
          <div class="project-header">
            <div>
              <strong>${escapeHtml(project.name)}</strong>
              <div class="meta-line">${escapeHtml(project.id)} - lead ${escapeHtml(project.lead_agent_id)}</div>
              ${renderProjectCostCounter(project.id)}
              ${renderProjectGit(project.git)}
              ${renderProjectReleasePolicy(project.release_policy)}
            </div>
            <div class="record-actions">
              <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
              ${editButton("project", project.id)}
            </div>
          </div>
          <div class="project-columns planning-project-columns">
            <div>
              <p class="subheading">Goals</p>
              ${renderGoals(project.id, goals, { compact: true })}
              <p class="subheading">Deliverables</p>
              ${renderDeliverables(projectDeliverables, { compact: true })}
            </div>
            <div>
              <p class="subheading">Tasks</p>
              ${renderTasks(visibleTasks, { compact: true, structuredPlanning: true })}
            </div>
          </div>
        </div>
      </details>
    </article>
  `;
}

function teamDisclosureKey(...parts) {
  return parts.map(text).filter(Boolean).join(":");
}

function teamDisclosureStateKey(key, view = activeTeamView) {
  const normalizedKey = text(key);
  if (!normalizedKey) {
    return "";
  }
  return teamDisclosureKey(normalizedTeamView(view), normalizedKey);
}

function teamDisclosureAttributes(key) {
  const normalizedKey = teamDisclosureStateKey(key);
  if (!normalizedKey) {
    return "";
  }
  return `data-team-disclosure-key="${escapeAttr(normalizedKey)}"${openTeamDisclosures.has(normalizedKey) ? " open" : ""}`;
}

function rememberOpenTeamDisclosures(root = document) {
  root.querySelectorAll?.("details[data-team-disclosure-key]").forEach((disclosure) => {
    const key = text(disclosure.dataset.teamDisclosureKey);
    if (!key) {
      return;
    }
    if (disclosure.open) {
      openTeamDisclosures.add(key);
    } else {
      openTeamDisclosures.delete(key);
    }
  });
}

function planningDisclosureKey(kind, recordId) {
  return teamDisclosureKey("planning", kind, recordId);
}

function planningDisclosureAttributes(kind, recordId) {
  return teamDisclosureAttributes(planningDisclosureKey(kind, recordId));
}

function renderProjectGit(git) {
  if (!git || !git.managed_repo_id) {
    return "";
  }
  const parts = [`managed ${git.managed_repo_id}`];
  if (git.base_branch) parts.push(`base ${git.base_branch}`);
  if (git.agent_author_identity) parts.push(`author ${git.agent_author_identity.name}`);
  if (git.agent_committer_identity) parts.push(`committer ${git.agent_committer_identity.name}`);
  return `<div class="meta-line">Git: ${escapeHtml(parts.join(" - "))}</div>`;
}

function renderProjectReleasePolicy(policy) {
  if (!policy) {
    return `<div class="meta-line">Release gates: not configured (Git releases are blocked)</div>`;
  }
  const commands = (policy.verification_commands || []).map((command) => command.name);
  const reviewers = (policy.review_requirements || []).map((requirement) => requirement.reviewer_agent_id);
  const human = policy.human_review_requirement;
  const humanLabel = human
    ? `human review via ${(human.reviewer_group_ids || []).join(", ")} (${human.preparation_mode || "manual"})`
    : "no human review";
  return `<div class="meta-line">Release gates: ${escapeHtml(commands.length ? `${commands.length} verification checks` : "no verification checks")} - ${escapeHtml(reviewers.length ? `reviewers ${reviewers.join(", ")}` : "no release reviewers")} - ${escapeHtml(humanLabel)}${policy.allow_ungated ? " - ungated explicitly allowed" : ""}</div>`;
}

function renderGoals(projectId, goals, { compact = false } = {}) {
  if (!goals.length) {
    return `<p class="empty-state">No goals.</p>`;
  }
  if (compact) {
    return `
      <ul class="goal-list compact-record-list">
        ${goals
          .map((goal) => {
            return `
              <li class="record-item compact-record-card">
                <details ${teamDisclosureAttributes(teamDisclosureKey("goal", projectId, goal.id, "detail"))}>
                  <summary class="compact-record-summary">
                    <span class="compact-record-main">
                      <strong>${escapeHtml(goal.description)}</strong>
                      <span>${escapeHtml(goal.id)}</span>
                    </span>
                    <span class="state-pill ${escapeHtml(goal.state)}">${escapeHtml(goal.state)}</span>
                  </summary>
                  <div class="compact-record-body">
                    <div class="meta-line">${escapeHtml(goal.id)}</div>
                    <div class="meta-line">${escapeHtml(goal.description)}</div>
                  </div>
                </details>
              </li>
            `;
          })
          .join("")}
      </ul>
    `;
  }
  return `
    <ul class="goal-list">
      ${goals
        .map((goal) => {
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(goal.description)}</strong>
                <span class="state-pill ${escapeHtml(goal.state)}">${escapeHtml(goal.state)}</span>
              </div>
              <div class="meta-line">${escapeHtml(goal.id)}</div>
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function renderTasks(tasks, { compact = false, structuredPlanning = false } = {}) {
  if (!tasks.length) {
    return `<p class="empty-state">No tasks.</p>`;
  }
  const rows = taskHierarchyRows(tasks);
  return `
    <ul class="task-list${compact ? " compact-record-list" : ""}">
      ${rows.map((row) => renderTaskHierarchyRow(row, { compact, structuredPlanning })).join("")}
    </ul>
  `;
}

function taskHierarchyRows(tasks) {
  const byTaskId = new Map(tasks.map((task) => [text(task.id), task]));
  const children = new Map();
  tasks.forEach((task) => {
    const parentId = text(task.parent_task_id);
    if (!parentId || !byTaskId.has(parentId) || parentId === text(task.id)) {
      return;
    }
    const rows = children.get(parentId) || [];
    rows.push(task);
    children.set(parentId, rows);
  });
  children.forEach((rows) => rows.sort(byPriority));
  const roots = tasks
    .filter((task) => {
      const parentId = text(task.parent_task_id);
      return !parentId || !byTaskId.has(parentId) || parentId === text(task.id);
    })
    .sort(byPriority);
  const rows = [];
  const visited = new Set();
  const visit = (task, depth, path) => {
    const taskId = text(task.id);
    if (visited.has(taskId)) {
      return;
    }
    visited.add(taskId);
    const cycle = path.has(taskId);
    rows.push({ task, depth, hierarchyWarning: cycle ? "cycle" : "" });
    if (cycle) {
      return;
    }
    const nextPath = new Set(path);
    nextPath.add(taskId);
    (children.get(taskId) || []).forEach((child) => visit(child, depth + 1, nextPath));
  };
  roots.forEach((task) => visit(task, 0, new Set()));
  tasks
    .filter((task) => !visited.has(text(task.id)))
    .sort(byPriority)
    .forEach((task) => visit(task, 0, new Set([text(task.id)])));
  return rows;
}

function renderTaskHierarchyRow(
  { task, depth, hierarchyWarning },
  { compact, structuredPlanning = false },
) {
  if (structuredPlanning) {
    return renderStructuredPlanningTask(task, {
      depth,
      hierarchyWarning,
    });
  }
  const invalid = Boolean(hierarchyWarning || (task.invalid_reason_codes || []).length);
  const classes = [
    "task-item",
    compact ? "compact-record-card" : "",
    depth ? "task-hierarchy-child" : "task-hierarchy-root",
    invalid ? "task-hierarchy-invalid" : "",
  ].filter(Boolean).join(" ");
  const body = `
    <div class="record-actions">
      ${editButton("task", task.id)}
      ${taskCancellationButton(task)}
    </div>
    <div class="meta-line">${escapeHtml(task.id)} - priority ${Number(task.priority ?? 0)} - owner ${escapeHtml(taskAssignmentLabel(task))}</div>
    ${renderTaskGraph(task)}
    <div class="meta-line">${escapeHtml(task.description)}</div>
  `;
  if (compact) {
    return `
      <li class="${classes}" style="--task-depth: ${Math.max(0, Number(depth) || 0)}">
        <details ${teamDisclosureAttributes(teamDisclosureKey("task", task.id, "detail"))}>
          <summary class="compact-record-summary">
            <span class="compact-record-main">
              <strong>${escapeHtml(task.title)}</strong>
              <span>${escapeHtml(task.id)} - owner ${escapeHtml(taskAssignmentLabel(task))} - depth ${depth}</span>
            </span>
            <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(taskStateLabel(task))}</span>
          </summary>
          <div class="compact-record-body">${body}</div>
        </details>
      </li>
    `;
  }
  return `
    <li class="${classes}" style="--task-depth: ${Math.max(0, Number(depth) || 0)}">
      <div class="task-line">
        <strong>${escapeHtml(task.title)}</strong>
        <div class="record-actions">
          <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(taskStateLabel(task))}</span>
          ${editButton("task", task.id)}
          ${taskCancellationButton(task)}
        </div>
      </div>
      <div class="meta-line">${escapeHtml(task.id)} - priority ${Number(task.priority ?? 0)} - owner ${escapeHtml(taskAssignmentLabel(task))}</div>
      ${renderTaskGraph(task)}
      <div class="meta-line">${escapeHtml(task.description)}</div>
    </li>
  `;
}

function taskRelativeAge(timestamp) {
  const parsed = Date.parse(text(timestamp));
  if (!Number.isFinite(parsed)) {
    return "age unavailable";
  }
  const seconds = Math.max(0, Math.floor((Date.now() - parsed) / 1000));
  if (seconds < 60) {
    return "just now";
  }
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  }
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    return `${hours} hour${hours === 1 ? "" : "s"}`;
  }
  const days = Math.floor(seconds / 86400);
  return `${days} day${days === 1 ? "" : "s"}`;
}

function taskAssessmentReasonCodePhrase(value) {
  const words = text(value)
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .toLowerCase();
  return words ? `${words[0].toUpperCase()}${words.slice(1)}` : "";
}

function taskAssessmentResolutionReasonLabel(value) {
  const reason = text(value);
  if (!reason) {
    return "";
  }
  if (taskAssessmentResolutionReasonMessages[reason]) {
    return taskAssessmentResolutionReasonMessages[reason];
  }
  if (reason.startsWith("invalid:")) {
    const invalidReasons = reason
      .slice("invalid:".length)
      .split(",")
      .map(taskAssessmentReasonCodePhrase)
      .filter(Boolean);
    return invalidReasons.length
      ? `Lifecycle data needs repair: ${invalidReasons.join("; ")}.`
      : "Lifecycle data needs repair.";
  }
  if (/^[a-z0-9]+(?:[_-][a-z0-9]+)*$/.test(reason)) {
    const fallback = taskAssessmentReasonCodePhrase(reason);
    return fallback ? `${fallback}.` : "";
  }
  return reason;
}

function planningTaskLifecycleReason(task) {
  const assessment = task.assessment || {};
  if (assessment.resolution_reason) {
    return taskAssessmentResolutionReasonLabel(assessment.resolution_reason);
  }
  if (task.waiting_reason) {
    return `Waiting for ${humanizeToken(task.waiting_reason)}.`;
  }
  const blocker = activeBlockersForRecord("task", task.id, task.id)[0];
  if (blocker?.summary) {
    return blocker.summary;
  }
  if (task.dependency_status === "waiting") {
    return "Waiting for prerequisite work.";
  }
  if (task.dependency_status === "blocked") {
    return "A prerequisite is blocked.";
  }
  return terminalTaskStates.has(text(task.state))
    ? `Task is ${taskStateLabel(task).toLowerCase()}.`
    : "Task is ready for its next lifecycle step.";
}

function planningTaskLifecycleStages(task) {
  const assessment = task.assessment || {};
  const dependencyStatus = text(task.dependency_status || "satisfied");
  const stateValue = text(task.state);
  const resolution = text(task.resolution_status || assessment.resolution_status || "unresolved");
  const executionComplete = ["finished", "waiting_for_review", "accepted", "rejected"].includes(stateValue);
  const reviewComplete = ["accepted", "rejected"].includes(stateValue);
  const reviewNotRequired =
    text(assessment.resolution_reason) === "support_task_finished";
  return [
    {
      label: "Created",
      state: "complete",
      detail: task.created_at ? formatTimelineTimestamp(task.created_at) : "Recorded",
    },
    {
      label: "Dependencies",
      state: dependencyStatus === "satisfied" ? "complete" : dependencyStatus === "blocked" ? "blocked" : "current",
      detail: humanizeToken(dependencyStatus),
    },
    {
      label: "Execution",
      state: stateValue === "cancelled" ? "cancelled" : executionComplete ? "complete" : stateValue === "blocked" ? "blocked" : ["in_progress"].includes(stateValue) ? "current" : "pending",
      detail: taskStateLabel(task),
    },
    {
      label: "Review / acceptance",
      state: stateValue === "cancelled" ? "skipped" : reviewNotRequired ? "skipped" : reviewComplete ? (stateValue === "rejected" ? "blocked" : "complete") : stateValue === "waiting_for_review" || stateValue === "finished" ? "current" : "pending",
      detail: stateValue === "cancelled" ? "Skipped — task cancelled" : reviewNotRequired ? "Not required — support task finished" : stateValue === "waiting_for_review" ? humanizeToken(task.waiting_reason || "awaiting review") : reviewComplete ? taskStateLabel(task) : "Not reached",
    },
    {
      label: "Resolved",
      state: resolution === "resolved" ? "complete" : resolution === "invalid" ? "blocked" : "pending",
      detail: humanizeToken(resolution),
    },
  ];
}

function renderPlanningTaskLifecycle(task) {
  return `
    <ol class="planning-task-lifecycle" aria-label="Task lifecycle">
      ${planningTaskLifecycleStages(task).map((stage, index) => `
        <li class="planning-lifecycle-stage planning-lifecycle-${escapeAttr(stage.state)}">
          <span class="planning-lifecycle-marker" aria-hidden="true">${stage.state === "complete" ? "✓" : stage.state === "cancelled" ? "×" : stage.state === "skipped" ? "–" : index + 1}</span>
          <span><strong>${escapeHtml(stage.label)}</strong><small>${escapeHtml(stage.detail)}</small></span>
        </li>
      `).join("")}
    </ol>
  `;
}

function planningTaskChildren(taskId) {
  return values(state.tasks)
    .filter((candidate) => text(candidate.parent_task_id) === text(taskId))
    .sort(byPriority);
}

function renderPlanningTaskTree(taskId, unresolvedIds, path = new Set()) {
  const normalizedTaskId = text(taskId);
  if (!normalizedTaskId || path.has(normalizedTaskId)) {
    return "";
  }
  const nextPath = new Set(path);
  nextPath.add(normalizedTaskId);
  const children = planningTaskChildren(normalizedTaskId);
  if (!children.length) {
    return "";
  }
  const unresolved = new Set((unresolvedIds || []).map(text));
  return `
    <ul class="planning-task-tree">
      ${children.map((child) => `
        <li class="${unresolved.has(text(child.id)) ? "planning-task-tree-unresolved" : ""}">
          <span class="planning-task-tree-row">
            <strong>${escapeHtml(child.title)}</strong>
            <span class="state-pill ${escapeAttr(child.state)}">${escapeHtml(taskStateLabel(child))}</span>
          </span>
          ${renderPlanningTaskTree(child.id, unresolvedIds, nextPath)}
        </li>
      `).join("")}
    </ul>
  `;
}

function renderPlanningTaskHierarchy(task) {
  const assessment = task.assessment || {};
  const childIds = uniqueTextList(task.child_task_ids || assessment.direct_child_ids || []);
  const unresolvedIds = uniqueTextList(assessment.unresolved_descendant_ids || []);
  const parent = task.parent_task_id ? state.tasks?.[task.parent_task_id] : null;
  const dependencies = uniqueTextList(task.dependency_task_ids || [])
    .map((id) => state.tasks?.[id])
    .filter(Boolean);
  const hasTree = childIds.length || unresolvedIds.length;
  return `
    <div class="planning-hierarchy-summary">
      <span><strong>${childIds.length}</strong> direct child${childIds.length === 1 ? "" : "ren"}</span>
      <span><strong>${unresolvedIds.length}</strong> unresolved descendant${unresolvedIds.length === 1 ? "" : "s"}</span>
    </div>
    ${parent ? `<div class="planning-named-relation"><span>Parent</span><strong>${escapeHtml(parent.title)}</strong><span class="state-pill ${escapeAttr(parent.state)}">${escapeHtml(taskStateLabel(parent))}</span></div>` : ""}
    ${dependencies.length ? `<div class="planning-named-relations"><strong>Dependencies</strong>${dependencies.map((dependency) => `<span>${escapeHtml(dependency.title)} <span class="state-pill ${escapeAttr(dependency.state)}">${escapeHtml(taskStateLabel(dependency))}</span></span>`).join("")}</div>` : ""}
    ${
      hasTree
        ? `
          <details class="planning-hierarchy-details" ${planningDisclosureAttributes("hierarchy", task.id)}>
            <summary>Show named hierarchy (${unresolvedIds.length} unresolved)</summary>
            ${renderPlanningTaskTree(task.id, unresolvedIds)}
          </details>
        `
        : `<p class="empty-state">No child tasks.</p>`
    }
  `;
}

function planningContributionState(deliverableId, contribution) {
  const missing = new Set(contribution.missing_source_deliverable_ids || []);
  if (missing.has(deliverableId)) {
    return "missing";
  }
  const deliverable = state.deliverables?.[deliverableId];
  if (!deliverable) {
    return "missing";
  }
  if (text(deliverable.state) === "accepted") {
    return "accepted";
  }
  const reviews = uniqueTextList(deliverable.review_request_ids || [])
    .map((id) => state.review_requests?.[id])
    .filter(Boolean);
  if (reviews.some((review) => text(review.state) === "accepted")) {
    return "reviewed";
  }
  if ((contribution.provided_source_deliverable_ids || []).includes(deliverableId)) {
    return "submitted";
  }
  return "submitted";
}

function renderPlanningContributionChecklist(task) {
  const assessment = task.assessment || {};
  const contribution = assessment.contribution_coverage || {};
  const requiredIds = uniqueTextList(contribution.required_source_deliverable_ids || []);
  const currentDeliverables = values(state.deliverables)
    .filter((deliverable) => text(deliverable.task_id) === text(task.id))
    .filter((deliverable) => !deliverable.superseded_by_deliverable_id);
  const unexpectedIds = uniqueTextList(contribution.unexpected_source_deliverable_ids || []);
  const rows = requiredIds.map((deliverableId) => {
    const deliverable = state.deliverables?.[deliverableId];
    const sourceTask = deliverable ? deliverableTask(deliverable) : null;
    const status = planningContributionState(deliverableId, contribution);
    return `
      <li class="planning-contribution-${escapeAttr(status)}">
        <span class="planning-contribution-marker" aria-hidden="true">${status === "accepted" ? "✓" : status === "missing" ? "!" : "•"}</span>
        <span><strong>${escapeHtml(sourceTask?.title || (deliverable ? deliverableDisplaySubject(deliverable) : "Required child contribution"))}</strong><small>${escapeHtml(humanizeToken(status))}</small></span>
      </li>
    `;
  });
  if (!rows.length && !currentDeliverables.length) {
    return `<p class="empty-state">No contribution composition is required for this task.</p>`;
  }
  return `
    <div class="planning-contribution-status">
      <span class="state-pill ${contribution.complete ? "accepted" : contribution.status === "incomplete" ? "blocked" : "neutral"}">${escapeHtml(humanizeToken(contribution.status || "not applicable"))}</span>
      ${currentDeliverables.length ? `<span>${currentDeliverables.length} submitted task deliverable${currentDeliverables.length === 1 ? "" : "s"}</span>` : ""}
    </div>
    ${rows.length ? `<ul class="planning-contribution-checklist">${rows.join("")}</ul>` : ""}
    ${unexpectedIds.length ? `<div class="task-invalid-warning">${unexpectedIds.length} unexpected source contribution${unexpectedIds.length === 1 ? "" : "s"} require review.</div>` : ""}
  `;
}

function renderPlanningReviewStatus(task) {
  const assessment = task.assessment || {};
  const reviewIds = uniqueTextList(assessment.active_review_request_ids || []);
  const reviews = reviewIds.map((id) => state.review_requests?.[id]).filter(Boolean);
  const acceptanceCount = uniqueTextList(assessment.active_acceptance_ids || []).length;
  if (!reviews.length && !acceptanceCount) {
    return "";
  }
  return `
    <div class="planning-delivery-review">
      ${acceptanceCount ? `<span>${acceptanceCount} direct acceptance${acceptanceCount === 1 ? "" : "s"} pending</span>` : ""}
      ${reviews.map((review) => {
        const reviewer = state.agents?.[review.reviewer_agent_id];
        const dispatch = latestDispatchForReview(review.id);
        const runtime = state.agent_runtimes?.[review.reviewer_agent_id];
        const active = dispatch && ["preparing", "leased"].includes(text(dispatch.status));
        const lease = active && runtime?.wake_leased_until
          ? ` · lease until ${formatTimelineTimestamp(runtime.wake_leased_until)}`
          : "";
        const dispatchStatus = dispatch
          ? ` · dispatch ${humanizeToken(dispatch.status || "unknown")}`
          : " · awaiting dispatch";
        return `<span>Review by ${escapeHtml(reviewer ? agentLabel(reviewer) : "assigned reviewer")} <span class="state-pill ${escapeAttr(review.state)}">${escapeHtml(humanizeToken(review.state))}</span>${escapeHtml(dispatchStatus + lease)}</span>`;
      }).join("")}
    </div>
  `;
}

function renderPlanningTaskBlockers(task) {
  const blockers = activeBlockersForRecord("task", task.id, task.id);
  const inherited = inheritedTaskBlockers(task);
  const rows = [
    ...blockers.map((blocker) => ({
      summary: blocker.summary || blocker.reason || "Task blocker",
      ownerId: blocker.delegate_agent_id || blocker.owner_agent_id,
      next: blockerNextAction(blocker),
    })),
    ...inherited.map((blocker) => ({
      summary: blocker.summary || blocker.reason || "Upstream blocker",
      ownerId: blocker.delegate_agent_id || blocker.owner_agent_id,
      next: blocker.relationship ? `Resolve the ${humanizeToken(blocker.relationship)} blocker upstream.` : "Resolve the upstream blocker.",
    })),
  ];
  if (!rows.length) {
    return "";
  }
  return `
    <ul class="planning-task-blockers">
      ${rows.map((row) => {
        const owner = row.ownerId ? state.agents?.[row.ownerId] : null;
        return `<li><strong>${escapeHtml(row.summary)}</strong><span>Owner: ${escapeHtml(owner ? agentLabel(owner) : "Operator")}</span><span>Next: ${escapeHtml(row.next)}</span></li>`;
      }).join("")}
    </ul>
  `;
}

function renderPlanningInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
}

function planningBriefHeading(line) {
  const markdown = line.match(/^(#{1,6})\s+(.+)$/);
  if (markdown) {
    return markdown[2].trim();
  }
  const framed = line.match(/^={2,}\s*(.+?)\s*={2,}$/);
  if (framed) {
    return framed[1].trim();
  }
  const normalized = line.trim();
  if (
    normalized.length >= 3 &&
    normalized.length <= 90 &&
    /[A-Z]/.test(normalized) &&
    normalized === normalized.toUpperCase() &&
    !/[.!?]$/.test(normalized)
  ) {
    return normalized;
  }
  return "";
}

function renderPlanningBriefMarkdown(value) {
  const lines = text(value).split(/\r?\n/);
  const output = [];
  let paragraph = [];
  let listType = "";
  let listItems = [];
  const flushParagraph = () => {
    if (!paragraph.length) return;
    output.push(`<p>${renderPlanningInlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!listItems.length) return;
    output.push(`<${listType}>${listItems.map((item) => `<li>${renderPlanningInlineMarkdown(item)}</li>`).join("")}</${listType}>`);
    listType = "";
    listItems = [];
  };
  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    const heading = planningBriefHeading(line);
    const unordered = line.match(/^[-*•]\s+(.+)$/);
    const ordered = line.match(/^\d+[.)]\s+(.+)$/);
    if (!line) {
      flushParagraph();
      flushList();
      return;
    }
    if (heading) {
      flushParagraph();
      flushList();
      output.push(`<h5>${renderPlanningInlineMarkdown(heading)}</h5>`);
      return;
    }
    if (unordered || ordered) {
      flushParagraph();
      const nextType = unordered ? "ul" : "ol";
      if (listType && listType !== nextType) {
        flushList();
      }
      listType = nextType;
      listItems.push((unordered || ordered)[1]);
      return;
    }
    flushList();
    paragraph.push(line);
  });
  flushParagraph();
  flushList();
  return output.join("");
}

function renderPlanningTaskBrief(task) {
  const brief = text(task.description);
  if (!brief) {
    return `<p class="empty-state">No task brief.</p>`;
  }
  const isLong = brief.length > 320 || brief.split(/\r?\n/).length > 6;
  const body = `<div class="planning-task-brief-content">${renderPlanningBriefMarkdown(brief)}</div>`;
  if (!isLong) {
    return body;
  }
  return `
    <details class="planning-task-brief-details" ${planningDisclosureAttributes("brief", task.id)}>
      <summary>Show full brief</summary>
      ${body}
    </details>
  `;
}

function renderPlanningTaskTechnicalDetails(task) {
  const assessment = task.assessment || {};
  const worktreeContexts = taskGitWorkContexts(task);
  const blockerIds = [
    ...activeBlockersForRecord("task", task.id, task.id).map((blocker) => blocker.id),
    ...inheritedTaskBlockers(task).map((blocker) => blocker.blocker_id),
  ];
  return `
    ${renderTechnicalDetails([
      ["Task ID", task.id],
      ["Project ID", task.project_id],
      ["Assigned to", taskAssignmentLabel(task)],
      ["Owner ID", task.owner_agent_id],
      ["Assignment role", task.assignment_role_id],
      ["Assignment wait reason", task.assignment_wait_reason],
      ["Case ID", task.case_id],
      ["Parent task ID", task.parent_task_id],
      ["Originating task ID", task.originating_task_id],
      ["Dependency task IDs", task.dependency_task_ids || []],
      ["Integration boundary", task.integration_boundary ? "Yes" : null],
      ["Requires Git provenance", task.deliverable_contract?.require_git_provenance ? "Yes" : null],
      ["Required contribution task IDs", task.deliverable_contract?.required_contribution_task_ids || []],
      ["Child task IDs", task.child_task_ids || assessment.direct_child_ids || []],
      ["Unresolved task IDs", assessment.unresolved_descendant_ids || []],
      ["Deliverable IDs", task.deliverable_ids || []],
      ["Acceptance IDs", assessment.active_acceptance_ids || []],
      ["Review IDs", assessment.active_review_request_ids || []],
      ["Blocker IDs", blockerIds],
    ], {
      summary: "Identifiers and lifecycle references",
      disclosureKey: planningDisclosureKey("technical", task.id),
    })}
    ${worktreeContexts.length ? `<details class="technical-details planning-worktree-details" ${planningDisclosureAttributes("worktree", task.id)}><summary>Worktree mapping</summary>${renderTaskWorktreeMapping(task)}</details>` : ""}
  `;
}

function cancelledTaskDependencyRepairEvidence(predecessorTaskId, successorTaskId) {
  return values(state.inbox)
    .filter(
      (item) =>
        text(item.payload?.kind) === "task_dependency_graph_repair_audit" &&
        text(item.payload?.predecessor_task_id) === text(predecessorTaskId) &&
        text(item.payload?.successor_task_id) === text(successorTaskId),
    )
    .sort((left, right) =>
      text(right.created_at).localeCompare(text(left.created_at)),
    )[0] || null;
}

function cancelledTaskDependencyRepairAffectedIds(predecessorTaskId) {
  const normalizedPredecessorId = text(predecessorTaskId);
  return values(state.tasks)
    .filter((task) => !terminalTaskStates.has(text(task.state)))
    .filter(
      (task) =>
        (task.dependency_task_ids || []).some(
          (dependencyId) => text(dependencyId) === normalizedPredecessorId,
        ) ||
        (Boolean(task.integration_boundary) &&
          (task.deliverable_contract?.required_contribution_task_ids || []).some(
            (dependencyId) => text(dependencyId) === normalizedPredecessorId,
          )),
    )
    .map((task) => text(task.id))
    .filter(Boolean)
    .sort();
}

function cancelledTaskDependencyRepairAction(task, cancellation) {
  const successorTaskId = text(cancellation?.successor_task_id);
  if (
    !task ||
    text(task.state) !== "cancelled" ||
    !successorTaskId ||
    Boolean(cancellation?.rebind_dependents)
  ) {
    return "";
  }
  const affectedIds = cancelledTaskDependencyRepairAffectedIds(task.id);
  const evidence = cancelledTaskDependencyRepairEvidence(task.id, successorTaskId);
  if (!affectedIds.length && !evidence) {
    return "";
  }
  const repairedIds = evidence?.payload?.rebound_dependent_task_ids || [];
  return `
    <div class="record-actions task-dependency-repair-action">
      ${
        evidence
          ? `<span class="meta-line">Last audited repair: ${escapeHtml(
              repairedIds.length
                ? `${repairedIds.length} dependent task${repairedIds.length === 1 ? "" : "s"}`
                : "no graph changes",
            )}${evidence.payload?.repaired_at ? ` · ${escapeHtml(formatTimelineTimestamp(evidence.payload.repaired_at))}` : ""}</span>`
          : `<span class="meta-line">${affectedIds.length} active dependent task${affectedIds.length === 1 ? "" : "s"} still reference this predecessor.</span>`
      }
      <button
        type="button"
        class="record-action"
        data-repair-cancelled-task-dependencies="${escapeAttr(task.id)}"
        data-successor-task-id="${escapeAttr(successorTaskId)}"
        ${lockedAttrs("repair cancelled-task dependencies")}
      >${evidence && !affectedIds.length ? "Verify dependency repair" : "Repair stranded dependents"}</button>
    </div>
  `;
}

function renderPlanningTaskCancellation(cancellation, task) {
  const successor = cancellation.successor_task_id
    ? state.tasks?.[cancellation.successor_task_id]
    : null;
  const actor = cancellation.actor_id ? state.agents?.[cancellation.actor_id] : null;
  const details = [
    cancellation.reason,
    cancellation.reason_code ? humanizeToken(cancellation.reason_code) : "",
    successor ? `Successor: ${successor.title}` : "",
    cancellation.rebind_dependents
      ? `${(cancellation.rebound_dependent_task_ids || []).length} active dependencies rebound`
      : "",
    (cancellation.rebound_integration_boundary_task_ids || []).length
      ? `${cancellation.rebound_integration_boundary_task_ids.length} integration preparations re-armed`
      : "",
    (cancellation.invalidated_git_work_context_ids || []).length
      ? `${cancellation.invalidated_git_work_context_ids.length} stale Git contexts abandoned`
      : "",
    actor ? `By ${agentLabel(actor)}` : cancellation.actor_kind ? `By ${humanizeToken(cancellation.actor_kind)}` : "",
    cancellation.cancelled_at ? `Cancelled ${formatTimelineTimestamp(cancellation.cancelled_at)}` : "",
  ].filter(Boolean);
  return `<div class="task-cancellation"><strong>Cancelled:</strong> ${escapeHtml(details.join(" · "))}${cancelledTaskDependencyRepairAction(task, cancellation)}</div>`;
}

function renderStructuredPlanningTask(task, { depth = 0, hierarchyWarning = "" } = {}) {
  const ownerName = taskAssignmentLabel(task);
  const assessment = task.assessment || {};
  const reason = planningTaskLifecycleReason(task);
  const readiness = text(task.readiness || assessment.readiness || "unknown");
  const resolution = text(task.resolution_status || assessment.resolution_status || "unknown");
  const stateSince = task.state_entered_at || task.updated_at || task.created_at;
  const invalidCodes = task.invalid_reason_codes || assessment.invalid_reason_codes || [];
  const classes = [
    "task-item",
    "compact-record-card",
    "planning-task-card",
    depth ? "task-hierarchy-child" : "task-hierarchy-root",
    hierarchyWarning || invalidCodes.length ? "task-hierarchy-invalid" : "",
  ].filter(Boolean).join(" ");
  return `
    <li class="${classes}" style="--task-depth: ${Math.max(0, Number(depth) || 0)}" data-planning-task-id="${escapeAttr(task.id)}">
      <details class="planning-task-details" ${planningDisclosureAttributes("task", task.id)}>
        <summary class="compact-record-summary planning-task-summary">
          <span class="compact-record-main">
            <strong>${escapeHtml(task.title)}</strong>
            <span>${escapeHtml(ownerName)} · state for ${escapeHtml(taskRelativeAge(stateSince))}</span>
            <span class="planning-task-reason-preview">${escapeHtml(readablePreview(reason, 140))}</span>
          </span>
          <span class="planning-task-summary-meta">
            <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(taskStateLabel(task))}</span>
            <span class="state-pill ${escapeAttr(taskReadinessPill(readiness))}">${escapeHtml(humanizeToken(readiness))}</span>
          </span>
        </summary>
        <div class="compact-record-body planning-task-body">
          <section class="planning-task-section">
            <div class="planning-task-section-heading"><h4>Overview</h4><div class="record-actions">${editButton("task", task.id)}${taskCancellationButton(task)}</div></div>
            <div class="planning-task-overview">
              <div><span>Owner</span><strong>${escapeHtml(ownerName)}</strong></div>
              <div><span>State</span><strong>${escapeHtml(taskStateLabel(task))}</strong></div>
              <div><span>State age</span><strong>${escapeHtml(taskRelativeAge(stateSince))}</strong></div>
              <div><span>Priority</span><strong>${Number(task.priority ?? 0)}</strong></div>
            </div>
            <p class="planning-lifecycle-reason"><strong>Lifecycle reason:</strong> ${escapeHtml(reason)}</p>
            ${renderPlanningExecutionStatus(task)}
            ${renderPlanningTaskBlockers(task)}
          </section>
          <section class="planning-task-section">
            <h4>Lifecycle</h4>
            ${renderPlanningTaskLifecycle(task)}
            ${task.cancellation ? renderPlanningTaskCancellation(task.cancellation, task) : ""}
            ${invalidCodes.length ? `<div class="task-invalid-warning"><strong>Lifecycle repair required:</strong> ${escapeHtml(invalidCodes.map(humanizeToken).join(", "))} ${editButton("task", task.id, "Repair task")}</div>` : ""}
          </section>
          <section class="planning-task-section">
            <h4>Hierarchy</h4>
            ${renderPlanningTaskHierarchy(task)}
          </section>
          <section class="planning-task-section">
            <h4>Delivery / contribution</h4>
            ${renderPlanningContributionChecklist(task)}
            ${renderPlanningReviewStatus(task)}
          </section>
          <section class="planning-task-section planning-task-brief-section">
            <h4>Task brief</h4>
            ${renderPlanningTaskBrief(task)}
          </section>
          <section class="planning-task-section planning-task-technical-section">
            <h4>Technical details</h4>
            ${renderPlanningTaskTechnicalDetails(task)}
          </section>
        </div>
      </details>
    </li>
  `;
}

function renderTaskGraph(task) {
  const assessment = task.assessment || {};
  const lines = [];
  if (task.case_id) {
    const item = state.cases[task.case_id];
    lines.push(`case ${item ? item.title : task.case_id}`);
  }
  if (task.parent_task_id) {
    lines.push(`parent ${task.parent_task_id}`);
  }
  if (task.originating_task_id) {
    lines.push(`originating task ${task.originating_task_id}`);
  }
  if ((task.dependency_task_ids || []).length) {
    lines.push(`depends on ${task.dependency_task_ids.join(", ")}`);
  }
  const dependencyWaitIds = taskBoardDependencyWaitIds(task);
  if (taskBoardWaitsOnDependencies(task) && dependencyWaitIds.length) {
    lines.push(`waiting on dependencies ${dependencyWaitIds.join(", ")}`);
  }
  activeBlockersForRecord("task", task.id, task.id).forEach((blocker) => {
    lines.push(blockerRoutingLabel(blocker));
  });
  const childIds = task.child_task_ids || assessment.direct_child_ids || [];
  if (childIds.length) {
    lines.push(`children ${childIds.join(", ")}`);
  }
  const unresolvedIds = assessment.unresolved_descendant_ids || [];
  if (unresolvedIds.length) {
    lines.push(`unresolved descendants ${unresolvedIds.join(", ")}`);
  }
  const counts = task.subtask_counts || {};
  const subtaskLine = counts.total
    ? `subtasks ${counts.accepted || 0} accepted, ${counts.finished || 0} finished, ${counts.open || 0} open, ${counts.blocked || 0} blocked`
    : "";
  const graphLines = lines.length
    ? `<div class="meta-line">${escapeHtml(lines.join(" - "))}</div>`
    : "";
  const countLine = subtaskLine ? `<div class="meta-line">${escapeHtml(subtaskLine)}</div>` : "";
  const readiness = text(task.readiness || assessment.readiness || "unknown");
  const resolution = text(task.resolution_status || assessment.resolution_status || "unknown");
  const integrationReady = Boolean(task.integration_ready ?? assessment.integration_ready);
  const acceptanceIds = assessment.active_acceptance_ids || [];
  const reviewIds = assessment.active_review_request_ids || [];
  const contribution = assessment.contribution_coverage || {};
  const coverageBits = [
    text(contribution.status) ? `coverage ${text(contribution.status)}` : "",
    (contribution.missing_source_deliverable_ids || []).length
      ? `missing ${contribution.missing_source_deliverable_ids.join(", ")}`
      : "",
    (contribution.unexpected_source_deliverable_ids || []).length
      ? `unexpected ${contribution.unexpected_source_deliverable_ids.join(", ")}`
      : "",
  ].filter(Boolean);
  const invalidCodes = task.invalid_reason_codes || assessment.invalid_reason_codes || [];
  const cancellation = task.cancellation || null;
  return `
    <div class="task-lifecycle-summary">
      <span>Readiness <span class="state-pill ${escapeAttr(taskReadinessPill(readiness))}">${escapeHtml(readiness)}</span></span>
      <span>Resolution <span class="state-pill ${escapeAttr(taskResolutionPill(resolution))}">${escapeHtml(resolution)}</span></span>
      <span>Integration <span class="state-pill ${integrationReady ? "accepted" : "neutral"}">${integrationReady ? "ready" : "not ready"}</span></span>
      <span>Dependencies <span class="state-pill ${escapeHtml(task.dependency_status || "satisfied")}">${escapeHtml(task.dependency_status || "satisfied")}</span></span>
    </div>
    ${assessment.resolution_reason ? `<div class="meta-line">Lifecycle reason: ${escapeHtml(taskAssessmentResolutionReasonLabel(assessment.resolution_reason))}</div>` : ""}
    ${graphLines}${countLine}
    ${acceptanceIds.length ? `<div class="meta-line">Direct acceptance: ${escapeHtml(acceptanceIds.join(", "))}</div>` : ""}
    ${reviewIds.length ? `<div class="meta-line">Formal review: ${escapeHtml(reviewIds.join(", "))}</div>` : ""}
    ${coverageBits.length ? `<div class="meta-line">Contribution composition: ${escapeHtml(coverageBits.join(" - "))}</div>` : ""}
    ${cancellation ? renderTaskCancellation(cancellation, task) : ""}
    ${renderTaskBlockerDetails(task)}
    ${renderTaskWorktreeMapping(task)}
    ${
      invalidCodes.length
        ? `<div class="task-invalid-warning"><strong>Legacy lifecycle repair required:</strong> ${escapeHtml(invalidCodes.join(", "))} ${editButton("task", task.id, "Repair task")}</div>`
        : ""
    }
  `;
}

function taskReadinessPill(readiness) {
  if (readiness === "actionable") return "accepted";
  if (readiness === "blocked" || readiness === "invalid") return "blocked";
  if (readiness === "resolved") return "neutral";
  return "info";
}

function taskResolutionPill(resolution) {
  if (resolution === "resolved") return "accepted";
  if (resolution === "invalid") return "blocked";
  return "info";
}

function renderTaskCancellation(cancellation, task) {
  const parts = [
    cancellation.reason_code ? `reason ${cancellation.reason_code}` : "",
    cancellation.reason,
    cancellation.actor_kind && cancellation.actor_id
      ? `by ${cancellation.actor_kind} ${cancellation.actor_id}`
      : "",
    cancellation.successor_task_id ? `successor ${cancellation.successor_task_id}` : "",
    cancellation.rebind_dependents
      ? `rebound dependents ${(cancellation.rebound_dependent_task_ids || []).join(", ") || "none"}`
      : "",
    (cancellation.rebound_integration_boundary_task_ids || []).length
      ? `re-armed integration boundaries ${cancellation.rebound_integration_boundary_task_ids.join(", ")}`
      : "",
    (cancellation.invalidated_git_work_context_ids || []).length
      ? `abandoned stale Git contexts ${cancellation.invalidated_git_work_context_ids.join(", ")}`
      : "",
    cancellation.cancelled_at ? `at ${cancellation.cancelled_at}` : "",
  ].filter(Boolean);
  return `<div class="task-cancellation"><strong>Cancelled:</strong> ${escapeHtml(parts.join(" - "))}${cancelledTaskDependencyRepairAction(task, cancellation)}</div>`;
}

function renderTaskBlockerDetails(task) {
  const blockers = activeBlockersForRecord("task", task.id, task.id);
  const inheritedBlockers = inheritedTaskBlockers(task);
  if (!blockers.length && !inheritedBlockers.length) {
    return "";
  }
  return `
    <div class="task-blocker-details">
      ${blockers.map((blocker) => renderTaskBlockerDetail(blocker)).join("")}
      ${inheritedBlockers.map((blocker) => renderInheritedTaskBlockerDetail(blocker)).join("")}
    </div>
  `;
}

function renderInheritedTaskBlockerDetail(blocker) {
  const route = [
    blocker.owner_agent_id ? `owner ${blocker.owner_agent_id}` : "no visible agent owner",
    blocker.delegate_agent_id ? `delegate ${blocker.delegate_agent_id}` : "",
    blocker.route ? `route ${blocker.route}` : "",
    `escalation level ${Number(blocker.escalation_depth || 0)}`,
  ].filter(Boolean).join(" - ");
  const timing = [
    blocker.created_at ? `blocked since ${blocker.created_at}` : "",
    blocker.follow_up_at ? `follow up ${blocker.follow_up_at}` : "",
    blocker.last_progress_at ? `last progress ${blocker.last_progress_at}` : "",
  ].filter(Boolean).join(" - ");
  return `
    <section class="task-blocker-card task-inherited-blocker-card">
      <div><strong>Upstream ${escapeHtml(blocker.relationship || "task")} ${escapeHtml(blocker.upstream_task_id || "unknown")}</strong></div>
      <div><strong>Blocker ${escapeHtml(blocker.blocker_id)}</strong> <span class="state-pill blocked">${escapeHtml(blocker.reason_code || "unspecified")}</span></div>
      <div>${escapeHtml(blocker.reason || blocker.summary || "No reason supplied")}</div>
      ${blocker.details ? `<div>${escapeHtml(blocker.details)}</div>` : ""}
      <div class="meta-line">Source ${escapeHtml(`${blocker.source_kind || "unknown"} ${blocker.source_id || ""}`.trim())} - ${escapeHtml(route)}</div>
      ${timing ? `<div class="meta-line">${escapeHtml(timing)}</div>` : ""}
    </section>
  `;
}

function renderTaskBlockerDetail(blocker) {
  const latestEvent = latestBlockerEvent(blocker.id);
  const source = `${text(blocker.source_kind) || "unknown"} ${text(blocker.source_id) || ""}`.trim();
  const accountable = [
    blocker.owner_agent_id ? `owner ${blocker.owner_agent_id}` : "no agent owner",
    blocker.delegate_agent_id ? `delegate ${blocker.delegate_agent_id}` : "",
  ].filter(Boolean).join(" - ");
  const timing = [
    blocker.created_at ? `blocked since ${blocker.created_at}` : "",
    blocker.follow_up_at ? `follow up ${blocker.follow_up_at}` : "",
    blocker.last_progress_at ? `last progress ${blocker.last_progress_at}` : "",
  ].filter(Boolean).join(" - ");
  return `
    <section class="task-blocker-card">
      <div><strong>Blocker ${escapeHtml(blocker.id)}</strong> <span class="state-pill blocked">${escapeHtml(blocker.reason_code || "unspecified")}</span></div>
      <div>${escapeHtml(blocker.summary || "No summary")}</div>
      ${blocker.details ? `<div>${escapeHtml(blocker.details)}</div>` : ""}
      <div class="meta-line">Source ${escapeHtml(source)} - ${escapeHtml(accountable)} - route ${escapeHtml(blocker.route || "unknown")} - escalation level ${Number(blocker.escalation_depth || 0)}</div>
      ${timing ? `<div class="meta-line">${escapeHtml(timing)}</div>` : ""}
      ${
        latestEvent
          ? `<div class="meta-line">Latest event ${escapeHtml(latestEvent.event_type)}${latestEvent.note ? `: ${escapeHtml(latestEvent.note)}` : ""}</div>`
          : ""
      }
      <div class="meta-line">Next action: ${escapeHtml(blockerNextAction(blocker))}</div>
      ${renderTerminalBlockerResolveAction(blocker)}
    </section>
  `;
}

function latestBlockerEvent(blockerId) {
  return values(state.blocker_events)
    .filter((event) => text(event.blocker_id) === text(blockerId))
    .sort((left, right) =>
      String(right.created_at || "").localeCompare(String(left.created_at || "")) ||
      String(right.id || "").localeCompare(String(left.id || "")),
    )[0] || null;
}

function blockerNextAction(blocker) {
  if (blocker.delegate_agent_id) {
    return `delegate ${blocker.delegate_agent_id} reports progress; owner ${blocker.owner_agent_id || "unassigned"} remains accountable`;
  }
  if (blocker.follow_up_at) {
    return `owner ${blocker.owner_agent_id || blocker.route || "operator"} follows up at ${blocker.follow_up_at}`;
  }
  if (["governance", "operator"].includes(text(blocker.route))) {
    return `${humanizeToken(blocker.route)} verifies the blocking condition and resolves or escalates it`;
  }
  return `owner ${blocker.owner_agent_id || "unassigned"} records progress, delegates, resolves, or escalates`;
}

function taskGitWorkContexts(task) {
  const rows = [
    ...(task.git_work_contexts || []),
    ...values(state.git_work_contexts).filter((context) => text(context.task_id) === text(task.id)),
  ];
  const unique = new Map();
  rows.forEach((row) => {
    const id = text(row.id || row.branch_context_id);
    if (id) unique.set(id, row);
  });
  return [...unique.values()].sort(byId);
}

function renderTaskWorktreeMapping(task) {
  const contexts = taskGitWorkContexts(task);
  const cleanupRows = values(state.worktree_cleanup_candidates)
    .filter((candidate) => text(candidate.task_id) === text(task.id))
    .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
  if (!contexts.length && !cleanupRows.length) {
    return "";
  }
  const project = state.projects[task.project_id] || {};
  const repository = project.git?.managed_repo_id || "not configured";
  const rows = contexts.map((context) => {
    const dispatch = state.dispatches?.[context.dispatch_id] || null;
    return `
      <tr>
        <td>${escapeHtml(task.id)}</td>
        <td>${escapeHtml(task.project_id)}</td>
        <td>${escapeHtml(repository)}</td>
        <td><code>${escapeHtml(context.worktree_path || "control checkout")}</code></td>
        <td><code>${escapeHtml(context.agent_branch || context.branch || project.git?.base_branch || "")}</code></td>
        <td>${escapeHtml(context.id || context.branch_context_id || "")}${dispatch ? ` - ${escapeHtml(dispatch.status)}` : ""}</td>
      </tr>
    `;
  }).join("");
  return `
    <div class="task-worktree-mapping">
      <div class="meta-line">Repository base ${escapeHtml(project.git?.base_branch || "unknown")} is the control checkout; edit only in the task worktree below.</div>
      ${
        rows
          ? `<div class="task-worktree-scroll"><table><thead><tr><th>Task</th><th>Project</th><th>Repository</th><th>Worktree</th><th>Branch</th><th>Context / dispatch</th></tr></thead><tbody>${rows}</tbody></table></div>`
          : ""
      }
      ${cleanupRows.map(renderTaskCleanupCandidate).join("")}
    </div>
  `;
}

function renderTaskCleanupCandidate(candidate) {
  const operatorAction = text(candidate.state) === "operator_action_required";
  const recovery = operatorAction || text(candidate.state) === "recovery_required";
  const ignoredCount = Number(candidate.ignored_file_count || 0);
  const ignoredSample = (candidate.ignored_file_inventory || []).slice(0, 5);
  const details = [
    `cleanup ${candidate.state}`,
    candidate.last_safety_check ? `check ${candidate.last_safety_check}` : "",
    candidate.last_error,
    Number(candidate.automatic_retry_count || 0)
      ? `${Number(candidate.automatic_retry_count)} consecutive automatic attempts`
      : "",
    candidate.operator_action_reason
      ? `operator action ${humanizeToken(candidate.operator_action_reason)}`
      : "",
    candidate.operator_action_required_at
      ? `halted ${candidate.operator_action_required_at}`
      : "",
    candidate.last_operator_retry_by
      ? `last requeued by ${candidate.last_operator_retry_by}${candidate.last_operator_retry_reason ? `: ${candidate.last_operator_retry_reason}` : ""}`
      : "",
    candidate.worktree_path,
    candidate.branch,
    ignoredCount
      ? `${ignoredCount} ignored file${ignoredCount === 1 ? "" : "s"}${candidate.ignored_file_inventory_truncated ? " (sampled)" : ""}`
      : "",
    candidate.ignored_file_inventory_sha256
      ? `inventory sha256 ${candidate.ignored_file_inventory_sha256}`
      : "",
    ignoredSample.length ? `sample ${ignoredSample.join(", ")}` : "",
    Number(candidate.reclaimed_bytes || 0) ? `reclaimed ${candidate.reclaimed_bytes} bytes` : "",
  ].filter(Boolean).join(" - ");
  return `
    <div class="${recovery ? "task-cleanup-recovery" : "meta-line"}">
      <div>${escapeHtml(details)}</div>
      ${
        operatorAction
          ? `<div class="record-actions">
              <button
                type="button"
                class="record-action"
                data-worktree-cleanup-retry="${escapeAttr(candidate.id)}"
                ${lockedAttrs("retry worktree cleanup")}
              >Retry cleanup</button>
            </div>`
          : ""
      }
    </div>
  `;
}

async function retryWorktreeCleanup(button, reason) {
  const candidateId = text(button?.dataset?.worktreeCleanupRetry);
  if (!candidateId) {
    setStatus("Worktree cleanup retry context is incomplete.", "error");
    return;
  }
  button.disabled = true;
  setStatus("Requeueing worktree cleanup.", "loading");
  try {
    const response = await teamFetch(
      `${API_BASE}/worktree-cleanup-candidates/${encodeURIComponent(candidateId)}/retry`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify({
          reason,
          expected_revision: state.revision,
        }),
      },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const result = await response.json();
    await applyMutationResultPayload(result);
    setStatus(`Cleanup ${candidateId} was requeued for a full safety pass.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Worktree cleanup retry failed.", "error");
  } finally {
    button.disabled = false;
  }
}

async function repairCancelledTaskDependencies(button, reason) {
  const predecessorTaskId = text(
    button?.dataset?.repairCancelledTaskDependencies,
  );
  const successorTaskId = text(button?.dataset?.successorTaskId);
  const predecessor = state.tasks?.[predecessorTaskId] || null;
  if (
    !predecessor ||
    text(predecessor.state) !== "cancelled" ||
    !successorTaskId ||
    text(predecessor.cancellation?.successor_task_id) !== successorTaskId
  ) {
    setStatus("Cancelled-task dependency repair context is incomplete.", "error");
    return;
  }
  const currentAffectedIds = cancelledTaskDependencyRepairAffectedIds(
    predecessorTaskId,
  );
  const priorEvidence = cancelledTaskDependencyRepairEvidence(
    predecessorTaskId,
    successorTaskId,
  );
  const expectedDependentTaskIds = currentAffectedIds.length
    ? currentAffectedIds
    : uniqueTextList(
        priorEvidence?.payload?.expected_dependent_task_ids || [],
      ).sort();
  button.disabled = true;
  setStatus("Validating and repairing the frozen dependent-task set.", "loading");
  try {
    const response = await teamFetch(
      `${API_BASE}/tasks/${encodeURIComponent(predecessorTaskId)}/repair-cancelled-dependencies`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify({
          successor_task_id: successorTaskId,
          expected_dependent_task_ids: expectedDependentTaskIds,
          reason,
          expected_revision: state.revision,
        }),
      },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const result = await response.json();
    const repair = result.task_dependency_repair || {};
    await applyMutationResultPayload(result);
    const changedCount = (repair.rebound_dependent_task_ids || []).length;
    setStatus(
      repair.audit_evidence_reused
        ? "The prior dependency repair was verified; no graph history was rewritten."
        : `Dependency repair recorded; ${changedCount} dependent task${changedCount === 1 ? "" : "s"} rebound.`,
      "ok",
    );
  } catch (error) {
    setStatus(
      error instanceof Error ? error.message : "Cancelled-task dependency repair failed.",
      "error",
    );
  } finally {
    button.disabled = false;
  }
}

function readablePreview(value, limit = 220) {
  const normalized = text(value).replace(/\s+/g, " ");
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, Math.max(1, limit - 1)).trimEnd()}…`;
}

function deliverableTask(deliverable) {
  return state.tasks?.[text(deliverable?.task_id)] || null;
}

function deliverableDisplaySubject(deliverable) {
  const task = deliverableTask(deliverable);
  if (task?.title) {
    return task.title;
  }
  const firstLine = text(deliverable?.summary)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);
  return readablePreview(firstLine || "Deliverable outcome", 90);
}

function deliverableProducerLabel(deliverable) {
  const producer = state.agents?.[text(deliverable?.producer_agent_id)];
  return producer ? agentLabel(producer) : text(deliverable?.producer_agent_id) || "Unassigned";
}

function renderRecordDate(timestamp, prefix = "") {
  if (!timestamp) {
    return "Date unavailable";
  }
  const label = formatTimelineTimestamp(timestamp);
  return `${prefix ? `${prefix} ` : ""}<time datetime="${escapeAttr(timestamp)}">${escapeHtml(label)}</time>`;
}

function renderIdentifierCopy(value, { label = "Copy ID" } = {}) {
  const normalized = text(value);
  if (!normalized) {
    return "";
  }
  return `
    <span class="technical-identifier">
      <code>${escapeHtml(normalized)}</code>
      <button
        type="button"
        class="copy-identifier-button"
        data-copy-identifier="${escapeAttr(normalized)}"
        data-copy-label="${escapeAttr(label)}"
        aria-label="${escapeAttr(`${label}: ${normalized}`)}"
      >${escapeHtml(label)}</button>
    </span>
  `;
}

function renderTechnicalDetails(entries, { summary = "Technical details", disclosureKey = "" } = {}) {
  const rows = entries
    .map(([label, value, copyLabel]) => {
      const values = Array.isArray(value) ? uniqueTextList(value) : [text(value)].filter(Boolean);
      if (!values.length) {
        return "";
      }
      return `
        <div>
          <dt>${escapeHtml(label)}</dt>
          <dd>${values.map((item) => renderIdentifierCopy(item, { label: copyLabel || (label.toLowerCase().includes("id") ? "Copy ID" : "Copy") })).join("")}</dd>
        </div>
      `;
    })
    .filter(Boolean);
  if (!rows.length) {
    return "";
  }
  return `
    <details class="technical-details" ${teamDisclosureAttributes(disclosureKey)}>
      <summary>${escapeHtml(summary)}</summary>
      <dl class="technical-details-list">${rows.join("")}</dl>
    </details>
  `;
}

async function copyTechnicalIdentifier(button) {
  const value = text(button.dataset.copyIdentifier);
  if (!value) {
    return;
  }
  const originalLabel = text(button.dataset.copyLabel) || "Copy";
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      const field = document.createElement("textarea");
      field.value = value;
      field.setAttribute("readonly", "");
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.appendChild(field);
      field.select();
      if (!document.execCommand("copy")) {
        throw new Error("Clipboard copy is unavailable.");
      }
      field.remove();
    }
    button.textContent = "Copied";
    button.dataset.copyState = "copied";
    window.setTimeout(() => {
      button.textContent = originalLabel;
      delete button.dataset.copyState;
    }, 1600);
  } catch {
    setStatus(`Could not copy ${value}.`, "error");
  }
}

function renderDeliverableOutcomes(deliverable) {
  const outcomes = deliverable.outcomes || [];
  if (!outcomes.length && !deliverable.completion) {
    return "";
  }
  const outcomeRows = outcomes.map((outcome) => {
    const name =
      outcome.definition_snapshot?.name ||
      values(state.outcome_kind_definitions).find((definition) => definition.kind === outcome.kind)?.name ||
      humanizeToken(outcome.kind);
    return `<li><strong>${escapeHtml(name)}</strong>${(outcome.reference_ids || []).length ? ` · ${outcome.reference_ids.length} reference${outcome.reference_ids.length === 1 ? "" : "s"}` : ""}${(outcome.evidence_refs || []).length ? ` · ${outcome.evidence_refs.length} evidence item${outcome.evidence_refs.length === 1 ? "" : "s"}` : ""}</li>`;
  });
  if (deliverable.completion) {
    outcomeRows.push(`<li><strong>${escapeHtml(humanizeToken(deliverable.completion.kind || "completion"))}</strong></li>`);
  }
  return `<ul class="deliverable-outcome-list">${outcomeRows.join("")}</ul>`;
}

function renderDeliverableEvidence(deliverable) {
  const refs = new Set(deliverable.artifact_refs || []);
  (deliverable.outcomes || []).forEach((outcome) => {
    (outcome.evidence_refs || []).forEach((ref) => refs.add(ref));
  });
  if (!refs.size) {
    return `<p class="empty-state">No evidence or artifacts recorded.</p>`;
  }
  return `<ul class="deliverable-evidence-list">${[...refs].map((ref) => `<li><code>${escapeHtml(ref)}</code></li>`).join("")}</ul>`;
}

function renderDeliverableAcceptanceVerification(deliverable) {
  const verification = deliverable.verification;
  if (!verification) {
    return "";
  }
  const status = text(verification.status || "unknown");
  const commands = verification.command_results || [];
  const waiver = verification.waiver;
  return `
    <section class="deliverable-detail-group deliverable-acceptance-verification">
      <h4>Deliverable verification (diagnostic)</h4>
      <div class="delivery-detail-grid">
        <div><div class="delivery-detail-label">Status</div><div class="delivery-detail-value"><span class="state-pill ${escapeAttr(status)}">${escapeHtml(humanizeToken(status))}</span></div></div>
        <div><div class="delivery-detail-label">Provenance</div><div class="delivery-detail-value">${escapeHtml(humanizeToken(verification.provenance || "agent_reported"))}</div></div>
        ${verification.run_id ? `<div><div class="delivery-detail-label">Run</div><div class="delivery-detail-value">${renderIdentifierCopy(verification.run_id, { label: "Copy ID" })}</div></div>` : ""}
        ${verification.candidate_commit_sha ? `<div><div class="delivery-detail-label">Candidate</div><div class="delivery-detail-value"><code>${escapeHtml(verification.candidate_commit_sha)}</code></div></div>` : ""}
      </div>
      ${verification.summary ? `<p class="meta-line">${escapeHtml(verification.summary)}</p>` : ""}
      ${commands.length ? `<ul>${commands.map((command) => `<li><strong>${escapeHtml(command.name)}</strong> - ${escapeHtml(humanizeToken(command.state || "pending"))}${command.failure_kind ? ` (${escapeHtml(humanizeToken(command.failure_kind))})` : ""}</li>`).join("")}</ul>` : ""}
      ${(verification.evidence_refs || []).length ? `<p class="meta-line"><strong>Evidence:</strong> ${(verification.evidence_refs || []).map((ref) => `<code>${escapeHtml(ref)}</code>`).join(" ")}</p>` : ""}
      ${waiver ? `<p class="meta-line"><strong>Waived by ${escapeHtml(waiver.actor_id)}:</strong> ${escapeHtml(waiver.reason)}</p>` : ""}
    </section>
  `;
}

function renderDeliverableTechnicalDetails(deliverable) {
  const gitRefs = deliverable.git_refs || {};
  const initialRelease = deliverable.initial_release_disposition || {};
  return renderTechnicalDetails([
    ["Deliverable ID", deliverable.id],
    ["Task ID", deliverable.task_id],
    ["Producer ID", deliverable.producer_agent_id],
    ["Supersedes deliverable ID", deliverable.supersedes_deliverable_id],
    ["Superseded by deliverable ID", deliverable.superseded_by_deliverable_id],
    ["Source deliverable IDs", deliverable.source_deliverable_ids || []],
    ["Branch context ID", gitRefs.branch_context_id],
    ["Agent branch", gitRefs.agent_branch],
    ["Review branch", gitRefs.review_branch],
    ["Feature branch", gitRefs.feature_branch],
    ["Release branch", gitRefs.release_branch],
    ["Commit", gitRefs.commit_sha],
    ["Submitted commit", gitRefs.submitted_commit_sha],
    ["Reviewed commit", gitRefs.reviewed_commit_sha],
    ["Accepted immutable ref", gitRefs.accepted_pinned_ref],
    ["Acceptance policy version", deliverable.acceptance_policy_version],
    ["Composition source fingerprint", deliverable.dependency_composition_manifest?.source_fingerprint],
    ["Composition base commit", deliverable.dependency_composition_manifest?.base_commit_sha],
    ["Composition frontier commits", deliverable.dependency_composition_manifest?.frontier_commit_shas || []],
    ["Promotion IDs", gitRefs.promotion_record_ids || []],
    ["Initial release package ID", initialRelease.release_package_id],
  ], {
    disclosureKey: teamDisclosureKey("deliverable", deliverable.id, "technical"),
  });
}

function renderInitialReleaseDisposition(deliverable) {
  const disposition = deliverable.initial_release_disposition;
  if (!disposition) {
    return "";
  }
  const dispositionState = text(disposition.state || "pending");
  return `
    <section class="deliverable-detail-group deliverable-initial-release-disposition">
      <h4>Initial release decision</h4>
      <div class="delivery-detail-grid">
        <div><div class="delivery-detail-label">Disposition</div><div class="delivery-detail-value"><span class="state-pill ${escapeAttr(dispositionState)}">${escapeHtml(humanizeToken(dispositionState))}</span></div></div>
        ${disposition.decided_by_actor ? `<div><div class="delivery-detail-label">Decided by</div><div class="delivery-detail-value">${escapeHtml(disposition.decided_by_actor)}</div></div>` : ""}
        ${disposition.decided_at ? `<div><div class="delivery-detail-label">Decided</div><div class="delivery-detail-value">${renderRecordDate(disposition.decided_at)}</div></div>` : ""}
        ${disposition.release_package_id ? `<div><div class="delivery-detail-label">Release package</div><div class="delivery-detail-value">${renderIdentifierCopy(disposition.release_package_id, { label: "Copy ID" })}</div></div>` : ""}
      </div>
      ${disposition.reason ? `<p class="meta-line"><strong>Reason:</strong> ${escapeHtml(disposition.reason)}</p>` : ""}
      ${disposition.reopen_reason ? `<p class="meta-line"><strong>Last reopened:</strong> ${escapeHtml(disposition.reopen_reason)}${disposition.reopened_by_actor ? ` · ${escapeHtml(disposition.reopened_by_actor)}` : ""}${disposition.reopened_at ? ` · ${renderRecordDate(disposition.reopened_at)}` : ""}</p>` : ""}
    </section>
  `;
}

function renderDeliverableCard(deliverable, { compact = false } = {}) {
  const task = deliverableTask(deliverable);
  const subject = deliverableDisplaySubject(deliverable);
  const producer = deliverableProducerLabel(deliverable);
  const preview = readablePreview(deliverable.summary);
  const reviewIds = deliverable.review_request_ids || [];
  return `
    <li class="record-item compact-record-card deliverable-card${compact ? " deliverable-card-compact" : ""}" data-deliverable-id="${escapeAttr(deliverable.id)}">
      <details class="deliverable-card-details" ${teamDisclosureAttributes(teamDisclosureKey("deliverable", deliverable.id, "detail"))}>
        <summary class="compact-record-summary deliverable-card-summary">
          <span class="compact-record-main">
            <strong>${escapeHtml(subject)}</strong>
            <span>${escapeHtml(producer)} · ${renderRecordDate(deliverable.created_at)}</span>
            ${preview && preview !== subject ? `<span class="deliverable-summary-preview">${escapeHtml(preview)}</span>` : ""}
          </span>
          <span class="state-pill ${escapeAttr(deliverable.state)}">${escapeHtml(humanizeToken(deliverable.state))}</span>
        </summary>
        <div class="compact-record-body deliverable-card-body">
          <section class="deliverable-detail-group">
            <h4>Summary / outcome</h4>
            <p class="deliverable-full-summary">${escapeHtml(deliverable.summary)}</p>
            ${renderDeliverableOutcomes(deliverable)}
          </section>
          <section class="deliverable-detail-group">
            <h4>Task and producer</h4>
            <div class="delivery-detail-grid">
              <div><div class="delivery-detail-label">Task</div><div class="delivery-detail-value">${escapeHtml(task?.title || "Task unavailable")}${task ? ` <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(taskStateLabel(task))}</span>` : ""}</div></div>
              <div><div class="delivery-detail-label">Producer</div><div class="delivery-detail-value">${escapeHtml(producer)}</div></div>
              <div><div class="delivery-detail-label">Recorded</div><div class="delivery-detail-value">${renderRecordDate(deliverable.created_at)}</div></div>
              <div><div class="delivery-detail-label">Decision</div><div class="delivery-detail-value">${escapeHtml(humanizeToken(deliverable.acceptance_decision?.decision || deliverable.state))}</div></div>
            </div>
          </section>
          <section class="deliverable-detail-group">
            <h4>Evidence and artifacts</h4>
            ${renderDeliverableEvidence(deliverable)}
          </section>
          ${renderDeliverableAcceptanceVerification(deliverable)}
          ${renderInitialReleaseDisposition(deliverable)}
          <section class="deliverable-detail-group">
            <h4>Reviews and decision</h4>
            ${reviewIds.length ? renderReviewSummary(reviewIds) : `<p class="empty-state">No reviews recorded.</p>`}
            ${renderRecordBlockers("deliverable", deliverable.id, deliverable.task_id)}
          </section>
          <section class="deliverable-detail-group deliverable-lineage-group">
            <h4>Lineage / technical details</h4>
            ${renderDeliverableTechnicalDetails(deliverable)}
          </section>
        </div>
      </details>
    </li>
  `;
}

function renderDeliverables(deliverables, { compact = false } = {}) {
  if (!deliverables.length) {
    return `<p class="empty-state">No deliverables.</p>`;
  }
  return `
    <ul class="deliverable-list${compact ? " compact-record-list" : ""}">
      ${deliverables.map((deliverable) => renderDeliverableCard(deliverable, { compact })).join("")}
    </ul>
  `;
}

function renderReviewSummary(reviewIds, { interactive = false } = {}) {
  if (!reviewIds.length) {
    return "";
  }
  const reviews = reviewIds.map((reviewId) => state.review_requests[reviewId]).filter(Boolean);
  if (!reviews.length) {
    return "";
  }
  return `
    <ul class="review-list">
      ${reviews
        .map((review) => {
          const profile = review.review_profile_id
            ? state.review_profiles[review.review_profile_id] || null
            : null;
          const deliverable = state.deliverables[review.deliverable_id] || null;
          const carriedFindings = (deliverable?.carried_findings || []).filter(
            (finding) => finding.state === "open",
          );
          const reviewer = state.agents[review.reviewer_agent_id] || null;
          const reviewerName = reviewer ? agentLabel(reviewer) : review.reviewer_agent_id;
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>Review by ${escapeHtml(reviewerName)}</strong>
                <span class="state-pill ${escapeHtml(review.state)}">${escapeHtml(review.state)}</span>
              </div>
              <div class="meta-line">${profile?.name ? `Profile ${escapeHtml(profile.name)}` : "No review profile"}${review.decision_at ? ` · decided ${renderRecordDate(review.decision_at)}` : review.created_at ? ` · requested ${renderRecordDate(review.created_at)}` : ""}</div>
              ${review.instructions ? `<div class="meta-line">Instructions: ${escapeHtml(review.instructions)}</div>` : ""}
              ${(review.findings || []).length ? `<div class="meta-line">Findings: ${review.findings.map((finding) => escapeHtml(finding)).join("; ")}</div>` : ""}
              ${renderRecordBlockers("review_request", review.id)}
              ${renderCriterionResults(review.criteria || [])}
              ${renderCarriedFindings(carriedFindings)}
              ${interactive ? renderReviewDecisionForm(review, profile, carriedFindings) : ""}
              ${renderTechnicalDetails([
                ["Review ID", review.id],
                ["Reviewer ID", review.reviewer_agent_id],
                ["Review profile ID", review.review_profile_id],
                ["Deliverable ID", review.deliverable_id],
                ["Release ID", review.release_package_id],
                ["Reviewed commit", review.reviewed_commit_sha],
              ], {
                disclosureKey: teamDisclosureKey("review-request", review.id, "technical"),
              })}
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function renderReviewDecisionForm(review, profile, carriedFindings = []) {
  if (terminalRecordStates.has(review.state) || review.decision_at) {
    return "";
  }
  const selectedState = decisionStates.includes(review.state) ? review.state : "";
  const criteriaHelp = renderReviewCriteriaHelp(profile?.criteria || []);
  return `
    <form
      class="review-decision-form"
      id="review-decision-form-${escapeAttr(review.id)}"
      data-review-decision-form="true"
      data-review-request-id="${escapeAttr(review.id)}"
    >
      <label>
        Decision
        <select name="state" required>
          <option value="">Select</option>
          ${decisionStates
            .map((stateValue) => {
              const selected = stateValue === selectedState ? " selected" : "";
              return `<option value="${escapeAttr(stateValue)}"${selected}>${escapeHtml(stateValue)}</option>`;
            })
            .join("")}
        </select>
      </label>
      <label>
        Findings
        <textarea name="findings" rows="2"></textarea>
      </label>
      ${renderReviewCriterionInputs(profile?.criteria || [])}
      ${renderFindingResolutionInputs(carriedFindings)}
      ${criteriaHelp}
      <button type="submit">Submit decision</button>
    </form>
  `;
}

function renderReviewCriterionInputs(criteria) {
  if (!criteria.length) return "";
  return `
    <fieldset data-review-criterion-inputs>
      <legend>Criteria results</legend>
      ${criteria.map((criterion) => `
        <div class="criteria-editor-row" data-review-criterion-id="${escapeAttr(criterion.id)}">
          <label>${escapeHtml(criterion.label)}${criterion.required === false ? " (optional)" : " (required)"}
            <select name="criterion_state__${escapeAttr(criterion.id)}"${criterion.required === false ? "" : " required"}>
              <option value="">Select</option>
              <option value="passed">Passed</option>
              <option value="failed">Failed</option>
              <option value="skipped">Skipped</option>
            </select>
          </label>
          <label>Finding
            <input name="criterion_finding__${escapeAttr(criterion.id)}" type="text" />
          </label>
          <label>Evidence references (optional)
            <textarea name="criterion_evidence_refs__${escapeAttr(criterion.id)}" rows="2" placeholder="Optional diagnostic references"></textarea>
          </label>
        </div>
      `).join("")}
    </fieldset>
  `;
}

function renderCarriedFindings(findings) {
  if (!findings.length) return "";
  return `<div class="meta-line"><strong>Open carried findings:</strong><ul>${findings
    .map((finding) => `<li>${escapeHtml(finding.id)} - ${escapeHtml(finding.text)}</li>`)
    .join("")}</ul></div>`;
}

function renderFindingResolutionInputs(findings) {
  if (!findings.length) return "";
  return `
    <fieldset data-finding-resolution-inputs>
      <legend>Carried-finding resolutions</legend>
      ${findings.map((finding) => `
        <label>${escapeHtml(finding.text)}
          <input name="finding_resolution__${escapeAttr(finding.id)}" type="text" placeholder="Resolution and evidence" />
        </label>
      `).join("")}
    </fieldset>
  `;
}

function renderReviewCriteriaHelp(criteria) {
  if (!criteria.length) {
    return "";
  }
  return `
    <div class="meta-line">Profile criteria:</div>
    ${renderProfileCriteria(criteria)}
  `;
}

function renderReviewCriteriaPlaceholder(criteria) {
  if (!criteria.length) {
    return "criterion_id | passed | finding | evidence-ref";
  }
  return criteria
    .map((criterion) => {
      const finding = criterion.description || criterion.label || "finding";
      return `${criterion.id} | passed | ${finding} | evidence-ref`;
    })
    .join("\n");
}

function renderCriterionResults(criteria) {
  if (!criteria.length) {
    return "";
  }
  return `
    <ul class="inline-list">
      ${criteria
        .map((criterion) => {
          const finding = criterion.finding ? ` - ${escapeHtml(criterion.finding)}` : "";
          const evidence = (criterion.evidence_refs || []).length
            ? ` · evidence: ${(criterion.evidence_refs || []).map((ref) => escapeHtml(ref)).join(", ")}`
            : "";
          return `<li>${escapeHtml(criterion.criterion_id)}: ${escapeHtml(criterion.state)}${finding}${evidence}</li>`;
        })
        .join("")}
    </ul>
  `;
}

function renderArtifactRefs(refs) {
  if (!refs.length) {
    return "";
  }
  return `<div class="meta-line">Artifacts: ${refs.map((ref) => escapeHtml(ref)).join(", ")}</div>`;
}

function renderGitRefs(gitRefs, extraPromotionIds = []) {
  const refs = gitRefs || {};
  const branchLabels = [
    ["agent_branch", "agent"],
    ["review_branch", "review"],
    ["feature_branch", "feature"],
    ["release_branch", "release"],
  ];
  const parts = branchLabels
    .filter(([key]) => refs[key])
    .map(([key, label]) => `${label} ${refs[key]}`);
  if (refs.commit_sha) {
    parts.push(`commit ${refs.commit_sha}`);
  }
  const promotionIds = uniqueTextList([
    ...(refs.promotion_record_ids || []),
    ...extraPromotionIds,
  ]);
  const promotions = promotionIds
    .map((promotionId) => state.git_promotions[promotionId])
    .filter(Boolean);
  if (!parts.length && !promotions.length) {
    return "";
  }
  const branchLine = parts.length
    ? `<div class="meta-line">Git: ${escapeHtml(parts.join(" - "))}</div>`
    : "";
  const promotionLine = promotions.length
    ? `<div class="meta-line">Promotions: ${promotions
        .map(
          (promotion) =>
            `${escapeHtml(promotion.id)} ${escapeHtml(promotion.promotion_kind)}:${escapeHtml(promotion.state)}`,
        )
        .join(", ")}</div>`
    : "";
  return `${branchLine}${promotionLine}`;
}

function renderReviewProfiles() {
  if (!document.querySelector("[data-review-profiles-view]")) {
    return;
  }
  const allProfiles = values(state.review_profiles).sort(compareReviewProfiles);
  const profiles = allProfiles.filter(isVisibleReviewProfile);
  if (!allProfiles.length) {
    renderIntoActiveTargets("[data-review-profiles-view]", `<p class="empty-state">No review profiles.</p>`);
    return;
  }
  if (!profiles.length) {
    renderIntoActiveTargets("[data-review-profiles-view]", `<p class="empty-state">No visible review profiles.</p>`);
    return;
  }
  renderIntoActiveTargets("[data-review-profiles-view]", `
    <ul class="review-list compact-record-list">
      ${profiles.map((profile) => renderCompactReviewProfile(profile)).join("")}
    </ul>
  `);
}

function renderCompactReviewProfile(profile) {
  const criteria = profile.criteria || [];
  const scope = reviewProfileScope(profile);
  const reviewerLabels = reviewProfileReviewerLabels(profile);
  const outcomeKinds = profile.required_outcome_kinds || [];
  const outcomeLabels = outcomeKinds.map((kind) => reviewProfileOutcomeLabel(profile, kind));
  return `
    <li class="record-item compact-record-card review-profile-card">
      <div class="review-profile-card-header">
        <span class="review-profile-card-title">
          <strong>${escapeHtml(profile.name)}</strong>
          <span>${escapeHtml(scope.summary)} · ${criteria.length} ${criteria.length === 1 ? "criterion" : "criteria"}</span>
        </span>
        <div class="record-actions">
          <span class="state-pill ${profile.archived ? "blocked" : "accepted"}">${profile.archived ? "archived" : "active"}</span>
          ${editButton("review-profile", profile.id)}
        </div>
      </div>
      <details class="review-profile-card-details" ${teamDisclosureAttributes(teamDisclosureKey("review-profile", profile.id, "detail"))}>
        <summary class="review-profile-detail-toggle">View profile</summary>
        <div class="review-profile-card-body">
          <div class="review-profile-section-grid">
            <section class="review-profile-section">
              <h4>Purpose</h4>
              <p>${escapeHtml(profile.description || "No purpose description provided.")}</p>
            </section>
            <section class="review-profile-section">
              <h4>Reviewers</h4>
              ${renderReviewProfileChips(reviewerLabels, "All reviewer roles")}
            </section>
            <section class="review-profile-section">
              <h4>Required outcomes</h4>
              ${renderReviewProfileChips(outcomeLabels, "No required outcomes")}
            </section>
            <section class="review-profile-section review-profile-criteria-section">
              <h4>Criteria</h4>
              ${renderProfileCriteria(criteria)}
            </section>
          </div>
          ${renderReviewProfileTechnicalDetails(profile, scope, outcomeKinds)}
        </div>
      </details>
    </li>
  `;
}

function reviewProfileScope(profile) {
  if (profile.project_id) {
    const project = state.projects[text(profile.project_id)];
    return {
      idLabel: "Project ID",
      id: text(profile.project_id),
      summary: `Project · ${text(project?.name) || "Unavailable project"}`,
    };
  }
  const team = state.teams[text(profile.team_id)];
  return {
    idLabel: "Team ID",
    id: text(profile.team_id),
    summary: `Team-wide · ${text(team?.name) || "Unavailable team"}`,
  };
}

function reviewProfileReviewerLabels(profile) {
  return (profile.applicable_role_ids || []).map((roleId) => {
    const role = state.roles[text(roleId)];
    if (role) {
      return roleLabel(role);
    }
    const roleKey = text(roleId).split("__").pop() || "";
    return roleKey ? humanizeToken(roleKey.replace(/-/g, "_")) : "Unavailable role";
  });
}

function reviewProfileOutcomeLabel(profile, kind) {
  const normalizedKind = text(kind);
  if (!normalizedKind) {
    return "";
  }
  const project = state.projects[text(profile.project_id)];
  const definition = project
    ? effectiveOutcomeKindsForProject(project).find((candidate) => text(candidate.kind) === normalizedKind)
    : values(state.outcome_kind_definitions)
        .filter((candidate) =>
          text(candidate.team_id) === text(profile.team_id)
          && !candidate.project_id
          && !candidate.archived
          && text(candidate.kind) === normalizedKind
        )
        .sort(byId)
        .at(-1);
  return text(definition?.name) || humanizeToken(normalizedKind);
}

function renderReviewProfileChips(labels, emptyLabel) {
  const visibleLabels = labels.map(text).filter(Boolean);
  if (!visibleLabels.length) {
    return `<p class="review-profile-empty">${escapeHtml(emptyLabel)}</p>`;
  }
  return `
    <ul class="review-profile-chip-list">
      ${visibleLabels.map((label) => `<li class="review-profile-chip">${escapeHtml(label)}</li>`).join("")}
    </ul>
  `;
}

function renderProfileCriteria(criteria) {
  if (!criteria.length) {
    return `<p class="review-profile-empty">No criteria defined.</p>`;
  }
  return `
    <ul class="review-profile-criteria-list">
      ${criteria
        .map((criterion) => {
          const required = criterion.required !== false;
          return `
            <li class="review-profile-criterion">
              <div class="review-profile-criterion-header">
                <strong>${escapeHtml(criterion.label || "Unnamed criterion")}</strong>
                <span class="criterion-requirement-badge ${required ? "required" : "optional"}">${required ? "Required" : "Optional"}</span>
              </div>
              ${criterion.description ? `<p>${escapeHtml(criterion.description)}</p>` : ""}
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function renderReviewProfileTechnicalDetails(profile, scope, outcomeKinds) {
  const criteria = profile.criteria || [];
  return `
    <details class="technical-details review-profile-technical-details" ${teamDisclosureAttributes(teamDisclosureKey("review-profile", profile.id, "technical"))}>
      <summary>Technical details</summary>
      <dl class="technical-details-list">
        <div>
          <dt>Profile ID</dt>
          <dd><code>${escapeHtml(profile.id)}</code></dd>
        </div>
        <div>
          <dt>${escapeHtml(scope.idLabel)}</dt>
          <dd><code>${escapeHtml(scope.id)}</code></dd>
        </div>
        ${(profile.applicable_role_ids || []).length ? `
          <div>
            <dt>Reviewer role IDs</dt>
            <dd>${profile.applicable_role_ids.map((roleId) => `<code>${escapeHtml(roleId)}</code>`).join(" ")}</dd>
          </div>
        ` : ""}
        ${outcomeKinds.length ? `
          <div>
            <dt>Outcome kinds</dt>
            <dd>${outcomeKinds.map((kind) => `<code>${escapeHtml(kind)}</code>`).join(" ")}</dd>
          </div>
        ` : ""}
        ${criteria.length ? `
          <div>
            <dt>Criterion IDs</dt>
            <dd>${criteria.map((criterion) => `<code>${escapeHtml(criterion.id)}</code>`).join(" ")}</dd>
          </div>
        ` : ""}
      </dl>
    </details>
  `;
}

function governanceTeamEditorRows(selectedIds = []) {
  const selected = new Set((selectedIds || []).map(text));
  const teamsById = new Map(values(state.teams).map((team) => [text(team.id), team]));
  selected.forEach((teamId) => {
    if (teamId && !teamsById.has(teamId)) {
      teamsById.set(teamId, { id: teamId, name: "Unavailable team", archived: true });
    }
  });
  return Array.from(teamsById.values())
    .sort(
      (left, right) =>
        text(left.name).localeCompare(text(right.name)) ||
        text(left.id).localeCompare(text(right.id)),
    )
    .map((team) => {
      const teamId = text(team.id);
      const searchText = `${text(team.name)} ${teamId}`.toLowerCase();
      return `
        <label
          class="governance-team-choice"
          data-governance-team-choice
          data-governance-team-search-text="${escapeAttr(searchText)}"
        >
          <input
            type="checkbox"
            name="governance_member_team__${escapeAttr(teamId)}"
            value="${escapeAttr(teamId)}"
            data-governance-member-team-id="${escapeAttr(teamId)}"
            ${selected.has(teamId) ? "checked" : ""}
          />
          <span>
            <strong>${escapeHtml(team.name || "Unnamed team")}</strong>
            ${team.archived ? `<small>Archived or unavailable</small>` : ""}
          </span>
        </label>
      `;
    })
    .join("");
}

function renderGovernanceMemberTeamsEditor(selectedIds = []) {
  return `
    <fieldset class="governance-member-teams-editor" data-governance-member-teams-editor>
      <legend>Member teams</legend>
      <label class="governance-team-search-label">
        <span>Search teams</span>
        <input
          type="search"
          name="governance_member_team_search"
          data-governance-team-search
          placeholder="Search by team name"
          autocomplete="off"
        />
      </label>
      <div class="governance-selected-teams" data-governance-selected-teams aria-live="polite"></div>
      <div class="governance-team-choices" data-governance-team-choices>
        ${governanceTeamEditorRows(selectedIds)}
      </div>
      <p class="field-help">Choose the teams supervised by this group. Team reporting lines are unchanged.</p>
    </fieldset>
  `;
}

function governanceMemberTeamIdsFromForm(form) {
  return Array.from(
    form.querySelectorAll("[data-governance-member-team-id]:checked"),
  )
    .map((input) => text(input.value))
    .filter(Boolean);
}

function syncGovernanceMemberTeamEditor(editor) {
  if (!editor) {
    return;
  }
  const selected = Array.from(
    editor.querySelectorAll("[data-governance-member-team-id]:checked"),
  );
  const summary = editor.querySelector("[data-governance-selected-teams]");
  if (summary) {
    summary.innerHTML = selected.length
      ? selected
          .map((input) => {
            const teamId = text(input.value);
            const team = state.teams?.[teamId];
            const label = team?.name || "Unavailable team";
            const attrs = lockedAttrs("change governance member teams");
            return `
              <span class="governance-team-chip">
                <span>${escapeHtml(label)}</span>
                <button
                  type="button"
                  data-governance-member-team-remove="${escapeAttr(teamId)}"
                  aria-label="${escapeAttr(`Remove ${label}`)}"
                  ${attrs}
                >×</button>
              </span>
            `;
          })
          .join("")
      : `<span class="empty-state">No member teams selected.</span>`;
  }
  const search = editor.querySelector("[data-governance-team-search]");
  filterGovernanceMemberTeams(editor, search?.value || "");
}

function filterGovernanceMemberTeams(editor, value) {
  if (!editor) {
    return;
  }
  const query = text(value).toLowerCase();
  editor.querySelectorAll("[data-governance-team-choice]").forEach((row) => {
    const checkbox = row.querySelector("[data-governance-member-team-id]");
    const matches =
      !query ||
      text(row.dataset.governanceTeamSearchText).includes(query) ||
      Boolean(checkbox?.checked);
    row.hidden = !matches;
  });
}

function removeGovernanceMemberTeam(button) {
  const editor = button.closest("[data-governance-member-teams-editor]");
  const teamId = text(button.dataset.governanceMemberTeamRemove);
  const checkbox = Array.from(
    editor?.querySelectorAll?.("[data-governance-member-team-id]") || [],
  ).find((candidate) => text(candidate.value) === teamId);
  if (!checkbox) {
    return;
  }
  checkbox.checked = false;
  syncGovernanceMemberTeamEditor(editor);
  rememberFormDraft(checkbox.form);
}

function renderGovernanceGoalsEditor(goals = []) {
  return `
    <fieldset class="governance-goals-editor" data-governance-goals-editor>
      <legend>Goals</legend>
      <div class="governance-goal-rows" data-governance-goal-rows>
        ${(goals || []).map((goal) => renderGovernanceGoalEditorRow(goal)).join("")}
      </div>
      <p class="governance-goals-empty" data-governance-goals-empty>No goals yet.</p>
      <button
        class="record-action governance-goal-add"
        type="button"
        data-governance-goal-add
      >Add goal</button>
      <p class="field-help">Each goal has its own name and description. Its stable ID is managed as technical metadata.</p>
    </fieldset>
  `;
}

function renderGovernanceGoalEditorRow(goal = {}) {
  const goalName =
    text(goal.name) || text(goal.description) || humanizeToken(goal.id) || "";
  const stateValue = text(goal.state) || "pending";
  return `
    <div class="governance-goal-row" data-governance-goal-row>
      <input type="hidden" name="governance_goal_id" value="${escapeAttr(goal.id || "")}" />
      <label>
        <span>Name</span>
        <input
          name="governance_goal_name"
          value="${escapeAttr(goalName)}"
          placeholder="Improve delivery predictability"
          required
        />
      </label>
      <label>
        <span>Description</span>
        <textarea
          name="governance_goal_description"
          rows="2"
          placeholder="Describe the intended outcome and how operators recognize it."
          required
        >${escapeHtml(goal.description || "")}</textarea>
      </label>
      <label>
        <span>State</span>
        <select name="governance_goal_state" required>
          ${recordStates.map((value) => `
            <option value="${escapeAttr(value)}"${stateValue === value ? " selected" : ""}>${escapeHtml(humanizeToken(value))}</option>
          `).join("")}
        </select>
      </label>
      <div class="governance-goal-row-actions" aria-label="Goal order and removal">
        <button
          class="record-action"
          type="button"
          data-governance-goal-move="up"
          aria-label="Move goal up"
        >Move up</button>
        <button
          class="record-action"
          type="button"
          data-governance-goal-move="down"
          aria-label="Move goal down"
        >Move down</button>
        <button
          class="record-action governance-goal-remove"
          type="button"
          data-governance-goal-remove
        >Remove</button>
      </div>
    </div>
  `;
}

function syncGovernanceGoalEditor(editor) {
  if (!editor) {
    return;
  }
  const rows = Array.from(editor.querySelectorAll("[data-governance-goal-row]"));
  const mutationLocked = teamMutationLocked();
  rows.forEach((row, index) => {
    const up = row.querySelector('[data-governance-goal-move="up"]');
    const down = row.querySelector('[data-governance-goal-move="down"]');
    if (up) {
      up.disabled = mutationLocked || index === 0;
      up.setAttribute("aria-disabled", String(up.disabled));
    }
    if (down) {
      down.disabled = mutationLocked || index === rows.length - 1;
      down.setAttribute("aria-disabled", String(down.disabled));
    }
  });
  const empty = editor.querySelector("[data-governance-goals-empty]");
  if (empty) {
    empty.hidden = rows.length > 0;
  }
}

function addGovernanceGoalRow(editor) {
  const rows = editor?.querySelector?.("[data-governance-goal-rows]");
  if (!rows) {
    return;
  }
  const wrapper = document.createElement("div");
  wrapper.innerHTML = renderGovernanceGoalEditorRow({});
  const row = wrapper.firstElementChild;
  if (!row) {
    return;
  }
  rows.appendChild(row);
  decorateFieldTooltips(row);
  syncGovernanceGoalEditor(editor);
  rememberFormDraft(editor.closest("form"));
  row.querySelector('[name="governance_goal_name"]')?.focus();
}

function removeGovernanceGoalRow(button) {
  const editor = button.closest("[data-governance-goals-editor]");
  button.closest("[data-governance-goal-row]")?.remove();
  syncGovernanceGoalEditor(editor);
  rememberFormDraft(editor?.closest("form"));
}

function moveGovernanceGoalRow(button) {
  const editor = button.closest("[data-governance-goals-editor]");
  const row = button.closest("[data-governance-goal-row]");
  const direction = text(button.dataset.governanceGoalMove);
  if (!editor || !row) {
    return;
  }
  if (direction === "up" && row.previousElementSibling) {
    row.parentElement.insertBefore(row, row.previousElementSibling);
  } else if (direction === "down" && row.nextElementSibling) {
    row.parentElement.insertBefore(row.nextElementSibling, row);
  }
  syncGovernanceGoalEditor(editor);
  rememberFormDraft(editor.closest("form"));
  button.focus();
}

function governanceGoalsDraftFromForm(form) {
  return Array.from(form.querySelectorAll("[data-governance-goal-row]")).map(
    (row) => ({
      id: text(row.querySelector('[name="governance_goal_id"]')?.value),
      name: text(row.querySelector('[name="governance_goal_name"]')?.value),
      description: text(
        row.querySelector('[name="governance_goal_description"]')?.value,
      ),
      state:
        text(row.querySelector('[name="governance_goal_state"]')?.value) ||
        "pending",
    }),
  );
}

function restoreGovernanceGoalsDraft(form, goals) {
  const editor = form.querySelector("[data-governance-goals-editor]");
  const rows = editor?.querySelector?.("[data-governance-goal-rows]");
  if (!rows || !Array.isArray(goals)) {
    return;
  }
  rows.innerHTML = goals.map((goal) => renderGovernanceGoalEditorRow(goal)).join("");
  decorateFieldTooltips(rows);
  syncGovernanceGoalEditor(editor);
}

function validateGovernanceGroupEditor(form) {
  const rows = Array.from(form.querySelectorAll("[data-governance-goal-row]"));
  const names = new Map();
  let valid = true;
  rows.forEach((row) => {
    const name = row.querySelector('[name="governance_goal_name"]');
    const description = row.querySelector('[name="governance_goal_description"]');
    const stateField = row.querySelector('[name="governance_goal_state"]');
    name?.setCustomValidity("");
    description?.setCustomValidity("");
    stateField?.setCustomValidity("");
    if (!text(name?.value)) {
      name?.setCustomValidity("Give this governance goal a name.");
      valid = false;
    }
    if (!text(description?.value)) {
      description?.setCustomValidity("Describe the intended governance outcome.");
      valid = false;
    }
    if (!recordStates.includes(text(stateField?.value))) {
      stateField?.setCustomValidity("Choose a valid governance goal state.");
      valid = false;
    }
    const normalizedName = text(name?.value).toLowerCase();
    if (normalizedName) {
      if (names.has(normalizedName)) {
        name?.setCustomValidity("Goal names must be unique within the group.");
        names.get(normalizedName)?.setCustomValidity(
          "Goal names must be unique within the group.",
        );
        valid = false;
      } else {
        names.set(normalizedName, name);
      }
    }
  });
  if (!form.checkValidity()) {
    valid = false;
  }
  if (!valid) {
    form.reportValidity();
    setStatus("Fix the highlighted governance goal before saving.", "error");
  }
  return valid;
}

function governanceGoalsFromForm(form) {
  return governanceGoalsDraftFromForm(form).map((goal) => {
    const payload = {
      name: goal.name,
      description: goal.description,
      state: goal.state,
    };
    if (goal.id) {
      payload.id = goal.id;
    }
    return payload;
  });
}

function renderReviewProfileCriteriaEditor(criteria = []) {
  const rows = criteria.length ? criteria : [{}];
  return `
    <fieldset class="criteria-editor" data-review-profile-criteria-editor>
      <legend>Criteria</legend>
      <div class="criteria-editor-rows" data-review-profile-criteria-rows>
        ${rows.map((criterion) => renderReviewProfileCriterionRow(criterion)).join("")}
      </div>
      <button class="record-action criteria-editor-add" type="button" data-review-profile-criterion-add>Add criterion</button>
    </fieldset>
  `;
}

function renderReviewProfileCriterionRow(criterion = {}) {
  const required = criterion.required !== false;
  return `
    <div class="criteria-editor-row" data-review-profile-criterion-row>
      <label>Label <input name="criterion_label" value="${escapeAttr(criterion.label || "")}" placeholder="Authorization" /></label>
      <label>Description <input name="criterion_description" value="${escapeAttr(criterion.description || "")}" placeholder="What reviewers should check" /></label>
      <label>Key <input name="criterion_id" value="${escapeAttr(criterion.id || "")}" placeholder="Auto from label" /></label>
      <label class="checkbox-label criteria-editor-required"><input name="criterion_required" type="checkbox"${required ? " checked" : ""} /> Required</label>
      <button class="record-action criteria-editor-remove" type="button" data-review-profile-criterion-remove>Remove</button>
    </div>
  `;
}

function addReviewProfileCriterionRow(editor) {
  const rows = editor?.querySelector?.("[data-review-profile-criteria-rows]");
  if (!rows) {
    return;
  }
  const wrapper = document.createElement("div");
  wrapper.innerHTML = renderReviewProfileCriterionRow({});
  const row = wrapper.firstElementChild;
  if (!row) {
    return;
  }
  rows.appendChild(row);
  decorateFieldTooltips(row);
  rememberFormDraft(editor.closest("form"));
  row.querySelector('input[name="criterion_label"]')?.focus();
}

function removeReviewProfileCriterionRow(button) {
  const editor = button.closest("[data-review-profile-criteria-editor]");
  const row = button.closest("[data-review-profile-criterion-row]");
  const rows = editor?.querySelector?.("[data-review-profile-criteria-rows]");
  if (!row || !rows) {
    return;
  }
  row.remove();
  if (!rows.querySelector("[data-review-profile-criterion-row]")) {
    addReviewProfileCriterionRow(editor);
    return;
  }
  rememberFormDraft(editor.closest("form"));
}

function reviewProfileCriteriaFromForm(form) {
  const rows = Array.from(form.querySelectorAll("[data-review-profile-criterion-row]"));
  const usedIds = new Set();
  return rows
    .map((row, index) => reviewProfileCriterionFromRow(row, index, usedIds))
    .filter(Boolean);
}

function reviewProfileCriteriaDraftFromForm(form) {
  const rows = Array.from(form.querySelectorAll("[data-review-profile-criterion-row]"));
  if (!rows.length) {
    return null;
  }
  return rows.map((row) => ({
    id: text(row.querySelector('[name="criterion_id"]')?.value),
    label: text(row.querySelector('[name="criterion_label"]')?.value),
    description: text(row.querySelector('[name="criterion_description"]')?.value),
    required: row.querySelector('[name="criterion_required"]')?.checked !== false,
  }));
}

function restoreReviewProfileCriteriaDraft(form, criteria) {
  const rows = form.querySelector("[data-review-profile-criteria-rows]");
  if (!rows || !Array.isArray(criteria)) {
    return;
  }
  rows.innerHTML = (criteria.length ? criteria : [{}])
    .map((criterion) => renderReviewProfileCriterionRow(criterion))
    .join("");
  decorateFieldTooltips(rows);
}

function reviewProfileCriterionFromRow(row, index, usedIds) {
  const label = text(row.querySelector('[name="criterion_label"]')?.value);
  const description = text(row.querySelector('[name="criterion_description"]')?.value);
  const explicitId = text(row.querySelector('[name="criterion_id"]')?.value);
  if (!label && !description && !explicitId) {
    return null;
  }
  const baseId = explicitId || criterionIdFromText(label || description || `criterion ${index + 1}`);
  const id = uniqueCriterionId(baseId, usedIds);
  usedIds.add(id);
  return {
    id,
    label: label || id,
    description,
    required: row.querySelector('[name="criterion_required"]')?.checked !== false,
  };
}

function criterionIdFromText(value) {
  const slug = text(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "criterion";
}

function uniqueCriterionId(baseId, usedIds) {
  const normalized = text(baseId) || "criterion";
  if (!usedIds.has(normalized)) {
    return normalized;
  }
  let suffix = 2;
  while (usedIds.has(`${normalized}-${suffix}`)) {
    suffix += 1;
  }
  return `${normalized}-${suffix}`;
}

function releaseIncludedWork(release) {
  const names = [];
  const deliverableIds = uniqueTextList([
    ...(release.deliverable_ids || []),
    ...((release.manifest?.deliverables || []).map((row) => row.deliverable_id)),
  ]);
  deliverableIds.forEach((deliverableId) => {
    const deliverable = state.deliverables?.[deliverableId];
    if (!deliverable) {
      return;
    }
    const task = deliverableTask(deliverable);
    names.push(task?.title || deliverableDisplaySubject(deliverable));
  });
  return uniqueTextList(names);
}

function releaseDisplayLabel(release) {
  const work = releaseIncludedWork(release);
  if (work.length) {
    return `Release: ${work[0]}${work.length > 1 ? ` + ${work.length - 1} more` : ""}`;
  }
  const timestamp = release.published_at || release.updated_at || release.created_at;
  if (timestamp) {
    return `${release.published_at ? "Published" : "Release prepared"} ${formatTimelineTimestamp(timestamp)}`;
  }
  return "Project release";
}

function releaseProjectLabel(release) {
  return state.projects?.[text(release.project_id)]?.name || text(release.project_id) || "Unknown project";
}

function releaseAgentReviews(release) {
  const embedded = release.release_reviews || [];
  if (embedded.length) {
    return embedded.filter((review) => !review.superseded_by_review_request_id);
  }
  return uniqueTextList(release.review_request_ids || [])
    .map((reviewId) => state.review_requests?.[reviewId])
    .filter((review) => review && !review.superseded_by_review_request_id);
}

function releaseVerificationCommandFailed(result) {
  const resultState = text(result?.state);
  const hasNonzeroExitCode =
    result?.exit_code !== null &&
    result?.exit_code !== undefined &&
    Number(result.exit_code) !== 0;
  return (
    ["failed", "timed_out", "blocked", "rejected"].includes(resultState) ||
    hasNonzeroExitCode ||
    Boolean(text(result?.failure_cause))
  );
}

function releaseVerificationDecisiveError(result, run = null) {
  const excerpt = text(result?.log_excerpt || run?.error);
  if (!excerpt) {
    return "";
  }
  const lines = excerpt
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) {
    return "";
  }
  const diagnosticPattern =
    /\b(?:error|failed|failure|fatal|exception|traceback|timed?\s*out|exit(?:ed)?)\b|(?:TS|CS)\d{3,}|ERR!/i;
  const diagnosticLines = lines.filter((line) => diagnosticPattern.test(line));
  return readablePreview(diagnosticLines.at(-1) || lines.at(-1), 220);
}

function releaseVerificationFailureSummary(runs) {
  const failedRuns = (runs || []).filter((run) =>
    ["failed", "timed_out", "blocked", "rejected"].includes(text(run.state)),
  );
  const run = failedRuns.at(-1);
  if (!run) {
    return "";
  }
  const failedResults = (run.command_results || []).filter(
    releaseVerificationCommandFailed,
  );
  const result = failedResults.at(-1);
  if (!result) {
    return run.error
      ? `Checks need attention · Error: ${readablePreview(run.error, 190)}`
      : "Checks need attention";
  }
  const parts = [`Failed command: ${text(result.name) || "Unnamed command"}`];
  if (result.exit_code !== null && result.exit_code !== undefined) {
    parts.push(`exit code ${Number(result.exit_code)}`);
  }
  if (result.failure_cause) {
    parts.push(`failure cause: ${humanizeToken(result.failure_cause)}`);
  }
  const decisiveError = releaseVerificationDecisiveError(result, run);
  if (decisiveError) {
    parts.push(`Error: ${decisiveError}`);
  }
  return readablePreview(parts.join(" · "), 260);
}

function releaseRequiresGitCandidate(release) {
  return Boolean(state.projects?.[text(release.project_id)]?.git);
}

function releaseCandidateInputsMissing(release) {
  const manifest = release.manifest || null;
  const modern = Number(release.policy_version || 1) >= 2 || Boolean(manifest);
  const candidateSha = text(release.candidate_commit_sha || manifest?.candidate_commit_sha);
  return Boolean(
    modern
    && releaseRequiresGitCandidate(release)
    && !candidateSha
    && !(release.git_promotion_record_ids || []).length
    && !(release.feature_branches || []).length
    && !text(release.release_branch)
    && !text(release.canonical_branch)
  );
}

function releaseCandidateDetail(release) {
  const manifest = release.manifest || null;
  if (release.candidate_commit_sha || manifest?.candidate_commit_sha) {
    return "Pinned candidate prepared";
  }
  if (Number(release.policy_version || 1) < 2) {
    return "Not recorded (legacy release)";
  }
  if (!releaseRequiresGitCandidate(release)) {
    return "Not required (artifact-only release)";
  }
  return releaseCandidateInputsMissing(release)
    ? "Blocked: no Git-backed release input"
    : "Not prepared";
}

function releaseProgressStages(release) {
  const manifest = release.manifest || null;
  const modern = Number(release.policy_version || 1) >= 2 || Boolean(manifest);
  const candidateSha = text(release.candidate_commit_sha || manifest?.candidate_commit_sha);
  const gitCandidateRequired = releaseRequiresGitCandidate(release);
  const verificationCommands = manifest?.verification_commands || [];
  const verificationRuns = (release.verification_runs || []).filter(
    (run) => !run.superseded_by_verification_run_id,
  );
  const reviews = releaseAgentReviews(release);
  const reviewRequirements = manifest?.review_requirements || [];
  const humanSummary = release.human_review || null;
  const humanRequired = Boolean(manifest?.human_review_requirement || humanSummary);
  const candidateInputsMissing = releaseCandidateInputsMissing(release);

  const stages = [];
  stages.push(
    !modern
      ? { label: "Candidate prepared", state: "legacy", detail: "Not recorded (legacy release)" }
      : candidateSha
        ? { label: "Candidate prepared", state: "complete", detail: "Pinned candidate ready" }
        : !gitCandidateRequired
          ? { label: "Candidate prepared", state: "not-applicable", detail: "No Git candidate required" }
        : candidateInputsMissing
          ? { label: "Candidate prepared", state: "blocked", detail: "No Git-backed release input" }
        : { label: "Candidate prepared", state: release.state === "blocked" ? "blocked" : "pending", detail: "Waiting for a pinned candidate" },
  );

  if (!modern) {
    stages.push({ label: "Automated verification", state: "legacy", detail: "Not recorded (legacy release)" });
  } else if (!verificationCommands.length && !verificationRuns.length) {
    stages.push({ label: "Automated verification", state: "not-applicable", detail: "No automated checks required" });
  } else {
    const runStates = verificationRuns.map((run) => text(run.state));
    const failed = runStates.some((stateValue) => ["failed", "timed_out", "blocked", "rejected"].includes(stateValue));
    const completed = runStates.length && runStates.every((stateValue) => ["passed", "succeeded", "accepted"].includes(stateValue));
    const runningRun = verificationRuns.find((run) => text(run.state) === "running");
    const currentCommandIndex = Number(runningRun?.current_command_index || 0);
    const currentCommand = runningRun?.command_results?.[currentCommandIndex]
      || runningRun?.command_results?.find((result) => text(result.state) === "running");
    const runningDetail = currentCommand?.name
      ? `Running: ${text(currentCommand.name)}`
      : runningRun
        ? "Preparing verifier"
        : "";
    stages.push({
      label: "Automated verification",
      state: failed ? "blocked" : completed ? "complete" : "pending",
      detail: failed ? releaseVerificationFailureSummary(verificationRuns) || "Checks need attention" : completed ? "Checks passed" : runningDetail || `${verificationCommands.length || verificationRuns.length} check${(verificationCommands.length || verificationRuns.length) === 1 ? "" : "s"} pending`,
    });
  }

  if (!modern && !reviews.length) {
    stages.push({ label: "Release agent review", state: "legacy", detail: "Not recorded (legacy release)" });
  } else if (!reviewRequirements.length && !reviews.length) {
    stages.push({ label: "Release agent review", state: "not-applicable", detail: "No agent review required" });
  } else {
    const reviewStates = reviews.map((review) => text(review.state));
    const failed = reviewStates.some((stateValue) => ["blocked", "rejected"].includes(stateValue));
    const completed = reviewStates.length && reviewStates.every((stateValue) => stateValue === "accepted");
    stages.push({
      label: "Release agent review",
      state: failed ? "blocked" : completed ? "complete" : "pending",
      detail: failed ? "Review needs attention" : completed ? `${reviews.length} review${reviews.length === 1 ? "" : "s"} accepted` : "Awaiting agent review",
    });
  }

  if (!humanRequired) {
    stages.push({ label: "Human review", state: "not-applicable", detail: "Not required" });
  } else {
    const humanState = text(humanSummary?.state || "required");
    const complete = ["approved", "accepted"].includes(humanState);
    const failed = ["blocked", "changes_requested", "rejected"].includes(humanState);
    stages.push({
      label: "Human review",
      state: complete ? "complete" : failed ? "blocked" : "pending",
      detail: complete ? "Approved" : failed ? humanizeToken(humanState) : "Awaiting human decision",
    });
  }

  const published = Boolean(release.published_at);
  stages.push({
    label: "Publication",
    state: published ? "complete" : ["blocked", "rejected"].includes(text(release.state)) ? "blocked" : "pending",
    detail: published ? `Published ${formatTimelineTimestamp(release.published_at)}` : release.error ? "Publication needs attention" : "Not published",
  });
  return stages;
}

function renderReleaseProgress(release) {
  const stages = releaseProgressStages(release);
  return `
    <ol class="release-progress" aria-label="Release progress">
      ${stages.map((stage, index) => `
        <li class="release-progress-stage release-progress-${escapeAttr(stage.state)}">
          <span class="release-progress-marker" aria-hidden="true">${stage.state === "complete" ? "✓" : index + 1}</span>
          <span class="release-progress-copy">
            <strong>${escapeHtml(stage.label)}</strong>
            <span>${escapeHtml(stage.detail)}</span>
          </span>
        </li>
      `).join("")}
    </ol>
  `;
}

function renderReleaseIncludedWork(release) {
  const deliverableIds = uniqueTextList([
    ...(release.deliverable_ids || []),
    ...((release.manifest?.deliverables || []).map((row) => row.deliverable_id)),
  ]);
  if (!deliverableIds.length) {
    return `<p class="empty-state">No included work recorded.</p>`;
  }
  return `
    <ul class="release-included-work">
      ${deliverableIds.map((deliverableId) => {
        const deliverable = state.deliverables?.[deliverableId];
        const task = deliverable ? deliverableTask(deliverable) : null;
        return `
          <li>
            <strong>${escapeHtml(task?.title || (deliverable ? deliverableDisplaySubject(deliverable) : "Included deliverable"))}</strong>
            ${deliverable?.summary ? `<p>${escapeHtml(readablePreview(deliverable.summary, 260))}</p>` : ""}
            ${deliverable ? `<span>${escapeHtml(deliverableProducerLabel(deliverable))} · ${escapeHtml(humanizeToken(deliverable.state))}</span>` : ""}
          </li>
        `;
      }).join("")}
    </ul>
  `;
}

function renderReleaseNotes(release) {
  const summary = text(release.summary);
  if (!summary) {
    return `<p class="empty-state release-notes-empty">No release notes were recorded for this package.</p>`;
  }
  return `<div class="release-notes">${escapeHtml(summary)}</div>`;
}

function renderReleaseTechnicalDetails(
  release,
  { disclosureKey = teamDisclosureKey("release", release.id, "technical") } = {},
) {
  const manifest = release.manifest || {};
  return renderTechnicalDetails([
    ["Release ID", release.id],
    ["Project ID", release.project_id],
    ["Deliverable IDs", release.deliverable_ids || []],
    ["Supersedes release ID", release.supersedes_release_package_id],
    ["Predecessor release ID", manifest.predecessor_release_package_id],
    ["Release branch", release.release_branch],
    ["Canonical branch", release.canonical_branch],
    ["Feature branches", release.feature_branches || []],
    ["Git base branch", release.git_base_branch],
    ["Base commit", release.base_commit_sha],
    ["Canonical destination base", release.canonical_base_commit_sha],
    ["Candidate commit", release.candidate_commit_sha || manifest.candidate_commit_sha],
    ["Candidate tree", release.candidate_tree_hash || manifest.candidate_tree_hash],
    ["Approved commit", release.approved_commit_sha],
    ["Published commit", release.published_commit_sha],
    ["Review IDs", release.review_request_ids || []],
    ["Verification run IDs", release.verification_run_ids || []],
    ["Promotion IDs", release.git_promotion_record_ids || []],
  ], { disclosureKey });
}

function renderReleaseCardBody(release) {
  return `
    <div class="release-card-body">
      <section class="release-card-section">
        <h4>Release progress</h4>
        ${renderReleaseProgress(release)}
      </section>
      <section class="release-card-section">
        <h4>Release notes</h4>
        ${renderReleaseNotes(release)}
      </section>
      <section class="release-card-section">
        <h4>Included work</h4>
        ${renderReleaseIncludedWork(release)}
      </section>
      ${release.error ? `<div class="task-invalid-warning"><strong>Release needs attention:</strong> ${escapeHtml(release.error)}</div>` : ""}
      ${renderRecordBlockers("release_package", release.id)}
      ${renderReleaseGateAction(release)}
      ${renderPostReleaseReportControl(release)}
      ${renderReleaseDetailControl(release)}
      ${renderCachedReleaseDetail(release.id)}
      ${renderReleasePolicyDetails(release) ? `<details class="technical-details release-policy-details" ${teamDisclosureAttributes(teamDisclosureKey("release-card", release.id, "policy"))}><summary>Policy and gate details</summary><div class="release-policy-body">${renderReleasePolicyDetails(release)}</div></details>` : ""}
      ${renderReleaseTechnicalDetails(release, {
        disclosureKey: teamDisclosureKey("release-card", release.id, "technical"),
      })}
    </div>
  `;
}

function renderReleaseCard(release, { compact = false } = {}) {
  const work = releaseIncludedWork(release);
  const scope = [
    releaseProjectLabel(release),
    work.length ? `${work.length} included work item${work.length === 1 ? "" : "s"}` : "No included work named",
    release.published_at ? `Published ${formatTimelineTimestamp(release.published_at)}` : release.created_at ? `Prepared ${formatTimelineTimestamp(release.created_at)}` : "",
  ].filter(Boolean);
  return `
    <li class="record-item compact-record-card release-card${compact ? " release-card-compact" : ""}" data-release-id="${escapeAttr(release.id)}">
      <details ${teamDisclosureAttributes(teamDisclosureKey("release-card", release.id, "detail"))}>
        <summary class="compact-record-summary release-card-summary">
          <span class="compact-record-main">
            <strong>${escapeHtml(releaseDisplayLabel(release))}</strong>
            <span>${escapeHtml(scope.join(" · "))}</span>
          </span>
          <span class="state-pill ${escapeAttr(release.state)}">${escapeHtml(humanizeToken(release.state))}</span>
        </summary>
        <div class="compact-record-body">${renderReleaseCardBody(release)}</div>
      </details>
    </li>
  `;
}

function renderReleases() {
  if (!document.querySelector("[data-releases-view]")) {
    return;
  }
  updateMyHumanReviewsFilterButton();
  const allReleases = values(state.release_packages).sort(compareProjectReleasesLatest);
  const focusedRequest = focusedHumanReviewRequestId
    ? humanReviewQueue.requests.find(
        (request) => text(request.id) === focusedHumanReviewRequestId,
      )
    : null;
  const assignedReleaseIds = new Set(
    (focusedRequest ? [focusedRequest] : humanReviewQueue.requests)
      .map((request) => text(request.release_package_id))
      .filter(Boolean),
  );
  const scopedReleases = myHumanReviewsOnly
    ? allReleases.filter((release) => assignedReleaseIds.has(text(release.id)))
    : allReleases;
  if (
    myHumanReviewsOnly &&
    (humanReviewQueue.loading ||
      (!humanReviewQueue.loaded && !humanReviewQueue.error))
  ) {
    renderIntoActiveTargets("[data-releases-view]", `<p class="empty-state">Loading your human reviews.</p>`);
    return;
  }
  if (myHumanReviewsOnly && humanReviewQueue.error) {
    renderIntoActiveTargets(
      "[data-releases-view]",
      `<div class="task-invalid-warning">${escapeHtml(humanReviewQueue.error)} <button type="button" class="record-action" data-my-human-reviews-retry>Retry</button></div>`,
    );
    return;
  }
  const releases = scopedReleases.filter(releaseMatchesHistoryFilters);
  const filters = renderReleaseHistoryFilters({
    visibleCount: releases.length,
    scopedCount: scopedReleases.length,
  });
  if (!releases.length) {
    renderIntoActiveTargets(
      "[data-releases-view]",
      `
        ${filters}
        <p class="empty-state">${myHumanReviewsOnly && !scopedReleases.length ? "No human reviews are assigned to you." : myHumanReviewsOnly ? "No assigned release packages match these filters." : "No release packages match these filters."}</p>
      `,
    );
    return;
  }
  if (activeTeamView === "delivery") {
    renderIntoActiveTargets("[data-releases-view]", `
      ${filters}
      <ul class="release-list compact-record-list">
        ${releases.map((release) => renderReleaseCard(release, { compact: true })).join("")}
      </ul>
    `);
    return;
  }
  renderIntoActiveTargets("[data-releases-view]", `
    ${filters}
    <ul class="release-list compact-record-list">
      ${releases.map((release) => renderReleaseCard(release)).join("")}
    </ul>
  `);
}

function renderReleaseHistoryFilters({ visibleCount, scopedCount }) {
  const hasFilters = Boolean(
    selectedReleaseHistoryState || releaseHistoryDateFrom || releaseHistoryDateThrough,
  );
  const summary = hasFilters
    ? `${visibleCount} of ${scopedCount} release package${scopedCount === 1 ? "" : "s"} shown`
    : `${scopedCount} release package${scopedCount === 1 ? "" : "s"} in history`;
  return `
    <div class="release-history-filters" data-release-history-filters role="group" aria-label="Release history filters">
      <label>
        State
        <select data-release-history-filter="state">
          ${renderSelectOptions(
            releaseHistoryStates.map((stateValue) => [stateValue, humanizeToken(stateValue)]),
            { selectedValue: selectedReleaseHistoryState, placeholder: "Any state" },
          )}
        </select>
      </label>
      <label>
        Activity date from
        <input type="date" value="${escapeAttr(releaseHistoryDateFrom)}" data-release-history-filter="date-from" />
      </label>
      <label>
        Activity date through
        <input type="date" value="${escapeAttr(releaseHistoryDateThrough)}" data-release-history-filter="date-through" />
      </label>
      <button type="button" class="secondary release-history-clear-filters" data-clear-release-history-filters>
        Clear filters
      </button>
      <p class="release-history-filter-summary">${escapeHtml(summary)}. Activity date is published when available; otherwise prepared.</p>
    </div>
  `;
}

function resetReleaseHistoryFilters() {
  selectedReleaseHistoryState = "";
  releaseHistoryDateFrom = "";
  releaseHistoryDateThrough = "";
}

function releaseMatchesHistoryFilters(release) {
  if (selectedReleaseHistoryState && text(release.state) !== selectedReleaseHistoryState) {
    return false;
  }
  if (!releaseHistoryDateFrom && !releaseHistoryDateThrough) {
    return true;
  }
  const activityTimestamp = Date.parse(
    normalizeTimelineTimestampForSort(release.published_at || release.created_at || ""),
  );
  if (!Number.isFinite(activityTimestamp)) {
    return false;
  }
  const from = dateInputLocalBoundary(releaseHistoryDateFrom, false);
  const through = dateInputLocalBoundary(releaseHistoryDateThrough, true);
  return (
    (!Number.isFinite(from) || activityTimestamp >= from) &&
    (!Number.isFinite(through) || activityTimestamp <= through)
  );
}

function renderCompactRelease(release) {
  return renderReleaseCard(release, { compact: true });
}

function renderReleaseHumanSummary(release) {
  const summary = release.human_review;
  if (!summary) {
    return `<div class="meta-line">Human release review: not required</div>`;
  }
  const bits = [
    `state ${summary.state || "required"}`,
    summary.request_id ? `request ${summary.request_id}` : "",
    Number(summary.request_count || 0) ? `${summary.request_count} attempt${Number(summary.request_count) === 1 ? "" : "s"}` : "",
    (summary.open_finding_ids || []).length ? `open findings ${summary.open_finding_ids.join(", ")}` : "",
    summary.next_action ? `next ${humanizeToken(summary.next_action)}` : "",
  ].filter(Boolean);
  return `<div class="release-human-summary"><strong>Human review:</strong> ${escapeHtml(bits.join(" - "))}</div>`;
}

function renderPostReleaseReportControl(release) {
  if (
    text(release.state) !== "accepted" ||
    (!text(release.published_at) || !text(release.published_commit_sha))
  ) {
    return "";
  }
  const priorResult = postReleaseReportResults.get(text(release.id));
  const caseRecord = priorResult?.case || null;
  const triageTaskId = text(priorResult?.triageTaskId);
  const triageTask = triageTaskId ? state.tasks?.[triageTaskId] : null;
  const resultMarkup = caseRecord
    ? `
      <div class="post-release-report-result" role="status">
        <strong>Follow-up recorded.</strong>
        <div>Case <code>${escapeHtml(caseRecord.id)}</code>${caseRecord.title ? ` - ${escapeHtml(caseRecord.title)}` : ""}${caseRecord.status ? ` - ${escapeHtml(caseRecord.status)}` : ""}</div>
        ${
          triageTaskId
            ? `<div>Triage task <code>${escapeHtml(triageTaskId)}</code>${triageTask?.title ? ` - ${escapeHtml(triageTask.title)}` : ""}${triageTask?.state ? ` - ${escapeHtml(triageTask.state)}` : ""}</div>`
            : ""
        }
      </div>
    `
    : "";
  return `
    <div class="post-release-report-control">
      ${resultMarkup}
      <details ${teamDisclosureAttributes(teamDisclosureKey("release-card", release.id, "post-release-report"))}>
        <summary>Report regression or adjustment</summary>
        <form
          class="post-release-report-form"
          data-post-release-report-form
          data-form-id="post-release-report-form"
          data-form-draft-scope="${escapeAttr(release.id)}"
          data-release-id="${escapeAttr(release.id)}"
        >
          <p class="post-release-report-boundary">
            Reporting creates a follow-up case and triage task. It does not roll back this published release and does not automatically create or authorize a successor.
          </p>
          <label>
            Reason or adjustment needed
            <textarea
              name="report"
              rows="3"
              required
              aria-required="true"
              placeholder="Describe the regression, operational issue, or adjustment that needs triage"
            ></textarea>
          </label>
          <label>
            Evidence references
            <textarea
              name="evidence_refs"
              rows="2"
              required
              aria-required="true"
              placeholder="artifact-space:..., one per line"
            ></textarea>
          </label>
          <div class="record-actions">
            <button type="submit" class="record-action">Create follow-up case and triage task</button>
          </div>
        </form>
      </details>
    </div>
  `;
}

function renderReleaseDetailControl(release) {
  const expanded = expandedReleaseDetailIds.has(text(release.id));
  return `
    <div class="record-actions release-detail-actions">
      <button
        type="button"
        class="record-action"
        data-release-detail-id="${escapeAttr(release.id)}"
        aria-expanded="${expanded ? "true" : "false"}"
      >${expanded ? "Close release detail" : "Open release detail"}</button>
    </div>
  `;
}

function renderCachedReleaseDetail(releaseId) {
  if (!expandedReleaseDetailIds.has(text(releaseId))) {
    return "";
  }
  const cached = releaseDetailCache.get(text(releaseId));
  if (!cached || cached.loading) {
    return `<div class="release-review-detail"><p class="empty-state">Loading release detail.</p></div>`;
  }
  if (cached.error) {
    return `<div class="release-review-detail task-invalid-warning">${escapeHtml(cached.error)}</div>`;
  }
  const release = mergeReleaseDetailWithCurrentState(cached.release);
  ensureReleaseVerificationLivePolling(release);
  return renderReleaseDetail(release);
}

async function toggleReleaseDetail(releaseId) {
  if (!releaseId) {
    return;
  }
  if (expandedReleaseDetailIds.has(releaseId)) {
    expandedReleaseDetailIds.delete(releaseId);
    clearReleaseVerificationLivePolling(releaseId);
    openTeamDisclosures.delete(
      teamDisclosureStateKey(teamDisclosureKey("release-card", releaseId, "detail")),
    );
    renderReleaseDetailSurfaces();
    return;
  }
  expandedReleaseDetailIds.add(releaseId);
  openTeamDisclosures.add(
    teamDisclosureStateKey(teamDisclosureKey("release-card", releaseId, "detail")),
  );
  renderReleaseDetailSurfaces();
  const cached = releaseDetailCache.get(releaseId);
  if (!cached?.release) {
    await loadReleaseDetail(releaseId);
  } else {
    ensureReleaseVerificationLivePolling(mergeReleaseDetailWithCurrentState(cached.release));
  }
}

function renderReleaseDetailSurfaces() {
  renderReleases();
  renderProjectFocus();
  renderReviewDeliveryWorkspace();
}

async function loadReleaseDetail(releaseId) {
  releaseDetailCache.set(releaseId, { loading: true, error: null, release: null });
  renderReleaseDetailSurfaces();
  try {
    const response = await teamFetch(`${API_BASE}/release-packages/${encodeURIComponent(releaseId)}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const release = await response.json();
    releaseDetailCache.set(releaseId, { loading: false, error: null, release });
  } catch (error) {
    releaseDetailCache.set(releaseId, {
      loading: false,
      error: error instanceof Error ? error.message : "Release detail could not be loaded.",
      release: null,
    });
  }
  renderReleaseDetailSurfaces();
}

function mergeReleaseDetailWithCurrentState(release) {
  const current = state.release_packages?.[text(release?.id)];
  if (!current) {
    return release;
  }
  return {
    ...release,
    state: current.state ?? release.state,
    error: current.error ?? release.error,
    updated_at: current.updated_at ?? release.updated_at,
    manifest: current.manifest ?? release.manifest,
    verification_runs: current.verification_runs ?? release.verification_runs,
  };
}

function activeReleaseVerificationRun(release) {
  return (release?.verification_runs || []).find(
    (run) => !run.superseded_by_verification_run_id && text(run.state) === "running",
  ) || null;
}

function releaseVerificationRunStillActive(releaseId) {
  const normalizedId = text(releaseId);
  const detailedRelease = releaseDetailCache.get(normalizedId)?.release;
  const release = detailedRelease
    ? mergeReleaseDetailWithCurrentState(detailedRelease)
    : state.release_packages?.[normalizedId];
  return Boolean(activeReleaseVerificationRun(release));
}

function clearReleaseVerificationLivePolling(releaseId) {
  const normalizedId = text(releaseId);
  const timer = releaseVerificationLivePollTimers.get(normalizedId);
  if (timer) {
    window.clearTimeout(timer);
  }
  releaseVerificationLivePollTimers.delete(normalizedId);
  releaseVerificationLiveCache.delete(normalizedId);
}

function ensureReleaseVerificationLivePolling(release) {
  const releaseId = text(release?.id);
  if (!releaseId || !expandedReleaseDetailIds.has(releaseId)) {
    return;
  }
  if (!activeReleaseVerificationRun(release)) {
    clearReleaseVerificationLivePolling(releaseId);
    return;
  }
  const cached = releaseVerificationLiveCache.get(releaseId);
  if (!cached || (!cached.loading && !cached.payload && !cached.error)) {
    void loadReleaseVerificationLive(releaseId);
  }
}

function scheduleReleaseVerificationLivePoll(releaseId, payload, retryWhileActive = false) {
  const normalizedId = text(releaseId);
  const previous = releaseVerificationLivePollTimers.get(normalizedId);
  if (previous) {
    window.clearTimeout(previous);
  }
  if (
    !expandedReleaseDetailIds.has(normalizedId)
    || (
      text(payload?.run?.state) !== "running"
      && !(retryWhileActive && releaseVerificationRunStillActive(normalizedId))
    )
  ) {
    releaseVerificationLivePollTimers.delete(normalizedId);
    return;
  }
  releaseVerificationLivePollTimers.set(normalizedId, window.setTimeout(() => {
    releaseVerificationLivePollTimers.delete(normalizedId);
    void loadReleaseVerificationLive(normalizedId);
  }, RELEASE_VERIFICATION_LIVE_POLL_MS));
}

async function loadReleaseVerificationLive(releaseId) {
  const normalizedId = text(releaseId);
  const existing = releaseVerificationLiveCache.get(normalizedId);
  if (existing?.loading) {
    return;
  }
  releaseVerificationLiveCache.set(normalizedId, {
    loading: true,
    error: null,
    payload: existing?.payload || null,
  });
  renderReleaseDetailSurfaces();
  try {
    const response = await teamFetch(
      `${API_BASE}/release-packages/${encodeURIComponent(normalizedId)}/verification-live?limit=160`,
      { headers: { Accept: "application/json" } },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    releaseVerificationLiveCache.set(normalizedId, { loading: false, error: null, payload });
    scheduleReleaseVerificationLivePoll(normalizedId, payload);
  } catch (error) {
    releaseVerificationLiveCache.set(normalizedId, {
      loading: false,
      error: error instanceof Error ? error.message : "Live verifier output could not be loaded.",
      payload: existing?.payload || null,
    });
    scheduleReleaseVerificationLivePoll(normalizedId, existing?.payload, true);
  }
  renderReleaseDetailSurfaces();
}

async function loadMyHumanReviews() {
  if (humanReviewQueue.loading) {
    return;
  }
  humanReviewQueue = { ...humanReviewQueue, loading: true, error: null };
  renderReleases();
  try {
    const query = new URLSearchParams({ assigned_to: "me" });
    const response = await teamFetch(`${API_BASE}/human-review-requests?${query.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    humanReviewQueue = {
      loaded: true,
      loading: false,
      error: null,
      requests: payload.human_review_requests || [],
    };
    state.revision = payload.revision ?? state.revision;
  } catch (error) {
    humanReviewQueue = {
      ...humanReviewQueue,
      loaded: false,
      loading: false,
      error: error instanceof Error ? error.message : "Human reviews could not be loaded.",
    };
  }
  renderShell();
  renderReleases();
}

async function openHumanReviewDeepLink(requestId) {
  const normalizedRequestId = text(requestId);
  myHumanReviewsOnly = true;
  focusedHumanReviewRequestId = normalizedRequestId;
  await loadMyHumanReviews();
  if (!focusedHumanReviewRequestId) {
    return true;
  }
  const request = humanReviewQueue.requests.find(
    (item) => text(item.id) === focusedHumanReviewRequestId,
  );
  const releaseId = text(request?.release_package_id);
  if (!request || !releaseId || !state.release_packages?.[releaseId]) {
    focusedHumanReviewRequestId = "";
    renderReleases();
    return false;
  }
  expandedReleaseDetailIds.add(releaseId);
  openTeamDisclosures.add(
    teamDisclosureStateKey(teamDisclosureKey("release-card", releaseId, "detail")),
  );
  renderReleases();
  await loadReleaseDetail(releaseId);
  focusHumanReviewRequestElement(focusedHumanReviewRequestId);
  return true;
}

function focusHumanReviewRequestElement(requestId) {
  const element = document.getElementById(`human-review-${text(requestId)}`);
  if (!element) {
    return;
  }
  const disclosure = element.closest("details[data-team-disclosure-key]");
  if (disclosure) {
    disclosure.open = true;
    const disclosureKey = text(disclosure.dataset.teamDisclosureKey);
    if (disclosureKey) {
      openTeamDisclosures.add(disclosureKey);
    }
  }
  element.focus({ preventScroll: true });
  element.scrollIntoView({ block: "center", behavior: "auto" });
}

function renderReleaseDetail(release) {
  if (!release) {
    return "";
  }
  const manifest = release.manifest || {};
  const project = state.projects[release.project_id] || {};
  const contributionGraph = manifest.contribution_graph || {};
  const deliverableRows = manifest.deliverables || [];
  const verificationRuns = release.verification_runs || [];
  const releaseReviews = release.release_reviews || [];
  const humanRequests = release.human_review?.requests || [];
  const includedWork = releaseIncludedWork(release);
  return `
    <section class="release-review-detail" id="release-detail-${escapeAttr(release.id)}">
      <div class="release-review-heading">
        <div>
          <p class="section-label">Release detail</p>
          <h3>${escapeHtml(releaseDisplayLabel(release))}</h3>
        </div>
        <span class="state-pill ${escapeAttr(release.state)}">${escapeHtml(release.state)}</span>
      </div>
      <div class="delivery-detail-grid">
        <div><div class="delivery-detail-label">Project</div><div class="delivery-detail-value">${escapeHtml(project.name || "Unknown project")}</div></div>
        <div><div class="delivery-detail-label">Included work</div><div class="delivery-detail-value">${escapeHtml(includedWork.join(", ") || "No named work")}</div></div>
        <div><div class="delivery-detail-label">Release lineage</div><div class="delivery-detail-value">${release.supersedes_release_package_id || manifest.predecessor_release_package_id ? "Successor release attempt" : "Initial release attempt"}</div></div>
        <div><div class="delivery-detail-label">Candidate</div><div class="delivery-detail-value">${escapeHtml(releaseCandidateDetail(release))}</div></div>
        <div><div class="delivery-detail-label">Publication</div><div class="delivery-detail-value">${release.published_at ? renderRecordDate(release.published_at, "Published") : escapeHtml(release.next_action_hint || release.next_action || "Not published")}</div></div>
        <div><div class="delivery-detail-label">Policy</div><div class="delivery-detail-value">Version ${Number(release.policy_version || 1)}</div></div>
      </div>
      ${renderReleaseCandidateCheckout(release)}
      ${renderReleaseDeliverableEvidence(deliverableRows, release)}
      ${renderReleaseContributionGraph(contributionGraph, release.id)}
      ${renderReleaseVerificationRuns(verificationRuns, manifest.verification_commands || [], release.id)}
      ${renderReleaseAgentReviews(releaseReviews, release.id, release.release_review_cohort)}
      ${renderReleaseHumanRequests(humanRequests, manifest, release.id)}
      ${renderReleaseEvidence(release, deliverableRows, humanRequests)}
    </section>
  `;
}

function renderReleaseCandidateCheckout(release) {
  const checkout = release.candidate_checkout;
  if (!checkout?.checkout_command) {
    return "";
  }
  const repositoryLabel =
    text(checkout.repository_display_name) ||
    text(checkout.canonical_repository_identity) ||
    "Managed repository";
  return `
    <div class="release-detail-section release-candidate-checkout">
      <h4>Test this candidate</h4>
      <p class="release-candidate-checkout-intro">Create a new detached checkout of the exact candidate that passed this release pipeline. The command verifies the immutable release ref, commit, and tree before writing anything.</p>
      <dl class="release-candidate-checkout-metadata">
        <div><dt>Repository</dt><dd><strong>${escapeHtml(repositoryLabel)}</strong><code>${escapeHtml(checkout.canonical_repository_identity)}</code></dd></div>
        <div><dt>Managed repository ID</dt><dd>${renderIdentifierCopy(checkout.managed_repo_id, { label: "Copy ID" })}</dd></div>
        <div><dt>Immutable release ref</dt><dd>${renderIdentifierCopy(checkout.release_ref, { label: "Copy ref" })}</dd></div>
        <div><dt>Candidate commit</dt><dd>${renderIdentifierCopy(checkout.candidate_commit_sha, { label: "Copy SHA" })}</dd></div>
        <div><dt>Candidate tree</dt><dd>${renderIdentifierCopy(checkout.candidate_tree_hash, { label: "Copy tree" })}</dd></div>
      </dl>
      <div class="release-candidate-checkout-command">
        <div class="delivery-detail-label">Supported host command</div>
        ${renderIdentifierCopy(checkout.checkout_command, { label: "Copy command" })}
      </div>
      <p class="meta-line">The target directory must be new. Existing or non-empty paths are never overwritten.</p>
    </div>
  `;
}

function renderReleaseDeliverableEvidence(rows, release) {
  if (!rows.length) {
    const deliverableIds = release.deliverable_ids || [];
    return deliverableIds.length
      ? `<div class="release-detail-section"><h4>Included work evidence</h4><ul class="delivery-detail-list">${deliverableIds.map((id) => {
          const deliverable = state.deliverables[id] || {};
          const task = deliverableTask(deliverable);
          return `<li><strong>${escapeHtml(task?.title || deliverableDisplaySubject(deliverable))}</strong>${deliverable.summary ? `<div>${escapeHtml(readablePreview(deliverable.summary, 320))}</div>` : ""}${renderTechnicalDetails([["Deliverable ID", id], ["Task ID", deliverable.task_id]], {
            disclosureKey: teamDisclosureKey("release-detail", release.id, "deliverable", id, "technical"),
          })}</li>`;
        }).join("")}</ul></div>`
      : "";
  }
  return `
    <div class="release-detail-section">
      <h4>Included work evidence</h4>
      <ul class="delivery-detail-list">
        ${rows.map((row) => {
          const deliverable = state.deliverables[row.deliverable_id] || {};
          const task = deliverableTask(deliverable);
          const reviews = row.accepted_reviews || [];
          const verification = row.verification || {};
          return `<li>
            <strong>${escapeHtml(task?.title || deliverableDisplaySubject(deliverable))}</strong>
            ${deliverable.summary ? `<div>${escapeHtml(readablePreview(deliverable.summary, 320))}</div>` : ""}
            <div class="meta-line">Verification ${escapeHtml(humanizeToken(verification.status || "not recorded"))}${reviews.length ? ` · ${reviews.length} accepted agent review${reviews.length === 1 ? "" : "s"}` : ""}</div>
            ${renderTechnicalDetails([
              ["Deliverable ID", row.deliverable_id],
              ["Task ID", deliverable.task_id],
              ["Feature branch", row.feature_branch],
              ["Feature commit", row.feature_commit_sha],
              ["Review IDs", reviews.map((review) => review.review_request_id)],
            ], {
              disclosureKey: teamDisclosureKey("release-detail", release.id, "deliverable", row.deliverable_id, "technical"),
            })}
          </li>`;
        }).join("")}
      </ul>
    </div>
  `;
}

function renderReleaseContributionGraph(graph, releaseId) {
  const rows = Object.entries(graph || {});
  if (!rows.length) {
    return "";
  }
  return `
    <div class="release-detail-section">
      <h4>Contribution lineage</h4>
      <ul class="delivery-detail-list">
        ${rows.map(([deliverableId, sourceIds]) => {
          const deliverable = state.deliverables?.[deliverableId];
          const sources = (sourceIds || []).map((sourceId) => state.deliverables?.[sourceId]).filter(Boolean);
          return `<li>
            <strong>${escapeHtml(deliverable ? deliverableDisplaySubject(deliverable) : "Included work")}</strong>
            <div class="meta-line">${sources.length ? `Built from ${escapeHtml(sources.map(deliverableDisplaySubject).join(", "))}` : "Direct contribution"}</div>
            ${renderTechnicalDetails([["Deliverable ID", deliverableId], ["Source deliverable IDs", sourceIds || []]], {
              disclosureKey: teamDisclosureKey("release-detail", releaseId, "contribution", deliverableId, "technical"),
            })}
          </li>`;
        }).join("")}
      </ul>
    </div>
  `;
}

function renderReleaseVerificationCommandResult(
  result,
  run,
  disclosureKey,
) {
  const failed = releaseVerificationCommandFailed(result);
  const decisiveError = failed
    ? releaseVerificationDecisiveError(result, run)
    : "";
  const metadata = [];
  if (result.exit_code !== null && result.exit_code !== undefined) {
    metadata.push(`Exit code: ${Number(result.exit_code)}`);
  }
  if (result.failure_cause) {
    metadata.push(`Failure cause: ${humanizeToken(result.failure_cause)}`);
  }
  if (result.duration_seconds !== null && result.duration_seconds !== undefined) {
    metadata.push(`Duration: ${Number(result.duration_seconds)} seconds`);
  }
  const logExcerpt = text(result.log_excerpt);
  return `
    <li class="release-verification-command-result${failed ? " release-verification-command-failed" : ""}">
      <div class="release-verification-command-heading">
        <strong>${failed ? "Failed command" : "Command"}: ${escapeHtml(result.name || "Unnamed command")}</strong>
        <span class="state-pill ${escapeAttr(result.state)}">${escapeHtml(humanizeToken(result.state))}</span>
      </div>
      ${metadata.length ? `<div class="meta-line">${escapeHtml(metadata.join(" · "))}</div>` : ""}
      ${decisiveError ? `<div class="release-verification-decisive-error"><strong>Decisive error:</strong> ${escapeHtml(decisiveError)}</div>` : ""}
      ${
        logExcerpt
          ? `<details class="release-verification-log" ${teamDisclosureAttributes(disclosureKey)}>
              <summary>Show captured command log</summary>
              <pre>${escapeHtml(logExcerpt)}</pre>
            </details>`
          : ""
      }
    </li>
  `;
}

function renderReleaseVerificationLiveOutput(run, result, index, releaseId) {
  if (text(run.state) !== "running" || text(result.state) !== "running") {
    return "";
  }
  const live = releaseVerificationLiveCache.get(text(releaseId));
  const liveRun = live?.payload?.run;
  const liveCommand = liveRun?.current_command;
  const matchesCurrentCommand = text(liveRun?.id) === text(run.id)
    && Number(liveCommand?.index) === index;
  if (!matchesCurrentCommand && !live?.loading && !live?.error) {
    return "";
  }
  const log = live?.payload?.log;
  const message = live?.error
    || log?.message
    || (live?.loading ? "Connecting to the live verifier output." : "Waiting for verifier output.");
  const logText = text(log?.text);
  return `
    <li class="release-verification-live-output">
      <details class="release-verification-log" open>
        <summary>Live verifier output · updates every 2 seconds</summary>
        ${log?.has_more_before ? `<p class="meta-line">Earlier log lines are omitted from this bounded tail.</p>` : ""}
        ${logText ? `<pre>${escapeHtml(logText)}</pre>` : `<p class="empty-state">${escapeHtml(message)}</p>`}
      </details>
    </li>
  `;
}

function renderReleaseVerificationCommandResults(run, disclosureKeyPrefix, releaseId) {
  const results = run.command_results || [];
  if (!results.length) {
    return "";
  }
  return `
    <ul class="release-verification-command-list">
      ${results.map((result, index) =>
        renderReleaseVerificationCommandResult(
          result,
          run,
          teamDisclosureKey(disclosureKeyPrefix, run.id, "command", String(index), "log"),
        ) + renderReleaseVerificationLiveOutput(run, result, index, releaseId)).join("")}
    </ul>
  `;
}

function renderReleaseVerificationRuns(runs, commands, releaseId) {
  if (!runs.length && !commands.length) {
    return "";
  }
  return `
    <div class="release-detail-section">
      <h4>Automated verification</h4>
      ${
        runs.length
          ? `<ul class="delivery-detail-list">${runs.map((run) => `<li>
              <strong>Verification attempt ${Number(run.attempt_number || 1)}</strong> <span class="state-pill ${escapeAttr(run.state)}">${escapeHtml(humanizeToken(run.state))}</span>
              ${run.failure_kind ? `<div class="meta-line">Failure type: ${escapeHtml(humanizeToken(run.failure_kind))}</div>` : ""}
              ${run.retry_reason ? `<div class="meta-line"><strong>Retry comment:</strong> ${escapeHtml(run.retry_reason)}</div>` : ""}
              ${run.retry_requested_by ? `<div class="meta-line">Requested by ${escapeHtml(run.retry_requested_by)}</div>` : ""}
              ${renderReleaseVerificationCommandResults(run, `release-detail:${releaseId}:verification`, releaseId)}
              ${renderTechnicalDetails([["Verification run ID", run.id], ["Candidate commit", run.candidate_commit_sha]], {
                disclosureKey: teamDisclosureKey("release-detail", releaseId, "verification-run", run.id, "technical"),
              })}
            </li>`).join("")}</ul>`
          : `<div class="meta-line">Configured commands: ${escapeHtml(commands.map((command) => command.name).join(", "))}</div>`
      }
    </div>
  `;
}

function renderReleaseAgentReviews(reviews, releaseId, cohort = null) {
  if (!reviews.length) {
    return "";
  }
  return `
    <div class="release-detail-section">
      <h4>Agent review decisions</h4>
      ${cohort ? `
        <div class="meta-line">
          <strong>Sealed cohort:</strong>
          ${escapeHtml(humanizeToken(cohort.status || "collecting"))} ·
          ${Number(cohort.resolved || 0)} of ${Number(cohort.total || reviews.length)} resolved.
          Correction work starts only after every cohort slot is accepted or rejected.
        </div>
      ` : ""}
      <ul class="delivery-detail-list">
        ${reviews.map((review) => {
          const reviewer = state.agents?.[review.reviewer_agent_id];
          return `<li>
          <strong>Review by ${escapeHtml(reviewer ? agentLabel(reviewer) : "Assigned reviewer")}</strong> <span class="state-pill ${escapeAttr(review.state)}">${escapeHtml(humanizeToken(review.state))}</span>
          ${review.instructions ? `<div class="meta-line">${escapeHtml(review.instructions)}</div>` : ""}
          ${renderReleaseReviewCriteria(review)}
          ${(review.findings || []).length ? `<div class="meta-line">Findings: ${escapeHtml(review.findings.join(", "))}</div>` : ""}
          ${renderTechnicalDetails([["Review ID", review.id], ["Review cohort ID", review.review_cohort_id], ["Reviewer ID", review.reviewer_agent_id], ["Reviewed commit", review.reviewed_commit_sha]], {
            disclosureKey: teamDisclosureKey("release-detail", releaseId, "agent-review", review.id, "technical"),
          })}
        </li>`;
        }).join("")}
      </ul>
    </div>
  `;
}

function renderReleaseReviewCriteria(review) {
  const snapshots = review.criteria_snapshot || [];
  const results = review.criteria || [];
  const resultsById = new Map(
    results.map((result) => [text(result.criterion_id || result.id), result]),
  );
  const rows = snapshots.length
    ? snapshots.map((criterion) => {
        const result = resultsById.get(text(criterion.id || criterion.criterion_id));
        return {
          label: criterion.label || criterion.criterion_id || criterion.id,
          state:
            result?.state ||
            criterion.state ||
            (criterion.required === false ? "optional" : "required"),
          finding: result?.finding || criterion.finding || "",
        };
      })
    : results.map((result) => ({
        label: result.label || result.criterion_id || result.id,
        state: result.state || "not recorded",
        finding: result.finding || "",
      }));
  if (!rows.length) {
    return "";
  }
  return `<ul class="inline-list">${rows.map((row) => `<li>${escapeHtml(row.label)}: ${escapeHtml(row.state)}${row.finding ? ` - ${escapeHtml(row.finding)}` : ""}</li>`).join("")}</ul>`;
}

function renderReleaseHumanRequests(requests, manifest, releaseId) {
  const carriedFindings = manifest.carried_human_findings || [];
  if (!requests.length) {
    return manifest.human_review_requirement
      ? `<div class="release-detail-section"><h4>Human acceptance</h4><p class="empty-state">Human review is required, but detailed requests are unavailable to this user or have not been created yet.</p></div>`
      : "";
  }
  return `
    <div class="release-detail-section">
      <h4>Human request and decision history</h4>
      ${carriedFindings.length ? `<div class="release-carried-findings"><strong>Carried findings requiring explicit confirmation:</strong> ${escapeHtml(carriedFindings.map((finding) => finding.finding_id || finding.id).join(", "))}</div>` : ""}
      <div class="human-review-request-list">
        ${requests.map((request) => renderHumanReviewRequest(request, carriedFindings, releaseId)).join("")}
      </div>
    </div>
  `;
}

function renderHumanReviewRequest(request, carriedFindings, releaseId = "") {
  const assignment = request.current_assignment || {};
  const target = request.test_target || null;
  const checklist = request.requirement_snapshot?.checklist || [];
  const decisions = request.decisions || [];
  const findings = request.findings || [];
  return `
    <article class="human-review-request${text(request.id) === focusedHumanReviewRequestId ? " human-review-request-focused" : ""}" id="human-review-${escapeAttr(request.id)}" tabindex="-1">
      <div class="record-line">
        <strong>Human acceptance request</strong>
        <span class="state-pill ${escapeAttr(request.state)}">${escapeHtml(request.state)}</span>
      </div>
      <div class="meta-line">${request.ready_at ? `Ready ${renderRecordDate(request.ready_at)}` : "Preparation pending"} · request revision ${Number(request.revision || 0)}</div>
      <div class="meta-line">Assigned reviewers: ${escapeHtml([...(assignment.explicitly_assigned_user_ids || []), ...(assignment.eligible_user_ids || [])].filter((value, index, values_) => values_.indexOf(value) === index).length || "none")}</div>
      <div class="meta-line">Preparation mode ${escapeHtml(humanizeToken(request.requirement_snapshot?.preparation_mode || "manual"))} · ${checklist.length} checklist item${checklist.length === 1 ? "" : "s"}</div>
      ${request.requirement_snapshot?.test_instructions ? `<div class="human-test-instructions"><strong>Test instructions:</strong> ${escapeHtml(request.requirement_snapshot.test_instructions)}</div>` : ""}
      ${renderHumanReviewBundleDownload(request)}
      ${renderHumanReviewTarget(target)}
      ${renderHumanChecklistHistory(checklist)}
      ${renderHumanDecisionHistory(decisions)}
      ${renderHumanFindingHistory(
        findings,
        request.correction_tasks || [],
        request.target_deliverable_options || [],
      )}
      ${request.state === "pending" ? renderHumanReviewDecisionForm(request, checklist, carriedFindings) : ""}
      ${renderHumanReviewOwnerActions(request)}
      ${renderTechnicalDetails([
        ["Human review request ID", request.id],
        ["Release ID", request.release_package_id],
        ["Candidate commit", request.candidate_commit_sha],
        ["Candidate tree", request.candidate_tree_hash],
      ], {
        disclosureKey: teamDisclosureKey("human-review", releaseId || request.release_package_id, request.id, "technical"),
      })}
    </article>
  `;
}

function renderHumanReviewBundleDownload(request) {
  const requestId = text(request.id);
  if (!requestId) {
    return "";
  }
  const href = `${API_BASE}/human-review-requests/${encodeURIComponent(requestId)}/review-bundle`;
  return `
    <div class="human-review-bundle">
      <a class="human-review-bundle-download" href="${escapeAttr(href)}" download>Download review bundle</a>
      <span class="meta-line">Source ZIP for this pinned candidate. Unpack it, then run the normal setup such as <code>dotnet restore</code> and <code>npm ci</code> where applicable.</span>
    </div>
  `;
}

function renderHumanReviewTarget(target) {
  if (!target) {
    return `<div class="meta-line">Preview/test target: manual instructions only or preparation still pending.</div>`;
  }
  const targetName = target.url || target.alias || "prepared target";
  return `
    <div class="human-review-target">
      <strong>Prepared test target:</strong> <code>${escapeHtml(targetName)}</code>
      ${target.expires_at ? `<div class="meta-line">Expires ${escapeHtml(target.expires_at)}</div>` : ""}
      ${target.instructions ? `<div>${escapeHtml(target.instructions)}</div>` : ""}
      ${(target.evidence_refs || []).length ? `<div class="meta-line">Target evidence: ${escapeHtml(target.evidence_refs.join(", "))}</div>` : ""}
    </div>
  `;
}

function renderHumanChecklistHistory(checklist) {
  if (!checklist.length) {
    return `<div class="meta-line">Human checklist: no configured items.</div>`;
  }
  return `<ul class="human-review-checklist">${checklist.map((item) => `<li><strong>${escapeHtml(item.label)}</strong> (${item.required === false ? "optional" : "required"})${item.description ? ` - ${escapeHtml(item.description)}` : ""}</li>`).join("")}</ul>`;
}

function renderHumanDecisionHistory(decisions) {
  if (!decisions.length) {
    return "";
  }
  return `<ul class="delivery-detail-list">${decisions.map((decision) => `<li>
    <strong>${escapeHtml(decision.decision)}</strong> by ${escapeHtml(decision.reviewer_display_name_snapshot || decision.reviewer_user_id)} at ${escapeHtml(decision.decided_at || "")}
    ${decision.summary ? `<div>${escapeHtml(decision.summary)}</div>` : ""}
    ${(decision.evidence_refs || []).length ? `<div class="meta-line">Evidence: ${escapeHtml(decision.evidence_refs.join(", "))}</div>` : ""}
  </li>`).join("")}</ul>`;
}

function humanReviewTargetOptionLabel(option) {
  const title =
    text(option?.task_title) ||
    text(option?.summary) ||
    text(option?.deliverable_id) ||
    "Included work";
  const producer =
    text(option?.producer?.display_name) ||
    text(option?.producer?.agent_id);
  const parts = [
    title,
    producer ? `by ${producer}` : "",
    option?.is_top_level ? "included deliverable" : "contribution",
  ].filter(Boolean);
  return parts.join(" · ");
}

function humanReviewTargetLabels(targetIds, targetOptions) {
  const optionsById = new Map(
    (targetOptions || []).map((option) => [text(option.deliverable_id), option]),
  );
  return (targetIds || []).map((deliverableId) => {
    const option = optionsById.get(text(deliverableId));
    return option
      ? humanReviewTargetOptionLabel(option)
      : text(deliverableId);
  });
}

function renderHumanFindingHistory(findings, correctionTasks, targetOptions = []) {
  if (!findings.length && !correctionTasks.length) {
    return "";
  }
  return `
    <div class="human-finding-history">
      <strong>Findings and correction work</strong>
      <ul class="delivery-detail-list">
        ${findings.map((finding) => `<li>
          <strong>${escapeHtml(finding.id)}</strong> <span class="state-pill ${escapeAttr(finding.state)}">${escapeHtml(finding.state)}</span> - ${escapeHtml(finding.text)}
          <div class="meta-line">Correction task ${escapeHtml(finding.correction_task_id)}${text(finding.target_scope) === "release_candidate" ? " · affected work: Entire release candidate" : finding.target_deliverable_ids?.length ? ` · affected work: ${escapeHtml(humanReviewTargetLabels(finding.target_deliverable_ids, targetOptions).join(", "))}` : ""}</div>
          ${finding.proposed_resolution ? `<div>Resolution proposal: ${escapeHtml(finding.proposed_resolution)}</div>` : ""}
          ${(finding.resolution_evidence_refs || []).length ? `<div class="meta-line">Resolution evidence: ${escapeHtml(finding.resolution_evidence_refs.join(", "))}</div>` : ""}
        </li>`).join("")}
        ${correctionTasks.map((task) => `<li><strong>${escapeHtml(task.id)}</strong> - ${escapeHtml(task.title)} <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(task.state)}</span>${task.originating_task_id ? ` - origin ${escapeHtml(task.originating_task_id)}` : ""}</li>`).join("")}
      </ul>
    </div>
  `;
}

function humanReviewFindingListFieldValue(value) {
  if (Array.isArray(value)) {
    return value.join("\n");
  }
  return value === null || value === undefined ? "" : String(value);
}

function renderHumanReviewTargetSelector(
  targetOptions,
  selectedTargetIds,
  selectedTargetScope,
) {
  const selected = new Set(splitList(selectedTargetIds));
  const scope = text(selectedTargetScope)
    || (selected.size ? "deliverables" : "");
  return `
    <div class="human-review-finding-targets" role="group" aria-label="Affected work">
      <span>Affected work</span>
      <div class="human-review-finding-target-options">
        <label class="checkbox-label">
          <input
            type="checkbox"
            name="human_finding_target_release_candidate"
            ${scope === "release_candidate" ? "checked" : ""}
          />
          <span>
            <strong>Entire release candidate</strong>
            <small>Route release-wide correction work to the project lead.</small>
          </span>
        </label>
        ${(targetOptions || []).map((option) => `
          <label class="checkbox-label">
            <input
              type="checkbox"
              name="human_finding_target_deliverable_ids"
              value="${escapeAttr(option.deliverable_id)}"
              ${selected.has(text(option.deliverable_id)) ? "checked" : ""}
            />
            <span>
              <strong>${escapeHtml(humanReviewTargetOptionLabel(option))}</strong>
              ${option.summary && text(option.summary) !== text(option.task_title) ? `<small>${escapeHtml(readablePreview(option.summary, 180))}</small>` : ""}
            </span>
          </label>
        `).join("")}
      </div>
    </div>
  `;
}

function renderHumanReviewFindingRow(finding = {}, targetOptions = []) {
  return `
    <div class="human-review-finding-row" data-human-review-finding-row>
      <div class="human-review-finding-row-heading">
        <strong data-human-review-finding-number>Finding</strong>
        <button
          class="record-action human-review-finding-remove"
          type="button"
          data-human-review-finding-remove
        >Remove</button>
      </div>
      <label class="human-review-finding-description">
        <span>Description</span>
        <textarea
          name="human_finding_text"
          rows="3"
          placeholder="Describe one concrete, actionable problem"
        >${escapeHtml(finding.text || "")}</textarea>
      </label>
      <div class="human-review-finding-metadata">
        ${renderHumanReviewTargetSelector(
          targetOptions,
          finding.target_deliverable_ids,
          finding.target_scope,
        )}
        <label>
          <span>Evidence references <small>(optional)</small></span>
          <textarea
            name="human_finding_evidence_refs"
            rows="2"
            placeholder="artifact-space:..., one per line"
          >${escapeHtml(humanReviewFindingListFieldValue(finding.evidence_refs))}</textarea>
        </label>
      </div>
    </div>
  `;
}

function renderHumanReviewFindingsEditor(targetOptions = []) {
  return `
    <div
      class="human-review-findings-editor"
      data-human-review-findings-editor
      data-human-review-target-options="${escapeAttr(JSON.stringify(targetOptions))}"
    >
      <div class="human-review-findings-heading">
        <strong>Findings</strong>
        <span>Required only when requesting changes.</span>
      </div>
      <div class="human-review-finding-rows" data-human-review-finding-rows></div>
      <p class="human-review-findings-empty" data-human-review-findings-empty>No findings added.</p>
      <button
        class="record-action human-review-finding-add"
        type="button"
        data-human-review-finding-add
      >Add finding</button>
    </div>
  `;
}

function humanReviewTargetOptionsFromEditor(editor) {
  try {
    const options = JSON.parse(editor?.dataset?.humanReviewTargetOptions || "[]");
    return Array.isArray(options) ? options : [];
  } catch {
    return [];
  }
}

function syncHumanReviewFindingEditor(editor) {
  if (!editor) {
    return;
  }
  const rows = Array.from(editor.querySelectorAll("[data-human-review-finding-row]"));
  rows.forEach((row, index) => {
    const number = row.querySelector("[data-human-review-finding-number]");
    if (number) {
      number.textContent = `Finding ${index + 1}`;
    }
    const remove = row.querySelector("[data-human-review-finding-remove]");
    if (remove) {
      remove.setAttribute("aria-label", `Remove finding ${index + 1}`);
    }
  });
  const empty = editor.querySelector("[data-human-review-findings-empty]");
  if (empty) {
    empty.hidden = rows.length > 0;
  }
}

function addHumanReviewFindingRow(editor, finding = {}) {
  const rows = editor?.querySelector?.("[data-human-review-finding-rows]");
  if (!rows) {
    return;
  }
  const wrapper = document.createElement("div");
  wrapper.innerHTML = renderHumanReviewFindingRow(
    finding,
    humanReviewTargetOptionsFromEditor(editor),
  );
  const row = wrapper.firstElementChild;
  if (!row) {
    return;
  }
  rows.appendChild(row);
  decorateFieldTooltips(row);
  syncHumanReviewFindingEditor(editor);
  rememberFormDraft(editor.closest("form"));
  row.querySelector('[name="human_finding_text"]')?.focus();
}

function removeHumanReviewFindingRow(button) {
  const editor = button.closest("[data-human-review-findings-editor]");
  button.closest("[data-human-review-finding-row]")?.remove();
  syncHumanReviewFindingEditor(editor);
  rememberFormDraft(editor?.closest("form"));
}

function humanReviewFindingFromRow(row) {
  const targetDeliverableIds = Array.from(
    row.querySelectorAll(
      'input[name="human_finding_target_deliverable_ids"]:checked',
    ),
  ).map((field) => text(field.value)).filter(Boolean);
  const targetsEntireRelease = Boolean(
    row.querySelector(
      'input[name="human_finding_target_release_candidate"]:checked',
    ),
  );
  return {
    text: text(row.querySelector('[name="human_finding_text"]')?.value),
    target_scope: targetsEntireRelease
      ? "release_candidate"
      : targetDeliverableIds.length
        ? "deliverables"
        : "",
    target_deliverable_ids: targetDeliverableIds,
    evidence_refs: splitList(
      row.querySelector('[name="human_finding_evidence_refs"]')?.value,
    ),
  };
}

function humanReviewFindingsFromForm(form, { validate = false } = {}) {
  const rows = Array.from(form.querySelectorAll("[data-human-review-finding-row]"));
  const findings = [];
  let valid = true;
  rows.forEach((row) => {
    const description = row.querySelector('[name="human_finding_text"]');
    const releaseTarget = row.querySelector(
      '[name="human_finding_target_release_candidate"]',
    );
    description?.setCustomValidity("");
    releaseTarget?.setCustomValidity("");
    const finding = humanReviewFindingFromRow(row);
    const hasMetadata =
      Boolean(finding.target_scope)
      || finding.target_deliverable_ids.length > 0
      || finding.evidence_refs.length > 0;
    if (!finding.text) {
      if (validate) {
        description?.setCustomValidity(
          hasMetadata
            ? "Describe the finding before attaching targets or evidence."
            : "Describe this finding or remove the empty row.",
        );
        valid = false;
      }
      return;
    }
    if (!finding.target_scope) {
      if (validate) {
        releaseTarget?.setCustomValidity(
          "Select the entire release candidate or at least one affected deliverable.",
        );
        valid = false;
      }
      return;
    }
    findings.push(finding);
  });
  if (validate && !rows.length) {
    setStatus("Request changes requires at least one actionable finding.", "error");
    form.querySelector("[data-human-review-finding-add]")?.focus();
    return null;
  }
  if (validate && !valid) {
    form.reportValidity();
    setStatus(
      "Complete or remove every added finding before requesting changes.",
      "error",
    );
    return null;
  }
  return findings;
}

function humanReviewFindingDraftFromRow(row) {
  const finding = humanReviewFindingFromRow(row);
  return {
    text: row.querySelector('[name="human_finding_text"]')?.value || "",
    target_scope: finding.target_scope,
    target_deliverable_ids: finding.target_deliverable_ids,
    evidence_refs:
      row.querySelector('[name="human_finding_evidence_refs"]')?.value || "",
  };
}

function humanReviewFindingsDraftFromForm(form) {
  const editor = form.querySelector("[data-human-review-findings-editor]");
  if (!editor) {
    return null;
  }
  return Array.from(editor.querySelectorAll("[data-human-review-finding-row]")).map(
    humanReviewFindingDraftFromRow,
  );
}

function restoreHumanReviewFindingsDraft(form, findings) {
  const editor = form.querySelector("[data-human-review-findings-editor]");
  const rows = editor?.querySelector?.("[data-human-review-finding-rows]");
  if (!rows || !Array.isArray(findings)) {
    return;
  }
  const targetOptions = humanReviewTargetOptionsFromEditor(editor);
  rows.innerHTML = findings
    .map((finding) => renderHumanReviewFindingRow(finding, targetOptions))
    .join("");
  decorateFieldTooltips(rows);
  syncHumanReviewFindingEditor(editor);
}

function renderHumanReviewDecisionForm(request, checklist, carriedFindings) {
  return `
    <form class="human-review-decision-form" data-human-review-decision-form data-form-id="human-review-decision-form" data-form-draft-scope="${escapeAttr(request.id)}" data-human-review-request-id="${escapeAttr(request.id)}" data-release-id="${escapeAttr(request.release_package_id)}" data-request-revision="${Number(request.revision || 0)}">
      <fieldset>
        <legend>Human acceptance checklist</legend>
        ${checklist.map((item) => `<label class="checkbox-label"><input type="checkbox" data-human-checklist-id="${escapeAttr(item.id)}"${item.required === false ? "" : " required"} /> ${escapeHtml(item.label)}${item.required === false ? " (optional)" : ""}</label>`).join("") || `<p class="empty-state">No checklist items.</p>`}
      </fieldset>
      ${
        carriedFindings.length
          ? `<fieldset><legend>Confirm carried findings</legend>${carriedFindings.map((finding) => `<label class="checkbox-label"><input type="checkbox" data-confirm-finding-id="${escapeAttr(finding.finding_id || finding.id)}" /> ${escapeHtml(finding.finding_id || finding.id)} - ${escapeHtml(finding.text || "")}</label>`).join("")}</fieldset>`
          : ""
      }
      <label>Decision summary<textarea name="summary" rows="3" placeholder="What was tested and why this decision is appropriate"></textarea></label>
      <label>Evidence references<textarea name="evidence_refs" rows="2" placeholder="artifact-space:..., one per line"></textarea></label>
      ${renderHumanReviewFindingsEditor(request.target_deliverable_options || [])}
      <div class="record-actions">
        <button type="submit" class="record-action human-review-approve" data-human-review-decision="approved">Approve</button>
        <button type="submit" class="record-action" data-human-review-decision="changes_requested" formnovalidate>Request changes</button>
        <button type="submit" class="record-action" data-human-review-decision="blocked" formnovalidate>Blocked</button>
      </div>
    </form>
  `;
}

function renderHumanReviewOwnerActions(request) {
  if (["approved", "changes_requested"].includes(text(request.state))) {
    return "";
  }
  return `
    <div class="record-actions human-review-owner-actions">
      ${request.state === "blocked" ? `<button type="button" class="record-action" data-human-review-resume-id="${escapeAttr(request.id)}">Resume unchanged candidate</button>` : ""}
      <button type="button" class="record-action" data-human-review-reassign-id="${escapeAttr(request.id)}">Reassign</button>
    </div>
  `;
}

function renderReleaseEvidence(release, deliverableRows, humanRequests) {
  const refs = new Set(release.artifact_refs || []);
  deliverableRows.forEach((row) => (row.artifact_refs || []).forEach((ref) => refs.add(ref)));
  humanRequests.forEach((request) => {
    (request.test_target?.evidence_refs || []).forEach((ref) => refs.add(ref));
    (request.decisions || []).forEach((decision) =>
      (decision.evidence_refs || []).forEach((ref) => refs.add(ref)),
    );
    (request.findings || []).forEach((finding) => {
      (finding.evidence_refs || []).forEach((ref) => refs.add(ref));
      (finding.resolution_evidence_refs || []).forEach((ref) => refs.add(ref));
    });
  });
  if (!refs.size) {
    return "";
  }
  return `<div class="release-detail-section"><h4>Evidence and attachments</h4><ul class="delivery-detail-list">${[...refs].sort().map((ref) => `<li><code>${escapeHtml(ref)}</code></li>`).join("")}</ul></div>`;
}

function humanReviewRequestById(requestId) {
  const queued = humanReviewQueue.requests.find((request) => text(request.id) === text(requestId));
  if (queued) {
    return queued;
  }
  for (const cached of releaseDetailCache.values()) {
    const request = cached.release?.human_review?.requests?.find(
      (item) => text(item.id) === text(requestId),
    );
    if (request) {
      return request;
    }
  }
  return null;
}

async function submitPostReleaseReport(form, submitter) {
  if (!guardTeamMutation("report post-release issues")) {
    return;
  }
  const releaseId = text(form.dataset.releaseId);
  const release = state.release_packages?.[releaseId];
  if (
    !releaseId ||
    text(release?.state) !== "accepted" ||
    (!text(release?.published_at) || !text(release?.published_commit_sha))
  ) {
    setStatus("Post-release reporting is only available for a published release.", "error");
    return;
  }
  const data = formData(form);
  const report = text(data.report);
  const evidenceRefs = splitList(data.evidence_refs);
  if (!report) {
    setStatus("A reason for the regression or adjustment is required.", "error");
    form.elements.report?.focus();
    return;
  }
  if (!evidenceRefs.length) {
    setStatus("At least one evidence reference is required.", "error");
    form.elements.evidence_refs?.focus();
    return;
  }
  if (submitter) {
    submitter.disabled = true;
  }
  setStatus(`Reporting follow-up for published release ${releaseId}.`, "loading");
  try {
    const response = await teamFetch(
      `${API_BASE}/release-packages/${encodeURIComponent(releaseId)}/report-issue`,
      {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({
          report,
          evidence_refs: evidenceRefs,
          request_id: teamUiMutationRequestId(releaseId, "report-issue"),
          expected_revision: Number(state.revision || 0),
        }),
      },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    clearFormDraft(form);
    form.reset();
    state.revision = payload.revision ?? state.revision;
    postReleaseReportResults.set(releaseId, {
      case: payload.case || null,
      triageTaskId: text(payload.result?.triage_task_id),
    });
    openTeamDisclosures.add(
      teamDisclosureStateKey(teamDisclosureKey("release-card", releaseId, "detail")),
    );
    releaseDetailCache.delete(releaseId);
    await loadView("delivery", { force: true, preserveForms: false });
    if (expandedReleaseDetailIds.has(releaseId)) {
      await loadReleaseDetail(releaseId);
    }
    renderReleases();
    setStatus(
      "Post-release follow-up recorded. The published release remains unchanged pending triage.",
      "success",
    );
  } catch (error) {
    setStatus(
      error instanceof Error ? error.message : "Post-release follow-up could not be recorded.",
      "error",
    );
  } finally {
    if (submitter) {
      submitter.disabled = false;
    }
  }
}

async function submitHumanReviewDecision(form, submitter) {
  if (!guardTeamMutation("record human review decisions")) {
    return;
  }
  const requestId = text(form.dataset.humanReviewRequestId);
  const releaseId = text(form.dataset.releaseId);
  const decision = text(submitter?.dataset?.humanReviewDecision);
  if (!requestId || !decision) {
    setStatus("Human review decision context is incomplete.", "error");
    return;
  }
  const checklistResults = {};
  form.querySelectorAll("[data-human-checklist-id]").forEach((input) => {
    checklistResults[text(input.dataset.humanChecklistId)] = Boolean(input.checked);
  });
  const confirmedFindingIds = [...form.querySelectorAll("[data-confirm-finding-id]:checked")]
    .map((input) => text(input.dataset.confirmFindingId))
    .filter(Boolean);
  const data = formData(form);
  let findings = [];
  if (decision === "changes_requested") {
    findings = humanReviewFindingsFromForm(form, { validate: true });
    if (!findings) {
      return;
    }
  }
  const payload = {
    decision,
    request_id: humanReviewMutationRequestId(requestId, decision),
    expected_revision: Number(form.dataset.requestRevision || 0),
    checklist_results: checklistResults,
    confirmed_finding_ids: confirmedFindingIds,
    summary: text(data.summary),
    evidence_refs: splitList(data.evidence_refs),
  };
  if (decision === "changes_requested") {
    payload.findings = findings;
  }
  await mutateHumanReview(
    submitter,
    `/human-review-requests/${encodeURIComponent(requestId)}/decisions`,
    payload,
    { requestId, releaseId, successMessage: `Human review ${decision.replaceAll("_", " ")} recorded.` },
  );
}

async function resumeHumanReview(button, requestId) {
  const request = humanReviewRequestById(requestId);
  if (!request) {
    setStatus("Human review request is no longer available.", "error");
    return;
  }
  await mutateHumanReview(
    button,
    `/human-review-requests/${encodeURIComponent(requestId)}/resume`,
    {
      request_id: humanReviewMutationRequestId(requestId, "resume"),
      expected_revision: Number(request.revision || 0),
    },
    {
      requestId,
      releaseId: request.release_package_id,
      successMessage: "Human review resumed against the unchanged candidate.",
    },
  );
}

async function reassignHumanReview(button, requestId, userIds) {
  const request = humanReviewRequestById(requestId);
  if (!request) {
    setStatus("Human review request is no longer available.", "error");
    return;
  }
  if (!userIds.length) {
    setStatus("Reassignment requires at least one Admin Web user ID.", "error");
    return;
  }
  await mutateHumanReview(
    button,
    `/human-review-requests/${encodeURIComponent(requestId)}/reassign`,
    {
      request_id: humanReviewMutationRequestId(requestId, "reassign"),
      expected_revision: Number(request.revision || 0),
      user_ids: userIds,
    },
    {
      requestId,
      releaseId: request.release_package_id,
      successMessage: "Human review reassigned.",
    },
  );
}

async function mutateHumanReview(control, endpoint, payload, { requestId, releaseId, successMessage }) {
  if (control) {
    control.disabled = true;
  }
  setStatus("Saving human review action.", "loading");
  try {
    const response = await teamFetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const result = await response.json();
    const decisionForm = control?.closest?.("[data-human-review-decision-form]");
    if (decisionForm) {
      clearFormDraft(decisionForm);
      decisionForm.reset();
    }
    state.revision = result.revision ?? state.revision;
    const updatedRequest = result.human_review_request;
    if (updatedRequest) {
      humanReviewQueue.requests = humanReviewQueue.requests.map((request) =>
        text(request.id) === text(requestId) ? updatedRequest : request,
      );
    }
    releaseDetailCache.delete(text(releaseId));
    await Promise.all([
      loadView("delivery", { force: true, preserveForms: false }),
      loadMyHumanReviews(),
    ]);
    if (expandedReleaseDetailIds.has(text(releaseId))) {
      await loadReleaseDetail(text(releaseId));
    }
    setStatus(successMessage, "success");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Human review action failed.", "error");
  } finally {
    if (control) {
      control.disabled = false;
    }
  }
}

function teamUiMutationRequestId(scopeId, action) {
  const randomPart =
    typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `team-ui:${scopeId}:${action}:${randomPart}`;
}

function humanReviewMutationRequestId(requestId, action) {
  return teamUiMutationRequestId(requestId, action);
}

function renderReleasePolicyDetails(release) {
  const waivers = release.verification_waivers || [];
  const scope = release.scope_validation || null;
  const waiverMarkup = waivers.length
    ? `<div class="meta-line"><strong>Verification waivers:</strong><ul>${waivers
        .map((waiver) => `<li>${escapeHtml(waiver.deliverable_id)} (${escapeHtml(waiver.verification_status)}): ${escapeHtml(waiver.reason)} - ${escapeHtml(waiver.actor_kind)} ${escapeHtml(waiver.actor_id)}</li>`)
        .join("")}</ul></div>`
    : "";
  const scopeMarkup = scope
    ? `<div class="meta-line">Scope validation: ${escapeHtml(scope.state)}${(scope.unexpected_commit_shas || []).length ? ` - unexpected commits ${escapeHtml(scope.unexpected_commit_shas.join(", "))}` : ""}${(scope.unexpected_paths || []).length ? ` - unexpected paths ${escapeHtml(scope.unexpected_paths.join(", "))}` : ""}</div>`
    : "";
  const humanRequirement = release.manifest?.human_review_requirement;
  const humanMarkup = humanRequirement
    ? `<div class="meta-line">Human gate: ${escapeHtml(humanRequirement.preparation_mode || "manual")} - groups ${escapeHtml((humanRequirement.configured_group_ids || []).join(", ") || "redacted")} - ${(humanRequirement.checklist || []).length} checklist item${(humanRequirement.checklist || []).length === 1 ? "" : "s"}</div>`
    : "";
  return `${waiverMarkup}${scopeMarkup}${humanMarkup}`;
}

function releaseHasSuccessor(releaseId) {
  const normalizedReleaseId = text(releaseId);
  return Boolean(normalizedReleaseId) && values(state.release_packages).some(
    (candidate) => text(candidate?.supersedes_release_package_id) === normalizedReleaseId,
  );
}

function renderReleaseGateAction(release) {
  const failure = release.gate_failure || null;
  const gateId = text(failure?.source_id);
  const retryableVerification =
    release.state === "blocked" &&
    failure?.stage === "verification" &&
    ["infrastructure", "timeout"].includes(failure?.kind);
  const retryableReview =
    release.state === "blocked" &&
    failure?.stage === "review" &&
    failure?.kind === "blocked";
  const rejectedVerificationRetry =
    release.state === "rejected" &&
    failure?.stage === "verification" &&
    failure?.kind === "candidate" &&
    !releaseHasSuccessor(release.id);
  const rejectedReviewException =
    release.state === "rejected" &&
    failure?.stage === "review" &&
    failure?.kind === "rejected" &&
    !releaseHasSuccessor(release.id);
  if (!gateId || !(
    retryableVerification || retryableReview || rejectedReviewException || rejectedVerificationRetry
  )) {
    return "";
  }
  const gateKind = retryableVerification || rejectedVerificationRetry ? "verification" : "review";
  const label = rejectedReviewException
    ? "Retry rejected review by exception"
    : `Retry ${gateKind} gate`;
  return `
    <div class="record-actions">
      <button
        class="record-action"
        type="button"
        data-release-gate-retry
        data-release-id="${escapeAttr(release.id)}"
        data-gate-kind="${escapeAttr(gateKind)}"
        data-gate-id="${escapeAttr(gateId)}"
        data-rejected-review-exception="${rejectedReviewException ? "true" : "false"}"
        data-rejected-verification-retry="${rejectedVerificationRetry ? "true" : "false"}"
      >${escapeHtml(label)}</button>
    </div>
  `;
}

async function retryReleaseGate(button, reason) {
  const releaseId = text(button?.dataset?.releaseId);
  const gateKind = text(button?.dataset?.gateKind);
  const gateId = text(button?.dataset?.gateId);
  const rejectedReviewException = button?.dataset?.rejectedReviewException === "true";
  const rejectedVerificationRetry = button?.dataset?.rejectedVerificationRetry === "true";
  if (!releaseId || !gateKind || !gateId) {
    setStatus("Release gate retry context is incomplete.", "error");
    return;
  }
  button.disabled = true;
  setStatus(`Retrying ${gateKind} gate.`, "loading");
  try {
    const response = await teamFetch(
      `${API_BASE}/release-packages/${encodeURIComponent(releaseId)}/retry-gate`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify({
          gate_kind: gateKind,
          gate_id: gateId,
          reason,
          ...(rejectedReviewException ? { allow_rejected_review_exception: true } : {}),
          ...(rejectedVerificationRetry ? { allow_rejected_verification_retry: true } : {}),
          expected_revision: state.revision,
        }),
      },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const result = await response.json();
    await applyMutationResultPayload(result);
    setStatus(`Release ${releaseId} ${gateKind} retry recorded.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Release gate retry failed.", "error");
  } finally {
    button.disabled = false;
  }
}

function renderTemplateSetup() {
  fillTemplateSelect();
  renderTemplateSetupFields();
}

function fillTemplateSelect() {
  if (!elements.templateSelect) {
    return;
  }
  const currentValue = elements.templateSelect.value;
  const options = templates.map((template) => {
    return [template.id, `${template.name} (${template.id})`];
  });
  elements.templateSelect.innerHTML = options.length
    ? options
        .map(([value, label]) => `<option value="${escapeAttr(value)}">${escapeHtml(label)}</option>`)
        .join("")
    : `<option value="">No templates available</option>`;
  elements.templateSelect.disabled = !options.length;
  if (options.some(([value]) => value === currentValue)) {
    elements.templateSelect.value = currentValue;
  } else if (options.length) {
    elements.templateSelect.value = options[0][0];
  }
}

function renderTemplateSetupFields({ preserveValues = true } = {}) {
  const form = document.querySelector("#setup-template-form");
  const template = selectedTemplate();
  const previousValues = preserveValues && form ? formData(form) : {};
  renderTemplateRoleFields(template, previousValues);
  renderTemplateGitFields(template);
  if (elements.templateRoleFields) {
    fillSelectsIn(elements.templateRoleFields);
    decorateFieldTooltips(elements.templateRoleFields);
  }
  renderTemplatePreview();
}

function renderTemplateRoleFields(template, previousValues = {}) {
  if (!elements.templateRoleFields) {
    return;
  }
  if (!template) {
    elements.templateRoleFields.innerHTML = "";
    return;
  }

  elements.templateRoleFields.innerHTML = `
    <p class="subheading">Roles</p>
    ${template.roles.map((role) => renderTemplateRoleSessionFields(role, previousValues)).join("")}
  `;
}

function renderTemplateRoleSessionFields(role, previousValues) {
  const roleKey = text(role.key);
  const existingAgentField = `${roleKey}_existing_agent_id`;
  const sessionField = `${roleKey}_session_id`;
  const sessionValue = text(previousValues[sessionField]);
  const existingAgentValue = text(previousValues[existingAgentField]);
  const roleLabel = text(role.label) || humanizeToken(roleKey);
  const suggestedRole = roleKey.replaceAll("_", "-");
  return `
    <fieldset class="template-role-fieldset" data-template-role-key="${escapeAttr(roleKey)}">
      <legend>${escapeHtml(roleLabel)}</legend>
      <label>
        Existing agent
        <select
          name="${escapeAttr(existingAgentField)}"
          data-options="agents"
          data-placeholder="Select existing agent"
          data-agent-source-picker
          data-session-field="${escapeAttr(sessionField)}"
          data-suggested-role="${escapeAttr(suggestedRole)}"
          data-current-value="${escapeAttr(existingAgentValue)}"
          data-tooltip-key="template_existing_agent_id"
        ></select>
      </label>
      <label>Session ID <input name="${escapeAttr(sessionField)}" value="${escapeAttr(sessionValue)}" required data-template-session-field data-tooltip-key="template_session_id" /></label>
    </fieldset>
  `;
}

function renderTemplateGitFields(template) {
  if (!elements.templateGitFields) {
    return;
  }
  elements.templateGitFields.hidden = !templateUsesGit(template);
}

function templateUsesGit(template) {
  return (template?.required_fields || []).some((field) => text(field.path).startsWith("project.git."));
}

function renderTemplatePreview() {
  if (!elements.templatePreview) {
    return;
  }
  const template = selectedTemplate();
  if (!template) {
    elements.templatePreview.innerHTML = `<p class="empty-state">No templates available.</p>`;
    return;
  }

  const leadRole = template.roles.find((role) => role.key === "project_lead");
  const leafRoles = template.roles
    .filter((role) => role.reports_to_role_key === (leadRole?.key || template.root_role_key))
    .map((role) => role.key)
    .join("/");
  const hierarchy = leadRole && leafRoles
    ? `${template.root_role_key} -> ${leadRole.key} -> ${leafRoles}`
    : template.root_role_key;

  elements.templatePreview.innerHTML = `
    <div class="template-preview-summary">
      <div class="meta-line">${escapeHtml(template.description)}</div>
      <div class="meta-line">Hierarchy: ${escapeHtml(hierarchy)}</div>
      <div class="meta-line">Required roles: ${escapeHtml(template.required_role_keys.join(", "))}</div>
    </div>

    <section class="template-preview-section">
      <h3>Planned roles</h3>
      <ul class="template-record-list">
        ${template.roles
          .map((role) => {
            const reportsTo = role.reports_to_role_key ? ` reports to ${role.reports_to_role_key}` : "";
            return `
              <li class="record-item">
                <div class="record-line">
                  <strong>${escapeHtml(role.label)}</strong>
                  <span class="state-pill">${escapeHtml(role.key)}</span>
                </div>
                <div class="meta-line">${escapeHtml(role.description)}${escapeHtml(reportsTo)}</div>
                <div class="meta-line">Max concurrent ephemeral agents: ${role.ephemeral_policy.max_concurrent}</div>
              </li>
            `;
          })
          .join("")}
      </ul>
    </section>

    <section class="template-preview-section">
      <h3>Required fields</h3>
      <ul class="template-record-list">
        ${template.required_fields
          .map((field) => {
            const required = field.required === false ? "optional" : "required";
            const example = field.example ? `Example: ${escapeHtml(field.example)}` : "";
            return `
              <li class="record-item">
                <div class="record-line">
                  <strong>${escapeHtml(field.label)}</strong>
                  <span class="state-pill ${field.required === false ? "blocked" : "accepted"}">${escapeHtml(required)}</span>
                </div>
                <div class="meta-line">${escapeHtml(field.path)}</div>
                ${field.description ? `<div class="meta-line">${escapeHtml(field.description)}</div>` : ""}
                ${example ? `<div class="meta-line">${example}</div>` : ""}
              </li>
            `;
          })
          .join("")}
      </ul>
    </section>

    <section class="template-preview-section">
      <h3>Project goals</h3>
      <ul class="template-record-list">
        ${template.project_goals
          .map((goal) => {
            return `
              <li class="record-item">
                <div class="record-line">
                  <strong>${escapeHtml(goal.description)}</strong>
                  <span class="state-pill ${escapeHtml(goal.state)}">${escapeHtml(goal.state)}</span>
                </div>
                <div class="meta-line">${escapeHtml(goal.id)}</div>
              </li>
            `;
          })
          .join("")}
      </ul>
    </section>

    <section class="template-preview-section">
      <h3>Initial tasks</h3>
      <ul class="template-record-list">
        ${template.tasks
          .map((task) => {
            const dependencyLine = (task.dependency_task_ids || []).length
              ? `<div class="meta-line">Depends on ${task.dependency_task_ids
                  .map((dependencyId) => escapeHtml(dependencyId))
                  .join(", ")}</div>`
              : "";
            return `
              <li class="record-item">
                <div class="record-line">
                  <strong>${escapeHtml(task.title)}</strong>
                  <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(taskStateLabel(task))}</span>
                </div>
                <div class="meta-line">${escapeHtml(task.id)} - owner ${escapeHtml(task.owner_role_key)} - creator ${escapeHtml(task.created_by_role_key)} - priority ${task.priority}</div>
                ${dependencyLine}
                <div class="meta-line">${escapeHtml(task.description)}</div>
              </li>
            `;
          })
          .join("")}
      </ul>
    </section>

    <section class="template-preview-section">
      <h3>Review profile</h3>
      <ul class="template-record-list">
        ${template.review_profiles
          .map((profile) => {
            return `
              <li class="record-item">
                <div class="record-line">
                  <strong>${escapeHtml(profile.name)}</strong>
                  <span class="state-pill">${escapeHtml(profile.id)}</span>
                </div>
                <div class="meta-line">${escapeHtml(profile.description)}</div>
                <div class="meta-line">Applicable roles: ${escapeHtml((profile.applicable_role_keys || []).join(", ") || "all reviewer roles")}</div>
                ${renderTemplateCriteria(profile.criteria || [])}
              </li>
            `;
          })
          .join("")}
      </ul>
    </section>

      ${
        templateUsesGit(template)
          ? `<section class="template-preview-section">
              <h3>Git config</h3>
              <div class="meta-line">Git is optional. If used, provide a host repo path and a bare repo value.</div>
              <div class="meta-line">Bare repo values that start with / are treated as paths; other values are treated as names.</div>
            </section>`
          : ""
      }
    `;
}

function renderTemplateCriteria(criteria) {
  if (!criteria.length) {
    return "";
  }
  return `
    <ul class="inline-list">
      ${criteria
        .map((criterion) => {
          const required = criterion.required === false ? "optional" : "required";
          const description = criterion.description ? ` - ${escapeHtml(criterion.description)}` : "";
          return `<li>${escapeHtml(criterion.id)}: ${escapeHtml(criterion.label)} (${required})${description}</li>`;
        })
        .join("")}
    </ul>
  `;
}

function bindTimelineFilters() {
  document.querySelectorAll("[data-timeline-filter]").forEach((filter) => {
    filter.addEventListener("change", () => {
      syncTimelineFilterControls(filter.dataset.timelineFilter, filter.value);
      timelinePage = 1;
      void loadTimeline();
    });
  });
}

function bindTimelinePagination() {
  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-timeline-page-action]");
    if (!button) {
      return;
    }
    const action = button.dataset.timelinePageAction;
    if (action === "next") {
      timelinePage += 1;
    } else if (action === "previous") {
      timelinePage = Math.max(1, timelinePage - 1);
    } else {
      return;
    }
    void loadTimeline();
  });
}

function syncTimelineFilterControls(filterKey, value) {
  if (!filterKey) {
    return;
  }
  document.querySelectorAll(`[data-timeline-filter="${filterKey}"]`).forEach((filter) => {
    if (filter.value !== value) {
      filter.value = value;
    }
  });
}

async function loadTimeline() {
  if (!document.querySelector("[data-timeline-view]")) {
    return;
  }
  const requestId = ++timelineRequestId;
  const search = timelineSearchParams();

  timelineLoading = true;
  setTimelineStatus("Loading activity timeline.", "loading");

  try {
    const response = await teamFetch(
      `${API_BASE}/timeline${search.toString() ? `?${search.toString()}` : ""}`,
      {
        headers: { Accept: "application/json" },
      },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    if (requestId !== timelineRequestId) {
      return;
    }
    timelineState = normalizeTimeline(payload);
    if (
      activeTeamView === "timeline" &&
      Number(timelineState.total_count || 0) > 0 &&
      Number(timelineState.offset || 0) >= Number(timelineState.total_count || 0)
    ) {
      timelinePage = Math.max(1, Math.ceil(Number(timelineState.total_count || 0) / timelinePageSize));
      void loadTimeline();
      return;
    }
    timelineLoading = false;
    refreshSelects();
    renderTimeline();
    setTimelineStatus(describeTimelineStatus(timelineState), "ok");
  } catch (error) {
    if (requestId !== timelineRequestId) {
      return;
    }
    timelineLoading = false;
    setTimelineStatus(error instanceof Error ? error.message : "Timeline failed to load.", "error");
    renderTimeline();
  }
}

function timelineSearchParams() {
  const filters = readTimelineFilters();
  const search = new URLSearchParams();
  if (filters.team_id) {
    search.set("team_id", filters.team_id);
  }
  if (filters.project_id) {
    search.set("project_id", filters.project_id);
  }
  if (filters.agent_id) {
    search.set("agent_id", filters.agent_id);
  }
  if (filters.type) {
    search.set("type", filters.type);
  }
  if (filters.status) {
    search.set("status", filters.status);
  }
  if (filters.attention_needed !== null && filters.attention_needed !== undefined) {
    search.set("attention_needed", filters.attention_needed ? "true" : "false");
  }
  search.set("limit", String(timelinePageSize));
  search.set("offset", String(Math.max(0, (Math.max(1, timelinePage) - 1) * timelinePageSize)));
  return search;
}

function normalizeTimeline(payload) {
  const entries = Array.isArray(payload?.entries)
    ? payload.entries.slice().sort(compareTimelineEntriesNewestFirst)
    : [];
  return {
    ...emptyTimelineState(),
    ...(payload || {}),
    entries,
  };
}

function compareTimelineEntriesNewestFirst(left, right) {
  const leftTime = timelineEntrySortTime(left);
  const rightTime = timelineEntrySortTime(right);
  if (leftTime !== rightTime) {
    return rightTime - leftTime;
  }
  return (
    String(left.category || "").localeCompare(String(right.category || "")) ||
    String(left.type || "").localeCompare(String(right.type || "")) ||
    String(left.id || "").localeCompare(String(right.id || ""))
  );
}

function timelineEntrySortTime(entry) {
  const timestamp = entry?.timestamp;
  if (!timestamp) {
    return Number.NEGATIVE_INFINITY;
  }
  const parsed = Date.parse(normalizeTimelineTimestampForSort(timestamp));
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function normalizeTimelineTimestampForSort(timestamp) {
  const text = String(timestamp || "").trim();
  if (!text) {
    return text;
  }
  // The API treats legacy offset-naive ISO datetimes as UTC when sorting.
  if (isOffsetNaiveIsoDateTime(text)) {
    return `${text}Z`;
  }
  return text;
}

function isOffsetNaiveIsoDateTime(timestamp) {
  return (
    /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(timestamp) &&
    !/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(timestamp)
  );
}

function renderTimeline() {
  if (!document.querySelector("[data-timeline-view]")) {
    return;
  }

  const entries = (timelineState.entries || []).slice().sort(compareTimelineEntriesNewestFirst);
  const totalCount = Number.isFinite(Number(timelineState.total_count))
    ? Number(timelineState.total_count)
    : entries.length;
  const limit = Number.isFinite(Number(timelineState.limit)) && Number(timelineState.limit) > 0
    ? Number(timelineState.limit)
    : timelinePageSize;
  let pageWasClamped = false;
  if (activeTeamView === "timeline") {
    const pageCount = Math.max(1, Math.ceil(totalCount / limit));
    const nextPage = Math.min(Math.max(1, timelinePage), pageCount);
    pageWasClamped = nextPage !== timelinePage;
    timelinePage = nextPage;
    refetchTimelineAfterPageClamp({ pageWasClamped, totalCount, entries });
  }
  const summaryParts = [`${totalCount} ${totalCount === 1 ? "entry" : "entries"}`];
  if (timelineState.timestampless_count) {
    summaryParts.push(`${timelineState.timestampless_count} timestamp unavailable`);
  }
  if (activeTeamView === "timeline" && totalCount > limit) {
    summaryParts.push(`page ${timelinePage} of ${Math.ceil(totalCount / limit)}`);
  }
  document.querySelectorAll("[data-timeline-summary]").forEach((summary) => {
    summary.textContent = targetIsActive(summary) ? summaryParts.join(" · ") : "";
  });

  if (!entries.length) {
    renderIntoActiveTargets(
      "[data-timeline-view]",
      timelineLoading
        ? `<p class="timeline-empty">Loading timeline entries.</p>`
        : `<p class="timeline-empty">No timeline entries match these filters.</p>`,
    );
    return;
  }

  if (activeTeamView === "timeline") {
    renderFocusedTimeline(entries);
    return;
  }

  renderIntoActiveTargets("[data-timeline-view]", `
    <ul class="timeline-list">
      ${entries.map((entry) => renderTimelineEntry(entry)).join("")}
    </ul>
  `);
}

function refetchTimelineAfterPageClamp({ pageWasClamped, totalCount, entries }) {
  if (!pageWasClamped || timelineLoading || totalCount <= 0 || entries.length) {
    return;
  }
  timelineLoading = true;
  setTimelineStatus("Loading activity timeline.", "loading");
  void loadTimeline();
}

function renderFocusedTimeline(entries) {
  const totalCount = Number.isFinite(Number(timelineState.total_count))
    ? Number(timelineState.total_count)
    : entries.length;
  const limit = Number.isFinite(Number(timelineState.limit)) && Number(timelineState.limit) > 0
    ? Number(timelineState.limit)
    : timelinePageSize;
  const offset = Number.isFinite(Number(timelineState.offset))
    ? Number(timelineState.offset)
    : (Math.max(1, timelinePage) - 1) * limit;
  const pageCount = Math.max(1, Math.ceil(totalCount / limit));
  timelinePage = Math.min(Math.max(1, timelinePage), pageCount);
  const pageEntries = entries;
  const pagination = totalCount > limit
    ? renderTimelinePagination(pageCount, offset, pageEntries.length, totalCount)
    : "";

  renderIntoActiveTargets("[data-timeline-view]", `
    ${pagination}
    <ul class="timeline-list timeline-list-compact">
      ${pageEntries.map((entry) => renderCompactTimelineEntry(entry)).join("")}
    </ul>
  `);
}

function renderTimelinePagination(pageCount, start, pageLength, total) {
  const first = start + 1;
  const last = start + pageLength;
  return `
    <div class="timeline-pagination" aria-label="Activity timeline pages">
      <button
        type="button"
        data-timeline-page-action="previous"
        ${timelinePage <= 1 ? "disabled" : ""}
      >Previous</button>
      <span>${first}-${last} of ${total}</span>
      <button
        type="button"
        data-timeline-page-action="next"
        ${timelinePage >= pageCount ? "disabled" : ""}
      >Next</button>
    </div>
  `;
}

function selectedTemplate() {
  const selectedId = text(elements.templateSelect?.value);
  if (selectedId) {
    return templates.find((template) => template.id === selectedId) || null;
  }
  return templates[0] || null;
}

function buildReviewDeliveryRows() {
  const taskByDeliverable = new Map();
  const projectByTask = new Map();
  const teamByProject = new Map();
  const reviewsByDeliverable = new Map();
  const releasePackagesByDeliverable = new Map();
  const gitPromotionsById = new Map();

  values(state.tasks).forEach((task) => {
    taskByDeliverable.set(task.id, task);
    projectByTask.set(task.id, state.projects[task.project_id] || null);
  });

  values(state.projects).forEach((project) => {
    teamByProject.set(project.id, state.teams[project.team_id] || null);
  });

  values(state.review_requests).forEach((review) => {
    const reviews = reviewsByDeliverable.get(review.deliverable_id) || [];
    reviews.push(review);
    reviewsByDeliverable.set(review.deliverable_id, reviews);
  });

  values(state.release_packages).forEach((release) => {
    (release.deliverable_ids || []).forEach((deliverableId) => {
      const releasePackages = releasePackagesByDeliverable.get(deliverableId) || [];
      releasePackages.push(release);
      releasePackagesByDeliverable.set(deliverableId, releasePackages);
    });
  });

  values(state.git_promotions).forEach((promotion) => {
    gitPromotionsById.set(promotion.id, promotion);
  });

  return values(state.deliverables)
    .map((deliverable) => {
      const task = taskByDeliverable.get(deliverable.task_id) || null;
      const project = task ? projectByTask.get(task.id) || null : null;
      const team = project ? teamByProject.get(project.id) || null : null;
      const reviewRequests = (reviewsByDeliverable.get(deliverable.id) || [])
        .slice()
        .sort(compareReviewRequestsLatest);
      const releasePackages = (releasePackagesByDeliverable.get(deliverable.id) || [])
        .slice()
        .sort(compareProjectReleasesLatest);
      const gitPromotionIds = [
        ...(deliverable.git_refs?.promotion_record_ids || []),
        ...releasePackages.flatMap((release) => release.git_promotion_record_ids || []),
      ];
      const gitPromotions = uniqueTextList(gitPromotionIds)
        .map((promotionId) => gitPromotionsById.get(promotionId))
        .filter(Boolean);
      const effectiveStatus = deriveDeliveryStatus(
        deliverable,
        task,
        reviewRequests,
        releasePackages,
        gitPromotions,
      );
      return {
        deliverable,
        task,
        project,
        team,
        reviewRequests,
        releasePackages,
        gitPromotions,
        effectiveStatus,
      };
    })
    .filter((row) => showArchived || (!isArchivedTeam(row.team) && !isArchivedProject(row.project)))
    .sort(compareReviewDeliveryRows);
}

function compareReviewDeliveryRows(left, right) {
  return (
    deliveryStatusRank(left.effectiveStatus) - deliveryStatusRank(right.effectiveStatus) ||
    teamSortKey(left.team).localeCompare(teamSortKey(right.team)) ||
    projectSortKey(left.project).localeCompare(projectSortKey(right.project)) ||
    byId(left.deliverable, right.deliverable)
  );
}

function deliveryStatusRank(status) {
  const index = deliveryStatusOrder.indexOf(status);
  return index === -1 ? deliveryStatusOrder.length : index;
}

function teamSortKey(team) {
  return String(team?.name || team?.id || "unknown-team");
}

function projectSortKey(project) {
  return String(project?.name || project?.id || "unknown-project");
}

function groupReviewDeliveryRows(rows) {
  const teamGroups = new Map();

  rows.forEach((row) => {
    const teamId = row.team?.id || "__unknown_team__";
    const projectId = row.project?.id || "__unknown_project__";
    let teamGroup = teamGroups.get(teamId);
    if (!teamGroup) {
      teamGroup = {
        team: row.team || null,
        projects: new Map(),
      };
      teamGroups.set(teamId, teamGroup);
    }
    let projectGroup = teamGroup.projects.get(projectId);
    if (!projectGroup) {
      projectGroup = {
        project: row.project || null,
        rows: [],
      };
      teamGroup.projects.set(projectId, projectGroup);
    }
    projectGroup.rows.push(row);
  });

  return Array.from(teamGroups.values())
    .sort((left, right) => teamSortKey(left.team).localeCompare(teamSortKey(right.team)))
    .map((teamGroup) => {
      return {
        team: teamGroup.team,
        projects: Array.from(teamGroup.projects.values())
          .sort((left, right) => projectSortKey(left.project).localeCompare(projectSortKey(right.project)))
          .map((projectGroup) => ({
            project: projectGroup.project,
            rows: projectGroup.rows.sort(compareReviewDeliveryRows),
          })),
      };
    });
}

function renderDeliveryTeamGroup(teamGroup, bucketStatuses) {
  const teamLabel = teamGroup.team
    ? escapeHtml(teamGroup.team.name)
    : "Unknown team";
  const visibleRowCount = teamGroup.projects.reduce((count, projectGroup) => {
    return count + projectGroup.rows.length;
  }, 0);
  return `
    <section class="delivery-team">
      <div class="delivery-team-title">
        <div>
          <p class="section-label">Team</p>
          <h3>${teamLabel}</h3>
          ${teamGroup.team ? renderTeamCostCounter(teamGroup.team.id) : ""}
          ${teamGroup.team ? renderTeamCostBlockedNotice(teamGroup.team.id) : ""}
        </div>
        <span class="delivery-bucket-count">${visibleRowCount} deliverable${visibleRowCount === 1 ? "" : "s"}</span>
      </div>
      <div class="delivery-project-list">
        ${teamGroup.projects.map((projectGroup) => renderDeliveryProjectGroup(projectGroup, bucketStatuses)).join("")}
      </div>
    </section>
  `;
}

function renderDeliveryProjectGroup(projectGroup, bucketStatuses) {
  const project = projectGroup.project;
  const projectLabel = project
    ? escapeHtml(project.name)
    : "Unknown project";
  const projectCount = projectGroup.rows.length;
  const bucketMarkup = bucketStatuses
    .map((status) => {
      const rows = projectGroup.rows.filter((row) => row.effectiveStatus === status);
      if (!rows.length) {
        return "";
      }
      return renderDeliveryBucket(status, rows);
    })
    .filter(Boolean)
    .join("");
  if (!bucketMarkup) {
    return "";
  }
  const expanded = Boolean(project && selectedDeliveryProjectId === text(project.id));
  return `
    <details class="delivery-project" ${teamDisclosureAttributes(teamDisclosureKey("delivery", "project", project?.id || projectGroup.project_id || "unknown"))}>
      <summary class="delivery-project-title">
        <span class="delivery-project-heading-main">
          <span class="section-label">Project</span>
          <strong>${projectLabel}</strong>
          ${project ? `<span class="meta-line">Team ${escapeHtml(state.teams?.[project.team_id]?.name || "Unknown team")}</span>` : ""}
        </span>
        <span class="delivery-bucket-count">${projectCount} deliverable${projectCount === 1 ? "" : "s"} · ${expanded ? "focused" : "open project"}</span>
      </summary>
      ${project ? renderProjectCostCounter(project.id) : ""}
      <div class="delivery-buckets delivery-project-body">
        ${bucketMarkup}
      </div>
    </details>
  `;
}

function renderDeliveryBucket(status, rows) {
  const label = deliveryStatusLabels[status] || status;
  return `
    <section class="delivery-bucket" data-status="${escapeAttr(status)}">
      <div class="delivery-bucket-title">
        <strong>${escapeHtml(label)}</strong>
        <span class="delivery-bucket-count">${rows.length} deliverable${rows.length === 1 ? "" : "s"}</span>
      </div>
      <div class="delivery-row-list">
        ${rows.map((row) => renderDeliveryRow(row)).join("")}
      </div>
    </section>
  `;
}

function renderDeliveryRow(row) {
  if (activeTeamView === "delivery") {
    return renderCompactDeliveryRow(row);
  }
  const deliverable = row.deliverable;
  const task = row.task;
  const project = row.project;
  const team = row.team;
  const projectLabel = project
    ? project.name
    : "Unknown project";
  const teamLabel = team ? team.name : "Unknown team";
  const reviewIds = row.reviewRequests.map((review) => review.id);
  const releaseSummaries = renderReleaseSummary(row.releasePackages);
  const gitPromotionIds = uniqueTextList([
    ...(deliverable.git_refs?.promotion_record_ids || []),
    ...row.releasePackages.flatMap((release) => release.git_promotion_record_ids || []),
  ]);
  return `
    <article class="delivery-row" data-deliverable-id="${escapeAttr(deliverable.id)}" data-status="${escapeAttr(row.effectiveStatus)}">
      <div class="delivery-row-header">
        <div class="delivery-row-title">
          <strong>${escapeHtml(deliverableDisplaySubject(deliverable))}</strong>
          <span class="state-pill ${escapeAttr(row.effectiveStatus)}">${escapeHtml(row.effectiveStatus)}</span>
        </div>
        <span class="delivery-bucket-count">${escapeHtml(teamLabel)} - ${escapeHtml(projectLabel)}</span>
      </div>
      <div class="delivery-row-body">
        ${renderDeliveryRowDetailBody(row, { reviewIds, releaseSummaries, gitPromotionIds })}
      </div>
    </article>
  `;
}

function renderCompactDeliveryRow(row) {
  const deliverable = row.deliverable;
  const task = row.task;
  const project = row.project;
  const team = row.team;
  const reviewIds = row.reviewRequests.map((review) => review.id);
  const releaseSummaries = renderReleaseSummary(row.releasePackages);
  const gitPromotionIds = uniqueTextList([
    ...(deliverable.git_refs?.promotion_record_ids || []),
    ...row.releasePackages.flatMap((release) => release.git_promotion_record_ids || []),
  ]);
  const scope = [
    team ? team.name || team.id : "Unknown team",
    project ? project.name || project.id : "Unknown project",
  ].join(" - ");
  const reviewText = `${reviewIds.length} review${reviewIds.length === 1 ? "" : "s"}`;
  const releaseText = `${row.releasePackages.length} release${row.releasePackages.length === 1 ? "" : "s"}`;
  return `
    <details class="delivery-row delivery-row-compact" data-deliverable-id="${escapeAttr(deliverable.id)}" data-status="${escapeAttr(row.effectiveStatus)}" ${teamDisclosureAttributes(teamDisclosureKey("delivery", "deliverable", deliverable.id, "detail"))}>
      <summary class="delivery-row-summary">
        <span class="delivery-summary-main">
          <strong>${escapeHtml(deliverableDisplaySubject(deliverable))}</strong>
          <span>${escapeHtml(deliverableProducerLabel(deliverable))} · ${renderRecordDate(deliverable.created_at)}</span>
          ${deliverable.summary ? `<span class="deliverable-summary-preview">${escapeHtml(readablePreview(deliverable.summary))}</span>` : ""}
        </span>
        <span class="delivery-summary-meta">
          <span class="state-pill ${escapeAttr(row.effectiveStatus)}">${escapeHtml(row.effectiveStatus)}</span>
          <span>${escapeHtml(reviewText)}</span>
          <span>${escapeHtml(releaseText)}</span>
        </span>
      </summary>
      <div class="delivery-row-body">
        <div class="meta-line">${escapeHtml(scope)}</div>
        ${renderDeliveryRowDetailBody(row, { reviewIds, releaseSummaries, gitPromotionIds })}
      </div>
    </details>
  `;
}

function renderDeliveryRowDetailBody(row, { reviewIds, releaseSummaries, gitPromotionIds }) {
  const deliverable = row.deliverable;
  const task = row.task;
  return `
    ${renderDeliverablePipeline(row)}
    <section class="deliverable-detail-group">
      <h4>Summary / outcome</h4>
      <p class="deliverable-full-summary">${escapeHtml(deliverable.summary)}</p>
      ${renderDeliverableOutcomes(deliverable)}
    </section>
    <section class="deliverable-detail-group">
      <h4>Task and producer</h4>
      <div class="delivery-detail-grid">
        <div><div class="delivery-detail-label">Task</div><div class="delivery-detail-value">${escapeHtml(task?.title || "Task unavailable")}${task ? ` <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(taskStateLabel(task))}</span>` : ""}</div></div>
        <div><div class="delivery-detail-label">Producer</div><div class="delivery-detail-value">${escapeHtml(deliverableProducerLabel(deliverable))}</div></div>
        <div><div class="delivery-detail-label">Recorded</div><div class="delivery-detail-value">${renderRecordDate(deliverable.created_at)}</div></div>
        <div><div class="delivery-detail-label">Deliverable state</div><div class="delivery-detail-value"><span class="state-pill ${escapeAttr(deliverable.state)}">${escapeHtml(humanizeToken(deliverable.state))}</span></div></div>
      </div>
    </section>
    <section class="deliverable-detail-group">
      <h4>Evidence and artifacts</h4>
      ${renderDeliverableEvidence(deliverable)}
    </section>
    ${renderDeliverableAcceptanceVerification(deliverable)}
    ${renderInitialReleaseDisposition(deliverable)}
    <section class="deliverable-detail-group delivery-row-reviews">
      <h4>Reviews and decision</h4>
      ${reviewIds.length ? renderReviewSummary(reviewIds, { interactive: true }) : `<p class="empty-state">No reviews recorded.</p>`}
    </section>
    ${releaseSummaries}
    <section class="deliverable-detail-group">
      <h4>Lineage / technical details</h4>
      ${renderDeliverableTechnicalDetails({
        ...deliverable,
        git_refs: {
          ...(deliverable.git_refs || {}),
          promotion_record_ids: uniqueTextList([
            ...(deliverable.git_refs?.promotion_record_ids || []),
            ...gitPromotionIds,
          ]),
        },
      })}
    </section>
  `;
}

function currentReleaseForDeliveryRow(row) {
  if (!row.releasePackages.length) {
    return null;
  }
  const currentReleases = currentDeliveryReleasePackages(row.releasePackages);
  return (currentReleases.length ? currentReleases : row.releasePackages)
    .slice()
    .sort((left, right) => {
      const leftTimestamp = Date.parse(left.updated_at || left.created_at || "") || 0;
      const rightTimestamp = Date.parse(right.updated_at || right.created_at || "") || 0;
      return rightTimestamp - leftTimestamp || text(right.id).localeCompare(text(left.id));
    })[0];
}

function currentDeliveryReleasePackages(releasePackages) {
  const supersededReleaseIds = new Set(
    releasePackages
      .map((release) => text(release.supersedes_release_package_id))
      .filter(Boolean),
  );
  return releasePackages.filter(
    (release) => !supersededReleaseIds.has(text(release.id)),
  );
}

function deliverableAcceptanceStage(row) {
  const deliverable = row.deliverable;
  const activeReviews = row.reviewRequests.filter(isCurrentReviewRequest);
  const label = activeReviews.length
    ? "Deliverable agent review"
    : "Deliverable acceptance";
  if (text(deliverable.state) === "accepted") {
    return {
      label,
      state: "complete",
      detail: activeReviews.length
        ? `${activeReviews.length} deliverable review${activeReviews.length === 1 ? "" : "s"} accepted`
        : "Deliverable accepted",
    };
  }
  const blockedReview = activeReviews.find(
    (review) => ["blocked", "rejected"].includes(text(review.state)),
  );
  if (
    ["blocked", "rejected"].includes(text(deliverable.state)) ||
    blockedReview
  ) {
    return {
      label,
      state: "blocked",
      detail: blockedReview
        ? `Deliverable review ${humanizeToken(blockedReview.state)}`
        : `Deliverable ${humanizeToken(deliverable.state)}`,
    };
  }
  if (activeReviews.length) {
    const decidedCount = activeReviews.filter((review) => text(review.state) === "accepted").length;
    return {
      label,
      state: "pending",
      detail: `${decidedCount} of ${activeReviews.length} deliverable reviews accepted`,
    };
  }
  return {
    label,
    state: "pending",
    detail:
      text(row.task?.waiting_reason) === "superior_acceptance"
        ? "Waiting for direct superior acceptance"
        : "Submission recorded; no acceptance decision yet",
  };
}

function deliverableEvidenceStage(deliverable) {
  const evidenceCount =
    (deliverable.artifact_refs || []).length +
    (deliverable.outcomes || []).flatMap(
      (outcome) => [...(outcome.reference_ids || []), ...(outcome.evidence_refs || [])],
    ).length;
  const hasGitEvidence = Boolean(
    text(deliverable.git_refs?.branch_context_id) ||
    text(deliverable.git_refs?.commit_sha) ||
    text(deliverable.git_refs?.feature_commit_sha),
  );
  const evidenceParts = [
    evidenceCount ? `${evidenceCount} evidence reference${evidenceCount === 1 ? "" : "s"}` : "",
    hasGitEvidence ? "Git evidence pinned" : "",
  ].filter(Boolean);
  return {
    label: "Implementation evidence",
    state: "complete",
    detail: evidenceParts.length ? evidenceParts.join(" · ") : "Deliverable submission recorded",
  };
}

function deliverablePipelineStages(row, release) {
  const stages = [
    deliverableEvidenceStage(row.deliverable),
    deliverableAcceptanceStage(row),
  ];
  if (!release) {
    const disposition = row.deliverable.initial_release_disposition;
    const dispositionState = text(disposition?.state);
    if (dispositionState === "pending") {
      stages.push({
        label: "Initial release decision",
        state: "waiting",
        detail: "Waiting for the project lead or team manager to request a release or mark it not planned",
      });
      return stages;
    }
    if (dispositionState === "not_planned") {
      stages.push({
        label: "Initial release decision",
        state: "complete",
        detail: disposition.reason
          ? `Not planned for an initial release: ${disposition.reason}`
          : "Not planned for an initial release",
      });
      return stages;
    }
    if (dispositionState === "release_requested") {
      stages.push({
        label: "Release inclusion",
        state: "blocked",
        detail: `Recorded release package ${text(disposition.release_package_id) || "is"} unavailable`,
      });
      return stages;
    }
    stages.push({
      label: "Release inclusion",
      state: "waiting",
      detail: "No release package includes this deliverable yet",
    });
    return stages;
  }
  const manifest = release.manifest || {};
  const candidatePinned = Boolean(
    text(release.candidate_commit_sha || manifest.candidate_commit_sha),
  );
  stages.push({
    label: "Release inclusion",
    state: "complete",
    detail: candidatePinned
      ? "Included in a release with a pinned candidate"
      : "Included in a release; candidate preparation is pending",
  });
  stages.push(
    ...releaseProgressStages(release).filter(
      (stage) => stage.label !== "Candidate prepared",
    ),
  );
  return stages;
}

function deliveryPipelineCurrentStage(stages) {
  return (
    stages.find((stage) => stage.state === "blocked") ||
    stages.find((stage) => ["pending", "waiting"].includes(stage.state)) ||
    stages[stages.length - 1]
  );
}

function deliveryPipelineBlockers(row, release) {
  const blockers = [
    ...activeBlockersForRecord("deliverable", row.deliverable.id, row.task?.id),
    ...(release ? activeBlockersForRecord("release_package", release.id) : []),
  ];
  return [...new Map(blockers.map((blocker) => [blocker.id, blocker])).values()];
}

function renderDeliverablePipeline(row) {
  const compactRelease = currentReleaseForDeliveryRow(row);
  const cached = compactRelease
    ? releaseDetailCache.get(text(compactRelease.id))
    : null;
  const release = cached?.release || compactRelease;
  const stages = deliverablePipelineStages(row, release);
  const currentStage = deliveryPipelineCurrentStage(stages);
  const blockers = deliveryPipelineBlockers(row, release);
  return `
    <section class="deliverable-detail-group deliverable-pipeline" aria-label="Deliverable delivery pipeline">
      <div class="deliverable-pipeline-heading">
        <div>
          <p class="section-label">Delivery pipeline</p>
          <h4>Current: ${escapeHtml(currentStage?.label || "Implementation evidence")}</h4>
          <p>${escapeHtml(currentStage?.detail || "Deliverable recorded")}</p>
        </div>
        <span class="state-pill ${escapeAttr(currentStage?.state || "pending")}">${escapeHtml(humanizeToken(currentStage?.state || "pending"))}</span>
      </div>
      ${
        blockers.length
          ? `<div class="deliverable-pipeline-blocker"><strong>Active blocker:</strong> ${escapeHtml(blockers.map((blocker) => blocker.summary || blocker.reason_code || blocker.id).join(" · "))}</div>`
          : release?.error
            ? `<div class="deliverable-pipeline-blocker"><strong>Release needs attention:</strong> ${escapeHtml(release.error)}</div>`
            : ""
      }
      <ol class="release-progress deliverable-progress" aria-label="Deliverable progress">
        ${stages.map((stage, index) => `
          <li class="release-progress-stage release-progress-${escapeAttr(stage.state)}">
            <span class="release-progress-marker" aria-hidden="true">${stage.state === "complete" ? "✓" : index + 1}</span>
            <span class="release-progress-copy">
              <strong>${escapeHtml(stage.label)}</strong>
              <span>${escapeHtml(stage.detail)}</span>
            </span>
          </li>
        `).join("")}
      </ol>
      ${renderDeliveryPipelineEvidenceControl(compactRelease, cached)}
      ${renderDeliveryPipelineGateEvidence(cached?.release)}
    </section>
  `;
}

function renderDeliveryPipelineEvidenceControl(release, cached) {
  if (!release) {
    return "";
  }
  if (cached?.loading) {
    return `<p class="meta-line">Loading exact release gate evidence.</p>`;
  }
  if (cached?.error) {
    return `
      <div class="task-invalid-warning">
        ${escapeHtml(cached.error)}
        <button type="button" class="record-action" data-delivery-pipeline-detail-id="${escapeAttr(release.id)}">Retry</button>
      </div>
    `;
  }
  if (cached?.release) {
    return `<p class="meta-line">Showing exact release gate evidence available to this operator.</p>`;
  }
  return `
    <div class="record-actions">
      <button type="button" class="record-action" data-delivery-pipeline-detail-id="${escapeAttr(release.id)}">
        Load exact gate evidence
      </button>
    </div>
  `;
}

function renderDeliveryPipelineGateEvidence(release) {
  if (!release) {
    return "";
  }
  const verificationRuns = release.verification_runs || [];
  const releaseReviews = release.release_reviews || [];
  const humanRequests = release.human_review?.requests || [];
  if (!verificationRuns.length && !releaseReviews.length && !humanRequests.length) {
    return `<p class="meta-line">No individual gate attempts have been recorded yet.</p>`;
  }
  return `
    <details class="delivery-pipeline-evidence" ${teamDisclosureAttributes(teamDisclosureKey("delivery", "release", release.id, "gate-evidence"))}>
      <summary>Gate attempts and decisions</summary>
      <ul class="delivery-detail-list">
        ${verificationRuns.map((run) => `
          <li>
            <strong>Automated verification attempt ${Number(run.attempt_number || 1)}</strong>
            <span class="state-pill ${escapeAttr(run.state)}">${escapeHtml(humanizeToken(run.state))}</span>
            ${run.failure_kind ? `<div class="meta-line">Failure type: ${escapeHtml(humanizeToken(run.failure_kind))}</div>` : ""}
            ${renderReleaseVerificationCommandResults(run, `delivery:${release.id}:verification`)}
          </li>
        `).join("")}
        ${releaseReviews.map((review) => `
          <li>
            <strong>Release review by ${escapeHtml(state.agents?.[review.reviewer_agent_id] ? agentLabel(state.agents[review.reviewer_agent_id]) : "assigned reviewer")}</strong>
            <span class="state-pill ${escapeAttr(review.state)}">${escapeHtml(humanizeToken(review.state))}</span>
          </li>
        `).join("")}
        ${humanRequests.map((request) => `
          <li>
            <strong>Human review request</strong>
            <span class="state-pill ${escapeAttr(request.state)}">${escapeHtml(humanizeToken(request.state))}</span>
          </li>
        `).join("")}
      </ul>
    </details>
  `;
}

function renderReleaseSummary(releasePackages) {
  if (!releasePackages.length) {
    return "";
  }
  return `
    <section class="deliverable-detail-group delivery-row-releases">
      <h4>Related releases</h4>
      <ul class="release-list release-related-list">
        ${releasePackages
          .map((release) => {
            return `
              <li class="record-item release-related-card" data-release-id="${escapeAttr(release.id)}">
                <div class="record-line">
                  <strong>${escapeHtml(releaseDisplayLabel(release))}</strong>
                  <span class="state-pill ${escapeAttr(release.state)}">${escapeHtml(humanizeToken(release.state))}</span>
                </div>
                <div class="meta-line">${escapeHtml(releaseProjectLabel(release))}${release.published_at ? ` · ${renderRecordDate(release.published_at, "published")}` : ""}</div>
                ${renderReleaseProgress(release)}
                <details class="release-related-details" ${teamDisclosureAttributes(teamDisclosureKey("delivery", "related-release", release.id, "detail"))}>
                  <summary>Release notes, included work, and technical details</summary>
                  ${renderReleaseNotes(release)}
                  ${renderReleaseIncludedWork(release)}
                  ${renderReleaseTechnicalDetails(release, {
                    disclosureKey: teamDisclosureKey("delivery", "related-release", release.id, "technical"),
                  })}
                </details>
                ${renderRecordBlockers("release_package", release.id)}
              </li>
            `;
          })
          .join("")}
      </ul>
    </section>
  `;
}

function deriveDeliveryStatus(deliverable, task, reviews, releasePackages, gitPromotions) {
  const currentReviews = reviews.filter(isCurrentReviewRequest);
  const currentReleases = currentDeliveryReleasePackages(releasePackages);
  const currentPromotionIds = new Set(
    currentReleases.flatMap((release) => release.git_promotion_record_ids || []),
  );
  const relevantGitPromotions = currentPromotionIds.size
    ? gitPromotions.filter((promotion) => currentPromotionIds.has(text(promotion.id)))
    : gitPromotions;
  if (deliverable.state === "blocked") {
    return "blocked";
  }
  if (task && (task.state === "blocked" || task.state === "rejected")) {
    return "blocked";
  }
  if (currentReviews.some((review) => review.state === "blocked")) {
    return "blocked";
  }
  if (currentReleases.some((release) => release.state === "blocked" || release.state === "rejected")) {
    return "blocked";
  }
  if (relevantGitPromotions.some((promotion) => promotion.state === "failed")) {
    return "blocked";
  }
  if (deliverable.state === "rejected" || currentReviews.some((review) => review.state === "rejected")) {
    return "rejected";
  }
  if (
    currentReleases.some((release) =>
      ["pending", "in_progress"].includes(text(release.state)),
    )
  ) {
    return "pending";
  }
  const reviewsPending = currentReviews.some(
    (review) => review.state === "pending" || review.state === "in_progress",
  );
  if (
    deliverable.state === "accepted" &&
    currentReviews.every((review) => review.state === "accepted")
  ) {
    return "accepted";
  }
  if (
    reviewsPending ||
    (task && task.state === "waiting_for_review" && task.waiting_reason !== "superior_acceptance")
  ) {
    return "waiting_for_review";
  }
  if (task && task.state === "waiting_for_review" && task.waiting_reason === "superior_acceptance") {
    return "waiting_for_acceptance";
  }
  return "pending";
}

function renderTimelineEntry(entry) {
  const timestamp = entry.timestamp ? formatTimelineTimestamp(entry.timestamp) : "Timestamp unavailable";
  const headline = timelineEntryHeadline(entry);
  return `
    <li class="timeline-entry">
      <article>
        <div class="timeline-entry-top">
          <div>
            <div class="timeline-timestamp">${escapeHtml(timestamp)}</div>
            <div class="timeline-pills">
              <span class="state-pill timeline-pill">${escapeHtml(humanizeToken(entry.type))}</span>
              <span class="state-pill timeline-pill">${escapeHtml(humanizeToken(entry.category))}</span>
            </div>
          </div>
          <span class="state-pill ${escapeAttr(entry.status || "pending")}">${escapeHtml(humanizeToken(entry.status || "pending"))}</span>
        </div>
        <p class="timeline-summary-text">${escapeHtml(headline)}</p>
        ${renderTimelineEntryDetails(entry)}
      </article>
    </li>
  `;
}

function renderCompactTimelineEntry(entry) {
  const timestamp = entry.timestamp ? formatTimelineTimestamp(entry.timestamp) : "Timestamp unavailable";
  const headline = timelineEntryHeadline(entry);
  return `
    <li class="timeline-entry timeline-entry-compact">
      <details ${teamDisclosureAttributes(teamDisclosureKey("timeline", entry.id, "detail"))}>
        <summary class="timeline-entry-summary">
          <span class="timeline-summary-main">
            <span class="timeline-timestamp">${escapeHtml(timestamp)}</span>
            <span class="timeline-summary-text">${escapeHtml(headline)}</span>
          </span>
          <span class="timeline-summary-pills">
            <span class="state-pill timeline-pill">${escapeHtml(humanizeToken(entry.type))}</span>
            <span class="state-pill ${escapeAttr(entry.status || "pending")}">${escapeHtml(humanizeToken(entry.status || "pending"))}</span>
          </span>
        </summary>
        <div class="timeline-entry-details">
          <div class="timeline-pills">
            <span class="state-pill timeline-pill">${escapeHtml(humanizeToken(entry.category))}</span>
            ${entry.attention_needed ? `<span class="state-pill blocked">attention needed</span>` : ""}
          </div>
          ${renderTimelineEntryDetails(entry)}
        </div>
      </details>
    </li>
  `;
}

function timelineEntryRecord(entry) {
  if (entry.task_id && state.tasks?.[entry.task_id]) {
    return state.tasks[entry.task_id];
  }
  if (entry.deliverable_id && state.deliverables?.[entry.deliverable_id]) {
    return state.deliverables[entry.deliverable_id];
  }
  if (entry.review_request_id && state.review_requests?.[entry.review_request_id]) {
    return state.review_requests[entry.review_request_id];
  }
  if (entry.inbox_item_id && state.inbox?.[entry.inbox_item_id]) {
    return state.inbox[entry.inbox_item_id];
  }
  if (entry.action_record_id && state.action_records?.[entry.action_record_id]) {
    return state.action_records[entry.action_record_id];
  }
  if (
    entry.ephemeral_assignment_id &&
    state.ephemeral_assignments?.[entry.ephemeral_assignment_id]
  ) {
    return state.ephemeral_assignments[entry.ephemeral_assignment_id];
  }
  if (entry.git_promotion_id && state.git_promotions?.[entry.git_promotion_id]) {
    return state.git_promotions[entry.git_promotion_id];
  }
  if (
    entry.release_package_id &&
    state.release_packages?.[entry.release_package_id]
  ) {
    return state.release_packages[entry.release_package_id];
  }
  return null;
}

function timelineEntrySubject(entry) {
  if (text(entry.subject)) {
    return text(entry.subject);
  }
  const task = entry.task_id ? state.tasks?.[entry.task_id] : null;
  const deliverable = entry.deliverable_id
    ? state.deliverables?.[entry.deliverable_id]
    : null;
  const review = entry.review_request_id
    ? state.review_requests?.[entry.review_request_id]
    : null;
  const inbox = entry.inbox_item_id ? state.inbox?.[entry.inbox_item_id] : null;
  const action = entry.action_record_id
    ? state.action_records?.[entry.action_record_id]
    : null;
  const assignment = entry.ephemeral_assignment_id
    ? state.ephemeral_assignments?.[entry.ephemeral_assignment_id]
    : null;
  const promotion = entry.git_promotion_id
    ? state.git_promotions?.[entry.git_promotion_id]
    : null;
  const releaseId =
    entry.release_package_id || review?.release_package_id || promotion?.release_package_id;
  const release = releaseId ? state.release_packages?.[releaseId] : null;
  if (entry.type === "task" && task) {
    return task.title;
  }
  if (entry.type === "deliverable" && deliverable) {
    return deliverableDisplaySubject(deliverable);
  }
  if (entry.type === "review") {
    if (release) {
      return releaseDisplayLabel(release);
    }
    if (deliverable) {
      return deliverableDisplaySubject(deliverable);
    }
    return task?.title || "Review";
  }
  if (entry.type === "inbox_item" && inbox) {
    return readablePreview(
      inboxItemDetail(inbox) ||
        `Message for ${agentLabel(state.agents?.[inbox.recipient_agent_id]) || "an agent"}`,
      120,
    );
  }
  if (entry.type === "action" && action) {
    return (
      text(action.action?.summary) ||
      humanizeToken(action.action?.type || "Agent action")
    );
  }
  if (entry.type === "ephemeral_assignment") {
    const assignmentTask = assignment?.task_id
      ? state.tasks?.[assignment.task_id]
      : task;
    return assignmentTask?.title || "Temporary assignment";
  }
  if (entry.type === "git_promotion") {
    return (
      task?.title ||
      (deliverable ? deliverableDisplaySubject(deliverable) : "") ||
      state.projects?.[promotion?.project_id]?.name ||
      "Git publication"
    );
  }
  if (entry.type === "release_package" && release) {
    return releaseDisplayLabel(release);
  }
  return humanizeToken(entry.type || "Activity");
}

function timelineEntryHeadline(entry) {
  if (text(entry.event)) {
    return text(entry.event);
  }
  if (text(entry.subject) && text(entry.summary)) {
    return text(entry.summary);
  }
  const subject = timelineEntrySubject(entry);
  const status = humanizeToken(entry.status || "");
  const eventLabels = {
    "task:created": "Created task",
    "task:updated": "Updated task",
    "inbox_item:created": "Sent message",
    "inbox_item:updated": "Updated message",
    "inbox_item:handled": "Marked message handled",
    "deliverable:created": "Submitted deliverable",
    "review:requested": "Requested review",
    "review:decision": status ? `Review decided ${status.toLowerCase()}` : "Recorded review decision",
    "action:created": status ? `Agent action ${status.toLowerCase()}` : "Recorded agent action",
    "ephemeral_assignment:requested": "Requested temporary assignment",
    "ephemeral_assignment:state": status ? `Temporary assignment changed to ${status.toLowerCase()}` : "Updated temporary assignment",
    "git_promotion:requested": "Requested Git publication",
    "git_promotion:result": status ? `Git publication ${status.toLowerCase()}` : "Recorded Git publication result",
    "release_package:created": "Prepared release",
    "release_package:published": "Published release",
    "release_package:blocked": "Blocked release",
  };
  const key = `${text(entry.type)}:${text(entry.category)}`;
  return `${eventLabels[key] || `${humanizeToken(entry.category)} ${humanizeToken(entry.type)}`} — ${subject}`;
}

function timelineActorLabel(entry) {
  const actor = entry.actor_agent_id
    ? state.agents?.[entry.actor_agent_id]
    : null;
  if (actor) {
    return `${agentLabel(actor)} · ${agentRoleLabel(actor)}`;
  }
  const process = text(entry.process);
  if (process) {
    return `${humanizeToken(process)} process`;
  }
  const actorSubject = text(entry.actor_subject);
  if (actorSubject && actorSubject !== text(entry.actor_agent_id)) {
    return actorSubject;
  }
  return "Initiator not retained";
}

function timelineScopeLabel(entry) {
  const team = entry.team_id ? state.teams?.[entry.team_id] : null;
  const project = entry.project_id ? state.projects?.[entry.project_id] : null;
  return [
    team?.name || "",
    project?.name || "",
  ]
    .filter(Boolean)
    .join(" · ") || "Scope not retained";
}

function timelineCurrentValue(entry) {
  const record = timelineEntryRecord(entry);
  if (entry.after && typeof entry.after !== "object") {
    return text(entry.after);
  }
  if (entry.after && typeof entry.after === "object") {
    return Object.entries(entry.after)
      .map(([key, value]) => `${humanizeToken(key)}: ${humanizeToken(value)}`)
      .join(" · ");
  }
  if (record?.state) {
    return humanizeToken(record.state);
  }
  if (record?.status) {
    return humanizeToken(record.status);
  }
  if (typeof record?.success === "boolean") {
    return record.success ? "Succeeded" : "Failed";
  }
  return humanizeToken(entry.status || "Recorded");
}

function timelineChangeDescription(entry) {
  if (entry.change && typeof entry.change === "object") {
    const beforeKnown = entry.change.before_known === true;
    return {
      before:
        beforeKnown && text(entry.change.before)
          ? text(entry.change.before)
          : "Earlier value not retained",
      after: text(entry.change.after) || timelineCurrentValue(entry),
      field: text(entry.change.field) || "State",
    };
  }
  if (text(entry.change)) {
    return {
      label: "Change",
      value: text(entry.change),
    };
  }
  const current = timelineCurrentValue(entry);
  if (entry.category === "handled") {
    return { before: "Pending", after: "Handled" };
  }
  if (entry.type === "git_promotion" && entry.category === "result") {
    return { before: "Pending", after: current };
  }
  if (["created", "requested"].includes(text(entry.category))) {
    return {
      label: "Operation",
      value: `${humanizeToken(entry.category)}; no earlier record is implied.`,
    };
  }
  if (
    text(entry.category) === "updated" &&
    !entry.change &&
    !text(entry.before)
  ) {
    return {
      label: "Operation",
      value: "Snapshot updated; no state transition is implied.",
    };
  }
  return {
    before: text(entry.before) || "Earlier value not retained",
    after: current,
  };
}

function timelineReason(entry) {
  if (text(entry.reason)) {
    return text(entry.reason);
  }
  const task = entry.task_id ? state.tasks?.[entry.task_id] : null;
  const review = entry.review_request_id
    ? state.review_requests?.[entry.review_request_id]
    : null;
  const action = entry.action_record_id
    ? state.action_records?.[entry.action_record_id]
    : null;
  const assignment = entry.ephemeral_assignment_id
    ? state.ephemeral_assignments?.[entry.ephemeral_assignment_id]
    : null;
  const promotion = entry.git_promotion_id
    ? state.git_promotions?.[entry.git_promotion_id]
    : null;
  const release = entry.release_package_id
    ? state.release_packages?.[entry.release_package_id]
    : null;
  const deliverable = entry.deliverable_id
    ? state.deliverables?.[entry.deliverable_id]
    : null;
  return text(
    task?.cancellation?.reason ||
      task?.waiting_reason ||
      review?.finding ||
      review?.block_kind ||
      action?.error ||
      action?.dismiss_reason ||
      assignment?.error ||
      promotion?.error ||
      release?.error ||
      release?.block_reason ||
      deliverable?.supersession_reason,
  );
}

function timelineRelatedLabels(entry) {
  const labels = [];
  const actorId = text(entry.actor_agent_id);
  uniqueTextList(entry.related_agent_ids || [])
    .filter((agentId) => text(agentId) !== actorId)
    .forEach((agentId) => {
      const agent = state.agents?.[agentId];
      labels.push(agent ? `${agentLabel(agent)} · ${agentRoleLabel(agent)}` : "Unavailable agent");
    });
  const task = entry.task_id ? state.tasks?.[entry.task_id] : null;
  const subject = timelineEntrySubject(entry);
  if (task?.title && task.title !== subject) {
    labels.push(`Task: ${task.title}`);
  }
  return uniqueTextList(labels);
}

function renderTimelineTechnicalDetails(entry) {
  const relatedAgentIds = uniqueTextList(entry.related_agent_ids || []).filter(
    (agentId) => text(agentId) !== text(entry.actor_agent_id),
  );
  return renderTechnicalDetails([
    ["Team ID", entry.team_id],
    ["Project ID", entry.project_id],
    ["Actor agent ID", entry.actor_agent_id],
    ["Related agent IDs", relatedAgentIds],
    ["task_id", "Task"],
    ["deliverable_id", "Deliverable"],
    ["review_request_id", "Review"],
    ["inbox_item_id", "Inbox"],
    ["action_record_id", "Action"],
    ["ephemeral_assignment_id", "Ephemeral"],
    ["git_promotion_id", "Git promotion"],
    ["release_package_id", "Release"],
  ].map(([key, label]) => {
    if (key.endsWith("_id")) {
      return [`${label} ID`, entry[key]];
    }
    return [key, label];
  }), {
    disclosureKey: teamDisclosureKey("timeline", entry.id, "technical"),
  });
}

function renderTimelineEntryDetails(entry) {
  const change = timelineChangeDescription(entry);
  const reason = timelineReason(entry);
  const related = timelineRelatedLabels(entry);
  return `
    <div class="timeline-domain-grid">
      <div><span>Event</span><strong>${escapeHtml(timelineEntryHeadline(entry))}</strong></div>
      <div><span>Initiated by</span><strong>${escapeHtml(timelineActorLabel(entry))}</strong></div>
      <div><span>Subject</span><strong>${escapeHtml(timelineEntrySubject(entry))}</strong></div>
      <div><span>Scope</span><strong>${escapeHtml(timelineScopeLabel(entry))}</strong></div>
    </div>
    <section class="timeline-change-section">
      <h4>Change${change.field ? ` · ${escapeHtml(change.field)}` : ""}</h4>
      ${
        change.before !== undefined
          ? `
            <div class="timeline-change-flow">
              <div><span>Before</span><strong>${escapeHtml(change.before)}</strong></div>
              <span aria-hidden="true">→</span>
              <div><span>After</span><strong>${escapeHtml(change.after)}</strong></div>
            </div>
          `
          : `<p><strong>${escapeHtml(change.label)}:</strong> ${escapeHtml(change.value)}</p>`
      }
      ${reason ? `<p class="timeline-reason"><strong>Reason:</strong> ${escapeHtml(reason)}</p>` : ""}
      ${
        !entry.before &&
        !entry.change &&
        !["created", "requested", "handled"].includes(text(entry.category)) &&
        !(entry.type === "git_promotion" && entry.category === "result")
          ? `<p class="timeline-retention-note">This snapshot does not retain the earlier field values or a complete transition diff.</p>`
          : ""
      }
    </section>
    ${
      related.length
        ? `<section class="timeline-related-work"><h4>Related people and work</h4><ul>${related.map((label) => `<li>${escapeHtml(label)}</li>`).join("")}</ul></section>`
        : ""
    }
    ${renderTimelineTechnicalDetails(entry)}
  `;
}

function describeTimelineStatus(timeline) {
  const filters = timeline.filters || {};
  const filterParts = [];
  if (filters.team_id) {
    filterParts.push(`team ${state.teams?.[filters.team_id]?.name || "unavailable"}`);
  }
  if (filters.project_id) {
    filterParts.push(`project ${state.projects?.[filters.project_id]?.name || "unavailable"}`);
  }
  if (filters.agent_id) {
    const agent = state.agents?.[filters.agent_id];
    filterParts.push(`agent ${agent ? agentLabel(agent) : "unavailable"}`);
  }
  if (filters.type) {
    filterParts.push(`type ${filters.type}`);
  }
  if (filters.status) {
    filterParts.push(`status ${Array.isArray(filters.status) ? filters.status.join(", ") : filters.status}`);
  }
  if (filters.attention_needed !== undefined && filters.attention_needed !== null) {
    filterParts.push(`attention ${filters.attention_needed ? "yes" : "no"}`);
  }
  const filterSuffix = filterParts.length ? ` for ${filterParts.join(", ")}` : "";
  if (!(timeline.entries || []).length) {
    return `No timeline entries matched the current filters${filterSuffix}.`;
  }
  return `Activity timeline loaded${filterSuffix}.`;
}

function readTimelineFilters() {
  const teamId = timelineFilterValue("team_id");
  const projectId = timelineFilterValue("project_id");
  const agentId = timelineFilterValue("agent_id");
  const type = timelineFilterValue("type");
  const status = timelineFilterValue("status");
  const attentionValue = timelineFilterValue("attention_needed");
  return {
    team_id: teamId || null,
    project_id: projectId || null,
    agent_id: agentId || null,
    type: type || null,
    status: status || null,
    attention_needed:
      attentionValue === "true" ? true : attentionValue === "false" ? false : null,
  };
}

function timelineFilterValue(filterKey) {
  const controls = Array.from(document.querySelectorAll(`[data-timeline-filter="${filterKey}"]`));
  const activeControl = controls.find(targetIsActive);
  return text((activeControl || controls[0])?.value);
}

function refreshSelects({ includeEditForm = false } = {}) {
  const options = buildSelectOptionSets();
  document.querySelectorAll("select[data-options]").forEach((select) => {
    if (!includeEditForm && elements.editForm?.contains(select)) {
      return;
    }
    fillSelect(select, optionsForSelect(select, options));
  });

  const updateTaskForm = document.querySelector("#update-task-form");
  if (updateTaskForm) {
    syncTaskUpdateForm(updateTaskForm, { keepEditedValues: true });
  }
}

function buildSelectOptionSets() {
  const teams = values(state.teams).filter(isVisibleTeam).sort(comparePeopleTeams).map((team) => [team.id, `${team.name} (${team.id})`]);
  const governanceGroups = values(state.governance_groups).filter(isVisibleGovernanceGroup).sort(compareGovernanceGroups).map((group) => [group.id, `${group.name} (${group.id})`]);
  const projects = values(state.projects).filter(isVisibleProject).sort(compareTeamProjects).map((project) => [project.id, `${project.name} (${project.id})`]);
  const roles = values(state.roles).filter(isVisibleRole).sort(compareTeamRoles).map((role) => [role.id, `${role.name} (${role.id})`]);
  const agents = values(state.agents).filter(isVisibleAgent).sort(comparePeopleAgents).map((agent) => [agent.id, `${agent.id} - ${agentRoleLabel(agent)}`]);
  const agentsByDisplayName = values(state.agents)
    .filter(isVisibleAgent)
    .map((agent) => [agent.id, text(agent.display_name) || text(agent.id)])
    .sort((left, right) => text(left[1]).localeCompare(text(right[1])) || text(left[0]).localeCompare(text(right[0])));
  const governanceManagerAgents = values(state.agents)
    .filter(isVisibleAgent)
    .map((agent) => [
      agent.id,
      `${agentLabel(agent)} — ${agentRoleLabel(agent)} · ${agent.id}`,
    ])
    .sort(
      (left, right) =>
        text(left[1]).localeCompare(text(right[1])) ||
        text(left[0]).localeCompare(text(right[0])),
    );
  const tasks = values(state.tasks).sort(byPriority).map((task) => [task.id, `${task.title} (${task.id})`]);
  const cases = values(state.cases).filter(isVisibleCase).sort(compareCases).map((item) => [item.id, caseLabel(item)]);
  const deliverables = values(state.deliverables)
    .sort((left, right) => text(left.summary || left.id).localeCompare(text(right.summary || right.id)) || byId(left, right))
    .map((deliverable) => [deliverable.id, `${deliverable.summary} (${deliverable.id})`]);
  const reviewRequests = values(state.review_requests)
    .sort(byId)
    .map((review) => [review.id, `${review.id} - ${review.state}`]);
  const reviewProfiles = values(state.review_profiles)
    .filter(isVisibleReviewProfile)
    .sort(compareReviewProfiles)
    .map((profile) => [profile.id, `${profile.name} (${profile.id})`]);
  const selectedProject = state.projects[selectedProjectFocusId] || null;
  const projectOutcomeKinds = selectedProject
    ? effectiveOutcomeKindsForProject(selectedProject).map((definition) => [
        definition.kind,
        `${definition.name} (${definition.kind})`,
      ])
    : [];
  const teamOutcomeKinds = selectedProject
    ? values(state.outcome_kind_definitions)
        .filter((definition) =>
          definition.team_id === selectedProject.team_id
          && !definition.project_id
          && !definition.archived
        )
        .sort((left, right) => text(left.kind).localeCompare(text(right.kind)) || byId(left, right))
        .map((definition) => [
          definition.kind,
          `${definition.name} (${definition.kind})`,
        ])
    : [];
  const releasePackages = values(state.release_packages)
    .sort(compareProjectReleasesLatest)
    .map((release) => [release.id, `${release.id} - ${release.state}`]);
  const timelineOptions = buildTimelineSelectOptions();
  return {
    teams,
    projects,
    roles,
    "project-review-roles": projectReviewRoleOptionsForProject(selectedProjectFocusId),
    "project-outcome-kinds": projectOutcomeKinds,
    "team-outcome-kinds": teamOutcomeKinds,
    agents,
    "agents-by-display-name": agentsByDisplayName,
    "governance-manager-agents": governanceManagerAgents,
    cases,
    tasks,
    deliverables,
    "review-profiles": reviewProfiles,
    "review-requests": reviewRequests,
    "release-packages": releasePackages,
    "governance-groups": governanceGroups,
    "governance-agenda-types": governanceAgendaTypes.map((item) => [item, humanizeToken(item)]),
    "inbox-priorities": inboxPriorities.map((item) => [item, humanizeToken(item)]),
    "case-statuses": caseStatuses.map((item) => [item, humanizeToken(item)]),
    "task-states": taskStates.map((item) => [item, item]),
    "task-edit-states": directlyEditableTaskStates.map((item) => [item, item]),
    "record-states": recordStates.map((item) => [item, item]),
    "agent-statuses": agentStatuses.map((item) => [item, item]),
    "context-clearing-policies": contextClearingPolicies,
    "cost-budget-renewal-modes": [
      ["manual", "Manual reset"],
      ["monthly_utc", "Monthly (UTC)"],
    ],
    "communication-targets": communicationTargets,
    "realtime-voices": realtimeVoiceOptions,
    "project-statuses": projectStatuses.map((item) => [item, item]),
    "decision-states": decisionStates.map((item) => [item, item]),
    "git-providers": gitProviders,
    "role-permissions": rolePermissionOptions(),
    "timeline-teams": timelineOptions.teams,
    "timeline-projects": timelineOptions.projects,
    "timeline-agents": timelineOptions.agents,
    "timeline-types": timelineOptions.types,
    "timeline-statuses": timelineOptions.statuses,
    "timeline-attention": [
      ["true", "Yes"],
      ["false", "No"],
    ],
  };
}

function optionsForSelect(select, optionSets) {
  if (select.dataset.options === "communication-targets") {
    const options = optionSets[select.dataset.options] || [];
    const currentValue = text(select.dataset.currentValue ?? select.value);
    if (currentValue && !options.some(([value]) => value === currentValue)) {
      return [[currentValue, `${currentValue} (no longer registered)`], ...options];
    }
    return options;
  }
  if (select.dataset.options === "review-profile-edit-outcome-kinds") {
    return reviewProfileEditOutcomeOptions(select);
  }
  if (select.dataset.options === "project-lead-agents") {
    return projectLeadAgentOptions(select);
  }
  if (select.dataset.options === "agent-edit-projects") {
    return agentEditProjectOptions(select);
  }
  if (select.dataset.options === "agent-edit-roles") {
    return agentEditRoleOptions(select);
  }
  if (select.dataset.options === "agent-edit-superiors") {
    return agentEditSuperiorOptions(select);
  }
  return optionSets[select.dataset.options] || [];
}

function normalizeCommunicationTargetOptions(value) {
  const optionsByValue = new Map();
  (Array.isArray(value) ? value : []).forEach((option) => {
    if (!option || typeof option !== "object") {
      return;
    }
    const targetId = text(option.value);
    if (!targetId || optionsByValue.has(targetId)) {
      return;
    }
    optionsByValue.set(targetId, [targetId, text(option.label) || targetId]);
  });
  return Array.from(optionsByValue.values()).sort(
    (left, right) => text(left[1]).localeCompare(text(right[1])) || text(left[0]).localeCompare(text(right[0])),
  );
}

function reviewProfileEditOutcomeOptions(select) {
  const profileId = text(select.form?.dataset.editId);
  const profile = state.review_profiles?.[profileId];
  if (!profile) {
    return [];
  }
  const project = state.projects[text(profile.project_id)] || null;
  const definitions = project
    ? effectiveOutcomeKindsForProject(project)
    : values(state.outcome_kind_definitions)
        .filter((definition) =>
          text(definition.team_id) === text(profile.team_id)
          && !definition.project_id
          && !definition.archived
        )
        .sort((left, right) => text(left.kind).localeCompare(text(right.kind)) || byId(left, right));
  const definitionsByKind = new Map(
    definitions.map((definition) => [text(definition.kind), definition]),
  );
  (profile.required_outcome_kinds || []).forEach((kind) => {
    const normalizedKind = text(kind);
    if (!normalizedKind || definitionsByKind.has(normalizedKind)) {
      return;
    }
    definitionsByKind.set(normalizedKind, {
      kind: normalizedKind,
      name: normalizedKind,
      archived: true,
    });
  });
  return Array.from(definitionsByKind.values())
    .sort((left, right) => text(left.kind).localeCompare(text(right.kind)) || byId(left, right))
    .map((definition) => [
      definition.kind,
      `${definition.name} (${definition.kind})${definition.archived ? " - archived" : ""}`,
    ]);
}

function agentEditTeamId(select) {
  const teamId = text(select.form?.elements?.team_id?.value);
  return teamId === noTeamRecordId ? "" : teamId;
}

function agentEditProjectOptions(select) {
  const teamId = agentEditTeamId(select);
  return teamId ? teamProjectOptions(teamId) : [];
}

function agentEditRoleOptions(select) {
  const teamId = agentEditTeamId(select);
  return teamId ? teamRoleOptions(teamId) : [];
}

function agentEditSuperiorOptions(select) {
  const teamId = agentEditTeamId(select);
  if (!teamId) {
    return [];
  }
  const editedAgentId = text(select.form?.dataset?.editId);
  return teamAgentOptions(teamId).filter(([agentId]) => text(agentId) !== editedAgentId);
}

function projectLeadAgentOptions(select) {
  const project = state.projects?.[text(select.form?.dataset?.editId)] || null;
  const teamId = text(select.form?.elements?.team_id?.value) || text(project?.team_id);
  return projectLeadAgentOptionsForTeam(teamId);
}

function projectLeadAgentOptionsForTeam(teamId) {
  const normalizedTeamId = text(teamId);
  const team = normalizedTeamId ? state.teams?.[normalizedTeamId] : null;
  if (!team) {
    return [];
  }
  return values(state.agents)
    .filter(isVisibleAgent)
    .filter((agent) => text(agent.team_id) === normalizedTeamId)
    .sort(comparePeopleAgents)
    .map((agent) => [agent.id, `${agent.id} - ${agentRoleLabel(agent)}`]);
}

function projectReviewRoleOptionsForProject(projectId) {
  const project = state.projects?.[text(projectId)];
  if (!project) {
    return [];
  }
  return values(state.roles)
    .filter(isVisibleRole)
    .filter((role) => {
      if (text(role.team_id) !== text(project.team_id)) {
        return false;
      }
      const roleProjectId = text(role.project_id);
      return !roleProjectId || roleProjectId === text(project.id);
    })
    .sort(compareTeamRoles)
    .map((role) => [role.id, `${role.name} (${role.id})`]);
}

function fillSelect(select, options) {
  const currentValue = select.dataset.currentValue ?? select.value;
  const includePlaceholder = !select.multiple;
  const placeholder = select.dataset.placeholder || (select.required ? "Select" : "Default");
  select.innerHTML = `${includePlaceholder ? `<option value="">${placeholder}</option>` : ""}${options
    .map(([value, label, tooltip]) => {
      const optionTooltip = text(tooltip);
      return `<option value="${escapeAttr(value)}"${optionTooltip ? ` title="${escapeAttr(optionTooltip)}"` : ""}>${escapeHtml(label)}</option>`;
    })
    .join("")}`;
  if (select.multiple) {
    const currentValues = new Set(splitList(currentValue));
    Array.from(select.options).forEach((option) => {
      option.selected = currentValues.has(option.value);
    });
    delete select.dataset.currentValue;
    return;
  }
  if (options.some(([value]) => value === currentValue)) {
    select.value = currentValue;
  } else if (!currentValue) {
    select.value = "";
  }
  delete select.dataset.currentValue;
}

function teamFetch(input, init = {}, { timeoutMs = teamRequestTimeoutMs } = {}) {
  const timeout = Number(timeoutMs);
  if (!Number.isFinite(timeout) || timeout <= 0 || typeof AbortController === "undefined") {
    return window.fetch(input, init);
  }

  const controller = new AbortController();
  const upstreamSignal = init?.signal || null;
  let timedOut = false;
  const abortFromUpstream = () => {
    controller.abort(upstreamSignal?.reason);
  };

  if (upstreamSignal) {
    if (upstreamSignal.aborted) {
      controller.abort(upstreamSignal.reason);
    } else {
      upstreamSignal.addEventListener("abort", abortFromUpstream, { once: true });
    }
  }

  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeout);

  return window.fetch(input, { ...init, signal: controller.signal })
    .catch((error) => {
      if (timedOut && error?.name === "AbortError") {
        throw new Error(`Request timed out after ${Math.round(timeout / 1000)}s.`);
      }
      throw error;
    })
    .finally(() => {
      window.clearTimeout(timer);
      upstreamSignal?.removeEventListener?.("abort", abortFromUpstream);
    });
}

function fillSelectsIn(root) {
  const options = buildSelectOptionSets();
  root.querySelectorAll("select[data-options]").forEach((select) => {
    fillSelect(select, optionsForSelect(select, options));
  });
}

async function submitCreateForm(form, { detailedResult = false } = {}) {
  if (!guardTeamMutation("save changes")) {
    return submitMutationResult(false, "locked", detailedResult);
  }
  const data = formData(form);
  const endpoint = endpointForForm(form, data);
  const method = form.dataset.method || "POST";
  if (!endpoint) {
    return submitMutationResult(false, "validation", detailedResult);
  }
  const formId = formSemanticIdentifier(form);
  if (
    formId === "create-governance-group-form" &&
    !validateGovernanceGroupEditor(form)
  ) {
    return submitMutationResult(false, "validation", detailedResult);
  }
  if (
    formId === "create-project-form" &&
    !validateHumanReviewPolicyEditor(form)
  ) {
    return submitMutationResult(false, "validation", detailedResult);
  }
  const existingProjectIds =
    formId === "create-project-form"
      ? new Set(values(state.projects).map((project) => project.id))
      : null;
  const payload = payloadForForm(form, data);
  const result = await submitMutation(form, endpoint, method, payload, { detailedResult });
  const saved = mutationResultSaved(result);
  if (saved && existingProjectIds) {
    const createdProjects = values(state.projects)
      .filter((project) => !existingProjectIds.has(project.id))
      .sort((left, right) =>
        String(right.created_at || "").localeCompare(String(left.created_at || "")) || byId(left, right),
      );
    if (form.hasAttribute("data-project-create-form")) {
      projectCreateFormOpen = false;
    }
    if (createdProjects.length) {
      selectedProjectFocusId = createdProjects[0].id;
      renderProjectFocus();
      refreshSelects();
    } else {
      syncProjectCreatePanel();
    }
  }
  if (saved && formId === "create-review-profile-form" && form.hasAttribute("data-project-review-profile-create-form")) {
    projectReviewProfileCreateProjectId = null;
    renderProjectFocus();
  }
  if (saved && formId === "create-team-from-teams-form") {
    closeTeamsCreateTeamPanel({ reset: true });
    renderAgents();
  }
  if (saved && formId === "create-governance-group-form") {
    closeGovernanceCreateGroupPanel({ reset: true });
  }
  if (saved && form.hasAttribute("data-team-inline-create-form")) {
    closeTeamInlineCreateFormFor(form);
  }
  if (saved && form.hasAttribute("data-org-action-form")) {
    closeOrgActionDialog();
  }
  return result;
}

async function submitTaskUpdate(form) {
  if (!guardTeamMutation("update tasks")) {
    return;
  }
  const data = formData(form);
  const taskId = text(data.task_id);
  if (!taskId) {
    setStatus("Task is required.", "error");
    return;
  }
  const payload = {};
  const stateValue = text(data.state);
  const priority = text(data.priority);
  const title = text(data.title);
  const description = text(data.description);
  const caseId = text(data.case_id);
  const parentTaskId = text(data.parent_task_id);
  if (stateValue) payload.state = stateValue;
  payload.case_id = caseId || null;
  if (priority) payload.priority = Number(priority);
  if (title) payload.title = title;
  if (description) payload.description = description;
  payload.parent_task_id = parentTaskId || null;
  if (form.elements.task_author_name) {
    payload.authored_by = taskAuthoredByForForm(data);
  }
  if (form.elements.git_base_branch) {
    payload.git_base_branch = text(data.git_base_branch) || null;
  }
  payload.dependency_task_ids = splitList(data.dependency_task_ids);
  const integrationBoundary = Boolean(
    form.elements.integration_boundary?.checked,
  );
  const requireGitProvenance = Boolean(
    form.elements.require_git_provenance?.checked,
  );
  const requiredContributionTaskIds = splitList(
    data.required_contribution_task_ids,
  );
  payload.integration_boundary = integrationBoundary;
  payload.deliverable_contract = (
    integrationBoundary
    || requireGitProvenance
    || requiredContributionTaskIds.length > 0
  )
    ? {
        require_git_provenance: requireGitProvenance,
        required_contribution_task_ids: requiredContributionTaskIds,
      }
    : null;
  const saved = await submitMutation(form, `/tasks/${encodeURIComponent(taskId)}`, "PATCH", payload, {
    resetOnSuccess: false,
  });
  if (saved) {
    showTaskPreparationInvalidationStatus(taskId);
  }
}

function showTaskPreparationInvalidationStatus(taskId) {
  const item = latestTaskPreparationInvalidation(taskId);
  const task = state.tasks?.[text(taskId)];
  if (
    !item ||
    text(item.state) !== "pending" ||
    (task?.updated_at && String(item.created_at || "") < String(task.updated_at))
  ) {
    return false;
  }
  const contextCount = (item.payload?.invalidated_git_work_context_ids || []).length;
  setStatus(
    `Saved revision ${state.revision}. ${contextCount} stale Git preparation${contextCount === 1 ? " was" : "s were"} retired; fresh preparation is queued.`,
    "ok",
  );
  return true;
}

async function importGitRepoForForm(form, button) {
  if (!guardTeamMutation("import Git repositories")) {
    return false;
  }
  const data = formData(form);
  const repoSource = text(data.repo_source);
  if (!repoSource) {
    setStatus("Repository source is required before import.", "error");
    return false;
  }
  const payload = { repo_source: repoSource };
  const directoryPath = text(form.dataset.directoryPath);
  if (directoryPath) {
    payload.directory_path = directoryPath;
  }
  const statusTarget = form.querySelector("[data-git-import-status]");
  setGitImportStatus(statusTarget, "Importing.");
  setFormBusy(form, true);
  button.disabled = true;
  setStatus("Importing Git repo.", "loading");
  try {
    const response = await teamFetch(`${API_BASE}/projects/git/import`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const body = await response.json();
    applyImportedGitConfig(form, body.git || body.imported?.git || {});
    const imported = body.imported || {};
    setGitImportStatus(statusTarget, imported.managed_repo_id || "Imported.");
    setStatus("Imported Git repo. Review the populated Git fields, then save.", "ok");
    return true;
  } catch (error) {
    const message = error instanceof Error ? error.message : "Git import failed.";
    setGitImportStatus(statusTarget, message);
    setStatus(message, "error");
    return false;
  } finally {
    setFormBusy(form, false);
    button.disabled = false;
  }
}

function applyImportedGitConfig(form, git) {
  const fields = {
    managed_repo_id: git.managed_repo_id,
    directory_path: git.directory_path,
    base_branch: git.base_branch,
    canonical_release_branch_prefix: git.canonical_release_branch_prefix,
  };
  Object.entries(fields).forEach(([name, value]) => {
    if (value === undefined || value === null) {
      return;
    }
    const field = form.elements[name];
    if (!field) {
      return;
    }
    field.value = value;
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function setGitImportStatus(element, message) {
  if (!element) {
    return;
  }
  element.textContent = message || "";
}

async function resetTeamCostBudget(button, teamId, reason) {
  if (!teamId) {
    setStatus("Team budget reset target is unavailable.", "error");
    return;
  }
  button.disabled = true;
  setStatus("Resetting cost budget.", "loading");
  try {
    const response = await teamFetch(
      `${API_BASE}/teams/${encodeURIComponent(teamId)}/cost-budget/reset`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify({ reason, expected_revision: state.revision }),
      },
    );
    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView();
      setStatus(
        `Revision conflict. Reloaded revision ${conflict.current_revision ?? state.revision}; verify the budget and retry.`,
        "warning",
      );
      return;
    }
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    await applyMutationResultPayload(await response.json());
    await loadCosts();
    setStatus("Started a new team cost budget window.", "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Budget reset failed.", "error");
  } finally {
    button.disabled = false;
  }
}

async function submitMutation(form, endpoint, method, payload, { resetOnSuccess = true, detailedResult = false } = {}) {
  if (!guardTeamMutation("save changes")) {
    return submitMutationResult(false, "locked", detailedResult);
  }
  rememberFormDraft(form);
  const drafts = captureFormDrafts();
  const body = mutationBodyWithConflictMetadata(endpoint, method, payload);
  const retryDraftKey = formDraftKey(form);
  setFormBusy(form, true);
  setStatus("Saving.", "loading");
  try {
    const response = await teamFetch(`${API_BASE}${endpoint}`, {
      method,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Team-Addon-Response-Scope": "view",
      },
      body: JSON.stringify(body),
    });

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      showRevisionConflictRecoveryStatus({
        form,
        draftKey: retryDraftKey,
        endpoint,
        method,
        payload,
        options: { resetOnSuccess },
        currentRevision: current,
      });
      return submitMutationResult(false, "conflict", detailedResult);
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    const result = await response.json();
    const rebasedFromRevision = result.rebased_from_revision;
    const scoped = isScopedMutationResponse(result);
    if (scoped) {
      applyScopedMutationResponse(result);
    } else {
      state = normalizeState(result);
      loadedTeamViews.clear();
      loadedTeamViews.add(activeTeamView);
    }
    if (resetOnSuccess) {
      clearFormDraft(form);
      form.reset();
    }
    if (scoped) {
      await refreshInvalidatedViews(result.invalidated_views || [activeTeamView]);
    } else {
      render();
      void loadTimeline();
    }
    if (rebasedFromRevision !== undefined && rebasedFromRevision !== null) {
      setStatus(
        `Saved revision ${state.revision} after reloading from revision ${rebasedFromRevision}.`,
        "ok",
      );
    } else {
      setStatus(`Saved revision ${state.revision}.`, "ok");
    }
    return submitMutationResult(true, "", detailedResult);
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Save failed.", "error");
    return submitMutationResult(false, "error", detailedResult);
  } finally {
    setFormBusy(form, false);
  }
}

function submitMutationResult(saved, failureReason, detailedResult) {
  const result = {
    saved: Boolean(saved),
    failureReason: saved ? "" : text(failureReason),
  };
  return detailedResult ? result : result.saved;
}

function mutationResultSaved(result) {
  return typeof result === "object" && result !== null ? Boolean(result.saved) : Boolean(result);
}

function isScopedMutationResponse(result) {
  return result?.response_scope === "view" || Array.isArray(result?.invalidated_views);
}

function applyScopedMutationResponse(result) {
  state = normalizeState({
    ...state,
    revision: result.revision ?? state.revision,
    resource_tokens: mergeResourceTokens(state.resource_tokens, result.resource_tokens || {}),
  });
  (result.invalidated_views || []).forEach((view) => {
    loadedTeamViews.delete(normalizedTeamView(view));
  });
}

async function refreshInvalidatedViews(invalidatedViews) {
  const normalizedInvalidated = new Set(
    (invalidatedViews || []).map(normalizedTeamView),
  );
  if (!normalizedInvalidated.size || normalizedInvalidated.has(activeTeamView)) {
    await loadView(activeTeamView, { force: true });
    return;
  }
  render();
}

async function applyMutationResultPayload(result) {
  if (isScopedMutationResponse(result)) {
    applyScopedMutationResponse(result);
    await refreshInvalidatedViews(result.invalidated_views || [activeTeamView]);
    return;
  }
  state = normalizeState(result);
  loadedTeamViews.clear();
  loadedTeamViews.add(activeTeamView);
  render();
  void loadTimeline();
}

function mutationBodyWithConflictMetadata(endpoint, method, payload) {
  const body = { ...payload, expected_revision: state.revision };
  const context = mutationResourceContext(endpoint, method, payload);
  if (!context) {
    return body;
  }
  if (context.kind === "create") {
    body.conflict_policy = "target_unchanged";
    return body;
  }
  const token = resourceToken(context.collection, context.id);
  if (token) {
    body.conflict_policy = "target_unchanged";
    body.base_resource_token = token;
  }
  return body;
}

function mutationResourceContext(endpoint, method, payload = {}) {
  const normalizedMethod = text(method).toUpperCase();
  const path = text(endpoint).split("?")[0];
  const directTargets = [
    [/^\/teams\/([^/]+)$/, "teams"],
    [/^\/governance-groups\/([^/]+)$/, "governance_groups"],
    [/^\/governance-agenda\/([^/]+)\/handle$/, "governance_agenda"],
    [/^\/projects\/([^/]+)$/, "projects"],
    [/^\/projects\/([^/]+)\/lead$/, "projects"],
    [/^\/roles\/([^/]+)$/, "roles"],
    [/^\/agents\/([^/]+)$/, "agents"],
    [/^\/agents\/([^/]+)\/context-rotation$/, "agents"],
    [/^\/agents\/([^/]+)\/stand-down$/, "agents"],
    [/^\/agents\/([^/]+)\/stand-down\/revoke$/, "agents"],
    [/^\/agents\/([^/]+)\/avatar(?:\/accept-preview)?$/, "agents"],
    [/^\/cases\/([^/]+)$/, "cases"],
    [/^\/tasks\/([^/]+)$/, "tasks"],
    [/^\/review-requests\/([^/]+)\/decision$/, "review_requests"],
    [/^\/action-records\/([^/]+)\/dismiss$/, "action_records"],
    [/^\/review-profiles\/([^/]+)$/, "review_profiles"],
    [/^\/outcome-kinds\/([^/]+)$/, "outcome_kind_definitions"],
  ];
  for (const [pattern, collection] of directTargets) {
    const match = path.match(pattern);
    if (match) {
      return { kind: "target", collection, id: decodeURIComponent(match[1]) };
    }
  }
  if (normalizedMethod !== "POST") {
    return null;
  }
  const createCollections = {
    "/teams": ["teams", payload.id || payload.team_id],
    "/governance-groups": ["governance_groups", payload.id || payload.governance_group_id],
    "/governance-agenda": ["governance_agenda", payload.id || payload.agenda_item_id],
    "/projects": ["projects", payload.id || payload.project_id],
    "/roles": ["roles", payload.id || payload.role_id],
    "/agents": ["agents", payload.id || payload.agent_id],
    "/cases": ["cases", payload.id || payload.case_id],
    "/tasks": ["tasks", payload.id || payload.task_id],
    "/deliverables": ["deliverables", payload.id || payload.deliverable_id],
    "/review-requests": ["review_requests", payload.id || payload.review_request_id],
    "/review-profiles": ["review_profiles", payload.id || payload.review_profile_id],
    "/outcome-kinds": ["outcome_kind_definitions", payload.id || payload.definition_id],
  };
  const create = createCollections[path];
  if (!create) {
    return null;
  }
  return { kind: "create", collection: create[0], id: text(create[1]) };
}

function resourceToken(collection, id) {
  const normalizedCollection = text(collection);
  const normalizedId = text(id);
  if (!normalizedCollection || !normalizedId) {
    return "";
  }
  return text(
    state.resource_tokens?.[normalizedCollection]?.[normalizedId]
      || state[normalizedCollection]?.[normalizedId]?.resource_token,
  );
}

function resourceExists(collection, id) {
  const normalizedCollection = text(collection);
  const normalizedId = text(id);
  return Boolean(normalizedCollection && normalizedId && state[normalizedCollection]?.[normalizedId]);
}

function showRevisionConflictRecoveryStatus({
  form,
  draftKey,
  endpoint,
  method,
  payload,
  options,
  currentRevision,
}) {
  const context = mutationResourceContext(endpoint, method, payload);
  pendingConflictRetry = {
    draftKey,
    endpoint,
    method,
    options,
    mode: retryModeForForm(form),
    editType: text(form?.dataset?.editType),
    editId: text(form?.dataset?.editId),
  };
  if (context?.kind === "target" && !resourceExists(context.collection, context.id)) {
    pendingConflictRetry = null;
    setStatus(
      `Revision conflict. Draft preserved, but the target record is no longer available at revision ${currentRevision}.`,
      "warning",
    );
    return;
  }
  setStatus(
    `Revision conflict. Draft preserved and revision ${currentRevision} loaded; review and retry save.`,
    "warning",
    { label: "Retry save", onClick: () => void retryPendingConflictSave() },
  );
}

function retryModeForForm(form) {
  if (form === elements.editForm || text(form?.dataset?.editType)) {
    return "edit";
  }
  if (form?.hasAttribute?.("data-org-action-form")) {
    return "org-action";
  }
  const formId = formIdentifier(form);
  if (formId === "update-task-form") {
    return "task-update";
  }
  if (formId === "decide-review-form" || form?.hasAttribute?.("data-review-decision-form")) {
    return "review-decision";
  }
  return "create";
}

async function retryPendingConflictSave() {
  if (!guardTeamMutation("retry saves")) {
    return;
  }
  const retry = pendingConflictRetry;
  if (!retry) {
    setStatus("No preserved save is available to retry.", "error");
    return;
  }
  let form = findFormByDraftKey(retry.draftKey);
  if (!form && retry.mode === "edit" && retry.editType && retry.editId) {
    openEditDialog(retry.editType, retry.editId);
    form = findFormByDraftKey(retry.draftKey) || elements.editForm;
    const draft = formDraftRegistry.get(retry.draftKey);
    if (draft) {
      restoreFormDrafts({ [retry.draftKey]: draft });
    }
  }
  if (!form) {
    setStatus("The preserved draft form is no longer open.", "error");
    return;
  }
  pendingConflictRetry = null;
  if (retry.mode === "edit") {
    await submitEditForm(form);
  } else if (retry.mode === "task-update") {
    await submitTaskUpdate(form);
  } else if (retry.mode === "review-decision") {
    await submitReviewDecision(form);
  } else if (retry.mode === "org-action") {
    await submitOrgActionForm(form);
  } else {
    await submitCreateForm(form);
  }
}

async function uploadAgentAvatar(button) {
  if (!guardTeamMutation("upload avatars")) {
    return;
  }
  const form = button.closest("form");
  const agentId = text(button.dataset.avatarUploadAgentId);
  const file = form?.elements.avatar_file?.files?.[0] || null;
  if (!form || !agentId || !file) {
    setStatus("Choose an avatar image to upload.", "error");
    return;
  }
  const body = new FormData();
  body.append("expected_revision", String(state.revision));
  body.append("file", file);
  await submitAvatarFormData(form, `/agents/${encodeURIComponent(agentId)}/avatar/upload`, body);
}

async function generateAvatarPreview(button) {
  if (!guardTeamMutation("generate avatar previews")) {
    return;
  }
  const form = button.closest("form");
  const agentId = text(button.dataset.avatarGenerateAgentId);
  if (!form || !agentId) {
    setStatus("No agent selected.", "error");
    return;
  }
  const data = formData(form);
  setFormBusy(form, true);
  setStatus("Generating avatar preview.", "loading");
  try {
    const response = await teamFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}/avatar/previews`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        style_preset: text(data.avatar_style_preset) || "professional",
        prompt_note: text(data.avatar_prompt_note) || null,
      }),
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    const preview = payload.preview || {};
    const slot = form.querySelector("[data-avatar-preview-slot]");
    if (slot) {
      slot.innerHTML = `
        <div class="avatar-preview-card">
          <img src="${escapeAttr(preview.url)}" alt="Generated avatar preview" />
          <div class="meta-line">Preview ready - ${escapeHtml(preview.style_preset || "generated")}</div>
        </div>
      `;
    }
    const acceptButton = form.querySelector("[data-avatar-accept-agent-id]");
    if (acceptButton) {
      acceptButton.disabled = false;
      acceptButton.dataset.avatarPreviewId = preview.preview_id || "";
    }
    setStatus("Avatar preview generated.", "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Avatar generation failed.", "error");
  } finally {
    setFormBusy(form, false);
  }
}

async function acceptAvatarPreview(button) {
  if (!guardTeamMutation("accept avatar previews")) {
    return;
  }
  const form = button.closest("form");
  const agentId = text(button.dataset.avatarAcceptAgentId);
  const previewId = text(button.dataset.avatarPreviewId);
  if (!form || !agentId || !previewId) {
    setStatus("Generate a preview before accepting it.", "error");
    return;
  }
  await submitAvatarJson(
    form,
    `/agents/${encodeURIComponent(agentId)}/avatar/accept-preview`,
    "POST",
    { preview_id: previewId },
    agentId,
  );
}

async function removeAgentAvatar(button) {
  if (!guardTeamMutation("remove avatars")) {
    return;
  }
  const form = button.closest("form");
  const agentId = text(button.dataset.avatarRemoveAgentId);
  if (!form || !agentId) {
    setStatus("No agent selected.", "error");
    return;
  }
  await submitAvatarJson(
    form,
    `/agents/${encodeURIComponent(agentId)}/avatar`,
    "DELETE",
    {},
    agentId,
  );
}

async function submitAvatarFormData(form, endpoint, body) {
  if (!guardTeamMutation("upload avatars")) {
    return false;
  }
  const drafts = captureFormDrafts();
  setFormBusy(form, true);
  setStatus("Saving avatar.", "loading");
  try {
    const response = await teamFetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-Team-Addon-Response-Scope": "view",
      },
      body,
    });
    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; review and submit again.`, "warning");
      return false;
    }
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    await applyMutationResultPayload(await response.json());
    openEditDialog("agent", text(form.dataset.editId));
    setStatus(`Saved avatar at revision ${state.revision}.`, "ok");
    return true;
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Avatar save failed.", "error");
    return false;
  } finally {
    setFormBusy(form, false);
  }
}

async function submitAvatarJson(form, endpoint, method, payload, agentId) {
  const saved = await submitMutation(
    form,
    endpoint,
    method,
    payload,
    { resetOnSuccess: false },
  );
  if (saved && agentId) {
    openEditDialog("agent", agentId);
  }
  return saved;
}

async function requestContextRotation(button, agentId) {
  if (!guardTeamMutation("clear agent context")) {
    return;
  }
  const normalizedAgentId = text(agentId);
  if (!normalizedAgentId) {
    setStatus("No agent selected.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus("Requesting context clearing.", "loading");
  try {
    const endpoint = `/agents/${encodeURIComponent(normalizedAgentId)}/context-rotation`;
    const response = await teamFetch(
      `${API_BASE}${endpoint}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(mutationBodyWithConflictMetadata(endpoint, "POST", {
          reason: "manual operator request",
        })),
      },
    );

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; request context clearing again.`, "warning");
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    await applyMutationResultPayload(await response.json());
    restoreFormDrafts(drafts);
    setStatus(`Context clearing queued for ${normalizedAgentId}.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Context clearing request failed.", "error");
  } finally {
    if (button.isConnected) {
      button.disabled = false;
    }
  }
}

async function startAgentVoiceCall(button, agentId) {
  if (!guardTeamMutation("start voice calls")) {
    return;
  }
  const normalizedAgentId = text(agentId);
  const conversationId = text(button?.dataset?.agentCallConversationId);
  if (!normalizedAgentId || !state.agents[normalizedAgentId]) {
    setStatus("No agent selected.", "error");
    return;
  }
  if (activeAgentVoiceCall && !agentVoiceCallIsTerminal(activeAgentVoiceCall)) {
    setStatus("A voice call is already active.", "warning");
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || typeof RTCPeerConnection === "undefined") {
    setStatus("Browser voice calls require microphone and WebRTC support.", "error");
    return;
  }
  const call = {
    agentId: normalizedAgentId,
    voiceSessionId: "",
    requestId: createAgentVoiceRequestId(),
    status: "connecting",
    startupPhase: "microphone",
    muted: false,
    cancelRequested: false,
    cleanupRequested: false,
    transcript: [],
    handledFunctionCalls: new Set(),
    mediaStream: null,
    peerConnection: null,
    dataChannel: null,
    remoteAudio: null,
    callsUrl: "",
    clientSecret: "",
    error: "",
  };
  activeAgentVoiceCall = call;
  button.disabled = true;
  renderAgents();
  renderChat();
  setStatus("Connecting voice call.", "loading");
  try {
    const mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    call.mediaStream = mediaStream;
    if (!agentVoiceCallIsCurrent(call)) {
      cleanupAgentVoiceCall(call);
      return;
    }
    if (call.cancelRequested) {
      cleanupAgentVoiceCall(call);
      clearAgentVoiceCallIfCurrent(call);
      setStatus("Voice call cancelled.", "ok");
      return;
    }
    call.startupPhase = "session";
    const response = await teamFetch(
      `${API_BASE}/agents/${encodeURIComponent(normalizedAgentId)}/voice-sessions`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          request_id: call.requestId,
          conversation_id: conversationId || null,
        }),
      },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    syncTeamStateFromChatPayload(payload);
    if (!agentVoiceCallIsCurrent(call)) {
      call.voiceSessionId = text(payload.voice_session_id);
      call.requestId = text(payload.request_id) || call.requestId;
      await endAgentVoiceSessionOnServer(call, "ended", "Cancelled during startup.")
        .catch(() => {});
      cleanupAgentVoiceCall(call);
      return;
    }
    call.voiceSessionId = text(payload.voice_session_id);
    call.requestId = text(payload.request_id) || call.requestId;
    call.clientSecret = realtimeClientSecretValue(payload);
    call.callsUrl = text(payload.realtime?.calls_url) || "https://api.openai.com/v1/realtime/calls";
    if (conversationId) {
      selectedChatConversationId = conversationId;
      void loadConversations({ selectedConversationId: conversationId });
    }
    if (!call.voiceSessionId || !call.clientSecret) {
      throw new Error("Voice call response was missing a session or client secret.");
    }
    if (call.cancelRequested) {
      await endAgentVoiceCallAfterStartupCancel(call);
      return;
    }
    call.startupPhase = "webrtc";
    await connectBrowserRealtimeVoice(call);
    if (!agentVoiceCallIsCurrent(call)) {
      cleanupAgentVoiceCall(call);
      return;
    }
    if (call.cancelRequested) {
      await endAgentVoiceCallAfterStartupCancel(call);
      return;
    }
    call.status = "active";
    call.startupPhase = "active";
    call.error = "";
    renderAgents();
    renderChat();
    await recordAgentVoiceEvent(call.voiceSessionId, {
      status: "active",
      event_type: "browser_call.active",
    });
    if (!agentVoiceCallIsCurrent(call) || call.cancelRequested) {
      return;
    }
    setStatus(`Voice call with ${agentLabel(state.agents[normalizedAgentId])} is live.`, "ok");
  } catch (error) {
    if (!agentVoiceCallIsCurrent(call)) {
      cleanupAgentVoiceCall(call);
      return;
    }
    const message = error instanceof Error ? error.message : "Voice call failed.";
    await failAgentVoiceCall(call, message);
  } finally {
    if (button.isConnected) {
      button.disabled = false;
    }
  }
}

async function connectBrowserRealtimeVoice(call) {
  const peerConnection = new RTCPeerConnection();
  const dataChannel = peerConnection.createDataChannel("oai-events");
  const remoteAudio = document.createElement("audio");
  remoteAudio.autoplay = true;
  remoteAudio.hidden = true;
  remoteAudio.setAttribute("playsinline", "true");
  document.body.appendChild(remoteAudio);
  call.peerConnection = peerConnection;
  call.dataChannel = dataChannel;
  call.remoteAudio = remoteAudio;

  peerConnection.addEventListener("track", (event) => {
    const [stream] = event.streams;
    if (stream) {
      remoteAudio.srcObject = stream;
      void remoteAudio.play().catch(() => {});
    }
  });
  peerConnection.addEventListener("connectionstatechange", () => {
    const status = peerConnection.connectionState;
    if (status === "failed" && !agentVoiceCallIsTerminal(call) && call.status !== "ending") {
      void failAgentVoiceCall(call, `WebRTC connection ${status}.`);
    }
  });
  dataChannel.addEventListener("message", (event) => {
    void handleRealtimeAgentVoiceEvent(call, event.data);
  });
  call.mediaStream.getTracks().forEach((track) => {
    peerConnection.addTrack(track, call.mediaStream);
  });
  const offer = await peerConnection.createOffer();
  await peerConnection.setLocalDescription(offer);
  const response = await teamFetch(call.callsUrl, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${call.clientSecret}`,
      "Content-Type": "application/sdp",
    },
    body: offer.sdp,
  });
  if (!response.ok) {
    throw new Error(await responseText(response));
  }
  await peerConnection.setRemoteDescription({
    type: "answer",
    sdp: await response.text(),
  });
}

function realtimeClientSecretValue(payload) {
  return (
    text(payload?.client_secret?.value) ||
    text(payload?.client_secret?.client_secret?.value) ||
    text(payload?.client_secret)
  );
}

async function recordAgentVoiceEvent(voiceSessionId, payload) {
  const normalizedVoiceSessionId = text(voiceSessionId);
  if (!normalizedVoiceSessionId) {
    return null;
  }
  const response = await teamFetch(
    `${API_BASE}/voice-sessions/${encodeURIComponent(normalizedVoiceSessionId)}/events`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload || {}),
    },
  );
  if (!response.ok) {
    throw new Error(await responseText(response));
  }
  return response.json();
}

async function endAgentVoiceCall(button) {
  if (!guardTeamMutation("end voice calls")) {
    return;
  }
  const call = activeAgentVoiceCall;
  if (!call) {
    return;
  }
  const wasConnecting = call.status === "connecting";
  button.disabled = true;
  call.cancelRequested = true;
  call.status = "ending";
  renderAgents();
  renderChat();
  if (!call.voiceSessionId && !call.requestId) {
    cleanupAgentVoiceCall(call);
    clearAgentVoiceCallIfCurrent(call);
    setStatus("Voice call cancelled.", "ok");
    if (button.isConnected) {
      button.disabled = false;
    }
    return;
  }
  if (!call.voiceSessionId && wasConnecting && call.startupPhase === "microphone") {
    cleanupAgentVoiceCall(call);
    clearAgentVoiceCallIfCurrent(call);
    setStatus("Voice call cancelled.", "ok");
    if (button.isConnected) {
      button.disabled = false;
    }
    return;
  }
  if (!call.voiceSessionId && wasConnecting) {
    cleanupAgentVoiceCall(call);
    setStatus("Cancelling voice call startup.", "loading");
    if (button.isConnected) {
      button.disabled = false;
    }
    return;
  }
  try {
    await endAgentVoiceSessionOnServer(call, "ended", "");
    cleanupAgentVoiceCall(call);
    call.status = "ended";
    clearAgentVoiceCallIfCurrent(call);
    setStatus("Voice call ended.", "ok");
  } catch (error) {
    markAgentVoiceEndFailed(call, "Voice call end failed", error);
    if (button.isConnected) {
      button.disabled = false;
    }
    return;
  }
  if (button.isConnected) {
    button.disabled = false;
  }
}

function agentVoiceCallIsCurrent(call) {
  return activeAgentVoiceCall === call;
}

function createAgentVoiceRequestId() {
  if (globalThis.crypto?.randomUUID) {
    return `browser-call:${globalThis.crypto.randomUUID()}`;
  }
  return `browser-call:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

function clearAgentVoiceCallIfCurrent(call) {
  if (!agentVoiceCallIsCurrent(call)) {
    return;
  }
  activeAgentVoiceCall = null;
  renderAgents();
  renderChat();
}

async function endAgentVoiceCallAfterStartupCancel(call) {
  try {
    await endAgentVoiceSessionOnServer(call, "ended", "Cancelled during startup.");
    cleanupAgentVoiceCall(call);
    call.status = "ended";
    clearAgentVoiceCallIfCurrent(call);
    setStatus("Voice call cancelled.", "ok");
  } catch (error) {
    cleanupAgentVoiceCall(call);
    markAgentVoiceEndFailed(call, "Voice call was stopped locally, but end update failed", error);
  }
}

async function failAgentVoiceCall(call, message) {
  call.status = "failed";
  call.error = message;
  if (agentVoiceCallIsCurrent(call)) {
    renderAgents();
    renderChat();
  }
  if (!call.voiceSessionId && !call.requestId) {
    cleanupAgentVoiceCall(call);
    clearAgentVoiceCallIfCurrent(call);
    setStatus(message, "error");
    return;
  }
  try {
    await endAgentVoiceSessionOnServer(call, "failed", message);
    cleanupAgentVoiceCall(call);
    clearAgentVoiceCallIfCurrent(call);
    setStatus(message, "error");
  } catch (error) {
    cleanupAgentVoiceCall(call);
    markAgentVoiceEndFailed(call, `${message} End update failed`, error);
  }
}

function markAgentVoiceEndFailed(call, prefix, error) {
  call.status = "end_failed";
  call.cleanupRequested = true;
  call.error = `${prefix}: ${error instanceof Error ? error.message : "retry required"}`;
  if (agentVoiceCallIsCurrent(call)) {
    renderAgents();
    renderChat();
  }
  setStatus(call.error, "error");
}

async function endAgentVoiceSessionOnServer(call, status, errorMessage) {
  let endpoint = "";
  if (call.voiceSessionId) {
    endpoint = `${API_BASE}/voice-sessions/${encodeURIComponent(call.voiceSessionId)}/end`;
  } else if (call.requestId) {
    endpoint = `${API_BASE}/voice-session-requests/${encodeURIComponent(call.requestId)}/end`;
  } else {
    return null;
  }
  const transcript = agentVoiceTranscriptExcerpt(call, { limit: 60 });
  const response = await teamFetch(endpoint, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      status,
      transcript: transcript || null,
      error: errorMessage || null,
    }),
  });
  if (!response.ok) {
    throw new Error(await responseText(response));
  }
  const payload = await response.json();
  call.voiceSessionId = call.voiceSessionId || text(payload.voice_session_id);
  return payload;
}

function cleanupAgentVoiceCall(call) {
  try {
    call.dataChannel?.close();
  } catch {
    // Ignore close races from the browser WebRTC stack.
  }
  try {
    call.peerConnection?.close();
  } catch {
    // Ignore close races from the browser WebRTC stack.
  }
  call.mediaStream?.getTracks?.().forEach((track) => {
    track.stop();
  });
  if (call.remoteAudio) {
    call.remoteAudio.pause();
    call.remoteAudio.srcObject = null;
    call.remoteAudio.remove();
  }
  call.dataChannel = null;
  call.peerConnection = null;
  call.mediaStream = null;
  call.remoteAudio = null;
}

function toggleAgentVoiceMute() {
  if (!guardTeamMutation("change voice calls")) {
    return;
  }
  const call = activeAgentVoiceCall;
  if (!call?.mediaStream) {
    return;
  }
  call.muted = !call.muted;
  call.mediaStream.getAudioTracks().forEach((track) => {
    track.enabled = !call.muted;
  });
  renderAgents();
  renderChat();
}

async function handleRealtimeAgentVoiceEvent(call, rawEvent) {
  let event;
  try {
    event = JSON.parse(rawEvent);
  } catch {
    return;
  }
  if (event.type === "error") {
    const message = text(event.error?.message) || "Realtime voice error.";
    call.error = message;
    renderAgents();
    renderChat();
    setStatus(message, "error");
    return;
  }
  const transcript = realtimeTranscriptFromEvent(event);
  if (transcript) {
    call.transcript.push(transcript);
    call.transcript = call.transcript.slice(-40);
    renderAgents();
    renderChat();
    if (call.voiceSessionId) {
      void recordAgentVoiceEvent(call.voiceSessionId, {
        text: transcript.text,
        speaker: transcript.speaker,
        event_type: transcript.eventType,
        metadata: { realtime_event_type: event.type },
      }).catch(() => {});
    }
  }
  const functionCall = realtimeFunctionCallFromEvent(event);
  if (functionCall) {
    await handleRealtimeFunctionCall(call, functionCall);
  }
}

function realtimeTranscriptFromEvent(event) {
  const eventType = text(event.type);
  if (eventType === "conversation.item.input_audio_transcription.completed") {
    const transcript = text(event.transcript);
    return transcript ? { speaker: "operator", text: transcript, eventType } : null;
  }
  if (
    eventType === "response.audio_transcript.done" ||
    eventType === "response.output_audio_transcript.done"
  ) {
    const transcript = text(event.transcript);
    return transcript ? { speaker: "agent", text: transcript, eventType } : null;
  }
  return null;
}

function realtimeFunctionCallFromEvent(event) {
  if (event.type === "response.function_call_arguments.done") {
    return {
      callId: text(event.call_id),
      name: text(event.name),
      argumentsText: text(event.arguments),
    };
  }
  if (event.type === "response.output_item.done" && event.item?.type === "function_call") {
    return {
      callId: text(event.item.call_id),
      name: text(event.item.name),
      argumentsText: text(event.item.arguments),
    };
  }
  return null;
}

async function handleRealtimeFunctionCall(call, functionCall) {
  if (!functionCall.callId || call.handledFunctionCalls.has(functionCall.callId)) {
    return;
  }
  call.handledFunctionCalls.add(functionCall.callId);
  if (functionCall.name !== "query_origin_agent") {
    sendRealtimeFunctionOutput(call, functionCall.callId, {
      status: "failed",
      error: `Unsupported function ${functionCall.name || "<empty>"}.`,
    });
    return;
  }
  const args = parseRealtimeFunctionArguments(functionCall.argumentsText);
  const question = text(args.question || args.query);
  if (!question) {
    sendRealtimeFunctionOutput(call, functionCall.callId, {
      status: "failed",
      error: "question is required",
    });
    return;
  }
  const previousStatus = call.status;
  call.status = "querying";
  renderAgents();
  renderChat();
  try {
    const query = await createAgentVoiceQuery(call, {
      question,
      reason: text(args.reason),
      functionCallId: functionCall.callId,
    });
    const completed = await waitForAgentVoiceQuery(call.voiceSessionId, query.query_id);
    sendRealtimeFunctionOutput(call, functionCall.callId, {
      status: completed.status,
      answer: completed.answer,
      error: completed.error,
      citations: completed.citations || [],
      confidence: completed.confidence || null,
    });
  } catch (error) {
    sendRealtimeFunctionOutput(call, functionCall.callId, {
      status: "failed",
      error: error instanceof Error ? error.message : "origin agent query failed",
    });
  } finally {
    if (!agentVoiceCallIsTerminal(call)) {
      call.status = previousStatus === "connecting" ? "active" : previousStatus;
      renderAgents();
      renderChat();
    }
  }
}

function parseRealtimeFunctionArguments(value) {
  try {
    const parsed = JSON.parse(value || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

async function createAgentVoiceQuery(call, { question, reason, functionCallId }) {
  const response = await teamFetch(
    `${API_BASE}/voice-sessions/${encodeURIComponent(call.voiceSessionId)}/queries`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        question,
        transcript_excerpt: agentVoiceTranscriptExcerpt(call),
        deadline_seconds: 90,
        metadata: {
          reason: reason || null,
          realtime_call_id: functionCallId,
          source: "browser_realtime_function",
        },
      }),
    },
  );
  if (!response.ok) {
    throw new Error(await responseText(response));
  }
  return response.json();
}

async function waitForAgentVoiceQuery(voiceSessionId, queryId) {
  const startedAt = Date.now();
  const timeoutMs = 90000;
  while (Date.now() - startedAt < timeoutMs) {
    const response = await teamFetch(
      `${API_BASE}/voice-sessions/${encodeURIComponent(voiceSessionId)}/queries/${encodeURIComponent(queryId)}`,
      { headers: { Accept: "application/json" } },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    if (["answered", "failed", "timeout"].includes(payload.status)) {
      return payload;
    }
    await delay(1200);
  }
  const timeoutResponse = await teamFetch(
    `${API_BASE}/voice-sessions/${encodeURIComponent(voiceSessionId)}/queries/${encodeURIComponent(queryId)}/timeout`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ error: "Browser voice query timed out." }),
    },
  );
  if (!timeoutResponse.ok) {
    throw new Error(await responseText(timeoutResponse));
  }
  return timeoutResponse.json();
}

function sendRealtimeFunctionOutput(call, callId, output) {
  if (!call.dataChannel || call.dataChannel.readyState !== "open") {
    return;
  }
  call.dataChannel.send(
    JSON.stringify({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify(output),
      },
    }),
  );
  call.dataChannel.send(JSON.stringify({ type: "response.create" }));
}

function agentVoiceTranscriptExcerpt(call, { limit = 12 } = {}) {
  return (call.transcript || [])
    .slice(-limit)
    .map((entry) => `${text(entry.speaker) || "voice"}: ${entry.text}`)
    .join("\n");
}

async function requestAgentStandDown(button, agentId, requestedScope) {
  if (!guardTeamMutation("stand down agents")) {
    return;
  }
  const normalizedAgentId = text(agentId);
  if (!normalizedAgentId) {
    setStatus("No agent selected.", "error");
    return;
  }
  const scope = text(requestedScope).toLowerCase();
  if (!["agent", "subtree"].includes(scope)) {
    setStatus("Stand-down scope must be agent or subtree.", "error");
    return;
  }
  if (
    scope === "subtree"
    && !window.confirm(
      `Stand down ${normalizedAgentId} and every subordinate in that agent's subtree?`,
    )
  ) {
    return;
  }
  const reason = text(window.prompt(`Reason to stand down ${normalizedAgentId}`));
  if (!reason) {
    setStatus("Stand-down reason is required.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus(scope === "agent" ? "Standing down agent." : "Standing down agent subtree.", "loading");
  try {
    const endpoint = `/agents/${encodeURIComponent(normalizedAgentId)}/stand-down`;
    const response = await teamFetch(
      `${API_BASE}${endpoint}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(mutationBodyWithConflictMetadata(endpoint, "POST", {
          reason,
          scope,
        })),
      },
    );

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; stand down again.`, "warning");
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    await applyMutationResultPayload(await response.json());
    restoreFormDrafts(drafts);
    setStatus(`Stood down ${normalizedAgentId}.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Stand-down request failed.", "error");
  } finally {
    if (button.isConnected) {
      button.disabled = false;
    }
  }
}

async function revokeAgentStandDown(button, agentId) {
  if (!guardTeamMutation("revoke stand-downs")) {
    return;
  }
  const normalizedAgentId = text(agentId);
  if (!normalizedAgentId) {
    setStatus("No agent selected.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus("Revoking stand-down.", "loading");
  try {
    const endpoint = `/agents/${encodeURIComponent(normalizedAgentId)}/stand-down/revoke`;
    const response = await teamFetch(
      `${API_BASE}${endpoint}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(mutationBodyWithConflictMetadata(endpoint, "POST", {})),
      },
    );

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; revoke again.`, "warning");
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    await applyMutationResultPayload(await response.json());
    restoreFormDrafts(drafts);
    setStatus(`Revoked stand-down for ${normalizedAgentId}.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Stand-down revoke failed.", "error");
  } finally {
    if (button.isConnected) {
      button.disabled = false;
    }
  }
}

async function dismissActionRecordAttention(button, actionRecordId) {
  if (!guardTeamMutation("dismiss failed actions")) {
    return;
  }
  if (!actionRecordId) {
    setStatus("No action record selected.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus("Dismissing failed action.", "loading");
  try {
    const endpoint = `/action-records/${encodeURIComponent(actionRecordId)}/dismiss`;
    const response = await teamFetch(
      `${API_BASE}${endpoint}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(mutationBodyWithConflictMetadata(endpoint, "POST", {
          dismissed_by: "operator",
        })),
      },
    );

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; review and dismiss again.`, "warning");
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    await applyMutationResultPayload(await response.json());
    restoreFormDrafts(drafts);
    setStatus(`Dismissed failed action ${actionRecordId}.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Dismiss failed.", "error");
  } finally {
    if (button.isConnected) {
      button.disabled = false;
    }
  }
}

async function resolveTerminalBlocker(button, blockerId, note) {
  if (!guardTeamMutation("resolve terminal blockers")) {
    return;
  }
  const normalizedBlockerId = text(blockerId);
  if (!normalizedBlockerId) {
    setStatus("No blocker selected.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus(`Resolving blocker ${normalizedBlockerId}.`, "loading");
  const endpoint = `/blockers/${encodeURIComponent(normalizedBlockerId)}/resolve`;
  try {
    const response = await teamFetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Team-Addon-Response-Scope": "view",
      },
      body: JSON.stringify(
        mutationBodyWithConflictMetadata(endpoint, "POST", { note }),
      ),
    });

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(
        `Revision conflict. Reloaded revision ${current}; verify the blocker and resolve again.`,
        "warning",
      );
      return;
    }
    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    await applyMutationResultPayload(await response.json());
    restoreFormDrafts(drafts);
    setStatus(`Resolved blocker ${normalizedBlockerId}.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Blocker resolution failed.", "error");
  } finally {
    if (button.isConnected) {
      button.disabled = false;
    }
  }
}

async function startDependencyCompositionRecovery(button, blockerId, resolverAgentId) {
  if (!guardTeamMutation("start dependency composition recoveries")) {
    return;
  }
  const normalizedBlockerId = text(blockerId);
  const normalizedResolverId = text(resolverAgentId);
  if (!normalizedBlockerId || !normalizedResolverId) {
    setStatus("A blocker and resolver agent are required.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus(`Creating merge recovery for ${normalizedBlockerId}.`, "loading");
  const endpoint = `/blockers/${encodeURIComponent(normalizedBlockerId)}/dependency-composition-recovery`;
  try {
    const response = await teamFetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Team-Addon-Response-Scope": "view",
      },
      body: JSON.stringify(
        mutationBodyWithConflictMetadata(endpoint, "POST", {
          resolver_agent_id: normalizedResolverId,
        }),
      ),
    });
    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      setStatus(
        `Revision conflict. Reloaded revision ${conflict.current_revision ?? state.revision}; verify the recovery and retry.`,
        "warning",
      );
      return;
    }
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const body = await response.json();
    await applyMutationResultPayload(body);
    restoreFormDrafts(drafts);
    const taskId = text(body.dependency_composition_recovery?.recovery_task_id);
    setStatus(
      taskId
        ? `Created dependency merge recovery task ${taskId}.`
        : `Started dependency merge recovery for ${normalizedBlockerId}.`,
      "ok",
    );
  } catch (error) {
    setStatus(
      error instanceof Error ? error.message : "Dependency merge recovery failed.",
      "error",
    );
  } finally {
    if (button.isConnected) {
      button.disabled = false;
    }
  }
}

async function recoverCandidateCorrectionSuccessor(
  button,
  blockerId,
  action,
  reason = null,
  instruction = null,
) {
  if (!guardTeamMutation("recover candidate correction successors")) {
    return;
  }
  const normalizedBlockerId = text(blockerId);
  const normalizedAction = text(action);
  if (!normalizedBlockerId || !normalizedAction) {
    setStatus("No candidate-correction recovery action was selected.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus(`${humanizeToken(normalizedAction)} for ${normalizedBlockerId}.`, "loading");
  const endpoint = `/candidate-correction-recoveries/${encodeURIComponent(normalizedBlockerId)}`;
  try {
    const payload = { action: normalizedAction };
    if (text(reason)) {
      payload.reason = text(reason);
    }
    if (text(instruction)) {
      payload.instruction = text(instruction);
    }
    const response = await teamFetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Team-Addon-Response-Scope": "view",
      },
      body: JSON.stringify(
        mutationBodyWithConflictMetadata(endpoint, "POST", payload),
      ),
    });
    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      setStatus(
        `Revision conflict. Reloaded revision ${conflict.current_revision ?? state.revision}; verify the recovery and retry.`,
        "warning",
      );
      return;
    }
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const body = await response.json();
    await applyMutationResultPayload(body);
    restoreFormDrafts(drafts);
    const recovery = body.candidate_correction_recovery || {};
    const recoveryTaskId = text(
      recovery.created_recovery_task_id ||
      recovery.existing_recovery_task_id ||
      recovery.recovery_task_id,
    );
    const releaseId = text(
      recovery.release_package_id ||
      recovery.created_successor_release_package_ids?.[0] ||
      recovery.existing_successor_release_package_ids?.[0],
    );
    setStatus(
      releaseId
        ? `Candidate recovery created or found successor ${releaseId}.`
        : recoveryTaskId
          ? `Candidate recovery created or found pinned task ${recoveryTaskId}.`
          : `Candidate recovery ${humanizeToken(normalizedAction)} completed.`,
      "ok",
    );
  } catch (error) {
    setStatus(
      error instanceof Error ? error.message : "Candidate correction recovery failed.",
      "error",
    );
  } finally {
    if (button.isConnected) {
      button.disabled = false;
    }
  }
}

async function resumeReleaseRecovery(button, blockerId, taskId) {
  if (!guardTeamMutation("resume release recoveries")) {
    return;
  }
  const blocker = state.blockers?.[text(blockerId)];
  const recoveryTask = state.tasks?.[text(taskId)];
  if (
    !blocker ||
    text(blocker.state) !== "open" ||
    text(blocker.source_kind) !== "release_package" ||
    !["governance", "operator"].includes(text(blocker.route))
  ) {
    setStatus("This release blocker is no longer eligible for operator recovery.", "error");
    return;
  }
  if (
    !recoveryTask ||
    text(recoveryTask.created_by_actor) !== "system:git-merge-recovery" ||
    text(recoveryTask.owner_agent_id) === "" ||
    text(recoveryTask.project_id) === ""
  ) {
    setStatus("The release blocker has no active merge-recovery task to resume.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  const releaseId = text(blocker.source_id);
  setStatus(`Queuing release recovery ${recoveryTask.id}.`, "loading");
  const endpoint = "/operator-directives";
  try {
    const response = await teamFetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Team-Addon-Response-Scope": "view",
      },
      body: JSON.stringify(
        mutationBodyWithConflictMetadata(endpoint, "POST", {
          team_id: blocker.team_id,
          recipient_agent_id: recoveryTask.owner_agent_id,
          project_id: recoveryTask.project_id,
          task_id: recoveryTask.id,
          title: `Resume release recovery for ${releaseId}`,
          message: (
            `Resume the existing recovery task ${recoveryTask.id} for release ${releaseId}. ` +
            "The control-plane schema is current. Reuse the already resolved agents/... ref when valid, " +
            "then call team_recover_release with its exact commit SHA. Do not create a replacement release package."
          ),
          source: "admin_ui_release_recovery",
          source_message_id: `release-recovery:${blocker.id}:${recoveryTask.id}`,
          created_by: "operator:admin-ui",
        }),
      ),
    });

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(
        `Revision conflict. Reloaded revision ${current}; verify the release blocker and resume again.`,
        "warning",
      );
      return;
    }
    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    await applyMutationResultPayload(await response.json());
    restoreFormDrafts(drafts);
    setStatus(`Queued release recovery ${recoveryTask.id} for its assigned agent.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Release recovery could not be resumed.", "error");
  } finally {
    if (button.isConnected) {
      button.disabled = false;
    }
  }
}

async function markGovernanceAgendaHandled(button, agendaItemId) {
  if (!guardTeamMutation("mark governance agenda handled")) {
    return;
  }
  if (!agendaItemId) {
    setStatus("No governance agenda item selected.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus("Marking governance agenda item handled.", "loading");
  try {
    const endpoint = `/governance-agenda/${encodeURIComponent(agendaItemId)}/handle`;
    const response = await teamFetch(
      `${API_BASE}${endpoint}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(mutationBodyWithConflictMetadata(endpoint, "POST", {})),
      },
    );

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await reloadActiveTeamView({ preserveForms: true });
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; mark handled again.`, "warning");
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    await applyMutationResultPayload(await response.json());
    restoreFormDrafts(drafts);
    setStatus(`Marked governance agenda item ${agendaItemId} handled.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Agenda update failed.", "error");
  } finally {
    if (button.isConnected) {
      button.disabled = false;
    }
  }
}

function endpointForForm(form, data) {
  const templateEndpoint = form.dataset.templateEndpoint;
  if (templateEndpoint) {
    const templateId = text(data.template_id);
    if (!templateId) {
      setStatus("Template is required.", "error");
      return null;
    }
    return templateEndpoint.replace("{template_id}", encodeURIComponent(templateId));
  }
  return form.dataset.endpoint || null;
}

function payloadForForm(form, data = formData(form)) {
  const formId = formSemanticIdentifier(form);
  if (formId === "setup-template-form") {
    const template = selectedTemplate();
    const roles = {};
    (template?.roles || []).forEach((role) => {
      const roleKey = text(role.key);
      if (!roleKey) {
        return;
      }
      roles[roleKey] = {
        session_id: text(data[`${roleKey}_session_id`]),
      };
    });
    const payload = {
      team: {
        name: text(data.team_name),
      },
      project: {
        name: text(data.project_name),
      },
      roles,
    };
    const operatorTarget = text(data.operator_communication_target_id);
    if (operatorTarget) {
      payload.team.operator_communication_target_id = operatorTarget;
    }
    payload.team.escalate_to_governance = Boolean(
      form.elements.escalate_to_governance?.checked,
    );
    const maxCost = numberOrNull(data.max_cost_usd);
    if (maxCost !== null) {
      payload.team.max_cost_usd = maxCost;
    }
    const costWarningRatio = numberOrNull(data.cost_warning_ratio);
    if (costWarningRatio !== null) {
      payload.team.cost_warning_ratio = costWarningRatio;
    }
    payload.team.cost_budget_renewal_mode = text(data.cost_budget_renewal_mode) || "manual";
    if (templateUsesGit(template)) {
      const git = gitConfigForForm(data);
      if (git) {
        payload.project.git = git;
      }
    }
    return payload;
  }
  if (formId === "create-team-form" || formId === "create-team-from-teams-form") {
    const payload = {
      name: text(data.name),
      manager_session_id: text(data.manager_session_id),
    };
    const managerRoleName = text(data.manager_role_name);
    if (managerRoleName) {
      payload.manager_role_name = managerRoleName;
    }
    const managerDisplayName = text(data.manager_display_name);
    if (managerDisplayName) {
      payload.manager_display_name = managerDisplayName;
    }
    const operatorTarget = text(data.operator_communication_target_id);
    if (operatorTarget) {
      payload.operator_communication_target_id = operatorTarget;
    }
    payload.escalate_to_governance = Boolean(
      form.elements.escalate_to_governance?.checked,
    );
    const maxCost = numberOrNull(data.max_cost_usd);
    if (maxCost !== null) {
      payload.max_cost_usd = maxCost;
    }
    const costWarningRatio = numberOrNull(data.cost_warning_ratio);
    if (costWarningRatio !== null) {
      payload.cost_warning_ratio = costWarningRatio;
    }
    payload.cost_budget_renewal_mode = text(data.cost_budget_renewal_mode) || "manual";
    return payload;
  }
  if (formId === "create-governance-group-form") {
    const payload = {
      name: text(data.name),
      manager_agent_id: text(data.manager_agent_id),
      member_team_ids: splitList(data.member_team_ids),
      goals: governanceGoalsFromForm(form),
    };
    const maxCost = numberOrNull(data.max_cost_usd);
    if (maxCost !== null) {
      payload.max_cost_usd = maxCost;
    }
    return payload;
  }
  if (formId === "create-governance-agenda-form") {
    const payload = {
      governance_group_id: text(data.governance_group_id),
      item_type: text(data.item_type),
      priority: text(data.priority) || "normal",
      title: text(data.title),
      summary: text(data.summary),
    };
    for (const key of ["team_id", "project_id", "agent_id"]) {
      const value = text(data[key]);
      if (value) {
        payload[key] = value;
      }
    }
    return payload;
  }
  if (formId === "create-project-form") {
    const goals = [];
    const goalDescription = text(data.goal_description);
    if (goalDescription) {
      goals.push({
        description: goalDescription,
      });
    }
    const payload = {
      team_id: text(data.team_id),
      name: text(data.name),
      lead_agent_id: text(data.lead_agent_id),
      directory_path: text(data.directory_path) || "root",
      goals,
    };
    const git = gitConfigForForm(data);
    if (git) {
      payload.git = git;
    }
    const releasePolicy = releasePolicyForForm(data, form);
    if (releasePolicy) {
      payload.release_policy = releasePolicy;
    }
    return payload;
  }
  if (formId === "create-role-form") {
    const payload = {
      team_id: text(data.team_id),
      name: text(data.name),
      description: text(data.description),
      permissions: splitList(data.permissions),
      ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
    };
    const projectId = text(data.project_id);
    if (projectId) {
      payload.project_id = projectId;
    }
    return payload;
  }
  if (formId === "create-case-form") {
    const payload = {
      team_id: text(data.team_id),
      title: text(data.title),
      description: text(data.description),
      status: text(data.status) || "open",
      priority: text(data.priority) || "normal",
      tags: splitList(data.tags),
      task_ids: splitList(data.task_ids),
    };
    const projectId = text(data.project_id);
    if (projectId) {
      payload.project_id = projectId;
    }
    const ownerAgentId = text(data.owner_agent_id);
    if (ownerAgentId) {
      payload.owner_agent_id = ownerAgentId;
    }
    const source = text(data.source);
    if (source) {
      payload.source = source;
    }
    const sourceRef = text(data.source_ref);
    if (sourceRef) {
      payload.source_ref = sourceRef;
    }
    return payload;
  }
  if (formId === "register-agent-form") {
    const requestedTeamId = text(data.team_id);
    const normalizedTeamId = requestedTeamId === noTeamRecordId ? null : requestedTeamId || null;
    const payload = {
      team_id: normalizedTeamId,
      project_id: text(data.project_id) || null,
      role_id: text(data.role_id) || null,
      session_id: text(data.session_id),
      reports_to_agent_id: text(data.reports_to_agent_id) || null,
      ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
    };
    const displayName = text(data.display_name);
    if (displayName) {
      payload.display_name = displayName;
    }
    return payload;
  }
  if (formId === "create-task-form") {
    const payload = {
      owner_agent_id: text(data.owner_agent_id),
      project_id: text(data.project_id),
      priority: numberOrZero(data.priority),
      title: text(data.title),
      description: text(data.description),
      created_by_agent_id: text(data.created_by_agent_id),
    };
    const authoredBy = taskAuthoredByForForm(data);
    if (authoredBy) payload.authored_by = authoredBy;
    const caseId = text(data.case_id);
    if (caseId) {
      payload.case_id = caseId;
    }
    if (text(data.state)) {
      payload.state = text(data.state);
    }
    if (text(data.git_base_branch)) {
      payload.git_base_branch = text(data.git_base_branch);
    }
    const parentTaskId = text(data.parent_task_id);
    if (parentTaskId) {
      payload.parent_task_id = parentTaskId;
    }
    payload.dependency_task_ids = splitList(data.dependency_task_ids);
    const integrationBoundary = Boolean(
      form.elements.integration_boundary?.checked,
    );
    const requireGitProvenance = Boolean(
      form.elements.require_git_provenance?.checked,
    );
    const requiredContributionTaskIds = splitList(
      data.required_contribution_task_ids,
    );
    if (
      integrationBoundary
      || requireGitProvenance
      || requiredContributionTaskIds.length > 0
    ) {
      payload.integration_boundary = integrationBoundary;
      payload.deliverable_contract = {
        require_git_provenance: requireGitProvenance,
        required_contribution_task_ids: requiredContributionTaskIds,
      };
    }
    return payload;
  }
  if (formId === "create-review-profile-form") {
    const payload = {
      team_id: text(data.team_id),
      name: text(data.name),
      description: text(data.description),
      applicable_role_ids: splitList(data.applicable_role_ids),
      required_outcome_kinds: splitList(data.required_outcome_kinds),
      criteria: reviewProfileCriteriaFromForm(form),
    };
    const projectId = text(data.project_id);
    if (projectId) {
      payload.project_id = projectId;
    }
    return payload;
  }
  if (formId === "create-outcome-kind-form") {
    return {
      team_id: text(data.team_id),
      project_id: form.elements.team_wide?.checked ? null : text(data.project_id) || null,
      kind: text(data.kind),
      name: text(data.name),
      description: text(data.description),
      reference_requirements: text(data.reference_requirements),
      evidence_requirements: text(data.evidence_requirements),
    };
  }
  if (formId === "record-deliverable-form") {
    const payload = {
      task_id: text(data.task_id),
      producer_agent_id: text(data.producer_agent_id),
      summary: text(data.summary),
      artifact_refs: splitList(data.artifact_refs),
    };
    if (text(data.state)) {
      payload.state = text(data.state);
    }
    return payload;
  }
  if (formId === "record-review-form") {
    const payload = {
      deliverable_id: text(data.deliverable_id),
      reviewer_agent_id: text(data.reviewer_agent_id),
      findings: splitList(data.findings),
    };
    const profileId = text(data.review_profile_id);
    if (profileId) {
      payload.review_profile_id = profileId;
    }
    const instructions = text(data.instructions);
    if (instructions) {
      payload.instructions = instructions;
    }
    if (text(data.state)) {
      payload.state = text(data.state);
    }
    return payload;
  }
  return {};
}

function gitConfigForForm(data) {
  const managedRepoId = text(data.managed_repo_id);
  if (!managedRepoId) {
    return null;
  }
  const git = { managed_repo_id: managedRepoId };
  const directoryPath = text(data.directory_path) || "root";
  git.directory_path = directoryPath;
  const optionalFields = [
    "base_branch",
    "canonical_release_branch_prefix",
  ];
  optionalFields.forEach((field) => {
    const value = text(data[field]);
    if (value) {
      git[field] = value;
    }
  });
  const authorName = text(data.agent_author_name);
  const authorEmail = text(data.agent_author_email);
  if (authorName || authorEmail) {
    git.agent_author_identity = { name: authorName, email: authorEmail };
  }
  const committerName = text(data.agent_committer_name);
  const committerEmail = text(data.agent_committer_email);
  if (committerName || committerEmail) {
    git.agent_committer_identity = { name: committerName, email: committerEmail };
  }
  return git;
}

function taskAuthoredByForForm(data) {
  const name = text(data.task_author_name);
  const email = text(data.task_author_email);
  return name || email ? { name, email } : null;
}

function releasePolicyForForm(data, form = null) {
  const raw = text(data.release_policy_json);
  if (!raw && !form?.elements?.human_review_enabled?.checked) {
    return null;
  }
  const policy = raw ? JSON.parse(raw) : {};
  if (!policy || Array.isArray(policy) || typeof policy !== "object") {
    throw new Error("Release policy must be a JSON object.");
  }
  if (form?.elements?.human_review_enabled) {
    delete policy.human_review_requirement;
    if (form.elements.human_review_enabled.checked) {
      const reviewerGroupIds = splitList(data.human_reviewer_group_ids);
      if (!reviewerGroupIds.length) {
        throw new Error("Human review requires at least one reviewer group ID.");
      }
      policy.human_review_requirement = {
        reviewer_group_ids: reviewerGroupIds,
        checklist: humanReviewChecklistFromForm(form),
        test_instructions: text(data.human_review_test_instructions),
        preparation_mode: text(data.human_review_preparation_mode) || "manual",
      };
    }
  }
  if (!Object.keys(policy).length) {
    return null;
  }
  return policy;
}

function releasePolicyWithoutHumanJson(policy) {
  if (!policy) {
    return "";
  }
  const bounded = { ...policy };
  delete bounded.human_review_requirement;
  return JSON.stringify(bounded, null, 2);
}

function renderHumanReviewPolicyEditor(requirement = null) {
  return `
    <fieldset class="human-review-policy-editor">
      <legend>Human release review</legend>
      ${checkboxField("Require authenticated human approval", "human_review_enabled", Boolean(requirement))}
      ${textareaField("Reviewer group IDs", "human_reviewer_group_ids", (requirement?.reviewer_group_ids || []).join("\n"), {
        rows: 3,
        placeholder: "release-approvers",
      })}
      ${renderHumanReviewChecklistEditor(requirement?.checklist || [])}
      ${textareaField("Test instructions", "human_review_test_instructions", requirement?.test_instructions || "", {
        rows: 4,
        placeholder: "Open the preview, execute the release scenario, and attach evidence.",
      })}
      <label>Preparation mode
        <select name="human_review_preparation_mode">
          <option value="manual"${requirement?.preparation_mode !== "workflow" ? " selected" : ""}>Manual</option>
          <option value="workflow"${requirement?.preparation_mode === "workflow" ? " selected" : ""}>Workflow-prepared preview</option>
        </select>
      </label>
      <p class="field-help">Reviewer identity and eligible users are resolved by the Admin Web directory. This editor cannot set users, identity snapshots, quorums, or bypasses.</p>
    </fieldset>
  `;
}

function renderHumanReviewChecklistEditor(checklist = []) {
  return `
    <fieldset class="human-review-checklist-editor" data-human-review-checklist-editor>
      <legend>Acceptance checklist</legend>
      <div class="human-review-checklist-editor-rows" data-human-review-checklist-rows>
        ${(checklist || []).map((item) => renderHumanReviewChecklistRow(item)).join("")}
      </div>
      <p class="human-review-checklist-empty" data-human-review-checklist-empty>No acceptance checks configured.</p>
      <button
        class="record-action human-review-checklist-add"
        type="button"
        data-human-review-checklist-add
      >Add check</button>
      <p class="field-help">Each release snapshots these checks in this order. Stable keys are generated from labels unless you set one explicitly.</p>
    </fieldset>
  `;
}

function renderHumanReviewChecklistRow(item = {}) {
  return `
    <div class="human-review-checklist-editor-row" data-human-review-checklist-row>
      <label>
        <span>Label</span>
        <input
          name="human_review_checklist_label"
          value="${escapeAttr(item.label || "")}"
          placeholder="Primary operator flow works"
        />
      </label>
      <label class="human-review-checklist-description">
        <span>Description</span>
        <textarea
          name="human_review_checklist_description"
          rows="2"
          placeholder="Explain what the reviewer should verify."
        >${escapeHtml(item.description || "")}</textarea>
      </label>
      <label>
        <span>Stable key</span>
        <input
          name="human_review_checklist_id"
          value="${escapeAttr(item.id || "")}"
          placeholder="Auto from label"
        />
        <small data-human-review-checklist-key-preview></small>
      </label>
      <label class="checkbox-label human-review-checklist-required">
        <input
          name="human_review_checklist_required"
          type="checkbox"
          ${item.required === false ? "" : "checked"}
        />
        Required
      </label>
      <div class="human-review-checklist-row-actions" aria-label="Checklist order and removal">
        <button
          class="record-action"
          type="button"
          data-human-review-checklist-move="up"
          aria-label="Move checklist item up"
        >Move up</button>
        <button
          class="record-action"
          type="button"
          data-human-review-checklist-move="down"
          aria-label="Move checklist item down"
        >Move down</button>
        <button
          class="record-action human-review-checklist-remove"
          type="button"
          data-human-review-checklist-remove
        >Remove</button>
      </div>
    </div>
  `;
}

function syncHumanReviewChecklistEditor(editor) {
  if (!editor) {
    return;
  }
  const rows = Array.from(editor.querySelectorAll("[data-human-review-checklist-row]"));
  const mutationLocked = teamMutationLocked();
  rows.forEach((row, index) => {
    const up = row.querySelector('[data-human-review-checklist-move="up"]');
    const down = row.querySelector('[data-human-review-checklist-move="down"]');
    if (up) {
      up.disabled = mutationLocked || index === 0;
      up.setAttribute("aria-disabled", String(up.disabled));
    }
    if (down) {
      down.disabled = mutationLocked || index === rows.length - 1;
      down.setAttribute("aria-disabled", String(down.disabled));
    }
    const explicitId = text(
      row.querySelector('[name="human_review_checklist_id"]')?.value,
    );
    const preview = row.querySelector("[data-human-review-checklist-key-preview]");
    if (preview) {
      preview.textContent = explicitId
        ? "Using the stored key."
        : `Generated key: ${humanReviewChecklistItemId(row, index)}`;
    }
  });
  const empty = editor.querySelector("[data-human-review-checklist-empty]");
  if (empty) {
    empty.hidden = rows.length > 0;
  }
}

function addHumanReviewChecklistRow(editor) {
  const rows = editor?.querySelector?.("[data-human-review-checklist-rows]");
  if (!rows) {
    return;
  }
  const wrapper = document.createElement("div");
  wrapper.innerHTML = renderHumanReviewChecklistRow({});
  const row = wrapper.firstElementChild;
  if (!row) {
    return;
  }
  rows.appendChild(row);
  decorateFieldTooltips(row);
  syncHumanReviewChecklistEditor(editor);
  rememberFormDraft(editor.closest("form"));
  row.querySelector('[name="human_review_checklist_label"]')?.focus();
}

function removeHumanReviewChecklistRow(button) {
  const editor = button.closest("[data-human-review-checklist-editor]");
  button.closest("[data-human-review-checklist-row]")?.remove();
  syncHumanReviewChecklistEditor(editor);
  rememberFormDraft(editor?.closest("form"));
}

function moveHumanReviewChecklistRow(button) {
  const editor = button.closest("[data-human-review-checklist-editor]");
  const row = button.closest("[data-human-review-checklist-row]");
  const direction = text(button.dataset.humanReviewChecklistMove);
  if (!editor || !row) {
    return;
  }
  if (direction === "up" && row.previousElementSibling) {
    row.parentElement.insertBefore(row, row.previousElementSibling);
  } else if (direction === "down" && row.nextElementSibling) {
    row.parentElement.insertBefore(row.nextElementSibling, row);
  }
  syncHumanReviewChecklistEditor(editor);
  rememberFormDraft(editor.closest("form"));
  button.focus();
}

function humanReviewChecklistItemId(row, index) {
  const explicitId = text(
    row.querySelector('[name="human_review_checklist_id"]')?.value,
  );
  const label = text(
    row.querySelector('[name="human_review_checklist_label"]')?.value,
  );
  return explicitId || criterionIdFromText(label || `check ${index + 1}`);
}

function humanReviewChecklistFromForm(form) {
  return Array.from(form.querySelectorAll("[data-human-review-checklist-row]")).map(
    (row, index) => ({
      id: humanReviewChecklistItemId(row, index),
      label: text(
        row.querySelector('[name="human_review_checklist_label"]')?.value,
      ),
      description:
        text(
          row.querySelector('[name="human_review_checklist_description"]')?.value,
        ) || null,
      required:
        row.querySelector('[name="human_review_checklist_required"]')?.checked !==
        false,
    }),
  );
}

function humanReviewChecklistDraftFromForm(form) {
  const editor = form.querySelector("[data-human-review-checklist-editor]");
  if (!editor) {
    return null;
  }
  return Array.from(editor.querySelectorAll("[data-human-review-checklist-row]")).map(
    (row) => ({
      id: text(row.querySelector('[name="human_review_checklist_id"]')?.value),
      label: text(
        row.querySelector('[name="human_review_checklist_label"]')?.value,
      ),
      description: text(
        row.querySelector('[name="human_review_checklist_description"]')?.value,
      ),
      required:
        row.querySelector('[name="human_review_checklist_required"]')?.checked !==
        false,
    }),
  );
}

function restoreHumanReviewChecklistDraft(form, checklist) {
  const editor = form.querySelector("[data-human-review-checklist-editor]");
  const rows = editor?.querySelector?.("[data-human-review-checklist-rows]");
  if (!rows || !Array.isArray(checklist)) {
    return;
  }
  rows.innerHTML = checklist
    .map((item) => renderHumanReviewChecklistRow(item))
    .join("");
  decorateFieldTooltips(rows);
  syncHumanReviewChecklistEditor(editor);
}

function validateHumanReviewPolicyEditor(form) {
  const enabled = Boolean(form.elements.human_review_enabled?.checked);
  const groupField = form.elements.human_reviewer_group_ids;
  groupField?.setCustomValidity("");
  const rows = Array.from(
    form.querySelectorAll("[data-human-review-checklist-row]"),
  );
  rows.forEach((row) => {
    row
      .querySelector('[name="human_review_checklist_id"]')
      ?.setCustomValidity("");
    row
      .querySelector('[name="human_review_checklist_label"]')
      ?.setCustomValidity("");
  });
  if (!enabled) {
    return true;
  }

  let valid = true;
  if (!splitList(groupField?.value).length) {
    groupField?.setCustomValidity(
      "Choose at least one Admin reviewer group for human approval.",
    );
    valid = false;
  }

  const usedIds = new Map();
  rows.forEach((row, index) => {
    const labelField = row.querySelector(
      '[name="human_review_checklist_label"]',
    );
    const idField = row.querySelector('[name="human_review_checklist_id"]');
    const label = text(labelField?.value);
    if (!label) {
      labelField?.setCustomValidity("Give this acceptance check a label.");
      valid = false;
    }
    const id = humanReviewChecklistItemId(row, index);
    const earlierIdField = usedIds.get(id);
    if (earlierIdField) {
      const message = `Checklist keys must be unique. "${id}" is used more than once.`;
      earlierIdField.setCustomValidity(message);
      idField?.setCustomValidity(message);
      valid = false;
    } else {
      usedIds.set(id, idField);
    }
  });
  if (!form.checkValidity()) {
    valid = false;
  }
  if (!valid) {
    form.reportValidity();
    setStatus("Fix the highlighted human review requirement before saving.", "error");
  }
  return valid;
}

async function submitReviewDecision(form) {
  if (!guardTeamMutation("record review decisions")) {
    return;
  }
  const data = formData(form);
  const reviewId = text(data.review_request_id || form.dataset.reviewRequestId);
  if (!reviewId) {
    setStatus("Review is required.", "error");
    return;
  }
  const stateValue = text(data.state);
  if (!stateValue) {
    setStatus("Decision is required.", "error");
    return;
  }
  const payload = {
    state: stateValue,
    findings: splitList(data.findings),
    criteria: reviewCriterionResultsFromForm(form),
    finding_resolutions: findingResolutionsFromForm(form),
  };
  await submitMutation(
    form,
    `/review-requests/${encodeURIComponent(reviewId)}/decision`,
    "POST",
    payload,
    { resetOnSuccess: false },
    );
}

function reviewCriterionResultsFromForm(form) {
  return [...form.querySelectorAll("[data-review-criterion-id]")]
    .map((row) => {
      const criterionId = text(row.dataset.reviewCriterionId);
      const stateValue = text(row.querySelector("select")?.value);
      if (!stateValue) return null;
      return {
        criterion_id: criterionId,
        state: stateValue,
        finding: text(row.querySelector('input[type="text"]')?.value),
        evidence_refs: splitList(
          row.querySelector('[name^="criterion_evidence_refs__"]')?.value,
        ),
      };
    })
    .filter(Boolean);
}

function findingResolutionsFromForm(form) {
  return [...form.querySelectorAll('[name^="finding_resolution__"]')]
    .map((input) => {
      const resolution = text(input.value);
      if (!resolution) return null;
      return {
        finding_id: input.name.slice("finding_resolution__".length),
        resolution,
        evidence_refs: [],
      };
    })
    .filter(Boolean);
}

function openTaskCancellationDialog(taskId) {
  if (!guardTeamMutation("cancel tasks")) {
    return;
  }
  const task = state.tasks?.[text(taskId)] || null;
  const availability = taskCancellationAvailability(task);
  if (!task || !availability.allowed) {
    setStatus(availability.explanation, "warning");
    return;
  }
  const form = elements.taskCancellationForm;
  const modal = elements.taskCancellationModal;
  if (!form || !modal) {
    setStatus("The task cancellation dialog is unavailable.", "error");
    return;
  }

  form.reset();
  form.dataset.taskId = task.id;
  if (elements.taskCancellationModalTitle) {
    elements.taskCancellationModalTitle.textContent = `Cancel ${task.title}`;
  }
  if (elements.taskCancellationSubject) {
    elements.taskCancellationSubject.textContent =
      `${task.title} · ${taskStateLabel(task)} · owner ${taskAssignmentLabel(task)}`;
  }
  const successorSelect = form.elements.successor_task_id;
  if (successorSelect instanceof HTMLSelectElement) {
    const successorOptions = values(state.tasks)
      .filter(
        (candidate) =>
          candidate.id !== task.id &&
          text(candidate.project_id) === text(task.project_id),
      )
      .sort(byPriority)
      .map(
        (candidate) =>
          `<option value="${escapeAttr(candidate.id)}">${escapeHtml(candidate.title)} · ${escapeHtml(taskStateLabel(candidate))}</option>`,
      )
      .join("");
    successorSelect.innerHTML = `<option value="">No successor</option>${successorOptions}`;
  }
  modal.hidden = false;
  form.elements.reason_code?.focus();
}

function closeTaskCancellationDialog() {
  const form = elements.taskCancellationForm;
  if (elements.taskCancellationModal) {
    elements.taskCancellationModal.hidden = true;
  }
  if (elements.taskCancellationModalTitle) {
    elements.taskCancellationModalTitle.textContent = "Cancel task";
  }
  if (elements.taskCancellationSubject) {
    elements.taskCancellationSubject.textContent = "";
  }
  if (form) {
    clearFormDraft(form);
    form.reset();
    form.dataset.taskId = "";
  }
}

async function submitTaskCancellation(form) {
  if (!guardTeamMutation("cancel tasks")) {
    return;
  }
  const taskId = text(form?.dataset?.taskId);
  const task = state.tasks?.[taskId] || null;
  const availability = taskCancellationAvailability(task);
  if (!task || !availability.allowed) {
    setStatus(availability.explanation, "warning");
    return;
  }
  const data = formData(form);
  const successorTaskId = text(data.successor_task_id) || null;
  const rebindDependents = Boolean(data.rebind_dependents);
  if (rebindDependents && !successorTaskId) {
    setStatus("Select a successor before replacing active dependencies.", "warning");
    return;
  }
  const expectedDependentTaskIds = values(state.tasks)
    .filter((candidate) => {
      if (terminalTaskStates.has(candidate.state)) return false;
      if ((candidate.dependency_task_ids || []).includes(task.id)) return true;
      return Boolean(
        candidate.integration_boundary
        && (candidate.deliverable_contract?.required_contribution_task_ids || [])
          .includes(task.id),
      );
    })
    .map((candidate) => candidate.id)
    .sort();
  const payload = {
    reason_code: text(data.reason_code),
    reason: text(data.reason),
    successor_task_id: successorTaskId,
    rebind_dependents: rebindDependents,
  };
  if (rebindDependents) {
    payload.expected_dependent_task_ids = expectedDependentTaskIds;
  }
  const saved = await submitMutation(
    form,
    `/tasks/${encodeURIComponent(task.id)}/cancel`,
    "POST",
    payload,
    { resetOnSuccess: false },
  );
  if (saved) {
    closeTaskCancellationDialog();
  }
}

function openEditDialog(type, id) {
  if (!guardTeamMutation("edit Team records")) {
    return;
  }
  const record = recordForEdit(type, id);
  if (!record || !elements.editModal || !elements.editForm || !elements.editFormBody) {
    setStatus("Record is no longer available.", "error");
    return;
  }

  elements.editForm.dataset.editType = type;
  elements.editForm.dataset.editId = id;
  elements.editModalTitle.textContent = editDialogTitle(type, record);
  elements.editFormBody.innerHTML = renderEditFields(type, record);
  fillSelectsIn(elements.editFormBody);
  decorateFieldTooltips(elements.editFormBody);
  if (type === "governance-group") {
    syncGovernanceMemberTeamEditor(
      elements.editFormBody.querySelector("[data-governance-member-teams-editor]"),
    );
    syncGovernanceGoalEditor(
      elements.editFormBody.querySelector("[data-governance-goals-editor]"),
    );
  }
  if (type === "project") {
    syncHumanReviewChecklistEditor(
      elements.editFormBody.querySelector("[data-human-review-checklist-editor]"),
    );
  }
  elements.editModal.hidden = false;
  const firstEditableField = elements.editFormBody.querySelector(
    "input:not([readonly]), select, textarea",
  );
  firstEditableField?.focus();
}

function closeEditDialog() {
  if (!elements.editModal || !elements.editForm || !elements.editFormBody) {
    return;
  }
  elements.editModal.hidden = true;
  elements.editForm.dataset.editType = "";
  elements.editForm.dataset.editId = "";
  elements.editFormBody.innerHTML = "";
}

async function submitEditForm(form) {
  if (!guardTeamMutation("save changes")) {
    return;
  }
  const type = text(form.dataset.editType);
  const id = text(form.dataset.editId);
  if (!type || !id) {
    setStatus("No edit record selected.", "error");
    return;
  }
  const endpoint = editEndpoint(type, id);
  if (type === "governance-group" && !validateGovernanceGroupEditor(form)) {
    return;
  }
  if (type === "project" && !validateHumanReviewPolicyEditor(form)) {
    return;
  }
  const payload = editPayloadForForm(type, form);
  const saved = await submitMutation(form, endpoint, "PATCH", payload, {
    resetOnSuccess: false,
  });
  if (saved) {
    if (type === "task") {
      showTaskPreparationInvalidationStatus(id);
    }
    clearFormDraft(form);
    closeEditDialog();
  } else if (type === "governance-group") {
    syncGovernanceGoalEditor(
      form.querySelector("[data-governance-goals-editor]"),
    );
  }
}

async function transferProjectLead(form) {
  if (!guardTeamMutation("transfer project leads")) {
    return;
  }
  const projectId = text(form?.dataset?.editId);
  const project = state.projects?.[projectId];
  const leadAgentId = text(form?.elements?.lead_agent_id?.value);
  if (!project || !leadAgentId) {
    setStatus("Select a project lead to transfer authority.", "error");
    return;
  }
  if (leadAgentId === text(project.lead_agent_id)) {
    setStatus("That agent is already the project lead.", "warning");
    return;
  }
  const transferred = await submitMutation(
    form,
    `/projects/${encodeURIComponent(projectId)}/lead`,
    "POST",
    { lead_agent_id: leadAgentId },
    { resetOnSuccess: false },
  );
  if (transferred) {
    clearFormDraft(form);
    closeEditDialog();
  }
}

function recordForEdit(type, id) {
  if (type === "team") return state.teams[id] || null;
  if (type === "governance-group") return state.governance_groups[id] || null;
  if (type === "project") return state.projects[id] || null;
  if (type === "role") return state.roles[id] || null;
  if (type === "agent") return state.agents[id] || null;
  if (type === "case") return state.cases[id] || null;
  if (type === "task") return state.tasks[id] || null;
  if (type === "review-profile") return state.review_profiles[id] || null;
  if (type === "outcome-kind") return state.outcome_kind_definitions[id] || null;
  return null;
}

function editEndpoint(type, id) {
  if (type === "team") return `/teams/${encodeURIComponent(id)}`;
  if (type === "governance-group") return `/governance-groups/${encodeURIComponent(id)}`;
  if (type === "project") return `/projects/${encodeURIComponent(id)}`;
  if (type === "role") return `/roles/${encodeURIComponent(id)}`;
  if (type === "agent") return `/agents/${encodeURIComponent(id)}`;
  if (type === "case") return `/cases/${encodeURIComponent(id)}`;
  if (type === "task") return `/tasks/${encodeURIComponent(id)}`;
  if (type === "review-profile") return `/review-profiles/${encodeURIComponent(id)}`;
  if (type === "outcome-kind") return `/outcome-kinds/${encodeURIComponent(id)}`;
  throw new Error(`Unknown edit type ${type}`);
}

function editDialogTitle(type, record) {
  const labels = {
    team: "team",
    "governance-group": "governance group",
    project: "project",
    role: "role",
    agent: "agent",
    case: "case",
    task: "task",
    "review-profile": "review profile",
    "outcome-kind": "outcome kind",
  };
  if (type === "governance-group") {
    return `Edit ${text(record.name) || "governance group"}`;
  }
  return `Edit ${labels[type] || "record"} ${record.id || ""}`.trim();
}

function renderEditFields(type, record) {
  if (type === "team") {
    return `
      ${readonlyField("Team ID", record.id)}
      ${textField("Name", "name", record.name, { required: true })}
      ${selectField("Manager", "manager_agent_id", "agents", record.manager_agent_id || "", { required: true })}
      ${selectField("Operator communication target", "operator_communication_target_id", "communication-targets", record.operator_communication_target_id || "", { placeholder: "No operator target" })}
      ${checkboxField("Escalate manager escalations to governance", "escalate_to_governance", Boolean(record.escalate_to_governance))}
      ${numberField("Estimated max cost USD", "max_cost_usd", record.max_cost_usd ?? "", { min: 0, step: "0.01" })}
      ${numberField("Governance cost warning threshold", "cost_warning_ratio", record.cost_warning_ratio ?? 0.8, { min: 0.01, max: 1, step: "0.01" })}
      ${selectField("Budget renewal", "cost_budget_renewal_mode", "cost-budget-renewal-modes", record.cost_budget_renewal_mode || "manual")}
      ${checkboxField("Archived", "archived", Boolean(record.archived))}
    `;
  }
  if (type === "governance-group") {
    return `
      ${textField("Name", "name", record.name, { required: true })}
      ${selectField("Manager", "manager_agent_id", "governance-manager-agents", record.manager_agent_id || "", { required: true })}
      ${renderGovernanceMemberTeamsEditor(record.member_team_ids || [])}
      ${renderGovernanceGoalsEditor(record.goals || [])}
      ${numberField("Estimated max cost USD", "max_cost_usd", record.max_cost_usd ?? "", { min: 0, step: "0.01" })}
      ${checkboxField("Archived", "archived", Boolean(record.archived))}
      ${renderTechnicalDetails([
        ["Governance group ID", record.id],
        ["Manager agent ID", record.manager_agent_id],
      ], {
        disclosureKey: teamDisclosureKey("edit", "governance-group", record.id, "technical"),
      })}
    `;
  }
  if (type === "project") {
    return `
      ${readonlyField("Project ID", record.id)}
      ${readonlyField("Team", record.team_id)}
      ${textField("Name", "name", record.name, { required: true })}
      ${textField("Directory scope", "directory_path", record.directory_path || "root", { required: true })}
      ${selectField("Status", "status", "project-statuses", record.status || "active", { required: true })}
      ${textareaField("Goals", "goals", goalsToText(record.goals || []), {
        rows: 4,
        placeholder: "id | description | state",
      })}
      <fieldset>
        <legend>Project lead handoff</legend>
        ${selectField("Project lead", "lead_agent_id", "project-lead-agents", record.lead_agent_id || "", { required: true, placeholder: "Select team agent" })}
        <p class="field-help">Transfer authority separately from other project edits. The former lead remains a project member.</p>
        <div class="record-actions">
          <button type="button" data-transfer-project-lead>Transfer project lead</button>
        </div>
      </fieldset>
      <p class="subheading">Git config</p>
      ${textField("Repository source", "repo_source", "", {
        placeholder: "Git URL or local checkout",
      })}
      ${gitImportControls()}
      ${textField("Managed repository ID", "managed_repo_id", record.git?.managed_repo_id || "", {
        placeholder: "mr_...",
      })}
      ${textField("Default base branch for new tasks", "base_branch", record.git?.base_branch || "main")}
      ${textField("Canonical release branch prefix", "canonical_release_branch_prefix", record.git?.canonical_release_branch_prefix || "")}
      ${textField("Agent commit author name", "agent_author_name", record.git?.agent_author_identity?.name || "", { placeholder: "Evander Bot" })}
      ${textField("Agent commit author email", "agent_author_email", record.git?.agent_author_identity?.email || "", { placeholder: "evander@quinth.se" })}
      ${textField("Agent commit committer name", "agent_committer_name", record.git?.agent_committer_identity?.name || "", { placeholder: "Evander Bot" })}
      ${textField("Agent commit committer email", "agent_committer_email", record.git?.agent_committer_identity?.email || "", { placeholder: "evander@quinth.se" })}
      <p class="subheading">Release gates</p>
      ${textareaField("Automated and agent-review policy JSON", "release_policy_json", releasePolicyWithoutHumanJson(record.release_policy), {
        rows: 12,
        placeholder: '{"verification_commands": [...], "review_requirements": [...], "allow_ungated": false}',
      })}
      <p class="field-help">Human review fields are intentionally excluded from the JSON editor and configured only through the bounded controls below.</p>
      ${renderHumanReviewPolicyEditor(record.release_policy?.human_review_requirement)}
    `;
  }
  if (type === "agent") {
    const runtime = state.agent_runtimes?.[record.id] || {};
    const contextPolicy = contextClearingPolicyValue(runtime);
    const contextIdleGraceMinutes = contextClearingIdleGraceMinutesForEdit(
      runtime,
    );
    return `
      ${readonlyField("Agent ID", record.id)}
      ${selectField("Team", "team_id", "teams", record.team_id || "", { placeholder: "No team" })}
      ${selectField("Project", "project_id", "agent-edit-projects", agentEditProjectId(record), { placeholder: "No project" })}
      ${selectField("Role", "role_id", "agent-edit-roles", record.role_id || "", { placeholder: "No role" })}
      ${textField("Session ID", "session_id", record.session_id, { required: true })}
      ${textField("Display name", "display_name", record.display_name || "")}
      ${renderAvatarEditControls(record)}
      ${renderVoiceProfileEditControls(record)}
      ${selectField("Reports to", "reports_to_agent_id", "agent-edit-superiors", record.reports_to_agent_id || "", { placeholder: "No superior" })}
      ${numberField("Max ephemeral agents", "max_concurrent", ephemeralCapacity(record), { min: 0 })}
      ${selectField("Status", "status", "agent-statuses", record.status || "active", { required: true })}
      ${selectField("Context clearing", "context_clearing_policy", "context-clearing-policies", contextPolicy, { required: true })}
      <div data-agent-context-idle-grace${contextPolicy === "idle" ? "" : " hidden"}>
        ${numberField("Idle grace minutes", "context_clearing_idle_grace_minutes", contextIdleGraceMinutes, { min: 0, max: 10080, step: "any", required: true })}
      </div>
    `;
  }
  if (type === "role") {
    return `
      ${readonlyField("Role ID", record.id)}
      ${readonlyField("Team", record.team_id)}
      ${selectField("Project", "project_id", "projects", record.project_id || "", { placeholder: "Team-wide" })}
      ${textField("Name", "name", record.name, { required: true })}
      ${textareaField("Description", "description", record.description || "", { rows: 3 })}
      ${multiSelectField("Permissions", "permissions", "role-permissions", record.permissions || [], { size: 3 })}
      ${numberField("Max ephemeral agents", "max_concurrent", record.ephemeral_policy?.max_concurrent ?? 0, { min: 0 })}
      ${checkboxField("Archived", "archived", Boolean(record.archived))}
    `;
  }
  if (type === "case") {
    return `
      ${readonlyField("Case ID", record.id)}
      ${readonlyField("Team", record.team_id)}
      ${selectField("Project", "project_id", "projects", record.project_id || "", { placeholder: "Team-wide" })}
      ${selectField("Owner", "owner_agent_id", "agents", record.owner_agent_id || "", { placeholder: "No owner" })}
      ${selectField("Status", "status", "case-statuses", record.status || "open", { required: true })}
      ${selectField("Priority", "priority", "inbox-priorities", record.priority || "normal", { required: true })}
      ${textField("Title", "title", record.title, { required: true })}
      ${textareaField("Description", "description", record.description || "", { rows: 3 })}
      ${textField("Source", "source", record.source || "")}
      ${textField("Source ref", "source_ref", record.source_ref || "")}
      ${textareaField("Tags", "tags", (record.tags || []).join("\n"), { rows: 2 })}
      ${textareaField("Linked tasks", "task_ids", (record.task_ids || []).join("\n"), { rows: 2 })}
    `;
  }
  if (type === "task") {
    const gitBaseLocked = taskGitBaseBranchLocked(record);
    return `
      ${readonlyField("Task ID", record.id)}
      ${readonlyField("Project", record.project_id)}
      ${readonlyField("Assigned to", taskAssignmentLabel(record))}
      ${taskEditStateField(record)}
      ${selectField("Case", "case_id", "cases", record.case_id || "", { placeholder: "No case" })}
      ${numberField("Priority", "priority", record.priority ?? 0, { min: 0, required: true })}
      ${textField("Title", "title", record.title, { required: true })}
      ${textareaField("Description", "description", record.description, { rows: 3, required: true })}
      ${textField("Commit author name", "task_author_name", record.authored_by?.name || "", { placeholder: "Project author" })}
      ${textField("Commit author email", "task_author_email", record.authored_by?.email || "", { placeholder: "Project author" })}
      ${gitBaseLocked
        ? readonlyField("Git base branch (locked)", record.git_base_branch || "Not recorded (legacy task)")
        : textField("Git base branch", "git_base_branch", record.git_base_branch || "", { placeholder: "Inherit project default" })}
      ${selectField("Parent task", "parent_task_id", "tasks", record.parent_task_id || "", { placeholder: "No parent" })}
      ${textareaField("Dependency tasks", "dependency_task_ids", (record.dependency_task_ids || []).join("\n"), { rows: 2 })}
      ${checkboxField("Integration boundary", "integration_boundary", Boolean(record.integration_boundary))}
      ${checkboxField("Require Git provenance", "require_git_provenance", Boolean(record.deliverable_contract?.require_git_provenance))}
      ${textareaField("Required contribution task IDs", "required_contribution_task_ids", (record.deliverable_contract?.required_contribution_task_ids || []).join("\n"), { rows: 2 })}
    `;
  }
  if (type === "review-profile") {
    return `
      ${readonlyField("Profile ID", record.id)}
      ${readonlyField("Team", record.team_id)}
      ${selectField("Project", "project_id", "projects", record.project_id || "", { placeholder: "Team-wide" })}
      ${textField("Name", "name", record.name, { required: true })}
      ${textareaField("Description", "description", record.description || "", { rows: 3 })}
      ${multiSelectField("Applicable roles", "applicable_role_ids", "roles", record.applicable_role_ids || [], { size: 4 })}
      ${multiSelectField("Required outcome kinds", "required_outcome_kinds", "review-profile-edit-outcome-kinds", record.required_outcome_kinds || [], { size: 4 })}
      ${renderReviewProfileCriteriaEditor(record.criteria || [])}
      ${checkboxField("Archived", "archived", Boolean(record.archived))}
    `;
  }
  if (type === "outcome-kind") {
    return `
      ${readonlyField("Definition ID", record.id)}
      ${readonlyField("Kind", record.kind)}
      ${readonlyField("Team", record.team_id)}
      ${readonlyField("Project", record.project_id || "Team-wide")}
      ${textField("Name", "name", record.name, { required: true })}
      ${textareaField("Description", "description", record.description || "", { rows: 3 })}
      ${textareaField("Reference requirements", "reference_requirements", record.reference_requirements || "", { rows: 3 })}
      ${textareaField("Evidence requirements", "evidence_requirements", record.evidence_requirements || "", { rows: 3 })}
      ${checkboxField("Archived", "archived", Boolean(record.archived))}
    `;
  }
  return "";
}

function taskEditStateField(task) {
  const stateValue = text(task?.state) || "pending";
  if (task.owner_agent_id && directlyEditableTaskStates.includes(stateValue)) {
    return selectField(
      "State",
      "state",
      "task-edit-states",
      stateValue,
      { required: true },
    );
  }
  return `
    ${readonlyField("State", taskStateLabel(task))}
    <input type="hidden" name="state" value="${escapeAttr(stateValue)}" />
  `;
}

function taskGitBaseBranchLocked(task) {
  const deliverableIds = new Set([
    ...(task?.deliverable_ids || []).map(text).filter(Boolean),
    ...Object.values(state.deliverables || {})
      .filter((deliverable) => text(deliverable.task_id) === text(task?.id))
      .map((deliverable) => text(deliverable.id))
      .filter(Boolean),
  ]);
  if (deliverableIds.size) return true;
  if (Object.values(state.git_work_contexts || {}).some((context) => text(context.task_id) === text(task?.id))) {
    return true;
  }
  return Object.values(state.git_promotions || {}).some((promotion) => (
    text(promotion.task_id) === text(task?.id)
    || deliverableIds.has(text(promotion.deliverable_id))
  ));
}

function renderAvatarEditControls(record) {
  const hasAvatar = Boolean(record?.avatar?.asset_id);
  const styleOptions = avatarStylePresets
    .map(([value, label]) => `<option value="${escapeAttr(value)}"${value === "professional" ? " selected" : ""}>${escapeHtml(label)}</option>`)
    .join("");
  return `
    <fieldset class="avatar-editor">
      <legend>Avatar</legend>
      <div class="avatar-editor-current">
        ${renderAgentAvatar(record, "agent-avatar-large")}
        <div>
          <div class="meta-line">${hasAvatar ? `Current avatar - ${escapeHtml(record.avatar.source || "custom")}` : "No avatar set"}</div>
          <div class="record-actions avatar-editor-actions">
            <input type="file" name="avatar_file" accept="image/png,image/jpeg,image/webp" />
            <button class="record-action" type="button" data-avatar-upload-agent-id="${escapeAttr(record.id)}">Upload</button>
            ${hasAvatar ? `<button class="record-action" type="button" data-avatar-remove-agent-id="${escapeAttr(record.id)}">Remove</button>` : ""}
          </div>
        </div>
      </div>
      <div class="avatar-generation-grid">
        <label>Style preset
          <select name="avatar_style_preset">${styleOptions}</select>
        </label>
        <label>Generation notes
          <textarea name="avatar_prompt_note" rows="2" placeholder="Optional visual hints"></textarea>
        </label>
      </div>
      <div class="record-actions avatar-editor-actions">
        <button class="record-action" type="button" data-avatar-generate-agent-id="${escapeAttr(record.id)}">Generate preview</button>
        <button class="record-action" type="button" data-avatar-accept-agent-id="${escapeAttr(record.id)}" disabled>Accept preview</button>
      </div>
      <div class="avatar-preview-slot" data-avatar-preview-slot></div>
    </fieldset>
  `;
}

function renderVoiceProfileEditControls(record) {
  const profile = record?.voice_profile || {};
  return `
    <fieldset class="voice-profile-editor">
      <legend>Voice</legend>
      ${selectField("Base voice", "voice", "realtime-voices", profile.voice || "", { placeholder: "Default (marin)" })}
      ${textareaField("Speaking style", "speaking_style", profile.speaking_style || "", {
        rows: 3,
        placeholder: "Calm, concise, lightly upbeat",
      })}
    </fieldset>
  `;
}

function editPayloadForForm(type, form) {
  const data = formData(form);
  if (type === "team") {
    return {
      name: text(data.name),
      manager_agent_id: text(data.manager_agent_id),
      operator_communication_target_id: text(data.operator_communication_target_id) || null,
      escalate_to_governance: Boolean(form.elements.escalate_to_governance?.checked),
      max_cost_usd: numberOrNull(data.max_cost_usd),
      cost_warning_ratio: numberOrNull(data.cost_warning_ratio),
      cost_budget_renewal_mode: text(data.cost_budget_renewal_mode) || "manual",
      archived: Boolean(form.elements.archived?.checked),
    };
  }
  if (type === "governance-group") {
    return {
      name: text(data.name),
      manager_agent_id: text(data.manager_agent_id),
      member_team_ids: governanceMemberTeamIdsFromForm(form),
      goals: governanceGoalsFromForm(form),
      max_cost_usd: numberOrNull(data.max_cost_usd),
      archived: Boolean(form.elements.archived?.checked),
    };
  }
  if (type === "project") {
    return {
      name: text(data.name),
      directory_path: text(data.directory_path) || "root",
      status: text(data.status),
      goals: parseGoals(data.goals),
      git: gitConfigForForm(data),
      release_policy: releasePolicyForForm(data, form),
    };
  }
  if (type === "agent") {
    const selectedProjectId = text(data.project_id);
    const currentRecord = recordForEdit("agent", text(form.dataset.editId));
    const currentProjectId = currentRecord ? agentEditProjectId(currentRecord) : "";
    const requestedTeamId = text(data.team_id);
    const normalizedTeamId = requestedTeamId === noTeamRecordId ? null : requestedTeamId || null;
    const contextClearingPolicy = text(data.context_clearing_policy) || "off";
    const payload = {
      team_id: normalizedTeamId,
      role_id: text(data.role_id) || null,
      session_id: text(data.session_id),
      reports_to_agent_id: text(data.reports_to_agent_id) || null,
      display_name: text(data.display_name) || null,
      voice_profile: {
        voice: text(data.voice) || null,
        speaking_style: text(data.speaking_style) || null,
      },
      ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
      status: text(data.status),
      context_clearing_policy: contextClearingPolicy,
    };
    if (contextClearingPolicy === "idle") {
      payload.context_clearing_idle_grace_seconds = Math.round(
        numberOrZero(data.context_clearing_idle_grace_minutes) * 60,
      );
    }
    if (selectedProjectId !== currentProjectId) {
      payload.project_id = selectedProjectId || null;
    }
    return payload;
  }
  if (type === "role") {
    return {
      project_id: text(data.project_id) || null,
      name: text(data.name),
      description: text(data.description),
      permissions: splitList(data.permissions),
      ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
      archived: Boolean(form.elements.archived?.checked),
    };
  }
  if (type === "case") {
    return {
      project_id: text(data.project_id) || null,
      owner_agent_id: text(data.owner_agent_id) || null,
      status: text(data.status),
      priority: text(data.priority) || "normal",
      title: text(data.title),
      description: text(data.description),
      source: text(data.source) || null,
      source_ref: text(data.source_ref) || null,
      tags: splitList(data.tags),
      task_ids: splitList(data.task_ids),
    };
  }
  if (type === "task") {
    const integrationBoundary = Boolean(
      form.elements.integration_boundary?.checked,
    );
    const requireGitProvenance = Boolean(
      form.elements.require_git_provenance?.checked,
    );
    const requiredContributionTaskIds = splitList(
      data.required_contribution_task_ids,
    );
    const deliverableContract = (
      integrationBoundary
      || requireGitProvenance
      || requiredContributionTaskIds.length > 0
    )
      ? {
          require_git_provenance: requireGitProvenance,
          required_contribution_task_ids: requiredContributionTaskIds,
        }
      : null;
    const payload = {
      state: text(data.state),
      case_id: text(data.case_id) || null,
      priority: numberOrZero(data.priority),
      title: text(data.title),
      description: text(data.description),
      parent_task_id: text(data.parent_task_id) || null,
      dependency_task_ids: splitList(data.dependency_task_ids),
    };
    const currentTask = state.tasks?.[text(form.dataset.editId)];
    const authoredBy = taskAuthoredByForForm(data);
    if (JSON.stringify(authoredBy) !== JSON.stringify(currentTask?.authored_by || null)) {
      payload.authored_by = authoredBy;
    }
    if (form.elements.git_base_branch) {
      const nextGitBaseBranch = text(data.git_base_branch) || null;
      if (!currentTask || nextGitBaseBranch !== (currentTask.git_base_branch || null)) {
        payload.git_base_branch = nextGitBaseBranch;
      }
    }
    const currentContract = currentTask?.deliverable_contract || null;
    if (
      !currentTask
      || Boolean(currentTask.integration_boundary) !== integrationBoundary
      || JSON.stringify(currentContract) !== JSON.stringify(deliverableContract)
    ) {
      payload.integration_boundary = integrationBoundary;
      payload.deliverable_contract = deliverableContract;
    }
    return payload;
  }
  if (type === "review-profile") {
    return {
      project_id: text(data.project_id) || null,
      name: text(data.name),
      description: text(data.description),
      applicable_role_ids: splitList(data.applicable_role_ids),
      required_outcome_kinds: splitList(data.required_outcome_kinds),
      criteria: reviewProfileCriteriaFromForm(form),
      archived: Boolean(form.elements.archived?.checked),
    };
  }
  if (type === "outcome-kind") {
    return {
      name: text(data.name),
      description: text(data.description),
      reference_requirements: text(data.reference_requirements),
      evidence_requirements: text(data.evidence_requirements),
      archived: Boolean(form.elements.archived?.checked),
    };
  }
  return {};
}

function readonlyField(label, value) {
  return `<label>${escapeHtml(label)}<input value="${escapeAttr(value)}" readonly /></label>`;
}

function textField(label, name, value, { required = false, placeholder = "" } = {}) {
  return `<label>${escapeHtml(label)}<input name="${escapeAttr(name)}" value="${escapeAttr(value)}"${required ? " required" : ""}${placeholder ? ` placeholder="${escapeAttr(placeholder)}"` : ""} /></label>`;
}

function numberField(label, name, value, { min = null, max = null, step = null, required = false } = {}) {
  return `<label>${escapeHtml(label)}<input name="${escapeAttr(name)}" type="number" value="${escapeAttr(value)}"${min !== null ? ` min="${escapeAttr(min)}"` : ""}${max !== null ? ` max="${escapeAttr(max)}"` : ""}${step !== null ? ` step="${escapeAttr(step)}"` : ""}${required ? " required" : ""} /></label>`;
}

function textareaField(label, name, value, { rows = 3, required = false, placeholder = "" } = {}) {
  return `<label>${escapeHtml(label)}<textarea name="${escapeAttr(name)}" rows="${escapeAttr(rows)}"${required ? " required" : ""}${placeholder ? ` placeholder="${escapeAttr(placeholder)}"` : ""}>${escapeHtml(value)}</textarea></label>`;
}

function selectField(label, name, optionsKey, value, { required = false, placeholder = "Default" } = {}) {
  return `<label>${escapeHtml(label)}<select name="${escapeAttr(name)}" data-options="${escapeAttr(optionsKey)}" data-current-value="${escapeAttr(value)}" data-placeholder="${escapeAttr(placeholder)}"${required ? " required" : ""}></select></label>`;
}

function multiSelectField(label, name, optionsKey, values, { size = 4 } = {}) {
  return `<label>${escapeHtml(label)}<select name="${escapeAttr(name)}" multiple size="${escapeAttr(size)}" data-options="${escapeAttr(optionsKey)}" data-current-value="${escapeAttr((values || []).join("\n"))}"></select></label>`;
}

function checkboxField(label, name, checked) {
  return `<label class="checkbox-label"><input name="${escapeAttr(name)}" type="checkbox"${checked ? " checked" : ""} /> ${escapeHtml(label)}</label>`;
}

function gitImportControls() {
  return `
    <div class="git-import-actions">
      <button type="button" data-git-import-button>Import Git repo</button>
      <span class="meta-line" data-git-import-status></span>
    </div>
  `;
}

function syncTaskUpdateForm(form, { keepEditedValues = false } = {}) {
  const taskId = form.elements.task_id?.value;
  const task = taskId ? state.tasks[taskId] : null;
  if (!task) {
    return;
  }
  if (!keepEditedValues || !form.elements.state.value) {
    form.elements.state.value = task.state || "";
  }
  if (!keepEditedValues || !form.elements.case_id.value) {
    form.elements.case_id.value = task.case_id || "";
  }
  if (!keepEditedValues || !form.elements.priority.value) {
    form.elements.priority.value = String(task.priority ?? "");
  }
  if (!keepEditedValues || !form.elements.title.value) {
    form.elements.title.value = task.title || "";
  }
  if (!keepEditedValues || !form.elements.description.value) {
    form.elements.description.value = task.description || "";
  }
  if (!keepEditedValues || !form.elements.parent_task_id.value) {
    form.elements.parent_task_id.value = task.parent_task_id || "";
  }
  if (!keepEditedValues || !form.elements.dependency_task_ids.value) {
    form.elements.dependency_task_ids.value = (task.dependency_task_ids || []).join("\n");
  }
  if (
    form.elements.integration_boundary
    && (!keepEditedValues || !form.elements.integration_boundary.checked)
  ) {
    form.elements.integration_boundary.checked = Boolean(
      task.integration_boundary,
    );
  }
  if (
    form.elements.require_git_provenance
    && (!keepEditedValues || !form.elements.require_git_provenance.checked)
  ) {
    form.elements.require_git_provenance.checked = Boolean(
      task.deliverable_contract?.require_git_provenance,
    );
  }
  if (
    form.elements.required_contribution_task_ids
    && (
      !keepEditedValues
      || !form.elements.required_contribution_task_ids.value
    )
  ) {
    form.elements.required_contribution_task_ids.value = (
      task.deliverable_contract?.required_contribution_task_ids || []
    ).join("\n");
  }
}

function formData(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  form.querySelectorAll("select[multiple][name]").forEach((field) => {
    values[field.name] = Array.from(field.selectedOptions)
      .map((option) => option.value)
      .filter(Boolean);
  });
  return values;
}

function formIdentifier(form) {
  return form.getAttribute("id") || "";
}

function formSemanticIdentifier(form) {
  return form?.dataset?.formId || formIdentifier(form);
}

function formDraftKey(form) {
  if (!form || !shouldTrackFormDraft(form)) {
    return "";
  }
  const formId = formIdentifier(form);
  const semanticId = formSemanticIdentifier(form);
  const parts = [formId || semanticId || "form"];
  if (semanticId && semanticId !== formId) {
    parts.push(`semantic:${semanticId}`);
  }
  const draftScope = text(form.dataset.formDraftScope);
  if (draftScope) {
    parts.push(`scope:${draftScope}`);
  }
  const editType = text(form.dataset.editType);
  const editId = text(form.dataset.editId);
  if (editType || editId) {
    parts.push(`edit:${editType || "record"}:${editId || "new"}`);
  }
  const groupId = text(form.dataset.teamRecordGroupId);
  const groupKind = text(form.dataset.teamRecordKind);
  if (groupId || groupKind) {
    parts.push(`group:${groupKind || "record"}:${groupId || "all"}`);
  }
  const reviewRequestId = text(form.dataset.reviewRequestId);
  if (reviewRequestId) {
    parts.push(`review:${reviewRequestId}`);
  }
  return parts.join("|");
}

function shouldTrackFormDraft(form) {
  return Boolean(
    form
    && (formIdentifier(form) || formSemanticIdentifier(form))
    && !form.matches("[data-chat-message-form]"),
  );
}

function rememberFormDraftFromEvent(event) {
  const form = event.target?.closest?.("form");
  if (form) {
    rememberFormDraft(form);
  }
}

function rememberFormDraft(form) {
  const key = formDraftKey(form);
  if (!key) {
    return null;
  }
  const values = formValuesForDraft(form);
  formDraftRegistry.set(key, values);
  return [key, values];
}

function clearFormDraft(form) {
  const key = formDraftKey(form);
  if (key) {
    formDraftRegistry.delete(key);
    if (pendingFormFocusDraft?.formKey === key) {
      pendingFormFocusDraft = null;
    }
  }
}

function formValuesForDraft(form) {
  const values = formData(form);
  form.querySelectorAll('input[type="file"][name]').forEach((field) => {
    delete values[field.name];
  });
  form.querySelectorAll('input[type="checkbox"][name]').forEach((field) => {
    values[field.name] = field.checked ? "true" : "false";
  });
  const criteriaDraft = reviewProfileCriteriaDraftFromForm(form);
  if (criteriaDraft) {
    values.__review_profile_criteria = criteriaDraft;
  }
  if (form.querySelector("[data-governance-goals-editor]")) {
    values.__governance_goals = governanceGoalsDraftFromForm(form);
  }
  const humanReviewChecklistDraft = humanReviewChecklistDraftFromForm(form);
  if (humanReviewChecklistDraft) {
    values.__human_review_checklist = humanReviewChecklistDraft;
  }
  if (form.matches("[data-human-review-decision-form]")) {
    values.__human_review_findings = humanReviewFindingsDraftFromForm(form) || [];
    values.__human_review_checklist_results = Object.fromEntries(
      Array.from(form.querySelectorAll("[data-human-checklist-id]")).map((field) => [
        text(field.dataset.humanChecklistId),
        Boolean(field.checked),
      ]),
    );
    values.__human_review_confirmed_finding_ids = Array.from(
      form.querySelectorAll("[data-confirm-finding-id]:checked"),
    )
      .map((field) => text(field.dataset.confirmFindingId))
      .filter(Boolean);
  }
  return values;
}

function findFormByDraftKey(key) {
  return Array.from(document.querySelectorAll("form")).find((form) => formDraftKey(form) === key) || null;
}

function captureFormDrafts() {
  const drafts = Object.fromEntries(formDraftRegistry.entries());
  document.querySelectorAll("form").forEach((form) => {
    if (form.dataset.formBusy === "true") {
      return;
    }
    const key = formDraftKey(form);
    if (!key || !formDraftRegistry.has(key)) {
      return;
    }
    const remembered = rememberFormDraft(form);
    if (remembered) {
      drafts[remembered[0]] = remembered[1];
    }
  });
  const focusDraft = captureFormFocusDraft() || pendingFormFocusDraft;
  if (focusDraft) {
    drafts[formDraftFocusKey] = focusDraft;
  }
  return drafts;
}

function restoreFormDrafts(drafts) {
  const focusDraft = drafts?.[formDraftFocusKey] || null;
  Object.entries(drafts || {}).forEach(([formId, values]) => {
    if (formId === formDraftFocusKey) {
      return;
    }
    const form = findFormByDraftKey(formId) || document.getElementById(formId);
    if (!form) {
      return;
    }
    restoreReviewProfileCriteriaDraft(form, values.__review_profile_criteria);
    restoreGovernanceGoalsDraft(form, values.__governance_goals);
    restoreHumanReviewChecklistDraft(form, values.__human_review_checklist);
    if (form.matches("[data-human-review-decision-form]")) {
      restoreHumanReviewFindingsDraft(form, values.__human_review_findings);
      const checklistResults = values.__human_review_checklist_results || {};
      form.querySelectorAll("[data-human-checklist-id]").forEach((field) => {
        field.checked = Boolean(checklistResults[text(field.dataset.humanChecklistId)]);
      });
      const confirmedFindingIds = new Set(
        values.__human_review_confirmed_finding_ids || [],
      );
      form.querySelectorAll("[data-confirm-finding-id]").forEach((field) => {
        field.checked = confirmedFindingIds.has(text(field.dataset.confirmFindingId));
      });
    }
    if (formSemanticIdentifier(form) === "setup-template-form") {
      const templateField = form.elements.template_id;
      if (templateField && values.template_id) {
        templateField.value = text(values.template_id);
      }
      renderTemplateSetupFields({ preserveValues: false });
    }
    Object.entries(values).forEach(([name, value]) => {
      if (
        name === "__review_profile_criteria" ||
        name === "__governance_goals" ||
        name === "__human_review_checklist" ||
        name === "__human_review_findings" ||
        name === "__human_review_checklist_results" ||
        name === "__human_review_confirmed_finding_ids" ||
        name.startsWith("criterion_") ||
        name.startsWith("governance_goal_") ||
        name.startsWith("human_review_checklist_") ||
        name.startsWith("human_finding_")
      ) {
        return;
      }
      const field = form.elements[name];
      if (!field) {
        return;
      }
      if (field instanceof HTMLInputElement && field.type === "file") {
        return;
      }
      if (field instanceof HTMLInputElement && field.type === "checkbox") {
        field.checked = value === true || value === "true" || value === "on";
        return;
      }
      if (field instanceof HTMLSelectElement && field.multiple) {
        const selected = new Set(Array.isArray(value) ? value.map(text) : splitList(value));
        const available = new Set(Array.from(field.options).map((option) => option.value));
        const missing = Array.from(selected).filter((item) => item && !available.has(item));
        Array.from(field.options).forEach((option) => {
          option.selected = selected.has(option.value);
        });
        markUnavailableDraftSelections(field, missing);
        return;
      }
      if (field instanceof HTMLSelectElement) {
        const normalizedValue = text(value);
        const available = new Set(Array.from(field.options).map((option) => option.value));
        field.value = normalizedValue;
        markUnavailableDraftSelections(
          field,
          normalizedValue && !available.has(normalizedValue) ? [normalizedValue] : [],
        );
        return;
      }
      field.value = value;
    });
    const leadSelect = form.elements.lead_agent_id;
    if (leadSelect?.matches?.('select[data-options="project-lead-agents"]')) {
      leadSelect.dataset.currentValue = text(values.lead_agent_id);
      refreshProjectLeadSelect(form);
    }
    syncGovernanceMemberTeamEditor(
      form.querySelector("[data-governance-member-teams-editor]"),
    );
    syncGovernanceGoalEditor(
      form.querySelector("[data-governance-goals-editor]"),
    );
    syncHumanReviewChecklistEditor(
      form.querySelector("[data-human-review-checklist-editor]"),
    );
    syncHumanReviewFindingEditor(
      form.querySelector("[data-human-review-findings-editor]"),
    );
    rememberFormDraft(form);
  });
  const updateTaskForm = document.querySelector("#update-task-form");
  if (updateTaskForm) {
    syncTaskUpdateForm(updateTaskForm, { keepEditedValues: true });
  }
  renderTemplatePreview();
  restoreFormFocusDraft(focusDraft);
}

function captureFormFocusDraft() {
  const field = document.activeElement;
  if (
    !(
      field instanceof HTMLInputElement
      || field instanceof HTMLTextAreaElement
      || field instanceof HTMLSelectElement
    )
  ) {
    return null;
  }
  const form = field.form;
  const formKey = formDraftKey(form);
  if (!formKey) {
    return null;
  }
  const identity = {
    id: text(field.id),
    name: text(field.name),
    nameIndex: -1,
    humanChecklistId: text(field.dataset.humanChecklistId),
    confirmFindingId: text(field.dataset.confirmFindingId),
  };
  if (identity.name) {
    identity.nameIndex = Array.from(form.elements)
      .filter((candidate) => text(candidate.name) === identity.name)
      .indexOf(field);
  }
  if (
    ![
      identity.id,
      identity.name,
      identity.humanChecklistId,
      identity.confirmFindingId,
    ].some(Boolean)
  ) {
    return null;
  }
  let selectionStart = null;
  let selectionEnd = null;
  let selectionDirection = null;
  if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) {
    try {
      selectionStart = field.selectionStart;
      selectionEnd = field.selectionEnd;
      selectionDirection = field.selectionDirection;
    } catch {
      // Some input types do not expose text selection.
    }
  }
  return {
    formKey,
    identity,
    selectionStart,
    selectionEnd,
    selectionDirection,
  };
}

function restoreFormFocusDraft(focusDraft) {
  if (!focusDraft?.formKey || !focusDraft.identity) {
    return;
  }
  pendingFormFocusDraft = focusDraft;
  const form = findFormByDraftKey(focusDraft.formKey);
  if (!form) {
    return;
  }
  const identity = focusDraft.identity;
  const fields = Array.from(form.elements).filter(
    (candidate) =>
      candidate instanceof HTMLInputElement
      || candidate instanceof HTMLTextAreaElement
      || candidate instanceof HTMLSelectElement,
  );
  let field = identity.id
    ? fields.find((candidate) => text(candidate.id) === identity.id)
    : null;
  if (!field && identity.humanChecklistId) {
    field = fields.find(
      (candidate) =>
        text(candidate.dataset.humanChecklistId) === identity.humanChecklistId,
    );
  }
  if (!field && identity.confirmFindingId) {
    field = fields.find(
      (candidate) =>
        text(candidate.dataset.confirmFindingId) === identity.confirmFindingId,
    );
  }
  if (!field && identity.name) {
    const namedFields = fields.filter(
      (candidate) => text(candidate.name) === identity.name,
    );
    field = namedFields[Math.max(0, Number(identity.nameIndex) || 0)] || null;
  }
  if (!field || field.disabled) {
    return;
  }
  pendingFormFocusDraft = null;
  field.focus({ preventScroll: true });
  if (
    (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)
    && typeof focusDraft.selectionStart === "number"
    && typeof focusDraft.selectionEnd === "number"
  ) {
    const start = Math.min(Math.max(focusDraft.selectionStart, 0), field.value.length);
    const end = Math.min(
      Math.max(focusDraft.selectionEnd, start),
      field.value.length,
    );
    try {
      field.setSelectionRange(
        start,
        end,
        focusDraft.selectionDirection || undefined,
      );
    } catch {
      // Ignore selection restoration for unsupported input types.
    }
  }
}

function markUnavailableDraftSelections(field, missingValues) {
  const missing = missingValues.filter(Boolean);
  if (!missing.length) {
    delete field.dataset.unavailableDraftValues;
    field.removeAttribute("title");
    return;
  }
  field.dataset.unavailableDraftValues = missing.join(",");
  field.title = `Draft kept unavailable selection: ${missing.join(", ")}`;
}

function setFormBusy(form, busy) {
  if (busy) {
    form.dataset.formBusy = "true";
  } else {
    delete form.dataset.formBusy;
  }
  form.querySelectorAll("button, input, select, textarea").forEach((field) => {
    field.disabled = busy;
  });
  if (!busy) {
    applyLockedMutationControls(form);
  }
}

function setStatus(message, level, action = null) {
  const normalizedMessage = text(message);
  elements.statusMessage.hidden = !normalizedMessage;
  elements.statusMessage.className = `status-message ${level || ""}`.trim();
  elements.statusMessage.textContent = normalizedMessage;
  if (normalizedMessage && action?.label && typeof action.onClick === "function") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "status-action";
    button.textContent = action.label;
    button.addEventListener("click", action.onClick);
    if (teamMutationLocked()) {
      lockMutationControl(button, "retry saves");
    }
    elements.statusMessage.append(" ");
    elements.statusMessage.appendChild(button);
  }
}

function setTimelineStatus(message, level) {
  document.querySelectorAll("[data-timeline-status]").forEach((status) => {
    status.textContent = targetIsActive(status) ? message : "";
    status.className = `timeline-status ${level || ""}`.trim();
  });
}

function formatTimelineTimestamp(timestamp) {
  const value = new Date(timestamp);
  if (Number.isNaN(value.getTime())) {
    return String(timestamp);
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(value);
}

async function responseText(response) {
  const textValue = await response.text();
  if (!textValue) {
    return `${response.status} ${response.statusText}`;
  }
  try {
    const payload = JSON.parse(textValue);
    return payload.error || textValue;
  } catch {
    return textValue;
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

function orderedTasksForAgent(agentId) {
  return values(state.tasks)
    .filter((task) => task.owner_agent_id === agentId)
    .sort(byPriority);
}

function buildTimelineSelectOptions() {
  const teamLabels = new Map();
  const projectLabels = new Map();
  const agentLabels = new Map();
  const typeLabels = new Map();
  const statusLabels = new Map();
  const filters = readTimelineFilters();

  values(state.teams)
    .sort(comparePeopleTeams)
    .forEach((team) => {
      teamLabels.set(team.id, team.name || team.id);
    });
  values(state.projects)
    .sort(compareTeamProjects)
    .forEach((project) => {
      projectLabels.set(project.id, project.name || project.id);
    });
  values(state.agents)
    .sort(comparePeopleAgents)
    .forEach((agent) => {
      agentLabels.set(
        agent.id,
        `${agentLabel(agent)} · ${agentRoleLabel(agent)}`,
      );
    });

  (Array.isArray(timelineState.facets?.types) ? timelineState.facets.types : [])
    .forEach((type) => {
      const normalizedType = text(type);
      if (normalizedType) {
        typeLabels.set(normalizedType, humanizeToken(normalizedType));
      }
    });
  (Array.isArray(timelineState.facets?.statuses) ? timelineState.facets.statuses : [])
    .forEach((status) => {
      const normalizedStatus = text(status);
      if (normalizedStatus) {
        statusLabels.set(normalizedStatus, humanizeToken(normalizedStatus));
      }
    });

  timelineState.entries.forEach((entry) => {
    if (entry.team_id && !teamLabels.has(entry.team_id)) {
      teamLabels.set(entry.team_id, entry.team_id);
    }
    if (entry.project_id && !projectLabels.has(entry.project_id)) {
      projectLabels.set(entry.project_id, entry.project_id);
    }
    if (entry.actor_agent_id && !agentLabels.has(entry.actor_agent_id)) {
      agentLabels.set(entry.actor_agent_id, entry.actor_agent_id);
    }
    (entry.related_agent_ids || []).forEach((agentId) => {
      if (!agentLabels.has(agentId)) {
        agentLabels.set(agentId, agentId);
      }
    });
    if (entry.type && !typeLabels.has(entry.type)) {
      typeLabels.set(entry.type, humanizeToken(entry.type));
    }
    if (entry.status && !statusLabels.has(entry.status)) {
      statusLabels.set(entry.status, humanizeToken(entry.status));
    }
  });

  if (filters.team_id && !teamLabels.has(filters.team_id)) {
    teamLabels.set(filters.team_id, filters.team_id);
  }
  if (filters.project_id && !projectLabels.has(filters.project_id)) {
    projectLabels.set(filters.project_id, filters.project_id);
  }
  if (filters.agent_id && !agentLabels.has(filters.agent_id)) {
    agentLabels.set(filters.agent_id, filters.agent_id);
  }
  if (filters.type && !typeLabels.has(filters.type)) {
    typeLabels.set(filters.type, humanizeToken(filters.type));
  }
  if (filters.status && !statusLabels.has(filters.status)) {
    statusLabels.set(filters.status, humanizeToken(filters.status));
  }

  return {
    teams: optionsFromMap(teamLabels),
    projects: optionsFromMap(projectLabels),
    agents: optionsFromMap(agentLabels),
    types: optionsFromMap(typeLabels, timelineTypeOrder),
    statuses: optionsFromMap(statusLabels),
  };
}

function optionsFromMap(map, preferredOrder = []) {
  const entries = [...map.entries()];
  const orderMap = new Map(preferredOrder.map((value, index) => [value, index]));
  entries.sort((left, right) => {
    const leftOrder = orderMap.has(left[0]) ? orderMap.get(left[0]) : Number.POSITIVE_INFINITY;
    const rightOrder = orderMap.has(right[0]) ? orderMap.get(right[0]) : Number.POSITIVE_INFINITY;
    if (leftOrder !== rightOrder) {
      return leftOrder - rightOrder;
    }
    return left[1].localeCompare(right[1]) || String(left[0]).localeCompare(String(right[0]));
  });
  return entries;
}

function taskStateLabel(task) {
  if (!task || task.state !== "waiting_for_review") {
    return humanizeToken(task?.state);
  }
  if (task.waiting_reason === "superior_acceptance") {
    return "Waiting for acceptance";
  }
  return "Waiting for review";
}

function humanizeToken(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b([a-z])/g, (match) => match.toUpperCase());
}

function byId(left, right) {
  return String(left.id || "").localeCompare(String(right.id || ""));
}

function byPriority(left, right) {
  return (
    Number(left.priority ?? 0) - Number(right.priority ?? 0) ||
    String(left.created_at || "").localeCompare(String(right.created_at || "")) ||
    byId(left, right)
  );
}

function values(collection) {
  return Object.values(collection || {});
}

function text(value) {
  return String(value ?? "").trim();
}

function domIdPart(value) {
  return text(value).replace(/[^A-Za-z0-9_-]+/g, "-") || "item";
}

function agentLabel(agent) {
  return text(agent?.display_name) || text(agent?.name) || text(agent?.label) || text(agent?.id);
}

function avatarUrl(agentOrDetails) {
  const avatar = agentOrDetails?.avatar;
  const assetId = text(avatar?.asset_id);
  if (!assetId) {
    return "";
  }
  return text(avatar.url) || `${API_BASE}/avatars/${encodeURIComponent(assetId)}`;
}

function agentInitials(agentOrDetails) {
  const label = agentLabel(agentOrDetails);
  const parts = label.split(/[\s._-]+/).filter(Boolean);
  const letters = parts.length > 1
    ? `${parts[0][0] || ""}${parts[1][0] || ""}`
    : label.slice(0, 2);
  return (letters || "A").toUpperCase();
}

function renderAgentAvatar(agentOrDetails, extraClass = "") {
  const src = avatarUrl(agentOrDetails);
  const label = agentLabel(agentOrDetails);
  const className = `agent-avatar ${extraClass}`.trim();
  if (src) {
    return `<span class="${escapeAttr(className)}"><img src="${escapeAttr(src)}" alt="${escapeAttr(`${label} avatar`)}" loading="lazy" /></span>`;
  }
  return `<span class="${escapeAttr(`${className} agent-avatar-fallback`)}" aria-hidden="true">${escapeHtml(agentInitials(agentOrDetails))}</span>`;
}

function roleLabel(roleOrId) {
  const roleId = text(roleOrId?.id ?? roleOrId);
  const role =
    roleOrId && typeof roleOrId === "object"
      ? roleOrId
      : state.roles[roleId];
  if (!role) {
    return roleId;
  }
  return text(role.name) || text(role.id) || roleId;
}

function agentRoleLabel(agent) {
  const roleId = text(agent?.role_id);
  return text(agent?.role_name) || (roleId ? roleLabel(roleId) : "No role");
}

function agentTeamLabel(agent) {
  const teamId = text(agent?.team_id);
  if (!teamId) return "No team";
  const team = state.teams[teamId];
  return team ? `${team.name} (${team.id})` : teamId;
}

function permissionLabel(permission) {
  return humanizeToken(permission);
}

function rolePermissionTooltip(permission) {
  return rolePermissionTooltips[text(permission)] || "Role-specific Team Add-on capability.";
}

function renderRolePermissionTooltip(permission) {
  const tooltip = rolePermissionTooltip(permission);
  const tooltipId = `team-field-tooltip-${++tooltipCounter}`;
  return `
    <span class="field-tooltip permission-token-tooltip" tabindex="0" role="button" aria-label="${escapeAttr(`Help: ${tooltip}`)}" aria-describedby="${escapeAttr(tooltipId)}">
      <span class="field-tooltip-icon" aria-hidden="true">i</span>
      <span class="field-tooltip-bubble" role="tooltip" id="${escapeAttr(tooltipId)}">${escapeHtml(tooltip)}</span>
    </span>
  `;
}

function renderRolePermissionToken(permission) {
  const label = permissionLabel(permission);
  return `
    <span class="permission-token" title="${escapeAttr(rolePermissionTooltip(permission))}">
      <span>${escapeHtml(label)}</span>
      ${renderRolePermissionTooltip(permission)}
    </span>
  `;
}

function renderRolePermissionsInline(permissions, emptyLabel = "no special permissions") {
  const items = splitList(permissions);
  if (!items.length) {
    return escapeHtml(emptyLabel);
  }
  return items.map(renderRolePermissionToken).join(", ");
}

function numberOrZero(value) {
  const parsed = Number.parseInt(text(value), 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function numberOrNull(value) {
  const raw = text(value);
  if (!raw) {
    return null;
  }
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function splitList(value) {
  if (Array.isArray(value)) {
    return value.map(text).filter(Boolean);
  }
  return text(value)
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseGoals(value) {
  return text(value)
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [id, description = "", stateValue = "pending"] = line
        .split("|")
        .map((part) => part.trim());
      return {
        id,
        description: description || id,
        state: stateValue || "pending",
      };
    });
}

function goalsToText(goals) {
  return (goals || [])
    .map((goal) => [goal.id, goal.description, goal.state || "pending"].join(" | "))
    .join("\n");
}

function parseCriterionResults(value) {
  return text(value)
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [criterion_id, stateValue = "passed", finding = "", evidenceRefs = ""] = line
        .split("|")
        .map((part) => part.trim());
      return {
        criterion_id,
        state: stateValue || "passed",
        finding,
        evidence_refs: splitList(evidenceRefs),
      };
    });
}

function escapeHtml(value) {
  return text(value).replace(/[&<>"']/g, (char) => {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char];
  });
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function editButton(type, id, label = "Edit") {
  return `<button type="button" class="record-action" data-edit-type="${escapeAttr(type)}" data-edit-id="${escapeAttr(id)}">${escapeHtml(label)}</button>`;
}

function taskCancellationAvailability(task) {
  const stateValue = text(task?.state);
  if (!directlyCancellableTaskStates.has(stateValue)) {
    return {
      allowed: false,
      explanation: stateValue
        ? `This task is already ${taskStateLabel(task)} and cannot be cancelled.`
        : "This task is unavailable.",
    };
  }
  const assessment = task?.assessment || {};
  const unresolvedIds = uniqueTextList(
    task?.unresolved_descendant_ids || assessment.unresolved_descendant_ids || [],
  );
  if (unresolvedIds.length) {
    return {
      allowed: false,
      explanation: `Resolve or cancel ${unresolvedIds.length} descendant task${unresolvedIds.length === 1 ? "" : "s"} first.`,
    };
  }
  const acceptanceIds = uniqueTextList(assessment.active_acceptance_ids || []);
  const blockingReviewIds = uniqueTextList(
    assessment.active_review_request_ids || [],
  ).filter((reviewId) => {
    const review = state.review_requests?.[reviewId];
    return !(
      text(review?.state) === "blocked" &&
      activeBlockersForRecord("review_request", reviewId).length
    );
  });
  if (acceptanceIds.length || blockingReviewIds.length) {
    return {
      allowed: false,
      explanation: "Finish or retire the active acceptance and review handoffs first.",
    };
  }
  return {
    allowed: true,
    explanation: "Cancel this task with an audited reason.",
  };
}

function taskCancellationButton(task) {
  const availability = taskCancellationAvailability(task);
  return `
    <button
      type="button"
      class="record-action task-cancellation-action"
      data-task-cancel-id="${escapeAttr(task?.id)}"
      title="${escapeAttr(availability.explanation)}"
      ${availability.allowed ? "" : 'disabled aria-disabled="true"'}
    >Cancel task</button>
  `;
}

function fieldText(form, name) {
  if (!form || !name) {
    return "";
  }
  return text(form.elements?.[name]?.value);
}

function setFieldValue(form, name, value) {
  if (!form || !name) {
    return;
  }
  const field = form.elements?.[name];
  if (!field) {
    return;
  }
  field.value = text(value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
}
