// Governance group editor, goals, agenda, and member teams.
import { elements } from "./team-ui-dom.js";
import {
  escapeAttr,
  escapeHtml,
  formatUsd,
  humanizeToken,
  prioritySortValue,
  responseText,
  text,
  values,
} from "./team-ui-format.js";
import { API_BASE, recordStates } from "./team-ui-config.js";

export function createTeamGovernanceController(peers) {

  let showHandledGovernanceAgenda = false;

  let governanceCreateGroupPanelOpen = false;

  function openGovernanceCreateGroupPanel() {
    governanceCreateGroupPanelOpen = true;
    syncGovernanceCreateGroupPanel();
    window.requestAnimationFrame(() => {
      elements.governanceCreateGroupForm?.elements.name?.focus();
    });
  }

  function closeGovernanceCreateGroupPanel({ reset = false } = {}) {
    governanceCreateGroupPanelOpen = false;
    if (reset) {
      elements.governanceCreateGroupForm?.reset();
      initializeGovernanceCreateGroupGoals({ reset: true });
    }
    syncGovernanceCreateGroupPanel();
  }

  function initializeGovernanceCreateGroupGoals({ reset = false } = {}) {
    const form = elements.governanceCreateGroupForm;
    const host = form?.querySelector?.("[data-governance-create-goals]");
    if (!host || (!reset && host.querySelector("[data-governance-goals-editor]"))) {
      return;
    }
    host.innerHTML = renderGovernanceGoalsEditor([]);
    peers.tooltips.decorateFieldTooltips(host);
    syncGovernanceGoalEditor(host.querySelector("[data-governance-goals-editor]"));
  }

  function toggleGovernanceCreateGroupPanel() {
    if (governanceCreateGroupPanelOpen) {
      closeGovernanceCreateGroupPanel();
      return;
    }
    openGovernanceCreateGroupPanel();
  }

  function syncGovernanceCreateGroupPanel() {
    if (!elements.governanceCreateGroupPanel) {
      return;
    }
    const open = governanceCreateGroupPanelOpen && peers.app.activeTeamView === "governance";
    elements.governanceCreateGroupPanel.hidden = !open;
    elements.governanceCreateGroupToggle?.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function renderGovernance() {
    if (!document.querySelector("[data-governance-view]")) {
      return;
    }
    const groups = values(peers.app.state.governance_groups)
      .filter(peers.app.isVisibleGovernanceGroup)
      .sort(peers.people.compareGovernanceGroups);
    if (!groups.length) {
      peers.app.renderIntoActiveTargets("[data-governance-view]", `
        <p class="empty-state">No governance groups registered.</p>
      `);
      return;
    }
    peers.app.renderIntoActiveTargets(
      "[data-governance-view]",
      `<ul class="record-list governance-list">${groups.map(renderGovernanceGroup).join("")}</ul>`,
    );
  }

  function renderGovernanceGroup(group) {
    const manager = peers.app.state.agents?.[group.manager_agent_id] || null;
    const memberTeams = (group.member_team_ids || [])
      .map((teamId) => peers.app.state.teams?.[teamId])
      .filter(Boolean)
      .sort(peers.people.comparePeopleTeams);
    const agenda = governanceAgendaForGroup(group.id);
    const pendingAgenda = agenda.filter((item) => item.state === "pending");
    const visibleAgenda = showHandledGovernanceAgenda
      ? agenda
      : agenda.filter((item) => item.state !== "handled");
    const cost = peers.costs.governanceCostForGroup(group.id);
    const capReached = cost?.cost_limit_reached || (cost?.member_team_cap_reached_ids || []).length > 0;
    return `
      <li class="record-item governance-item">
        <div class="record-line">
          <strong>${escapeHtml(group.name)}</strong>
          <div class="record-actions">
            ${group.archived ? `<span class="state-pill blocked">archived</span>` : ""}
            ${capReached ? `<span class="state-pill warning">cost attention</span>` : ""}
            ${peers.access.editButton("governance-group", group.id, "Edit")}
          </div>
        </div>
        <div class="meta-line">Manager ${escapeHtml(manager ? peers.agents.agentLabel(manager) : "Unavailable")} · ${escapeHtml(manager ? peers.agents.agentRoleLabel(manager) : "Role unavailable")}</div>
        ${cost ? peers.costs.renderGovernanceCostCounter(cost) : ""}
        ${renderGovernanceGoals(group.goals || [])}
        ${renderGovernanceMemberTeams(memberTeams, cost)}
        ${renderGovernanceAgenda(group, visibleAgenda, pendingAgenda)}
        ${peers.reviews.renderTechnicalDetails([
          ["Governance group ID", group.id],
          ["Manager agent ID", group.manager_agent_id],
        ], {
          disclosureKey: peers.disclosures.teamDisclosureKey("governance-group", group.id, "technical"),
        })}
      </li>
    `;
  }

  function renderGovernanceMemberTeams(memberTeams, cost) {
    if (!memberTeams.length) {
      return `<p class="empty-state">No member teams.</p>`;
    }
    return `
      <div class="governance-subsection">
        <div class="meta-line">Member teams</div>
        <ul class="compact-record-list">
          ${memberTeams
            .map((team) => {
              const teamCost = cost?.member_team_costs?.[team.id] || peers.costs.teamCostForTeam(team.id);
              const costLabel = teamCost ? ` - ${formatUsd(teamCost.total_cost ?? teamCost.estimated_cost)}` : "";
              const cap = teamCost?.cost_limit_reached ? " - cap reached" : "";
              return `<li><strong>${escapeHtml(team.name)}</strong> <span>${escapeHtml(`${costLabel}${cap}`.replace(/^ - /, ""))}</span></li>`;
            })
            .join("")}
        </ul>
      </div>
    `;
  }

  function renderGovernanceGoals(goals) {
    if (!goals.length) {
      return "";
    }
    return `
      <div class="governance-subsection governance-goals-subsection">
        <div class="meta-line">Goals</div>
        <ul class="compact-record-list governance-goal-list">
          ${goals
            .map((goal) => {
              const goalName =
                text(goal.name) && text(goal.name) !== text(goal.id)
                  ? text(goal.name)
                  : text(goal.description) || humanizeToken(goal.id);
              return `
              <li>
                <strong>${escapeHtml(goalName)}</strong>
                ${text(goal.description) && text(goal.description) !== goalName ? `<span>${escapeHtml(goal.description)}</span>` : ""}
              </li>
            `;
            })
            .join("")}
        </ul>
      </div>
    `;
  }

  function renderGovernanceAgenda(group, agenda, pendingAgenda) {
    if (!agenda.length) {
      return `
        <div class="governance-subsection">
          <div class="meta-line">Agenda - ${escapeHtml(pendingAgenda.length)} pending</div>
          <p class="empty-state">No active governance agenda items.</p>
        </div>
      `;
    }
    return `
      <div class="governance-subsection">
        <div class="meta-line">Agenda - ${escapeHtml(pendingAgenda.length)} pending</div>
        <ul class="compact-record-list">
          ${agenda
            .map((item) => {
              const related = [item.team_id && `team ${item.team_id}`, item.project_id && `project ${item.project_id}`, item.agent_id && `agent ${item.agent_id}`]
                .filter(Boolean)
                .join(" - ");
              return `
                <li>
                  <strong>${escapeHtml(item.title)}</strong>
                  <span>${escapeHtml(humanizeToken(item.item_type))} - ${escapeHtml(item.priority || "normal")} - ${escapeHtml(item.state || "pending")}${related ? ` - ${escapeHtml(related)}` : ""}</span>
                  <span>${escapeHtml(item.summary)}</span>
                  ${
                    item.state === "pending"
                      ? `<button class="record-action" type="button" data-governance-agenda-handle-id="${escapeAttr(item.id)}">Mark handled</button>`
                      : ""
                  }
                </li>
              `;
            })
            .join("")}
        </ul>
      </div>
    `;
  }

  function governanceAgendaForGroup(groupId) {
    return values(peers.app.state.governance_agenda)
      .filter((item) => item.governance_group_id === groupId)
      .sort((left, right) => {
        return (
          prioritySortValue(left.priority) - prioritySortValue(right.priority) ||
          String(left.created_at || "").localeCompare(String(right.created_at || "")) ||
          String(left.id || "").localeCompare(String(right.id || ""))
        );
      });
  }

  function bindGovernanceAgendaVisibilityToggle() {
    elements.showHandledGovernanceAgendaToggle?.addEventListener("change", () => {
      const drafts = peers.drafts.captureFormDrafts();
      showHandledGovernanceAgenda = Boolean(elements.showHandledGovernanceAgendaToggle?.checked);
      peers.app.render();
      peers.drafts.restoreFormDrafts(drafts);
    });
  }

  function updateGovernanceAgendaVisibilityToggle() {
    if (elements.showHandledGovernanceAgendaToggle) {
      elements.showHandledGovernanceAgendaToggle.checked = showHandledGovernanceAgenda;
    }
  }

  function governanceTeamEditorRows(selectedIds = []) {
    const selected = new Set((selectedIds || []).map(text));
    const teamsById = new Map(values(peers.app.state.teams).map((team) => [text(team.id), team]));
    selected.forEach((teamId) => {
      if (teamId && !teamsById.has(teamId)) {
        teamsById.set(teamId, { id: teamId, name: "Unavailable team", archived: true });
      }
    });
    return Array.from(teamsById.values())
      .sort(
        (left, right) =>
          text(left.name).localeCompare(text(right.name)) ||
          text(left.id).localeCompare(text(right.id)),
      )
      .map((team) => {
        const teamId = text(team.id);
        const searchText = `${text(team.name)} ${teamId}`.toLowerCase();
        return `
          <label
            class="governance-team-choice"
            data-governance-team-choice
            data-governance-team-search-text="${escapeAttr(searchText)}"
          >
            <input
              type="checkbox"
              name="governance_member_team__${escapeAttr(teamId)}"
              value="${escapeAttr(teamId)}"
              data-governance-member-team-id="${escapeAttr(teamId)}"
              ${selected.has(teamId) ? "checked" : ""}
            />
            <span>
              <strong>${escapeHtml(team.name || "Unnamed team")}</strong>
              ${team.archived ? `<small>Archived or unavailable</small>` : ""}
            </span>
          </label>
        `;
      })
      .join("");
  }

  function renderGovernanceMemberTeamsEditor(selectedIds = []) {
    return `
      <fieldset class="governance-member-teams-editor" data-governance-member-teams-editor>
        <legend>Member teams</legend>
        <label class="governance-team-search-label">
          <span>Search teams</span>
          <input
            type="search"
            name="governance_member_team_search"
            data-governance-team-search
            placeholder="Search by team name"
            autocomplete="off"
          />
        </label>
        <div class="governance-selected-teams" data-governance-selected-teams aria-live="polite"></div>
        <div class="governance-team-choices" data-governance-team-choices>
          ${governanceTeamEditorRows(selectedIds)}
        </div>
        <p class="field-help">Choose the teams supervised by this group. Team reporting lines are unchanged.</p>
      </fieldset>
    `;
  }

  function governanceMemberTeamIdsFromForm(form) {
    return Array.from(
      form.querySelectorAll("[data-governance-member-team-id]:checked"),
    )
      .map((input) => text(input.value))
      .filter(Boolean);
  }

  function syncGovernanceMemberTeamEditor(editor) {
    if (!editor) {
      return;
    }
    const selected = Array.from(
      editor.querySelectorAll("[data-governance-member-team-id]:checked"),
    );
    const summary = editor.querySelector("[data-governance-selected-teams]");
    if (summary) {
      summary.innerHTML = selected.length
        ? selected
            .map((input) => {
              const teamId = text(input.value);
              const team = peers.app.state.teams?.[teamId];
              const label = team?.name || "Unavailable team";
              const attrs = peers.access.lockedAttrs("change governance member teams");
              return `
                <span class="governance-team-chip">
                  <span>${escapeHtml(label)}</span>
                  <button
                    type="button"
                    data-governance-member-team-remove="${escapeAttr(teamId)}"
                    aria-label="${escapeAttr(`Remove ${label}`)}"
                    ${attrs}
                  >×</button>
                </span>
              `;
            })
            .join("")
        : `<span class="empty-state">No member teams selected.</span>`;
    }
    const search = editor.querySelector("[data-governance-team-search]");
    filterGovernanceMemberTeams(editor, search?.value || "");
  }

  function filterGovernanceMemberTeams(editor, value) {
    if (!editor) {
      return;
    }
    const query = text(value).toLowerCase();
    editor.querySelectorAll("[data-governance-team-choice]").forEach((row) => {
      const checkbox = row.querySelector("[data-governance-member-team-id]");
      const matches =
        !query ||
        text(row.dataset.governanceTeamSearchText).includes(query) ||
        Boolean(checkbox?.checked);
      row.hidden = !matches;
    });
  }

  function removeGovernanceMemberTeam(button) {
    const editor = button.closest("[data-governance-member-teams-editor]");
    const teamId = text(button.dataset.governanceMemberTeamRemove);
    const checkbox = Array.from(
      editor?.querySelectorAll?.("[data-governance-member-team-id]") || [],
    ).find((candidate) => text(candidate.value) === teamId);
    if (!checkbox) {
      return;
    }
    checkbox.checked = false;
    syncGovernanceMemberTeamEditor(editor);
    peers.drafts.rememberFormDraft(checkbox.form);
  }

  function renderGovernanceGoalsEditor(goals = []) {
    return `
      <fieldset class="governance-goals-editor" data-governance-goals-editor>
        <legend>Goals</legend>
        <div class="governance-goal-rows" data-governance-goal-rows>
          ${(goals || []).map((goal) => renderGovernanceGoalEditorRow(goal)).join("")}
        </div>
        <p class="governance-goals-empty" data-governance-goals-empty>No goals yet.</p>
        <button
          class="record-action governance-goal-add"
          type="button"
          data-governance-goal-add
        >Add goal</button>
        <p class="field-help">Each goal has its own name and description. Its stable ID is managed as technical metadata.</p>
      </fieldset>
    `;
  }

  function renderGovernanceGoalEditorRow(goal = {}) {
    const goalName =
      text(goal.name) || text(goal.description) || humanizeToken(goal.id) || "";
    const stateValue = text(goal.state) || "pending";
    return `
      <div class="governance-goal-row" data-governance-goal-row>
        <input type="hidden" name="governance_goal_id" value="${escapeAttr(goal.id || "")}" />
        <label>
          <span>Name</span>
          <input
            name="governance_goal_name"
            value="${escapeAttr(goalName)}"
            placeholder="Improve delivery predictability"
            required
          />
        </label>
        <label>
          <span>Description</span>
          <textarea
            name="governance_goal_description"
            rows="2"
            placeholder="Describe the intended outcome and how operators recognize it."
            required
          >${escapeHtml(goal.description || "")}</textarea>
        </label>
        <label>
          <span>State</span>
          <select name="governance_goal_state" required>
            ${recordStates.map((value) => `
              <option value="${escapeAttr(value)}"${stateValue === value ? " selected" : ""}>${escapeHtml(humanizeToken(value))}</option>
            `).join("")}
          </select>
        </label>
        <div class="governance-goal-row-actions" aria-label="Goal order and removal">
          <button
            class="record-action"
            type="button"
            data-governance-goal-move="up"
            aria-label="Move goal up"
          >Move up</button>
          <button
            class="record-action"
            type="button"
            data-governance-goal-move="down"
            aria-label="Move goal down"
          >Move down</button>
          <button
            class="record-action governance-goal-remove"
            type="button"
            data-governance-goal-remove
          >Remove</button>
        </div>
      </div>
    `;
  }

  function syncGovernanceGoalEditor(editor) {
    if (!editor) {
      return;
    }
    const rows = Array.from(editor.querySelectorAll("[data-governance-goal-row]"));
    const mutationLocked = peers.access.teamMutationLocked();
    rows.forEach((row, index) => {
      const up = row.querySelector('[data-governance-goal-move="up"]');
      const down = row.querySelector('[data-governance-goal-move="down"]');
      if (up) {
        up.disabled = mutationLocked || index === 0;
        up.setAttribute("aria-disabled", String(up.disabled));
      }
      if (down) {
        down.disabled = mutationLocked || index === rows.length - 1;
        down.setAttribute("aria-disabled", String(down.disabled));
      }
    });
    const empty = editor.querySelector("[data-governance-goals-empty]");
    if (empty) {
      empty.hidden = rows.length > 0;
    }
  }

  function addGovernanceGoalRow(editor) {
    const rows = editor?.querySelector?.("[data-governance-goal-rows]");
    if (!rows) {
      return;
    }
    const wrapper = document.createElement("div");
    wrapper.innerHTML = renderGovernanceGoalEditorRow({});
    const row = wrapper.firstElementChild;
    if (!row) {
      return;
    }
    rows.appendChild(row);
    peers.tooltips.decorateFieldTooltips(row);
    syncGovernanceGoalEditor(editor);
    peers.drafts.rememberFormDraft(editor.closest("form"));
    row.querySelector('[name="governance_goal_name"]')?.focus();
  }

  function removeGovernanceGoalRow(button) {
    const editor = button.closest("[data-governance-goals-editor]");
    button.closest("[data-governance-goal-row]")?.remove();
    syncGovernanceGoalEditor(editor);
    peers.drafts.rememberFormDraft(editor?.closest("form"));
  }

  function moveGovernanceGoalRow(button) {
    const editor = button.closest("[data-governance-goals-editor]");
    const row = button.closest("[data-governance-goal-row]");
    const direction = text(button.dataset.governanceGoalMove);
    if (!editor || !row) {
      return;
    }
    if (direction === "up" && row.previousElementSibling) {
      row.parentElement.insertBefore(row, row.previousElementSibling);
    } else if (direction === "down" && row.nextElementSibling) {
      row.parentElement.insertBefore(row.nextElementSibling, row);
    }
    syncGovernanceGoalEditor(editor);
    peers.drafts.rememberFormDraft(editor.closest("form"));
    button.focus();
  }

  function governanceGoalsDraftFromForm(form) {
    return Array.from(form.querySelectorAll("[data-governance-goal-row]")).map(
      (row) => ({
        id: text(row.querySelector('[name="governance_goal_id"]')?.value),
        name: text(row.querySelector('[name="governance_goal_name"]')?.value),
        description: text(
          row.querySelector('[name="governance_goal_description"]')?.value,
        ),
        state:
          text(row.querySelector('[name="governance_goal_state"]')?.value) ||
          "pending",
      }),
    );
  }

  function restoreGovernanceGoalsDraft(form, goals) {
    const editor = form.querySelector("[data-governance-goals-editor]");
    const rows = editor?.querySelector?.("[data-governance-goal-rows]");
    if (!rows || !Array.isArray(goals)) {
      return;
    }
    rows.innerHTML = goals.map((goal) => renderGovernanceGoalEditorRow(goal)).join("");
    peers.tooltips.decorateFieldTooltips(rows);
    syncGovernanceGoalEditor(editor);
  }

  function validateGovernanceGroupEditor(form) {
    const rows = Array.from(form.querySelectorAll("[data-governance-goal-row]"));
    const names = new Map();
    let valid = true;
    rows.forEach((row) => {
      const name = row.querySelector('[name="governance_goal_name"]');
      const description = row.querySelector('[name="governance_goal_description"]');
      const stateField = row.querySelector('[name="governance_goal_state"]');
      name?.setCustomValidity("");
      description?.setCustomValidity("");
      stateField?.setCustomValidity("");
      if (!text(name?.value)) {
        name?.setCustomValidity("Give this governance goal a name.");
        valid = false;
      }
      if (!text(description?.value)) {
        description?.setCustomValidity("Describe the intended governance outcome.");
        valid = false;
      }
      if (!recordStates.includes(text(stateField?.value))) {
        stateField?.setCustomValidity("Choose a valid governance goal state.");
        valid = false;
      }
      const normalizedName = text(name?.value).toLowerCase();
      if (normalizedName) {
        if (names.has(normalizedName)) {
          name?.setCustomValidity("Goal names must be unique within the group.");
          names.get(normalizedName)?.setCustomValidity(
            "Goal names must be unique within the group.",
          );
          valid = false;
        } else {
          names.set(normalizedName, name);
        }
      }
    });
    if (!form.checkValidity()) {
      valid = false;
    }
    if (!valid) {
      form.reportValidity();
      peers.app.setStatus("Fix the highlighted governance goal before saving.", "error");
    }
    return valid;
  }

  function governanceGoalsFromForm(form) {
    return governanceGoalsDraftFromForm(form).map((goal) => {
      const payload = {
        name: goal.name,
        description: goal.description,
        state: goal.state,
      };
      if (goal.id) {
        payload.id = goal.id;
      }
      return payload;
    });
  }

  async function markGovernanceAgendaHandled(button, agendaItemId) {
    if (!peers.access.guardTeamMutation("mark governance agenda handled")) {
      return;
    }
    if (!agendaItemId) {
      peers.app.setStatus("No governance agenda item selected.", "error");
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    button.disabled = true;
    peers.app.setStatus("Marking governance agenda item handled.", "loading");
    try {
      const endpoint = `/governance-agenda/${encodeURIComponent(agendaItemId)}/handle`;
      const response = await peers.access.teamFetch(
        `${API_BASE}${endpoint}`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Team-Addon-Response-Scope": "view",
          },
          body: JSON.stringify(peers.mutations.mutationBodyWithConflictMetadata(endpoint, "POST", {})),
        },
      );

      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        const current = conflict.current_revision ?? peers.app.state.revision;
        peers.app.setStatus(`Revision conflict. Reloaded revision ${current}; mark handled again.`, "warning");
        return;
      }

      if (!response.ok) {
        throw new Error(await responseText(response));
      }

      await peers.mutations.applyMutationResultPayload(await response.json());
      peers.drafts.restoreFormDrafts(drafts);
      peers.app.setStatus(`Marked governance agenda item ${agendaItemId} handled.`, "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Agenda update failed.", "error");
    } finally {
      if (button.isConnected) {
        button.disabled = false;
      }
    }
  }
  function handleGovernanceEditorClick(event) {
    const governanceGoalAdd = event.target?.closest?.("[data-governance-goal-add]");
    if (governanceGoalAdd) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("edit governance goals")) {
        return true;
      }
      addGovernanceGoalRow(
        governanceGoalAdd.closest("[data-governance-goals-editor]"),
      );
      return true;
    }

    const governanceGoalRemove = event.target?.closest?.("[data-governance-goal-remove]");
    if (governanceGoalRemove) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("edit governance goals")) {
        return true;
      }
      removeGovernanceGoalRow(governanceGoalRemove);
      return true;
    }

    const governanceGoalMove = event.target?.closest?.("[data-governance-goal-move]");
    if (governanceGoalMove) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("reorder governance goals")) {
        return true;
      }
      moveGovernanceGoalRow(governanceGoalMove);
      return true;
    }

    const governanceMemberRemove = event.target?.closest?.("[data-governance-member-team-remove]");
    if (governanceMemberRemove) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("change governance member teams")) {
        return true;
      }
      removeGovernanceMemberTeam(governanceMemberRemove);
      return true;
    }
    return false;
  }
  function handleMemberTeamChange(event) {
    const governanceMember = event.target?.closest?.("[data-governance-member-team-id]");
    if (governanceMember instanceof HTMLInputElement) {
      syncGovernanceMemberTeamEditor(
        governanceMember.closest("[data-governance-member-teams-editor]"),
      );
      peers.drafts.rememberFormDraft(governanceMember.form);
      return true;
    }
    return false;
  }
  function handleGoalInput(event) {
    const governanceGoalField = event.target?.closest?.(
      '[name="governance_goal_name"], [name="governance_goal_description"], [name="governance_goal_state"]',
    );
    if (
      governanceGoalField instanceof HTMLInputElement ||
      governanceGoalField instanceof HTMLTextAreaElement ||
      governanceGoalField instanceof HTMLSelectElement
    ) {
      governanceGoalField.form
        ?.querySelectorAll?.(
          '[name="governance_goal_name"], [name="governance_goal_description"], [name="governance_goal_state"]',
        )
        .forEach((field) => field.setCustomValidity(""));
    }
    return false;
  }
  function handleMemberTeamSearch(event) {
    const governanceTeamSearch = event.target?.closest?.("[data-governance-team-search]");
    if (!(governanceTeamSearch instanceof HTMLInputElement)) {
      return true;
    }
    filterGovernanceMemberTeams(
      governanceTeamSearch.closest("[data-governance-member-teams-editor]"),
      governanceTeamSearch.value,
    );
    return false;
  }
  function handleGroupCreationClick(event) {
    const governanceCreateGroupButton = event.target.closest("[data-governance-create-toggle]");
    if (governanceCreateGroupButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("create governance groups")) {
        return true;
      }
      toggleGovernanceCreateGroupPanel();
      return true;
    }
    return false;
  }
  function handleAgendaClick(event) {
    const governanceAgendaHandleButton = event.target.closest("[data-governance-agenda-handle-id]");
    if (governanceAgendaHandleButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("mark governance agenda handled")) {
        return true;
      }
      void markGovernanceAgendaHandled(
        governanceAgendaHandleButton,
        governanceAgendaHandleButton.dataset.governanceAgendaHandleId,
      );
      return true;
    }
    return false;
  }

  return {
    bindGovernanceAgendaVisibilityToggle,
    closeGovernanceCreateGroupPanel,
    governanceAgendaForGroup,
    governanceGoalsDraftFromForm,
    governanceGoalsFromForm,
    governanceMemberTeamIdsFromForm,
    handleMemberTeamChange,
    handleGovernanceEditorClick,
    handleGoalInput,
    handleMemberTeamSearch,
    handleAgendaClick,
    handleGroupCreationClick,
    initializeGovernanceCreateGroupGoals,
    renderGovernance,
    renderGovernanceGoalsEditor,
    renderGovernanceMemberTeamsEditor,
    restoreGovernanceGoalsDraft,
    syncGovernanceCreateGroupPanel,
    syncGovernanceGoalEditor,
    syncGovernanceMemberTeamEditor,
    updateGovernanceAgendaVisibilityToggle,
    validateGovernanceGroupEditor,
  };
}
