// Organization graph, context menus, and org action dialogs.
import {
  domIdPart,
  escapeAttr,
  escapeHtml,
  formData,
  humanizeToken,
  renderSelectOptions,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { elements } from "./team-ui-dom.js";
import { orgFlowLayout } from "./team-ui-config.js";

export function createTeamOrgController(peers) {

  let selectedOrgTeamId = "all";

  let orgContextMenuState = null;

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
          disabled: peers.access.teamMutationLocked(),
          disabledReason: peers.access.lockedMutationMessage("create teams"),
        },
      ],
    });
  }

  function openOrgAgentContextMenu(detail) {
    const agentId = text(detail?.agentId);
    const agent = peers.app.state.agents?.[agentId];
    const point = orgContextPoint(detail);
    openOrgContextMenu({
      kind: "agent",
      point,
      agentId,
      items: [
        {
          action: "open-chat",
          label: "Open chat",
          disabled: peers.chat.chatState.read_only || !agent,
          disabledReason: peers.chat.chatState.read_only
            ? "This chat history is read-only."
            : "Agent is no longer available.",
        },
        {
          action: "open-session",
          label: "Open session",
          disabled: !text(agent?.session_id),
          disabledReason: "This agent has no linked session.",
        },
        {
          action: "add-subordinate-agent",
          label: "Add subordinate agent",
          disabled: peers.access.teamMutationLocked() || !agent,
          disabledReason: peers.access.teamMutationLocked()
            ? peers.access.lockedMutationMessage("add subordinate agents")
            : "Agent is no longer available.",
        },
      ],
    });
  }

  function openOrgGovernanceContextMenu(detail) {
    const groupId = text(detail?.groupId);
    const group = peers.app.state.governance_groups?.[groupId];
    const point = orgContextPoint(detail);
    openOrgContextMenu({
      kind: "governance",
      point,
      groupId,
      items: [
        {
          action: "governance-add-team",
          label: "Add team to oversight",
          disabled: peers.access.teamMutationLocked() || !group || governanceOversightTeamOptions(group, "add").length === 0,
          disabledReason: peers.access.teamMutationLocked()
            ? peers.access.lockedMutationMessage("edit governance oversight")
            : "No visible teams are available to add.",
        },
        {
          action: "governance-remove-team",
          label: "Remove team from oversight",
          disabled: peers.access.teamMutationLocked() || !group || governanceOversightTeamOptions(group, "remove").length === 0,
          disabledReason: peers.access.teamMutationLocked()
            ? peers.access.lockedMutationMessage("edit governance oversight")
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
    if (action === "open-chat") {
      void peers.chat.openChatForAgent(context.agentId);
      return;
    }
    if (action === "open-session") {
      peers.agents.navigateToAgentSession(peers.app.state.agents?.[context.agentId]?.session_id);
      return;
    }
    if (action === "create-team") {
      if (!peers.access.guardTeamMutation("create teams")) {
        return;
      }
      openOrgCreateTeamDialog();
      return;
    }
    if (action === "governance-add-team") {
      if (!peers.access.guardTeamMutation("edit governance oversight")) {
        return;
      }
      openOrgGovernanceOversightDialog(context.groupId, "add");
      return;
    }
    if (action === "governance-remove-team") {
      if (!peers.access.guardTeamMutation("edit governance oversight")) {
        return;
      }
      openOrgGovernanceOversightDialog(context.groupId, "remove");
      return;
    }
    if (action === "add-subordinate-agent") {
      if (!peers.access.guardTeamMutation("add subordinate agents")) {
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
    peers.selects.fillSelectsIn(dialog.body);
    peers.tooltips.decorateFieldTooltips(dialog.body);
    peers.access.applyLockedMutationControls(dialog.modal);
    dialog.modal.hidden = false;
    window.requestAnimationFrame(() => {
      dialog.body.querySelector("input:not([readonly]), select, textarea, button")?.focus();
    });
  }

  function closeOrgActionDialog() {
    const dialog = ensureOrgActionDialog();
    const form = dialog.body.querySelector("form");
    if (form) {
      peers.drafts.clearFormDraft(form);
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
          <label>Governance cost warning threshold <input name="cost_warning_ratio" type="number" min="0.01" max="1" step="0.01" value="0.8" /></label>
          <label>Budget renewal <select name="cost_budget_renewal_mode"><option value="manual">Manual reset</option><option value="monthly_utc">Monthly (UTC)</option></select></label>
          <div class="org-action-form-actions">
            <button type="submit">Create team</button>
            <button type="button" class="secondary" data-org-action-close>Cancel</button>
          </div>
        </form>
      `,
    });
  }

  function openOrgGovernanceOversightDialog(groupId, mode) {
    const group = peers.app.state.governance_groups?.[text(groupId)];
    if (!group) {
      peers.app.setStatus("Governance group is no longer available.", "error");
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
    const superior = peers.app.state.agents?.[text(agentId)];
    if (!superior) {
      peers.app.setStatus("Agent is no longer available.", "error");
      return;
    }
    const teamId = text(superior.team_id);
    if (!teamId) {
      peers.app.setStatus("Teamless agents cannot have subordinate Team agents.", "error");
      return;
    }
    const directoryPath = peers.sessions.teamAgentDirectoryPath();
    await peers.sessions.loadCoreAgentSessions({ directoryPath, force: false, rerender: false });
    showOrgActionDialog({
      title: "Add subordinate agent",
      bodyHtml: renderOrgSubordinateAgentForm(superior, directoryPath),
    });
  }

  function renderOrgSubordinateAgentForm(superior, directoryPath) {
    const teamId = text(superior.team_id);
    const selectedProjectId = peers.agents.agentProjectIds(superior)[0] || "";
    const superiorLabel = peers.agents.agentLabel(superior);
    const defaultsOptions = peers.people.teamAgentOptions(teamId).filter(([agentId]) => text(agentId) !== text(superior.id));
    const createSessionDisabled = peers.access.teamPrivilegedUnlocked()
      ? ""
      : peers.access.lockedAttrs("create a core agent session");
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
          <select name="project_id">${renderSelectOptions(peers.selects.teamProjectOptions(teamId), { selectedValue: selectedProjectId, placeholder: "No project" })}</select>
        </label>
        <label>Role
          <select name="role_id" required>${renderSelectOptions(peers.selects.teamRoleOptions(teamId), { placeholder: "Select role" })}</select>
        </label>
        <label>Existing session
          <select
            name="existing_session_id"
            data-core-agent-session-picker
            data-session-field="session_id"
          >${peers.sessions.renderCoreAgentSessionOptions()}</select>
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
    return values(peers.app.state.teams)
      .filter(peers.app.isVisibleTeam)
      .filter((team) => memberTeamIds.has(text(team.id)) === includeMember)
      .sort(peers.people.comparePeopleTeams)
      .map((team) => [team.id, `${team.name || team.id} (${team.id})`]);
  }

  async function submitOrgActionForm(form) {
    if (!peers.access.guardTeamMutation("save changes")) {
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
    const endpoint = peers.forms.endpointForForm(form, data);
    const method = form.dataset.method || "POST";
    if (!endpoint) {
      return false;
    }
    const saved = await peers.mutations.submitMutation(form, endpoint, method, peers.forms.payloadForForm(form, data));
    if (saved) {
      closeOrgActionDialog();
    }
    return saved;
  }

  async function submitOrgGovernanceOversightForm(form, removeTeam) {
    const groupId = text(form.dataset.groupId);
    const teamId = text(formData(form).team_id);
    const group = peers.app.state.governance_groups?.[groupId];
    if (!group) {
      peers.app.setStatus("Governance group is no longer available.", "error");
      return false;
    }
    if (!teamId) {
      peers.app.setStatus("Team is required.", "error");
      return false;
    }
    const currentTeamIds = uniqueTextList(group.member_team_ids || []);
    const nextTeamIds = removeTeam
      ? currentTeamIds.filter((memberTeamId) => memberTeamId !== teamId)
      : uniqueTextList([...currentTeamIds, teamId]);
    const saved = await peers.mutations.submitMutation(
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
      peers.editor.openEditDialog("agent", agentId);
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

  function renderFocusedOrgChart() {
    if (!elements.orgFocusView) {
      return;
    }

    const payload = buildFocusedOrgChartPayload();
    const { teams, visibleTeams, focusedTeams, visibleAgents } = payload;
    if (selectedOrgTeamId !== "all" && !visibleTeams.some((team) => text(team.id) === text(selectedOrgTeamId))) {
      selectedOrgTeamId = "all";
    }
    const awakeCount = visibleAgents.filter((agent) => peers.agents.agentAwakeState(peers.agents.agentCardData(agent)).kind === "awake").length;
    const attentionCount = visibleAgents.filter((agent) => {
      const details = peers.agents.agentCardData(agent);
      return details.taskStats.blockedWaitingCount || details.inboxCount || peers.agents.agentRuntimeAttentionRows(agent.id).length;
    }).length;
    const dueCount = visibleAgents.filter((agent) => peers.agents.agentAwakeState(peers.agents.agentCardData(agent)).kind === "due").length;

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
    const teams = values(peers.app.state.teams);
    const visibleTeams = teams.filter(peers.app.isVisibleTeam).sort(peers.people.comparePeopleTeams);
    if (selectedOrgTeamId !== "all" && !visibleTeams.some((team) => text(team.id) === text(selectedOrgTeamId))) {
      selectedOrgTeamId = "all";
    }
    const focusedTeams =
      selectedOrgTeamId === "all"
        ? visibleTeams
        : visibleTeams.filter((team) => text(team.id) === text(selectedOrgTeamId));
    const focusedTeamIds = new Set(focusedTeams.map((team) => text(team.id)));
    const visibleAgents = values(peers.app.state.agents).filter(
      (agent) => peers.app.isVisibleAgent(agent) && focusedTeamIds.has(text(agent.team_id)),
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

  function buildOrgFlowGraph(focusedTeams) {
    const nodes = [];
    const edges = [];
    const focusedTeamIds = new Set(focusedTeams.map((team) => text(team.id)));
    const governanceGroups = values(peers.app.state.governance_groups)
      .filter(peers.app.isVisibleGovernanceGroup)
      .filter((group) => (group.member_team_ids || []).some((teamId) => focusedTeamIds.has(text(teamId))))
      .sort(peers.people.compareGovernanceGroups);
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
      const teamAgents = values(peers.app.state.agents).filter(
        (agent) => text(agent.team_id) === text(team.id) && peers.app.isVisibleAgent(agent),
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
      messageEvents: orgFlowMessageEvents(nodes),
      fitKey: orgFlowLayoutFitKey(nodes, edges),
    };
  }

  function orgFlowMessageEvents(nodes) {
    const visibleAgentIds = new Set(
      nodes
        .filter((node) => node.data?.kind === "agent")
        .map((node) => text(node.data.agentId))
        .filter(Boolean),
    );
    const events = [];
    visibleAgentIds.forEach((recipientAgentId) => {
      peers.agents.pendingInboxForAgent(recipientAgentId).forEach((item) => {
        const itemType = text(item.item_type);
        const senderAgentId = text(item.sender_agent_id);
        if (
          !["status_request", "progress_report"].includes(itemType) ||
          !senderAgentId ||
          senderAgentId === recipientAgentId ||
          !visibleAgentIds.has(senderAgentId)
        ) {
          return;
        }
        events.push({
          id: text(item.id),
          sourceNodeId: `agent:${senderAgentId}`,
          targetNodeId: `agent:${recipientAgentId}`,
          sourceLabel: peers.agents.agentLabel(peers.app.state.agents[senderAgentId]),
          targetLabel: peers.agents.agentLabel(peers.app.state.agents[recipientAgentId]),
          itemType,
          label: peers.agents.inboxItemDetail(item) || humanizeToken(itemType),
        });
      });
    });
    return events.filter((event) => event.id);
  }

  function orgFlowLayoutFitKey(nodes, edges) {
    return JSON.stringify({
      scope: selectedOrgTeamId,
      archived: peers.app.showArchived,
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
      animated: false,
    };
  }

  function orgTeamNodeData(team, teamAgents) {
    const awakeCount = teamAgents.filter((agent) => peers.agents.agentAwakeState(peers.agents.agentCardData(agent)).kind === "awake").length;
    const attentionCount = teamAgents.filter((agent) => {
      const details = peers.agents.agentCardData(agent);
      return details.taskStats.blockedWaitingCount || details.inboxCount || peers.agents.agentRuntimeAttentionRows(agent.id).length;
    }).length;
    const cost = peers.costs.teamCostForTeam(team.id);
    return {
      kind: "team",
      teamId: text(team.id),
      name: text(team.name) || text(team.id),
      archived: Boolean(team.archived),
      awakeCount,
      attentionCount,
      costHtml: peers.costs.renderTeamCostCounter(team.id),
      costBlockedNoticeHtml: peers.costs.renderTeamCostBlockedNotice(team.id),
      costBlocked: Boolean(cost?.cost_limit_reached),
    };
  }

  function orgGovernanceNodeData(group, visibleMemberTeamCount) {
    const manager = peers.app.state.agents?.[group.manager_agent_id] || null;
    const agenda = peers.governance.governanceAgendaForGroup(group.id);
    const pendingAgenda = agenda.filter((item) => item.state === "pending");
    const cost = peers.costs.governanceCostForGroup(group.id);
    const capReached = cost?.cost_limit_reached || (cost?.member_team_cap_reached_ids || []).length > 0;
    return {
      kind: "governance",
      groupId: text(group.id),
      name: text(group.name) || text(group.id),
      archived: Boolean(group.archived),
      managerLabel: manager ? peers.agents.agentLabel(manager) : text(group.manager_agent_id) || "Unassigned",
      managerAvatar: manager ? orgAgentAvatarData(peers.agents.agentCardData(manager)) : null,
      memberTeamCount: visibleMemberTeamCount,
      pendingAgendaCount: pendingAgenda.length,
      costAttention: Boolean(capReached),
      costHtml: cost ? peers.costs.renderGovernanceCostCounter(cost) : "",
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
    const awake = peers.agents.agentAwakeState(agentData);
    const work = peers.agents.orgNodeWorkSummaryData(agentData);
    const activityTone = peers.agents.activityToneClass(agentData.activitySummary?.tone);
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
      activity: {
        headline: text(agentData.activitySummary?.headline) || work.primary,
        tone: activityTone || (awake.kind === "awake" ? "working" : awake.kind),
      },
      detailLines: peers.agents.hierarchyDetailLines(agentData, { focused: true }),
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
    metaParts.push(peers.agents.inboxPriorityLabel(item));
    metaParts.push(peers.agents.inboxReplyIntentLabel(item));
    return {
      detail: peers.agents.inboxItemDetail(item) || item.id,
      meta: metaParts.join(" - "),
    };
  }

  function orgAgentAvatarData(agentData) {
    const label = peers.agents.agentLabel(agentData);
    const src = peers.agents.avatarUrl(agentData);
    return {
      src,
      alt: src ? `${label} avatar` : "",
      initials: peers.agents.agentInitials(agentData),
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
        const teamAgents = values(peers.app.state.agents).filter(
          (agent) => text(agent.team_id) === text(team.id) && peers.app.isVisibleAgent(agent),
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
        focused ? `org-focus-node-card--${peers.agents.agentAwakeState(node.agentData).kind}` : "",
        focused && peers.agents.activityToneClass(node.agentData.activitySummary?.tone)
          ? `org-focus-node-card--activity-${peers.agents.activityToneClass(node.agentData.activitySummary?.tone)}`
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
                ${peers.agents.renderAgentAvatar(node.agentData)}
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
                ${peers.agents.renderAgentRosterStatusPill(node.agentData)}
                ${focused ? peers.agents.renderAgentAwakePill(node.agentData) : ""}
                ${peers.agents.renderAgentInboxHeaderIndicators(node.agentData)}
              </div>
            </div>
            <span id="${escapeAttr(descriptionId)}" class="visually-hidden">Agent ID ${escapeHtml(node.agentData.id)}. Role ${escapeHtml(node.agentData.role)}.</span>
            ${focused ? peers.agents.renderOrgNodeWorkSummary(node.agentData) : ""}
            <div class="hierarchy-node-details${focused ? " org-node-hover-panel" : ""}">${peers.agents.renderHierarchyDetails(node.agentData, { focused })}</div>
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

  function teamHierarchyGraph(team) {
    const teamId = text(team?.id);
    const managerId = text(team?.manager_agent_id);
    const teamAgents = values(peers.app.state.agents)
      .filter((agent) => text(agent?.team_id) === teamId)
      .sort(peers.people.comparePeopleAgents);
    const seen = new Set();

    function buildNode(agentId, ancestry = {}) {
      const agent = peers.app.state.agents?.[agentId];
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
          label: `Cycle detected at ${peers.agents.agentLabel(agent)}`,
        };
      }

      seen.add(agentId);

      const nextPath = new Set(ancestry.path || []);
      nextPath.add(agentId);
      const childIds = uniqueTextList(agent.subordinate_agent_ids);
      const children = [];
      childIds.forEach((childId) => {
        const child = peers.app.state.agents?.[childId];
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
        if (!peers.app.showArchived && peers.app.isArchivedAgent(child)) {
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
        agentData: peers.agents.agentCardData(agent),
        children,
        directSubordinateCount: childIds.length,
        reachableSubordinateCount: children.filter((child) => child?.kind === "agent").length,
      };
    }

    const roots = [];
    const orphanRoots = [];
    if (managerId) {
      const manager = peers.app.state.agents?.[managerId];
      if (!manager || text(manager.team_id) !== teamId) {
        roots.push({
          kind: "missing-manager",
          id: managerId,
          teamId,
          label: `Missing manager ${managerId}`,
        });
      } else if (peers.app.showArchived || !peers.app.isArchivedAgent(manager)) {
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
      if (!peers.app.showArchived && peers.app.isArchivedAgent(agent)) {
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

  function renderGoals(projectId, goals, { compact = false } = {}) {
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
                  <details ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("goal", projectId, goal.id, "detail"))}>
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

  return {
    bindOrgFlowEvents,
    bindOrgFocusTabs,
    closeOrgActionDialog,
    renderFocusedOrgChart,
    renderGoals,
    showOrgActionDialog,
    submitOrgActionForm,
  };
}
