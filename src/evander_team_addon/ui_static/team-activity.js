import { renderIdentifierText } from "./team-identifiers.js";

function emptyActivityState() {
  return {
    total_count: 0,
    limit: 100,
    offset: 0,
    has_more: false,
    retention_days: 30,
    events: [],
    loading: false,
    error: null,
  };
}

export function createTeamActivityController({
  apiBase,
  documentRoot,
  elements,
  getState,
  isActive,
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
  pageSize = 100,
}) {
  let activityState = emptyActivityState();
  let requestId = 0;

  function bind() {
    documentRoot.addEventListener("change", (event) => {
      if (event.target?.closest?.("[data-activity-filter]")) {
        void load({ offset: 0 });
      }
    });
    documentRoot.addEventListener("submit", (event) => {
      if (!event.target?.closest?.("[data-activity-search-form]")) {
        return;
      }
      event.preventDefault();
      void load({ offset: 0 });
    });
    documentRoot.addEventListener("click", (event) => {
      if (event.target?.closest?.("[data-activity-refresh]")) {
        event.preventDefault();
        void load({ offset: activityState.offset || 0 });
        return;
      }
      const page = event.target?.closest?.("[data-activity-page]");
      if (!page) {
        return;
      }
      event.preventDefault();
      const delta = page.dataset.activityPage === "next" ? activityState.limit : -activityState.limit;
      void load({ offset: Math.max(0, Number(activityState.offset || 0) + delta) });
    });
  }

  async function load({ offset = 0 } = {}) {
    const activeRequest = ++requestId;
    activityState = { ...activityState, loading: true, error: null };
    if (isActive()) {
      render();
    }
    try {
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String(Math.max(0, Number(offset) || 0)),
      });
      const filters = {
        team_id: elements.activityTeamFilter?.value,
        agent_id: elements.activityAgentFilter?.value,
        status: elements.activityStatusFilter?.value,
        dispatch_id: elements.activityDispatchFilter?.value,
        q: elements.activitySearchInput?.value,
      };
      Object.entries(filters).forEach(([key, value]) => {
        const normalized = text(value);
        if (normalized) {
          params.set(key, normalized);
        }
      });
      const response = await teamFetch(`${apiBase}/dispatch-events?${params.toString()}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      if (activeRequest !== requestId) {
        return payload;
      }
      activityState = {
        ...emptyActivityState(),
        ...payload,
        events: Array.isArray(payload.events) ? payload.events : [],
        loading: false,
      };
      if (isActive()) {
        render();
      }
      return payload;
    } catch (error) {
      if (activeRequest !== requestId) {
        return null;
      }
      activityState = {
        ...activityState,
        loading: false,
        error: error instanceof Error ? error.message : "Dispatch activity failed to load.",
      };
      if (isActive()) {
        render();
      }
      return null;
    }
  }

  function render() {
    renderFilters();
    if (elements.activityStatus) {
      elements.activityStatus.className = `mail-status${activityState.error ? " error" : ""}`;
      elements.activityStatus.textContent = activityState.loading
        ? "Loading activity…"
        : activityState.error
          ? activityState.error
          : `${activityState.total_count} events · retained ${activityState.retention_days} days`;
    }
    if (elements.activityList) {
      elements.activityList.innerHTML = activityState.events.length
        ? activityState.events.map(renderEvent).join("")
        : `<div class="mail-empty">${activityState.loading ? "Loading…" : "No matching dispatch activity."}</div>`;
    }
    if (elements.activityPagination) {
      const start = activityState.total_count ? activityState.offset + 1 : 0;
      const end = Math.min(activityState.total_count, activityState.offset + activityState.events.length);
      elements.activityPagination.innerHTML = `
        <button type="button" data-activity-page="previous" ${activityState.offset <= 0 ? "disabled" : ""}>Previous</button>
        <span>${start}–${end} of ${activityState.total_count}</span>
        <button type="button" data-activity-page="next" ${activityState.has_more ? "" : "disabled"}>Next</button>
      `;
    }
  }

  function renderFilters() {
    const state = getState();
    const selectedTeam = text(elements.activityTeamFilter?.value);
    const teams = values(state.teams)
      .sort((left, right) => String(left.name || left.id).localeCompare(String(right.name || right.id)) || byId(left, right))
      .map((team) => [team.id, `${team.name || team.id} (${team.id})`]);
    if (elements.activityTeamFilter) {
      elements.activityTeamFilter.innerHTML = renderSelectOptions(teams, {
        selectedValue: selectedTeam,
        placeholder: "All teams",
      });
    }
    const selectedAgent = text(elements.activityAgentFilter?.value);
    const agents = values(state.agents)
      .filter((agent) => !selectedTeam || text(agent.team_id) === selectedTeam)
      .sort((left, right) => agentLabel(left).localeCompare(agentLabel(right)) || byId(left, right))
      .map((agent) => [agent.id, `${agentLabel(agent)} (${agent.id})`]);
    if (elements.activityAgentFilter) {
      elements.activityAgentFilter.innerHTML = renderSelectOptions(agents, {
        selectedValue: selectedAgent,
        placeholder: "All agents",
      });
    }
  }

  function renderEvent(event) {
    const statusClass = event.status === "failure" ? "error" : "success";
    const tool = event.tool_name ? `<code>${escapeHtml(event.tool_name)}</code>` : humanizeToken(event.kind);
    const error = event.error_code
      ? `<div class="meta-line">${escapeHtml(event.error_code)}${event.correction_context_summary ? ` · ${renderIdentifierText(event.correction_context_summary)}` : ""}</div>`
      : "";
    return `
      <article class="mail-message activity-event ${escapeAttr(statusClass)}">
        <div class="mail-message-heading">
          <strong>${escapeHtml(event.agent_display_name || event.agent_id || "System")}</strong>
          <span class="record-state ${escapeAttr(statusClass)}">${escapeHtml(event.status || "")}</span>
        </div>
        <div>${tool} · ${renderIdentifierText(event.summary || "")}</div>
        <div class="meta-line">${escapeHtml(event.occurred_at || "")} · ${escapeHtml(event.dispatch_id || "no dispatch")}</div>
        ${error}
      </article>
    `;
  }

  return { bind, load, render };
}
