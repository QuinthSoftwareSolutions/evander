// View navigation, scoped state loading, and stream lifecycle.
import {
  defaultTeamView,
  emptyState,
  normalizedTeamView,
  readStoredTeamView,
  storeTeamView,
} from "./team-ui-state.js";
import {
  API_BASE,
  initialChatDeepLink,
  initialHumanReviewDeepLink,
  initialTaskBoardDeepLink,
  teamStateCollectionKeys,
} from "./team-ui-config.js";
import {
  byId,
  escapeHtml,
  humanizeToken,
  responseText,
  text,
  values,
} from "./team-ui-format.js";
import { elements } from "./team-ui-dom.js";
import { taskBoardViewSearchParams } from "./team-case-board.js";

export function createTeamAppController(peers) {

  let state = emptyState();

  let taskPreparationInvalidationByTaskId = new Map();

  let communicationTargets = [];

  const loadedTeamViews = new Set();

  const loadingTeamViews = new Map();

  const pendingForcedTeamViewLoads = new Map();

  let teamViewCacheGeneration = 0;

  let stateStream = null;

  let stateStreamErrorRefreshPending = false;

  let showArchived = false;

  let activeTeamView =
    initialHumanReviewDeepLink.enabled
      ? "delivery"
      : initialChatDeepLink.enabled
        ? "chat"
        : initialTaskBoardDeepLink.get("view") === "task-board"
          ? "task-board"
          : readStoredTeamView();

  function bindTopLevelTabs() {
    const tabs = Array.from(document.querySelectorAll(".team-view-tabs [data-team-view]"));
    const viewSelect = document.getElementById("team-view-select");
    if (viewSelect) {
      tabs.forEach((tab) => viewSelect.add(new Option(tab.textContent.trim(), tab.dataset.teamView)));
      viewSelect.addEventListener("change", () => setTeamView(viewSelect.value));
    }
    document.querySelector(".team-view-tabs")?.addEventListener("keydown", (event) => {
      const index = tabs.indexOf(event.target);
      if (index < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      const next = tabs[nextIndex];
      setTeamView(next.dataset.teamView);
      next.focus({ preventScroll: true });
      next.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    document.querySelector("[data-team-detail-close]")?.addEventListener("click", peers.people.closeTeamRecordInspector);
    document.querySelector(".people-agent-detail-panel")?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        peers.people.closeTeamRecordInspector();
      }
    });
    document.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-team-view], [data-team-link-view]");
      if (!button) {
        return;
      }
      event.preventDefault();
      if (button.dataset.teamLinkRecordKind === "task" && button.dataset.teamLinkRecordId) {
        peers.board.openTaskBoardTask(button.dataset.teamLinkRecordId);
        return;
      }
      setTeamView(button.dataset.teamView || button.dataset.teamLinkView || defaultTeamView);
      if (button.dataset.teamLinkRecordKind === "agent" && button.dataset.teamLinkRecordId) {
        peers.people.selectTeamRecord("agent", button.dataset.teamLinkRecordId);
      }
    });
    updateTeamViewTabs();
  }

  function bindPageLifecycle() {
    window.addEventListener("beforeunload", () => {
      peers.chat.closeChatStream();
      closeStateStream();
    });
    window.addEventListener("pagehide", () => {
      peers.chat.closeChatStream();
      closeStateStream();
    });
    window.addEventListener("pageshow", () => {
      ensureStateStream();
      peers.chat.syncChatStreamForActiveView({ refresh: true });
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") {
        peers.chat.closeChatStream();
        closeStateStream();
        return;
      }
      ensureStateStream();
      peers.chat.syncChatStreamForActiveView({ refresh: true });
    });
  }

  function setTeamView(view) {
    const nextView = normalizedTeamView(view);
    if (nextView === "people" && activeTeamView !== "people") {
      peers.people.clearSelectedTeamRecord();
    }
    const enteringChat = activeTeamView !== "chat" && nextView === "chat";
    const leavingChat = activeTeamView === "chat" && nextView !== "chat";
    activeTeamView = nextView;
    storeTeamView(activeTeamView);
    if (enteringChat && peers.chat.selectedChatConversationId) {
      peers.chat.markChatConversationRead(peers.chat.selectedChatConversationId, { rerender: false });
      peers.chat.scheduleChatScrollToBottom(peers.chat.selectedChatConversationId);
    }
    renderShell();
    if (loadedTeamViews.has(activeTeamView)) {
      renderActiveView(activeTeamView);
    } else {
      renderActiveViewLoading(activeTeamView);
    }
    if (leavingChat) {
      peers.chat.closeChatStream();
    }
    if (!loadedTeamViews.has(activeTeamView)) {
      void loadView(activeTeamView, { force: true });
    } else if (enteringChat) {
      peers.chat.syncChatStreamForActiveView({ refresh: true });
    } else if (activeTeamView === "activity") {
      scheduleAuxiliaryLoadsForView(activeTeamView);
    }
  }

  function updateTeamViewTabs() {
    const unreadChatCount = peers.chat.unreadChatConversationCount();
    const viewSelect = document.getElementById("team-view-select");
    if (viewSelect) {
      viewSelect.value = activeTeamView;
      const chatOption = viewSelect.querySelector('option[value="chat"]');
      if (chatOption) chatOption.textContent = unreadChatCount ? `Chat (${unreadChatCount} unread)` : "Chat";
    }
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
    const drafts = peers.drafts.captureFormDrafts();
    peers.disclosures.rememberOpenTeamDisclosures();
    const targets = Array.from(document.querySelectorAll(selector));
    targets.forEach((target) => {
      if (targetIsActive(target)) {
        target.innerHTML = html;
      } else {
        target.innerHTML = "";
      }
    });
    peers.drafts.restoreFormDrafts(drafts);
    return targets.filter(targetIsActive).length;
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
      const focused = await peers.humanReviews.openHumanReviewDeepLink(
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
    const response = await peers.access.teamFetch(`${API_BASE}/bootstrap`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    state = normalizeState({ ...state, revision: payload.revision ?? state.revision });
    communicationTargets = peers.selects.normalizeCommunicationTargetOptions(payload.communication_targets);
    peers.chat.applyBootstrapScope(payload);
    renderShell();
    if (activeTeamView === "chat") {
      peers.chat.renderChat();
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
      const promise = peers.chat.loadChatView({ force, preserveForms, loadGeneration }).finally(() => {
        loadingTeamViews.delete(normalizedView);
      });
      loadingTeamViews.set(normalizedView, promise);
      return promise;
    }
    const loadGeneration = teamViewCacheGeneration;
    const promise = (async () => {
      const query = normalizedView === "timeline"
        ? peers.timeline.timelineSearchParams()
        : normalizedView === "task-board"
          ? taskBoardViewSearchParams({
              caseId: peers.board.taskBoardCaseId,
              teamId: peers.board.selectedTaskBoardTeamId,
              projectId: peers.board.selectedTaskBoardProjectId,
            })
          : new URLSearchParams();
      const response = await peers.access.teamFetch(`${API_BASE}/views/${encodeURIComponent(normalizedView)}${query.toString() ? `?${query.toString()}` : ""}`, {
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
        const drafts = preserveForms ? peers.drafts.captureFormDrafts() : null;
        render();
        if (drafts) {
          peers.drafts.restoreFormDrafts(drafts);
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
      peers.templates.applyTemplates({ templates: data.templates });
    }
    if (data.timeline) {
      peers.timeline.applyTimeline(data.timeline);
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
      void peers.mail.load();
    }
    if (view === "activity") {
      void peers.activity.load();
    }
    if (teamViewUsesCostState(view)) {
      void peers.costs.loadCosts();
    }
  }

  function teamViewUsesCostState(view) {
    return view !== "chat" && view !== "task-board";
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
    const drafts = peers.drafts.captureFormDrafts();
    state = normalizeState(payload);
    render();
    peers.drafts.restoreFormDrafts(drafts);
    if (teamViewUsesCostState(activeTeamView)) {
      void peers.costs.loadCosts();
    }
    void peers.chat.loadConversations({ preserveSelection: true });
    void peers.timeline.loadTimeline();
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

  function renderShell() {
    elements.revisionBadge.textContent = `Revision ${state.revision}`;
    updateArchivedVisibilityToggle();
    peers.governance.updateGovernanceAgendaVisibilityToggle();
    updateTeamViewTabs();
    peers.planning.updatePlanningFilterControls();
    peers.people.syncTeamsCreateTeamPanel();
    peers.governance.syncGovernanceCreateGroupPanel();
    peers.access.applyLockedMutationControls();
  }

  function render() {
    const releaseVerificationScrollState = peers.releases.captureReleaseVerificationScrollState();
    peers.disclosures.rememberOpenTeamDisclosures();
    renderShell();
    renderActiveView(activeTeamView);
    peers.releases.restoreReleaseVerificationScrollState(releaseVerificationScrollState);
  }

  function renderActiveView(view = activeTeamView) {
    switch (normalizedTeamView(view)) {
      case "workspace":
        peers.workspace.renderWorkspaceDashboard();
        decorateActiveViewTooltips();
        break;
      case "operations":
        peers.operations.renderRuntimeOperations();
        decorateActiveViewTooltips();
        break;
      case "delivery":
        peers.delivery.renderReviewDeliveryWorkspace();
        peers.reviews.renderReviewProfiles();
        peers.releases.renderReleases();
        decorateActiveViewTooltips();
        break;
      case "memory":
        peers.workspace.renderMemoryView();
        decorateActiveViewTooltips();
        break;
      case "projects":
        peers.projects.renderProjectFocus();
        refreshActiveViewSelects();
        decorateActiveViewTooltips();
        break;
      case "task-board":
        peers.board.renderTaskBoard();
        decorateActiveViewTooltips();
        break;
      case "cases":
        peers.cases.renderCases();
        decorateActiveViewTooltips();
        break;
      case "people":
        peers.people.renderAgents();
        refreshActiveViewSelects();
        decorateActiveViewTooltips();
        break;
      case "chat":
        peers.chat.renderChat();
        decorateActiveViewTooltips();
        break;
      case "mail":
        peers.mail.render();
        decorateActiveViewTooltips();
        break;
      case "activity":
        peers.activity.render();
        decorateActiveViewTooltips();
        break;
      case "governance":
        peers.governance.renderGovernance();
        refreshActiveViewSelects();
        decorateActiveViewTooltips();
        break;
      case "planning":
        peers.projects.renderProjects();
        peers.planning.updatePlanningFilterControls();
        decorateActiveViewTooltips();
        break;
      case "timeline":
        refreshActiveViewSelects();
        peers.timeline.renderTimeline();
        decorateActiveViewTooltips();
        break;
      case "org":
        renderFocusedOrgChartIfActive();
        decorateActiveViewTooltips();
        break;
      default:
        break;
    }
    peers.access.applyLockedMutationControls();
  }

  function renderActiveViewLoading(view = activeTeamView) {
    const drafts = peers.drafts.captureFormDrafts();
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
    peers.drafts.restoreFormDrafts(drafts);
  }

  function activeViewPanel() {
    return document.querySelector(`[data-team-view-panel="${activeTeamView}"]`);
  }

  function refreshActiveViewSelects() {
    const panel = activeViewPanel();
    if (panel) {
      peers.selects.fillSelectsIn(panel);
    }
  }

  function decorateActiveViewTooltips() {
    const panel = activeViewPanel();
    if (panel) {
      peers.tooltips.decorateFieldTooltips(panel);
    }
  }

  function renderFocusedOrgChartIfActive() {
    if (activeTeamView === "org") {
      peers.org.renderFocusedOrgChart();
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

  function bindArchivedVisibilityToggle() {
    elements.showArchivedToggle?.addEventListener("change", () => {
      const drafts = peers.drafts.captureFormDrafts();
      showArchived = Boolean(elements.showArchivedToggle?.checked);
      render();
      peers.drafts.restoreFormDrafts(drafts);
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

  function isArchivedGovernanceGroup(group) {
    return Boolean(group?.archived);
  }

  function isArchivedProject(project) {
    return text(project?.status) === "archived";
  }

  function isArchivedAgent(agent) {
    return peers.agents.agentStatus(agent) === "archived";
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
      peers.operations.runtimeAttentionGroupRank(peers.operations.runtimeAttentionGroup(left)) -
        peers.operations.runtimeAttentionGroupRank(peers.operations.runtimeAttentionGroup(right)) ||
      peers.operations.runtimeAttentionOccurredTime(right) - peers.operations.runtimeAttentionOccurredTime(left) ||
      peers.operations.runtimeSeverityRank(left.severity) - peers.operations.runtimeSeverityRank(right.severity) ||
      String(left.kind || "").localeCompare(String(right.kind || "")) ||
      String(left.id || "").localeCompare(String(right.id || ""))
    );
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
      if (peers.access.teamMutationLocked()) {
        peers.access.lockMutationControl(button, "retry saves");
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

  function replaceState(nextState) {
    state = normalizeState(nextState);
  }

  function hasLoadedView(view) {
    return loadedTeamViews.has(view);
  }

  function markViewLoaded(view) {
    loadedTeamViews.add(view);
  }

  function forgetLoadedView(view) {
    loadedTeamViews.delete(normalizedTeamView(view));
  }

  function resetLoadedViewsToActive() {
    loadedTeamViews.clear();
    loadedTeamViews.add(activeTeamView);
  }

  return {
    hasLoadedView,
    markViewLoaded,
    forgetLoadedView,
    resetLoadedViewsToActive,
    replaceState,
    get activeTeamView() { return activeTeamView; },
    applyViewPayload,
    bindArchivedVisibilityToggle,
    bindPageLifecycle,
    bindTopLevelTabs,
    bootTeamAddon,
    get communicationTargets() { return communicationTargets; },
    formatTimelineTimestamp,
    isArchivedAgent,
    isArchivedProject,
    isArchivedTeam,
    isVisibleAgent,
    isVisibleCase,
    isVisibleGovernanceGroup,
    isVisibleProject,
    isVisibleReviewProfile,
    isVisibleRole,
    isVisibleRuntimeRow,
    isVisibleTeam,
    loadView,
    mergeResourceTokens,
    reloadActiveTeamView,
    render,
    renderIntoActiveTargets,
    renderShell,
    runtimeSortRows,
    setStatus,
    setTeamView,
    setTimelineStatus,
    get showArchived() { return showArchived; },
    get state() { return state; },
    targetIsActive,
    get taskPreparationInvalidationByTaskId() { return taskPreparationInvalidationByTaskId; },
    get teamViewCacheGeneration() { return teamViewCacheGeneration; },
    teamViewUsesCostState,
    updateTeamViewTabs,
  };
}
