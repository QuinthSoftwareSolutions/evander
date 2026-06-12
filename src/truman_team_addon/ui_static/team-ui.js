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
const terminalTaskStates = new Set(["finished", "accepted", "rejected"]);
const terminalRecordStates = new Set(["accepted", "rejected"]);

let state = emptyState();
let runtimeFilter = null;
let runtimeFilterTouched = false;
let templates = [];
let timelineState = emptyTimelineState();
let timelineLoading = false;
let timelineRequestId = 0;

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
  refreshButton: document.querySelector("#refresh-button"),
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

document.addEventListener("DOMContentLoaded", () => {
  bindForms();
  bindRuntimeFilters();
  bindDeliveryFilters();
  elements.templateSelect?.addEventListener("change", () => {
    renderTemplatePreview();
  });
  bindTimelineFilters();
  elements.refreshButton?.addEventListener("click", () => {
    void loadState({ preserveForms: true });
  });
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
  const activeTeams = teams.filter((team) => !team.archived);
  const projects = values(state.projects);
  const agents = values(state.agents).filter((agent) => agent.status !== "archived");
  const tasks = values(state.tasks);
  const deliverables = values(state.deliverables);
  const reviews = values(state.review_requests);
  const reviewProfiles = values(state.review_profiles);
  const ephemeral = values(state.ephemeral_assignments);

  const metrics = [
    ["Teams", teams.length],
    ["Projects", projects.filter((project) => project.status !== "archived").length],
    ["Agents", agents.length],
    ["Open tasks", tasks.filter((task) => !terminalTaskStates.has(task.state)).length],
    ["Pending reviews", reviews.filter((review) => !terminalRecordStates.has(review.state)).length],
    ["Review profiles", reviewProfiles.filter((profile) => !profile.archived).length],
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
        : !activeTeams.length
          ? `<p class="empty-state summary-empty-state">No active team.</p>`
          : "",
    );
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
  return [...agents].sort(byId);
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
  const attention = runtimeAttentionForFilter(runtime.attention || [], runtimeFilter);
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
  const activeTeams = teams.filter((team) => !team.archived);
  if (!teams.length) {
    elements.hierarchyView.innerHTML = `<p class="empty-state">No teams registered.</p>`;
    return;
  }
  if (!activeTeams.length) {
    elements.hierarchyView.innerHTML = `<p class="empty-state">No active team.</p>`;
    return;
  }

  elements.hierarchyView.innerHTML = activeTeams
    .map((team) => {
      const hierarchy = teamHierarchyGraph(team);
      return `
        <article class="hierarchy-team">
          <div class="hierarchy-team-header">
            <div>
              <p class="section-label">${escapeHtml(team.id)}</p>
              <h3>${escapeHtml(team.name)}</h3>
            </div>
            ${team.archived ? `<span class="state-pill archived">archived</span>` : ""}
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
  const agents = values(state.agents).sort(byId);
  if (!agents.length) {
    elements.agentsView.innerHTML = `<p class="empty-state">No agents registered.</p>`;
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
                <span class="state-pill ${escapeHtml(details.status)}">${escapeHtml(details.status)}</span>
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
    if (manager && text(manager.team_id) === teamId) {
      const root = buildNode(managerId, { path: new Set(), parentId: null });
      if (root) {
        roots.push(root);
      }
    } else {
      roots.push({
        kind: "missing-manager",
        id: managerId,
        teamId,
        label: `Missing manager ${managerId}`,
      });
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
  const projects = values(state.projects).sort(byId);
  if (!projects.length) {
    elements.projectsView.innerHTML = `<p class="empty-state">No projects registered.</p>`;
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
            <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
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
                <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(task.state)}</span>
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
  const profiles = values(state.review_profiles).sort(byId);
  if (!profiles.length) {
    elements.reviewProfilesView.innerHTML = `<p class="empty-state">No review profiles.</p>`;
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
                <span class="state-pill ${profile.archived ? "blocked" : "accepted"}">${profile.archived ? "archived" : "active"}</span>
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
      <div class="meta-line">Git is optional. If used, provide a host repo path and exactly one bare repo field.</div>
      <div class="meta-line">Optional fields: host repo path, bare repo name, bare repo path, base branch, canonical origin URL, provider, canonical release branch prefix.</div>
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
  const actorLine = [
    entry.actor_agent_id ? `Actor ${entry.actor_agent_id}` : "Actor unknown",
    entry.team_id ? `team ${entry.team_id}` : "",
    entry.project_id ? `project ${entry.project_id}` : "",
  ]
    .filter(Boolean)
    .join(" - ");
  const relatedAgents = [
    entry.actor_agent_id ? `actor ${entry.actor_agent_id}` : null,
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

function refreshSelects() {
  const teams = values(state.teams).sort(byId).map((team) => [team.id, `${team.name} (${team.id})`]);
  const projects = values(state.projects).sort(byId).map((project) => [project.id, `${project.name} (${project.id})`]);
  const agents = values(state.agents).sort(byId).map((agent) => [agent.id, `${agent.id} - ${agent.role}`]);
  const tasks = values(state.tasks).sort(byPriority).map((task) => [task.id, `${task.title} (${task.id})`]);
  const deliverables = values(state.deliverables).sort(byId).map((deliverable) => [deliverable.id, `${deliverable.summary} (${deliverable.id})`]);
  const reviewRequests = values(state.review_requests)
    .sort(byId)
    .map((review) => [review.id, `${review.id} - ${review.state}`]);
  const reviewProfiles = values(state.review_profiles)
    .filter((profile) => !profile.archived)
    .sort(byId)
    .map((profile) => [profile.id, `${profile.name} (${profile.id})`]);
  const releasePackages = values(state.release_packages)
    .sort(byId)
    .map((release) => [release.id, `${release.id} - ${release.state}`]);
  const timelineOptions = buildTimelineSelectOptions();
  const options = {
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
    "decision-states": decisionStates.map((item) => [item, item]),
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

  document.querySelectorAll("select[data-options]").forEach((select) => {
    const key = select.dataset.options;
    fillSelect(select, options[key] || []);
  });

  const updateTaskForm = document.querySelector("#update-task-form");
  if (updateTaskForm) {
    syncTaskUpdateForm(updateTaskForm, { keepEditedValues: true });
  }
}

function fillSelect(select, options) {
  const currentValue = select.value;
  const placeholder = select.dataset.placeholder || (select.required ? "Select" : "Default");
  select.innerHTML = `<option value="">${placeholder}</option>${options
    .map(([value, label]) => `<option value="${escapeAttr(value)}">${escapeHtml(label)}</option>`)
    .join("")}`;
  if (options.some(([value]) => value === currentValue)) {
    select.value = currentValue;
  }
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
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    state = normalizeState(await response.json());
    if (resetOnSuccess) {
      form.reset();
    }
    render();
    setStatus(`Saved revision ${state.revision}.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Save failed.", "error");
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
      drafts[formId] = formData(form);
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
