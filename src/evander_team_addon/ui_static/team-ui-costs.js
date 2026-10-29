// Cost refresh coalescing, backfill fencing, and budget presentation.
import { emptyCostState } from "./team-ui-state.js";
import { API_BASE, COSTS_REFRESH_INTERVAL_MS } from "./team-ui-config.js";
import {
  escapeHtml,
  formatCount,
  formatUsd,
  numberOrNull,
  numberOrZero,
  responseText,
  text,
} from "./team-ui-format.js";

export function createTeamCostsController(peers) {

  let costState = emptyCostState();

  let costsRequestId = 0;

  let costsInFlight = null;

  let costsForcedRefresh = null;

  let costsLastLoadedAt = 0;

  let costsRefreshTimer = null;

  let costsRefreshPending = false;

  let costBackfillRequestId = 0;

  let costBackfillLoading = false;

  let costBackfillFinished = null;

  function scheduleCostsRefresh(delay) {
    if (costsRefreshTimer !== null) {
      return;
    }
    costsRefreshTimer = window.setTimeout(() => {
      costsRefreshTimer = null;
      if (peers.app.teamViewUsesCostState(peers.app.activeTeamView)) {
        void loadCosts({ force: true });
      }
    }, delay);
  }

  function queueForcedCostsRefresh(completion) {
    if (!costsForcedRefresh) {
      costsForcedRefresh = completion.then(() => {
        costsForcedRefresh = null;
        return loadCosts({ force: true });
      });
    }
    return costsForcedRefresh;
  }

  function loadCosts({ force = false } = {}) {
    if (costBackfillLoading) {
      return force ? queueForcedCostsRefresh(costBackfillFinished) : Promise.resolve();
    }
    if (costsInFlight) {
      if (force) {
        // A successful budget reset must observe a read started after the reset,
        // including when an older transcript backfill is also running.
        return queueForcedCostsRefresh(costsInFlight);
      }
      costsRefreshPending = true;
      return costsInFlight;
    }
    const remaining = costsLastLoadedAt + COSTS_REFRESH_INTERVAL_MS - Date.now();
    if (!force && remaining > 0) {
      // Keep the last state event during the interval; it must not be lost just
      // because no further events arrive after this refresh is deferred.
      scheduleCostsRefresh(remaining);
      return Promise.resolve();
    }
    if (costsRefreshTimer !== null) {
      window.clearTimeout(costsRefreshTimer);
      costsRefreshTimer = null;
    }
    costsRefreshPending = false;
    costsInFlight = fetchCosts().finally(() => {
      costsInFlight = null;
      if (costsRefreshPending) {
        scheduleCostsRefresh(COSTS_REFRESH_INTERVAL_MS);
      }
    });
    return costsInFlight;
  }

  async function fetchCosts() {
    const requestId = ++costsRequestId;
    try {
      const response = await peers.access.teamFetch(`${API_BASE}/costs?backfill=false`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      if (requestId !== costsRequestId) {
        return;
      }
      costState = normalizeCosts(payload);
      costsLastLoadedAt = Date.now();
      if (peers.app.teamViewUsesCostState(peers.app.activeTeamView)) {
        const drafts = peers.drafts.captureFormDrafts();
        peers.app.render();
        peers.drafts.restoreFormDrafts(drafts);
      }
    } catch (error) {
      if (requestId !== costsRequestId) {
        return;
      }
      costState = normalizeCosts({
        ...emptyCostState(),
        error: error instanceof Error ? error.message : "Cost data failed to load.",
      });
      if (peers.app.teamViewUsesCostState(peers.app.activeTeamView)) {
        const drafts = peers.drafts.captureFormDrafts();
        peers.app.render();
        peers.drafts.restoreFormDrafts(drafts);
      }
    }
  }

  async function refreshCostBackfill() {
    if (costBackfillLoading) {
      return;
    }
    const requestId = ++costBackfillRequestId;
    // Ignore a GET already in flight if it completes after the newer backfill.
    costsRequestId += 1;
    costsRefreshPending = false;
    if (costsRefreshTimer !== null) {
      window.clearTimeout(costsRefreshTimer);
      costsRefreshTimer = null;
    }
    let drafts = peers.drafts.captureFormDrafts();
    costBackfillLoading = true;
    let finishBackfill;
    costBackfillFinished = new Promise((resolve) => { finishBackfill = resolve; });
    peers.app.setStatus("Refreshing transcript costs.", "loading");
    peers.app.render();
    peers.drafts.restoreFormDrafts(drafts);
    try {
      const response = await peers.access.teamFetch(`${API_BASE}/costs/backfill`, {
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
      drafts = peers.drafts.captureFormDrafts();
      costBackfillLoading = false;
      costState = normalizeCosts(payload);
      costsLastLoadedAt = Date.now();
      peers.app.render();
      peers.drafts.restoreFormDrafts(drafts);
      peers.app.setStatus("", "");
    } catch (error) {
      if (requestId !== costBackfillRequestId) {
        return;
      }
      drafts = peers.drafts.captureFormDrafts();
      costBackfillLoading = false;
      costState = normalizeCosts({
        ...costState,
        error: error instanceof Error ? error.message : "Cost transcript refresh failed.",
      });
      peers.app.render();
      peers.drafts.restoreFormDrafts(drafts);
      peers.app.setStatus(error instanceof Error ? error.message : "Cost transcript refresh failed.", "error");
    } finally {
      costBackfillLoading = false;
      costBackfillFinished = null;
      finishBackfill();
    }
  }

  function normalizeCosts(payload) {
    return { ...emptyCostState(), ...(payload || {}) };
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
    const buttonAttrs = peers.access.lockedAttrs("refresh transcript costs") || (costBackfillLoading ? "disabled" : "");
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
        <strong>Blocked: cost cap reached</strong>
        <span>Team wakes are suspended until this budget is reset, renewed, raised, or cleared. ${escapeHtml(formatUsd(cost.budget_window_total_cost ?? cost.budget_window_estimated_cost ?? cost.total_cost ?? cost.estimated_cost))} used of ${escapeHtml(capLabel)} ${escapeHtml(text(cost.cost_limit_basis) === "budget_window" ? "budget window" : "lifetime cap")}.${escapeHtml(coverageNote)}</span>
      </div>
    `;
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
    const budgetWindowCost = numberOrNull(cost.budget_window_total_cost ?? cost.budget_window_estimated_cost);
    const displayedCost = budgetWindowCost === null ? (numberOrNull(cost.total_cost ?? cost.estimated_cost) ?? 0) : budgetWindowCost;
    if (maxCost !== null) {
      parts.push(
        limitReached
          ? `limit reached ${formatUsd(maxCost)}`
          : `${formatUsd(cost.remaining_cost_usd)} remaining`,
      );
    }
    if (events) {
      parts.push(`${formatCount(events)} event${events === 1 ? "" : "s"}`);
    }
    if (budgetWindowCost !== null && numberOrNull(cost.estimated_cost) !== null) {
      parts.push(`lifetime ${formatUsd(cost.total_cost ?? cost.estimated_cost)}`);
    }
    if ((numberOrNull(cost.reported_cost) ?? 0) > 0) {
      parts.push(`${formatUsd(cost.reported_cost)} provider reported, ${formatUsd(cost.estimated_cost)} estimated (lifetime)`);
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

  async function resetTeamCostBudget(button, teamId, reason) {
    if (!teamId) {
      peers.app.setStatus("Team budget reset target is unavailable.", "error");
      return;
    }
    button.disabled = true;
    peers.app.setStatus("Resetting cost budget.", "loading");
    try {
      const response = await peers.access.teamFetch(
        `${API_BASE}/teams/${encodeURIComponent(teamId)}/cost-budget/reset`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Team-Addon-Response-Scope": "view",
          },
          body: JSON.stringify({ reason, expected_revision: peers.app.state.revision }),
        },
      );
      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView();
        peers.app.setStatus(
          `Revision conflict. Reloaded revision ${conflict.current_revision ?? peers.app.state.revision}; verify the budget and retry.`,
          "warning",
        );
        return;
      }
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      await peers.mutations.applyMutationResultPayload(await response.json());
      await loadCosts({ force: true });
      peers.app.setStatus("Started a new team cost budget window.", "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Budget reset failed.", "error");
    } finally {
      button.disabled = false;
    }
  }
  function bindBudgetReset() {

    document.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-team-cost-budget-reset]");
      if (!button) {
        return;
      }
      event.preventDefault();
      if (!peers.access.guardTeamMutation("reset a team cost budget")) {
        return;
      }
      const reason = window.prompt("Why is a new budget window authorized?", "");
      if (!text(reason)) {
        peers.app.setStatus("A budget reset reason is required.", "error");
        return;
      }
      void resetTeamCostBudget(button, text(button.dataset.teamCostBudgetReset), text(reason));
    });
  }
  function handleBackfillClick(event) {
    const costBackfillButton = event.target.closest("[data-cost-backfill-refresh]");
    if (costBackfillButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("refresh transcript costs")) {
        return true;
      }
      void refreshCostBackfill();
      return true;
    }
    return false;
  }

  return {
    refreshCostBackfill,
    bindBudgetReset,
    get costState() { return costState; },
    governanceCostForGroup,
    governanceCostMemberTeamCount,
    handleBackfillClick,
    loadCosts,
    renderCostBackfillControl,
    renderGovernanceCostCounter,
    renderProjectCostCounter,
    renderTeamCostBlockedNotice,
    renderTeamCostBlockedPill,
    renderTeamCostCounter,
    teamCostForTeam,
  };
}
