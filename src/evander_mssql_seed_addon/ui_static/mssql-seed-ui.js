const state = {
  sidecars: [],
  seeds: [],
  databases: [],
};

const els = {
  sidecarsBody: document.getElementById("sidecarsBody"),
  seedsBody: document.getElementById("seedsBody"),
  databasesBody: document.getElementById("databasesBody"),
  status: document.getElementById("statusMessage"),
  refreshButton: document.getElementById("refreshButton"),
  refreshDatabasesButton: document.getElementById("refreshDatabasesButton"),
  saveButton: document.getElementById("saveButton"),
  addSidecarButton: document.getElementById("addSidecarButton"),
  addSeedButton: document.getElementById("addSeedButton"),
};

function api(path, options = {}) {
  return fetch(`/api/addons/mssql-seed-addon${path}`, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  }).then(async (response) => {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `Request failed with HTTP ${response.status}`);
    }
    return payload;
  });
}

function setStatus(message, isError = false) {
  els.status.textContent = message;
  els.status.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function editableCell(value, placeholder, onInput) {
  const td = document.createElement("td");
  const input = document.createElement("input");
  input.value = value || "";
  input.placeholder = placeholder;
  input.addEventListener("input", () => onInput(input.value));
  td.append(input);
  return td;
}

function renderSettings() {
  els.sidecarsBody.replaceChildren();
  if (!state.sidecars.length) {
    els.sidecarsBody.append(emptyRow(5, "No sidecars are configured."));
  }
  state.sidecars.forEach((sidecar, index) => {
    const row = document.createElement("tr");
    row.append(
      editableCell(sidecar.sidecar_id, "worker-a", (value) => (sidecar.sidecar_id = value)),
      editableCell(sidecar.base_url, "http://worker:11990", (value) => (sidecar.base_url = value)),
      editableCell(sidecar.token_env, "EVANDER_MSSQL_SEED_SIDECAR_TOKEN", (value) => (sidecar.token_env = value)),
      editableCell(sidecar.description, "Optional", (value) => (sidecar.description = value)),
      actionCell(() => {
        state.sidecars.splice(index, 1);
        renderSettings();
      }),
    );
    els.sidecarsBody.append(row);
  });

  els.seedsBody.replaceChildren();
  if (!state.seeds.length) {
    els.seedsBody.append(emptyRow(5, "No approved seeds are configured."));
  }
  state.seeds.forEach((seed, index) => {
    const row = document.createElement("tr");
    row.append(
      editableCell(seed.seed_alias, "app-demo", (value) => (seed.seed_alias = value)),
      editableCell(seed.sidecar_id, "worker-a", (value) => (seed.sidecar_id = value)),
      editableCell(seed.backup_path, "/var/lib/evander/mssql-seeds/seed.bak", (value) => (seed.backup_path = value)),
      editableCell(seed.description, "Optional", (value) => (seed.description = value)),
      actionCell(() => {
        state.seeds.splice(index, 1);
        renderSettings();
      }),
    );
    els.seedsBody.append(row);
  });
}

function renderDatabases() {
  els.databasesBody.replaceChildren();
  if (!state.databases.length) {
    els.databasesBody.append(emptyRow(6, "No seeded databases have been provisioned yet."));
  }
  state.databases.forEach((database) => {
    const row = document.createElement("tr");
    [
      database.session_id,
      database.logical_name,
      database.seed_alias,
      database.database_name,
      database.reset_count,
      database.last_reset_at || "",
    ].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      row.append(td);
    });
    els.databasesBody.append(row);
  });
}

function emptyRow(colspan, text) {
  const row = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = colspan;
  td.className = "empty";
  td.textContent = text;
  row.append(td);
  return row;
}

function actionCell(onRemove) {
  const td = document.createElement("td");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "danger";
  button.textContent = "Remove";
  button.addEventListener("click", onRemove);
  td.append(button);
  return td;
}

async function loadSettings() {
  setStatus("Loading settings...");
  const payload = await api("/settings");
  state.sidecars = payload.sidecars || [];
  state.seeds = payload.seeds || [];
  renderSettings();
  setStatus("Settings loaded.");
}

async function loadDatabases() {
  const payload = await api("/databases");
  state.databases = payload.databases || [];
  renderDatabases();
}

async function saveSettings() {
  setStatus("Saving settings...");
  const payload = await api("/settings", {
    method: "PUT",
    body: JSON.stringify({ sidecars: state.sidecars, seeds: state.seeds }),
  });
  state.sidecars = payload.sidecars || [];
  state.seeds = payload.seeds || [];
  renderSettings();
  await loadDatabases();
  setStatus("Settings saved.");
}

els.addSidecarButton.addEventListener("click", () => {
  state.sidecars.push({
    sidecar_id: "",
    base_url: "",
    token_env: "EVANDER_MSSQL_SEED_SIDECAR_TOKEN",
    description: "",
  });
  renderSettings();
});

els.addSeedButton.addEventListener("click", () => {
  state.seeds.push({
    seed_alias: "",
    sidecar_id: state.sidecars[0]?.sidecar_id || "",
    backup_path: "",
    description: "",
  });
  renderSettings();
});

els.refreshButton.addEventListener("click", async () => {
  try {
    await loadSettings();
    await loadDatabases();
  } catch (error) {
    setStatus(error.message, true);
  }
});

els.refreshDatabasesButton.addEventListener("click", async () => {
  try {
    await loadDatabases();
    setStatus("Tracked databases refreshed.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

els.saveButton.addEventListener("click", async () => {
  try {
    await saveSettings();
  } catch (error) {
    setStatus(error.message, true);
  }
});

Promise.all([loadSettings(), loadDatabases()]).catch((error) => setStatus(error.message, true));
