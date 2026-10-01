// Case filters, durable history, and task links.
import {
  byId,
  escapeAttr,
  escapeHtml,
  humanizeToken,
  prioritySortValue,
  taskStateLabel,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { activeCaseStatuses, casePageSize, caseStatuses, terminalCaseStatuses } from "./team-ui-config.js";
import { renderIdentifierText } from "./team-identifiers.js";

export function createTeamCasesController(peers) {

  let caseShowHistory = false;

  let caseQuery = "";

  let caseStatusFilter = "active";

  const caseTeamPages = new Map();

  function renderCases() {
    const allCases = values(peers.app.state.cases).sort(compareCases);
    const visibleCases = allCases.filter(peers.app.isVisibleCase);
    if (!allCases.length) {
      peers.app.renderIntoActiveTargets("[data-cases-view]", `<p class="empty-state">No cases registered.</p>`);
      return;
    }
    if (!visibleCases.length) {
      peers.app.renderIntoActiveTargets("[data-cases-view]", `<p class="empty-state">No visible cases.</p>`);
      return;
    }
    const grouped = groupCasesByTeam(visibleCases);
    peers.app.renderIntoActiveTargets(
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
      .map(([teamId, cases]) => ({ teamId, team: peers.app.state.teams[teamId] || null, cases }))
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
    const project = item.project_id ? peers.app.state.projects[item.project_id] : null;
    const owner = item.owner_agent_id ? peers.app.state.agents[item.owner_agent_id] : null;
    const taskLabels = uniqueTextList(item.task_ids).flatMap((taskId) => {
      const task = peers.app.state.tasks[taskId];
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
      owner ? peers.agents.agentLabel(owner) : item.owner_agent_id,
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
    const project = item.project_id ? peers.app.state.projects[item.project_id] : null;
    const owner = item.owner_agent_id ? peers.app.state.agents[item.owner_agent_id] : null;
    const metaParts = [
      item.id,
      item.priority ? `${humanizeToken(item.priority)} priority` : "",
      project ? project.name : item.project_id ? `project ${item.project_id}` : "Team-wide",
      owner ? `owner ${peers.agents.agentLabel(owner)}` : item.owner_agent_id ? `owner ${item.owner_agent_id}` : "No owner",
    ].filter(Boolean);
    const source = [item.source, item.source_ref].filter(Boolean).join(" ");
    return `
      <li class="task-item" data-case-id="${escapeAttr(item.id)}">
        <div class="task-line">
          <strong>${escapeHtml(item.title)}</strong>
          <div class="record-actions">
            <span class="state-pill ${escapeHtml(item.status || "open")}">${escapeHtml(humanizeToken(item.status || "open"))}</span>
            ${peers.access.editButton("case", item.id)}
            <button type="button" class="record-action secondary" data-open-case-context="${escapeAttr(item.id)}">Context</button>
            ${item.project_id ? `<button type="button" class="record-action secondary" data-open-case-board="${escapeAttr(item.id)}">View task board</button>` : ""}
            ${project?.git && project.release_policy?.reintegration?.enabled !== false ? `<button type="button" class="record-action secondary" data-reintegrate-case-id="${escapeAttr(item.id)}">Reintegrate accepted work</button>` : ""}
          </div>
        </div>
        <div class="meta-line">${renderIdentifierText(metaParts.join(" - "))}</div>
        ${source ? `<div class="meta-line">Source ${renderIdentifierText(source)}</div>` : ""}
        ${renderCaseTags(item)}
        ${renderCaseTaskLinks(item)}
        ${item.description ? `<div class="meta-line">${renderIdentifierText(item.description)}</div>` : ""}
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
      const task = peers.app.state.tasks[taskId];
      return task ? `${task.title} (${task.id}, ${taskStateLabel(task)})` : taskId;
    });
    return `<div class="meta-line">Tasks ${escapeHtml(labels.join(" - "))}</div>`;
  }

  function caseLabel(item) {
    const team = item?.team_id ? peers.app.state.teams[item.team_id] : null;
    const project = item?.project_id ? peers.app.state.projects[item.project_id] : null;
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

  return {
    bindCaseFilters,
    caseLabel,
    compareCases,
    renderCases,
  };
}
