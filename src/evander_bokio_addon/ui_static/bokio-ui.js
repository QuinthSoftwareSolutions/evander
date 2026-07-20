const apiBase = "/api/addons/bokio-addon";
const pageParams = new URLSearchParams(window.location.search);
let privilegedUnlocked = pageParams.get("privileged_unlocked") === "true";

const state = {
  actions: [],
  operations: [],
  selectedActionId: null,
  busy: false,
};

const els = {
  actions: document.getElementById("actions"),
  actionDetail: document.getElementById("action-detail"),
  actionSearch: document.getElementById("action-search"),
  statusFilter: document.getElementById("status-filter"),
  kindFilter: document.getElementById("kind-filter"),
  operations: document.getElementById("operations"),
  summary: document.getElementById("connection-summary"),
  companyName: document.getElementById("company-name"),
  companyId: document.getElementById("company-id"),
  pendingCount: document.getElementById("pending-count"),
  attentionCount: document.getElementById("attention-count"),
  operationCount: document.getElementById("operation-count"),
  notice: document.getElementById("notice"),
  lockedNotice: document.getElementById("locked-notice"),
  refresh: document.getElementById("refresh"),
};

async function requestJson(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    credentials: "same-origin",
    headers: { "content-type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

function postJson(path, payload = {}) {
  return requestJson(path, { method: "POST", body: JSON.stringify(payload) });
}

function text(value) {
  return String(value ?? "").trim();
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleString();
}

function kindLabel(kind) {
  return {
    supplier_invoice: "Supplier invoice",
    journal_entry: "Journal entry",
    invoice: "Customer invoice",
    bank_payment: "Bank payment",
  }[kind] || text(kind).replaceAll("_", " ") || "Bokio action";
}

function statusLabel(status) {
  return text(status).replaceAll("_", " ") || "unknown";
}

function setNotice(message = "", tone = "info") {
  els.notice.textContent = message;
  els.notice.className = message ? `notice ${tone}` : "notice hidden";
}

function setPrivilegedUnlocked(unlocked) {
  const nextUnlocked = Boolean(unlocked);
  if (privilegedUnlocked === nextUnlocked) return;
  privilegedUnlocked = nextUnlocked;
  els.lockedNotice.classList.toggle("hidden", privilegedUnlocked);
  renderActionDetail();
}

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin) return;
  const data = event.data || {};
  if (data.type !== "admin-privileged-operations") return;
  setPrivilegedUnlocked(Boolean(data.privileged_operations?.unlocked));
});

function actionSearchText(action) {
  return [
    action.action_id,
    action.kind,
    action.status,
    action.source,
    action.note,
    JSON.stringify(action.payload || {}),
  ].join(" ").toLowerCase();
}

function filteredActions() {
  const query = els.actionSearch.value.trim().toLowerCase();
  const status = els.statusFilter.value;
  const kind = els.kindFilter.value;
  const terminal = new Set(["executed", "rejected"]);
  return state.actions.filter((action) => {
    if (query && !actionSearchText(action).includes(query)) return false;
    if (kind !== "all" && action.kind !== kind) return false;
    if (status === "open" && terminal.has(action.status)) return false;
    if (status !== "all" && status !== "open" && action.status !== status) return false;
    return true;
  });
}

function makeStatusPill(status) {
  const pill = document.createElement("span");
  pill.className = `status status-${text(status)}`;
  pill.textContent = statusLabel(status);
  return pill;
}

function renderActions() {
  const actions = filteredActions();
  els.actions.replaceChildren();
  if (!actions.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact";
    empty.textContent = "No prepared actions match these filters.";
    els.actions.append(empty);
  }
  for (const action of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `action-row${state.selectedActionId === action.action_id ? " selected" : ""}`;
    button.addEventListener("click", () => {
      state.selectedActionId = action.action_id;
      renderActions();
      renderActionDetail();
    });
    const top = document.createElement("div");
    top.className = "action-row-top";
    const title = document.createElement("strong");
    title.textContent = kindLabel(action.kind);
    top.append(title, makeStatusPill(action.status));
    const meta = document.createElement("span");
    meta.className = "action-meta";
    meta.textContent = `${formatDate(action.created_at)} · ${text(action.source) || "unknown source"}`;
    const note = document.createElement("span");
    note.className = "action-note";
    note.textContent = text(action.note) || text(action.action_id);
    button.append(top, note, meta);
    els.actions.append(button);
  }

  if (!state.actions.some((action) => action.action_id === state.selectedActionId)) {
    state.selectedActionId = actions[0]?.action_id || null;
  }
  renderActionDetail();
}

function appendProperty(grid, label, value) {
  const item = document.createElement("div");
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = text(value) || "—";
  item.append(term, detail);
  grid.append(item);
}

function appendJsonSection(parent, title, value, extraClass = "") {
  if (value == null) return;
  const section = document.createElement("section");
  section.className = `detail-section ${extraClass}`.trim();
  const heading = document.createElement("h3");
  heading.textContent = title;
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(value, null, 2);
  section.append(heading, pre);
  parent.append(section);
}

function mutationButton(label, className, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.className = className;
  button.disabled = state.busy || !privilegedUnlocked;
  button.addEventListener("click", action);
  return button;
}

async function runActionMutation(action, suffix, payload = {}) {
  if (state.busy || !privilegedUnlocked) return;
  state.busy = true;
  renderActionDetail();
  setNotice(`${action.kind === "bank_payment" ? "Submitting payment preparation" : "Updating action"}...`);
  try {
    await postJson(`/prepared-actions/${encodeURIComponent(action.action_id)}/${suffix}`, payload);
    setNotice("Bokio action updated.", "success");
    await refresh({ preserveNotice: true });
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    state.busy = false;
    renderActionDetail();
  }
}

function renderActionDetail() {
  const action = state.actions.find((item) => item.action_id === state.selectedActionId);
  els.actionDetail.replaceChildren();
  if (!action) {
    els.actionDetail.className = "empty-state";
    els.actionDetail.textContent = "Select a prepared action to review its payload and audit history.";
    return;
  }
  els.actionDetail.className = "";

  const header = document.createElement("div");
  header.className = "detail-header";
  const headingGroup = document.createElement("div");
  const eyebrow = document.createElement("div");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = action.action_id;
  const heading = document.createElement("h2");
  heading.textContent = kindLabel(action.kind);
  headingGroup.append(eyebrow, heading);
  header.append(headingGroup, makeStatusPill(action.status));
  els.actionDetail.append(header);

  if (action.kind === "bank_payment") {
    const warning = document.createElement("div");
    warning.className = "notice warning inline";
    warning.textContent = "Execution creates the payment in Bokio, but a signatory must still approve it with BankID.";
    els.actionDetail.append(warning);
  }
  if (action.status === "needs_attention" || action.status === "executing") {
    const warning = document.createElement("div");
    warning.className = "notice danger inline";
    warning.textContent = "The request may have reached Bokio. Reconcile the action in Bokio before recovering it.";
    els.actionDetail.append(warning);
  }

  const properties = document.createElement("dl");
  properties.className = "property-grid";
  appendProperty(properties, "Source", action.source);
  appendProperty(properties, "Created", formatDate(action.created_at));
  appendProperty(properties, "Updated", formatDate(action.updated_at));
  appendProperty(properties, "Note", action.note);
  els.actionDetail.append(properties);

  const controls = document.createElement("div");
  controls.className = "action-controls";
  if (action.status === "pending_approval") {
    controls.append(
      mutationButton("Approve & execute", "primary", () => runActionMutation(action, "approve-and-execute")),
      mutationButton("Reject", "danger", () => {
        const reason = window.prompt("Why is this action being rejected?", "");
        if (reason !== null) runActionMutation(action, "reject", { reason });
      }),
    );
  } else if (action.status === "approved") {
    controls.append(mutationButton("Execute", "primary", () => runActionMutation(action, "execute")));
  } else if (action.status === "failed") {
    controls.append(mutationButton("Retry", "primary", () => runActionMutation(action, "retry")));
  } else if (action.status === "needs_attention" || action.status === "executing") {
    const recoveryLabel = action.status === "executing" ? "Confirm absent & recover" : "Confirm absent & retry";
    controls.append(mutationButton(recoveryLabel, "danger", () => {
      const confirmed = window.confirm(
        "Confirm that you checked Bokio and this action was not created. Retry may create a duplicate if it already exists.",
      );
      if (confirmed) runActionMutation(action, "retry", { confirm_not_created: true });
    }));
  }
  if (controls.childElementCount) els.actionDetail.append(controls);

  appendJsonSection(els.actionDetail, "Prepared payload", action.payload || {});
  appendJsonSection(els.actionDetail, "Bokio result", action.result, "success-section");
  appendJsonSection(els.actionDetail, "Failure details", action.error, "error-section");

  const historySection = document.createElement("section");
  historySection.className = "detail-section";
  const historyHeading = document.createElement("h3");
  historyHeading.textContent = "History";
  const timeline = document.createElement("ol");
  timeline.className = "timeline";
  const history = Array.isArray(action.history) ? action.history : [];
  for (const entry of history) {
    const item = document.createElement("li");
    const line = document.createElement("div");
    line.append(makeStatusPill(entry.status));
    const date = document.createElement("time");
    date.textContent = formatDate(entry.at);
    line.append(date);
    const detail = document.createElement("p");
    detail.textContent = text(entry.detail) || "State updated";
    item.append(line, detail);
    timeline.append(item);
  }
  if (!history.length) {
    const item = document.createElement("li");
    item.textContent = "No transition history was recorded for this legacy action.";
    timeline.append(item);
  }
  historySection.append(historyHeading, timeline);
  els.actionDetail.append(historySection);
}

function renderOperations() {
  els.operations.replaceChildren();
  if (!state.operations.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact";
    empty.textContent = "No autonomous supplier-invoice operations have been recorded.";
    els.operations.append(empty);
    return;
  }
  for (const operation of state.operations) {
    const row = document.createElement("article");
    row.className = "operation-row";
    const main = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = operation.operation_key;
    const meta = document.createElement("span");
    meta.textContent = `${operation.session_id} · ${formatDate(operation.updated_at)} · attempt ${operation.attempt_count}`;
    main.append(title, meta);
    row.append(main, makeStatusPill(operation.status));
    if (operation.status === "needs_attention" || operation.status === "executing") {
      const warning = document.createElement("p");
      warning.textContent = "Outcome unknown — check Bokio before allowing a confirmed retry.";
      row.append(warning);
    }
    els.operations.append(row);
  }
}

function companyInformation(payload) {
  const result = payload?.result || payload || {};
  return result.companyInformation || result.company_information || result;
}

async function refresh(options = {}) {
  els.refresh.disabled = true;
  if (!options.preserveNotice) setNotice();
  els.summary.textContent = "Refreshing...";
  try {
    const [health, actionsPayload, operationsPayload] = await Promise.all([
      requestJson("/health"),
      requestJson("/prepared-actions"),
      requestJson("/operations?operation_kind=supplier_invoice&limit=100"),
    ]);
    state.actions = Array.isArray(actionsPayload.actions) ? actionsPayload.actions : [];
    state.operations = Array.isArray(operationsPayload.operations) ? operationsPayload.operations : [];
    els.summary.textContent = `${health.status} · ${health.settings?.company_id || "no company id"}`;
    els.companyId.textContent = health.settings?.company_id || "No company ID configured";
    els.pendingCount.textContent = String(state.actions.filter((item) => item.status === "pending_approval").length);
    els.attentionCount.textContent = String(state.actions.filter((item) => item.status === "needs_attention").length);
    els.operationCount.textContent = String(state.operations.length);
    try {
      const company = companyInformation(await requestJson("/company"));
      els.companyName.textContent = text(company.name || company.companyName || company.legalName) || "Configured company";
    } catch (error) {
      els.companyName.textContent = "Company unavailable";
      if (!options.preserveNotice) setNotice(error.message, "error");
    }
    renderActions();
    renderOperations();
  } catch (error) {
    els.summary.textContent = "Unable to load Bokio state";
    setNotice(error.message, "error");
  } finally {
    els.refresh.disabled = false;
  }
}

for (const input of [els.actionSearch, els.statusFilter, els.kindFilter]) {
  input.addEventListener("input", renderActions);
  input.addEventListener("change", renderActions);
}
els.refresh.addEventListener("click", () => refresh());
els.lockedNotice.classList.toggle("hidden", privilegedUnlocked);
refresh();
