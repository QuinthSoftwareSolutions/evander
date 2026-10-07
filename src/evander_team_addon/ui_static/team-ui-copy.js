// Team configuration and session copying, including partial failure and conflict recovery.
import {
  byId,
  escapeAttr,
  escapeHtml,
  renderSelectOptions,
  responseText,
  text,
  values,
} from "./team-ui-format.js";

export function createTeamCopyController(peers) {

  const pendingTeamCopySessionAgents = new Map();

  function teamCopyConfiguration(teamId) {
    const normalizedTeamId = text(teamId);
    const projects = values(peers.app.state.projects)
      .filter((project) =>
        text(project.team_id) === normalizedTeamId
        && ["active", "blocked"].includes(text(project.status)),
      )
      .sort(peers.people.compareTeamProjects);
    const projectIds = new Set(projects.map((project) => text(project.id)));
    const roles = values(peers.app.state.roles)
      .filter((role) =>
        text(role.team_id) === normalizedTeamId
        && !role.archived
        && (!text(role.project_id) || projectIds.has(text(role.project_id))),
      )
      .sort(peers.people.compareTeamRoles);
    const roleIds = new Set(roles.map((role) => text(role.id)));
    const agents = values(peers.app.state.agents)
      .filter((agent) =>
        text(agent.team_id) === normalizedTeamId
        && peers.agents.agentStatus(agent) !== "archived"
        && roleIds.has(text(agent.role_id)),
      )
      .sort(peers.people.comparePeopleAgents);
    const reviewProfiles = values(peers.app.state.review_profiles).filter((profile) =>
      text(profile.team_id) === normalizedTeamId
      && !profile.archived
      && (!text(profile.project_id) || projectIds.has(text(profile.project_id))),
    );
    const outcomeKinds = values(peers.app.state.outcome_kind_definitions).filter((definition) =>
      text(definition.team_id) === normalizedTeamId
      && !definition.archived
      && (!text(definition.project_id) || projectIds.has(text(definition.project_id))),
    );
    return { projects, projectIds, agents, roles, reviewProfiles, outcomeKinds };
  }

  function teamCopyAgentDirectoryPath(agent, configuration) {
    const assignedProject = peers.agents.agentProjectIds(agent)
      .map((projectId) => peers.app.state.projects[projectId])
      .filter((project) => project && configuration.projectIds.has(text(project.id)))
      .sort(peers.people.compareTeamProjects)[0];
    return text(assignedProject?.directory_path) || "root";
  }

  function renderTeamCopySessionOptions(selectedValue = "") {
    const registeredSessions = peers.sessions.registeredTeamSessionIds();
    const selected = text(selectedValue);
    const options = peers.sessions.coreAgentSessions
      .filter((session) => session.session_id === selected || !registeredSessions.has(session.session_id))
      .map((session) => [
        session.session_id,
        `${session.target_name} (${session.session_id})${session.backend ? ` - ${peers.sessions.backendLabelForCoreAgentSession(session.backend)}` : ""}`,
      ]);
    if (selected && !options.some(([sessionId]) => sessionId === selected)) {
      options.unshift([selected, `${selected} (new session)`]);
    }
    return renderSelectOptions(options, {
      selectedValue: selected,
      placeholder: "Copy source session configuration (new ID)",
    });
  }

  function renderTeamCopyDialogBody(
    team,
    configuration,
    sourceRevision,
    sourceConfigurationToken,
  ) {
    const excludedProjects = values(peers.app.state.projects).filter((project) =>
      text(project.team_id) === text(team.id)
      && !["active", "blocked"].includes(text(project.status)),
    ).length;
    const includedAgentIds = new Set(configuration.agents.map((agent) => text(agent.id)));
    const excludedAgents = values(peers.app.state.agents).filter((agent) =>
      text(agent.team_id) === text(team.id) && !includedAgentIds.has(text(agent.id)),
    ).length;
    const excludedDefinitions = [
      ...values(peers.app.state.roles).filter((role) => text(role.team_id) === text(team.id) && role.archived),
      ...values(peers.app.state.review_profiles).filter((profile) => text(profile.team_id) === text(team.id) && profile.archived),
      ...values(peers.app.state.outcome_kind_definitions).filter((definition) => text(definition.team_id) === text(team.id) && definition.archived),
    ].length;
    const excludedCount = excludedProjects + excludedAgents + excludedDefinitions;
    return `
      <form
        id="team-copy-form"
        class="org-action-form team-copy-form"
        data-team-copy-form
        data-form-id="team-copy-form"
        data-source-team-id="${escapeAttr(team.id)}"
        data-source-revision="${escapeAttr(sourceRevision)}"
        data-source-configuration-token="${escapeAttr(sourceConfigurationToken)}"
      >
        <label>New team name <input name="name" required value="${escapeAttr(`${team.name || team.id} Copy`)}" /></label>
        <div class="team-copy-summary" role="note">
          <strong>Clean configuration copy</strong>
          <span>${configuration.agents.length} agents, ${configuration.roles.length} roles, ${configuration.projects.length} projects, ${configuration.reviewProfiles.length} review profiles, and ${configuration.outcomeKinds.length} outcome kinds.</span>
          <span>Tasks, cases, conversations, memories, deliverables, reviews, releases, dispatches, cost history, and governance membership will not be copied.</span>
          ${excludedCount ? `<span>${excludedCount} completed or archived configuration record${excludedCount === 1 ? "" : "s"} will be excluded.</span>` : ""}
        </div>
        <div class="team-copy-session-header">
          <div>
            <strong>Agent sessions</strong>
            <p>New sessions automatically inherit each source session's model, capabilities, approved tools, access grants, and limits. You can also select an existing session.</p>
          </div>
          <button type="button" class="record-action" data-team-copy-sessions-refresh>Refresh sessions</button>
        </div>
        <div class="team-copy-session-list">
          ${configuration.agents.map((agent) => {
            const directoryPath = teamCopyAgentDirectoryPath(agent, configuration);
            return `
              <div class="team-copy-session-row" data-team-copy-agent-row="${escapeAttr(agent.id)}">
                <div class="team-copy-agent-label">
                  <strong>${escapeHtml(peers.agents.agentLabel(agent))}</strong>
                  <span>${escapeHtml(peers.agents.agentRoleLabel(agent))} · ${escapeHtml(agent.id)}</span>
                </div>
                <label>
                  Destination session
                  <select data-team-copy-session-select data-source-agent-id="${escapeAttr(agent.id)}" data-source-session-id="${escapeAttr(agent.session_id)}">
                    ${renderTeamCopySessionOptions()}
                  </select>
                </label>
                <button
                  type="button"
                  class="record-action"
                  data-team-copy-session-create="${escapeAttr(agent.id)}"
                  data-directory-path="${escapeAttr(directoryPath)}"
                >Configure manually</button>
              </div>
            `;
          }).join("")}
        </div>
        <p data-team-copy-progress role="status" aria-live="polite"></p>
        <div class="org-action-form-actions">
          <button type="submit">Copy team</button>
          <button type="button" class="secondary" data-org-action-close>Cancel</button>
        </div>
      </form>
    `;
  }

  async function openTeamCopyDialog(teamId) {
    let team = peers.app.state.teams?.[text(teamId)];
    if (!team) {
      peers.app.setStatus("Team is no longer available.", "error");
      return;
    }
    let configuration = teamCopyConfiguration(team.id);
    await peers.sessions.loadTeamCopyCoreAgentSessions(configuration);
    team = peers.app.state.teams?.[text(teamId)];
    if (!team || team.archived) {
      peers.app.setStatus("Team is no longer available for copying.", "error");
      return;
    }
    configuration = teamCopyConfiguration(team.id);
    const sourceRevision = peers.app.state.revision;
    const sourceConfigurationToken = text(team.copy_configuration_token);
    if (!sourceConfigurationToken) {
      peers.app.setStatus("The Team copy preview is unavailable. Reload the page and try again.", "error");
      return;
    }
    peers.org.showOrgActionDialog({
      title: `Copy ${team.name || team.id}`,
      bodyHtml: renderTeamCopyDialogBody(
        team,
        configuration,
        sourceRevision,
        sourceConfigurationToken,
      ),
    });
    syncTeamCopySessionSelections(document.querySelector("#team-copy-form"));
  }

  function syncTeamCopySessionSelections(form) {
    if (!form?.hasAttribute?.("data-team-copy-form")) {
      return;
    }
    const selects = Array.from(form.querySelectorAll("[data-team-copy-session-select]"));
    const selectedValues = selects.map((select) => text(select.value)).filter(Boolean);
    selects.forEach((select) => {
      const current = text(select.value);
      Array.from(select.options).forEach((option) => {
        option.disabled = Boolean(
          option.value
          && option.value !== current
          && selectedValues.includes(option.value),
        );
      });
      select.setCustomValidity("");
    });
  }

  function refreshTeamCopySessionSelects(form) {
    const selections = new Map(
      Array.from(form?.querySelectorAll?.("[data-team-copy-session-select]") || []).map(
        (select) => [text(select.dataset.sourceAgentId), text(select.value)],
      ),
    );
    form?.querySelectorAll?.("[data-team-copy-session-select]").forEach((select) => {
      const selected = selections.get(text(select.dataset.sourceAgentId)) || "";
      select.innerHTML = renderTeamCopySessionOptions(selected);
      select.value = selected;
    });
    syncTeamCopySessionSelections(form);
  }

  function openTeamCopyAgentSessionDialog(button) {
    const form = button?.closest?.("form[data-team-copy-form]");
    const sourceAgentId = text(button?.dataset?.teamCopySessionCreate);
    if (!form || !sourceAgentId) {
      peers.app.setStatus("Team copy session row is unavailable.", "error");
      return;
    }
    const contextId = `team-copy-${sourceAgentId}-${Date.now()}`;
    pendingTeamCopySessionAgents.set(contextId, {
      formId: form.id,
      sourceAgentId,
    });
    const detail = {
      contextId,
      directoryPath: text(button.dataset.directoryPath) || "root",
    };
    if (typeof window.openTeamAgentCreateDialog === "function") {
      window.openTeamAgentCreateDialog(detail);
      return;
    }
    window.dispatchEvent(new CustomEvent("team-agent-create:open", { detail }));
  }

  async function copyTeamAgentSessions(form, selects) {
    for (const select of selects) {
      if (text(select.value)) continue;
      if (!form.isConnected) throw new Error("Team copy was closed. Prepared sessions remain available in the session list.");
      const sourceSessionId = text(select.dataset.sourceSessionId);
      const agentName = text(select.closest("[data-team-copy-agent-row]")?.querySelector("strong")?.textContent)
        || select.dataset.sourceAgentId;
      const progressMessage = `Copying session configuration for ${agentName}.`;
      form.querySelector("[data-team-copy-progress]").textContent = progressMessage;
      peers.app.setStatus(progressMessage, "loading");
      const operationUrl = select.dataset.copyOperationId
        ? `/api/admin/agents/create-operations/${encodeURIComponent(select.dataset.copyOperationId)}`
        : "/api/admin/agents/copy-operations";
      let response = await peers.access.teamFetch(operationUrl, select.dataset.copyOperationId ? {} : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_session_id: sourceSessionId }),
      });
      if (!response.ok) throw new Error(await responseText(response));
      let { operation } = await response.json();
      select.dataset.copyOperationId = operation.operation_id;
      const deadline = Date.now() + 10 * 60 * 1000;
      while (["queued", "running"].includes(operation.status)) {
        if (Date.now() >= deadline) throw new Error("Session creation is still running. Retry to check its progress.");
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        response = await peers.access.teamFetch(`/api/admin/agents/create-operations/${encodeURIComponent(operation.operation_id)}`);
        if (!response.ok) throw new Error(await responseText(response));
        ({ operation } = await response.json());
      }
      if (operation.status !== "succeeded") {
        delete select.dataset.copyOperationId;
        throw new Error(operation.error || `Could not copy session ${sourceSessionId}.`);
      }
      const sessionId = text(operation.session_id || operation.result?.session_id);
      if (!sessionId) throw new Error("Session copy did not return a session ID.");
      peers.sessions.coreAgentSessions.push(peers.sessions.normalizeCoreAgentSession({
        session_id: sessionId,
        target_name: operation.target_name,
        backend: operation.backend,
        directory_path: operation.directory_path,
      }));
      select.innerHTML = renderTeamCopySessionOptions(sessionId);
      select.value = sessionId;
      refreshTeamCopySessionSelects(form);
    }
  }

  async function submitTeamCopyForm(form) {
    if (!peers.access.guardTeamMutation("copy teams")) {
      return false;
    }
    if (form.dataset.copyingSessions === "true") return false;
    const sourceTeamId = text(form.dataset.sourceTeamId);
    const sessionSelects = Array.from(form.querySelectorAll("[data-team-copy-session-select]"));
    if (sessionSelects.some((select) => !text(select.value))) {
      const currentConfigurationToken = text(
        peers.app.state.teams?.[sourceTeamId]?.copy_configuration_token,
      );
      if (
        currentConfigurationToken
        && currentConfigurationToken !== text(form.dataset.sourceConfigurationToken)
      ) {
        peers.app.setStatus("The source Team configuration changed after this copy preview was opened. Close and reopen the copy wizard to review the current configuration.", "warning");
        return false;
      }
      form.dataset.copyingSessions = "true";
      peers.drafts.setFormBusy(form, true);
      try {
        await copyTeamAgentSessions(form, sessionSelects);
        if (!form.isConnected) return false;
        form.querySelector("[data-team-copy-progress]").textContent = "Sessions copied. Saving team.";
      } catch (error) {
        const message = `${error.message} Sessions already copied are kept selected; retry to continue.`;
        form.querySelector("[data-team-copy-progress]").textContent = message;
        peers.app.setStatus(message, "error");
        return false;
      } finally {
        delete form.dataset.copyingSessions;
        peers.drafts.setFormBusy(form, false);
      }
    }
    const agentSessionIds = Object.fromEntries(
      sessionSelects.map((select) => [text(select.dataset.sourceAgentId), text(select.value)]),
    );
    const sessionIds = Object.values(agentSessionIds).filter(Boolean);
    if (sessionIds.length !== sessionSelects.length) {
      peers.app.setStatus("Assign a destination session to every copied agent.", "error");
      sessionSelects.find((select) => !text(select.value))?.focus();
      return false;
    }
    if (new Set(sessionIds).size !== sessionIds.length) {
      peers.app.setStatus("Each copied agent must use a different destination session.", "error");
      return false;
    }
    const existingTeamIds = new Set(Object.keys(peers.app.state.teams || {}));
    const result = await peers.mutations.submitMutation(
      form,
      `/teams/${encodeURIComponent(sourceTeamId)}/copy`,
      "POST",
      {
        name: text(form.elements.name?.value),
        agent_session_ids: agentSessionIds,
        expected_revision: Number(form.dataset.sourceRevision),
        source_configuration_token: text(form.dataset.sourceConfigurationToken),
      },
      { resetOnSuccess: false, detailedResult: true },
    );
    if (!result.saved) {
      if (result.failureReason === "conflict") {
        peers.mutations.clearConflictRetry();
        peers.app.setStatus(
          "The source Team configuration changed after this copy preview was opened. Close and reopen the copy wizard to review the current configuration.",
          "warning",
        );
      }
      return false;
    }
    const copiedTeam = values(peers.app.state.teams)
      .filter((team) => !existingTeamIds.has(text(team.id)))
      .sort((left, right) => text(right.created_at).localeCompare(text(left.created_at)) || byId(left, right))[0];
    peers.org.closeOrgActionDialog();
    if (copiedTeam) {
      peers.people.expandTeamRecordGroup(copiedTeam.id);
      peers.people.selectTeamRecord("team", copiedTeam.id);
    }
    return true;
  }
  function bindTeamCopySubmission() {

    document.addEventListener("submit", (event) => {
      const form = event.target?.closest?.("form[data-team-copy-form]");
      if (!form) {
        return;
      }
      event.preventDefault();
      void submitTeamCopyForm(form);
    });
  }
  function handleCopyTeamClick(event) {
    const copyTeamButton = event.target.closest("[data-team-copy-open]");
    if (copyTeamButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("copy teams")) {
        return true;
      }
      void openTeamCopyDialog(copyTeamButton.dataset.teamCopyOpen);
      return true;
    }
    return false;
  }

  function takePendingSessionAgent(contextId) {
    const copyTarget = pendingTeamCopySessionAgents.get(contextId);
    if (copyTarget) {
      pendingTeamCopySessionAgents.delete(contextId);
    }
    return copyTarget;
  }

  return {
    takePendingSessionAgent,
    bindTeamCopySubmission,
    handleCopyTeamClick,
    openTeamCopyAgentSessionDialog,
    refreshTeamCopySessionSelects,
    renderTeamCopySessionOptions,
    submitTeamCopyForm,
    syncTeamCopySessionSelections,
    teamCopyAgentDirectoryPath,
    teamCopyConfiguration,
  };
}
