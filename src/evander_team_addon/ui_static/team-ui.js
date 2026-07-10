const API_BASE = "/api/addons/team-addon";

const taskStates = [
  "pending",
  "in_progress",
  "blocked",
  "waiting_for_review",
  "finished",
  "accepted",
  "rejected",
];
const caseStatuses = [
  "open",
  "triage",
  "in_progress",
  "waiting",
  "resolved",
  "closed",
];
const taskBoardLanes = [
  { id: "planned", label: "Planned", states: ["pending"] },
  { id: "in-progress", label: "In Progress", states: ["in_progress", "blocked"] },
  { id: "reviewing", label: "Reviewing", states: ["waiting_for_review"] },
  { id: "done", label: "Done", states: ["finished", "accepted"] },
];
const taskBoardStateToLane = new Map(
  taskBoardLanes.flatMap((lane) => lane.states.map((state) => [state, lane.id])),
);
const recordStates = ["pending", "in_progress", "accepted", "rejected", "blocked"];
const agentStatuses = ["active", "paused", "archived"];
const contextClearingPolicies = [
  ["off", "Off", "Never automatically clear context; manual clear still works."],
  ["idle", "When idle", "Clear after the agent becomes idle following work."],
  ["every_wake", "Every wake", "Start each Team wake with a fresh context."],
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
const terminalTaskStates = new Set(["finished", "accepted", "rejected"]);
const terminalRecordStates = new Set(["accepted", "rejected"]);
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
  "review_profiles",
  "memories",
  "inbox",
  "action_records",
  "release_packages",
  "ephemeral_assignments",
  "git_promotions",
  "stand_downs",
  "conversations",
  "conversation_messages",
];

let state = emptyState();
let costState = emptyCostState();
let runtimeFilter = null;
let runtimeFilterTouched = false;
let templates = [];
let timelineState = emptyTimelineState();
let chatState = emptyChatState();
let mailState = emptyMailState();
let mailRequestId = 0;
let selectedMailItemId = null;
let selectedMailState = "";
let chatRequestId = 0;
let chatNewPickerOpen = false;
let selectedChatConversationId = null;
let selectedChatOperatorUserId = null;
let chatSending = false;
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
let activeTeamView = readStoredTeamView();
let teamPrivilegeUnlocked = readTeamPrivilegedUnlockedFromLocation();
let selectedPlanningTaskStates = new Set(defaultPlanningTaskStates);
let selectedOrgTeamId = "all";
let selectedProjectFocusId = null;
let projectCreateFormOpen = false;
let projectReviewProfileCreateProjectId = null;
const projectRecordSectionPages = {};
const projectRecordShowRejected = new Set();
const openProjectRecordSections = new Set();
let selectedTeamRecordKind = "agent";
let selectedTeamRecordId = null;
const expandedTeamRecordGroupIds = new Set();
const teamRecordGroupTabs = {};
const teamRecordGroupPages = {};
const openTeamInlineCreateForms = new Set();
const teamRecordPageSize = 6;
let selectedTaskBoardTeamId = "";
let selectedTaskBoardProjectId = "";
let activeAgentVoiceCall = null;
let teamsCreateTeamPanelOpen = false;
let governanceCreateGroupPanelOpen = false;
let orgContextMenuState = null;
const formDraftRegistry = new Map();
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
]);
const ephemeralWorkKinds = new Set([
  "failed_ephemeral_assignment",
  "cleanup_pending_ephemeral_assignment",
]);
const runtimeFilterLabels = Object.fromEntries(runtimeFilterChoices);
const timelinePageSize = 10;
const mailPageSize = 50;
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
  teamsCreateTeamPanel: document.querySelector("#teams-create-team-panel"),
  teamsCreateTeamForm: document.querySelector("#create-team-from-teams-form"),
  governanceCreateGroupPanel: document.querySelector("#governance-create-panel"),
  governanceCreateGroupForm: document.querySelector("#create-governance-group-form"),
  governanceCreateGroupToggle: document.querySelector("[data-governance-create-toggle]"),
  editModal: document.querySelector("#edit-modal"),
  editModalTitle: document.querySelector("#edit-modal-title"),
  editForm: document.querySelector("#edit-record-form"),
  editFormBody: document.querySelector("#edit-form-body"),
};

const deliveryStatusOrder = [
  "pending",
  "waiting_for_review",
  "accepted",
  "rejected",
  "blocked",
];
const deliveryFilterOptions = new Set(["all", ...deliveryStatusOrder]);
const deliveryStatusLabels = {
  pending: "Pending",
  waiting_for_review: "Waiting for review",
  accepted: "Accepted",
  rejected: "Rejected",
  blocked: "Blocked",
  all: "All",
};

let selectedDeliveryFilter = "all";
let tooltipCounter = 0;

const rolePermissionTooltips = {
  create_root_task: "Allows this role to create top-level project tasks instead of only subtasks inside existing work.",
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
  "setup-template-form:max_cost_usd": "Optional estimated lifetime USD cap for this team. Team wakes stop when estimated accrued cost reaches the cap.",
  "setup-template-form:project_name": "Human-readable project name shown in project cards and filters.",

  "create-team-form:name": "Human-readable team name shown throughout the Team Add-on UI.",
  "create-team-form:manager_existing_agent_id": "Copy session and display defaults from an existing agent without reusing that agent.",
  "create-team-form:manager_session_id": "Session ID used to resume or wake the manager agent.",
  "create-team-form:manager_display_name": "Optional display name. If blank, the generated agent ID is shown.",
  "create-team-form:manager_role_name": "Human-readable role name for the manager role.",
  "create-team-form:operator_communication_target_id": "Optional external target for escalations raised by this team's manager.",
  "create-team-form:escalate_to_governance": "Route manager escalations to governance agenda instead of operator escalation.",
  "create-team-form:max_cost_usd": "Optional estimated lifetime USD cap for this team. Team wakes stop when estimated accrued cost reaches the cap.",

  "create-governance-group-form:name": "Human-readable portfolio or governance group name.",
  "create-governance-group-form:manager_agent_id": "Existing persistent agent that wakes for governance agenda, cost, and blocker signals.",
  "create-governance-group-form:member_team_ids": "Teams supervised by this governance group. Their internal reporting chains stay unchanged.",
  "create-governance-group-form:goals": "Lightweight governance goals, one per line as id | description | state.",
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
  "create-task-form:dependency_task_ids": "Optional dependency task IDs, one per line or comma-separated. Dependencies must be in the same project.",

  "update-task-form:task_id": "Task to update. Selecting a task loads its current editable fields.",
  "update-task-form:state": "New task state. Leave blank to keep the current state.",
  "update-task-form:case_id": "Case link to set or clear for this task.",
  "update-task-form:priority": "New priority. Lower numbers sort earlier.",
  "update-task-form:title": "New title. Leave blank to keep the current title.",
  "update-task-form:description": "New description. Leave blank to keep the current description.",
  "update-task-form:parent_task_id": "Parent task in the same project. Clear the value to remove the parent link.",
  "update-task-form:dependency_task_ids": "Replacement dependency list, one per line or comma-separated.",

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
  "decide-review-form:criteria": "Criterion result rows in criterion_id | state | finding format.",

  "edit:team:name": "Rename the team without changing its immutable ID.",
  "edit:team:operator_communication_target_id": "Set or clear the external target used for operator escalations.",
  "edit:team:escalate_to_governance": "Route manager escalations to governance instead of operator escalation when true.",
  "edit:team:max_cost_usd": "Set or clear the estimated lifetime USD cap that suspends future Team wakes when reached.",
  "edit:team:archived": "Archived teams stay in state; archiving a team also archives its agents.",
  "edit:project:name": "Rename the project without changing its immutable ID.",
  "edit:project:status": "Manual project status override. Later workflow actions may update this again.",
  "edit:project:goals": "Replace project goals using id | description | state rows.",
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
  "edit:task:dependency_task_ids": "Replacement dependency list, one per line or comma-separated.",
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

  host_repo_path: "Host-local working repository path. The canonical remote URL alone is not enough; the host needs a local repository path.",
  bare_repo: "Bare mirror name or absolute bare mirror path. Values starting with / are saved as paths; other values are saved as names.",
  base_branch: "Canonical base branch used for agent branches, reviews, and promotions.",
  canonical_origin_url: "Trusted upstream Git URL used when opening or promoting work outside the sandbox mirror.",
  provider: "Git hosting provider used by host-side PR and promotion helpers.",
  canonical_release_branch_prefix: "Prefix used when constructing canonical release branch names.",
};

document.addEventListener("DOMContentLoaded", () => {
  bindTopLevelTabs();
  bindForms();
  bindRecordEditing();
  bindExistingAgentPickers();
  bindTeamAgentSessionControls();
  bindProjectLeadSelects();
  bindAgentEditTeamScopedSelects();
  bindRuntimeFilters();
  bindDeliveryFilters();
  bindPlanningFilters();
  bindProjectFocus();
  bindTaskBoardSelectors();
  bindOrgFocusTabs();
  bindOrgFlowEvents();
  bindArchivedVisibilityToggle();
  bindGovernanceAgendaVisibilityToggle();
  bindInboxPopoverPositioning();
  bindMailControls();
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
    review_profiles: {},
    inbox: {},
    action_records: {},
    release_packages: {},
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
    description: "Derived timeline from the current TeamState snapshot.",
    filters: {},
    timestampless_count: 0,
    total_count: 0,
    limit: 10,
    offset: 0,
    has_more: false,
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

function emptyMailState() {
  return {
    revision: 0,
    filters: {},
    state_counts: { pending: 0, handled: 0 },
    total_count: 0,
    limit: 50,
    offset: 0,
    has_more: false,
    items: [],
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
  const targets = Array.from(document.querySelectorAll(selector));
  targets.forEach((target) => {
    if (targetIsActive(target)) {
      target.innerHTML = html;
    } else {
      target.innerHTML = "";
    }
  });
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
  }
  syncGovernanceCreateGroupPanel();
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
  const point = orgContextPoint(detail);
  openOrgContextMenu({
    kind: "agent",
    point,
    agentId,
    items: [
      {
        action: "add-subordinate-agent",
        label: "Add subordinate agent",
        disabled: teamMutationLocked() || !state.agents?.[agentId],
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
    .sort(byId)
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
    const soundToggle = event.target?.closest?.("[data-chat-notification-sound]");
    if (soundToggle instanceof HTMLInputElement) {
      chatNotificationSoundMuted = !soundToggle.checked;
      storeChatNotificationSoundMuted();
      return;
    }

    const select = event.target?.closest?.("[data-chat-operator-select]");
    if (!select) {
      return;
    }
    selectedChatOperatorUserId = text(select.value) || null;
    selectedChatConversationId = null;
    chatNewPickerOpen = false;
    closeChatStream();
    void loadConversations({ preserveSelection: false });
  });
}

function bindRecordEditing() {
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

    const chatAgentButton = event.target.closest("[data-chat-agent-id]");
    if (chatAgentButton) {
      event.preventDefault();
      void createOrFocusChat(chatAgentButton.dataset.chatAgentId);
      return;
    }

    const chatConversationButton = event.target.closest(".chat-conversation-row[data-chat-conversation-id]");
    if (chatConversationButton) {
      event.preventDefault();
      selectedChatConversationId = chatConversationButton.dataset.chatConversationId || null;
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
      );
      return;
    }

    const standDownRevokeButton = event.target.closest("[data-stand-down-revoke-agent-id]");
    if (standDownRevokeButton) {
      event.preventDefault();
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

function bindPrivilegeMessages() {
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) {
      return;
    }
    const data = event.data || {};
    if (data.type !== "admin-privileged-operations") {
      return;
    }
    setTeamPrivilegedUnlocked(Boolean(data.privileged_operations?.unlocked));
  });
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
  ["[data-review-profile-criterion-add]", "edit review profile criteria"],
  ["[data-review-profile-criterion-remove]", "edit review profile criteria"],
  ["[data-context-rotation-agent-id]", "clear agent context"],
  ["[data-stand-down-agent-id]", "stand down agents"],
  ["[data-stand-down-revoke-agent-id]", "revoke stand-downs"],
  ["[data-dismiss-action-id]", "dismiss failed actions"],
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
  ["#update-task-form button[type='submit']", "update tasks"],
  ["#decide-review-form button[type='submit']", "record review decisions"],
  ["#edit-record-form button[type='submit']", "save changes"],
];

function applyLockedMutationControls(root = document) {
  if (!teamMutationLocked()) {
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
    button.addEventListener("click", () => {
      const filter = button.dataset.filter || "all";
      setDeliveryFilter(filter);
    });
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

function bindProjectFocus() {
  document.addEventListener("toggle", (event) => {
    const section = event.target;
    if (!section?.matches?.("[data-project-record-section]")) {
      return;
    }
    const key = projectRecordSectionKey(section.dataset.projectId, section.dataset.projectSection);
    if (section.open) {
      openProjectRecordSections.add(key);
    } else {
      openProjectRecordSections.delete(key);
    }
  }, true);

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
      openProjectRecordSections.add(key);
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
      openProjectRecordSections.add(key);
      renderProjectFocus();
    }
  });
}

function bindTaskBoardSelectors() {
  const teamSelect = document.querySelector("[data-task-board-team]");
  const projectSelect = document.querySelector("[data-task-board-project]");
  const updateTeam = () => {
    selectedTaskBoardTeamId = text(teamSelect.value);
    selectedTaskBoardProjectId = "";
    renderTaskBoard();
  };
  const updateProject = () => {
    selectedTaskBoardProjectId = text(projectSelect.value);
    renderTaskBoard();
  };
  teamSelect?.addEventListener("input", updateTeam);
  teamSelect?.addEventListener("change", updateTeam);
  projectSelect?.addEventListener("input", updateProject);
  projectSelect?.addEventListener("change", updateProject);
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
    void loadMail();
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
    void loadView(activeTeamView, { force: true }).finally(() => {
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
  return { ...emptyState(), ...(payload || {}) };
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
  const previousConversations = new Map(
    chatState.conversations.map((conversation) => [text(conversation.id), conversation]),
  );
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
  if (selectedMessagesLoaded) {
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

async function loadConversationDetail(conversationId, requestId = chatRequestId) {
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
    scheduleChatScrollToBottom(normalizedConversationId);
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
    void loadConversationDetail(selectedChatConversationId);
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
    void loadConversationDetail(normalizedConversationId).finally(() => {
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
  if (!body) {
    setStatus("Message is required.", "error");
    return;
  }
  chatSending = true;
  scheduleChatScrollForRender(conversation.id);
  renderChat();
  setStatus("Sending chat message.", "loading");
  try {
    const conversationId = text(conversation.id);
    const response = await teamFetch(`${API_BASE}/conversations/${encodeURIComponent(conversation.id)}/messages`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        body,
      }),
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
    }
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
      renderMail();
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
  const nextFilter = deliveryFilterOptions.has(filter) ? filter : "all";
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
    const active = (button.dataset.filter || "all") === selectedDeliveryFilter;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function renderReviewDeliveryWorkspace() {
  if (!document.querySelector("[data-review-delivery-view]")) {
    return;
  }
  updateDeliveryFilterButtons();
  const workspaceRows = buildReviewDeliveryRows();
  const visibleRows =
    selectedDeliveryFilter === "all"
      ? workspaceRows
      : workspaceRows.filter((row) => row.effectiveStatus === selectedDeliveryFilter);

  if (!visibleRows.length) {
    renderIntoActiveTargets("[data-review-delivery-view]", `
      <p class="empty-state">
        No deliverables match the ${escapeHtml(deliveryStatusLabels[selectedDeliveryFilter] || selectedDeliveryFilter)} filter.
      </p>
    `);
    bindWorkspaceReviewDecisionForms();
    return;
  }

  const teamGroups = groupReviewDeliveryRows(visibleRows);
  const bucketStatuses =
    selectedDeliveryFilter === "all" ? deliveryStatusOrder : [selectedDeliveryFilter];

  renderIntoActiveTargets(
    "[data-review-delivery-view]",
    teamGroups.map((teamGroup) => renderDeliveryTeamGroup(teamGroup, bucketStatuses)).join(""),
  );
  bindWorkspaceReviewDecisionForms();
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
    [
      renderWorkspaceAttentionPanel(model),
      renderWorkspaceDeliveryPanel(model),
    ].join(""),
  );
  renderWorkspaceTarget(
    panel,
    "[data-workspace-status-column]",
    [
      renderWorkspaceTeamStatusPanel(model),
      renderWorkspaceWorkStatusPanel(model),
      renderWorkspaceNavigationPanel(),
    ].join(""),
  );
}

function renderWorkspaceTarget(panel, selector, html) {
  const target = panel.querySelector(selector);
  if (target) {
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
    .sort(byId);
  const visibleAgentIds = new Set(visibleAgents.map((agent) => text(agent.id)));
  const openTasks = values(state.tasks)
    .filter((task) => !terminalTaskStates.has(task.state))
    .filter((task) => workspaceTaskIsVisible(task, visibleProjectIds))
    .sort(comparePlanningTasks);
  const blockedTasks = openTasks.filter(isTaskWaitingOrBlocked);
  const pendingInbox = values(state.inbox)
    .filter((item) => text(item.state) !== "handled")
    .filter((item) => visibleAgentIds.has(text(item.recipient_agent_id)))
    .sort(compareWorkspaceInboxItems);
  const pendingReviews = values(state.review_requests)
    .filter((review) => !terminalRecordStates.has(review.state))
    .filter((review) => workspaceReviewIsVisible(review, visibleProjectIds))
    .sort(compareWorkspaceReviews);
  const pendingGovernanceAgenda = values(state.governance_agenda)
    .filter((item) => text(item.state || "pending") === "pending")
    .filter((item) => workspaceGovernanceAgendaIsVisible(item, visibleTeamIds, visibleProjectIds))
    .sort(compareWorkspaceGovernanceAgenda);
  const runtime = state.runtime_operations || { agents: [], attention: [] };
  const runtimeAttention = (runtime.attention || [])
    .filter(isVisibleRuntimeRow)
    .sort(runtimeSortRows);
  const dueAgents = dueLeasedAgentsForFilter(runtime.agents || []);
  const deliveryRows = buildReviewDeliveryRows();
  const costAttention = workspaceCostAttentionRows(visibleTeamIds, visibleProjectIds);
  const attentionItems = workspaceAttentionItems({
    runtimeAttention,
    costAttention,
    blockedTasks,
    pendingReviews,
    pendingInbox,
    pendingGovernanceAgenda,
  });
  return {
    visibleTeams,
    visibleProjects,
    visibleAgents,
    openTasks,
    blockedTasks,
    pendingInbox,
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
      detail: costState.error ? "cost data unavailable" : "estimated cap checks",
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
  const limit = 7;
  const visibleItems = model.attentionItems.slice(0, limit);
  const remaining = Math.max(0, model.attentionItems.length - visibleItems.length);
  const body = visibleItems.length
    ? `
      <ul class="workspace-attention-list">
        ${visibleItems.map(renderWorkspaceAttentionItem).join("")}
      </ul>
      ${remaining ? `<div class="workspace-more-row">${remaining} more item${remaining === 1 ? "" : "s"} in detailed tabs.</div>` : ""}
    `
    : `<p class="empty-state">No runtime blockers, pending reviews, open inbox items, or governance agenda items need attention.</p>`;
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
    return text(item.state) !== "handled" && agent && text(agent.team_id) === text(team.id);
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
  const openCases = values(state.cases).filter((item) => isVisibleCase(item) && !["resolved", "closed"].includes(text(item.status)));
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

function renderWorkspaceNavigationPanel() {
  const links = [
    ["projects", "Projects", "Project catalog and review profiles"],
    ["task-board", "Task board", "Project work by lane"],
    ["cases", "Cases", "Intake and ticket context"],
    ["governance", "Governance", "Groups, agenda, and oversight"],
    ["mail", "Agent Mail", "Internal messages and historical correspondence"],
    ["timeline", "Timeline", "Derived audit view"],
    ["org", "Org chart", "Team graph"],
  ];
  return `
    <section class="workspace-dashboard-panel" aria-labelledby="workspace-navigation-heading">
      <div class="workspace-panel-header">
        <div>
          <p class="section-label">Drill down</p>
          <h3 id="workspace-navigation-heading">Detailed tabs</h3>
        </div>
      </div>
      <div class="workspace-link-grid">
        ${links
          .map(([view, label, detail]) => `
            <button type="button" class="workspace-link-card" data-team-link-view="${escapeAttr(view)}">
              <strong>${escapeHtml(label)}</strong>
              <span>${escapeHtml(detail)}</span>
            </button>
          `)
          .join("")}
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
      rows.push({
        title: `${group.name || groupId} cost attention`,
        meta: cost?.cost_limit_reached
          ? `${formatUsd(cost.estimated_cost)} estimated`
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
    const project = state.projects[text(task.project_id)];
    const owner = state.agents[text(task.owner_agent_id)];
    items.push({
      title: task.title || task.id,
      meta: [
        humanizeToken(task.state),
        project ? project.name || project.id : text(task.project_id),
        owner ? agentLabel(owner) : text(task.owner_agent_id),
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

function workspaceRuntimeAttentionMeta(row) {
  return [
    humanizeToken(row.kind || "attention"),
    row.state ? humanizeToken(row.state) : "",
    row.agent_id ? `agent ${row.agent_id}` : "",
    row.project_id ? `project ${row.project_id}` : "",
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
    <span class="state-pill warning team-cost-blocked-pill" title="Team wakes are suspended until the estimated cap is raised or cleared.">
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
      <span>Team wakes are suspended until the estimated cap is raised or cleared. ${escapeHtml(formatUsd(cost.estimated_cost))} estimated used of ${escapeHtml(capLabel)} lifetime cap.${escapeHtml(coverageNote)}</span>
    </div>
  `;
}

function renderGovernance() {
  if (!document.querySelector("[data-governance-view]")) {
    return;
  }
  const groups = values(state.governance_groups).filter(isVisibleGovernanceGroup).sort(byId);
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
    .filter(Boolean);
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
      <div class="meta-line">${escapeHtml(group.id)} - manager ${escapeHtml(manager ? agentLabel(manager) : group.manager_agent_id)}</div>
      ${cost ? renderCostCounter(cost, { label: "Estimated group cost", compact: true }) : ""}
      ${renderGovernanceGoals(group.goals || [])}
      ${renderGovernanceMemberTeams(memberTeams, cost)}
      ${renderGovernanceAgenda(group, visibleAgenda, pendingAgenda)}
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
            return `<li><strong>${escapeHtml(team.name)}</strong> <span>${escapeHtml(team.id)}${escapeHtml(costLabel)}${escapeHtml(cap)}</span></li>`;
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
          .map((goal) => `<li><strong>${escapeHtml(goal.description)}</strong></li>`)
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

function renderCostCounter(cost, { label, compact = false, error = "" } = {}) {
  if (!cost) {
    return `
      <div class="cost-counter cost-counter--warning${compact ? " cost-counter--compact" : ""}">
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
  if (maxCost !== null) {
    parts.push(
      limitReached
        ? `estimated limit reached ${formatUsd(maxCost)}`
        : `${formatUsd(cost.remaining_cost_usd)} remaining`,
    );
  }
  if (events) {
    parts.push(`${events} event${events === 1 ? "" : "s"}`);
  }
  if (unpriced) {
    parts.push(`${unpriced} unpriced`);
  }
  const sourceSummary = costSourceSummary(cost.source_counts || {});
  if (sourceSummary) {
    parts.push(sourceSummary);
  }
  return `
    <div class="${className}${compact ? " cost-counter--compact" : ""}">
      <span>${escapeHtml(label || "Cost")}</span>
      <strong>${escapeHtml(formatUsd(cost.estimated_cost))}</strong>
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
    .map(([source, count]) => `${source}:${numberOrZero(count)}`)
    .join(", ");
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
    runtimeSeverityRank(left.severity) - runtimeSeverityRank(right.severity) ||
    String(left.kind || "").localeCompare(String(right.kind || "")) ||
    String(left.id || "").localeCompare(String(right.id || ""))
  );
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

function renderRuntimeAttentionRow(row) {
  if (activeTeamView === "operations") {
    return renderCompactRuntimeAttentionRow(row);
  }
  const refs = runtimeReferenceParts(row);
  const stateLine = row.state ? `State ${escapeHtml(row.state)}` : "";
  const errorLine = row.error ? `Error ${escapeHtml(row.error)}` : "";
  const detail = row.detail ? `<div class="meta-line">${escapeHtml(row.detail)}</div>` : "";
  const nextActionLine = row.next_action_hint
    ? `<div class="meta-line">Next action: ${escapeHtml(row.next_action_hint)}</div>`
    : "";
  const actions =
    row.kind === "failed_action_record" && row.action_record_id
      ? `
        <div class="record-actions runtime-attention-actions">
          <button
            type="button"
            class="record-action"
            data-dismiss-action-id="${escapeAttr(row.action_record_id)}"
          >Dismiss</button>
        </div>
      `
      : "";
  return `
    <li class="record-item runtime-item runtime-attention-item runtime-severity-${escapeHtml(row.severity || "info")}">
      <div class="record-line">
        <strong>${escapeHtml(row.title || row.id)}</strong>
        <span class="state-pill ${escapeHtml(row.severity || "info")}">${escapeHtml(row.severity || "info")}</span>
      </div>
      <div class="meta-line">${escapeHtml(row.kind || "attention")} - ${escapeHtml(row.id)}</div>
      ${detail}
      ${
        stateLine || errorLine
          ? `<div class="meta-line">${escapeHtml([stateLine, errorLine].filter(Boolean).join(" - "))}</div>`
          : ""
      }
      ${nextActionLine}
      ${refs ? `<div class="meta-line">${refs}</div>` : ""}
      ${actions}
    </li>
  `;
}

function renderCompactRuntimeAttentionRow(row) {
  const refs = runtimeReferenceParts(row);
  const stateLine = row.state ? `State ${escapeHtml(row.state)}` : "";
  const errorLine = row.error ? `Error ${escapeHtml(row.error)}` : "";
  const nextActionLine = row.next_action_hint
    ? `<div class="meta-line">Next action: ${escapeHtml(row.next_action_hint)}</div>`
    : "";
  const action =
    row.kind === "failed_action_record" && row.action_record_id
      ? `
        <div class="record-actions runtime-attention-actions">
          <button
            type="button"
            class="record-action"
            data-dismiss-action-id="${escapeAttr(row.action_record_id)}"
          >Dismiss</button>
        </div>
      `
      : "";
  const summaryParts = [
    humanizeToken(row.kind || "attention"),
    row.agent_id ? `agent ${row.agent_id}` : "",
    row.project_id ? `project ${row.project_id}` : "",
  ].filter(Boolean);
  return `
    <li class="record-item runtime-item runtime-item-compact runtime-severity-${escapeHtml(row.severity || "info")}">
      <details>
        <summary class="runtime-summary compact-record-summary">
          <span class="compact-record-main">
            <strong>${escapeHtml(row.title || row.id)}</strong>
            <span>${escapeHtml(summaryParts.join(" - ") || row.id)}</span>
          </span>
          <span class="runtime-summary-meta">
            <span class="state-pill ${escapeHtml(row.severity || "info")}">${escapeHtml(row.severity || "info")}</span>
            ${row.state ? `<span>${escapeHtml(humanizeToken(row.state))}</span>` : ""}
          </span>
        </summary>
        <div class="compact-record-body">
          <div class="meta-line">${escapeHtml(row.kind || "attention")} - ${escapeHtml(row.id)}</div>
          ${row.detail ? `<div class="meta-line">${escapeHtml(row.detail)}</div>` : ""}
          ${
            stateLine || errorLine
              ? `<div class="meta-line">${escapeHtml([stateLine, errorLine].filter(Boolean).join(" - "))}</div>`
              : ""
          }
          ${nextActionLine}
          ${refs ? `<div class="meta-line">${refs}</div>` : ""}
          ${action}
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
  const contextPolicy = contextClearingPolicyValue(agent);
  const lastRotated = runtimeText(agent.last_context_rotated_at);
  const handoffSummary = text(agent.last_handoff_summary);
  return `
    <li class="record-item runtime-item runtime-agent-item">
      <div class="record-line">
        <strong>${escapeHtml(agent.id)}</strong>
        <div class="record-actions">
          <span class="state-pill ${escapeHtml(status)}">${escapeHtml(status)}</span>
          ${sessionStatus.isExpired ? `<span class="state-pill critical">session expired</span>` : ""}
          ${renderAgentStandDownControls(agent.id, agent)}
          <button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(agent.id)}">Clear context</button>
        </div>
      </div>
      <div class="meta-line">${escapeHtml(agentRoleLabel(agent))} - ${escapeHtml(sessionSummary)}${agentProjectSummary(agent) ? ` - projects ${escapeHtml(agentProjectSummary(agent))}` : ""}</div>
      <div class="meta-line">Last wake ${escapeHtml(lastWake)} - Next wake ${escapeHtml(nextWake)} - Wake lease ${escapeHtml(wakeLease)}</div>
      <div class="meta-line">Wake reason ${escapeHtml(wakeReason)} - No progress ${escapeHtml(agent.no_progress_count ?? 0)} - Active dispatch ${escapeHtml(dispatchId)}</div>
      <div class="meta-line">Context clearing ${escapeHtml(contextClearingPolicyLabel(contextPolicy))} - ${escapeHtml(contextPending ? "pending" : "not pending")} - Last rotated ${escapeHtml(lastRotated)}</div>
      ${
        handoffSummary
          ? `<details class="runtime-handoff"><summary>Handoff summary</summary><pre>${escapeHtml(handoffSummary)}</pre></details>`
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
      <details>
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
            <button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(agent.id)}">Clear context</button>
          </div>
          <div class="meta-line">Last wake ${escapeHtml(lastWake)} - Next wake ${escapeHtml(nextWake)} - Wake lease ${escapeHtml(wakeLease)}</div>
          <div class="meta-line">Wake reason ${escapeHtml(wakeReason)} - No progress ${escapeHtml(agent.no_progress_count ?? 0)} - Active dispatch ${escapeHtml(dispatchId)}</div>
          <div class="meta-line">Context clearing ${escapeHtml(contextClearingPolicyLabel(contextPolicy))} - ${escapeHtml(contextPending ? "pending" : "not pending")} - Last rotated ${escapeHtml(lastRotated)}</div>
          ${
            handoffSummary
              ? `<details class="runtime-handoff"><summary>Handoff summary</summary><pre>${escapeHtml(handoffSummary)}</pre></details>`
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
    runtimeFilter = runtime.attention.length ? "needs_attention" : "all";
  }

  const agents = runtimeAgentsForFilter(runtime.agents || []);
  const dueLeasedAgents = dueLeasedAgentsForFilter(runtime.agents || []);
  const visibleRuntimeAttention = (runtime.attention || []).filter(isVisibleRuntimeRow);
  const attention = runtimeAttentionForFilter(visibleRuntimeAttention, runtimeFilter);
  const allAttention = [...(runtime.attention || [])].sort(runtimeSortRows);
  const attentionCount = allAttention.length;
  const visibleAttentionCount = attention.length;

  const sections = [];

  if (runtimeFilter === "all" || runtimeFilter === "needs_attention") {
    sections.push(
      renderRuntimeSection({
        title: runtimeFilter === "all" ? "Persistent runtime state" : "Agents needing attention",
        count: runtimeFilter === "all" ? `${agents.length} visible` : `${dueLeasedAgents.length} visible`,
        emptyMessage: runtimeFilter === "all" ? "No agent runtime data yet." : "No due or leased agents.",
        body: renderRuntimeAgentRows(
          runtimeFilter === "all" ? agents : dueLeasedAgents,
          runtimeFilter === "all" ? "No agent runtime data yet." : "No due or leased agents.",
        ),
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
    sections.push(
      renderRuntimeSection({
        title: "Blockers and wake-ups",
        count: `${visibleAttentionCount} visible / ${attentionCount} total`,
        emptyMessage: "No current blockers.",
        body: renderRuntimeAttentionRows(attention, "No current blockers."),
      }),
    );
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
  const visibleTeams = teams.filter(isVisibleTeam).sort(byId);
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
    .sort(byId);
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
    fitKey: orgFlowLayoutFitKey(nodes, edges),
  };
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
    animated: kind === "governance",
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
    costHtml: cost ? renderCostCounter(cost, { label: "Estimated group cost", compact: true }) : "",
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
    const allAgents = values(state.agents).sort(byId);
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
                      <button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(details.id)}">Clear context</button>
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
  const allProjects = values(state.projects).sort(byId);
  const projects = allProjects.filter((project) => isVisibleProject(project) && visibleTeamIds.has(text(project.team_id)));
  const allRoles = values(state.roles).sort(byId);
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
        <label>Goal <textarea name="goal_description" rows="2"></textarea></label>
      </div>
      <details class="team-inline-create-advanced">
        <summary>Git config</summary>
        <div class="team-inline-create-fields">
          <label>Host repo path <input name="host_repo_path" /></label>
          <label>Bare repo <input name="bare_repo" placeholder="repo.git or /srv/git/repo.git" /></label>
          ${gitImportControls()}
          <label>Base branch <input name="base_branch" placeholder="main" /></label>
          <label>Canonical origin URL <input name="canonical_origin_url" /></label>
          <label>Provider <select name="provider">${renderSelectOptions(gitProviders, { placeholder: "Default" })}</select></label>
          <label>Canonical release branch prefix <input name="canonical_release_branch_prefix" /></label>
        </div>
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
        ${renderAgentDetailField("Open tasks", `${openTasks.length} / ${projectTasks.length}`)}
        ${renderAgentDetailField("Deliverables", projectDeliverables.length)}
      </div>
      ${renderProjectCostCounter(project.id)}
      ${renderProjectGit(project.git)}
      <section class="agent-detail-section">
        <h3>Goals</h3>
        ${renderGoals(project.goals || [], { compact: true })}
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
      </div>
      <div class="agent-detail-grid">
        ${renderAgentDetailField("Manager", manager ? `${agentLabel(manager)} (${manager.id})` : text(team.manager_agent_id) || "None")}
        ${renderAgentDetailField("Agents", teamRecords.agents.length)}
        ${renderAgentDetailField("Projects", teamRecords.projects.length)}
        ${renderAgentDetailField("Roles", teamRecords.roles.length)}
        ${renderAgentDetailField("Escalate to governance", Boolean(team.escalate_to_governance) ? "Enabled" : "Disabled")}
        ${renderAgentDetailField("Operator communication target", text(team.operator_communication_target_id) || "None")}
        ${renderAgentDetailField("Estimated max cost USD", text(team.max_cost_usd) || "None")}
      </div>
      <section class="agent-detail-section">
        <h3>Team cost</h3>
        ${renderTeamCostCounter(team.id) || "<p class=\"empty-state\">No cost estimate available.</p>"}
        ${renderCostBackfillControl()}
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
          ? `<section class="agent-detail-section"><h3>Description</h3><p class="meta-line">${escapeHtml(role.description)}</p></section>`
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

function bindMailControls() {
  document.addEventListener("change", (event) => {
    const filter = event.target?.closest?.("[data-mail-filter]");
    if (!filter) {
      return;
    }
    if (filter.dataset.mailFilter === "team_id") {
      const selectedTeamId = text(filter.value);
      const selectedAgent = state.agents[text(elements.mailAgentFilter?.value)];
      if (selectedAgent && text(selectedAgent.team_id) !== selectedTeamId) {
        elements.mailAgentFilter.value = "";
      }
    }
    if (filter.dataset.mailFilter === "agent_id" && !text(filter.value)) {
      elements.mailDirectionFilter.value = "";
    }
    selectedMailItemId = null;
    void loadMail({ offset: 0 });
  });

  document.addEventListener("submit", (event) => {
    const form = event.target?.closest?.("[data-mail-search-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    selectedMailItemId = null;
    void loadMail({ offset: 0 });
  });

  document.addEventListener("click", (event) => {
    const refresh = event.target?.closest?.("[data-mail-refresh]");
    if (refresh) {
      event.preventDefault();
      void loadMail({ offset: mailState.offset || 0 });
      return;
    }
    const folder = event.target?.closest?.("[data-mail-state]");
    if (folder) {
      event.preventDefault();
      selectedMailState = text(folder.dataset.mailState);
      selectedMailItemId = null;
      void loadMail({ offset: 0 });
      return;
    }
    const message = event.target?.closest?.("[data-mail-item-id]");
    if (message) {
      event.preventDefault();
      selectedMailItemId = text(message.dataset.mailItemId);
      renderMail();
      return;
    }
    const pageButton = event.target?.closest?.("[data-mail-page]");
    if (pageButton) {
      event.preventDefault();
      const direction = text(pageButton.dataset.mailPage);
      const delta = direction === "next" ? mailState.limit : -mailState.limit;
      selectedMailItemId = null;
      void loadMail({ offset: Math.max(0, Number(mailState.offset || 0) + delta) });
    }
  });
}

async function loadMail({ offset = 0 } = {}) {
  const requestId = ++mailRequestId;
  const query = mailSearchParams(offset);
  mailState = { ...mailState, loading: true, error: null };
  if (activeTeamView === "mail") {
    renderMail();
  }
  try {
    const response = await teamFetch(`${API_BASE}/inbox?${query.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    if (requestId !== mailRequestId) {
      return payload;
    }
    mailState = {
      ...emptyMailState(),
      ...payload,
      state_counts: { ...emptyMailState().state_counts, ...(payload.state_counts || {}) },
      items: Array.isArray(payload.items) ? payload.items : [],
      loading: false,
      error: null,
    };
    const selectedStillVisible = mailState.items.some((item) => item.id === selectedMailItemId);
    if (!selectedStillVisible) {
      selectedMailItemId = mailState.items[0]?.id || null;
    }
    if (activeTeamView === "mail") {
      renderMail();
    }
    return payload;
  } catch (error) {
    if (requestId !== mailRequestId) {
      return null;
    }
    mailState = {
      ...mailState,
      loading: false,
      error: error instanceof Error ? error.message : "Agent mail failed to load.",
    };
    if (activeTeamView === "mail") {
      renderMail();
    }
    return null;
  }
}

function mailSearchParams(offset = 0) {
  const params = new URLSearchParams({
    limit: String(mailPageSize),
    offset: String(Math.max(0, Number(offset) || 0)),
  });
  const agentId = text(elements.mailAgentFilter?.value);
  const values = {
    team_id: elements.mailTeamFilter?.value,
    agent_id: agentId,
    direction: agentId ? elements.mailDirectionFilter?.value : "",
    item_type: elements.mailTypeFilter?.value,
    state: selectedMailState,
    q: elements.mailSearchInput?.value,
  };
  Object.entries(values).forEach(([key, value]) => {
    const normalized = text(value);
    if (normalized) {
      params.set(key, normalized);
    }
  });
  return params;
}

function renderMail() {
  renderMailFilterOptions();
  renderMailFolders();
  renderMailStatus();
  renderMailList();
  renderMailPagination();
  renderMailReader();
}

function renderMailFilterOptions() {
  const selectedTeamId = text(elements.mailTeamFilter?.value);
  const teamOptions = values(state.teams)
    .sort((left, right) => agentLabel(left).localeCompare(agentLabel(right)) || byId(left, right))
    .map((team) => [team.id, `${team.name || team.id} (${team.id})`]);
  if (elements.mailTeamFilter) {
    elements.mailTeamFilter.innerHTML = renderSelectOptions(teamOptions, {
      selectedValue: selectedTeamId,
      placeholder: "All teams",
    });
  }

  const selectedAgentId = text(elements.mailAgentFilter?.value);
  const agentOptions = values(state.agents)
    .filter((agent) => !selectedTeamId || text(agent.team_id) === selectedTeamId)
    .sort((left, right) => agentLabel(left).localeCompare(agentLabel(right)) || byId(left, right))
    .map((agent) => [agent.id, `${agentLabel(agent)} (${agent.id})`]);
  if (elements.mailAgentFilter) {
    elements.mailAgentFilter.innerHTML = renderSelectOptions(agentOptions, {
      selectedValue: selectedAgentId,
      placeholder: "Everyone",
    });
  }
  const hasAgent = Boolean(text(elements.mailAgentFilter?.value));
  if (elements.mailDirectionFilter) {
    elements.mailDirectionFilter.disabled = !hasAgent;
    if (!hasAgent) {
      elements.mailDirectionFilter.value = "";
    }
  }
}

function renderMailFolders() {
  const pending = Number(mailState.state_counts?.pending || 0);
  const handled = Number(mailState.state_counts?.handled || 0);
  document.querySelectorAll("[data-mail-state]").forEach((button) => {
    button.setAttribute(
      "aria-pressed",
      text(button.dataset.mailState) === selectedMailState ? "true" : "false",
    );
  });
  const counts = { all: pending + handled, pending, handled };
  document.querySelectorAll("[data-mail-folder-count]").forEach((element) => {
    element.textContent = String(counts[element.dataset.mailFolderCount] || 0);
  });
}

function renderMailStatus() {
  if (!elements.mailStatus) {
    return;
  }
  elements.mailStatus.className = `mail-status${mailState.error ? " error" : ""}`;
  if (mailState.loading) {
    elements.mailStatus.textContent = "Opening the mail archive...";
    return;
  }
  if (mailState.error) {
    elements.mailStatus.textContent = mailState.error;
    return;
  }
  const noun = mailState.total_count === 1 ? "message" : "messages";
  elements.mailStatus.textContent = `${mailState.total_count} ${noun} matched.`;
}

function renderMailList() {
  if (!elements.mailList) {
    return;
  }
  if (!mailState.items.length) {
    elements.mailList.innerHTML = `<p class="empty-state">No correspondence matched these filters.</p>`;
    return;
  }
  elements.mailList.innerHTML = mailState.items.map((item) => renderMailRow(item)).join("");
}

function renderMailRow(item) {
  const selected = item.id === selectedMailItemId;
  const correspondent = mailCorrespondent(item);
  const party = mailRowParty(item);
  const subject = mailSubject(item);
  const preview = mailBody(item).replace(/\s+/g, " ").trim();
  const priority = text(item.priority);
  return `
    <button
      type="button"
      class="mail-message-row${selected ? " is-selected" : ""}${item.state === "pending" ? " is-pending" : ""}"
      data-mail-item-id="${escapeAttr(item.id)}"
      aria-pressed="${selected ? "true" : "false"}"
    >
      ${renderAgentAvatar(correspondent, "mail-row-avatar")}
      <span class="mail-row-main">
        <span class="mail-row-line">
          <span class="mail-row-party">${escapeHtml(party)}</span>
          <time class="mail-row-time">${escapeHtml(formatMailRowTimestamp(item.created_at))}</time>
        </span>
        <span class="mail-row-subject">${escapeHtml(subject)}</span>
        <span class="mail-row-preview">${escapeHtml(preview || humanizeToken(item.item_type))}</span>
        <span class="mail-row-meta">${escapeHtml(humanizeToken(item.item_type))} · ${escapeHtml(item.state === "pending" ? "Awaiting action" : "Filed")}</span>
      </span>
      ${priority === "high" ? `<span class="mail-priority-mark" title="High priority">!</span>` : ""}
    </button>
  `;
}

function renderMailPagination() {
  if (!elements.mailPagination) {
    return;
  }
  const total = Number(mailState.total_count || 0);
  const offset = Number(mailState.offset || 0);
  const start = total ? offset + 1 : 0;
  const end = Math.min(total, offset + mailState.items.length);
  elements.mailPagination.innerHTML = `
    <span>${escapeHtml(`${start}-${end} of ${total}`)}</span>
    <span class="mail-pagination-actions">
      <button type="button" data-mail-page="previous" ${offset <= 0 ? "disabled" : ""}>Previous</button>
      <button type="button" data-mail-page="next" ${mailState.has_more ? "" : "disabled"}>Next</button>
    </span>
  `;
}

function renderMailReader() {
  if (!elements.mailReader) {
    return;
  }
  const item = mailState.items.find((candidate) => candidate.id === selectedMailItemId);
  if (!item) {
    elements.mailReader.innerHTML = `<p class="empty-state">Select a message to read it.</p>`;
    return;
  }
  const sender = mailSender(item);
  const recipient = item.recipient || { id: item.recipient_agent_id, display_name: item.recipient_agent_id };
  const task = item.task;
  const updated = text(item.updated_at) && text(item.updated_at) !== text(item.created_at)
    ? `<span>Filed ${escapeHtml(formatMailTimestamp(item.updated_at))}</span>`
    : "";
  elements.mailReader.innerHTML = `
    <header class="mail-reader-header">
      <h3>${escapeHtml(mailSubject(item))}</h3>
      <div class="mail-reader-identity">
        ${renderAgentAvatar(sender, "mail-reader-avatar")}
        <div class="mail-reader-addresses">
          <strong>${escapeHtml(agentLabel(sender))}${sender.role_name ? ` · ${escapeHtml(sender.role_name)}` : ""}</strong>
          <span>to ${escapeHtml(agentLabel(recipient))}${recipient.role_name ? ` · ${escapeHtml(recipient.role_name)}` : ""}</span>
        </div>
        <time class="mail-reader-date">${escapeHtml(formatMailTimestamp(item.created_at))}</time>
      </div>
      <div class="mail-reader-pills">
        <span class="state-pill ${escapeAttr(item.state || "pending")}">${escapeHtml(item.state === "pending" ? "Awaiting action" : "Filed")}</span>
        <span class="state-pill">${escapeHtml(inboxItemLabels[item.item_type] || humanizeToken(item.item_type))}</span>
        <span class="state-pill">${escapeHtml(humanizeToken(item.priority || "normal"))} priority</span>
        ${item.response_requested ? `<span class="state-pill warning">Reply requested</span>` : ""}
      </div>
    </header>
    <div class="mail-reader-body">${escapeHtml(mailBody(item) || "No message body was recorded.")}</div>
    <div class="mail-reader-context">
      ${task ? `<span>Task: ${escapeHtml(task.title)} (${escapeHtml(task.id)}) · ${escapeHtml(humanizeToken(task.state))}</span>` : ""}
      <span>Message ID: ${escapeHtml(item.id)}</span>
      ${updated}
    </div>
    <details class="mail-reader-envelope">
      <summary>Full message envelope</summary>
      <dl class="mail-envelope-grid">${renderMailEnvelope(item)}</dl>
    </details>
  `;
}

function mailSender(item) {
  if (item.sender) {
    return item.sender;
  }
  const createdBy = text(item.payload?.created_by);
  return {
    id: createdBy || "operator-system",
    display_name: createdBy || "Operator / system",
    role_name: createdBy ? "External sender" : "System",
  };
}

function mailCorrespondent(item) {
  const focusAgentId = text(elements.mailAgentFilter?.value);
  if (focusAgentId && text(item.sender_agent_id) === focusAgentId) {
    return item.recipient || { id: item.recipient_agent_id, display_name: item.recipient_agent_id };
  }
  return mailSender(item);
}

function mailRowParty(item) {
  const sender = mailSender(item);
  const recipient = item.recipient || { id: item.recipient_agent_id, display_name: item.recipient_agent_id };
  const focusAgentId = text(elements.mailAgentFilter?.value);
  if (focusAgentId && text(item.sender_agent_id) === focusAgentId) {
    return `To: ${agentLabel(recipient)}`;
  }
  if (focusAgentId) {
    return `From: ${agentLabel(sender)}`;
  }
  return `${agentLabel(sender)} → ${agentLabel(recipient)}`;
}

function mailSubject(item) {
  const payload = item.payload || {};
  const candidates = [
    payload.title,
    payload.subject,
    payload.summary,
    payload.question,
    payload.message,
    payload.instructions,
  ];
  const subject = candidates.map(text).find(Boolean);
  return subject || `${inboxItemLabels[item.item_type] || humanizeToken(item.item_type)} ${item.id}`;
}

function mailBody(item) {
  const payload = item.payload || {};
  const subject = mailSubject(item);
  const candidates = [
    payload.message,
    payload.question,
    payload.instructions,
    payload.finding,
    payload.summary,
  ];
  const body = candidates.map(text).find((value) => value && (value !== subject || candidates.length === 1));
  if (body) {
    return body;
  }
  const fallback = candidates.map(text).find(Boolean);
  return fallback || "";
}

function renderMailEnvelope(item) {
  const entries = [
    ["Message ID", item.id],
    ["From", `${agentLabel(mailSender(item))} (${item.sender_agent_id || "external"})`],
    ["To", `${agentLabel(item.recipient)} (${item.recipient_agent_id})`],
    ["Created", formatMailTimestamp(item.created_at)],
    ["Updated", formatMailTimestamp(item.updated_at)],
    ["Type", item.item_type],
    ["State", item.state],
    ["Priority", item.priority],
    ["Response requested", item.response_requested ? "Yes" : "No"],
    ["Task", item.task_id || "None"],
    ...Object.entries(item.payload || {}).map(([key, value]) => [humanizeToken(key), mailEnvelopeValue(value)]),
  ];
  return entries
    .map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`)
    .join("");
}

function mailEnvelopeValue(value) {
  if (value && typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return text(value);
}

function formatMailTimestamp(value) {
  const raw = text(value);
  const parsed = new Date(raw);
  if (!raw || Number.isNaN(parsed.getTime())) {
    return raw || "Unknown time";
  }
  return parsed.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatMailRowTimestamp(value) {
  const raw = text(value);
  const parsed = new Date(raw);
  if (!raw || Number.isNaN(parsed.getTime())) {
    return raw;
  }
  const now = new Date();
  const sameYear = parsed.getFullYear() === now.getFullYear();
  return parsed.toLocaleString([], sameYear
    ? { month: "short", day: "numeric" }
    : { year: "numeric", month: "short", day: "numeric" });
}

function renderChat() {
  renderChatOperatorView();
  renderChatConversationList();
  renderChatThread();
  updateTeamViewTabs();
  applyLockedMutationControls();
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
  const composerState = captureChatComposerState(conversation.id);
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
      ${renderChatComposer(label, readOnly)}
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
  composer.outerHTML = renderChatComposer(label, readOnly);
  return true;
}

function renderChatThreadHeader(conversation, agent, label, readOnly) {
  return `
    <header class="chat-thread-header">
      <div class="chat-thread-title">
        ${renderChatAvatar(agent)}
        <div>
          <h2>${escapeHtml(label)}</h2>
          <div class="meta-line">${escapeHtml(agent.session_id || conversation.agent_id || "")}</div>
        </div>
      </div>
      <div class="chat-thread-actions">
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

function renderChatComposer(label, readOnly) {
  return `
    <form class="chat-composer" data-chat-message-form>
      <textarea name="body" rows="3" placeholder="${readOnly ? "Read-only chat history" : `Message ${escapeAttr(label)}`}" ${chatSending || readOnly ? "disabled" : ""}></textarea>
      <button type="submit" ${chatSending || readOnly ? "disabled" : ""}>Send</button>
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
  return `
    <article class="chat-message chat-message--${escapeAttr(sender)} chat-message--${escapeAttr(type)}">
      <div class="chat-message-bubble">
        <p>${escapeHtml(message.body || "")}</p>
        <div class="chat-message-meta">
          <span>${escapeHtml(chatMessageSenderLabel(sender, metadata))}</span>
          <time>${escapeHtml(formatChatTimestamp(message.created_at))}</time>
          ${status ? `<span>${escapeHtml(chatStatusLabel(status))}</span>` : ""}
        </div>
        ${message.error ? `<div class="chat-message-error">${escapeHtml(message.error)}</div>` : ""}
      </div>
    </article>
  `;
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
        <button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(details.id)}">Clear context</button>
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
    rows.push(
      `Context clearing ${contextClearingPolicyLabel(runtime.contextClearingPolicy)}${
        runtime.contextRotationPending ? " - pending" : ""
      }`,
    );
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
          return `<li>${escapeHtml(task.title)} <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(task.state)}</span></li>`;
        })
        .join("")}
    </ul>
  `;
}

function renderRoles() {
  if (!document.querySelector("[data-roles-view]")) {
    return;
  }
  const allRoles = values(state.roles).sort(byId);
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
    .filter((item) => item.recipient_agent_id === agentId && item.state !== "handled")
    .sort(byId);
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

function operatorRootStandDown(agentId, record) {
  const normalizedAgentId = text(agentId);
  return effectiveStandDowns(record).find((item) => {
    return text(item.source) === "operator"
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
    return `<button class="record-action" type="button" data-stand-down-agent-id="${escapeAttr(normalizedAgentId)}">Stand down</button>`;
  }
  const operatorRoot = operatorRootStandDown(normalizedAgentId, record);
  const summary = agentStandDownSummary({ effectiveStandDowns: standDowns });
  return `
    <span class="state-pill blocked" title="${escapeAttr(summary)}">stood down</span>
    ${
      operatorRoot
        ? `<button class="record-action" type="button" data-stand-down-revoke-agent-id="${escapeAttr(normalizedAgentId)}">Revoke</button>`
        : ""
    }
  `;
}

function agentStandDownSummary(record) {
  const standDowns = effectiveStandDowns(record);
  const primary = standDowns[standDowns.length - 1] || {};
  const source = text(primary.source) || "stand-down";
  const root = text(primary.root_agent_id || primary.agent_id);
  const reason = text(primary.reason);
  const inherited = Boolean(primary.inherited) && root ? ` inherited from ${root}` : "";
  return `${humanizeToken(source)} stand-down${inherited}${reason ? `: ${reason}` : ""}`;
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
      autoContextClearingEnabled: false,
      contextRotationPending: false,
      lastContextRotatedAt: null,
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
  const autoContextClearingEnabled = contextClearingPolicy !== "off";
  const lastContextRotatedAt = text(runtime.last_context_rotated_at);
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
  }
  if (lastContextRotatedAt) {
    parts.push(`last context ${lastContextRotatedAt}`);
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
    autoContextClearingEnabled,
    contextRotationPending,
    lastContextRotatedAt: lastContextRotatedAt || null,
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
    ? `${currentTask.title} (${humanizeToken(currentTask.state)})`
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
    const label = `${task.title} (${humanizeToken(task.state)})`;
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
    task.state === "waiting_for_review" ||
    task.dependency_status === "waiting" ||
    task.dependency_status === "blocked" ||
    Boolean((task.blocked_by_task_ids || []).length)
  );
}

function agentRuntimeAttentionRows(agentId) {
  return (state.runtime_operations?.attention || []).filter((row) => {
    return text(row.agent_id) === text(agentId);
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
    .sort(byId);
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
  const allProjects = values(state.projects).sort(byId);
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
              </div>
              <div class="record-actions">
                <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
                ${editButton("project", project.id)}
              </div>
            </div>
          <div class="project-columns">
            <div>
              <p class="subheading">Goals</p>
              ${renderGoals(project.goals || [])}
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
  if (!document.querySelector("[data-projects-focus-list]")) {
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
}

function renderProjectFocusListItem(project) {
  const active = project.id === selectedProjectFocusId;
  const team = state.teams[project.team_id];
  const reviewProfiles = projectSpecificReviewProfiles(project);
  return `
    <button
      type="button"
      class="projects-focus-row${active ? " active" : ""}"
      role="tab"
      data-project-focus-id="${escapeAttr(project.id)}"
      aria-selected="${active ? "true" : "false"}"
    >
      <span class="projects-focus-row-main">
        <strong>${escapeHtml(project.name)}</strong>
        <span>${escapeHtml(team ? `${team.name} (${team.id})` : project.team_id)}</span>
      </span>
      <span class="projects-focus-row-meta">
        <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
        <span>${reviewProfiles.length} profiles</span>
      </span>
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
    <div class="projects-detail-grid">
      <section>
        <h3>Goals</h3>
        ${renderGoals(project.goals || [], { compact: true })}
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
          <p class="section-label">Review profile</p>
          <h3>Project review profiles</h3>
        </div>
      </div>
      ${renderProjectReviewProfiles(projectProfiles, teamProfiles)}
      ${renderProjectReviewProfileCreatePanel(project)}
    </section>
  `;
}

function syncProjectCreatePanel({ reset = false } = {}) {
  const form = document.querySelector("[data-project-create-form]");
  const button = document.querySelector("[data-project-create-toggle]");
  if (form) {
    form.hidden = !projectCreateFormOpen;
    if (reset) {
      form.reset();
    }
    if (projectCreateFormOpen) {
      fillSelectsIn(form);
      decorateFieldTooltips(form);
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
      ${openProjectRecordSections.has(key) ? "open" : ""}
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
  return `${text(projectId)}:${text(section)}`;
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
    grouped.map(renderCaseTeamGroup).join(""),
  );
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
  const openCount = group.cases.filter((item) => text(item.status) !== "closed").length;
  return `
    <article class="project-block">
      <div class="project-header">
        <div>
          <strong>${escapeHtml(group.team?.name || group.teamId || "Unknown team")}</strong>
          <div class="meta-line">${escapeHtml(group.teamId || "team missing")} - ${openCount} open case${openCount === 1 ? "" : "s"}</div>
        </div>
      </div>
      <ul class="task-list">
        ${group.cases.map(renderCaseItem).join("")}
      </ul>
    </article>
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
    <li class="task-item">
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
    return task ? `${task.title} (${task.id}, ${humanizeToken(task.state)})` : taskId;
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
      <details class="team-wide-review-profiles">
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
    .sort(byId);
}

function teamWideReviewProfilesForProject(project) {
  return values(state.review_profiles)
    .filter(isVisibleReviewProfile)
    .filter((profile) => !profile.project_id && profile.team_id === project.team_id)
    .sort(byId);
}

function renderTaskBoard() {
  const visibleTeams = values(state.teams).filter(isVisibleTeam).sort(comparePeopleTeams);
  const selectedTeam = visibleTeams.find((team) => text(team.id) === text(selectedTaskBoardTeamId)) || null;
  if (selectedTaskBoardTeamId && !selectedTeam) {
    selectedTaskBoardTeamId = "";
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
  const lanes = taskBoardLanes.map((lane) => {
    const tasks = projectTasks
      .filter((task) => taskBoardLaneForTask(task) === lane.id)
      .sort(compareTaskBoardTasks);
    return { ...lane, tasks };
  });
  const boardMeta = [
    `${projectTasks.length} task${projectTasks.length === 1 ? "" : "s"}`,
    `${lanes.reduce((count, lane) => count + lane.tasks.filter(isTaskBoardBlocked).length, 0)} blocked`,
  ];
  renderIntoActiveTargets(
    "[data-task-board-view]",
    `
      <div class="task-board-context">
        <strong>${escapeHtml(selectedProject.name || selectedProject.id)}</strong>
        <span>${escapeHtml(selectedTeam.name || selectedTeam.id)} - ${escapeHtml(boardMeta.join(" - "))}</span>
      </div>
      <div class="task-board-lanes" aria-label="Task board lanes">
        ${lanes.map(renderTaskBoardLane).join("")}
      </div>
    `,
  );
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

function renderTaskBoardLane(lane) {
  return `
    <section class="task-board-lane task-board-lane-${escapeAttr(lane.id)}" aria-label="${escapeAttr(lane.label)}">
      <div class="task-board-lane-header">
        <h3>${escapeHtml(lane.label)}</h3>
        <span>${lane.tasks.length}</span>
      </div>
      ${
        lane.tasks.length
          ? `<ul class="task-board-card-list">${lane.tasks.map(renderTaskBoardCard).join("")}</ul>`
          : `<p class="empty-state task-board-empty">No tasks.</p>`
      }
    </section>
  `;
}

function renderTaskBoardCard(task) {
  const owner = state.agents[task.owner_agent_id] || null;
  const assignedTo = owner ? agentLabel(owner) : text(task.owner_agent_id);
  const blocked = isTaskBoardBlocked(task);
  const stateLabel = blocked ? "Blocked" : humanizeToken(task.state);
  const laneEntered = taskBoardLaneEnteredText(task);
  return `
    <li class="task-board-card${blocked ? " task-board-card-blocked" : ""}">
      <div class="task-board-card-title-row">
        <strong>${escapeHtml(task.title)}</strong>
        <span class="state-pill ${blocked ? "blocked" : escapeAttr(task.state)}">${escapeHtml(stateLabel)}</span>
      </div>
      <div class="task-board-card-meta">${escapeHtml(task.id)} - priority ${Number(task.priority ?? 0)}</div>
      <div class="task-board-card-meta">Assigned to: ${escapeHtml(assignedTo || "Unassigned")}</div>
      <div class="task-board-card-meta">Entered lane: ${escapeHtml(laneEntered)}</div>
      ${renderTaskBoardBlockerLine(task)}
    </li>
  `;
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

function isTaskBoardBlocked(task) {
  return (
    task.state === "blocked" ||
    task.dependency_status === "blocked" ||
    Boolean((task.blocked_by_task_ids || []).length)
  );
}

function renderTaskBoardBlockerLine(task) {
  const blockers = [];
  if ((task.blocked_by_task_ids || []).length) {
    blockers.push(`blocked by ${task.blocked_by_task_ids.join(", ")}`);
  }
  if (task.dependency_status === "blocked") {
    blockers.push("dependency blocked");
  }
  if (!blockers.length) {
    return "";
  }
  return `<div class="task-board-card-blockers">${escapeHtml(blockers.join(" - "))}</div>`;
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
    waiting_for_review: 1,
    in_progress: 2,
    pending: 3,
    finished: 4,
    accepted: 5,
    rejected: 6,
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
  if (waitingTasks.length) {
    detailBits.push(`${waitingTasks.length} waiting review`);
  }
  if (hiddenCount > 0) {
    detailBits.push(`${hiddenCount} hidden`);
  }
  return `
    <article class="project-block planning-project-card">
      <details>
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
            </div>
            <div class="record-actions">
              <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
              ${editButton("project", project.id)}
            </div>
          </div>
          <div class="project-columns planning-project-columns">
            <div>
              <p class="subheading">Goals</p>
              ${renderGoals(goals, { compact: true })}
              <p class="subheading">Deliverables</p>
              ${renderDeliverables(projectDeliverables, { compact: true })}
            </div>
            <div>
              <p class="subheading">Tasks</p>
              ${renderTasks(visibleTasks, { compact: true })}
            </div>
          </div>
        </div>
      </details>
    </article>
  `;
}

function renderProjectGit(git) {
  if (!git || !git.host_repo_path) {
    return "";
  }
  const bare = git.bare_repo_name || git.bare_repo_path || "";
  const parts = [`host ${git.host_repo_path}`];
  if (bare) parts.push(`bare ${bare}`);
  if (git.base_branch) parts.push(`base ${git.base_branch}`);
  if (git.provider) parts.push(`provider ${git.provider}`);
  return `<div class="meta-line">Git: ${escapeHtml(parts.join(" - "))}</div>`;
}

function renderGoals(goals, { compact = false } = {}) {
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
                <details>
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

function renderTasks(tasks, { compact = false } = {}) {
  if (!tasks.length) {
    return `<p class="empty-state">No tasks.</p>`;
  }
  if (compact) {
    return `
      <ul class="task-list compact-record-list">
        ${tasks
          .map((task) => {
            return `
              <li class="task-item compact-record-card">
                <details>
                  <summary class="compact-record-summary">
                    <span class="compact-record-main">
                      <strong>${escapeHtml(task.title)}</strong>
                      <span>${escapeHtml(task.id)} - owner ${escapeHtml(task.owner_agent_id)} - priority ${task.priority}</span>
                    </span>
                    <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(task.state)}</span>
                  </summary>
                  <div class="compact-record-body">
                    <div class="record-actions">
                      ${editButton("task", task.id)}
                    </div>
                    <div class="meta-line">${escapeHtml(task.id)} - priority ${task.priority} - owner ${escapeHtml(task.owner_agent_id)}</div>
                    ${renderTaskGraph(task)}
                    <div class="meta-line">${escapeHtml(task.description)}</div>
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
    <ul class="task-list">
      ${tasks
        .map((task) => {
          return `
              <li class="task-item">
                <div class="task-line">
                  <strong>${escapeHtml(task.title)}</strong>
                  <div class="record-actions">
                    <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(task.state)}</span>
                    ${editButton("task", task.id)}
                  </div>
                </div>
              <div class="meta-line">${escapeHtml(task.id)} - priority ${task.priority} - owner ${escapeHtml(task.owner_agent_id)}</div>
              ${renderTaskGraph(task)}
              <div class="meta-line">${escapeHtml(task.description)}</div>
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function renderTaskGraph(task) {
  const lines = [];
  if (task.case_id) {
    const item = state.cases[task.case_id];
    lines.push(`case ${item ? item.title : task.case_id}`);
  }
  if (task.parent_task_id) {
    lines.push(`parent ${task.parent_task_id}`);
  }
  if ((task.dependency_task_ids || []).length) {
    lines.push(`depends on ${task.dependency_task_ids.join(", ")}`);
  }
  if ((task.blocked_by_task_ids || []).length) {
    lines.push(`blocked by ${task.blocked_by_task_ids.join(", ")}`);
  }
  if ((task.child_task_ids || []).length) {
    lines.push(`children ${task.child_task_ids.join(", ")}`);
  }
  const counts = task.subtask_counts || {};
  const subtaskLine = counts.total
    ? `subtasks ${counts.accepted || 0} accepted, ${counts.finished || 0} finished, ${counts.open || 0} open, ${counts.blocked || 0} blocked`
    : "";
  const graphLines = lines.length
    ? `<div class="meta-line">${escapeHtml(lines.join(" - "))}</div>`
    : "";
  const countLine = subtaskLine ? `<div class="meta-line">${escapeHtml(subtaskLine)}</div>` : "";
  return `
    <div class="meta-line">
      Dependency status <span class="state-pill ${escapeHtml(task.dependency_status || "satisfied")}">${escapeHtml(task.dependency_status || "satisfied")}</span>
    </div>
    ${graphLines}${countLine}
  `;
}

function renderDeliverables(deliverables, { compact = false } = {}) {
  if (!deliverables.length) {
    return `<p class="empty-state">No deliverables.</p>`;
  }
  if (compact) {
    return `
      <ul class="deliverable-list compact-record-list">
        ${deliverables
          .map((deliverable) => {
            return `
              <li class="record-item compact-record-card">
                <details>
                  <summary class="compact-record-summary">
                    <span class="compact-record-main">
                      <strong>${escapeHtml(deliverable.summary)}</strong>
                      <span>${escapeHtml(deliverable.id)} - task ${escapeHtml(deliverable.task_id)}</span>
                    </span>
                    <span class="state-pill ${escapeHtml(deliverable.state)}">${escapeHtml(deliverable.state)}</span>
                  </summary>
                  <div class="compact-record-body">
                    <div class="meta-line">${escapeHtml(deliverable.id)} - task ${escapeHtml(deliverable.task_id)}</div>
                    ${renderReviewSummary(deliverable.review_request_ids || [])}
                    ${renderArtifactRefs(deliverable.artifact_refs || [])}
                    ${renderGitRefs(deliverable.git_refs)}
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
    <ul class="deliverable-list">
      ${deliverables
        .map((deliverable) => {
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(deliverable.summary)}</strong>
                <span class="state-pill ${escapeHtml(deliverable.state)}">${escapeHtml(deliverable.state)}</span>
              </div>
              <div class="meta-line">${escapeHtml(deliverable.id)} - task ${escapeHtml(deliverable.task_id)}</div>
              ${renderReviewSummary(deliverable.review_request_ids || [])}
              ${renderArtifactRefs(deliverable.artifact_refs || [])}
              ${renderGitRefs(deliverable.git_refs)}
            </li>
          `;
        })
        .join("")}
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
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(review.id)}</strong>
                <span class="state-pill ${escapeHtml(review.state)}">${escapeHtml(review.state)}</span>
              </div>
              <div class="meta-line">Reviewer id ${escapeHtml(review.reviewer_agent_id)}${review.review_profile_id ? ` - profile ${escapeHtml(review.review_profile_id)}${profile?.name ? ` (${escapeHtml(profile.name)})` : ""}` : ""}${review.decision_at ? ` - decided ${escapeHtml(review.decision_at)}` : ""}</div>
              ${review.instructions ? `<div class="meta-line">Instructions: ${escapeHtml(review.instructions)}</div>` : ""}
              ${(review.findings || []).length ? `<div class="meta-line">Findings: ${review.findings.map((finding) => escapeHtml(finding)).join("; ")}</div>` : ""}
              ${renderCriterionResults(review.criteria || [])}
              ${interactive ? renderReviewDecisionForm(review, profile) : ""}
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function renderReviewDecisionForm(review, profile) {
  if (terminalRecordStates.has(review.state)) {
    return "";
  }
  const selectedState = decisionStates.includes(review.state) ? review.state : "";
  const criteriaPlaceholder = renderReviewCriteriaPlaceholder(profile?.criteria || []);
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
      <label>
        Criteria results
        <textarea name="criteria" rows="3" placeholder="${escapeAttr(criteriaPlaceholder)}"></textarea>
      </label>
      ${criteriaHelp}
      <button type="submit">Submit decision</button>
    </form>
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
    return "criterion_id | passed | finding";
  }
  return criteria
    .map((criterion) => {
      const finding = criterion.description || criterion.label || "finding";
      return `${criterion.id} | passed | ${finding}`;
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
          return `<li>${escapeHtml(criterion.criterion_id)}: ${escapeHtml(criterion.state)}${finding}</li>`;
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
  const allProfiles = values(state.review_profiles).sort(byId);
  const profiles = allProfiles.filter(isVisibleReviewProfile);
  if (!allProfiles.length) {
    renderIntoActiveTargets("[data-review-profiles-view]", `<p class="empty-state">No review profiles.</p>`);
    return;
  }
  if (!profiles.length) {
    renderIntoActiveTargets("[data-review-profiles-view]", `<p class="empty-state">No visible review profiles.</p>`);
    return;
  }
  if (activeTeamView === "delivery") {
    renderIntoActiveTargets("[data-review-profiles-view]", `
      <ul class="review-list compact-record-list">
        ${profiles.map((profile) => renderCompactReviewProfile(profile)).join("")}
      </ul>
    `);
    return;
  }
  renderIntoActiveTargets("[data-review-profiles-view]", `
    <ul class="review-list">
      ${profiles
        .map((profile) => {
          const scope = profile.project_id ? `project ${profile.project_id}` : `team ${profile.team_id}`;
          const roles = (profile.applicable_role_ids || []).length
            ? `roles ${profile.applicable_role_ids.map(roleLabel).join(", ")}`
            : "all reviewer roles";
          return `
              <li class="record-item">
                <div class="record-line">
                  <strong>${escapeHtml(profile.name)}</strong>
                  <div class="record-actions">
                    <span class="state-pill ${profile.archived ? "blocked" : "accepted"}">${profile.archived ? "archived" : "active"}</span>
                    ${editButton("review-profile", profile.id)}
                  </div>
                </div>
              <div class="meta-line">${escapeHtml(profile.id)} - ${escapeHtml(scope)} - ${escapeHtml(roles)}</div>
              ${profile.description ? `<div class="meta-line">${escapeHtml(profile.description)}</div>` : ""}
              ${renderProfileCriteria(profile.criteria || [])}
            </li>
          `;
        })
        .join("")}
    </ul>
  `);
}

function renderCompactReviewProfile(profile) {
  const scope = profile.project_id ? `project ${profile.project_id}` : `team ${profile.team_id}`;
  const roles = (profile.applicable_role_ids || []).length
    ? `roles ${profile.applicable_role_ids.map(roleLabel).join(", ")}`
    : "all reviewer roles";
  const criteria = profile.criteria || [];
  return `
    <li class="record-item compact-record-card">
      <details>
        <summary class="compact-record-summary">
          <span class="compact-record-main">
            <strong>${escapeHtml(profile.name)}</strong>
            <span>${escapeHtml(scope)} - ${criteria.length} criteria</span>
          </span>
          <span class="state-pill ${profile.archived ? "blocked" : "accepted"}">${profile.archived ? "archived" : "active"}</span>
        </summary>
        <div class="compact-record-body">
          <div class="record-actions">
            ${editButton("review-profile", profile.id)}
          </div>
          <div class="meta-line">${escapeHtml(profile.id)} - ${escapeHtml(scope)} - ${escapeHtml(roles)}</div>
          ${profile.description ? `<div class="meta-line">${escapeHtml(profile.description)}</div>` : ""}
          ${renderProfileCriteria(criteria)}
        </div>
      </details>
    </li>
  `;
}

function renderProfileCriteria(criteria) {
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
  }
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

function renderReleases() {
  if (!document.querySelector("[data-releases-view]")) {
    return;
  }
  const releases = values(state.release_packages).sort(byId);
  if (!releases.length) {
    renderIntoActiveTargets("[data-releases-view]", `<p class="empty-state">No release packages.</p>`);
    return;
  }
  if (activeTeamView === "delivery") {
    renderIntoActiveTargets("[data-releases-view]", `
      <ul class="release-list compact-record-list">
        ${releases.map((release) => renderCompactRelease(release)).join("")}
      </ul>
    `);
    return;
  }
  renderIntoActiveTargets("[data-releases-view]", `
    <ul class="release-list">
      ${releases
        .map((release) => {
          const deliverables = (release.deliverable_ids || []).join(", ");
          const lines = [];
          if (release.release_branch) lines.push(`release ${release.release_branch}`);
          if (release.canonical_branch) lines.push(`canonical ${release.canonical_branch}`);
          if (release.published_commit_sha) lines.push(`commit ${release.published_commit_sha}`);
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(release.id)}</strong>
                <span class="state-pill ${escapeHtml(release.state)}">${escapeHtml(release.state)}</span>
              </div>
              <div class="meta-line">Project ${escapeHtml(release.project_id)}${deliverables ? ` - deliverables ${escapeHtml(deliverables)}` : ""}</div>
              ${lines.length ? `<div class="meta-line">${escapeHtml(lines.join(" - "))}</div>` : ""}
              ${release.published_at ? `<div class="meta-line">Published ${escapeHtml(release.published_at)}</div>` : ""}
              ${release.error ? `<div class="meta-line">Error: ${escapeHtml(release.error)}</div>` : ""}
            </li>
          `;
        })
        .join("")}
    </ul>
  `);
}

function renderCompactRelease(release) {
  const deliverables = (release.deliverable_ids || []).join(", ");
  const lines = [];
  if (release.release_branch) lines.push(`release ${release.release_branch}`);
  if (release.canonical_branch) lines.push(`canonical ${release.canonical_branch}`);
  if (release.published_commit_sha) lines.push(`commit ${release.published_commit_sha}`);
  return `
    <li class="record-item compact-record-card">
      <details>
        <summary class="compact-record-summary">
          <span class="compact-record-main">
            <strong>${escapeHtml(release.id)}</strong>
            <span>Project ${escapeHtml(release.project_id)}${deliverables ? ` - ${escapeHtml(deliverables)}` : ""}</span>
          </span>
          <span class="state-pill ${escapeHtml(release.state)}">${escapeHtml(release.state)}</span>
        </summary>
        <div class="compact-record-body">
          <div class="meta-line">Project ${escapeHtml(release.project_id)}${deliverables ? ` - deliverables ${escapeHtml(deliverables)}` : ""}</div>
          ${lines.length ? `<div class="meta-line">${escapeHtml(lines.join(" - "))}</div>` : ""}
          ${release.published_at ? `<div class="meta-line">Published ${escapeHtml(release.published_at)}</div>` : ""}
          ${release.error ? `<div class="meta-line">Error: ${escapeHtml(release.error)}</div>` : ""}
        </div>
      </details>
    </li>
  `;
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
                  <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(task.state)}</span>
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
  setTimelineStatus("Loading derived timeline.", "loading");

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
  setTimelineStatus("Loading derived timeline.", "loading");
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
    <div class="timeline-pagination" aria-label="Timeline pages">
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
      const reviewRequests = (reviewsByDeliverable.get(deliverable.id) || []).slice().sort(byId);
      const releasePackages = (releasePackagesByDeliverable.get(deliverable.id) || []).slice().sort(byId);
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
    ? `${escapeHtml(teamGroup.team.name)} (${escapeHtml(teamGroup.team.id)})`
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
    ? `${escapeHtml(project.name)} (${escapeHtml(project.id)})`
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
  return `
    <article class="delivery-project">
      <div class="delivery-project-title">
        <div>
          <p class="section-label">Project</p>
          <h4>${projectLabel}</h4>
          ${project ? renderProjectCostCounter(project.id) : ""}
          ${project ? `<div class="meta-line">Team ${escapeHtml(project.team_id)}</div>` : ""}
        </div>
        <span class="delivery-bucket-count">${projectCount} deliverable${projectCount === 1 ? "" : "s"}</span>
      </div>
      <div class="delivery-buckets">
        ${bucketMarkup}
      </div>
    </article>
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
    ? `${escapeHtml(project.name)} (${escapeHtml(project.id)})`
    : "Unknown project";
  const teamLabel = team ? `${escapeHtml(team.name)} (${escapeHtml(team.id)})` : "Unknown team";
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
          <strong>${escapeHtml(deliverable.id)}</strong>
          <span class="state-pill ${escapeAttr(row.effectiveStatus)}">${escapeHtml(row.effectiveStatus)}</span>
        </div>
        <span class="delivery-bucket-count">${escapeHtml(teamLabel)} - ${escapeHtml(projectLabel)}</span>
      </div>
      <div class="delivery-row-body">
        <div class="delivery-detail-grid">
          <div>
            <div class="delivery-detail-label">Deliverable</div>
            <div class="delivery-detail-value">${escapeHtml(deliverable.id)}</div>
          </div>
          <div>
            <div class="delivery-detail-label">Task</div>
            <div class="delivery-detail-value">
              ${task ? `${escapeHtml(task.id)} - ${escapeHtml(task.title)} - ${escapeHtml(task.state)}` : "missing"}
            </div>
          </div>
          <div>
            <div class="delivery-detail-label">Producer</div>
            <div class="delivery-detail-value">${escapeHtml(deliverable.producer_agent_id)}</div>
          </div>
          <div>
            <div class="delivery-detail-label">Deliverable state</div>
            <div class="delivery-detail-value">
              <span class="state-pill ${escapeAttr(deliverable.state)}">${escapeHtml(deliverable.state)}</span>
            </div>
          </div>
        </div>
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
    <details class="delivery-row delivery-row-compact" data-deliverable-id="${escapeAttr(deliverable.id)}" data-status="${escapeAttr(row.effectiveStatus)}">
      <summary class="delivery-row-summary">
        <span class="delivery-summary-main">
          <strong>${escapeHtml(deliverable.summary || deliverable.id)}</strong>
          <span>${escapeHtml(deliverable.id)}${task ? ` - ${escapeHtml(task.title)}` : ""}</span>
        </span>
        <span class="delivery-summary-meta">
          <span class="state-pill ${escapeAttr(row.effectiveStatus)}">${escapeHtml(row.effectiveStatus)}</span>
          <span>${escapeHtml(reviewText)}</span>
          <span>${escapeHtml(releaseText)}</span>
        </span>
      </summary>
      <div class="delivery-row-body">
        <div class="meta-line">${escapeHtml(scope)}</div>
        <div class="delivery-detail-grid">
          <div>
            <div class="delivery-detail-label">Deliverable</div>
            <div class="delivery-detail-value">${escapeHtml(deliverable.id)}</div>
          </div>
          <div>
            <div class="delivery-detail-label">Task</div>
            <div class="delivery-detail-value">
              ${task ? `${escapeHtml(task.id)} - ${escapeHtml(task.title)} - ${escapeHtml(task.state)}` : "missing"}
            </div>
          </div>
          <div>
            <div class="delivery-detail-label">Producer</div>
            <div class="delivery-detail-value">${escapeHtml(deliverable.producer_agent_id)}</div>
          </div>
          <div>
            <div class="delivery-detail-label">Deliverable state</div>
            <div class="delivery-detail-value">
              <span class="state-pill ${escapeAttr(deliverable.state)}">${escapeHtml(deliverable.state)}</span>
            </div>
          </div>
        </div>
        ${renderDeliveryRowDetailBody(row, { reviewIds, releaseSummaries, gitPromotionIds })}
      </div>
    </details>
  `;
}

function renderDeliveryRowDetailBody(row, { reviewIds, releaseSummaries, gitPromotionIds }) {
  const deliverable = row.deliverable;
  return `
    <div class="delivery-row-meta">${escapeHtml(deliverable.summary)}</div>
    ${renderArtifactRefs(deliverable.artifact_refs || [])}
    ${renderGitRefs(deliverable.git_refs, gitPromotionIds)}
    ${
      reviewIds.length
        ? `<div class="delivery-row-reviews"><div class="meta-line">Reviews: ${escapeHtml(reviewIds.join(", "))}</div>${renderReviewSummary(reviewIds, { interactive: true })}</div>`
        : ""
    }
    ${releaseSummaries}
  `;
}

function renderReleaseSummary(releasePackages) {
  if (!releasePackages.length) {
    return "";
  }
  return `
    <div class="delivery-row-releases">
      <div class="meta-line">Release packages:</div>
      <ul class="release-list">
        ${releasePackages
          .map((release) => {
            const deliverables = (release.deliverable_ids || []).join(", ");
            const promotionIds = uniqueTextList(release.git_promotion_record_ids || []);
            const promotionText = promotionIds.length ? promotionIds.join(", ") : "";
            const artifactText = (release.artifact_refs || []).length
              ? release.artifact_refs.map((ref) => escapeHtml(ref)).join(", ")
              : "";
            return `
              <li class="record-item">
                <div class="record-line">
                  <strong>${escapeHtml(release.id)}</strong>
                  <span class="state-pill ${escapeAttr(release.state)}">${escapeHtml(release.state)}</span>
                </div>
                <div class="meta-line">Project ${escapeHtml(release.project_id)}${deliverables ? ` - deliverables ${escapeHtml(deliverables)}` : ""}</div>
                ${release.release_branch ? `<div class="meta-line">Release ${escapeHtml(release.release_branch)}</div>` : ""}
                ${release.canonical_branch ? `<div class="meta-line">Canonical ${escapeHtml(release.canonical_branch)}</div>` : ""}
                ${release.published_commit_sha ? `<div class="meta-line">Commit ${escapeHtml(release.published_commit_sha)}</div>` : ""}
                ${promotionText ? `<div class="meta-line">Git promotions: ${escapeHtml(promotionText)}</div>` : ""}
                ${artifactText ? `<div class="meta-line">Artifact refs: ${artifactText}</div>` : ""}
                ${release.published_at ? `<div class="meta-line">Published ${escapeHtml(release.published_at)}</div>` : ""}
                ${release.error ? `<div class="meta-line">Error: ${escapeHtml(release.error)}</div>` : ""}
              </li>
            `;
          })
          .join("")}
      </ul>
    </div>
  `;
}

function deriveDeliveryStatus(deliverable, task, reviews, releasePackages, gitPromotions) {
  if (deliverable.state === "blocked") {
    return "blocked";
  }
  if (task && (task.state === "blocked" || task.state === "rejected")) {
    return "blocked";
  }
  if (reviews.some((review) => review.state === "blocked")) {
    return "blocked";
  }
  if (releasePackages.some((release) => release.state === "blocked" || release.state === "rejected")) {
    return "blocked";
  }
  if (gitPromotions.some((promotion) => promotion.state === "failed")) {
    return "blocked";
  }
  if (deliverable.state === "rejected" || reviews.some((review) => review.state === "rejected")) {
    return "rejected";
  }
  const reviewsPending = reviews.some((review) => review.state === "pending");
  if (deliverable.state === "accepted" && reviews.every((review) => review.state === "accepted")) {
    return "accepted";
  }
  if ((task && task.state === "waiting_for_review") || reviewsPending) {
    return "waiting_for_review";
  }
  return "pending";
}

function renderTimelineEntry(entry) {
  const timestamp = entry.timestamp ? formatTimelineTimestamp(entry.timestamp) : "Timestamp unavailable";
  const actorSubject = text(entry.actor_subject || entry.actor_agent_id);
  const actorLine = [
    actorSubject ? `Actor ${actorSubject}` : "Actor unknown",
    entry.team_id ? `team ${entry.team_id}` : "",
    entry.project_id ? `project ${entry.project_id}` : "",
  ]
    .filter(Boolean)
    .join(" - ");
  const relatedAgents = [
    entry.actor_agent_id ? `actor agent ${entry.actor_agent_id}` : actorSubject ? `actor ${actorSubject}` : null,
    (entry.related_agent_ids || []).length ? `related agents ${entry.related_agent_ids.join(", ")}` : null,
  ]
    .filter(Boolean)
    .join(" - ");
  const relatedIds = renderTimelineRelatedIds(entry);
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
          <span class="state-pill ${escapeHtml(entry.status || "pending")}">${escapeHtml(entry.status || "pending")}</span>
        </div>
        <div class="timeline-meta">
          <div class="meta-line">${escapeHtml(actorLine)}</div>
          ${relatedAgents ? `<div class="meta-line">${escapeHtml(relatedAgents)}</div>` : ""}
        </div>
        <p class="timeline-summary-text">${escapeHtml(entry.summary)}</p>
        ${relatedIds ? `<div class="timeline-related">${relatedIds}</div>` : ""}
      </article>
    </li>
  `;
}

function renderCompactTimelineEntry(entry) {
  const timestamp = entry.timestamp ? formatTimelineTimestamp(entry.timestamp) : "Timestamp unavailable";
  const actorSubject = text(entry.actor_subject || entry.actor_agent_id);
  const relatedIds = renderTimelineRelatedIds(entry);
  const relatedAgents = [
    entry.actor_agent_id ? `actor agent ${entry.actor_agent_id}` : actorSubject ? `actor ${actorSubject}` : null,
    (entry.related_agent_ids || []).length ? `related agents ${entry.related_agent_ids.join(", ")}` : null,
  ]
    .filter(Boolean)
    .join(" - ");
  const scope = [
    entry.team_id ? `team ${entry.team_id}` : "",
    entry.project_id ? `project ${entry.project_id}` : "",
  ]
    .filter(Boolean)
    .join(" - ");
  return `
    <li class="timeline-entry timeline-entry-compact">
      <details>
        <summary class="timeline-entry-summary">
          <span class="timeline-summary-main">
            <span class="timeline-timestamp">${escapeHtml(timestamp)}</span>
            <span class="timeline-summary-text">${escapeHtml(entry.summary)}</span>
          </span>
          <span class="timeline-summary-pills">
            <span class="state-pill timeline-pill">${escapeHtml(humanizeToken(entry.type))}</span>
            <span class="state-pill ${escapeHtml(entry.status || "pending")}">${escapeHtml(entry.status || "pending")}</span>
          </span>
        </summary>
        <div class="timeline-entry-details">
          <div class="timeline-pills">
            <span class="state-pill timeline-pill">${escapeHtml(humanizeToken(entry.category))}</span>
            ${entry.attention_needed ? `<span class="state-pill blocked">attention needed</span>` : ""}
          </div>
          <div class="timeline-meta">
            <div class="meta-line">${escapeHtml(actorSubject ? `Actor ${actorSubject}` : "Actor unknown")}</div>
            ${scope ? `<div class="meta-line">${escapeHtml(scope)}</div>` : ""}
            ${relatedAgents ? `<div class="meta-line">${escapeHtml(relatedAgents)}</div>` : ""}
          </div>
          ${relatedIds ? `<div class="timeline-related">${relatedIds}</div>` : ""}
        </div>
      </details>
    </li>
  `;
}

function renderTimelineRelatedIds(entry) {
  const parts = [];
  const idLabels = [
    ["task_id", "Task"],
    ["deliverable_id", "Deliverable"],
    ["review_request_id", "Review"],
    ["inbox_item_id", "Inbox"],
    ["action_record_id", "Action"],
    ["ephemeral_assignment_id", "Ephemeral"],
    ["git_promotion_id", "Git promotion"],
    ["release_package_id", "Release"],
  ];
  idLabels.forEach(([key, label]) => {
    if (entry[key]) {
      parts.push(`${label} ${entry[key]}`);
    }
  });
  return parts.length ? `Related IDs: ${parts.map((part) => escapeHtml(part)).join(" · ")}` : "";
}

function describeTimelineStatus(timeline) {
  const filters = timeline.filters || {};
  const filterParts = [];
  if (filters.team_id) {
    filterParts.push(`team ${filters.team_id}`);
  }
  if (filters.project_id) {
    filterParts.push(`project ${filters.project_id}`);
  }
  if (filters.agent_id) {
    filterParts.push(`agent ${filters.agent_id}`);
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
  return `Derived timeline loaded${filterSuffix}.`;
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
  const teams = values(state.teams).filter(isVisibleTeam).sort(byId).map((team) => [team.id, `${team.name} (${team.id})`]);
  const governanceGroups = values(state.governance_groups).filter(isVisibleGovernanceGroup).sort(byId).map((group) => [group.id, `${group.name} (${group.id})`]);
  const projects = values(state.projects).filter(isVisibleProject).sort(byId).map((project) => [project.id, `${project.name} (${project.id})`]);
  const roles = values(state.roles).filter(isVisibleRole).sort(byId).map((role) => [role.id, `${role.name} (${role.id})`]);
  const agents = values(state.agents).filter(isVisibleAgent).sort(byId).map((agent) => [agent.id, `${agent.id} - ${agentRoleLabel(agent)}`]);
  const agentsByDisplayName = values(state.agents)
    .filter(isVisibleAgent)
    .map((agent) => [agent.id, text(agent.display_name) || text(agent.id)])
    .sort((left, right) => text(left[1]).localeCompare(text(right[1])) || text(left[0]).localeCompare(text(right[0])));
  const tasks = values(state.tasks).sort(byPriority).map((task) => [task.id, `${task.title} (${task.id})`]);
  const cases = values(state.cases).filter(isVisibleCase).sort(compareCases).map((item) => [item.id, caseLabel(item)]);
  const deliverables = values(state.deliverables).sort(byId).map((deliverable) => [deliverable.id, `${deliverable.summary} (${deliverable.id})`]);
  const reviewRequests = values(state.review_requests)
    .sort(byId)
    .map((review) => [review.id, `${review.id} - ${review.state}`]);
  const reviewProfiles = values(state.review_profiles)
    .filter(isVisibleReviewProfile)
    .sort(byId)
    .map((profile) => [profile.id, `${profile.name} (${profile.id})`]);
  const releasePackages = values(state.release_packages)
    .sort(byId)
    .map((release) => [release.id, `${release.id} - ${release.state}`]);
  const timelineOptions = buildTimelineSelectOptions();
  return {
    teams,
    projects,
    roles,
    "project-review-roles": projectReviewRoleOptionsForProject(selectedProjectFocusId),
    agents,
    "agents-by-display-name": agentsByDisplayName,
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
    "record-states": recordStates.map((item) => [item, item]),
    "agent-statuses": agentStatuses.map((item) => [item, item]),
    "context-clearing-policies": contextClearingPolicies,
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
  const teamId = text(select.form?.elements?.team_id?.value);
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
    .sort(byId)
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
  payload.dependency_task_ids = splitList(data.dependency_task_ids);
  await submitMutation(form, `/tasks/${encodeURIComponent(taskId)}`, "PATCH", payload, {
    resetOnSuccess: false,
  });
}

async function importGitRepoForForm(form, button) {
  if (!guardTeamMutation("import Git repositories")) {
    return false;
  }
  const data = formData(form);
  const repoSource = text(data.host_repo_path) || text(data.canonical_origin_url);
  if (!repoSource) {
    setStatus("Host repo path or canonical origin URL is required before import.", "error");
    return false;
  }
  const bareRepo = text(data.bare_repo || data.bare_repo_name);
  if (bareRepo && bareRepo.startsWith("/")) {
    setStatus("Import creates a control-plane bare repo name; use a name, not an absolute path.", "error");
    return false;
  }
  const payload = { repo_source: repoSource };
  if (bareRepo) {
    payload.bare_repo_name = bareRepo;
  }
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
    setGitImportStatus(statusTarget, imported.bare_repo_name || "Imported.");
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
    host_repo_path: git.host_repo_path,
    bare_repo: git.bare_repo_name || git.bare_repo_path,
    base_branch: git.base_branch,
    canonical_origin_url: git.canonical_origin_url,
    provider: git.provider,
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

async function requestAgentStandDown(button, agentId) {
  if (!guardTeamMutation("stand down agents")) {
    return;
  }
  const normalizedAgentId = text(agentId);
  if (!normalizedAgentId) {
    setStatus("No agent selected.", "error");
    return;
  }
  const reason = text(window.prompt(`Reason to stand down ${normalizedAgentId}`));
  if (!reason) {
    setStatus("Stand-down reason is required.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus("Standing down agent subtree.", "loading");
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
    return payload;
  }
  if (formId === "create-governance-group-form") {
    const payload = {
      name: text(data.name),
      manager_agent_id: text(data.manager_agent_id),
      member_team_ids: splitList(data.member_team_ids),
      goals: parseGovernanceGoals(data.goals),
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
      goals,
    };
    const git = gitConfigForForm(data);
    if (git) {
      payload.git = git;
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
    const caseId = text(data.case_id);
    if (caseId) {
      payload.case_id = caseId;
    }
    if (text(data.state)) {
      payload.state = text(data.state);
    }
    const parentTaskId = text(data.parent_task_id);
    if (parentTaskId) {
      payload.parent_task_id = parentTaskId;
    }
    payload.dependency_task_ids = splitList(data.dependency_task_ids);
    return payload;
  }
  if (formId === "create-review-profile-form") {
    const payload = {
      team_id: text(data.team_id),
      name: text(data.name),
      description: text(data.description),
      applicable_role_ids: splitList(data.applicable_role_ids),
      criteria: reviewProfileCriteriaFromForm(form),
    };
    const projectId = text(data.project_id);
    if (projectId) {
      payload.project_id = projectId;
    }
    return payload;
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
  const hostRepoPath = text(data.host_repo_path);
  if (!hostRepoPath) {
    return null;
  }
  const git = { host_repo_path: hostRepoPath };
  const bareRepo = text(data.bare_repo);
  if (bareRepo) {
    if (bareRepo.startsWith("/")) {
      git.bare_repo_path = bareRepo;
    } else {
      git.bare_repo_name = bareRepo;
    }
  }
  const optionalFields = [
    "bare_repo_name",
    "bare_repo_path",
    "base_branch",
    "canonical_origin_url",
    "provider",
    "canonical_release_branch_prefix",
  ];
  optionalFields.forEach((field) => {
    const value = text(data[field]);
    if (value) {
      git[field] = value;
    }
  });
  return git;
}

function gitToBareRepo(git) {
  if (!git) {
    return "";
  }
  return text(git.bare_repo_path || git.bare_repo_name);
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
    criteria: parseCriterionResults(data.criteria),
  };
  await submitMutation(
    form,
    `/review-requests/${encodeURIComponent(reviewId)}/decision`,
    "POST",
    payload,
    { resetOnSuccess: false },
    );
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
  const payload = editPayloadForForm(type, form);
  const saved = await submitMutation(form, endpoint, "PATCH", payload, {
    resetOnSuccess: false,
  });
  if (saved) {
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
  };
  return `Edit ${labels[type] || "record"} ${record.id || ""}`.trim();
}

function renderEditFields(type, record) {
  if (type === "team") {
    return `
      ${readonlyField("Team ID", record.id)}
      ${textField("Name", "name", record.name, { required: true })}
      ${selectField("Manager", "manager_agent_id", "agents", record.manager_agent_id || "", { required: true })}
      ${textField("Operator communication target id", "operator_communication_target_id", record.operator_communication_target_id || "")}
      ${checkboxField("Escalate manager escalations to governance", "escalate_to_governance", Boolean(record.escalate_to_governance))}
      ${numberField("Estimated max cost USD", "max_cost_usd", record.max_cost_usd ?? "", { min: 0, step: "0.01" })}
      ${checkboxField("Archived", "archived", Boolean(record.archived))}
    `;
  }
  if (type === "governance-group") {
    return `
      ${readonlyField("Governance group ID", record.id)}
      ${textField("Name", "name", record.name, { required: true })}
      ${selectField("Manager", "manager_agent_id", "agents", record.manager_agent_id || "", { required: true })}
      ${multiSelectField("Member teams", "member_team_ids", "teams", record.member_team_ids || [], { size: 5 })}
      ${textareaField("Goals", "goals", governanceGoalsToText(record.goals || []), {
        rows: 4,
        placeholder: "id | description",
      })}
      ${numberField("Estimated max cost USD", "max_cost_usd", record.max_cost_usd ?? "", { min: 0, step: "0.01" })}
      ${checkboxField("Archived", "archived", Boolean(record.archived))}
    `;
  }
  if (type === "project") {
    return `
      ${readonlyField("Project ID", record.id)}
      ${readonlyField("Team", record.team_id)}
      ${textField("Name", "name", record.name, { required: true })}
      ${selectField("Status", "status", "project-statuses", record.status || "active", { required: true })}
      ${textareaField("Goals", "goals", goalsToText(record.goals || []), {
        rows: 4,
        placeholder: "id | description | state",
      })}
      <p class="subheading">Git config</p>
      ${textField("Host repo path", "host_repo_path", record.git?.host_repo_path || "")}
      ${textField("Bare repo", "bare_repo", gitToBareRepo(record.git), {
        placeholder: "repo.git or /srv/git/repo.git",
      })}
      ${gitImportControls()}
      ${textField("Base branch", "base_branch", record.git?.base_branch || "main")}
      ${textField("Canonical origin URL", "canonical_origin_url", record.git?.canonical_origin_url || "")}
      ${selectField("Provider", "provider", "git-providers", record.git?.provider || "", { placeholder: "Default" })}
      ${textField("Canonical release branch prefix", "canonical_release_branch_prefix", record.git?.canonical_release_branch_prefix || "")}
    `;
  }
  if (type === "agent") {
    const runtime = state.agent_runtimes?.[record.id] || {};
    const contextPolicy = contextClearingPolicyValue(runtime);
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
    return `
      ${readonlyField("Task ID", record.id)}
      ${readonlyField("Project", record.project_id)}
      ${readonlyField("Owner", record.owner_agent_id)}
      ${selectField("State", "state", "task-states", record.state || "pending", { required: true })}
      ${selectField("Case", "case_id", "cases", record.case_id || "", { placeholder: "No case" })}
      ${numberField("Priority", "priority", record.priority ?? 0, { min: 0, required: true })}
      ${textField("Title", "title", record.title, { required: true })}
      ${textareaField("Description", "description", record.description, { rows: 3, required: true })}
      ${selectField("Parent task", "parent_task_id", "tasks", record.parent_task_id || "", { placeholder: "No parent" })}
      ${textareaField("Dependency tasks", "dependency_task_ids", (record.dependency_task_ids || []).join("\n"), { rows: 2 })}
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
      ${renderReviewProfileCriteriaEditor(record.criteria || [])}
      ${checkboxField("Archived", "archived", Boolean(record.archived))}
    `;
  }
  return "";
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
      archived: Boolean(form.elements.archived?.checked),
    };
  }
  if (type === "governance-group") {
    return {
      name: text(data.name),
      manager_agent_id: text(data.manager_agent_id),
      member_team_ids: splitList(data.member_team_ids),
      goals: parseGovernanceGoals(data.goals),
      max_cost_usd: numberOrNull(data.max_cost_usd),
      archived: Boolean(form.elements.archived?.checked),
    };
  }
  if (type === "project") {
    return {
      name: text(data.name),
      status: text(data.status),
      goals: parseGoals(data.goals),
      git: gitConfigForForm(data),
    };
  }
  if (type === "agent") {
    const selectedProjectId = text(data.project_id);
    const currentRecord = recordForEdit("agent", text(form.dataset.editId));
    const currentProjectId = currentRecord ? agentEditProjectId(currentRecord) : "";
    const requestedTeamId = text(data.team_id);
    const normalizedTeamId = requestedTeamId === noTeamRecordId ? null : requestedTeamId || null;
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
      context_clearing_policy: text(data.context_clearing_policy) || "off",
    };
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
    return {
      state: text(data.state),
      case_id: text(data.case_id) || null,
      priority: numberOrZero(data.priority),
      title: text(data.title),
      description: text(data.description),
      parent_task_id: text(data.parent_task_id) || null,
      dependency_task_ids: splitList(data.dependency_task_ids),
    };
  }
  if (type === "review-profile") {
    return {
      project_id: text(data.project_id) || null,
      name: text(data.name),
      description: text(data.description),
      applicable_role_ids: splitList(data.applicable_role_ids),
      criteria: reviewProfileCriteriaFromForm(form),
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

function numberField(label, name, value, { min = null, step = null, required = false } = {}) {
  return `<label>${escapeHtml(label)}<input name="${escapeAttr(name)}" type="number" value="${escapeAttr(value)}"${min !== null ? ` min="${escapeAttr(min)}"` : ""}${step !== null ? ` step="${escapeAttr(step)}"` : ""}${required ? " required" : ""} /></label>`;
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
  return Boolean(form && formIdentifier(form) && !form.matches("[data-chat-message-form]"));
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
  return values;
}

function findFormByDraftKey(key) {
  return Array.from(document.querySelectorAll("form")).find((form) => formDraftKey(form) === key) || null;
}

function captureFormDrafts() {
  const drafts = Object.fromEntries(formDraftRegistry.entries());
  document.querySelectorAll("form").forEach((form) => {
    const remembered = rememberFormDraft(form);
    if (remembered) {
      drafts[remembered[0]] = remembered[1];
    }
  });
  return drafts;
}

function restoreFormDrafts(drafts) {
  Object.entries(drafts || {}).forEach(([formId, values]) => {
    const form = findFormByDraftKey(formId) || document.getElementById(formId);
    if (!form) {
      return;
    }
    restoreReviewProfileCriteriaDraft(form, values.__review_profile_criteria);
    if (formSemanticIdentifier(form) === "setup-template-form") {
      const templateField = form.elements.template_id;
      if (templateField && values.template_id) {
        templateField.value = text(values.template_id);
      }
      renderTemplateSetupFields({ preserveValues: false });
    }
    Object.entries(values).forEach(([name, value]) => {
      if (name === "__review_profile_criteria" || name.startsWith("criterion_")) {
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
    rememberFormDraft(form);
  });
  const updateTaskForm = document.querySelector("#update-task-form");
  if (updateTaskForm) {
    syncTaskUpdateForm(updateTaskForm, { keepEditedValues: true });
  }
  renderTemplatePreview();
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
    .sort(byId)
    .forEach((team) => {
      teamLabels.set(team.id, `${team.name} (${team.id})`);
    });
  values(state.projects)
    .sort(byId)
    .forEach((project) => {
      projectLabels.set(project.id, `${project.name} (${project.id})`);
    });
  values(state.agents)
    .sort(byId)
    .forEach((agent) => {
      agentLabels.set(agent.id, `${agent.id} - ${agentRoleLabel(agent)}`);
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

function parseGovernanceGoals(value) {
  return parseGoals(value);
}

function goalsToText(goals) {
  return (goals || [])
    .map((goal) => [goal.id, goal.description, goal.state || "pending"].join(" | "))
    .join("\n");
}

function governanceGoalsToText(goals) {
  return (goals || [])
    .map((goal) => {
      const state = text(goal.state);
      return state && state !== "pending"
        ? [goal.id, goal.description, state].join(" | ")
        : [goal.id, goal.description].join(" | ");
    })
    .join("\n");
}

function parseCriterionResults(value) {
  return text(value)
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [criterion_id, stateValue = "passed", finding = ""] = line
        .split("|")
        .map((part) => part.trim());
      return {
        criterion_id,
        state: stateValue || "passed",
        finding,
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
