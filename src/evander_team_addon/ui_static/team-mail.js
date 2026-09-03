function emptyMailState() {
  return {
    revision: 0,
    filters: {},
    state_counts: { pending: 0, handled: 0 },
    total_count: 0,
    limit: 50,
    offset: 0,
    has_more: false,
    items: [],
    loading: false,
    error: null,
  };
}

export function createTeamMailController({
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
  renderAgentAvatar,
  escapeHtml,
  escapeAttr,
  humanizeToken,
  taskStateLabel,
  inboxItemLabels,
  disclosureAttributes,
  captureDisclosures,
  canMutate,
  mutationLocked,
  lockedMessage,
  setStatus,
  mutationBody,
  applyMutationResult,
  reloadActiveView,
  pageSize = 50,
}) {
  let mailState = emptyMailState();
  let requestId = 0;
  let selectedItemId = null;
  let selectedState = "";
  let composerOpen = false;
  let composerSubmitting = false;
  let composerStatus = { message: "", level: "" };

  function bind() {
    documentRoot.addEventListener("change", (event) => {
      const filter = event.target?.closest?.("[data-mail-filter]");
      if (filter) {
        if (filter.dataset.mailFilter === "team_id") {
          const selectedTeamId = text(filter.value);
          const selectedAgent = getState().agents[text(elements.mailAgentFilter?.value)];
          if (selectedAgent && text(selectedAgent.team_id) !== selectedTeamId) {
            elements.mailAgentFilter.value = "";
          }
        }
        if (filter.dataset.mailFilter === "agent_id" && !text(filter.value)) {
          elements.mailDirectionFilter.value = "";
        }
        selectedItemId = null;
        void load({ offset: 0 });
        return;
      }
      if (event.target?.closest?.("[data-mail-directive-team]")) {
        renderComposer();
      }
    });

    documentRoot.addEventListener("submit", (event) => {
      const directiveForm = event.target?.closest?.("[data-mail-directive-form]");
      if (directiveForm) {
        event.preventDefault();
        void submitDirective(directiveForm);
        return;
      }
      const form = event.target?.closest?.("[data-mail-search-form]");
      if (!form) {
        return;
      }
      event.preventDefault();
      selectedItemId = null;
      void load({ offset: 0 });
    });

    documentRoot.addEventListener("click", (event) => {
      const openComposer = event.target?.closest?.("[data-mail-directive-open]");
      if (openComposer) {
        event.preventDefault();
        openDirectiveComposer();
        return;
      }
      const cancelComposer = event.target?.closest?.("[data-mail-directive-cancel]");
      if (cancelComposer) {
        event.preventDefault();
        closeDirectiveComposer({ reset: true });
        return;
      }
      const refresh = event.target?.closest?.("[data-mail-refresh]");
      if (refresh) {
        event.preventDefault();
        void load({ offset: mailState.offset || 0 });
        return;
      }
      const folder = event.target?.closest?.("[data-mail-state]");
      if (folder) {
        event.preventDefault();
        selectedState = text(folder.dataset.mailState);
        selectedItemId = null;
        void load({ offset: 0 });
        return;
      }
      const message = event.target?.closest?.("[data-mail-item-id]");
      if (message) {
        event.preventDefault();
        selectedItemId = text(message.dataset.mailItemId);
        render();
        return;
      }
      const pageButton = event.target?.closest?.("[data-mail-page]");
      if (pageButton) {
        event.preventDefault();
        const direction = text(pageButton.dataset.mailPage);
        const delta = direction === "next" ? mailState.limit : -mailState.limit;
        selectedItemId = null;
        void load({ offset: Math.max(0, Number(mailState.offset || 0) + delta) });
      }
    });
  }

  async function load({ offset = 0 } = {}) {
    const activeRequestId = ++requestId;
    const query = searchParams(offset);
    mailState = { ...mailState, loading: true, error: null };
    if (isActive()) {
      render();
    }
    try {
      const response = await teamFetch(`${apiBase}/inbox?${query.toString()}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      if (activeRequestId !== requestId) {
        return payload;
      }
      mailState = {
        ...emptyMailState(),
        ...payload,
        state_counts: { ...emptyMailState().state_counts, ...(payload.state_counts || {}) },
        items: Array.isArray(payload.items) ? payload.items : [],
        loading: false,
        error: null,
      };
      const selectedStillVisible = mailState.items.some((item) => item.id === selectedItemId);
      if (!selectedStillVisible) {
        selectedItemId = mailState.items[0]?.id || null;
      }
      if (isActive()) {
        render();
      }
      return payload;
    } catch (error) {
      if (activeRequestId !== requestId) {
        return null;
      }
      mailState = {
        ...mailState,
        loading: false,
        error: error instanceof Error ? error.message : "Agent mail failed to load.",
      };
      if (isActive()) {
        render();
      }
      return null;
    }
  }

  function searchParams(offset = 0) {
    const params = new URLSearchParams({
      limit: String(pageSize),
      offset: String(Math.max(0, Number(offset) || 0)),
    });
    const agentId = text(elements.mailAgentFilter?.value);
    const filters = {
      team_id: elements.mailTeamFilter?.value,
      agent_id: agentId,
      direction: agentId ? elements.mailDirectionFilter?.value : "",
      item_type: elements.mailTypeFilter?.value,
      state: selectedState,
      q: elements.mailSearchInput?.value,
    };
    Object.entries(filters).forEach(([key, value]) => {
      const normalized = text(value);
      if (normalized) {
        params.set(key, normalized);
      }
    });
    return params;
  }

  function render() {
    captureDisclosures?.();
    renderComposerMutationControls();
    renderComposer();
    renderFilterOptions();
    renderFolders();
    renderStatus();
    renderList();
    renderPagination();
    renderReader();
  }

  function renderComposerMutationControls() {
    const locked = Boolean(mutationLocked?.());
    const message = lockedMessage?.("create operator directives") || "";
    if (elements.mailDirectiveOpen) {
      elements.mailDirectiveOpen.disabled = locked;
      elements.mailDirectiveOpen.setAttribute("aria-disabled", String(locked));
      elements.mailDirectiveOpen.title = locked ? message : "";
    }
    if (elements.mailDirectiveSubmit) {
      elements.mailDirectiveSubmit.disabled = locked || composerSubmitting;
      elements.mailDirectiveSubmit.setAttribute("aria-disabled", String(locked || composerSubmitting));
    }
  }

  function renderComposer() {
    const form = elements.mailDirectiveForm;
    if (!form) {
      return;
    }
    form.hidden = !composerOpen;
    if (!composerOpen) {
      return;
    }

    const state = getState();
    const selectedTeamId = text(elements.mailDirectiveTeam?.value);
    const teams = values(state.teams)
      .filter((team) => !team.archived)
      .sort((left, right) => teamLabel(left).localeCompare(teamLabel(right)) || byId(left, right));
    const activeTeamIds = new Set(teams.map((team) => text(team.id)));
    const resolvedTeamId = activeTeamIds.has(selectedTeamId) ? selectedTeamId : "";
    if (elements.mailDirectiveTeam) {
      elements.mailDirectiveTeam.innerHTML = renderSelectOptions(
        teams.map((team) => [team.id, `${teamLabel(team)} (${team.id})`]),
        { selectedValue: resolvedTeamId, placeholder: "Select a Team" },
      );
    }

    const selectedProjectId = text(elements.mailDirectiveProject?.value);
    const projects = values(state.projects)
      .filter((project) => text(project.team_id) === resolvedTeamId)
      .sort((left, right) => projectLabel(left).localeCompare(projectLabel(right)) || byId(left, right));
    const activeProjectIds = new Set(projects.map((project) => text(project.id)));
    const resolvedProjectId = activeProjectIds.has(selectedProjectId) ? selectedProjectId : "";
    if (elements.mailDirectiveProject) {
      elements.mailDirectiveProject.innerHTML = renderSelectOptions(
        projects.map((project) => [project.id, `${projectLabel(project)} (${project.id})`]),
        { selectedValue: resolvedProjectId, placeholder: "No project context" },
      );
      elements.mailDirectiveProject.disabled = !resolvedTeamId;
    }

    renderComposerRecipient(state, resolvedTeamId);
    if (elements.mailDirectiveSubmit) {
      const disabled = Boolean(mutationLocked?.()) || composerSubmitting || !resolvedTeamId;
      elements.mailDirectiveSubmit.disabled = disabled;
      elements.mailDirectiveSubmit.setAttribute("aria-disabled", String(disabled));
    }
    if (elements.mailDirectiveStatus) {
      elements.mailDirectiveStatus.className = `mail-directive-form-status${
        composerStatus.level ? ` ${composerStatus.level}` : ""
      }`;
      elements.mailDirectiveStatus.textContent = composerStatus.message;
    }
  }

  function renderComposerRecipient(state, teamId) {
    if (!elements.mailDirectiveRecipient) {
      return;
    }
    const team = state.teams[teamId];
    const manager = team ? state.agents[text(team.manager_agent_id)] : null;
    if (!team) {
      elements.mailDirectiveRecipient.textContent = "Choose a Team to see its manager recipient.";
      return;
    }
    if (!manager) {
      elements.mailDirectiveRecipient.textContent = "This Team has no available manager recipient.";
      return;
    }
    elements.mailDirectiveRecipient.textContent = `Will be delivered to ${agentLabel(manager)} (${manager.id}), this Team’s manager.`;
  }

  function openDirectiveComposer() {
    if (!canMutate?.("create operator directives")) {
      return;
    }
    composerOpen = true;
    composerSubmitting = false;
    composerStatus = { message: "", level: "" };
    const currentlySelectedTeam = text(elements.mailDirectiveTeam?.value);
    const filteredTeam = text(elements.mailTeamFilter?.value);
    renderComposer();
    if (!currentlySelectedTeam && filteredTeam && elements.mailDirectiveTeam) {
      elements.mailDirectiveTeam.value = filteredTeam;
      renderComposer();
    }
    elements.mailDirectiveTeam?.focus();
  }

  function closeDirectiveComposer({ reset = false } = {}) {
    if (composerSubmitting) {
      return;
    }
    composerOpen = false;
    composerStatus = { message: "", level: "" };
    if (reset) {
      resetDirectiveComposerForm();
    }
    renderComposer();
  }

  function resetDirectiveComposerForm() {
    elements.mailDirectiveForm?.reset();
    const state = getState();
    if (elements.mailDirectiveTeam) {
      const teams = values(state.teams)
        .filter((team) => !team.archived)
        .sort((left, right) => teamLabel(left).localeCompare(teamLabel(right)) || byId(left, right));
      elements.mailDirectiveTeam.innerHTML = renderSelectOptions(
        teams.map((team) => [team.id, `${teamLabel(team)} (${team.id})`]),
        { selectedValue: "", placeholder: "Select a Team" },
      );
      elements.mailDirectiveTeam.value = "";
    }
    if (elements.mailDirectiveProject) {
      elements.mailDirectiveProject.innerHTML = renderSelectOptions([], {
        selectedValue: "",
        placeholder: "No project context",
      });
      elements.mailDirectiveProject.value = "";
      elements.mailDirectiveProject.disabled = true;
    }
  }

  async function submitDirective(form) {
    if (composerSubmitting || !canMutate?.("create operator directives")) {
      return;
    }
    if (!form.reportValidity()) {
      return;
    }
    const teamId = text(elements.mailDirectiveTeam?.value);
    const projectId = text(elements.mailDirectiveProject?.value);
    const title = text(elements.mailDirectiveTitle?.value);
    const message = text(elements.mailDirectiveMessage?.value);
    const state = getState();
    const team = state.teams[teamId];
    const manager = team ? state.agents[text(team.manager_agent_id)] : null;
    if (!team || team.archived || !manager || text(manager.status) !== "active") {
      composerStatus = {
        message: "Select an active Team with an active manager recipient.",
        level: "error",
      };
      renderComposer();
      return;
    }
    if (projectId && text(state.projects[projectId]?.team_id) !== teamId) {
      composerStatus = { message: "The selected project is not in this Team.", level: "error" };
      renderComposer();
      return;
    }

    composerSubmitting = true;
    composerStatus = { message: "Creating directive…", level: "loading" };
    renderComposer();
    const payload = mutationBody?.({
      team_id: teamId,
      project_id: projectId || null,
      title: title || null,
      message,
      source: "admin_ui_operator_directive",
    }) || {
      team_id: teamId,
      project_id: projectId || null,
      title: title || null,
      message,
      source: "admin_ui_operator_directive",
    };
    try {
      const response = await teamFetch(`${apiBase}/operator-directives`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(payload),
      });
      if (response.status === 409) {
        await reloadActiveView?.({ preserveForms: true });
        composerStatus = {
          message: "The Team state changed. Review the manager and submit the directive again.",
          level: "error",
        };
        return;
      }
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const result = await response.json();
      const directiveId = text(result.operator_directive?.id);
      await applyMutationResult?.(result);
      elements.mailTeamFilter.value = teamId;
      elements.mailAgentFilter.value = "";
      elements.mailDirectionFilter.value = "";
      elements.mailTypeFilter.value = "operator_directive";
      elements.mailSearchInput.value = "";
      selectedState = "pending";
      selectedItemId = directiveId || null;
      composerOpen = false;
      resetDirectiveComposerForm();
      composerStatus = { message: "", level: "" };
      await load({ offset: 0 });
      setStatus?.(`Created an operator directive for ${agentLabel(manager)}.`, "ok");
    } catch (error) {
      composerStatus = {
        message: error instanceof Error ? error.message : "The directive could not be created.",
        level: "error",
      };
    } finally {
      composerSubmitting = false;
      renderComposer();
    }
  }

  function teamLabel(team) {
    return text(team?.name) || text(team?.id);
  }

  function projectLabel(project) {
    return text(project?.name) || text(project?.id);
  }

  function renderFilterOptions() {
    const state = getState();
    const selectedTeamId = text(elements.mailTeamFilter?.value);
    const teamOptions = values(state.teams)
      .sort((left, right) => agentLabel(left).localeCompare(agentLabel(right)) || byId(left, right))
      .map((team) => [team.id, `${team.name || team.id} (${team.id})`]);
    if (elements.mailTeamFilter) {
      elements.mailTeamFilter.innerHTML = renderSelectOptions(teamOptions, {
        selectedValue: selectedTeamId,
        placeholder: "All teams",
      });
    }

    const selectedAgentId = text(elements.mailAgentFilter?.value);
    const agentOptions = values(state.agents)
      .filter((agent) => !selectedTeamId || text(agent.team_id) === selectedTeamId)
      .sort((left, right) => agentLabel(left).localeCompare(agentLabel(right)) || byId(left, right))
      .map((agent) => [agent.id, `${agentLabel(agent)} (${agent.id})`]);
    if (elements.mailAgentFilter) {
      elements.mailAgentFilter.innerHTML = renderSelectOptions(agentOptions, {
        selectedValue: selectedAgentId,
        placeholder: "Everyone",
      });
    }
    const hasAgent = Boolean(text(elements.mailAgentFilter?.value));
    if (elements.mailDirectionFilter) {
      elements.mailDirectionFilter.disabled = !hasAgent;
      if (!hasAgent) {
        elements.mailDirectionFilter.value = "";
      }
    }
  }

  function renderFolders() {
    const pending = Number(mailState.state_counts?.pending || 0);
    const handled = Number(mailState.state_counts?.handled || 0);
    documentRoot.querySelectorAll("[data-mail-state]").forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        text(button.dataset.mailState) === selectedState ? "true" : "false",
      );
    });
    const counts = { all: pending + handled, pending, handled };
    documentRoot.querySelectorAll("[data-mail-folder-count]").forEach((element) => {
      element.textContent = String(counts[element.dataset.mailFolderCount] || 0);
    });
  }

  function renderStatus() {
    if (!elements.mailStatus) {
      return;
    }
    elements.mailStatus.className = `mail-status${mailState.error ? " error" : ""}`;
    if (mailState.loading) {
      elements.mailStatus.textContent = "Opening the mail archive...";
      return;
    }
    if (mailState.error) {
      elements.mailStatus.textContent = mailState.error;
      return;
    }
    const noun = mailState.total_count === 1 ? "message" : "messages";
    elements.mailStatus.textContent = `${mailState.total_count} ${noun} matched.`;
  }

  function renderList() {
    if (!elements.mailList) {
      return;
    }
    if (!mailState.items.length) {
      elements.mailList.innerHTML = `<p class="empty-state">No correspondence matched these filters.</p>`;
      return;
    }
    elements.mailList.innerHTML = mailState.items.map((item) => renderRow(item)).join("");
  }

  function renderRow(item) {
    const selected = item.id === selectedItemId;
    const correspondent = correspondentFor(item);
    const party = rowParty(item);
    const subject = subjectFor(item);
    const preview = bodyFor(item).replace(/\s+/g, " ").trim();
    const priority = text(item.priority);
    return `
      <button
        type="button"
        class="mail-message-row${selected ? " is-selected" : ""}${item.state === "pending" ? " is-pending" : ""}"
        data-mail-item-id="${escapeAttr(item.id)}"
        aria-pressed="${selected ? "true" : "false"}"
      >
        ${renderAgentAvatar(correspondent, "mail-row-avatar")}
        <span class="mail-row-main">
          <span class="mail-row-line">
            <span class="mail-row-party">${escapeHtml(party)}</span>
            <time class="mail-row-time">${escapeHtml(formatRowTimestamp(item.created_at))}</time>
          </span>
          <span class="mail-row-subject">${escapeHtml(subject)}</span>
          <span class="mail-row-preview">${escapeHtml(preview || humanizeToken(item.item_type))}</span>
          <span class="mail-row-meta">${escapeHtml(humanizeToken(item.item_type))} · ${escapeHtml(item.state === "pending" ? "Awaiting action" : "Filed")}</span>
        </span>
        ${priority === "high" ? `<span class="mail-priority-mark" title="High priority">!</span>` : ""}
      </button>
    `;
  }

  function renderPagination() {
    if (!elements.mailPagination) {
      return;
    }
    const total = Number(mailState.total_count || 0);
    const offset = Number(mailState.offset || 0);
    const start = total ? offset + 1 : 0;
    const end = Math.min(total, offset + mailState.items.length);
    elements.mailPagination.innerHTML = `
      <span>${escapeHtml(`${start}-${end} of ${total}`)}</span>
      <span class="mail-pagination-actions">
        <button type="button" data-mail-page="previous" ${offset <= 0 ? "disabled" : ""}>Previous</button>
        <button type="button" data-mail-page="next" ${mailState.has_more ? "" : "disabled"}>Next</button>
      </span>
    `;
  }

  function renderReader() {
    if (!elements.mailReader) {
      return;
    }
    const item = mailState.items.find((candidate) => candidate.id === selectedItemId);
    if (!item) {
      elements.mailReader.innerHTML = `<p class="empty-state">Select a message to read it.</p>`;
      return;
    }
    const sender = senderFor(item);
    const recipient = item.recipient || { id: item.recipient_agent_id, display_name: item.recipient_agent_id };
    const task = item.task;
    const updated = text(item.updated_at) && text(item.updated_at) !== text(item.created_at)
      ? `<span>Filed ${escapeHtml(formatTimestamp(item.updated_at))}</span>`
      : "";
    elements.mailReader.innerHTML = `
      <header class="mail-reader-header">
        <h3>${escapeHtml(subjectFor(item))}</h3>
        <div class="mail-reader-identity">
          ${renderAgentAvatar(sender, "mail-reader-avatar")}
          <div class="mail-reader-addresses">
            <strong>${escapeHtml(agentLabel(sender))}${sender.role_name ? ` · ${escapeHtml(sender.role_name)}` : ""}</strong>
            <span>to ${escapeHtml(agentLabel(recipient))}${recipient.role_name ? ` · ${escapeHtml(recipient.role_name)}` : ""}</span>
          </div>
          <time class="mail-reader-date">${escapeHtml(formatTimestamp(item.created_at))}</time>
        </div>
        <div class="mail-reader-pills">
          <span class="state-pill ${escapeAttr(item.state || "pending")}">${escapeHtml(item.state === "pending" ? "Awaiting action" : "Filed")}</span>
          <span class="state-pill">${escapeHtml(inboxItemLabels[item.item_type] || humanizeToken(item.item_type))}</span>
          <span class="state-pill">${escapeHtml(humanizeToken(item.priority || "normal"))} priority</span>
          ${item.response_requested ? `<span class="state-pill warning">Reply requested</span>` : ""}
        </div>
      </header>
      <div class="mail-reader-body">${escapeHtml(bodyFor(item) || "No message body was recorded.")}</div>
      <div class="mail-reader-context">
        ${task ? `<span>Task: ${escapeHtml(task.title)} (${escapeHtml(task.id)}) · ${escapeHtml(taskStateLabel(task))}</span>` : ""}
        <span>Message ID: ${escapeHtml(item.id)}</span>
        ${updated}
      </div>
      <details class="mail-reader-envelope" ${disclosureAttributes?.(`mail:message:${item.id}:envelope`) || ""}>
        <summary>Full message envelope</summary>
        <dl class="mail-envelope-grid">${renderEnvelope(item)}</dl>
      </details>
    `;
  }

  function senderFor(item) {
    if (item.sender) {
      return item.sender;
    }
    const createdBy = text(item.payload?.created_by);
    return {
      id: createdBy || "operator-system",
      display_name: createdBy || "Operator / system",
      role_name: createdBy ? "External sender" : "System",
    };
  }

  function correspondentFor(item) {
    const focusAgentId = text(elements.mailAgentFilter?.value);
    if (focusAgentId && text(item.sender_agent_id) === focusAgentId) {
      return item.recipient || { id: item.recipient_agent_id, display_name: item.recipient_agent_id };
    }
    return senderFor(item);
  }

  function rowParty(item) {
    const sender = senderFor(item);
    const recipient = item.recipient || { id: item.recipient_agent_id, display_name: item.recipient_agent_id };
    const focusAgentId = text(elements.mailAgentFilter?.value);
    if (focusAgentId && text(item.sender_agent_id) === focusAgentId) {
      return `To: ${agentLabel(recipient)}`;
    }
    if (focusAgentId) {
      return `From: ${agentLabel(sender)}`;
    }
    return `${agentLabel(sender)} → ${agentLabel(recipient)}`;
  }

  function subjectFor(item) {
    const payload = item.payload || {};
    const candidates = [
      payload.title,
      payload.subject,
      payload.summary,
      payload.question,
      payload.message,
      payload.instructions,
    ];
    const subject = candidates.map(text).find(Boolean);
    return subject || `${inboxItemLabels[item.item_type] || humanizeToken(item.item_type)} ${item.id}`;
  }

  function bodyFor(item) {
    const payload = item.payload || {};
    const subject = subjectFor(item);
    const candidates = [
      payload.message,
      payload.body,
      payload.question,
      payload.instructions,
      payload.error,
      payload.finding,
      payload.reason,
      payload.details,
      payload.description,
      payload.summary,
    ];
    const bodyValues = candidates.map(bodyValue).filter(Boolean);
    const body = bodyValues.find((value) => value !== subject);
    if (body) {
      return body;
    }
    const fallbackEntries = Object.entries(payload)
      .filter(([key]) => !["title", "subject", "summary"].includes(key))
      .map(([key, value]) => [humanizeToken(key), bodyValue(value)])
      .filter(([, value]) => Boolean(value));
    if (fallbackEntries.length) {
      return fallbackEntries.map(([key, value]) => `${key}: ${value}`).join("\n\n");
    }
    return bodyValues[0] || "";
  }

  function bodyValue(value) {
    if (value && typeof value === "object") {
      return JSON.stringify(value, null, 2);
    }
    return text(value);
  }

  function renderEnvelope(item) {
    const entries = [
      ["Message ID", item.id],
      ["From", `${agentLabel(senderFor(item))} (${item.sender_agent_id || "external"})`],
      ["To", `${agentLabel(item.recipient)} (${item.recipient_agent_id})`],
      ["Created", formatTimestamp(item.created_at)],
      ["Updated", formatTimestamp(item.updated_at)],
      ["Type", item.item_type],
      ["State", item.state],
      ["Priority", item.priority],
      ["Response requested", item.response_requested ? "Yes" : "No"],
      ["Task", item.task_id || "None"],
      ...Object.entries(item.payload || {}).map(([key, value]) => [humanizeToken(key), envelopeValue(value)]),
    ];
    return entries
      .map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`)
      .join("");
  }

  function envelopeValue(value) {
    if (value && typeof value === "object") {
      return JSON.stringify(value, null, 2);
    }
    return text(value);
  }

  function formatTimestamp(value) {
    const raw = text(value);
    const parsed = new Date(raw);
    if (!raw || Number.isNaN(parsed.getTime())) {
      return raw || "Unknown time";
    }
    return parsed.toLocaleString([], {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatRowTimestamp(value) {
    const raw = text(value);
    const parsed = new Date(raw);
    if (!raw || Number.isNaN(parsed.getTime())) {
      return raw;
    }
    const now = new Date();
    const sameYear = parsed.getFullYear() === now.getFullYear();
    return parsed.toLocaleString([], sameYear
      ? { month: "short", day: "numeric" }
      : { year: "numeric", month: "short", day: "numeric" });
  }

  return { bind, load, render };
}
