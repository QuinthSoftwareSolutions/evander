// Workspace dashboard metrics and attention pagination.
import {
  byId,
  escapeAttr,
  escapeHtml,
  formatUsd,
  humanizeToken,
  prioritySortValue,
  taskStateLabel,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { renderIdentifierText } from "./team-identifiers.js";
import {
  activeCaseStatuses,
  deliveryStatusLabels,
  deliveryStatusOrder,
  inboxItemLabels,
  terminalRecordStates,
  terminalTaskStates,
  workspaceAttentionPageSize,
} from "./team-ui-config.js";

export function createTeamWorkspaceController(peers) {

  let workspaceAttentionPage = 1;

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

  function renderMemoryView() {
    const allMemories = values(peers.app.state.memories).sort(compareMemories);
    const visibleMemories = allMemories.filter(isVisibleMemory);
    if (!allMemories.length) {
      peers.app.renderIntoActiveTargets("[data-memory-view]", `<p class="empty-state">No Team memories recorded.</p>`);
      return;
    }
    if (!visibleMemories.length) {
      peers.app.renderIntoActiveTargets("[data-memory-view]", `<p class="empty-state">No visible Team memories.</p>`);
      return;
    }
    peers.app.renderIntoActiveTargets(
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
      .map(([teamId, items]) => ({ teamId, team: peers.app.state.teams[teamId] || null, memories: items }))
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
    const project = memory.project_id ? peers.app.state.projects[memory.project_id] : null;
    const agent = memory.agent_id ? peers.app.state.agents[memory.agent_id] : null;
    const metaParts = [
      memory.id,
      humanizeToken(memory.kind || "other"),
      humanizeToken(memory.scope || "team_global"),
      project ? project.name : memory.project_id ? `project ${memory.project_id}` : "Team-wide",
      memory.codebase_id ? `codebase ${memory.codebase_id}` : "",
      agent ? `agent ${peers.agents.agentLabel(agent)}` : memory.agent_id ? `agent ${memory.agent_id}` : "",
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
        ${memory.summary ? `<div class="meta-line">${renderIdentifierText(memory.summary)}</div>` : ""}
        ${renderMemoryTags(memory)}
        ${refs.length ? `<div class="meta-line">${escapeHtml(refs.join(" - "))}</div>` : ""}
        ${memory.updated_at ? `<div class="meta-line">Updated ${escapeHtml(peers.app.formatTimelineTimestamp(memory.updated_at))}</div>` : ""}
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
    if (!peers.app.showArchived && text(memory.status || "active") === "archived") {
      return false;
    }
    const team = memory.team_id ? peers.app.state.teams[memory.team_id] : null;
    if (team && !peers.app.isVisibleTeam(team)) {
      return false;
    }
    const project = memory.project_id ? peers.app.state.projects[memory.project_id] : null;
    if (project && !peers.app.isVisibleProject(project)) {
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
      peers.disclosures.rememberOpenTeamDisclosures();
      target.innerHTML = html;
    }
  }

  function buildWorkspaceDashboardModel() {
    const visibleTeams = values(peers.app.state.teams).filter(peers.app.isVisibleTeam).sort(peers.people.comparePeopleTeams);
    const visibleTeamIds = new Set(visibleTeams.map((team) => text(team.id)));
    const visibleProjects = values(peers.app.state.projects)
      .filter(peers.app.isVisibleProject)
      .filter((project) => !project.team_id || visibleTeamIds.has(text(project.team_id)))
      .sort(peers.people.compareTeamProjects);
    const visibleProjectIds = new Set(visibleProjects.map((project) => text(project.id)));
    const visibleAgents = values(peers.app.state.agents)
      .filter(peers.app.isVisibleAgent)
      .filter((agent) => !agent.team_id || visibleTeamIds.has(text(agent.team_id)))
      .sort(peers.people.comparePeopleAgents);
    const visibleAgentIds = new Set(visibleAgents.map((agent) => text(agent.id)));
    const openTasks = values(peers.app.state.tasks)
      .filter((task) => !terminalTaskStates.has(task.state))
      .filter((task) => workspaceTaskIsVisible(task, visibleProjectIds))
      .sort(peers.planning.comparePlanningTasks);
    const blockedTasks = openTasks.filter(peers.agents.isTaskWaitingOrBlocked);
    const runtime = peers.app.state.runtime_operations || { agents: [], attention: [] };
    const runtimeInboxIds = new Set(
      (runtime.attention || []).map((row) => text(row.inbox_item_id)).filter(Boolean),
    );
    const pendingInbox = values(peers.app.state.inbox)
      .filter((item) => text(item.state) !== "handled")
      .filter((item) => !runtimeInboxIds.has(text(item.id)))
      .filter((item) => visibleAgentIds.has(text(item.recipient_agent_id)))
      .sort(compareWorkspaceInboxItems);
    const attentionInbox = pendingInbox.filter(workspaceInboxItemNeedsAttention);
    const pendingReviews = values(peers.app.state.review_requests)
      .filter((review) => !terminalRecordStates.has(review.state))
      .filter(workspaceReviewNeedsAttention)
      .filter((review) => workspaceReviewIsVisible(review, visibleProjectIds))
      .sort(compareWorkspaceReviews);
    const pendingGovernanceAgenda = values(peers.app.state.governance_agenda)
      .filter((item) => text(item.state || "pending") === "pending")
      .filter((item) => workspaceGovernanceAgendaIsVisible(item, visibleTeamIds, visibleProjectIds))
      .sort(compareWorkspaceGovernanceAgenda);
    const runtimeAttention = (runtime.attention || [])
      .filter(peers.app.isVisibleRuntimeRow)
      .filter((row) => peers.operations.runtimeAttentionGroup(row) !== "retained_history")
      .filter(workspaceRuntimeAttentionNeedsAttention)
      .sort(peers.app.runtimeSortRows);
    const dueAgents = peers.operations.dueLeasedAgentsForFilter(runtime.agents || []);
    const deliveryRows = peers.delivery.buildReviewDeliveryRows();
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
        detail: peers.costs.costState.error ? "cost data unavailable" : "team caps and portfolio roll-ups",
        view: "governance",
        tone: model.costAttention.length || peers.costs.costState.error ? "warning" : "ok",
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
        ${model.generatedAt ? `<div class="meta-line">Updated ${escapeHtml(peers.app.formatTimelineTimestamp(model.generatedAt))}</div>` : ""}
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
    const teamAgents = values(peers.app.state.agents).filter((agent) => peers.app.isVisibleAgent(agent) && text(agent.team_id) === text(team.id));
    const teamProjectIds = new Set(
      values(peers.app.state.projects)
        .filter((project) => peers.app.isVisibleProject(project) && text(project.team_id) === text(team.id))
        .map((project) => text(project.id)),
    );
    const teamTasks = values(peers.app.state.tasks).filter((task) => teamProjectIds.has(text(task.project_id)));
    const openTasks = teamTasks.filter((task) => !terminalTaskStates.has(task.state));
    const blockedTasks = openTasks.filter(peers.agents.isTaskWaitingOrBlocked);
    const inboxCount = values(peers.app.state.inbox).filter((item) => {
      const agent = peers.app.state.agents[text(item.recipient_agent_id)];
      return (
        text(item.state) !== "handled" &&
        peers.agents.pendingInboxItemIsCurrent(item) &&
        agent &&
        text(agent.team_id) === text(team.id)
      );
    }).length;
    const dueCount = (peers.app.state.runtime_operations?.agents || []).filter((row) => {
      return text(row.team_id) === text(team.id) && peers.operations.isDueOrLeasedAgent(row);
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
    const openCases = values(peers.app.state.cases).filter(
      (item) =>
        peers.app.isVisibleCase(item) &&
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
    const deliverable = peers.app.state.deliverables[text(review.deliverable_id)];
    const task = deliverable ? peers.app.state.tasks[text(deliverable.task_id)] : null;
    return !task || workspaceTaskIsVisible(task, visibleProjectIds);
  }

  function isCurrentDeliverable(deliverable) {
    return !text(deliverable?.superseded_by_deliverable_id);
  }

  function isCurrentReviewRequest(review) {
    return !review?.retired_at && !text(review?.superseded_by_review_request_id);
  }

  function workspaceReviewNeedsAttention(review) {
    const deliverable = peers.app.state.deliverables?.[text(review.deliverable_id)];
    return isCurrentReviewRequest(review) && isCurrentDeliverable(deliverable);
  }

  function workspaceGovernanceAgendaIsVisible(item, visibleTeamIds, visibleProjectIds) {
    const group = peers.app.state.governance_groups[text(item.governance_group_id)];
    if (group && !peers.app.isVisibleGovernanceGroup(group)) {
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
    Object.entries(peers.costs.costState.team_costs || {}).forEach(([teamId, cost]) => {
      if (cost?.cost_limit_reached && visibleTeamIds.has(text(teamId))) {
        const team = peers.app.state.teams[text(teamId)];
        rows.push({
          title: `${team?.name || teamId} cost cap reached`,
          meta: `${formatUsd(cost.total_cost ?? cost.estimated_cost)} cost`,
          severity: "warning",
          view: "operations",
          action: "Open operations",
        });
      }
    });
    Object.entries(peers.costs.costState.project_costs || {}).forEach(([projectId, cost]) => {
      if (cost?.cost_limit_reached && visibleProjectIds.has(text(projectId))) {
        const project = peers.app.state.projects[text(projectId)];
        rows.push({
          title: `${project?.name || projectId} cost cap reached`,
          meta: `${formatUsd(cost.total_cost ?? cost.estimated_cost)} cost`,
          severity: "warning",
          view: "projects",
          action: "Open projects",
        });
      }
    });
    Object.entries(peers.costs.costState.governance_group_costs || {}).forEach(([groupId, cost]) => {
      const group = peers.app.state.governance_groups[text(groupId)];
      if (!group || !peers.app.isVisibleGovernanceGroup(group)) {
        return;
      }
      if (cost?.cost_limit_reached || (cost?.member_team_cap_reached_ids || []).length) {
        const memberTeamCount = peers.costs.governanceCostMemberTeamCount(cost);
        rows.push({
          title: `${group.name || groupId} portfolio cost attention`,
          meta: cost?.cost_limit_reached
            ? `${formatUsd(cost.total_cost ?? cost.estimated_cost)} governed roll-up across ${memberTeamCount} member team${memberTeamCount === 1 ? "" : "s"}`
            : `${(cost.member_team_cap_reached_ids || []).length} member cap${(cost.member_team_cap_reached_ids || []).length === 1 ? "" : "s"} reached`,
          severity: "warning",
          view: "governance",
          action: "Open governance",
        });
      }
    });
    if (peers.costs.costState.error) {
      rows.push({
        title: "Cost data unavailable",
        meta: peers.costs.costState.error,
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
      const project = peers.app.state.projects[text(task.project_id)];
      const owner = peers.app.state.agents[text(task.owner_agent_id)];
      items.push({
        title: task.title || task.id,
        meta: [
          taskStateLabel(task),
          project ? project.name || project.id : text(task.project_id),
          peers.board.taskAssignmentLabel(task),
        ].filter(Boolean).join(" - "),
        severity: task.state === "blocked" ? "warning" : "info",
        view: "task-board",
        action: "Open board",
      });
    });
    pendingReviews.forEach((review) => {
      const deliverable = peers.app.state.deliverables[text(review.deliverable_id)];
      const reviewer = peers.app.state.agents[text(review.reviewer_agent_id)];
      items.push({
        title: `Review ${review.id}`,
        meta: [
          humanizeToken(review.state || "pending"),
          deliverable ? deliverable.summary || deliverable.id : text(review.deliverable_id),
          reviewer ? peers.agents.agentLabel(reviewer) : text(review.reviewer_agent_id),
        ].filter(Boolean).join(" - "),
        severity: review.state === "blocked" ? "warning" : "info",
        view: "delivery",
        action: "Open delivery",
      });
    });
    pendingInbox.forEach((item) => {
      const recipient = peers.app.state.agents[text(item.recipient_agent_id)];
      items.push({
        title: inboxItemLabels[item.item_type] || humanizeToken(item.item_type || "inbox item"),
        meta: [
          peers.agents.inboxItemDetail(item) || item.id,
          recipient ? peers.agents.agentLabel(recipient) : text(item.recipient_agent_id),
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
    return !inboxItemId || workspaceInboxItemNeedsAttention(peers.app.state.inbox?.[inboxItemId] || {});
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
      peers.operations.runtimeSeverityRank(left.severity) - peers.operations.runtimeSeverityRank(right.severity) ||
      text(left.title).localeCompare(text(right.title)) ||
      text(left.meta).localeCompare(text(right.meta))
    );
  }

  return {
    bindWorkspaceAttentionPagination,
    compareWorkspaceInboxItems,
    isCurrentDeliverable,
    isCurrentReviewRequest,
    renderMemoryView,
    renderWorkspaceDashboard,
  };
}
