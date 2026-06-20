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
const recordStates = ["pending", "in_progress", "accepted", "rejected", "blocked"];
const agentStatuses = ["active", "paused", "archived"];
const avatarStylePresets = [
  ["professional", "Professional"],
  ["playful", "Playful"],
  ["minimal", "Minimal"],
];
const projectStatuses = ["active", "blocked", "completed", "archived"];
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

let state = emptyState();
let costState = emptyCostState();
let runtimeFilter = null;
let runtimeFilterTouched = false;
let templates = [];
let timelineState = emptyTimelineState();
let timelineLoading = false;
let timelineRequestId = 0;
let timelinePage = 1;
let costsRequestId = 0;
let showArchived = false;
let activeTeamView = "workspace";
let selectedPlanningTaskStates = new Set(defaultPlanningTaskStates);
let selectedOrgTeamId = "all";
let selectedPeopleAgentId = null;
let activeAgentVoiceCall = null;

const teamViewIds = new Set([
  "workspace",
  "overview",
  "operations",
  "delivery",
  "people",
  "planning",
  "setup",
  "timeline",
  "org",
]);
const runtimeFilterChoices = [
  ["all", "All"],
  ["needs_attention", "Needs attention"],
  ["due_leased_agents", "Due / leased agents"],
  ["blocked_waiting_work", "Blocked / waiting work"],
  ["ephemeral_work", "Ephemeral work"],
];
const blockedWorkKinds = new Set([
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

const elements = {
  revisionBadge: document.querySelector("#revision-badge"),
  statusMessage: document.querySelector("#status-message"),
  summaryGrid: document.querySelector("#summary-grid"),
  runtimeOperationsView: document.querySelector("#runtime-operations-view"),
  runtimeFilterButtons: Array.from(document.querySelectorAll("[data-runtime-filter]")),
  reviewDeliveryView: document.querySelector("#review-delivery-view"),
  hierarchyView: document.querySelector("#hierarchy-view"),
  agentsView: document.querySelector("#agents-view"),
  rolesView: document.querySelector("#roles-view"),
  projectsView: document.querySelector("#projects-view"),
  reviewProfilesView: document.querySelector("#review-profiles-view"),
  releasesView: document.querySelector("#releases-view"),
  templateSelect: document.querySelector("#template-select"),
  templatePreview: document.querySelector("#template-preview"),
  timelineSummary: document.querySelector("#timeline-summary"),
  timelineStatus: document.querySelector("#timeline-status"),
  timelineView: document.querySelector("#timeline-view"),
  timelineTeamFilter: document.querySelector("#timeline-team-filter"),
  timelineProjectFilter: document.querySelector("#timeline-project-filter"),
  timelineAgentFilter: document.querySelector("#timeline-agent-filter"),
  timelineTypeFilter: document.querySelector("#timeline-type-filter"),
  timelineStatusFilter: document.querySelector("#timeline-status-filter"),
  timelineAttentionFilter: document.querySelector("#timeline-attention-filter"),
  showArchivedToggle: document.querySelector("#show-archived-toggle"),
  refreshButton: document.querySelector("#refresh-button"),
  teamViewButtons: Array.from(document.querySelectorAll("[data-team-view]")),
  teamViewPanels: Array.from(document.querySelectorAll("[data-team-view-panel]")),
  orgFocusSummary: document.querySelector("#org-focus-summary"),
  orgFocusTeamTabs: document.querySelector("#org-focus-team-tabs"),
  orgFocusView: document.querySelector("#org-focus-view"),
  setupFormsPanel: document.querySelector("#setup-forms-panel"),
  workspaceSetupAnchor: document.querySelector("#workspace-setup-anchor"),
  focusedSetupMount: document.querySelector("#focused-setup-mount"),
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

const fieldTooltips = {
  "setup-template-form:template_id": "Template blueprint used to create the team, project, roles, starter tasks, and review profile.",
  "setup-template-form:team_name": "Human-readable name shown in hierarchy, filters, and timeline rows.",
  "setup-template-form:manager_existing_agent_id": "Copy session defaults from an existing agent for the new manager.",
  "setup-template-form:manager_session_id": "Session ID used when the manager agent is woken or resumed.",
  "setup-template-form:operator_communication_target_id": "Optional external target for manager escalations, such as a chat channel or operator contact.",
  "setup-template-form:max_cost_usd": "Optional estimated lifetime USD cap for this team. Team wakes stop when estimated accrued cost reaches the cap.",
  "setup-template-form:project_name": "Human-readable project name shown in project cards and filters.",
  "setup-template-form:project_lead_existing_agent_id": "Copy defaults from an existing project lead while creating a distinct new project-lead agent.",
  "setup-template-form:project_lead_session_id": "Session ID used when the project lead agent is woken or resumed.",
  "setup-template-form:implementer_existing_agent_id": "Copy defaults from an existing implementer for the template-created implementer.",
  "setup-template-form:implementer_session_id": "Session ID used when the implementer agent is woken or resumed.",
  "setup-template-form:reviewer_existing_agent_id": "Copy defaults from an existing reviewer for the template-created reviewer.",
  "setup-template-form:reviewer_session_id": "Session ID used when the reviewer agent is woken or resumed.",
  "setup-template-form:release_operator_existing_agent_id": "Copy defaults from an existing release operator for the release handoff role.",
  "setup-template-form:release_operator_session_id": "Session ID used when the release operator agent is woken or resumed.",

  "create-team-form:name": "Human-readable team name shown throughout the Team Add-on UI.",
  "create-team-form:manager_existing_agent_id": "Copy session and display defaults from an existing agent without reusing that agent.",
  "create-team-form:manager_session_id": "Session ID used to resume or wake the manager agent.",
  "create-team-form:manager_display_name": "Optional display name. If blank, the generated agent ID is shown.",
  "create-team-form:manager_role_name": "Human-readable role name for the manager role.",
  "create-team-form:operator_communication_target_id": "Optional external target for escalations raised by this team's manager.",
  "create-team-form:max_cost_usd": "Optional estimated lifetime USD cap for this team. Team wakes stop when estimated accrued cost reaches the cap.",

  "create-project-form:team_id": "Team that owns this project. The project lead and later project agents must belong to the same team.",
  "create-project-form:name": "Human-readable project name shown in project cards, filters, and timelines.",
  "create-project-form:lead_existing_agent_id": "Copy session and display defaults from an existing agent while creating a distinct new lead agent.",
  "create-project-form:lead_session_id": "Session ID used to resume or wake the project lead agent.",
  "create-project-form:lead_display_name": "Optional display name. If blank, the generated lead agent ID is shown.",
  "create-project-form:lead_role_name": "Human-readable role name for the project lead role.",
  "create-project-form:goal_description": "Optional initial goal for the project backlog.",

  "register-agent-form:existing_agent_id": "Copy defaults from an existing agent. This helps reuse session conventions for the new agent.",
  "register-agent-form:team_id": "Team this persistent agent belongs to.",
  "register-agent-form:project_id": "Project this agent works in. Project agents must belong to the selected team.",
  "register-agent-form:role_id": "First-class role assigned to this agent.",
  "register-agent-form:session_id": "Session ID used when this agent is woken or resumed.",
  "register-agent-form:display_name": "Optional display name. If blank, the generated agent ID is shown.",
  "register-agent-form:reports_to_agent_id": "Supervisor for routing, permissions, status requests, and hierarchy display.",
  "register-agent-form:max_concurrent": "Maximum ephemeral worker agents this persistent agent may spawn at the same time.",
  "edit:agent:auto_context_clearing_enabled": "When enabled, the Team runtime marks this agent for fresh context after it becomes idle.",

  "create-task-form:owner_agent_id": "Persistent agent responsible for completing this task.",
  "create-task-form:project_id": "Project that owns this task and constrains parent and dependency links.",
  "create-task-form:priority": "Lower numbers sort earlier in each agent's task queue.",
  "create-task-form:title": "Short task title shown in project and agent task lists.",
  "create-task-form:description": "Work instructions for the owning agent.",
  "create-task-form:created_by_agent_id": "Agent credited as the task creator in task history and timeline attribution.",
  "create-task-form:state": "Optional initial task state. Blank creates a pending task.",
  "create-task-form:parent_task_id": "Optional parent task in the same project.",
  "create-task-form:dependency_task_ids": "Optional dependency task IDs, one per line or comma-separated. Dependencies must be in the same project.",

  "update-task-form:task_id": "Task to update. Selecting a task loads its current editable fields.",
  "update-task-form:state": "New task state. Leave blank to keep the current state.",
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
  "create-role-form:permissions": "Selectable permissions granted to agents assigned this role.",
  "create-role-form:max_concurrent": "Default maximum ephemeral worker agents for agents created with this role.",

  "create-review-profile-form:applicable_role_ids": "Optional reviewer roles. Blank allows all reviewer roles.",
  "create-review-profile-form:criteria": "Criteria rows in id | label | description | required format.",

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
  "edit:task:state": "Task state to save.",
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
  "edit:role:permissions": "Selectable permissions granted to this role.",
  "edit:role:max_concurrent": "Default maximum ephemeral worker agents for agents created with this role.",
  "edit:role:archived": "Archived roles stay in state but should not be assigned to new agents.",
  "edit:review-profile:applicable_role_ids": "Optional reviewer roles. Blank allows all reviewer roles.",
  "edit:review-profile:criteria": "Replace criteria using id | label | description | required rows.",
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
  bindRuntimeFilters();
  bindDeliveryFilters();
  bindPlanningFilters();
  bindOrgFocusTabs();
  bindArchivedVisibilityToggle();
  bindInboxPopoverPositioning();
  elements.templateSelect?.addEventListener("change", () => {
    renderTemplatePreview();
  });
  bindTimelineFilters();
  bindTimelinePagination();
  elements.refreshButton?.addEventListener("click", () => {
    void loadState({ preserveForms: true });
  });
  decorateFieldTooltips(document);
  void loadState();
});

function emptyState() {
  return {
    revision: 0,
    teams: {},
    projects: {},
    roles: {},
    agents: {},
    agent_runtimes: {},
    tasks: {},
    deliverables: {},
    review_requests: {},
    review_profiles: {},
    inbox: {},
    action_records: {},
    release_packages: {},
    ephemeral_assignments: {},
    git_promotions: {},
    runtime_operations: {
      generated_at: null,
      agents: [],
      attention: [],
    },
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
    entries: [],
  };
}

function emptyCostState() {
  return {
    generated_at: null,
    coverage_status: "partial",
    coverage_reasons: ["costs_not_loaded"],
    team_costs: {},
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

const decisionStates = ["accepted", "rejected", "blocked"];
const inboxItemLabels = {
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
  elements.teamViewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      setTeamView(button.dataset.teamView || "workspace");
    });
  });
  updateTeamViewTabs();
}

function setTeamView(view) {
  activeTeamView = teamViewIds.has(view) ? view : "workspace";
  render();
}

function updateTeamViewTabs() {
  elements.teamViewButtons.forEach((button) => {
    const active = (button.dataset.teamView || "workspace") === activeTeamView;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
    button.tabIndex = active ? 0 : -1;
  });
  elements.teamViewPanels.forEach((panel) => {
    const active = (panel.dataset.teamViewPanel || "workspace") === activeTeamView;
    panel.hidden = !active;
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

function placeSetupFormsPanel() {
  const panel = elements.setupFormsPanel;
  if (!panel) {
    return;
  }
  if (activeTeamView === "setup" && elements.focusedSetupMount) {
    elements.focusedSetupMount.appendChild(panel);
    return;
  }
  if (elements.workspaceSetupAnchor && elements.workspaceSetupAnchor.parentNode) {
    elements.workspaceSetupAnchor.after(panel);
  }
}

function bindForms() {
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
}

function bindRecordEditing() {
  document.addEventListener("click", (event) => {
    const selectAgentButton = event.target.closest("[data-select-agent-id]");
    if (selectAgentButton) {
      event.preventDefault();
      selectPeopleAgent(selectAgentButton.dataset.selectAgentId);
      return;
    }

    const agentCallButton = event.target.closest("[data-agent-call-agent-id]");
    if (agentCallButton) {
      event.preventDefault();
      void startAgentVoiceCall(agentCallButton, agentCallButton.dataset.agentCallAgentId);
      return;
    }

    const agentCallMuteButton = event.target.closest("[data-agent-call-mute]");
    if (agentCallMuteButton) {
      event.preventDefault();
      toggleAgentVoiceMute(agentCallMuteButton);
      return;
    }

    const agentCallEndButton = event.target.closest("[data-agent-call-end]");
    if (agentCallEndButton) {
      event.preventDefault();
      void endAgentVoiceCall(agentCallEndButton);
      return;
    }

    const contextRotationButton = event.target.closest("[data-context-rotation-agent-id]");
    if (contextRotationButton) {
      event.preventDefault();
      void requestContextRotation(
        contextRotationButton,
        contextRotationButton.dataset.contextRotationAgentId,
      );
      return;
    }

    const standDownButton = event.target.closest("[data-stand-down-agent-id]");
    if (standDownButton) {
      event.preventDefault();
      void requestAgentStandDown(
        standDownButton,
        standDownButton.dataset.standDownAgentId,
      );
      return;
    }

    const standDownRevokeButton = event.target.closest("[data-stand-down-revoke-agent-id]");
    if (standDownRevokeButton) {
      event.preventDefault();
      void revokeAgentStandDown(
        standDownRevokeButton,
        standDownRevokeButton.dataset.standDownRevokeAgentId,
      );
      return;
    }

    const dismissActionButton = event.target.closest("[data-dismiss-action-id]");
    if (dismissActionButton) {
      event.preventDefault();
      void dismissActionRecordAttention(
        dismissActionButton,
        dismissActionButton.dataset.dismissActionId,
      );
      return;
    }

    const avatarGenerateButton = event.target.closest("[data-avatar-generate-agent-id]");
    if (avatarGenerateButton) {
      event.preventDefault();
      void generateAvatarPreview(avatarGenerateButton);
      return;
    }

    const avatarAcceptButton = event.target.closest("[data-avatar-accept-agent-id]");
    if (avatarAcceptButton) {
      event.preventDefault();
      void acceptAvatarPreview(avatarAcceptButton);
      return;
    }

    const avatarUploadButton = event.target.closest("[data-avatar-upload-agent-id]");
    if (avatarUploadButton) {
      event.preventDefault();
      void uploadAgentAvatar(avatarUploadButton);
      return;
    }

    const avatarRemoveButton = event.target.closest("[data-avatar-remove-agent-id]");
    if (avatarRemoveButton) {
      event.preventDefault();
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
    openEditDialog(editTarget.dataset.editType, editTarget.dataset.editId);
  });

  elements.editForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitEditForm(elements.editForm);
  });
}

function bindExistingAgentPickers() {
  document.querySelectorAll("[data-agent-source-picker]").forEach((select) => {
    select.addEventListener("change", () => applyExistingAgentDefaults(select));
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
  setFieldValue(form, select.dataset.projectField, sourceAgent.project_id || "");
  setFieldValue(form, select.dataset.reportsToField, sourceAgent.reports_to_agent_id || "");
  if (select.dataset.maxConcurrentField) {
    setFieldValue(form, select.dataset.maxConcurrentField, String(ephemeralCapacity(sourceAgent)));
  }

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
  const formId = formIdentifier(form);
  const fieldName = text(field.name);
  const editType = text(form?.dataset?.editType);
  const keys = [];
  if (editType) {
    keys.push(`edit:${editType}:${fieldName}`);
  }
  if (formId) {
    keys.push(`${formId}:${fieldName}`);
  }
  keys.push(fieldName);
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

function updatePlanningFilterControls() {
  document.querySelectorAll("[data-planning-task-status]").forEach((input) => {
    const status = input.dataset.planningTaskStatus;
    input.checked = selectedPlanningTaskStates.has(status);
  });
}

async function loadState({ preserveForms = false } = {}) {
  const drafts = preserveForms ? captureFormDrafts() : null;
  setStatus("Loading team state.", "loading");
  timelineLoading = true;
  try {
    const [stateResponse, templatesResponse] = await Promise.all([
      fetch(`${API_BASE}/state`, {
        headers: { Accept: "application/json" },
      }),
      fetch(`${API_BASE}/templates`, {
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
    void loadTimeline();
  } catch (error) {
    timelineLoading = false;
    setStatus(error instanceof Error ? error.message : "Team state failed to load.", "error");
  }
}

async function loadCosts() {
  const requestId = ++costsRequestId;
  try {
    const response = await fetch(`${API_BASE}/costs`, {
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

function normalizeState(payload) {
  return { ...emptyState(), ...(payload || {}) };
}

function normalizeTemplates(payload) {
  return Array.isArray(payload?.templates) ? payload.templates : [];
}

function normalizeCosts(payload) {
  return { ...emptyCostState(), ...(payload || {}) };
}

function render() {
  elements.revisionBadge.textContent = `Revision ${state.revision}`;
  updateArchivedVisibilityToggle();
  updateTeamViewTabs();
  placeSetupFormsPanel();
  renderSummary();
  renderRuntimeOperations();
  renderReviewDeliveryWorkspace();
  updatePlanningFilterControls();
  renderHierarchy();
  renderFocusedOrgChartIfActive();
  renderAgents();
  renderRoles();
  renderProjects();
  renderReviewProfiles();
  renderReleases();
  renderTemplateSetup();
  refreshSelects();
  renderTimeline();
  decorateFieldTooltips(document);
  updateTeamViewTabs();
  placeSetupFormsPanel();
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

function renderSummary() {
  const teams = values(state.teams);
  const visibleTeams = teams.filter(isVisibleTeam);
  const projects = values(state.projects);
  const visibleProjects = projects.filter(isVisibleProject);
  const visibleAgents = values(state.agents).filter(isVisibleAgent);
  const tasks = values(state.tasks);
  const deliverables = values(state.deliverables);
  const reviews = values(state.review_requests);
  const reviewProfiles = values(state.review_profiles);
  const visibleReviewProfiles = reviewProfiles.filter(isVisibleReviewProfile);
  const ephemeral = values(state.ephemeral_assignments);

  const metrics = [
    ["Teams", visibleTeams.length],
    ["Projects", visibleProjects.length],
    ["Agents", visibleAgents.length],
    ["Open tasks", tasks.filter((task) => !terminalTaskStates.has(task.state)).length],
    ["Pending reviews", reviews.filter((review) => !terminalRecordStates.has(review.state)).length],
    ["Review profiles", visibleReviewProfiles.length],
    ["Ephemeral", ephemeral.filter((assignment) => assignment.state !== "cleaned_up").length],
  ];

  const html = metrics
    .map(([label, value]) => {
      return `<div class="metric"><strong>${value}</strong><span>${escapeHtml(label)}</span></div>`;
    })
    .join("")
    .concat(
      !teams.length
        ? `<p class="empty-state summary-empty-state">No teams registered.</p>`
        : !visibleTeams.length
          ? `<p class="empty-state summary-empty-state">No active team.</p>`
      : "",
    );
  renderIntoActiveTargets("[data-summary-grid]", html);
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

function isArchivedTeam(team) {
  return Boolean(team?.archived);
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
      ${refs ? `<div class="meta-line">${refs}</div>` : ""}
      ${actions}
    </li>
  `;
}

function renderCompactRuntimeAttentionRow(row) {
  const refs = runtimeReferenceParts(row);
  const stateLine = row.state ? `State ${escapeHtml(row.state)}` : "";
  const errorLine = row.error ? `Error ${escapeHtml(row.error)}` : "";
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
  const wakeReason = runtimeText(agent.wake_reason);
  const lastWake = runtimeText(agent.last_wake_at);
  const nextWake = runtimeText(agent.next_wake_at);
  const wakeLease = runtimeText(agent.wake_leased_until);
  const dispatchId = runtimeText(agent.active_dispatch_id);
  const contextPending = Boolean(agent.context_rotation_pending);
  const contextAuto = Boolean(agent.auto_context_clearing_enabled);
  const lastRotated = runtimeText(agent.last_context_rotated_at);
  const handoffSummary = text(agent.last_handoff_summary);
  return `
    <li class="record-item runtime-item runtime-agent-item">
      <div class="record-line">
        <strong>${escapeHtml(agent.id)}</strong>
        <div class="record-actions">
          <span class="state-pill ${escapeHtml(status)}">${escapeHtml(status)}</span>
          ${renderAgentStandDownControls(agent.id, agent)}
          <button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(agent.id)}">Clear context</button>
        </div>
      </div>
      <div class="meta-line">${escapeHtml(agentRoleLabel(agent))} - ${escapeHtml(agent.session_id)}${agent.project_id ? ` - project ${escapeHtml(agent.project_id)}` : ""}</div>
      <div class="meta-line">Last wake ${escapeHtml(lastWake)} - Next wake ${escapeHtml(nextWake)} - Wake lease ${escapeHtml(wakeLease)}</div>
      <div class="meta-line">Wake reason ${escapeHtml(wakeReason)} - No progress ${escapeHtml(agent.no_progress_count ?? 0)} - Active dispatch ${escapeHtml(dispatchId)}</div>
      <div class="meta-line">Context clearing ${escapeHtml(contextAuto ? "auto on" : "auto off")} - ${escapeHtml(contextPending ? "pending" : "not pending")} - Last rotated ${escapeHtml(lastRotated)}</div>
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
  const wakeReason = runtimeText(agent.wake_reason);
  const lastWake = runtimeText(agent.last_wake_at);
  const nextWake = runtimeText(agent.next_wake_at);
  const wakeLease = runtimeText(agent.wake_leased_until);
  const dispatchId = runtimeText(agent.active_dispatch_id);
  const contextPending = Boolean(agent.context_rotation_pending);
  const contextAuto = Boolean(agent.auto_context_clearing_enabled);
  const lastRotated = runtimeText(agent.last_context_rotated_at);
  const handoffSummary = text(agent.last_handoff_summary);
  const flags = [
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
            <span>${escapeHtml(agentRoleLabel(agent))} - ${escapeHtml(agent.session_id)}${agent.project_id ? ` - project ${escapeHtml(agent.project_id)}` : ""}</span>
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
          <div class="meta-line">Context clearing ${escapeHtml(contextAuto ? "auto on" : "auto off")} - ${escapeHtml(contextPending ? "pending" : "not pending")} - Last rotated ${escapeHtml(lastRotated)}</div>
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
  if (isDueOrLeasedAgent(agent)) {
    return "warning";
  }
  return "info";
}

function isDueOrLeasedAgent(agent) {
  return Boolean(agent.wake_due || agent.lease_active || agent.active_dispatch_id);
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

  const teams = values(state.teams);
  const visibleTeams = teams.filter(isVisibleTeam);
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

  if (elements.orgFocusTeamTabs) {
    elements.orgFocusTeamTabs.innerHTML = renderOrgFocusTeamTabs(visibleTeams);
  }

  if (!teams.length) {
    elements.orgFocusView.innerHTML = `<p class="empty-state">No teams registered.</p>`;
    return;
  }
  if (!visibleTeams.length) {
    elements.orgFocusView.innerHTML = `<p class="empty-state">No active team.</p>`;
    return;
  }

  elements.orgFocusView.innerHTML = focusedTeams
    .map((team) => {
      const hierarchy = teamHierarchyGraph(team);
      const teamAgents = values(state.agents).filter(
        (agent) => text(agent.team_id) === text(team.id) && isVisibleAgent(agent),
      );
      const teamAwakeCount = teamAgents.filter((agent) => agentAwakeState(agentCardData(agent)).kind === "awake").length;
      const teamAttentionCount = teamAgents.filter((agent) => {
        const details = agentCardData(agent);
        return details.taskStats.blockedWaitingCount || details.inboxCount || agentRuntimeAttentionRows(agent.id).length;
      }).length;
      return `
        <article class="org-focus-team">
          <div class="org-focus-team-header">
            <div>
              <p class="section-label">${escapeHtml(team.id)}</p>
              <h3>${escapeHtml(team.name)}</h3>
              ${renderTeamCostCounter(team.id)}
              ${renderTeamCostBlockedNotice(team.id)}
            </div>
            <div class="org-focus-team-status">
              ${team.archived ? `<span class="state-pill archived">archived</span>` : ""}
              ${renderTeamCostBlockedPill(team.id)}
              <span class="state-pill info">${teamAwakeCount} awake</span>
              <span class="state-pill ${teamAttentionCount ? "warning" : "accepted"}">${teamAttentionCount} attention</span>
            </div>
          </div>
          <div class="org-focus-chart">
            ${renderHierarchyForest(hierarchy.roots, { focused: true })}
            ${
              hierarchy.orphanRoots.length
                ? `
                  <section class="hierarchy-fallbacks" aria-label="Unreached agents">
                    <p class="subheading">Unreached agents</p>
                    ${renderHierarchyForest(hierarchy.orphanRoots, { focused: true })}
                  </section>
                `
                : ""
            }
          </div>
        </article>
      `;
    })
    .join("");
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
    const cardClasses = [
      "hierarchy-node-card",
      focused ? "org-focus-node-card" : "",
      focused ? `org-focus-node-card--${agentAwakeState(node.agentData).kind}` : "",
      node.agentData.taskStats.blockedWaitingCount || node.agentData.inboxCount ? "org-focus-node-card--attention" : "",
    ]
      .filter(Boolean)
      .join(" ");
    const actionAttrs = focused
      ? `role="button" data-edit-type="agent" data-edit-id="${escapeAttr(node.agentData.id)}" aria-label="Edit ${escapeAttr(node.agentData.label)}"`
      : "";
    return `
      <li class="hierarchy-branch">
        <article class="${cardClasses}" tabindex="0" ${actionAttrs}>
          <div class="hierarchy-node-summary">
            ${renderAgentAvatar(node.agentData)}
            <div class="hierarchy-node-title">
              <strong>${escapeHtml(node.agentData.label)}</strong>
              ${
                node.agentData.label !== node.agentData.id
                  ? `<span class="hierarchy-node-id">${escapeHtml(node.agentData.id)}</span>`
                  : ""
              }
            </div>
            <div class="hierarchy-node-subtitle">
              <span class="hierarchy-node-role">${escapeHtml(node.agentData.role)}</span>
              <span class="state-pill ${escapeHtml(node.agentData.status)}">${escapeHtml(node.agentData.status)}</span>
              ${focused ? renderAgentAwakePill(node.agentData) : ""}
              ${renderAgentInboxHeaderIndicators(node.agentData)}
            </div>
          </div>
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
  const allAgents = values(state.agents).sort(byId);
  const agents = allAgents.filter(isVisibleAgent);
  if (activeTeamView === "people") {
    renderPeopleAgents(allAgents, agents);
    return;
  }
  renderIntoActiveTargets("[data-agent-detail-view]", "");
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
                    <span class="state-pill ${escapeHtml(details.status)}">${escapeHtml(details.status)}</span>
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
}

function renderPeopleAgents(allAgents, agents) {
  if (!allAgents.length) {
    selectedPeopleAgentId = null;
    renderIntoActiveTargets("[data-agents-view]", `<p class="empty-state">No agents registered.</p>`);
    renderIntoActiveTargets("[data-agent-detail-view]", renderPeopleAgentDetail(null, "No agents registered."));
    return;
  }
  if (!agents.length) {
    selectedPeopleAgentId = null;
    renderIntoActiveTargets("[data-agents-view]", `<p class="empty-state">No visible agents.</p>`);
    renderIntoActiveTargets("[data-agent-detail-view]", renderPeopleAgentDetail(null, "No visible agents."));
    return;
  }

  const selectedAgent = agents.find((agent) => text(agent.id) === text(selectedPeopleAgentId)) || null;
  if (selectedPeopleAgentId && !selectedAgent) {
    selectedPeopleAgentId = null;
  }

  renderIntoActiveTargets("[data-agents-view]", `
    <ul class="agent-list compact-record-list people-agent-list">
      ${agents.map((agent) => renderSelectableAgent(agent)).join("")}
    </ul>
  `);
  renderIntoActiveTargets("[data-agent-detail-view]", renderPeopleAgentDetail(selectedAgent));
}

function selectPeopleAgent(agentId) {
  const normalizedAgentId = text(agentId);
  if (!normalizedAgentId || !state.agents[normalizedAgentId]) {
    return;
  }
  selectedPeopleAgentId = normalizedAgentId;
  renderAgents();
}

function renderSelectableAgent(agent) {
  const details = agentCardData(agent);
  const blockedWaiting = details.taskStats.blockedWaitingCount;
  const inboxCount = details.inboxCount;
  const summaryBits = [
    `${details.taskStats.openCount} open`,
    blockedWaiting ? `${blockedWaiting} blocked/waiting` : "",
    inboxCount ? `${inboxCount} inbox` : "",
  ].filter(Boolean);
  const selected = text(selectedPeopleAgentId) === details.id;
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
            <span>${escapeHtml(details.role)} - ${escapeHtml(details.id)}</span>
          </span>
          <span class="agent-summary-meta">
            <span class="state-pill ${escapeHtml(details.status)}">${escapeHtml(details.status)}</span>
            ${renderAgentAwakePill(details)}
            ${renderAgentInboxHeaderIndicators(details, { interactive: false })}
            <span>${escapeHtml(summaryBits.join(" - ") || "no open work")}</span>
          </span>
      </button>
    </li>
  `;
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
  const projectValue = details.projectId
    ? details.projectLabel && details.projectLabel !== details.projectId
      ? `${details.projectLabel} (${details.projectId})`
      : details.projectId
    : "None";

  return `
    <div class="agent-detail">
      <div class="agent-detail-header">
        ${renderAgentAvatar(details, "agent-avatar-large")}
        <div class="agent-detail-title">
          <h3>${escapeHtml(details.label)}</h3>
          <div class="meta-line">${escapeHtml(details.role)} - ${escapeHtml(details.id)}</div>
        </div>
        <div class="agent-detail-badges">
          <span class="state-pill ${escapeHtml(details.status)}">${escapeHtml(details.status)}</span>
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
      ${call.error ? `<div class="meta-line agent-voice-error">${escapeHtml(call.error)}</div>` : ""}
      ${transcriptRows ? `<ol class="agent-voice-transcript">${transcriptRows}</ol>` : ""}
    </section>
  `;
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
  const runtime = details.runtime || {};
  if (!runtime.present) {
    return ["No runtime snapshot"];
  }
  const rows = [runtime.summary || runtime.status || "Runtime idle"];
  if (runtime.lastHandoffSummary) {
    rows.push(`Last handoff ${runtime.lastHandoffSummary}`);
  }
  if (runtime.autoContextClearingEnabled || runtime.contextRotationPending) {
    rows.push(
      `Context clearing ${runtime.autoContextClearingEnabled ? "auto on" : "auto off"}${
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
          const permissions = (role.permissions || []).length
            ? role.permissions.map(permissionLabel).join(", ")
            : "no special permissions";
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(role.name)}</strong>
                <div class="record-actions">
                  <span class="state-pill ${role.archived ? "blocked" : "accepted"}">${role.archived ? "archived" : "active"}</span>
                  ${editButton("role", role.id)}
                </div>
              </div>
              <div class="meta-line">${escapeHtml(role.id)} - ${escapeHtml(scope)} - ${escapeHtml(permissions)}</div>
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

function agentStatus(agent) {
  return text(agent?.status) || "active";
}

function agentCardData(agent) {
  const taskStats = agentTaskStats(agent.id);
  const runtime = runtimeSummary(agent.id);
  const directSubordinateIds = uniqueTextList(agent?.subordinate_agent_ids);
  const projectId = text(agent?.project_id);
  const projectName = projectLabel(projectId);
  const reportsToId = text(agent?.reports_to_agent_id);
  const ephemeralCurrentCount = activeEphemeralAssignmentsForAgent(agent.id).length;
  const inboxItems = pendingInboxForAgent(agent.id);
  const standDowns = effectiveStandDowns(agent);
  const inboxIndicatorItems = {
    status_request: inboxItems.filter((item) => text(item.item_type) === "status_request"),
    progress_report: inboxItems.filter((item) => text(item.item_type) === "progress_report"),
  };

  return {
    id: text(agent?.id),
    label: agentLabel(agent),
    role: agentRoleLabel(agent),
    roleId: text(agent?.role_id),
    avatar: agent?.avatar || null,
    status: agentStatus(agent),
    sessionId: text(agent?.session_id),
    projectId: projectId || null,
    projectLabel: projectName || projectId || null,
    reportsToLabel: reportsToId || "operator",
    directSubordinateCount: directSubordinateIds.length,
    taskStats,
    inboxCount: inboxItems.length,
    inboxIndicatorItems,
    runtime,
    effectiveStandDowns: standDowns,
    standDownActive: Boolean(agent?.stand_down_active) || standDowns.length > 0,
    ephemeralCapacity: ephemeralCapacity(agent),
    ephemeralCurrentCount,
  };
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
  const autoContextClearingEnabled = Boolean(runtime.auto_context_clearing_enabled);
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

function agentAwakeState(agentData) {
  const runtimeRow = runtimeOperationForAgent(agentData.id);
  const activeDispatchId = text(runtimeRow?.active_dispatch_id || agentData.runtime?.activeDispatchId);
  if (activeDispatchId) {
    return {
      kind: "awake",
      label: "Awake",
      detail: `dispatch ${activeDispatchId}`,
    };
  }
  if (Boolean(runtimeRow?.lease_active) || isFutureTimestamp(agentData.runtime?.wakeLeasedUntil)) {
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
  const openTasks = agentData.taskStats.openTasks || [];
  const waitingTasks = openTasks.filter(isTaskWaitingOrBlocked);
  const currentTask = openTasks.find((task) => !isTaskWaitingOrBlocked(task)) || openTasks[0] || null;
  const inboxItems = pendingInboxForAgent(agentData.id);
  const runtimeAttention = agentRuntimeAttentionRows(agentData.id);
  const activeAssignments = activeEphemeralAssignmentsForAgent(agentData.id);
  const primary = currentTask
    ? `${currentTask.title} (${humanizeToken(currentTask.state)})`
    : inboxItems[0]
      ? `${inboxItemLabels[inboxItems[0].item_type] || inboxItems[0].item_type}: ${inboxItemDetail(inboxItems[0]) || inboxItems[0].id}`
      : activeAssignments[0]
        ? `${activeAssignments[0].title || activeAssignments[0].id} (${humanizeToken(activeAssignments[0].state)})`
        : "No open work";
  const waiting = [];
  waitingTasks.slice(0, 2).forEach((task) => {
    waiting.push(`${task.title} (${humanizeToken(task.state)})`);
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
  const snapshot = agentWorkSnapshot(agentData);
  const waitingLabel = snapshot.waiting.length
    ? snapshot.waiting[0]
    : snapshot.inboxItems.length
      ? `${snapshot.inboxItems.length} pending inbox`
      : "Clear";
  return `
    <div class="org-node-work">
      <div class="org-node-work-line org-node-work-line--now">
        <span>Now</span>
        <strong>${escapeHtml(snapshot.primary)}</strong>
      </div>
      <div class="org-node-work-line org-node-work-line--waiting">
        <span>Waiting</span>
        <strong>${escapeHtml(waitingLabel)}</strong>
      </div>
    </div>
  `;
}

function renderHierarchyDetails(agentData, { focused = false } = {}) {
  const lines = [];
  const awake = agentAwakeState(agentData);
  const snapshot = agentWorkSnapshot(agentData);

  if (focused) {
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
    lines.push(`Session ${agentData.sessionId}`);
  }
  if (agentData.projectId) {
    const projectText =
      agentData.projectLabel && agentData.projectLabel !== agentData.projectId
        ? `${agentData.projectLabel} (${agentData.projectId})`
        : agentData.projectId;
    lines.push(`Project ${projectText}`);
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
    `Context clearing ${agentData.runtime?.autoContextClearingEnabled ? "auto on" : "auto off"}${
      agentData.runtime?.contextRotationPending ? " - pending" : ""
    }`,
  );
  lines.push(`Ephemeral ${agentData.ephemeralCurrentCount} / ${agentData.ephemeralCapacity}`);

  return lines.map((line) => `<div class="meta-line">${escapeHtml(line)}</div>`).join("");
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
  renderTemplatePreview();
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

      <section class="template-preview-section">
        <h3>Git config</h3>
        <div class="meta-line">Git is optional. If used, provide a host repo path and a bare repo value.</div>
        <div class="meta-line">Bare repo values that start with / are treated as paths; other values are treated as names.</div>
      </section>
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
    renderTimeline();
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

  timelineLoading = true;
  setTimelineStatus("Loading derived timeline.", "loading");

  try {
    const response = await fetch(
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
  if (activeTeamView === "timeline") {
    const pageCount = Math.max(1, Math.ceil(entries.length / timelinePageSize));
    timelinePage = Math.min(Math.max(1, timelinePage), pageCount);
  }
  const summaryParts = [`${entries.length} ${entries.length === 1 ? "entry" : "entries"}`];
  if (timelineState.timestampless_count) {
    summaryParts.push(`${timelineState.timestampless_count} timestamp unavailable`);
  }
  if (activeTeamView === "timeline" && entries.length > timelinePageSize) {
    summaryParts.push(`page ${timelinePage} of ${Math.ceil(entries.length / timelinePageSize)}`);
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

function renderFocusedTimeline(entries) {
  const pageCount = Math.max(1, Math.ceil(entries.length / timelinePageSize));
  timelinePage = Math.min(Math.max(1, timelinePage), pageCount);
  const start = (timelinePage - 1) * timelinePageSize;
  const pageEntries = entries.slice(start, start + timelinePageSize);
  const pagination = entries.length > timelinePageSize
    ? renderTimelinePagination(pageCount, start, pageEntries.length, entries.length)
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
    const key = select.dataset.options;
    fillSelect(select, options[key] || []);
  });

  const updateTaskForm = document.querySelector("#update-task-form");
  if (updateTaskForm) {
    syncTaskUpdateForm(updateTaskForm, { keepEditedValues: true });
  }
}

function buildSelectOptionSets() {
  const teams = values(state.teams).filter(isVisibleTeam).sort(byId).map((team) => [team.id, `${team.name} (${team.id})`]);
  const projects = values(state.projects).filter(isVisibleProject).sort(byId).map((project) => [project.id, `${project.name} (${project.id})`]);
  const roles = values(state.roles).filter(isVisibleRole).sort(byId).map((role) => [role.id, `${role.name} (${role.id})`]);
  const agents = values(state.agents).filter(isVisibleAgent).sort(byId).map((agent) => [agent.id, `${agent.id} - ${agentRoleLabel(agent)}`]);
  const tasks = values(state.tasks).sort(byPriority).map((task) => [task.id, `${task.title} (${task.id})`]);
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
    agents,
    tasks,
    deliverables,
    "review-profiles": reviewProfiles,
    "review-requests": reviewRequests,
    "release-packages": releasePackages,
    "task-states": taskStates.map((item) => [item, item]),
    "record-states": recordStates.map((item) => [item, item]),
    "agent-statuses": agentStatuses.map((item) => [item, item]),
    "project-statuses": projectStatuses.map((item) => [item, item]),
    "decision-states": decisionStates.map((item) => [item, item]),
    "git-providers": gitProviders,
    "role-permissions": (state.available_role_permissions || []).map((permission) => [permission, permissionLabel(permission)]),
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

function fillSelect(select, options) {
  const currentValue = select.dataset.currentValue ?? select.value;
  const placeholder = select.dataset.placeholder || (select.required ? "Select" : "Default");
  select.innerHTML = `<option value="">${placeholder}</option>${options
    .map(([value, label]) => `<option value="${escapeAttr(value)}">${escapeHtml(label)}</option>`)
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

function fillSelectsIn(root) {
  const options = buildSelectOptionSets();
  root.querySelectorAll("select[data-options]").forEach((select) => {
    const key = select.dataset.options;
    fillSelect(select, options[key] || []);
  });
}

async function submitCreateForm(form) {
  const data = formData(form);
  const endpoint = endpointForForm(form, data);
  const method = form.dataset.method || "POST";
  if (!endpoint) {
    return;
  }
  const payload = payloadForForm(form, data);
  await submitMutation(form, endpoint, method, payload);
}

async function submitTaskUpdate(form) {
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
  const parentTaskId = text(data.parent_task_id);
  if (stateValue) payload.state = stateValue;
  if (priority) payload.priority = Number(priority);
  if (title) payload.title = title;
  if (description) payload.description = description;
  payload.parent_task_id = parentTaskId || null;
  payload.dependency_task_ids = splitList(data.dependency_task_ids);
  await submitMutation(form, `/tasks/${encodeURIComponent(taskId)}`, "PATCH", payload, {
    resetOnSuccess: false,
  });
}

async function submitMutation(form, endpoint, method, payload, { resetOnSuccess = true } = {}) {
  const drafts = captureFormDrafts();
  const body = { ...payload, expected_revision: state.revision };
  setFormBusy(form, true);
  setStatus("Saving.", "loading");
  try {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      method,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });

      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await loadState();
        restoreFormDrafts(drafts);
        const current = conflict.current_revision ?? state.revision;
        setStatus(`Revision conflict. Reloaded revision ${current}; review and submit again.`, "warning");
        return false;
      }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    state = normalizeState(await response.json());
    if (resetOnSuccess) {
      form.reset();
    }
      render();
      void loadTimeline();
      setStatus(`Saved revision ${state.revision}.`, "ok");
      return true;
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Save failed.", "error");
      return false;
    } finally {
      setFormBusy(form, false);
    }
}

async function uploadAgentAvatar(button) {
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
    const response = await fetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}/avatar/previews`, {
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
  const drafts = captureFormDrafts();
  setFormBusy(form, true);
  setStatus("Saving avatar.", "loading");
  try {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: { Accept: "application/json" },
      body,
    });
    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await loadState();
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; review and submit again.`, "warning");
      return false;
    }
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    state = normalizeState(await response.json());
    render();
    openEditDialog("agent", text(form.dataset.editId));
    void loadTimeline();
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
  const normalizedAgentId = text(agentId);
  if (!normalizedAgentId) {
    setStatus("No agent selected.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus("Requesting context clearing.", "loading");
  try {
    const response = await fetch(
      `${API_BASE}/agents/${encodeURIComponent(normalizedAgentId)}/context-rotation`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          expected_revision: state.revision,
          reason: "manual operator request",
        }),
      },
    );

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await loadState();
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; request context clearing again.`, "warning");
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    state = normalizeState(await response.json());
    render();
    restoreFormDrafts(drafts);
    void loadTimeline();
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
  const normalizedAgentId = text(agentId);
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
    const response = await fetch(
      `${API_BASE}/agents/${encodeURIComponent(normalizedAgentId)}/voice-sessions`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ request_id: call.requestId }),
      },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
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
  const response = await fetch(call.callsUrl, {
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
  const response = await fetch(
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
  const call = activeAgentVoiceCall;
  if (!call) {
    return;
  }
  const wasConnecting = call.status === "connecting";
  button.disabled = true;
  call.cancelRequested = true;
  call.status = "ending";
  renderAgents();
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
  const response = await fetch(endpoint, {
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
  const call = activeAgentVoiceCall;
  if (!call?.mediaStream) {
    return;
  }
  call.muted = !call.muted;
  call.mediaStream.getAudioTracks().forEach((track) => {
    track.enabled = !call.muted;
  });
  renderAgents();
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
    setStatus(message, "error");
    return;
  }
  const transcript = realtimeTranscriptFromEvent(event);
  if (transcript) {
    call.transcript.push(transcript);
    call.transcript = call.transcript.slice(-40);
    renderAgents();
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
  const response = await fetch(
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
    const response = await fetch(
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
  const timeoutResponse = await fetch(
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
    const response = await fetch(
      `${API_BASE}/agents/${encodeURIComponent(normalizedAgentId)}/stand-down`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          expected_revision: state.revision,
          reason,
        }),
      },
    );

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await loadState();
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; stand down again.`, "warning");
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    state = normalizeState(await response.json());
    render();
    restoreFormDrafts(drafts);
    void loadTimeline();
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
  const normalizedAgentId = text(agentId);
  if (!normalizedAgentId) {
    setStatus("No agent selected.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus("Revoking stand-down.", "loading");
  try {
    const response = await fetch(
      `${API_BASE}/agents/${encodeURIComponent(normalizedAgentId)}/stand-down/revoke`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          expected_revision: state.revision,
        }),
      },
    );

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await loadState();
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; revoke again.`, "warning");
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    state = normalizeState(await response.json());
    render();
    restoreFormDrafts(drafts);
    void loadTimeline();
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
  if (!actionRecordId) {
    setStatus("No action record selected.", "error");
    return;
  }
  const drafts = captureFormDrafts();
  button.disabled = true;
  setStatus("Dismissing failed action.", "loading");
  try {
    const response = await fetch(
      `${API_BASE}/action-records/${encodeURIComponent(actionRecordId)}/dismiss`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          expected_revision: state.revision,
          dismissed_by: "operator",
        }),
      },
    );

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await loadState();
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; review and dismiss again.`, "warning");
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    state = normalizeState(await response.json());
    render();
    restoreFormDrafts(drafts);
    void loadTimeline();
    setStatus(`Dismissed failed action ${actionRecordId}.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Dismiss failed.", "error");
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
  const formId = formIdentifier(form);
  if (formId === "setup-template-form") {
    const payload = {
      team: {
        name: text(data.team_name),
        manager_session_id: text(data.manager_session_id),
      },
      project: {
        name: text(data.project_name),
        lead_session_id: text(data.project_lead_session_id),
      },
      agents: {
        implementer: {
          session_id: text(data.implementer_session_id),
        },
        reviewer: {
          session_id: text(data.reviewer_session_id),
        },
        release_operator: {
          session_id: text(data.release_operator_session_id),
        },
      },
    };
    const operatorTarget = text(data.operator_communication_target_id);
    if (operatorTarget) {
      payload.team.operator_communication_target_id = operatorTarget;
    }
    const maxCost = numberOrNull(data.max_cost_usd);
    if (maxCost !== null) {
      payload.team.max_cost_usd = maxCost;
    }
    const git = gitConfigForForm(data);
    if (git) {
      payload.project.git = git;
    }
    return payload;
  }
  if (formId === "create-team-form") {
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
    const maxCost = numberOrNull(data.max_cost_usd);
    if (maxCost !== null) {
      payload.max_cost_usd = maxCost;
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
      lead_session_id: text(data.lead_session_id),
      goals,
    };
    const leadRoleName = text(data.lead_role_name);
    if (leadRoleName) {
      payload.lead_role_name = leadRoleName;
    }
    const leadDisplayName = text(data.lead_display_name);
    if (leadDisplayName) {
      payload.lead_display_name = leadDisplayName;
    }
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
  if (formId === "register-agent-form") {
    const payload = {
      team_id: text(data.team_id),
      project_id: text(data.project_id),
      role_id: text(data.role_id),
      session_id: text(data.session_id),
      reports_to_agent_id: text(data.reports_to_agent_id),
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
      criteria: parseCriteria(data.criteria),
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
    closeEditDialog();
  }
}

function recordForEdit(type, id) {
  if (type === "team") return state.teams[id] || null;
  if (type === "project") return state.projects[id] || null;
  if (type === "role") return state.roles[id] || null;
  if (type === "agent") return state.agents[id] || null;
  if (type === "task") return state.tasks[id] || null;
  if (type === "review-profile") return state.review_profiles[id] || null;
  return null;
}

function editEndpoint(type, id) {
  if (type === "team") return `/teams/${encodeURIComponent(id)}`;
  if (type === "project") return `/projects/${encodeURIComponent(id)}`;
  if (type === "role") return `/roles/${encodeURIComponent(id)}`;
  if (type === "agent") return `/agents/${encodeURIComponent(id)}`;
  if (type === "task") return `/tasks/${encodeURIComponent(id)}`;
  if (type === "review-profile") return `/review-profiles/${encodeURIComponent(id)}`;
  throw new Error(`Unknown edit type ${type}`);
}

function editDialogTitle(type, record) {
  const labels = {
    team: "team",
    project: "project",
    role: "role",
    agent: "agent",
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
      ${textField("Operator communication target id", "operator_communication_target_id", record.operator_communication_target_id || "")}
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
      ${textField("Base branch", "base_branch", record.git?.base_branch || "main")}
      ${textField("Canonical origin URL", "canonical_origin_url", record.git?.canonical_origin_url || "")}
      ${selectField("Provider", "provider", "git-providers", record.git?.provider || "", { placeholder: "Default" })}
      ${textField("Canonical release branch prefix", "canonical_release_branch_prefix", record.git?.canonical_release_branch_prefix || "")}
    `;
  }
  if (type === "agent") {
    const runtime = state.agent_runtimes?.[record.id] || {};
    return `
      ${readonlyField("Agent ID", record.id)}
      ${readonlyField("Team", record.team_id)}
      ${selectField("Project", "project_id", "projects", record.project_id || "", { placeholder: "No project" })}
      ${selectField("Role", "role_id", "roles", record.role_id || "", { required: true })}
      ${textField("Session ID", "session_id", record.session_id, { required: true })}
      ${textField("Display name", "display_name", record.display_name || "")}
      ${renderAvatarEditControls(record)}
      ${selectField("Reports to", "reports_to_agent_id", "agents", record.reports_to_agent_id || "", { placeholder: "Operator" })}
      ${numberField("Max ephemeral agents", "max_concurrent", ephemeralCapacity(record), { min: 0 })}
      ${selectField("Status", "status", "agent-statuses", record.status || "active", { required: true })}
      ${checkboxField("Automatic context clearing", "auto_context_clearing_enabled", Boolean(runtime.auto_context_clearing_enabled))}
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
  if (type === "task") {
    return `
      ${readonlyField("Task ID", record.id)}
      ${readonlyField("Project", record.project_id)}
      ${readonlyField("Owner", record.owner_agent_id)}
      ${selectField("State", "state", "task-states", record.state || "pending", { required: true })}
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
      ${textareaField("Criteria", "criteria", criteriaToText(record.criteria || []), {
        rows: 4,
        placeholder: "id | label | description | required",
      })}
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

function editPayloadForForm(type, form) {
  const data = formData(form);
  if (type === "team") {
    return {
      name: text(data.name),
      operator_communication_target_id: text(data.operator_communication_target_id) || null,
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
    return {
      project_id: text(data.project_id) || null,
      role_id: text(data.role_id),
      session_id: text(data.session_id),
      reports_to_agent_id: text(data.reports_to_agent_id) || null,
      display_name: text(data.display_name) || null,
      ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
      status: text(data.status),
      auto_context_clearing_enabled: Boolean(form.elements.auto_context_clearing_enabled?.checked),
    };
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
  if (type === "task") {
    return {
      state: text(data.state),
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
      criteria: parseCriteria(data.criteria),
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

function syncTaskUpdateForm(form, { keepEditedValues = false } = {}) {
  const taskId = form.elements.task_id?.value;
  const task = taskId ? state.tasks[taskId] : null;
  if (!task) {
    return;
  }
  if (!keepEditedValues || !form.elements.state.value) {
    form.elements.state.value = task.state || "";
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

function captureFormDrafts() {
  const drafts = {};
  document.querySelectorAll("form").forEach((form) => {
    const formId = formIdentifier(form);
    if (formId) {
      const values = formData(form);
      form.querySelectorAll('input[type="checkbox"][name]').forEach((field) => {
        values[field.name] = field.checked ? "true" : "false";
      });
      drafts[formId] = values;
    }
  });
  return drafts;
}

function restoreFormDrafts(drafts) {
  Object.entries(drafts || {}).forEach(([formId, values]) => {
    const form = document.getElementById(formId);
    if (!form) {
      return;
    }
    Object.entries(values).forEach(([name, value]) => {
      const field = form.elements[name];
      if (!field) {
        return;
      }
      if (field instanceof HTMLInputElement && field.type === "checkbox") {
        field.checked = value === true || value === "true" || value === "on";
        return;
      }
      if (field instanceof HTMLSelectElement && field.multiple) {
        const selected = new Set(Array.isArray(value) ? value.map(text) : splitList(value));
        Array.from(field.options).forEach((option) => {
          option.selected = selected.has(option.value);
        });
        return;
      }
      field.value = value;
    });
  });
  const updateTaskForm = document.querySelector("#update-task-form");
  if (updateTaskForm) {
    syncTaskUpdateForm(updateTaskForm, { keepEditedValues: true });
  }
  renderTemplatePreview();
}

function setFormBusy(form, busy) {
  form.querySelectorAll("button, input, select, textarea").forEach((field) => {
    field.disabled = busy;
  });
}

function setStatus(message, level) {
  const normalizedMessage = text(message);
  elements.statusMessage.hidden = !normalizedMessage;
  elements.statusMessage.textContent = normalizedMessage;
  elements.statusMessage.className = `status-message ${level || ""}`.trim();
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
  return text(agent?.role_name) || roleLabel(agent?.role_id);
}

function permissionLabel(permission) {
  return humanizeToken(permission);
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

function criteriaToText(criteria) {
  return (criteria || [])
    .map((criterion) =>
      [
        criterion.id,
        criterion.label || criterion.id,
        criterion.description || "",
        criterion.required === false ? "false" : "true",
      ].join(" | "),
    )
    .join("\n");
}

function parseCriteria(value) {
  return text(value)
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [id, label, description = "", required = "true"] = line
        .split("|")
        .map((part) => part.trim());
      return {
        id,
        label: label || id,
        description,
        required: !["false", "no", "0", "optional"].includes(required.toLowerCase()),
      };
    });
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
