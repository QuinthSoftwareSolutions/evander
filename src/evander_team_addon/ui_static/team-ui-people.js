// Team record selection, inline creation, paging, and inspectors.
import { elements } from "./team-ui-dom.js";
import {
  byId,
  byPriority,
  clampPage,
  escapeAttr,
  escapeHtml,
  humanizeToken,
  numberOrNull,
  renderMultiSelectOptions,
  renderSelectOptions,
  splitList,
  text,
  values,
} from "./team-ui-format.js";
import { noTeamRecordId, noTeamRecordName, teamRecordPageSize, terminalTaskStates } from "./team-ui-config.js";

export function createTeamPeopleController(peers) {

  let selectedTeamRecordKind = "agent";

  let selectedTeamRecordId = null;

  let teamRecordReturnFocus = null;

  const expandedTeamRecordGroupIds = new Set();

  const teamRecordGroupTabs = {};

  const teamRecordGroupPages = {};

  const openTeamInlineCreateForms = new Set();

  let teamsCreateTeamPanelOpen = false;

  function openTeamsCreateTeamPanel() {
    teamsCreateTeamPanelOpen = true;
    syncTeamsCreateTeamPanel();
    window.requestAnimationFrame(() => {
      elements.teamsCreateTeamForm?.elements.name?.focus();
    });
  }

  function closeTeamsCreateTeamPanel({ reset = false } = {}) {
    teamsCreateTeamPanelOpen = false;
    if (reset) {
      elements.teamsCreateTeamForm?.reset();
    }
    syncTeamsCreateTeamPanel();
  }

  function syncTeamsCreateTeamPanel() {
    if (!elements.teamsCreateTeamPanel) {
      return;
    }
    elements.teamsCreateTeamPanel.hidden = !teamsCreateTeamPanelOpen || peers.app.activeTeamView !== "people";
  }

  function renderAgents() {
    try {
      const allAgents = values(peers.app.state.agents).sort(comparePeopleAgents);
      const agents = allAgents.filter(peers.app.isVisibleAgent);
      if (peers.app.activeTeamView === "people") {
        renderPeopleAgents(allAgents, agents);
        return;
      }
      peers.app.renderIntoActiveTargets("[data-agent-detail-view]", "");
      peers.app.renderIntoActiveTargets("[data-teams-records-view]", "");
      if (!allAgents.length) {
        peers.app.renderIntoActiveTargets("[data-agents-view]", `<p class="empty-state">No agents registered.</p>`);
        return;
      }
      if (!agents.length) {
        peers.app.renderIntoActiveTargets("[data-agents-view]", `<p class="empty-state">No visible agents.</p>`);
        return;
      }
      peers.app.renderIntoActiveTargets("[data-agents-view]", `
        <ul class="agent-list">
          ${agents
            .map((agent) => {
              const details = peers.agents.agentCardData(agent);
              return `
                  <li class="agent-item">
                    <div class="agent-line">
                      <div class="agent-title-with-avatar">
                        ${peers.agents.renderAgentAvatar(details)}
                        <strong>${escapeHtml(details.label)}</strong>
                      </div>
                      <div class="record-actions">
                        ${peers.agents.renderAgentRosterStatusPill(details)}
                        ${peers.agents.renderAgentStandDownControls(details.id, details)}
                        ${peers.voice.renderOpenAgentChatButton(details.id)}
                        ${peers.voice.renderOpenAgentSessionButton(details.sessionId)}
                        ${["every_wake", "work_lineage"].includes(details.runtime.contextClearingPolicy) ? "" : `<button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(details.id)}">Clear context</button>`}
                        ${peers.access.editButton("agent", details.id)}
                      </div>
                    </div>
                  <div class="meta-line">${escapeHtml(details.role)} - ${escapeHtml(details.id)}</div>
                  <div class="meta-line">Reports to ${escapeHtml(details.reportsToLabel)} - ${details.taskStats.openCount} open task(s) - max ephemeral ${details.ephemeralCapacity}</div>
                  ${details.standDownActive ? `<div class="meta-line">${escapeHtml(peers.agents.agentStandDownSummary(details))}</div>` : ""}
                  ${peers.agents.renderAgentOpenTasks(details.taskStats.openTasks)}
                  ${peers.agents.renderAgentInbox(agent.id)}
                </li>
              `;
            })
            .join("")}
        </ul>
      `);
    } finally {
      peers.access.applyLockedMutationControls();
    }
  }

  function renderPeopleAgents(allAgents, agents) {
    peers.app.renderIntoActiveTargets("[data-agents-view]", "");
    const visibleTeams = peopleViewTeams();
    const visibleTeamIds = new Set(visibleTeams.map((team) => text(team.id)));
    const teamAgents = agents.filter((agent) => isTeamInPeopleScope(agent, visibleTeamIds));
    const allProjects = values(peers.app.state.projects).sort(compareTeamProjects);
    const projects = allProjects.filter((project) => peers.app.isVisibleProject(project) && visibleTeamIds.has(text(project.team_id)));
    const allRoles = values(peers.app.state.roles).sort(compareTeamRoles);
    const roles = allRoles.filter((role) => peers.app.isVisibleRole(role) && visibleTeamIds.has(text(role.team_id)));

    if (!visibleTeams.length) {
      clearSelectedTeamRecord();
      peers.app.renderIntoActiveTargets("[data-teams-records-view]", `<p class="empty-state">No visible teams.</p>`);
      peers.app.renderIntoActiveTargets("[data-agent-detail-view]", renderTeamRecordDetail(null));
      return;
    }

    const scopedTeams = visibleTeams;
    const scopedTeamIds = visibleTeamIds;
    const scopedRecords = {
      agents: teamAgents.sort(comparePeopleAgents),
      projects: projects.filter((project) => scopedTeamIds.has(text(project.team_id))).sort(compareTeamProjects),
      roles: roles.filter((role) => scopedTeamIds.has(text(role.team_id))).sort(compareTeamRoles),
    };
    const selectedRecord = selectedTeamRecordFrom(scopedRecords);
    peers.app.renderIntoActiveTargets("[data-teams-records-view]", `
      <div class="team-record-groups">
        ${scopedTeams.map((team) => renderTeamRecordGroup(team, scopedRecords)).join("")}
      </div>
    `);
    peers.app.renderIntoActiveTargets("[data-agent-detail-view]", renderTeamRecordDetail(selectedRecord));
    const detailPanel = document.querySelector(".people-agent-detail-panel");
    if (detailPanel) detailPanel.hidden = !selectedRecord;
    document.querySelector(".people-layout")?.classList.toggle("has-inspector", Boolean(selectedRecord));
  }

  function renderTeamRecordGroup(team, scopedRecords) {
    const teamId = text(team.id);
    const teamLabel = isNoTeamRecord(team) ? noTeamRecordName : teamId;
    const teamTitle = isNoTeamRecord(team) ? noTeamRecordName : text(team.name || teamId);
    const isNoTeam = isNoTeamRecord(team);
    const activeKind = isNoTeam ? "agent" : normalizedTeamRecordKind(teamRecordGroupTabs[teamId] || "agent");
    teamRecordGroupTabs[teamId] = activeKind;
    const teamRecords = teamRecordsForTeam(teamId, scopedRecords);
    const expanded = expandedTeamRecordGroupIds.has(teamId);
    const selected = selectedTeamRecordKind === "team" && text(selectedTeamRecordId) === text(teamId);
    const counts = [
      `${teamRecords.agents.length} agent${teamRecords.agents.length === 1 ? "" : "s"}`,
    ];
    if (!isNoTeam) {
      counts.push(
        `${teamRecords.projects.length} project${teamRecords.projects.length === 1 ? "" : "s"}`,
        `${teamRecords.roles.length} role${teamRecords.roles.length === 1 ? "" : "s"}`,
      );
    }
    return `
      <article class="team-record-group${expanded ? " is-expanded" : " is-collapsed"}${selected ? " is-selected" : ""}" data-team-record-group-id="${escapeAttr(teamId)}">
        <button
          class="team-record-group-header"
          type="button"
          aria-expanded="${expanded ? "true" : "false"}"
          aria-pressed="${selected ? "true" : "false"}"
          data-team-record-toggle="${escapeAttr(teamId)}"
        >
          <span class="team-record-group-title">
            <span class="section-label">${escapeHtml(teamLabel)}</span>
            <strong>${escapeHtml(teamTitle)}</strong>
          </span>
          <span class="team-record-group-counts">
            ${counts.map((count) => `<span class="state-pill neutral">${escapeHtml(count)}</span>`).join("")}
          </span>
        </button>
        ${
          expanded
            ? `<div class="team-record-group-body">
                ${renderTeamRecordKindTabs(teamId, teamRecords, activeKind)}
                ${renderTeamRecordSection(activeKind, teamRecords)}
              </div>`
            : ""
        }
      </article>
    `;
  }

  function renderTeamRecordKindTabs(teamId, teamRecords, activeKind) {
    const isNoTeam = isNoTeamRecord({ id: text(teamId) });
    const tabs = isNoTeam
      ? [["agent", "Agents", teamRecords.agents.length]]
      : [
        ["agent", "Agents", teamRecords.agents.length],
        ["project", "Projects", teamRecords.projects.length],
        ["role", "Roles", teamRecords.roles.length],
      ];
    return `
      <div class="team-record-kind-tabs" role="tablist" aria-label="${escapeAttr(`Records for ${teamId}`)}">
        ${tabs
          .map(([kind, label, count]) => {
            const active = kind === activeKind;
            return `
              <button
                type="button"
                class="team-record-kind-tab${active ? " active" : ""}"
                role="tab"
                aria-selected="${active ? "true" : "false"}"
                data-team-record-group-id="${escapeAttr(teamId)}"
                data-team-record-tab="${escapeAttr(kind)}"
              >
                <span>${escapeHtml(label)}</span>
                <strong>${escapeHtml(count)}</strong>
              </button>
            `;
          })
          .join("")}
      </div>
    `;
  }

  function renderTeamRecordSection(kind, teamRecords) {
    const config = teamRecordSectionConfig(kind);
    const records = teamRecords[config.collection] || [];
    const pageKey = teamRecordPageKey(teamRecords.teamId, kind);
    const totalPages = Math.max(1, Math.ceil(records.length / teamRecordPageSize));
    const page = clampPage(teamRecordGroupPages[pageKey] || 1, totalPages);
    teamRecordGroupPages[pageKey] = page;
    const pageRecords = records.slice((page - 1) * teamRecordPageSize, page * teamRecordPageSize);
    const isNoTeam = isNoTeamRecord({ id: teamRecords.teamId });
    const canCreate = config.createLabel && (!isNoTeam || normalizedTeamRecordKind(kind) === "agent");
    const createOpen = canCreate && isTeamInlineCreateFormOpen(teamRecords.teamId, kind);
    return `
      <section class="team-record-section" aria-label="${escapeAttr(config.title)}">
        <div class="team-record-section-header">
          <div class="team-record-section-title">
            <h3>${escapeHtml(config.title)}</h3>
            <span>${escapeHtml(records.length)}</span>
          </div>
          ${
            canCreate
              ? `<button
                  type="button"
                  class="team-inline-create-toggle"
                  aria-expanded="${createOpen ? "true" : "false"}"
                  data-team-inline-create-toggle
                  data-team-record-group-id="${escapeAttr(teamRecords.teamId)}"
                  data-team-record-kind="${escapeAttr(kind)}"
                >${escapeHtml(createOpen ? "Close" : config.createLabel)}</button>`
              : ""
          }
        </div>
        ${renderTeamInlineCreateForm(teamRecords.teamId, kind)}
        ${
          records.length
            ? `<ul class="team-record-list compact-record-list">${pageRecords.map((record) => config.render(record)).join("")}</ul>
               ${renderTeamRecordPager(teamRecords.teamId, kind, page, totalPages, records.length)}`
            : `<p class="empty-state">${escapeHtml(config.emptyMessage)}</p>`
        }
      </section>
    `;
  }

  function teamRecordSectionConfig(kind) {
    if (kind === "project") return { title: "Projects", collection: "projects", render: renderSelectableProject, emptyMessage: "No projects.", createLabel: "Add project" };
    if (kind === "role") return { title: "Roles", collection: "roles", render: renderSelectableRole, emptyMessage: "No roles.", createLabel: "Add role" };
    return { title: "Agents", collection: "agents", render: renderSelectableAgent, emptyMessage: "No agents.", createLabel: "Add agent" };
  }

  function renderTeamInlineCreateForm(teamId, kind) {
    const normalizedTeamId = text(teamId);
    const normalizedKind = normalizedTeamRecordKind(kind);
    if (!normalizedTeamId || !isTeamInlineCreateFormOpen(normalizedTeamId, normalizedKind)) {
      return "";
    }
    if (normalizedKind === "project") {
      return renderTeamInlineProjectForm(normalizedTeamId);
    }
    if (normalizedKind === "role") {
      return renderTeamInlineRoleForm(normalizedTeamId);
    }
    return renderTeamInlineAgentForm(normalizedTeamId);
  }

  function renderTeamInlineProjectForm(teamId) {
    const formId = teamInlineCreateDomId(teamId, "project");
    return `
      <form
        id="${escapeAttr(formId)}"
        class="team-inline-create-form"
        data-team-inline-create-form
        data-form-id="create-project-form"
        data-endpoint="/projects"
        data-method="POST"
        data-team-record-group-id="${escapeAttr(teamId)}"
        data-team-record-kind="project"
      >
        <input type="hidden" name="team_id" value="${escapeAttr(teamId)}" />
        <div class="team-inline-create-fields">
          <label>Name <input name="name" required /></label>
          <label>Project lead <select name="lead_agent_id" required data-options="project-lead-agents" data-placeholder="Select team agent">${renderSelectOptions(
            peers.selects.projectLeadAgentOptionsForTeam(teamId),
            { placeholder: "Select unassigned team agent" },
          )}</select></label>
          <label>Directory scope <input name="directory_path" value="root" required /></label>
          <label>Goal <textarea name="goal_description" rows="2"></textarea></label>
        </div>
        <details class="team-inline-create-advanced" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("people", "inline-project-create", teamId, "git"))}>
          <summary>Git config</summary>
          <div class="team-inline-create-fields">
            <label>Repository source <input name="repo_source" placeholder="Git URL or local checkout" /></label>
            ${peers.editor.gitImportControls()}
            <label>Managed repository ID <input name="managed_repo_id" placeholder="mr_..." /></label>
            <label>Default base branch for new tasks <input name="base_branch" placeholder="main" /></label>
            <label>Canonical release branch prefix <input name="canonical_release_branch_prefix" /></label>
            <label>Agent commit author name <input name="agent_author_name" placeholder="Evander Bot" /></label>
            <label>Agent commit author email <input name="agent_author_email" placeholder="evander@quinth.se" /></label>
            <label>Agent commit committer name <input name="agent_committer_name" placeholder="Evander Bot" /></label>
            <label>Agent commit committer email <input name="agent_committer_email" placeholder="evander@quinth.se" /></label>
          </div>
        </details>
        <details class="team-inline-create-advanced" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("people", "inline-project-create", teamId, "release-gates"))}>
          <summary>Release gates</summary>
          ${peers.editor.checkboxField("Configure release policy", "release_policy_enabled", false)}
          ${peers.editor.checkboxField("Automatically publish the release branch to the canonical remote", "automatic_release_branch_publication", true)}
          ${peers.policy.renderReleaseGateSequenceEditor()}
          <label>Automated and agent-review policy JSON<textarea name="release_policy_json" rows="10" placeholder='{"verification_commands": [...], "review_requirements": [...]}'></textarea></label>
          ${peers.policy.renderHumanReviewPolicyEditor()}
          <p class="field-help">Git releases require an explicit policy. Enable any gates that should run, or leave all three disabled for an ungated release.</p>
        </details>
        ${renderTeamInlineCreateActions(teamId, "project", "Create project")}
      </form>
    `;
  }

  function renderTeamInlineRoleForm(teamId) {
    const formId = teamInlineCreateDomId(teamId, "role");
    return `
      <form
        id="${escapeAttr(formId)}"
        class="team-inline-create-form"
        data-team-inline-create-form
        data-form-id="create-role-form"
        data-endpoint="/roles"
        data-method="POST"
        data-team-record-group-id="${escapeAttr(teamId)}"
        data-team-record-kind="role"
      >
        <input type="hidden" name="team_id" value="${escapeAttr(teamId)}" />
        <div class="team-inline-create-fields">
          <label>Project <select name="project_id">${renderSelectOptions(peers.selects.teamProjectOptions(teamId), { placeholder: "Team-wide" })}</select></label>
          <label>Name <input name="name" required /></label>
          <label>Description <textarea name="description" rows="2"></textarea></label>
          <label>Permissions <select name="permissions" multiple size="3">${renderMultiSelectOptions(peers.selects.rolePermissionOptions())}</select></label>
          <label>Max ephemeral agents <input name="max_concurrent" type="number" min="0" value="0" /></label>
        </div>
        ${renderTeamInlineCreateActions(teamId, "role", "Create role")}
      </form>
    `;
  }

  function renderTeamInlineAgentForm(teamId) {
    const formId = teamInlineCreateDomId(teamId, "agent");
    const normalizedTeamId = text(teamId);
    const visibleTeamId = isNoTeamRecord({ id: normalizedTeamId }) ? "" : normalizedTeamId;
    const createSessionDisabled = peers.access.teamPrivilegedUnlocked()
      ? ""
      : peers.access.lockedAttrs("create a core agent session");
    return `
      <form
        id="${escapeAttr(formId)}"
        class="team-inline-create-form"
        data-team-inline-create-form
        data-form-id="register-agent-form"
        data-endpoint="/agents"
        data-method="POST"
        data-team-record-group-id="${escapeAttr(teamId)}"
        data-team-record-kind="agent"
      >
        <input type="hidden" name="team_id" value="${escapeAttr(visibleTeamId)}" />
        <div class="team-inline-create-fields">
          <label>
            Existing agent defaults
            <select
              name="existing_agent_id"
              data-agent-source-picker
              data-session-field="session_id"
              data-display-field="display_name"
              data-role-field="role_id"
              data-project-field="project_id"
              data-reports-to-field="reports_to_agent_id"
              data-max-concurrent-field="max_concurrent"
            >${renderSelectOptions(teamAgentOptions(teamId), { placeholder: "Select existing agent" })}</select>
          </label>
          <label>Project <select name="project_id">${renderSelectOptions(peers.selects.teamProjectOptions(teamId), { placeholder: "No project" })}</select></label>
          <label>Role <select name="role_id">${renderSelectOptions(peers.selects.teamRoleOptions(teamId), { placeholder: "No role" })}</select></label>
          <div class="team-agent-session-controls">
            <label>
              Agent session
              <select
                name="existing_session_id"
                data-core-agent-session-picker
                data-session-field="session_id"
              >${peers.sessions.renderCoreAgentSessionOptions()}</select>
            </label>
            <div class="team-agent-session-actions">
              <button
                type="button"
                class="secondary"
                data-core-agent-session-refresh
                data-tooltip-key="existing_session_id"
              >Refresh sessions</button>
              <button
                type="button"
                class="secondary"
                data-core-agent-session-create
                data-tooltip-key="create_session"
                ${createSessionDisabled}
              >Create new session</button>
            </div>
          </div>
          <label>Manual session ID <input name="session_id" required placeholder="Select or create a session" /></label>
          <label>Display name <input name="display_name" /></label>
          <label>Reports to <select name="reports_to_agent_id">${renderSelectOptions(teamAgentOptions(teamId), { placeholder: "No superior" })}</select></label>
          <label>Max ephemeral agents <input name="max_concurrent" type="number" min="0" value="0" /></label>
        </div>
        ${renderTeamInlineCreateActions(teamId, "agent", "Register agent")}
      </form>
    `;
  }

  function renderTeamInlineCreateActions(teamId, kind, submitLabel) {
    return `
      <div class="team-inline-create-actions">
        <button type="submit">${escapeHtml(submitLabel)}</button>
        <button
          type="button"
          class="secondary"
          data-team-inline-create-close
          data-team-record-group-id="${escapeAttr(teamId)}"
          data-team-record-kind="${escapeAttr(kind)}"
        >Cancel</button>
      </div>
    `;
  }

  function teamAgentOptions(teamId) {
    const normalizedTeamId = text(teamId);
    const agentFilter = isNoTeamRecord({ id: normalizedTeamId })
      ? (agent) => peers.app.isVisibleAgent(agent) && !text(agent.team_id)
      : (agent) => peers.app.isVisibleAgent(agent) && text(agent.team_id) === normalizedTeamId;
    return values(peers.app.state.agents)
      .filter(agentFilter)
      .sort(comparePeopleAgents)
      .map((agent) => [agent.id, `${peers.agents.agentLabel(agent)} (${agent.id})`]);
  }

  function teamInlineCreateKey(teamId, kind) {
    return `${text(teamId)}:${normalizedTeamRecordKind(kind)}`;
  }

  function teamInlineCreateDomId(teamId, kind) {
    return `team-inline-create-${normalizedTeamRecordKind(kind)}-${text(teamId).replace(/[^A-Za-z0-9_-]+/g, "-")}`;
  }

  function isTeamInlineCreateFormOpen(teamId, kind) {
    return openTeamInlineCreateForms.has(teamInlineCreateKey(teamId, kind));
  }

  function toggleTeamInlineCreateForm(teamId, kind) {
    const normalizedTeamId = text(teamId);
    const normalizedKind = normalizedTeamRecordKind(kind);
    if (!normalizedTeamId) {
      return;
    }
    const key = teamInlineCreateKey(normalizedTeamId, normalizedKind);
    let refreshAgentSessions = false;
    if (openTeamInlineCreateForms.has(key)) {
      openTeamInlineCreateForms.delete(key);
    } else {
      openTeamInlineCreateForms.add(key);
      expandedTeamRecordGroupIds.add(normalizedTeamId);
      teamRecordGroupTabs[normalizedTeamId] = normalizedKind;
      if (normalizedKind === "agent") {
        refreshAgentSessions = true;
      }
    }
    renderAgents();
    peers.tooltips.decorateFieldTooltips(document);
    if (refreshAgentSessions) {
      void peers.sessions.refreshCoreAgentSessionsForForm(
        document.getElementById(teamInlineCreateDomId(normalizedTeamId, "agent")),
        { force: false },
      );
    }
  }

  function closeTeamInlineCreateForm(teamId, kind, { rerender = true } = {}) {
    openTeamInlineCreateForms.delete(teamInlineCreateKey(teamId, kind));
    if (rerender) {
      renderAgents();
      peers.tooltips.decorateFieldTooltips(document);
    }
  }

  function closeTeamInlineCreateFormFor(form, { rerender = true } = {}) {
    if (!form?.hasAttribute?.("data-team-inline-create-form")) {
      return;
    }
    closeTeamInlineCreateForm(
      form.dataset.teamRecordGroupId,
      form.dataset.teamRecordKind,
      { rerender },
    );
  }

  function renderTeamRecordPager(teamId, kind, page, totalPages, totalRecords) {
    if (totalPages <= 1) {
      return `<div class="team-record-pager-summary">Showing ${escapeHtml(totalRecords)} ${escapeHtml(kind)}${totalRecords === 1 ? "" : "s"}</div>`;
    }
    return `
      <div class="team-record-pager" aria-label="${escapeAttr(kind)} pages">
        <button type="button" data-team-record-group-id="${escapeAttr(teamId)}" data-team-record-kind="${escapeAttr(kind)}" data-team-record-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button>
        <span>Page ${escapeHtml(page)} of ${escapeHtml(totalPages)} · ${escapeHtml(totalRecords)} total</span>
        <button type="button" data-team-record-group-id="${escapeAttr(teamId)}" data-team-record-kind="${escapeAttr(kind)}" data-team-record-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>Next</button>
      </div>
    `;
  }

  function toggleTeamRecordGroup(teamId) {
    const normalizedTeamId = text(teamId);
    if (!normalizedTeamId) return;
    if (expandedTeamRecordGroupIds.has(normalizedTeamId)) {
      expandedTeamRecordGroupIds.delete(normalizedTeamId);
    } else {
      expandedTeamRecordGroupIds.add(normalizedTeamId);
    }
    renderAgents();
  }

  function setTeamRecordGroupTab(teamId, kind) {
    const normalizedTeamId = text(teamId);
    if (!normalizedTeamId) return;
    teamRecordGroupTabs[normalizedTeamId] = normalizedTeamRecordKind(kind);
    teamRecordGroupPages[teamRecordPageKey(normalizedTeamId, teamRecordGroupTabs[normalizedTeamId])] = 1;
    renderAgents();
  }

  function setTeamRecordGroupPage(teamId, kind, page) {
    const normalizedTeamId = text(teamId);
    if (!normalizedTeamId || !Number.isFinite(page)) return;
    teamRecordGroupPages[teamRecordPageKey(normalizedTeamId, normalizedTeamRecordKind(kind))] = Math.max(1, Math.floor(page));
    renderAgents();
  }

  function normalizedTeamRecordKind(kind) {
    if (kind === "project" || kind === "role") return kind;
    return "agent";
  }

  function teamRecordPageKey(teamId, kind) {
    return `${text(teamId)}:${normalizedTeamRecordKind(kind)}`;
  }

  function teamRecordsForTeam(teamId, records) {
    const normalizedTeamId = text(teamId);
    if (isNoTeamRecord({ id: normalizedTeamId })) {
      return {
        teamId: normalizedTeamId,
        agents: (records.agents || []).filter((agent) => !text(agent.team_id)),
        projects: [],
        roles: [],
      };
    }
    return {
      teamId: normalizedTeamId,
      agents: (records.agents || []).filter((agent) => text(agent.team_id) === normalizedTeamId),
      projects: (records.projects || []).filter((project) => text(project.team_id) === normalizedTeamId),
      roles: (records.roles || []).filter((role) => text(role.team_id) === normalizedTeamId),
    };
  }

  function isNoTeamRecord(team) {
    return text(team?.id) === noTeamRecordId;
  }

  function isTeamInPeopleScope(agent, visibleTeamIds) {
    const teamId = text(agent?.team_id);
    if (!teamId) {
      return visibleTeamIds.has(noTeamRecordId);
    }
    return visibleTeamIds.has(teamId);
  }

  function peopleViewTeams() {
    const visibleTeams = values(peers.app.state.teams).filter(peers.app.isVisibleTeam).sort(comparePeopleTeams);
    const visibleAgents = values(peers.app.state.agents).filter(peers.app.isVisibleAgent);
    const hasNoTeamAgents = visibleAgents.some((agent) => !text(agent.team_id));
    if (!hasNoTeamAgents) {
      return visibleTeams;
    }
    return [...visibleTeams, { id: noTeamRecordId, name: noTeamRecordName, archived: false }];
  }

  function selectedTeamRecordFrom(records) {
    if (!selectedTeamRecordId) {
      return null;
    }
    if (selectedTeamRecordKind === "team") {
      const selectedTeam = peers.app.state.teams[selectedTeamRecordId];
      if (!selectedTeam || !peers.app.isVisibleTeam(selectedTeam)) {
        clearSelectedTeamRecord();
        return null;
      }
      return {
        kind: selectedTeamRecordKind,
        record: selectedTeam,
      };
    }
    const selectedRecord = teamRecordCollection(records, selectedTeamRecordKind)
      .find((record) => text(record.id) === text(selectedTeamRecordId));
    if (!selectedRecord) {
      clearSelectedTeamRecord();
      return null;
    }
    return {
      kind: selectedTeamRecordKind,
      record: selectedRecord,
    };
  }

  function teamRecordCollection(records, kind) {
    if (kind === "team") {
      return [];
    }
    if (kind === "project") {
      return records.projects || [];
    }
    if (kind === "role") {
      return records.roles || [];
    }
    return records.agents || [];
  }

  function clearSelectedTeamRecord() {
    selectedTeamRecordKind = "agent";
    selectedTeamRecordId = null;
    const panel = document.querySelector(".people-agent-detail-panel");
    if (panel) panel.hidden = true;
    document.querySelector(".people-layout")?.classList.remove("has-inspector");
  }

  function closeTeamRecordInspector() {
    clearSelectedTeamRecord();
    renderAgents();
    if (teamRecordReturnFocus) document.querySelector(teamRecordReturnFocus)?.focus({ preventScroll: true });
  }

  function comparePeopleTeams(left, right) {
    return peers.delivery.teamSortKey(left).localeCompare(peers.delivery.teamSortKey(right)) || byId(left, right);
  }

  function compareGovernanceGroups(left, right) {
    return (
      text(left?.name || left?.id).localeCompare(text(right?.name || right?.id)) ||
      byId(left, right)
    );
  }

  function comparePeopleAgents(left, right) {
    return (
      teamRecordSortKey(left).localeCompare(teamRecordSortKey(right)) ||
      peers.agents.agentLabel(left).localeCompare(peers.agents.agentLabel(right)) ||
      byId(left, right)
    );
  }

  function teamRecordSortKey(record) {
    const teamId = text(record?.team_id);
    const team = peers.app.state.teams[teamId];
    return peers.delivery.teamSortKey(team || { id: teamId });
  }

  function compareTeamProjects(left, right) {
    return (
      teamRecordSortKey(left).localeCompare(teamRecordSortKey(right)) ||
      peers.delivery.projectSortKey(left).localeCompare(peers.delivery.projectSortKey(right)) ||
      byId(left, right)
    );
  }

  function compareTeamRoles(left, right) {
    return (
      teamRecordSortKey(left).localeCompare(teamRecordSortKey(right)) ||
      peers.agents.roleLabel(left).localeCompare(peers.agents.roleLabel(right)) ||
      byId(left, right)
    );
  }

  function compareReviewProfiles(left, right) {
    const leftProject = peers.app.state.projects?.[text(left?.project_id)] || { id: text(left?.project_id) };
    const rightProject = peers.app.state.projects?.[text(right?.project_id)] || { id: text(right?.project_id) };
    return (
      teamRecordSortKey(left).localeCompare(teamRecordSortKey(right)) ||
      peers.delivery.projectSortKey(leftProject).localeCompare(peers.delivery.projectSortKey(rightProject)) ||
      text(left?.name || left?.id).localeCompare(text(right?.name || right?.id)) ||
      byId(left, right)
    );
  }

  function selectPeopleAgent(agentId) {
    selectTeamRecord("agent", agentId);
  }

  function selectTeamRecord(kind, recordId, { rerender = true } = {}) {
    const normalizedRecordId = text(recordId);
    if (!normalizedRecordId || !teamRecordExists(kind, normalizedRecordId)) {
      return;
    }
    selectedTeamRecordKind = kind;
    selectedTeamRecordId = normalizedRecordId;
    const attribute = kind === "team" ? "data-team-record-toggle" : `data-select-${kind}-id`;
    teamRecordReturnFocus = `[${attribute}="${CSS.escape(normalizedRecordId)}"]`;
    if (rerender) {
      renderAgents();
      document.querySelector("[data-team-detail-close]")?.focus({ preventScroll: true });
    }
  }

  function teamRecordExists(kind, recordId) {
    if (kind === "team") {
      const team = peers.app.state.teams[recordId];
      return Boolean(team && peers.app.isVisibleTeam(team));
    }
    if (kind === "project") {
      return Boolean(peers.app.state.projects[recordId]);
    }
    if (kind === "role") {
      return Boolean(peers.app.state.roles[recordId]);
    }
    return Boolean(peers.app.state.agents[recordId]);
  }

  function renderSelectableAgent(agent) {
    const details = peers.agents.agentCardData(agent);
    const selected = selectedTeamRecordKind === "agent" && text(selectedTeamRecordId) === details.id;
    return `
      <li class="agent-item agent-item-compact people-agent-card${selected ? " is-selected" : ""}" data-agent-id="${escapeAttr(details.id)}">
        <button
          class="agent-summary compact-record-summary people-agent-select"
          type="button"
          data-select-agent-id="${escapeAttr(details.id)}"
          aria-controls="people-agent-detail-view"
          aria-pressed="${selected ? "true" : "false"}"
        >
            ${peers.agents.renderAgentAvatar(details)}
            <span class="compact-record-main">
              <strong>${escapeHtml(details.label)}</strong>
              <span class="people-agent-role">${escapeHtml(details.role)}</span>
            </span>
        </button>
      </li>
    `;
  }

  function renderSelectableProject(project) {
    const selected = selectedTeamRecordKind === "project" && text(selectedTeamRecordId) === text(project.id);
    const taskCount = values(peers.app.state.tasks).filter((task) => task.project_id === project.id).length;
    const lead = peers.app.state.agents[project.lead_agent_id];
    const leadLabel = lead ? peers.agents.agentLabel(lead) : text(project.lead_agent_id) || "No lead";
    return `
      <li class="team-record-card${selected ? " is-selected" : ""}" data-project-id="${escapeAttr(project.id)}">
        <button
          class="team-record-select"
          type="button"
          data-select-project-id="${escapeAttr(project.id)}"
          aria-controls="people-agent-detail-view"
          aria-pressed="${selected ? "true" : "false"}"
        >
          <span class="team-record-main">
            <strong>${escapeHtml(project.name || project.id)}</strong>
            <span>Lead ${escapeHtml(leadLabel)} - ${taskCount} task${taskCount === 1 ? "" : "s"}</span>
          </span>
          <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
        </button>
      </li>
    `;
  }

  function renderSelectableRole(role) {
    const selected = selectedTeamRecordKind === "role" && text(selectedTeamRecordId) === text(role.id);
    const assignedCount = agentsForRole(role.id).length;
    const scope = role.project_id ? peers.agents.projectLabel(role.project_id) : "Team role";
    return `
      <li class="team-record-card${selected ? " is-selected" : ""}" data-role-id="${escapeAttr(role.id)}">
        <button
          class="team-record-select"
          type="button"
          data-select-role-id="${escapeAttr(role.id)}"
          aria-controls="people-agent-detail-view"
          aria-pressed="${selected ? "true" : "false"}"
        >
          <span class="team-record-main">
            <strong>${escapeHtml(role.name || role.id)}</strong>
            <span>${escapeHtml(scope)} - ${assignedCount} agent${assignedCount === 1 ? "" : "s"}</span>
          </span>
          <span class="people-agent-role">${escapeHtml(role.archived ? "Archived" : "Role")}</span>
        </button>
      </li>
    `;
  }

  function renderTeamRecordDetail(selectedRecord) {
    if (!selectedRecord) {
      return renderPeopleAgentDetail(null, "Select a team, agent, project, or role.");
    }
    if (selectedRecord.kind === "team") {
      return renderTeamRecordDetailContent(selectedRecord.record);
    }
    if (selectedRecord.kind === "project") {
      return renderTeamProjectDetail(selectedRecord.record);
    }
    if (selectedRecord.kind === "role") {
      return renderTeamRoleDetail(selectedRecord.record);
    }
    return renderPeopleAgentDetail(selectedRecord.record);
  }

  function renderTeamProjectDetail(project) {
    const team = peers.app.state.teams[project.team_id] || null;
    const lead = peers.app.state.agents[project.lead_agent_id] || null;
    const projectTasks = values(peers.app.state.tasks)
      .filter((task) => task.project_id === project.id)
      .sort(byPriority);
    const openTasks = projectTasks.filter((task) => !terminalTaskStates.has(task.state));
    const projectDeliverables = values(peers.app.state.deliverables).filter((deliverable) => {
      const task = peers.app.state.tasks[deliverable.task_id];
      return task?.project_id === project.id;
    });
    return `
      <div class="agent-detail team-record-detail">
        <div class="agent-detail-header">
          <div class="agent-detail-title">
            <h3>${escapeHtml(project.name || project.id)}</h3>
            <div class="meta-line">${escapeHtml(project.id)}</div>
          </div>
          <div class="agent-detail-badges">
            <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
          </div>
        </div>
        <div class="record-actions agent-detail-actions">
          ${peers.access.editButton("project", project.id)}
        </div>
        <div class="agent-detail-grid">
          ${peers.voice.renderAgentDetailField("Team", team ? `${team.name || team.id} (${team.id})` : project.team_id || "None")}
          ${peers.voice.renderAgentDetailField("Lead", lead ? `${peers.agents.agentLabel(lead)} (${lead.id})` : project.lead_agent_id || "None")}
          ${peers.voice.renderAgentDetailField("Directory scope", project.directory_path || "root")}
          ${peers.voice.renderAgentDetailField("Open tasks", `${openTasks.length} / ${projectTasks.length}`)}
          ${peers.voice.renderAgentDetailField("Deliverables", projectDeliverables.length)}
        </div>
        ${peers.costs.renderProjectCostCounter(project.id)}
        ${peers.projects.renderProjectGit(project.git)}
        ${peers.projects.renderProjectReleasePolicy(project.release_policy)}
        <section class="agent-detail-section">
          <h3>Goals</h3>
          ${peers.org.renderGoals(project.id, project.goals || [], { compact: true })}
        </section>
        <section class="agent-detail-section">
          <h3>Tasks</h3>
          ${peers.planning.renderTasks(projectTasks, { compact: true })}
        </section>
        <section class="agent-detail-section">
          <h3>Deliverables</h3>
          ${peers.reviews.renderDeliverables(projectDeliverables, { compact: true })}
        </section>
      </div>
    `;
  }

  function renderTeamRecordDetailContent(team) {
    const manager = peers.app.state.agents[team.manager_agent_id] || null;
    const teamRecords = teamRecordsForTeam(team.id, {
      teamId: text(team.id),
      agents: values(peers.app.state.agents).filter((agent) => peers.app.isVisibleAgent(agent) && text(agent.team_id) === text(team.id)),
      projects: values(peers.app.state.projects).filter((project) => peers.app.isVisibleProject(project) && text(project.team_id) === text(team.id)),
      roles: values(peers.app.state.roles).filter((role) => peers.app.isVisibleRole(role) && text(role.team_id) === text(team.id)),
    });
    const teamCost = peers.costs.teamCostForTeam(team.id);
    const renewalLabel = text(team.cost_budget_renewal_mode) === "monthly_utc"
      ? "Monthly at 00:00 UTC"
      : "Manual reset";
    const budgetWindows = Array.isArray(team.cost_budget_windows) ? team.cost_budget_windows : [];
    const budgetWindowHistory = budgetWindows.length
      ? `<ul class="record-list">${[...budgetWindows]
        .sort((left, right) => text(right.started_at).localeCompare(text(left.started_at)))
        .map((window) => `<li><strong>${escapeHtml(humanizeToken(window.kind) || "budget window")}</strong> ${escapeHtml(peers.chat.formatChatTimestamp(window.started_at))} — ${escapeHtml(text(window.reason) || "No reason recorded")} <span class="meta-line">${escapeHtml(text(window.actor) || "unknown actor")}</span></li>`)
        .join("")}</ul>`
      : `<p class="empty-state">This Team retains its legacy lifetime cap until an operator resets its budget.</p>`;
    return `
      <div class="agent-detail team-record-detail">
        <div class="agent-detail-header">
          <div class="agent-detail-title">
            <h3>${escapeHtml(team.name || team.id)}</h3>
            <div class="meta-line">${escapeHtml(team.id)}</div>
          </div>
          <div class="agent-detail-badges">
            ${team.archived ? `<span class="state-pill archived">archived</span>` : `<span class="state-pill accepted">active</span>`}
            ${peers.costs.renderTeamCostBlockedPill(team.id)}
          </div>
        </div>
        <div class="record-actions agent-detail-actions">
          ${peers.access.editButton("team", team.id)}
          ${team.archived ? "" : `<button type="button" class="record-action" data-team-copy-open="${escapeAttr(team.id)}">Copy team</button>`}
          ${team.max_cost_usd !== null && team.max_cost_usd !== undefined ? `<button type="button" class="record-action" data-team-cost-budget-reset="${escapeAttr(team.id)}">Reset budget</button>` : ""}
        </div>
        <div class="agent-detail-grid">
          ${peers.voice.renderAgentDetailField("Manager", manager ? `${peers.agents.agentLabel(manager)} (${manager.id})` : text(team.manager_agent_id) || "None")}
          ${peers.voice.renderAgentDetailField("Agents", teamRecords.agents.length)}
          ${peers.voice.renderAgentDetailField("Projects", teamRecords.projects.length)}
          ${peers.voice.renderAgentDetailField("Roles", teamRecords.roles.length)}
          ${peers.voice.renderAgentDetailField("Escalate to governance", Boolean(team.escalate_to_governance) ? "Enabled" : "Disabled")}
          ${peers.voice.renderAgentDetailField("Operator communication target", text(team.operator_communication_target_id) || "None")}
          ${peers.voice.renderAgentDetailField("Estimated max cost USD", text(team.max_cost_usd) || "None")}
          ${peers.voice.renderAgentDetailField("Governance cost warning", `${Math.round((numberOrNull(team.cost_warning_ratio) ?? 0.8) * 100)}% of cap`)}
          ${peers.voice.renderAgentDetailField("Budget renewal", renewalLabel)}
          ${peers.voice.renderAgentDetailField("Budget window start", teamCost?.budget_window_started_at ? peers.chat.formatChatTimestamp(teamCost.budget_window_started_at) : "Lifetime legacy cap")}
        </div>
        <section class="agent-detail-section">
          <h3>Team cost</h3>
          ${peers.costs.renderTeamCostCounter(team.id) || "<p class=\"empty-state\">No cost estimate available.</p>"}
          ${peers.costs.renderCostBackfillControl()}
        </section>
        <section class="agent-detail-section">
          <h3>Budget history</h3>
          ${budgetWindowHistory}
        </section>
      </div>
    `;
  }

  function renderTeamRoleDetail(role) {
    const team = peers.app.state.teams[role.team_id] || null;
    const project = role.project_id ? peers.app.state.projects[role.project_id] || null : null;
    const assignedAgents = agentsForRole(role.id);
    const permissions = splitList(role.permissions);
    return `
      <div class="agent-detail team-record-detail">
        <div class="agent-detail-header">
          <div class="agent-detail-title">
            <h3>${escapeHtml(role.name || role.id)}</h3>
            <div class="meta-line">${escapeHtml(role.id)}</div>
          </div>
          <div class="agent-detail-badges">
            <span class="state-pill ${role.archived ? "blocked" : "accepted"}">${role.archived ? "archived" : "active"}</span>
          </div>
        </div>
        <div class="record-actions agent-detail-actions">
          ${peers.access.editButton("role", role.id)}
        </div>
        <div class="agent-detail-grid">
          ${peers.voice.renderAgentDetailField("Team", team ? `${team.name || team.id} (${team.id})` : role.team_id || "None")}
          ${peers.voice.renderAgentDetailField("Project", project ? `${project.name || project.id} (${project.id})` : role.project_id || "None")}
          ${peers.voice.renderAgentDetailField("Assigned agents", assignedAgents.length)}
          ${peers.voice.renderAgentDetailField("Max ephemeral", role.ephemeral_policy?.max_concurrent ?? 0)}
        </div>
        ${
          role.description
            ? `<section class="agent-detail-section"><h3>Description</h3><p class="meta-line role-description">${escapeHtml(role.description)}</p></section>`
            : ""
        }
        <section class="agent-detail-section">
          <h3>Permissions</h3>
          ${
            permissions.length
              ? `<ul class="inline-list agent-detail-list">${permissions.map((permission) => `<li>${peers.tooltips.renderRolePermissionToken(permission)}</li>`).join("")}</ul>`
              : `<p class="empty-state">No special permissions.</p>`
          }
        </section>
        <section class="agent-detail-section">
          <h3>Agents</h3>
          ${
            assignedAgents.length
              ? `<ul class="inline-list agent-detail-list">${assignedAgents
                  .map((agent) => `<li>${escapeHtml(peers.agents.agentLabel(agent))} (${escapeHtml(agent.id)})</li>`)
                  .join("")}</ul>`
              : `<p class="empty-state">No agents assigned.</p>`
          }
        </section>
      </div>
    `;
  }

  function agentsForRole(roleId) {
    const normalizedRoleId = text(roleId);
    return values(peers.app.state.agents)
      .filter((agent) => text(agent.role_id) === normalizedRoleId && peers.app.isVisibleAgent(agent))
      .sort(comparePeopleAgents);
  }

  function renderPeopleAgentDetail(agent, emptyMessage = "No agent selected.") {
    if (!agent) {
      return `
        <div class="agent-detail-empty">
          <p class="empty-state">${escapeHtml(emptyMessage)}</p>
        </div>
      `;
    }

    const details = peers.agents.agentCardData(agent);
    const snapshot = peers.agents.agentWorkSnapshot(details);
    const waitingRows = snapshot.waiting.length
      ? snapshot.waiting.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
      : `<li>Clear</li>`;
    const runtimeRows = peers.agents.agentRuntimeDetailRows(details);
    const projectValue = details.projectIds.length ? details.projectLabel || details.projectIds.join(", ") : "None";

    return `
      <div class="agent-detail">
        <div class="agent-detail-header">
          ${peers.agents.renderAgentAvatar(details, "agent-avatar-large")}
          <div class="agent-detail-title">
            <h3>${escapeHtml(details.label)}</h3>
            <div class="meta-line">${escapeHtml(details.role)} - ${escapeHtml(details.id)}</div>
          </div>
          <div class="agent-detail-badges">
            ${peers.agents.renderAgentRosterStatusPill(details)}
            ${peers.agents.renderAgentSessionPill(details)}
            ${peers.agents.renderAgentAwakePill(details)}
            ${peers.agents.renderAgentInboxHeaderIndicators(details)}
          </div>
        </div>
        <div class="record-actions agent-detail-actions">
          ${peers.agents.renderAgentStandDownControls(details.id, details)}
          ${peers.voice.renderOpenAgentChatButton(details.id)}
          ${peers.voice.renderOpenAgentSessionButton(details.sessionId)}
          ${["every_wake", "work_lineage"].includes(details.runtime.contextClearingPolicy) ? "" : `<button class="record-action" type="button" data-context-rotation-agent-id="${escapeAttr(details.id)}">Clear context</button>`}
          ${peers.access.editButton("agent", details.id)}
        </div>
        ${peers.voice.renderAgentVoiceCallPanel(details)}
        <div class="agent-detail-grid">
          ${peers.voice.renderAgentDetailField("Session", details.sessionId || "None")}
          ${peers.voice.renderAgentDetailField("Reports to", details.reportsToLabel)}
          ${peers.voice.renderAgentDetailField("Project", projectValue)}
          ${peers.voice.renderAgentDetailField("Subordinates", details.directSubordinateCount)}
          ${peers.voice.renderAgentDetailField("Open tasks", `${details.taskStats.openCount} / ${details.taskStats.totalCount}`)}
          ${peers.voice.renderAgentDetailField("Ephemeral", `${details.ephemeralCurrentCount} / ${details.ephemeralCapacity}`)}
        </div>
        <section class="agent-detail-section">
          <h3>Work</h3>
          <div class="agent-detail-work-line">
            <span>Now</span>
            <strong>${escapeHtml(snapshot.primary)}</strong>
          </div>
          <div class="meta-line">Waiting</div>
          <ul class="inline-list agent-detail-list">
            ${waitingRows}
          </ul>
          ${peers.agents.renderAgentOpenTasks(details.taskStats.openTasks)}
        </section>
        <section class="agent-detail-section">
          <h3>Runtime</h3>
          <ul class="inline-list agent-detail-list">
            ${runtimeRows.map((row) => `<li>${escapeHtml(row)}</li>`).join("")}
          </ul>
        </section>
        <section class="agent-detail-section">
          <h3>Inbox</h3>
          ${peers.agents.renderAgentInbox(details.id) || `<p class="empty-state">No pending inbox.</p>`}
        </section>
      </div>
    `;
  }
  function handleTeamCreationClick(event) {
    const openCreateTeamButton = event.target.closest("[data-teams-create-team-open]");
    if (openCreateTeamButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("create teams")) {
        return true;
      }
      openTeamsCreateTeamPanel();
      return true;
    }

    const closeCreateTeamButton = event.target.closest("[data-teams-create-team-close]");
    if (closeCreateTeamButton) {
      event.preventDefault();
      closeTeamsCreateTeamPanel();
      return true;
    }
    return false;
  }
  function handlePeopleNavigationClick(event) {
    const teamGroupToggle = event.target.closest("[data-team-record-toggle]");
    if (teamGroupToggle) {
      event.preventDefault();
      const teamRecordId = text(teamGroupToggle.dataset.teamRecordToggle);
      if (teamRecordId === noTeamRecordId) {
        toggleTeamRecordGroup(teamRecordId);
        clearSelectedTeamRecord();
        renderAgents();
        return true;
      }
      selectTeamRecord("team", teamRecordId);
      toggleTeamRecordGroup(teamRecordId);
      return true;
    }

    const teamRecordTab = event.target.closest("[data-team-record-tab]");
    if (teamRecordTab) {
      event.preventDefault();
      setTeamRecordGroupTab(teamRecordTab.dataset.teamRecordGroupId, teamRecordTab.dataset.teamRecordTab);
      return true;
    }

    const teamRecordPage = event.target.closest("[data-team-record-page]");
    if (teamRecordPage) {
      event.preventDefault();
      setTeamRecordGroupPage(
        teamRecordPage.dataset.teamRecordGroupId,
        teamRecordPage.dataset.teamRecordKind,
        Number(teamRecordPage.dataset.teamRecordPage),
      );
      return true;
    }

    const inlineCreateToggle = event.target.closest("[data-team-inline-create-toggle]");
    if (inlineCreateToggle) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("add Team records")) {
        return true;
      }
      toggleTeamInlineCreateForm(
        inlineCreateToggle.dataset.teamRecordGroupId,
        inlineCreateToggle.dataset.teamRecordKind,
      );
      return true;
    }

    const inlineCreateClose = event.target.closest("[data-team-inline-create-close]");
    if (inlineCreateClose) {
      event.preventDefault();
      closeTeamInlineCreateForm(
        inlineCreateClose.dataset.teamRecordGroupId,
        inlineCreateClose.dataset.teamRecordKind,
      );
      return true;
    }

    const selectAgentButton = event.target.closest("[data-select-agent-id]");
    if (selectAgentButton) {
      event.preventDefault();
      selectPeopleAgent(selectAgentButton.dataset.selectAgentId);
      return true;
    }
    return false;
  }
  function handleProjectRoleSelectionClick(event) {
    const selectProjectButton = event.target.closest("[data-select-project-id]");
    if (selectProjectButton) {
      event.preventDefault();
      selectTeamRecord("project", selectProjectButton.dataset.selectProjectId);
      return true;
    }

    const selectRoleButton = event.target.closest("[data-select-role-id]");
    if (selectRoleButton) {
      event.preventDefault();
      selectTeamRecord("role", selectRoleButton.dataset.selectRoleId);
      return true;
    }
    return false;
  }

  function expandTeamRecordGroup(teamId) {
    expandedTeamRecordGroupIds.add(teamId);
  }

  return {
    expandTeamRecordGroup,
    clearSelectedTeamRecord,
    closeTeamInlineCreateFormFor,
    closeTeamRecordInspector,
    closeTeamsCreateTeamPanel,
    compareGovernanceGroups,
    comparePeopleAgents,
    comparePeopleTeams,
    compareReviewProfiles,
    compareTeamProjects,
    compareTeamRoles,
    handleTeamCreationClick,
    handlePeopleNavigationClick,
    handleProjectRoleSelectionClick,
    renderAgents,
    selectTeamRecord,
    syncTeamsCreateTeamPanel,
    teamAgentOptions,
  };
}
