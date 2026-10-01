// Runtime filters, attention grouping, and operation presentation.
import {
  byId,
  escapeAttr,
  escapeHtml,
  humanizeToken,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import {
  blockedWorkKinds,
  ephemeralWorkKinds,
  failedAttentionKinds,
  runtimeAttentionAgeChoices,
  runtimeAttentionGroups,
  runtimeAttentionPageSize,
  runtimeFilterChoices,
  runtimeFilterLabels,
} from "./team-ui-config.js";
import { elements } from "./team-ui-dom.js";

export function createTeamOperationsController(peers) {

  let runtimeFilter = null;

  let runtimeFilterTouched = false;

  const runtimeAttentionFilters = {
    kind: "all",
    age: "all",
    team: "all",
    project: "all",
    agent: "all",
  };

  let runtimeAttentionShowHistory = false;

  const runtimeAttentionGroupPages = new Map();

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
      return text(peers.app.state.blockers?.[row.blocker_id || row.id]?.created_at);
    }
    if (["blocked_task", "waiting_dependency_task"].includes(kind)) {
      const task = peers.app.state.tasks?.[row.task_id || row.id];
      return text(task?.state_entered_at || task?.updated_at || task?.created_at);
    }
    if (["pending_inbox_item", "pending_escalation"].includes(kind)) {
      return text(peers.app.state.inbox?.[row.inbox_item_id || row.id]?.created_at);
    }
    if (kind === "failed_action_record") {
      return text(peers.app.state.action_records?.[row.action_record_id || row.id]?.created_at);
    }
    if (kind === "failed_git_promotion") {
      const promotion = peers.app.state.git_promotions?.[row.promotion_id || row.id];
      return text(promotion?.updated_at || promotion?.created_at);
    }
    if (kind === "blocked_release_package") {
      const release = peers.app.state.release_packages?.[row.release_package_id || row.id];
      return text(release?.updated_at || release?.created_at);
    }
    if (["failed_ephemeral_assignment", "cleanup_pending_ephemeral_assignment"].includes(kind)) {
      const assignment = peers.app.state.ephemeral_assignments?.[row.assignment_id || row.id];
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
    const generated = Date.parse(text(peers.app.state.runtime_operations?.generated_at));
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
    return [...agents].filter(peers.app.isVisibleRuntimeRow).sort(byId);
  }

  function dueLeasedAgentsForFilter(agents) {
    return runtimeAgentsForFilter(agents).filter((agent) => isDueOrLeasedAgent(agent));
  }

  function runtimeAttentionForFilter(attention, filter) {
    const sorted = [...attention].sort(peers.app.runtimeSortRows);
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
      return peers.app.state.teams?.[value]?.name || value;
    }
    if (field === "project") {
      return peers.app.state.projects?.[value]?.name || value;
    }
    if (field === "agent") {
      const agent = peers.app.state.agents?.[value];
      return agent ? peers.agents.agentLabel(agent) : value;
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
    const agent = agentId ? peers.app.state.agents?.[agentId] : null;
    return {
      id: agentId,
      label: agent ? peers.agents.agentLabel(agent) : agentId || "Operator",
    };
  }

  function runtimeAttentionWhere(row) {
    const projectId = text(row.project_id || row.links?.project_ids?.[0]);
    const taskId = text(row.task_id || row.links?.task_ids?.[0]);
    const deliverableId = text(row.deliverable_id || row.links?.deliverable_ids?.[0]);
    const project = projectId ? peers.app.state.projects?.[projectId] : null;
    const task = taskId ? peers.app.state.tasks?.[taskId] : null;
    const deliverable = deliverableId ? peers.app.state.deliverables?.[deliverableId] : null;
    return [
      project?.name,
      task?.title,
      deliverable ? peers.reviews.deliverableDisplaySubject(deliverable) : "",
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
        <time datetime="${escapeAttr(timestamp)}">${escapeHtml(peers.app.formatTimelineTimestamp(timestamp))}</time>
        · ${escapeHtml(runtimeAttentionAgeLabel(row))}
      </span>
    `;
  }

  function runtimeAttentionDisplayTitle(row) {
    const kind = text(row.kind);
    if (kind === "failed_action_record") {
      const action = peers.app.state.action_records?.[row.action_record_id || row.id]?.action || {};
      const actionType = text(action.type || action.kind);
      return actionType ? `${humanizeToken(actionType)} action failed` : "Agent action failed";
    }
    if (kind === "failed_git_promotion") {
      const promotion = peers.app.state.git_promotions?.[row.promotion_id || row.id];
      return `${humanizeToken(promotion?.promotion_kind || row.promotion_kind || "Git")} promotion failed`;
    }
    if (kind === "expired_agent_session") {
      return `Session expired for ${runtimeAttentionOwner(row).label}`;
    }
    if (kind === "blocked_release_package") {
      const release = peers.app.state.release_packages?.[row.release_package_id || row.id];
      return release ? `${peers.releases.releaseDisplayLabel(release)} is blocked` : "Release is blocked";
    }
    if (["failed_ephemeral_assignment", "cleanup_pending_ephemeral_assignment"].includes(kind)) {
      const assignment = peers.app.state.ephemeral_assignments?.[row.assignment_id || row.id];
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
      const item = peers.app.state.inbox?.[row.inbox_item_id || row.id];
      return text(item ? peers.agents.inboxItemDetail(item) : row.detail) || runtimeAttentionDisplayTitle(row);
    }
    if (kind === "team_blocker") {
      const blocker = peers.app.state.blockers?.[row.blocker_id || row.id];
      return text(blocker?.details || blocker?.summary) || runtimeAttentionDisplayTitle(row);
    }
    if (["blocked_task", "waiting_dependency_task"].includes(kind)) {
      const task = peers.app.state.tasks?.[row.task_id || row.id];
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
      const blocker = peers.app.state.blockers?.[row.blocker_id];
      if (
        text(blocker?.source_kind) === "release_package" &&
        peers.recovery.releaseRecoveryTaskForBlocker(blocker)
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
      const project = peers.app.state.projects?.[id];
      if (project) related.push(["Project", project.name]);
    });
    uniqueTextList([row.task_id, ...(row.links?.task_ids || [])]).forEach((id) => {
      const task = peers.app.state.tasks?.[id];
      if (task) related.push(["Task", task.title]);
    });
    uniqueTextList([row.deliverable_id, ...(row.links?.deliverable_ids || [])]).forEach((id) => {
      const deliverable = peers.app.state.deliverables?.[id];
      if (deliverable) related.push(["Deliverable", peers.reviews.deliverableDisplaySubject(deliverable)]);
    });
    uniqueTextList([row.release_package_id, ...(row.links?.release_package_ids || [])]).forEach((id) => {
      const release = peers.app.state.release_packages?.[id];
      if (release) related.push(["Release", peers.releases.releaseDisplayLabel(release)]);
    });
    if (!related.length) {
      return `<p class="empty-state">No named work is linked.</p>`;
    }
    return `<dl class="runtime-related-work">${related.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
  }

  function renderRuntimeAttentionTechnicalDetails(row) {
    return peers.reviews.renderTechnicalDetails([
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
      disclosureKey: peers.disclosures.teamDisclosureKey("runtime-attention", row.id, "technical"),
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
      blockerAction = peers.recovery.renderTerminalBlockerResolveAction(
        peers.app.state.blockers?.[row.blocker_id],
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
    if (peers.app.activeTeamView === "operations") {
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
        <details ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("operations", "attention", row.id, "detail"))}>
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
    if (peers.app.activeTeamView === "operations") {
      return renderCompactRuntimeAgentRow(agent);
    }
    const status = runtimeAgentStatus(agent);
    const refs = runtimeReferenceParts(agent);
    const sessionStatus = peers.agents.normalizeAgentSessionHealth(agent);
    const sessionSummary = runtimeAgentSessionSummary(agent);
    const wakeReason = runtimeText(agent.wake_reason);
    const lastWake = runtimeText(agent.last_wake_at);
    const nextWake = runtimeText(agent.next_wake_at);
    const wakeLease = runtimeText(agent.wake_leased_until);
    const dispatchId = runtimeText(agent.active_dispatch_id);
    const contextPending = Boolean(agent.context_rotation_pending);
    const handoffPreparing = Boolean(agent.context_handoff_preparation_pending);
    const handoffPreparationStatus = runtimeText(agent.context_handoff_preparation_status);
    const contextPolicy = peers.selects.contextClearingPolicyValue(agent);
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
            ${peers.agents.renderAgentStandDownControls(agent.id, agent)}
            ${["every_wake", "work_lineage"].includes(contextPolicy) ? "" : `<button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(agent.id)}">Clear context</button>`}
          </div>
        </div>
        <div class="meta-line">${escapeHtml(peers.agents.agentRoleLabel(agent))} - ${escapeHtml(sessionSummary)}${peers.agents.agentProjectSummary(agent) ? ` - projects ${escapeHtml(peers.agents.agentProjectSummary(agent))}` : ""}</div>
        <div class="meta-line">Last wake ${escapeHtml(lastWake)} - Next wake ${escapeHtml(nextWake)} - Wake lease ${escapeHtml(wakeLease)}</div>
        <div class="meta-line">Wake reason ${escapeHtml(wakeReason)} - No progress ${escapeHtml(agent.no_progress_count ?? 0)} - Active dispatch ${escapeHtml(dispatchId)}</div>
        <div class="meta-line">${contextPolicy === "work_lineage" ? "Context routing Work lineage" : `Context clearing ${escapeHtml(peers.selects.contextClearingPolicyLabel(contextPolicy))} - ${escapeHtml(handoffPreparing ? "preparing handoff" : contextPending ? "queued" : idleRotationDue ? `idle grace until ${idleRotationDue}` : "not pending")}${contextPolicy === "every_wake" ? "" : ` - Last rotated ${escapeHtml(lastRotated)}`}${handoffPreparationStatus ? ` - Handoff ${escapeHtml(handoffPreparationStatus)}` : ""}`}</div>
        ${
          handoffSummary
            ? `<details class="runtime-handoff" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey(peers.app.activeTeamView, "runtime-agent", agent.id, "handoff"))}><summary>Handoff summary</summary><pre>${escapeHtml(handoffSummary)}</pre></details>`
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
    const sessionStatus = peers.agents.normalizeAgentSessionHealth(agent);
    const sessionSummary = runtimeAgentSessionSummary(agent);
    const wakeReason = runtimeText(agent.wake_reason);
    const lastWake = runtimeText(agent.last_wake_at);
    const nextWake = runtimeText(agent.next_wake_at);
    const wakeLease = runtimeText(agent.wake_leased_until);
    const dispatchId = runtimeText(agent.active_dispatch_id);
    const contextPending = Boolean(agent.context_rotation_pending);
    const contextPolicy = peers.selects.contextClearingPolicyValue(agent);
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
        <details ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("operations", "agent", agent.id, "detail"))}>
          <summary class="runtime-summary compact-record-summary">
            <span class="compact-record-main">
              <strong>${escapeHtml(agent.id)}</strong>
              <span>${escapeHtml(peers.agents.agentRoleLabel(agent))} - ${escapeHtml(sessionSummary)}${peers.agents.agentProjectSummary(agent) ? ` - projects ${escapeHtml(peers.agents.agentProjectSummary(agent))}` : ""}</span>
            </span>
            <span class="runtime-summary-meta">
              <span class="state-pill ${escapeHtml(status)}">${escapeHtml(status)}</span>
              <span>${escapeHtml(flags.join(" - ") || "idle")}</span>
            </span>
          </summary>
          <div class="compact-record-body">
            <div class="record-actions">
              ${peers.agents.renderAgentStandDownControls(agent.id, agent)}
              ${["every_wake", "work_lineage"].includes(contextPolicy) ? "" : `<button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(agent.id)}">Clear context</button>`}
            </div>
            <div class="meta-line">Last wake ${escapeHtml(lastWake)} - Next wake ${escapeHtml(nextWake)} - Wake lease ${escapeHtml(wakeLease)}</div>
            <div class="meta-line">Wake reason ${escapeHtml(wakeReason)} - No progress ${escapeHtml(agent.no_progress_count ?? 0)} - Active dispatch ${escapeHtml(dispatchId)}</div>
            <div class="meta-line">${contextPolicy === "work_lineage" ? "Context routing Work lineage" : `Context clearing ${escapeHtml(peers.selects.contextClearingPolicyLabel(contextPolicy))} - ${escapeHtml(contextPending ? "pending" : idleRotationDue ? `idle grace until ${idleRotationDue}` : "not pending")}${contextPolicy === "every_wake" ? "" : ` - Last rotated ${escapeHtml(lastRotated)}`}`}</div>
            ${
              handoffSummary
                ? `<details class="runtime-handoff" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("operations", "agent", agent.id, "handoff"))}><summary>Handoff summary</summary><pre>${escapeHtml(handoffSummary)}</pre></details>`
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
    const health = peers.agents.normalizeAgentSessionHealth(agent);
    return peers.agents.sessionHealthRuntimeLine(health, agent.session_id) || text(agent.session_id);
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

    const runtime = peers.app.state.runtime_operations || {
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
    const visibleRuntimeAttention = (runtime.attention || []).filter(peers.app.isVisibleRuntimeRow);
    const scopedAttention = runtimeAttentionForFilter(visibleRuntimeAttention, runtimeFilter);
    const attention = scopedAttention.filter(runtimeAttentionMatchesFacets);
    const allAttention = [...(runtime.attention || [])].sort(peers.app.runtimeSortRows);
    const attentionCount = allAttention.length;
    const visibleAttentionCount = attention.length;

    const sections = [];
    const recoveries = values(peers.app.state.git_recovery_incidents);
    if (recoveries.length && ["all", "needs_attention", "blocked_waiting_work"].includes(runtimeFilter)) {
      const causes = new Map();
      for (const incident of recoveries) {
        const row = causes.get(incident.cause) || {total: 0, resumed: 0, retries: 0, candidates: 0, operator: 0, elapsed: []};
        row.total += 1;
        row.resumed += incident.state === "resumed" ? 1 : 0;
        row.retries += incident.preparation_retries || 0;
        row.candidates += incident.candidate_generations || 0;
        row.operator += peers.app.state.blockers?.[incident.blocker_id]?.route === "operator" ? 1 : 0;
        if (incident.resumed_at) row.elapsed.push(Math.max(0, (Date.parse(incident.resumed_at) - Date.parse(incident.created_at)) / 60000));
        causes.set(incident.cause, row);
      }
      sections.push(renderRuntimeSection({title: "Git recovery", count: `${recoveries.length} in loaded history`,
        body: `<table><thead><tr><th>Cause</th><th>Resumed / incidents</th><th>Preparation retries</th><th>Reviewed generations</th><th>At operator</th><th>Mean minutes to resume</th></tr></thead><tbody>${[...causes].sort(([a], [b]) => a.localeCompare(b)).map(([cause, row]) => `<tr><td>${escapeHtml(text(cause).replaceAll("_", " "))}</td><td>${row.resumed} / ${row.total}</td><td>${row.retries}</td><td>${row.candidates}</td><td>${row.operator}</td><td>${row.elapsed.length ? (row.elapsed.reduce((a, b) => a + b, 0) / row.elapsed.length).toFixed(1) : "—"}</td></tr>`).join("")}</tbody></table>`,
      }));
    }

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

    peers.app.renderIntoActiveTargets("[data-runtime-operations-view]", `
      <div class="runtime-filter-summary">Showing ${escapeHtml(runtimeFilterLabel(runtimeFilter))}.</div>
      ${sections.join("")}
      ${
        runtime.generated_at
          ? `<div class="meta-line runtime-generated-at">Updated ${escapeHtml(peers.app.formatTimelineTimestamp(runtime.generated_at))}</div>`
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

  return {
    bindRuntimeFilters,
    dueLeasedAgentsForFilter,
    isDueOrLeasedAgent,
    renderRuntimeOperations,
    runtimeAttentionGroup,
    runtimeAttentionGroupRank,
    runtimeAttentionOccurredTime,
    runtimeSeverityRank,
  };
}
