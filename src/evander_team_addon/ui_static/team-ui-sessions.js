// Core session registration and Team agent session pickers.
import {
  fieldText,
  formData,
  formIdentifier,
  formSemanticIdentifier,
  renderSelectOptions,
  responseText,
  setFieldValue,
  text,
  values,
} from "./team-ui-format.js";
import { noTeamRecordId } from "./team-ui-config.js";

export function createTeamSessionsController(peers) {

  let coreAgentSessions = [];

  let coreAgentSessionsDirectoryPath = "";

  let coreAgentSessionsLoading = null;

  const pendingTeamAgentCreateForms = new Map();

  function bindExistingAgentPickers() {
    document.addEventListener("change", (event) => {
      const select = event.target?.closest?.("[data-agent-source-picker]");
      if (!select) {
        return;
      }
      applyExistingAgentDefaults(select);
    });
  }

  function bindTeamAgentSessionControls() {
    document.addEventListener("change", (event) => {
      const copySelect = event.target?.closest?.("[data-team-copy-session-select]");
      if (copySelect) {
        peers.copy.syncTeamCopySessionSelections(copySelect.form);
        return;
      }
      const select = event.target?.closest?.("[data-core-agent-session-picker]");
      if (!select) {
        return;
      }
      applyCoreAgentSessionSelection(select);
    });

    document.addEventListener("click", (event) => {
      const copyRefreshButton = event.target?.closest?.("[data-team-copy-sessions-refresh]");
      if (copyRefreshButton) {
        event.preventDefault();
        const form = copyRefreshButton.closest("form[data-team-copy-form]");
        const configuration = peers.copy.teamCopyConfiguration(text(form?.dataset?.sourceTeamId));
        void loadTeamCopyCoreAgentSessions(configuration)
          .then(() => peers.copy.refreshTeamCopySessionSelects(form));
        return;
      }

      const copyCreateButton = event.target?.closest?.("[data-team-copy-session-create]");
      if (copyCreateButton) {
        event.preventDefault();
        if (!peers.access.teamPrivilegedUnlocked()) {
          peers.app.setStatus("Unlock privileged operations to create a core agent session.", "warning");
          return;
        }
        peers.copy.openTeamCopyAgentSessionDialog(copyCreateButton);
        return;
      }

      const refreshButton = event.target?.closest?.("[data-core-agent-session-refresh]");
      if (refreshButton) {
        event.preventDefault();
        const form = refreshButton.closest("form");
        void refreshCoreAgentSessionsForForm(form, { force: true });
        return;
      }

      const createButton = event.target?.closest?.("[data-core-agent-session-create]");
      if (!createButton) {
        return;
      }
      event.preventDefault();
      if (!peers.access.teamPrivilegedUnlocked()) {
        peers.app.setStatus("Unlock privileged operations to create a core agent session.", "warning");
        return;
      }
      const form = createButton.closest("form");
      if (!form) {
        peers.app.setStatus("Agent registration form is unavailable.", "error");
        return;
      }
      openCoreAgentCreateDialogForForm(form);
    });

    window.addEventListener("team-agent-create:created", (event) => {
      void completeTeamAgentCreateRegistration(event.detail || {});
    });
  }

  function applyExistingAgentDefaults(select) {
    const sourceAgent = peers.app.state.agents[text(select.value)];
    const form = select.form;
    if (!sourceAgent || !form) {
      return;
    }

    setFieldValue(form, select.dataset.sessionField, sourceAgent.session_id);
    setFieldValue(form, select.dataset.displayField, sourceAgent.display_name || peers.agents.agentLabel(sourceAgent));
    setFieldValue(form, select.dataset.roleField, sourceAgent.role_id);
    setFieldValue(form, select.dataset.projectField, peers.agents.agentProjectIds(sourceAgent)[0] || "");
    setFieldValue(form, select.dataset.reportsToField, sourceAgent.reports_to_agent_id || "");
    if (select.dataset.maxConcurrentField) {
      setFieldValue(form, select.dataset.maxConcurrentField, String(peers.agents.ephemeralCapacity(sourceAgent)));
    }

  }

  function applyCoreAgentSessionSelection(select) {
    const form = select.form;
    if (!form) {
      return;
    }
    const sessionId = text(select.value);
    setFieldValue(form, select.dataset.sessionField || "session_id", sessionId);
    if (!sessionId) {
      return;
    }
    const session = coreAgentSessions.find((item) => item.session_id === sessionId);
    if (session && !fieldText(form, "display_name")) {
      setFieldValue(form, "display_name", session.display_name || session.target_name || session.session_id);
    }
  }

  function validateTeamAgentSessionCreateRegistration(form) {
    if (formSemanticIdentifier(form) !== "register-agent-form") {
      return true;
    }
    const data = formData(form);
    const teamId = normalizedAgentRegistrationTeamId(data.team_id);
    if (!teamId) {
      return true;
    }
    if (!peers.app.state.teams?.[teamId]) {
      return rejectCoreAgentSessionCreate(form, "team_id", "Select a valid team before creating a core agent session.");
    }
    const roleId = text(data.role_id);
    if (!roleId) {
      return rejectCoreAgentSessionCreate(form, "role_id", "Select a role before creating a core agent session.");
    }
    const role = peers.app.state.roles?.[roleId];
    if (!role || text(role.team_id) !== teamId) {
      return rejectCoreAgentSessionCreate(
        form,
        "role_id",
        "Select a valid role for this team before creating a core agent session.",
      );
    }
    const projectId = text(data.project_id);
    if (projectId) {
      const project = peers.app.state.projects?.[projectId];
      if (!project || text(project.team_id) !== teamId) {
        return rejectCoreAgentSessionCreate(
          form,
          "project_id",
          "Select a valid project for this team before creating a core agent session.",
        );
      }
    }
    const roleProjectId = text(role.project_id);
    if (roleProjectId && roleProjectId !== projectId) {
      return rejectCoreAgentSessionCreate(
        form,
        "project_id",
        "Select the project required by the selected role before creating a core agent session.",
      );
    }
    const reportsToAgentId = text(data.reports_to_agent_id);
    if (reportsToAgentId) {
      const superior = peers.app.state.agents?.[reportsToAgentId];
      if (!superior || text(superior.team_id) !== teamId) {
        return rejectCoreAgentSessionCreate(
          form,
          "reports_to_agent_id",
          "Select a valid superior agent for this team before creating a core agent session.",
        );
      }
    }
    return true;
  }

  function normalizedAgentRegistrationTeamId(teamId) {
    const normalizedTeamId = text(teamId);
    return normalizedTeamId === noTeamRecordId ? "" : normalizedTeamId;
  }

  function rejectCoreAgentSessionCreate(form, fieldName, message) {
    peers.app.setStatus(message, "error");
    const field = form.elements?.[fieldName];
    if (field) {
      field.focus?.();
    }
    return false;
  }

  function openCoreAgentCreateDialogForForm(form) {
    if (!validateTeamAgentSessionCreateRegistration(form)) {
      return;
    }
    const contextId = formIdentifier(form) || `register-agent-${Date.now()}`;
    if (!form.id) {
      form.id = contextId;
    }
    pendingTeamAgentCreateForms.set(contextId, form.id);
    const detail = {
      contextId,
      directoryPath: teamAgentDirectoryPath(form),
    };
    if (typeof window.openTeamAgentCreateDialog === "function") {
      window.openTeamAgentCreateDialog(detail);
      return;
    }
    window.dispatchEvent(new CustomEvent("team-agent-create:open", { detail }));
  }

  async function completeTeamAgentCreateRegistration(detail) {
    const contextId = text(detail.contextId);
    const sessionId = text(detail.sessionId);
    if (!sessionId) {
      peers.app.setStatus("Created agent session did not return a session ID.", "error");
      return;
    }
    const copyTarget = peers.copy.takePendingSessionAgent(contextId);
    if (copyTarget) {
      const copyForm = document.getElementById(copyTarget.formId);
      const select = Array.from(
        copyForm?.querySelectorAll?.("[data-team-copy-session-select]") || [],
      ).find((candidate) => text(candidate.dataset.sourceAgentId) === copyTarget.sourceAgentId);
      if (!copyForm || !select) {
        peers.app.setStatus(`Created session ${sessionId}, but the Team copy form is no longer open.`, "warning");
        return;
      }
      if (!coreAgentSessions.some((session) => session.session_id === sessionId)) {
        coreAgentSessions.push(normalizeCoreAgentSession({
          session_id: sessionId,
          target_name: detail.targetName || sessionId,
          backend: detail.operation?.backend,
          directory_path: detail.operation?.directory_path,
        }));
        coreAgentSessions = sortCoreAgentSessions(coreAgentSessions);
      }
      select.innerHTML = peers.copy.renderTeamCopySessionOptions(sessionId);
      select.value = sessionId;
      peers.copy.refreshTeamCopySessionSelects(copyForm);
      peers.app.setStatus(`Created session ${sessionId}; finish mapping the copied team.`, "ok");
      return;
    }
    const formId = pendingTeamAgentCreateForms.get(contextId);
    const form = formId ? document.getElementById(formId) : null;
    pendingTeamAgentCreateForms.delete(contextId);
    if (!form) {
      peers.app.setStatus(`Created session ${sessionId}, but the Team registration form is no longer open.`, "warning");
      return;
    }
    setFieldValue(form, "session_id", sessionId);
    const existingSessionSelect = form.elements?.existing_session_id;
    if (existingSessionSelect) {
      existingSessionSelect.dataset.currentValue = sessionId;
    }
    await loadCoreAgentSessions({
      directoryPath: teamAgentDirectoryPath(form),
      force: true,
      rerender: false,
    });
    refreshCoreAgentSessionPicker(form, sessionId);
    peers.app.setStatus(`Created session ${sessionId}; registering Team agent.`, "loading");
    const result = await peers.forms.submitCreateForm(form, { detailedResult: true });
    if (!result.saved && result.failureReason !== "conflict") {
      peers.app.setStatus(`Created session ${sessionId}; review the Team registration error and submit again.`, "warning");
    }
  }

  function teamAgentDirectoryPath(form = null) {
    const fromForm = text(form?.dataset?.directoryPath);
    if (fromForm) {
      return fromForm;
    }
    const fromQuery = new URLSearchParams(window.location.search).get("directory_path");
    return text(fromQuery) || "root";
  }

  function registeredTeamSessionIds() {
    return new Set(values(peers.app.state.agents).map((agent) => text(agent.session_id)).filter(Boolean));
  }

  function normalizeCoreAgentSession(item) {
    const sessionId = text(item?.session_id);
    if (!sessionId) {
      return null;
    }
    const backend = text(item?.backend);
    const targetName = text(item?.target_name) || text(item?.agent_id) || text(item?.vm_name) || sessionId;
    return {
      session_id: sessionId,
      agent_id: text(item?.agent_id),
      backend,
      target_name: targetName,
      status: text(item?.status),
      directory_path: text(item?.directory_path),
      display_name: [targetName, backendLabelForCoreAgentSession(backend)].filter(Boolean).join(" - "),
    };
  }

  function backendLabelForCoreAgentSession(backend) {
    if (backend === "container_worker_vm") {
      return "container";
    }
    if (backend === "libvirt_vm") {
      return "VM";
    }
    return backend;
  }

  function sortCoreAgentSessions(sessions) {
    return sessions.filter(Boolean).sort((left, right) =>
      text(left.target_name).localeCompare(text(right.target_name), undefined, {
        numeric: true,
        sensitivity: "base",
      }) || text(left.session_id).localeCompare(text(right.session_id)),
    );
  }

  async function fetchCoreAgentSessions(directoryPath) {
    const normalizedDirectoryPath = text(directoryPath) || "root";
    const query = new URLSearchParams({
      directory_path: normalizedDirectoryPath,
      direct_only: "true",
      include_inactive: "true",
      backend: "all",
      summary_only: "false",
      reconcile_containers: "true",
      omit_vm_state: "false",
    });
    const response = await peers.access.teamFetch(`/api/admin/agents?${query.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    return (payload.items || []).map(normalizeCoreAgentSession).filter(Boolean);
  }

  async function loadTeamCopyCoreAgentSessions(configuration) {
    const directoryPaths = Array.from(new Set(
      configuration.agents.map((agent) => peers.copy.teamCopyAgentDirectoryPath(agent, configuration)),
    ));
    try {
      const sessionGroups = await Promise.all(
        (directoryPaths.length ? directoryPaths : ["root"]).map(fetchCoreAgentSessions),
      );
      const sessionsById = new Map();
      sessionGroups.flat().forEach((session) => sessionsById.set(session.session_id, session));
      coreAgentSessions = sortCoreAgentSessions(Array.from(sessionsById.values()));
      coreAgentSessionsDirectoryPath = "";
      return coreAgentSessions;
    } catch (error) {
      coreAgentSessions = [];
      coreAgentSessionsDirectoryPath = "";
      peers.app.setStatus(error instanceof Error ? error.message : "Agent sessions could not be loaded.", "error");
      return coreAgentSessions;
    }
  }

  async function loadCoreAgentSessions({ directoryPath = "root", force = false, rerender = false } = {}) {
    const normalizedDirectoryPath = text(directoryPath) || "root";
    if (
      !force &&
      coreAgentSessionsDirectoryPath === normalizedDirectoryPath &&
      coreAgentSessions.length > 0
    ) {
      return coreAgentSessions;
    }
    if (coreAgentSessionsLoading) {
      return coreAgentSessionsLoading;
    }
    coreAgentSessionsLoading = (async () => {
      coreAgentSessions = sortCoreAgentSessions(
        await fetchCoreAgentSessions(normalizedDirectoryPath),
      );
      coreAgentSessionsDirectoryPath = normalizedDirectoryPath;
      return coreAgentSessions;
    })();
    try {
      return await coreAgentSessionsLoading;
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Agent sessions could not be loaded.", "error");
      return coreAgentSessions;
    } finally {
      coreAgentSessionsLoading = null;
      if (rerender) {
        peers.people.renderAgents();
        peers.tooltips.decorateFieldTooltips(document);
      }
    }
  }

  async function refreshCoreAgentSessionsForForm(form, { force = false } = {}) {
    if (!form) {
      peers.app.setStatus("Agent registration form is unavailable.", "error");
      return;
    }
    await loadCoreAgentSessions({
      directoryPath: teamAgentDirectoryPath(form),
      force,
      rerender: false,
    });
    refreshCoreAgentSessionPicker(form);
  }

  function refreshCoreAgentSessionPicker(form, selectedValue = "") {
    const select = form?.elements?.existing_session_id;
    if (!select?.matches?.("[data-core-agent-session-picker]")) {
      return;
    }
    const nextValue = text(selectedValue) || text(select.value) || text(select.dataset.currentValue);
    select.innerHTML = renderCoreAgentSessionOptions(nextValue);
    if (nextValue && Array.from(select.options).some((option) => option.value === nextValue)) {
      select.value = nextValue;
    }
  }

  function renderCoreAgentSessionOptions(selectedValue = "") {
    const registeredSessions = registeredTeamSessionIds();
    const options = coreAgentSessions
      .filter((session) => session.session_id === selectedValue || !registeredSessions.has(session.session_id))
      .map((session) => [
        session.session_id,
        `${session.target_name} (${session.session_id})${session.backend ? ` - ${backendLabelForCoreAgentSession(session.backend)}` : ""}`,
      ]);
    if (coreAgentSessionsLoading) {
      return renderSelectOptions(options, { selectedValue, placeholder: "Loading sessions..." });
    }
    return renderSelectOptions(options, {
      selectedValue,
      placeholder: options.length ? "Select existing session" : "No unregistered sessions found",
    });
  }

  return {
    backendLabelForCoreAgentSession,
    bindExistingAgentPickers,
    bindTeamAgentSessionControls,
    get coreAgentSessions() { return coreAgentSessions; },
    loadCoreAgentSessions,
    loadTeamCopyCoreAgentSessions,
    normalizeCoreAgentSession,
    refreshCoreAgentSessionsForForm,
    registeredTeamSessionIds,
    renderCoreAgentSessionOptions,
    teamAgentDirectoryPath,
  };
}
