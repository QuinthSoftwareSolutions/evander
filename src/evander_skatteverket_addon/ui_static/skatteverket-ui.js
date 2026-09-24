import { onAddonPrivileges } from "./admin-design.js";

(function () {
  const apiBase = "/api/addons/skatteverket-addon";
  const connectionEl = document.getElementById("connection");
  const catalogEl = document.getElementById("catalog");
  const draftsEl = document.getElementById("drafts");
  const submissionsEl = document.getElementById("submissions");
  const refreshButton = document.getElementById("refresh");
  const connectionForm = document.getElementById("connection-form");
  const apiPickerEl = document.getElementById("api-picker");
  const formStatusEl = document.getElementById("connection-form-status");
  let catalogApis = [];
  let privilegedUnlocked = false;
  let saving = false;
  const lockedMessage = "Unlock privileged operations in Admin Web to edit Skatteverket connections.";
  function applyPrivileges() {
    connectionForm.querySelectorAll('input, select, button').forEach(control => {
      control.disabled = saving || !privilegedUnlocked;
      control.title = privilegedUnlocked ? '' : lockedMessage;
    });
    document.getElementById('privilege-notice').hidden = privilegedUnlocked;
  }

  function row(title, detail) {
    const item = document.createElement("div");
    item.className = "skv-row";
    const strong = document.createElement("strong");
    strong.textContent = title;
    const span = document.createElement("span");
    span.textContent = detail;
    item.append(strong, span);
    return item;
  }

  function renderList(container, items, emptyText, mapper) {
    container.replaceChildren();
    if (!items.length) {
      container.append(row(emptyText, ""));
      return;
    }
    for (const item of items) {
      const mapped = mapper(item);
      container.append(row(mapped.title, mapped.detail));
    }
  }

  function setFormStatus(message, state) {
    formStatusEl.textContent = message || "";
    formStatusEl.dataset.state = state || "";
  }

  function input(name) {
    return connectionForm.elements.namedItem(name);
  }

  function value(name) {
    const element = input(name);
    return element && "value" in element ? String(element.value).trim() : "";
  }

  function selectedApiNames() {
    return Array.from(apiPickerEl.querySelectorAll('input[name="enabled_api_names"]:checked')).map(
      (checkbox) => checkbox.value,
    );
  }

  function renderApiPicker(apis, enabledNames) {
    const selected = new Set(enabledNames || []);
    apiPickerEl.replaceChildren();
    for (const api of apis) {
      const option = document.createElement("label");
      option.className = "skv-api-option";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.name = "enabled_api_names";
      checkbox.value = api.name;
      checkbox.checked = selected.size ? selected.has(api.name) : true;
      const text = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = api.display_name || api.name;
      const detail = document.createElement("small");
      detail.textContent = `${api.name} · ${api.version} · ${(api.implemented_operations || []).join(", ") || "configured later"}`;
      text.append(title, detail);
      option.append(checkbox, text);
      apiPickerEl.append(option);
    }
  }

  function populateConnectionForm(connection, apis) {
    const settings = connection || {};
    input("connection_id").value = settings.connection_id || "default";
    input("display_name").value = settings.display_name || "";
    input("environment").value = settings.environment || "sandbox";
    input("enabled").checked = settings.enabled !== false;
    input("base_url").value = settings.base_url || "";
    input("token_url").value = settings.token_url || "";
    input("submission_endpoint").value = settings.submission_endpoint || "";
    input("client_id_env").value = settings.client_id_env || "";
    input("client_secret_env").value = settings.client_secret_env || "";
    input("apigw_client_id_env").value = settings.apigw_client_id_env || "";
    input("apigw_client_secret_env").value = settings.apigw_client_secret_env || "";
    input("api_key_env").value = settings.api_key_env || "";
    input("certificate_subject").value = settings.certificate_subject || "";
    input("redirect_uri").value = settings.redirect_uri || "";
    renderApiPicker(apis, settings.enabled_api_names || []);
  }

  function connectionPayload() {
    const enabledApiNames = selectedApiNames();
    if (!enabledApiNames.length) {
      throw new Error("Select at least one API, or disable the connection.");
    }
    return {
      connection_id: value("connection_id") || "default",
      display_name: value("display_name") || value("connection_id") || "default",
      environment: value("environment") || "sandbox",
      enabled: Boolean(input("enabled").checked),
      base_url: value("base_url"),
      token_url: value("token_url"),
      submission_endpoint: value("submission_endpoint"),
      client_id_env: value("client_id_env"),
      client_secret_env: value("client_secret_env"),
      apigw_client_id_env: value("apigw_client_id_env"),
      apigw_client_secret_env: value("apigw_client_secret_env"),
      api_key_env: value("api_key_env"),
      certificate_subject: value("certificate_subject"),
      redirect_uri: value("redirect_uri"),
      enabled_api_names: enabledApiNames,
    };
  }

  async function saveConnection(event) {
    event.preventDefault();
    if (!privilegedUnlocked || saving) { setFormStatus(lockedMessage, "error"); return; }
    saving = true;
    applyPrivileges();
    setFormStatus("Saving...", "pending");
    try {
      const response = await fetch(`${apiBase}/connections`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(connectionPayload()),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Save failed with HTTP ${response.status}`);
      }
      populateConnectionForm(payload.connection, catalogApis);
      setFormStatus("Saved", "success");
      await load({ preserveStatus: true });
    } catch (error) {
      setFormStatus(String(error), "error");
    } finally {
      saving = false;
      applyPrivileges();
    }
  }

  async function load(options = {}) {
    refreshButton.disabled = true;
    try {
      const [status, catalog, state] = await Promise.all([
        fetch(`${apiBase}/connection-status`).then((response) => response.json()),
        fetch(`${apiBase}/catalog`).then((response) => response.json()),
        fetch(`${apiBase}/state`).then((response) => response.json()),
      ]);
      catalogApis = catalog.apis || [];
      connectionEl.textContent = JSON.stringify(status, null, 2);
      populateConnectionForm((state.connections || [])[0], catalogApis);
      applyPrivileges();
      renderList(catalogEl, catalogApis, "No APIs", (item) => ({
        title: item.display_name || item.name,
        detail: `${item.name} · ${item.version} · ${(item.implemented_operations || []).join(", ") || "configured later"}`,
      }));
      renderList(draftsEl, state.drafts || [], "No drafts", (item) => ({
        title: item.draft_id,
        detail: `${item.api_name} · ${item.status} · revision ${item.revision}`,
      }));
      renderList(submissionsEl, state.recent_submissions || [], "No submissions", (item) => ({
        title: item.submission_id,
        detail: `${item.api_name} · ${item.status} · ${item.created_at}`,
      }));
      if (!options.preserveStatus) {
        setFormStatus("", "");
      }
    } catch (error) {
      connectionEl.textContent = String(error);
      setFormStatus(String(error), "error");
    } finally {
      refreshButton.disabled = false;
    }
  }

  refreshButton.addEventListener("click", load);
  connectionForm.addEventListener("submit", saveConnection);
  onAddonPrivileges(unlocked => { privilegedUnlocked=unlocked;applyPrivileges(); });
  load();
})();
