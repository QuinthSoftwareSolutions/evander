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
const projectStatuses = ["active", "blocked", "completed", "archived"];
const gitProviders = [
  ["github", "GitHub"],
  ["azure_devops", "Azure DevOps"],
];
const terminalTaskStates = new Set(["finished", "accepted", "rejected"]);
const terminalRecordStates = new Set(["accepted", "rejected"]);

let state = emptyState();
let runtimeFilter = null;
let runtimeFilterTouched = false;
let templates = [];
let timelineState = emptyTimelineState();
let timelineLoading = false;
let timelineRequestId = 0;
let showArchived = false;

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

const elements = {
  revisionBadge: document.querySelector("#revision-badge"),
  statusMessage: document.querySelector("#status-message"),
  summaryGrid: document.querySelector("#summary-grid"),
  runtimeOperationsView: document.querySelector("#runtime-operations-view"),
  runtimeFilterButtons: Array.from(document.querySelectorAll("[data-runtime-filter]")),
  reviewDeliveryView: document.querySelector("#review-delivery-view"),
  hierarchyView: document.querySelector("#hierarchy-view"),
  agentsView: document.querySelector("#agents-view"),
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
  "setup-template-form:team_id": "Immutable identifier for the new team. The UI suggests one from the team name, but you can edit it before creation.",
  "setup-template-form:team_name": "Human-readable name shown in hierarchy, filters, and timeline rows.",
  "setup-template-form:manager_existing_agent_id": "Copy session defaults from an existing agent while keeping this new manager's ID editable.",
  "setup-template-form:manager_agent_id": "Persistent manager agent ID for this team. Agents in multiple projects can report to this shared manager.",
  "setup-template-form:manager_session_id": "Session ID used when the manager agent is woken or resumed.",
  "setup-template-form:operator_communication_target_id": "Optional external target for manager escalations, such as a chat channel or operator contact.",
  "setup-template-form:project_id": "Immutable identifier for the new project. It is used as a prefix for template-created records.",
  "setup-template-form:project_name": "Human-readable project name shown in project cards and filters.",
  "setup-template-form:project_lead_existing_agent_id": "Copy defaults from an existing project lead while creating a distinct new project-lead agent.",
  "setup-template-form:project_lead_agent_id": "Persistent project lead agent ID. This agent owns project planning and release handoff tasks.",
  "setup-template-form:project_lead_session_id": "Session ID used when the project lead agent is woken or resumed.",
  "setup-template-form:implementer_existing_agent_id": "Copy defaults from an existing implementer while keeping the new implementer ID editable.",
  "setup-template-form:implementer_agent_id": "Persistent agent ID for the implementation role created by the template.",
  "setup-template-form:implementer_session_id": "Session ID used when the implementer agent is woken or resumed.",
  "setup-template-form:reviewer_existing_agent_id": "Copy defaults from an existing reviewer while keeping the new reviewer ID editable.",
  "setup-template-form:reviewer_agent_id": "Persistent agent ID for the reviewer role created by the template.",
  "setup-template-form:reviewer_session_id": "Session ID used when the reviewer agent is woken or resumed.",
  "setup-template-form:release_operator_existing_agent_id": "Copy defaults from an existing release operator while keeping the new release-operator ID editable.",
  "setup-template-form:release_operator_agent_id": "Persistent agent ID for the release handoff and publication role.",
  "setup-template-form:release_operator_session_id": "Session ID used when the release operator agent is woken or resumed.",

  "create-team-form:id": "Immutable team identifier. The UI suggests one from the name, but you can edit it before creation.",
  "create-team-form:name": "Human-readable team name shown throughout the Team Add-on UI.",
  "create-team-form:manager_existing_agent_id": "Copy session, display, and ID defaults from an existing agent without reusing that agent ID directly.",
  "create-team-form:manager_agent_id": "Persistent ID for the manager agent created with this team.",
  "create-team-form:manager_session_id": "Session ID used to resume or wake the manager agent.",
  "create-team-form:manager_display_name": "Optional display name. If blank, the agent ID is shown.",
  "create-team-form:manager_role": "Role label for the manager. Keep this as manager unless you have a specific routing convention.",
  "create-team-form:operator_communication_target_id": "Optional external target for escalations raised by this team's manager.",

  "create-project-form:id": "Immutable project identifier. The UI suggests one from the name, but you can edit it before creation.",
  "create-project-form:team_id": "Team that owns this project. The project lead and later project agents must belong to the same team.",
  "create-project-form:name": "Human-readable project name shown in project cards, filters, and timelines.",
  "create-project-form:lead_existing_agent_id": "Copy session and display defaults from an existing agent while creating a distinct new lead agent.",
  "create-project-form:lead_agent_id": "Persistent project lead agent ID. The lead reports to the team's manager.",
  "create-project-form:lead_session_id": "Session ID used to resume or wake the project lead agent.",
  "create-project-form:lead_display_name": "Optional display name. If blank, the lead agent ID is shown.",
  "create-project-form:lead_role": "Role label for the lead. Keep this as project_lead unless you have a specific routing convention.",
  "create-project-form:goal_id": "Optional goal identifier. If blank, the UI derives one from the project ID.",
  "create-project-form:goal_description": "Optional initial goal for the project backlog.",

  "register-agent-form:existing_agent_id": "Copy defaults from an existing agent. This helps reuse session conventions while keeping the new ID editable.",
  "register-agent-form:id": "Immutable agent identifier. The UI suggests one from project and role, but you can edit it before creation.",
  "register-agent-form:team_id": "Team this persistent agent belongs to.",
  "register-agent-form:project_id": "Project this agent works in. Project agents must belong to the selected team.",
  "register-agent-form:role": "Routing and responsibility label for this agent.",
  "register-agent-form:session_id": "Session ID used when this agent is woken or resumed.",
  "register-agent-form:display_name": "Optional display name. If blank, the agent ID is shown.",
  "register-agent-form:reports_to_agent_id": "Supervisor for routing, permissions, status requests, and hierarchy display.",
  "register-agent-form:max_concurrent": "Maximum ephemeral worker agents this persistent agent may spawn at the same time.",

  "create-task-form:id": "Immutable task identifier. The UI suggests one from project and title.",
  "create-task-form:owner_agent_id": "Persistent agent responsible for completing this task.",
  "create-task-form:project_id": "Project that owns this task and constrains parent and dependency links.",
  "create-task-form:priority": "Lower numbers sort earlier in each agent's task queue.",
  "create-task-form:title": "Short task title shown in project and agent task lists.",
  "create-task-form:description": "Work instructions for the owning agent.",
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

  "create-review-profile-form:id": "Immutable review profile identifier.",
  "create-review-profile-form:team_id": "Team that owns this review profile.",
  "create-review-profile-form:project_id": "Optional project scope. Blank makes the profile team-wide.",
  "create-review-profile-form:name": "Human-readable profile name shown when requesting reviews.",
  "create-review-profile-form:description": "Optional summary of when this profile should be used.",
  "create-review-profile-form:applicable_roles": "Optional reviewer roles, one per line or comma-separated. Blank allows all reviewer roles.",
  "create-review-profile-form:criteria": "Criteria rows in id | label | description | required format.",

  "record-deliverable-form:id": "Immutable deliverable identifier.",
  "record-deliverable-form:task_id": "Task this deliverable completes or advances.",
  "record-deliverable-form:producer_agent_id": "Agent that produced the deliverable.",
  "record-deliverable-form:summary": "Short summary of the delivered work.",
  "record-deliverable-form:artifact_refs": "Optional artifact references, one per line or comma-separated.",
  "record-deliverable-form:state": "Initial deliverable state. Blank records it as pending.",

  "record-review-form:id": "Immutable review request identifier.",
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
  "edit:team:archived": "Archived teams stay in state; archiving a team also archives its agents.",
  "edit:project:name": "Rename the project without changing its immutable ID.",
  "edit:project:status": "Manual project status override. Later workflow actions may update this again.",
  "edit:project:goals": "Replace project goals using id | description | state rows.",
  "edit:agent:project_id": "Move the agent within its team. Project leads must remain assigned to their project.",
  "edit:agent:role": "Routing and responsibility label for this persistent agent.",
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
  "edit:review-profile:applicable_roles": "Optional reviewer roles, one per line or comma-separated. Blank allows all reviewer roles.",
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
  bindForms();
  bindRecordEditing();
  bindGeneratedIds();
  bindExistingAgentPickers();
  bindRuntimeFilters();
  bindDeliveryFilters();
  bindArchivedVisibilityToggle();
  elements.templateSelect?.addEventListener("change", () => {
    renderTemplatePreview();
  });
  bindTimelineFilters();
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
    }
  });

  elements.editForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitEditForm(elements.editForm);
  });
}

function bindGeneratedIds() {
  const rules = [
    {
      form: "#setup-template-form",
      target: "team_id",
      sources: ["team_name"],
      build: (form) => joinIdParts(["team", fieldText(form, "team_name")]),
    },
    {
      form: "#setup-template-form",
      target: "manager_agent_id",
      sources: ["team_name", "team_id"],
      build: (form) => joinIdParts([fieldText(form, "team_id") || fieldText(form, "team_name"), "manager"]),
    },
    {
      form: "#setup-template-form",
      target: "project_id",
      sources: ["project_name"],
      build: (form) => joinIdParts(["project", fieldText(form, "project_name")]),
    },
    {
      form: "#setup-template-form",
      target: "project_lead_agent_id",
      sources: ["project_name", "project_id"],
      build: (form) => joinIdParts([fieldText(form, "project_id") || fieldText(form, "project_name"), "project-lead"]),
    },
    {
      form: "#setup-template-form",
      target: "implementer_agent_id",
      sources: ["project_name", "project_id"],
      build: (form) => joinIdParts([fieldText(form, "project_id") || fieldText(form, "project_name"), "implementer"]),
    },
    {
      form: "#setup-template-form",
      target: "reviewer_agent_id",
      sources: ["project_name", "project_id"],
      build: (form) => joinIdParts([fieldText(form, "project_id") || fieldText(form, "project_name"), "reviewer"]),
    },
    {
      form: "#setup-template-form",
      target: "release_operator_agent_id",
      sources: ["project_name", "project_id"],
      build: (form) => joinIdParts([fieldText(form, "project_id") || fieldText(form, "project_name"), "release-operator"]),
    },
    {
      form: "#create-team-form",
      target: "id",
      sources: ["name"],
      build: (form) => joinIdParts(["team", fieldText(form, "name")]),
    },
    {
      form: "#create-team-form",
      target: "manager_agent_id",
      sources: ["name", "id"],
      build: (form) => joinIdParts([fieldText(form, "id") || fieldText(form, "name"), "manager"]),
    },
    {
      form: "#create-project-form",
      target: "id",
      sources: ["name"],
      build: (form) => joinIdParts(["project", fieldText(form, "name")]),
    },
    {
      form: "#create-project-form",
      target: "lead_agent_id",
      sources: ["name", "id"],
      build: (form) => joinIdParts([fieldText(form, "id") || fieldText(form, "name"), "project-lead"]),
    },
    {
      form: "#register-agent-form",
      target: "id",
      sources: ["project_id", "role"],
      build: (form) => joinIdParts([fieldText(form, "project_id"), fieldText(form, "role") || "agent"]),
    },
    {
      form: "#create-task-form",
      target: "id",
      sources: ["project_id", "title"],
      build: (form) => joinIdParts([fieldText(form, "project_id") || "task", fieldText(form, "title")]),
    },
    {
      form: "#create-review-profile-form",
      target: "id",
      sources: ["project_id", "team_id", "name"],
      build: (form) =>
        joinIdParts([fieldText(form, "project_id") || fieldText(form, "team_id") || "profile", fieldText(form, "name")]),
    },
  ];

  rules.forEach((rule) => bindGeneratedIdRule(rule));
}

function bindGeneratedIdRule(rule) {
  const form = document.querySelector(rule.form);
  const target = form?.elements?.[rule.target];
  if (!form || !target) {
    return;
  }
  target.dataset.autogenerated = target.value ? "false" : "true";
  target.addEventListener("input", () => {
    if (!target.value) {
      target.dataset.autogenerated = "true";
      target.dataset.lastGenerated = "";
      return;
    }
    if (target.value !== target.dataset.lastGenerated) {
      target.dataset.autogenerated = "false";
    }
  });
  (rule.sources || []).forEach((sourceName) => {
    const source = form.elements[sourceName];
    if (!source) {
      return;
    }
    ["input", "change"].forEach((eventName) => {
      source.addEventListener(eventName, () => fillGeneratedId(rule, form, target));
    });
  });
  fillGeneratedId(rule, form, target);
}

function fillGeneratedId(rule, form, target) {
  if (target.value && target.dataset.autogenerated !== "true") {
    return;
  }
  const generated = text(rule.build(form));
  if (!generated) {
    return;
  }
  setAutogeneratedValue(target, generated);
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
  setFieldValue(form, select.dataset.roleField, sourceAgent.role);
  setFieldValue(form, select.dataset.projectField, sourceAgent.project_id || "");
  setFieldValue(form, select.dataset.reportsToField, sourceAgent.reports_to_agent_id || "");
  if (select.dataset.maxConcurrentField) {
    setFieldValue(form, select.dataset.maxConcurrentField, String(ephemeralCapacity(sourceAgent)));
  }

  const idField = select.dataset.idField ? form.elements[select.dataset.idField] : null;
  if (idField && (!idField.value || idField.dataset.autogenerated === "true")) {
    const role =
      fieldText(form, select.dataset.roleField) ||
      text(select.dataset.suggestedRole) ||
      sourceAgent.role ||
      "agent";
    const scope =
      fieldText(form, select.dataset.projectField) ||
      fieldText(form, "project_id") ||
      fieldText(form, "id") ||
      fieldText(form, "team_id") ||
      fieldText(form, "team_name");
    setAutogeneratedValue(idField, joinIdParts([scope, role]));
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
    setStatus(`Loaded revision ${state.revision}.`, "ok");
    void loadTimeline();
  } catch (error) {
    timelineLoading = false;
    setStatus(error instanceof Error ? error.message : "Team state failed to load.", "error");
  }
}

function normalizeState(payload) {
  return { ...emptyState(), ...(payload || {}) };
}

function normalizeTemplates(payload) {
  return Array.isArray(payload?.templates) ? payload.templates : [];
}

function render() {
  elements.revisionBadge.textContent = `Revision ${state.revision}`;
  updateArchivedVisibilityToggle();
  renderSummary();
  renderRuntimeOperations();
  renderReviewDeliveryWorkspace();
  renderHierarchy();
  renderAgents();
  renderProjects();
  renderReviewProfiles();
  renderReleases();
  renderTemplateSetup();
  refreshSelects();
  renderTimeline();
  decorateFieldTooltips(document);
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
  if (!elements.reviewDeliveryView) {
    return;
  }
  updateDeliveryFilterButtons();
  const workspaceRows = buildReviewDeliveryRows();
  const visibleRows =
    selectedDeliveryFilter === "all"
      ? workspaceRows
      : workspaceRows.filter((row) => row.effectiveStatus === selectedDeliveryFilter);

  if (!visibleRows.length) {
    elements.reviewDeliveryView.innerHTML = `
      <p class="empty-state">
        No deliverables match the ${escapeHtml(deliveryStatusLabels[selectedDeliveryFilter] || selectedDeliveryFilter)} filter.
      </p>
    `;
    bindWorkspaceReviewDecisionForms();
    return;
  }

  const teamGroups = groupReviewDeliveryRows(visibleRows);
  const bucketStatuses =
    selectedDeliveryFilter === "all" ? deliveryStatusOrder : [selectedDeliveryFilter];

  elements.reviewDeliveryView.innerHTML = teamGroups
    .map((teamGroup) => renderDeliveryTeamGroup(teamGroup, bucketStatuses))
    .join("");
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

  elements.summaryGrid.innerHTML = metrics
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

function isVisibleTeam(team) {
  return showArchived || !isArchivedTeam(team);
}

function isVisibleProject(project) {
  return showArchived || !isArchivedProject(project);
}

function isVisibleAgent(agent) {
  return showArchived || !isArchivedAgent(agent);
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
  const refs = runtimeReferenceParts(row);
  const stateLine = row.state ? `State ${escapeHtml(row.state)}` : "";
  const errorLine = row.error ? `Error ${escapeHtml(row.error)}` : "";
  const detail = row.detail ? `<div class="meta-line">${escapeHtml(row.detail)}</div>` : "";
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
  const status = runtimeAgentStatus(agent);
  const refs = runtimeReferenceParts(agent);
  const wakeReason = runtimeText(agent.wake_reason);
  const lastWake = runtimeText(agent.last_wake_at);
  const nextWake = runtimeText(agent.next_wake_at);
  const wakeLease = runtimeText(agent.wake_leased_until);
  const dispatchId = runtimeText(agent.active_dispatch_id);
  return `
    <li class="record-item runtime-item runtime-agent-item">
      <div class="record-line">
        <strong>${escapeHtml(agent.id)}</strong>
        <span class="state-pill ${escapeHtml(status)}">${escapeHtml(status)}</span>
      </div>
      <div class="meta-line">${escapeHtml(agent.role)} - ${escapeHtml(agent.session_id)}${agent.project_id ? ` - project ${escapeHtml(agent.project_id)}` : ""}</div>
      <div class="meta-line">Last wake ${escapeHtml(lastWake)} - Next wake ${escapeHtml(nextWake)} - Wake lease ${escapeHtml(wakeLease)}</div>
      <div class="meta-line">Wake reason ${escapeHtml(wakeReason)} - No progress ${escapeHtml(agent.no_progress_count ?? 0)} - Active dispatch ${escapeHtml(dispatchId)}</div>
      <div class="meta-line">Flags ${escapeHtml(runtimeBooleanLabel(agent.wake_due, "wake due"))} - ${escapeHtml(runtimeBooleanLabel(agent.lease_active, "lease active"))}</div>
      ${refs ? `<div class="meta-line">${refs}</div>` : ""}
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
  if (!elements.runtimeOperationsView) {
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

  elements.runtimeOperationsView.innerHTML = `
    <div class="runtime-filter-summary">Showing ${escapeHtml(runtimeFilterLabel(runtimeFilter))}.</div>
    ${sections.join("")}
    ${
      runtime.generated_at
        ? `<div class="meta-line runtime-generated-at">Runtime generated ${escapeHtml(runtime.generated_at)}</div>`
        : ""
    }
  `;
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

function renderHierarchy() {
  const teams = values(state.teams);
  const visibleTeams = teams.filter(isVisibleTeam);
  if (!teams.length) {
    elements.hierarchyView.innerHTML = `<p class="empty-state">No teams registered.</p>`;
    return;
  }
  if (!visibleTeams.length) {
    elements.hierarchyView.innerHTML = `<p class="empty-state">No active team.</p>`;
    return;
  }

  elements.hierarchyView.innerHTML = visibleTeams
    .map((team) => {
      const hierarchy = teamHierarchyGraph(team);
      return `
        <article class="hierarchy-team">
            <div class="hierarchy-team-header">
              <div>
                <p class="section-label">${escapeHtml(team.id)}</p>
                <h3>${escapeHtml(team.name)}</h3>
              </div>
              <div class="record-actions">
                ${team.archived ? `<span class="state-pill archived">archived</span>` : ""}
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
    .join("");
}

function renderHierarchyForest(nodes) {
  if (!nodes.length) {
    return `<p class="empty-state">No reporting tree.</p>`;
  }
  return `
    <ol class="hierarchy-tree">
      ${nodes.map((node) => renderHierarchyNode(node)).join("")}
    </ol>
  `;
}

function renderHierarchyNode(node) {
  if (!node) {
    return "";
  }
  if (node.kind === "agent") {
    const children = (node.children || []).length
      ? `<ol class="hierarchy-children">${node.children.map((child) => renderHierarchyNode(child)).join("")}</ol>`
      : "";
    return `
      <li class="hierarchy-branch">
        <article class="hierarchy-node-card" tabindex="0">
          <div class="hierarchy-node-summary">
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
            </div>
          </div>
          <div class="hierarchy-node-details">${renderHierarchyDetails(node.agentData)}</div>
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
  if (!allAgents.length) {
    elements.agentsView.innerHTML = `<p class="empty-state">No agents registered.</p>`;
    return;
  }
  if (!agents.length) {
    elements.agentsView.innerHTML = `<p class="empty-state">No visible agents.</p>`;
    return;
  }
  elements.agentsView.innerHTML = `
    <ul class="agent-list">
      ${agents
        .map((agent) => {
          const details = agentCardData(agent);
          return `
              <li class="agent-item">
                <div class="agent-line">
                  <strong>${escapeHtml(details.label)}</strong>
                  <div class="record-actions">
                    <span class="state-pill ${escapeHtml(details.status)}">${escapeHtml(details.status)}</span>
                    ${editButton("agent", details.id)}
                  </div>
                </div>
              <div class="meta-line">${escapeHtml(details.role)} - ${escapeHtml(details.id)}</div>
              <div class="meta-line">Reports to ${escapeHtml(details.reportsToLabel)} - ${details.taskStats.openCount} open task(s) - max ephemeral ${details.ephemeralCapacity}</div>
              ${renderAgentOpenTasks(details.taskStats.openTasks)}
              ${renderAgentInbox(agent.id)}
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
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

function pendingInboxForAgent(agentId) {
  return values(state.inbox)
    .filter((item) => item.recipient_agent_id === agentId && item.state !== "handled")
    .sort(byId);
}

function pendingInboxCount(agentId) {
  return pendingInboxForAgent(agentId).length;
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

  return {
    id: text(agent?.id),
    label: agentLabel(agent),
    role: text(agent?.role),
    status: agentStatus(agent),
    sessionId: text(agent?.session_id),
    projectId: projectId || null,
    projectLabel: projectName || projectId || null,
    reportsToLabel: reportsToId || "operator",
    directSubordinateCount: directSubordinateIds.length,
    taskStats,
    inboxCount: pendingInboxCount(agent.id),
    runtime,
    ephemeralCapacity: ephemeralCapacity(agent),
    ephemeralCurrentCount,
  };
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
    };
  }
  const activeDispatchId = text(runtime.active_dispatch_id);
  const wakeReason = text(runtime.wake_reason);
  const nextWakeAt = text(runtime.next_wake_at);
  const wakeLeasedUntil = text(runtime.wake_leased_until);
  const lastWakeAt = text(runtime.last_wake_at);
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
  return {
    present: true,
    status: activeDispatchId ? "busy" : wakeReason ? `wake ${wakeReason}` : "idle",
    summary: parts.join(" - "),
    activeDispatchId: activeDispatchId || null,
    wakeReason: wakeReason || null,
    nextWakeAt: nextWakeAt || null,
    wakeLeasedUntil: wakeLeasedUntil || null,
    lastWakeAt: lastWakeAt || null,
  };
}

function renderHierarchyDetails(agentData) {
  const lines = [];

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
          return `
            <li>
              <span class="state-pill ${escapeHtml(item.item_type)}">${escapeHtml(label)}</span>
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

function renderProjects() {
  const allProjects = values(state.projects).sort(byId);
  const projects = allProjects.filter(isVisibleProject);
  if (!allProjects.length) {
    elements.projectsView.innerHTML = `<p class="empty-state">No projects registered.</p>`;
    return;
  }
  if (!projects.length) {
    elements.projectsView.innerHTML = `<p class="empty-state">No visible projects.</p>`;
    return;
  }

  elements.projectsView.innerHTML = projects
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
    .join("");
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

function renderGoals(goals) {
  if (!goals.length) {
    return `<p class="empty-state">No goals.</p>`;
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

function renderTasks(tasks) {
  if (!tasks.length) {
    return `<p class="empty-state">No tasks.</p>`;
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

function renderDeliverables(deliverables) {
  if (!deliverables.length) {
    return `<p class="empty-state">No deliverables.</p>`;
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
  if (!elements.reviewProfilesView) {
    return;
  }
  const allProfiles = values(state.review_profiles).sort(byId);
  const profiles = allProfiles.filter(isVisibleReviewProfile);
  if (!allProfiles.length) {
    elements.reviewProfilesView.innerHTML = `<p class="empty-state">No review profiles.</p>`;
    return;
  }
  if (!profiles.length) {
    elements.reviewProfilesView.innerHTML = `<p class="empty-state">No visible review profiles.</p>`;
    return;
  }
  elements.reviewProfilesView.innerHTML = `
    <ul class="review-list">
      ${profiles
        .map((profile) => {
          const scope = profile.project_id ? `project ${profile.project_id}` : `team ${profile.team_id}`;
          const roles = (profile.applicable_roles || []).length
            ? `roles ${profile.applicable_roles.join(", ")}`
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
  if (!elements.releasesView) {
    return;
  }
  const releases = values(state.release_packages).sort(byId);
  if (!releases.length) {
    elements.releasesView.innerHTML = `<p class="empty-state">No release packages.</p>`;
    return;
  }
  elements.releasesView.innerHTML = `
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
  const filters = [
    elements.timelineTeamFilter,
    elements.timelineProjectFilter,
    elements.timelineAgentFilter,
    elements.timelineTypeFilter,
    elements.timelineStatusFilter,
    elements.timelineAttentionFilter,
  ];
  filters.forEach((filter) => {
    filter?.addEventListener("change", () => {
      void loadTimeline();
    });
  });
}

async function loadTimeline() {
  if (!elements.timelineView) {
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
  return {
    ...emptyTimelineState(),
    ...(payload || {}),
    entries: Array.isArray(payload?.entries) ? payload.entries : [],
  };
}

function renderTimeline() {
  if (!elements.timelineView) {
    return;
  }

  const entries = timelineState.entries || [];
  const summaryParts = [`${entries.length} ${entries.length === 1 ? "entry" : "entries"}`];
  if (timelineState.timestampless_count) {
    summaryParts.push(`${timelineState.timestampless_count} timestamp unavailable`);
  }
  elements.timelineSummary.textContent = summaryParts.join(" · ");

  if (!entries.length) {
    elements.timelineView.innerHTML = timelineLoading
      ? `<p class="timeline-empty">Loading timeline entries.</p>`
      : `<p class="timeline-empty">No timeline entries match these filters.</p>`;
    return;
  }

  elements.timelineView.innerHTML = `
    <ul class="timeline-list">
      ${entries.map((entry) => renderTimelineEntry(entry)).join("")}
    </ul>
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
        <div class="delivery-row-meta">${escapeHtml(deliverable.summary)}</div>
        ${renderArtifactRefs(deliverable.artifact_refs || [])}
        ${renderGitRefs(deliverable.git_refs, gitPromotionIds)}
        ${
          reviewIds.length
            ? `<div class="delivery-row-reviews"><div class="meta-line">Reviews: ${escapeHtml(reviewIds.join(", "))}</div>${renderReviewSummary(reviewIds, { interactive: true })}</div>`
            : ""
        }
        ${releaseSummaries}
      </div>
    </article>
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
  const teamId = text(elements.timelineTeamFilter?.value);
  const projectId = text(elements.timelineProjectFilter?.value);
  const agentId = text(elements.timelineAgentFilter?.value);
  const type = text(elements.timelineTypeFilter?.value);
  const status = text(elements.timelineStatusFilter?.value);
  const attentionValue = text(elements.timelineAttentionFilter?.value);
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
  const agents = values(state.agents).filter(isVisibleAgent).sort(byId).map((agent) => [agent.id, `${agent.id} - ${agent.role}`]);
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
        id: text(data.team_id),
        name: text(data.team_name),
        manager_agent_id: text(data.manager_agent_id),
        manager_session_id: text(data.manager_session_id),
      },
      project: {
        id: text(data.project_id),
        name: text(data.project_name),
        lead_agent_id: text(data.project_lead_agent_id),
        lead_session_id: text(data.project_lead_session_id),
      },
      agents: {
        implementer: {
          agent_id: text(data.implementer_agent_id),
          session_id: text(data.implementer_session_id),
        },
        reviewer: {
          agent_id: text(data.reviewer_agent_id),
          session_id: text(data.reviewer_session_id),
        },
        release_operator: {
          agent_id: text(data.release_operator_agent_id),
          session_id: text(data.release_operator_session_id),
        },
      },
    };
    const operatorTarget = text(data.operator_communication_target_id);
    if (operatorTarget) {
      payload.team.operator_communication_target_id = operatorTarget;
    }
    const git = gitConfigForForm(data);
    if (git) {
      payload.project.git = git;
    }
    return payload;
  }
  if (formId === "create-team-form") {
    const payload = {
      id: text(data.id),
      name: text(data.name),
      manager_agent_id: text(data.manager_agent_id),
      manager_session_id: text(data.manager_session_id),
      manager_role: text(data.manager_role) || "manager",
    };
    const managerDisplayName = text(data.manager_display_name);
    if (managerDisplayName) {
      payload.manager_display_name = managerDisplayName;
    }
    const operatorTarget = text(data.operator_communication_target_id);
    if (operatorTarget) {
      payload.operator_communication_target_id = operatorTarget;
    }
    return payload;
  }
  if (formId === "create-project-form") {
    const goals = [];
    const goalDescription = text(data.goal_description);
    if (goalDescription) {
      goals.push({
        id: text(data.goal_id) || `${text(data.id)}-goal`,
        description: goalDescription,
      });
    }
    const payload = {
      id: text(data.id),
      team_id: text(data.team_id),
      name: text(data.name),
      lead_agent_id: text(data.lead_agent_id),
      lead_session_id: text(data.lead_session_id),
      lead_role: text(data.lead_role) || "project_lead",
      goals,
    };
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
  if (formId === "register-agent-form") {
    const payload = {
      id: text(data.id),
      team_id: text(data.team_id),
      project_id: text(data.project_id),
      role: text(data.role),
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
      id: text(data.id),
      owner_agent_id: text(data.owner_agent_id),
      project_id: text(data.project_id),
      priority: numberOrZero(data.priority),
      title: text(data.title),
      description: text(data.description),
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
      id: text(data.id),
      team_id: text(data.team_id),
      name: text(data.name),
      description: text(data.description),
      applicable_roles: splitList(data.applicable_roles),
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
      id: text(data.id),
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
      id: text(data.id),
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
  if (type === "agent") return state.agents[id] || null;
  if (type === "task") return state.tasks[id] || null;
  if (type === "review-profile") return state.review_profiles[id] || null;
  return null;
}

function editEndpoint(type, id) {
  if (type === "team") return `/teams/${encodeURIComponent(id)}`;
  if (type === "project") return `/projects/${encodeURIComponent(id)}`;
  if (type === "agent") return `/agents/${encodeURIComponent(id)}`;
  if (type === "task") return `/tasks/${encodeURIComponent(id)}`;
  if (type === "review-profile") return `/review-profiles/${encodeURIComponent(id)}`;
  throw new Error(`Unknown edit type ${type}`);
}

function editDialogTitle(type, record) {
  const labels = {
    team: "team",
    project: "project",
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
    return `
      ${readonlyField("Agent ID", record.id)}
      ${readonlyField("Team", record.team_id)}
      ${selectField("Project", "project_id", "projects", record.project_id || "", { placeholder: "No project" })}
      ${textField("Role", "role", record.role, { required: true })}
      ${textField("Session ID", "session_id", record.session_id, { required: true })}
      ${textField("Display name", "display_name", record.display_name || "")}
      ${selectField("Reports to", "reports_to_agent_id", "agents", record.reports_to_agent_id || "", { placeholder: "Operator" })}
      ${numberField("Max ephemeral agents", "max_concurrent", ephemeralCapacity(record), { min: 0 })}
      ${selectField("Status", "status", "agent-statuses", record.status || "active", { required: true })}
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
      ${textareaField("Applicable roles", "applicable_roles", (record.applicable_roles || []).join("\n"), { rows: 2 })}
      ${textareaField("Criteria", "criteria", criteriaToText(record.criteria || []), {
        rows: 4,
        placeholder: "id | label | description | required",
      })}
      ${checkboxField("Archived", "archived", Boolean(record.archived))}
    `;
  }
  return "";
}

function editPayloadForForm(type, form) {
  const data = formData(form);
  if (type === "team") {
    return {
      name: text(data.name),
      operator_communication_target_id: text(data.operator_communication_target_id) || null,
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
      role: text(data.role),
      session_id: text(data.session_id),
      reports_to_agent_id: text(data.reports_to_agent_id) || null,
      display_name: text(data.display_name) || null,
      ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
      status: text(data.status),
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
      applicable_roles: splitList(data.applicable_roles),
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

function numberField(label, name, value, { min = null, required = false } = {}) {
  return `<label>${escapeHtml(label)}<input name="${escapeAttr(name)}" type="number" value="${escapeAttr(value)}"${min !== null ? ` min="${escapeAttr(min)}"` : ""}${required ? " required" : ""} /></label>`;
}

function textareaField(label, name, value, { rows = 3, required = false, placeholder = "" } = {}) {
  return `<label>${escapeHtml(label)}<textarea name="${escapeAttr(name)}" rows="${escapeAttr(rows)}"${required ? " required" : ""}${placeholder ? ` placeholder="${escapeAttr(placeholder)}"` : ""}>${escapeHtml(value)}</textarea></label>`;
}

function selectField(label, name, optionsKey, value, { required = false, placeholder = "Default" } = {}) {
  return `<label>${escapeHtml(label)}<select name="${escapeAttr(name)}" data-options="${escapeAttr(optionsKey)}" data-current-value="${escapeAttr(value)}" data-placeholder="${escapeAttr(placeholder)}"${required ? " required" : ""}></select></label>`;
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
  return Object.fromEntries(new FormData(form).entries());
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
  elements.statusMessage.textContent = message;
  elements.statusMessage.className = `status-message ${level || ""}`.trim();
}

function setTimelineStatus(message, level) {
  if (!elements.timelineStatus) {
    return;
  }
  elements.timelineStatus.textContent = message;
  elements.timelineStatus.className = `timeline-status ${level || ""}`.trim();
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
      agentLabels.set(agent.id, `${agent.id} - ${agent.role}`);
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
  return text(agent?.display_name) || text(agent?.name) || text(agent?.id);
}

function numberOrZero(value) {
  const parsed = Number.parseInt(text(value), 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function splitList(value) {
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

function setAutogeneratedValue(field, value) {
  const generated = uniqueSuggestedId(value);
  field.value = generated;
  field.dataset.autogenerated = "true";
  field.dataset.lastGenerated = generated;
}

function joinIdParts(parts) {
  return (parts || [])
    .map((part) => slugify(part))
    .filter(Boolean)
    .join("-");
}

function slugify(value) {
  return text(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function uniqueSuggestedId(value) {
  const base = slugify(value);
  if (!base) {
    return "";
  }
  const existing = new Set([
    ...Object.keys(state.teams || {}),
    ...Object.keys(state.projects || {}),
    ...Object.keys(state.agents || {}),
    ...Object.keys(state.tasks || {}),
    ...Object.keys(state.review_profiles || {}),
  ]);
  if (!existing.has(base)) {
    return base;
  }
  let index = 2;
  while (existing.has(`${base}-${index}`)) {
    index += 1;
  }
  return `${base}-${index}`;
}
