const $ = (id) => document.getElementById(id);
const state = { connections: [], selected: null, directory: new URLSearchParams(location.search).get("directory_path") || "root" };
$("directory").value = state.directory;
const notice = (message, error = false) => { $("notice").textContent = message; $("notice").className = error ? "error" : ""; };
const run = (fn) => async (event) => { event?.preventDefault(); try { await fn(event); } catch (error) { notice(error.message, true); } };
async function request(path, payload) {
  const response = await fetch(`/api/addons/miro-addon${path}?directory_path=${encodeURIComponent(state.directory)}`, {
    credentials: "same-origin", method: payload === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" }, body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Miro request failed");
  return result;
}
function button(label, handler) { const element = document.createElement("button"); element.type = "button"; element.textContent = label; element.addEventListener("click", run(handler)); return element; }
function addBoard(board = {}) {
  const row = document.createElement("div"); row.className = "board";
  const id = document.createElement("input"); id.type = "text"; id.placeholder = "Board ID"; id.setAttribute("aria-label", "Board ID"); id.value = board.board_id || ""; id.required = true;
  const name = document.createElement("input"); name.type = "text"; name.placeholder = "Board name"; name.setAttribute("aria-label", "Board name"); name.value = board.name || "";
  const write = document.createElement("input"); write.type = "checkbox"; write.checked = Boolean(board.writable);
  const label = document.createElement("label"); label.append(write, "Allow writes");
  row.append(id, name, label, button("Remove", () => row.remove())); $("boards").append(row);
}
function edit(connection = null) {
  state.selected = connection;
  const config = connection?.config || {};
  $("connection-id").value = config.id || ""; $("connection-id").disabled = Boolean(connection);
  $("name").value = config.name || "";
  $("callback").value = config.redirect_uri || `${location.origin}/api/addons/miro-addon/oauth/callback`;
  $("budget").value = config.daily_call_budget || 100;
  $("sessions").value = (config.session_ids || []).join("\n");
  $("enabled").checked = config.enabled !== false; $("workflows").checked = Boolean(config.allow_workflows);
  $("create-boards").checked = Boolean(config.allow_create_boards);
  $("boards").replaceChildren(); (config.boards || []).forEach(addBoard);
  $("authorize").hidden = true;
  for (const id of ["connect", "disconnect", "health"]) $(id).disabled = !connection || config.directory_path !== state.directory;
  $("activity").replaceChildren(); $("details").textContent = "";
}
async function refresh() {
  const selectedId = state.selected?.config.id;
  state.connections = (await request("/connections")).connections;
  $("connections").replaceChildren();
  for (const connection of state.connections) {
    const config = connection.config;
    $("connections").append(button(`${config.name} · ${connection.connected ? "Connected" : "Not connected"}\n${connection.calls_today}/${config.daily_call_budget} calls today · ${config.directory_path}`, async () => { edit(connection); await activity(); }));
  }
  const selected = state.connections.find((item) => item.config.id === selectedId);
  if (selected) { edit(selected); await activity(); }
  else if (!state.selected) edit();
  if (!state.connections.length) notice("No connections in this directory. Create one to get started.");
}
function connectionPath() { if (!state.selected) throw new Error("Save or select a connection first"); return `/connections/${encodeURIComponent(state.selected.config.id)}`; }
async function activity() {
  const result = await request(`${connectionPath()}/activity`); $("activity").replaceChildren();
  for (const subscription of result.subscriptions) {
    const p = document.createElement("p");
    p.textContent = `${subscription.subscription_id}: ${subscription.last_error || "Monitoring"} · last observation ${subscription.checked_at ? new Date(subscription.checked_at * 1000).toLocaleString() : "pending"}`;
    $("activity").append(p);
  }
  for (const operation of result.operations) {
    const row = document.createElement("div"); row.className = "operation";
    const title = document.createElement("div"); title.textContent = `${operation.operation.replaceAll("_", " ")} · ${operation.status} · ${operation.actor}`; row.append(title);
    if (operation.url?.startsWith("https://miro.com/app/board/")) {
      const link = document.createElement("a"); link.textContent = "Open board"; link.href = operation.url; link.target = "_blank"; link.rel = "noopener noreferrer"; row.append(link);
    }
    row.append(button("Details", () => { $("details").textContent = JSON.stringify(operation, null, 2); }));
    if (operation.status === "uncertain" || operation.status === "running") {
      const path = `${connectionPath()}/operations/${encodeURIComponent(operation.operation_id)}`;
      row.append(button("Read back", async () => { $("details").textContent = JSON.stringify(await request(`${path}/reconcile`, {}), null, 2); await activity(); }));
      for (const [label, outcome] of [["Mark applied", "applied"], ["Mark not applied", "not_applied"]]) {
        row.append(button(label, async () => {
          const evidence = window.prompt("Describe the Miro readback evidence for this outcome. This does not replay the operation.");
          if (!evidence) return;
          await request(`${path}/resolve`, { outcome, evidence }); await activity();
        }));
      }
    }
    $("activity").append(row);
  }
  if (!result.operations.length && !result.subscriptions.length) $("activity").textContent = "No operations or polling subscriptions yet.";
}
$("settings").addEventListener("submit", run(async () => {
  if (state.selected && state.selected.config.directory_path !== state.directory) throw new Error(`Load ${state.selected.config.directory_path} to edit this inherited connection.`);
  const boards = [...$("boards").children].map((row) => ({ board_id: row.querySelectorAll("input")[0].value.trim(), name: row.querySelectorAll("input")[1].value.trim(), writable: row.querySelector("input[type=checkbox]").checked }));
  const config = { id: $("connection-id").value.trim(), name: $("name").value.trim(), directory_path: state.directory,
    redirect_uri: $("callback").value.trim(), daily_call_budget: Number($("budget").value),
    session_ids: $("sessions").value.split(/\s+/).filter(Boolean), boards, enabled: $("enabled").checked,
    allow_workflows: $("workflows").checked, allow_create_boards: $("create-boards").checked };
  state.selected = await request("/connections", { config, expected_revision: state.selected?.revision || 0 });
  await refresh(); notice("Connection settings saved.");
}));
$("connect").addEventListener("click", run(async () => {
  const result = await request(`${connectionPath()}/connect`, {});
  const url = new URL(result.authorization_url);
  if (url.origin !== "https://mcp.miro.com") throw new Error("Unexpected authorization destination");
  await refresh(); $("authorize").href = url.href; $("authorize").hidden = false;
  notice("Continue to Miro to choose the account and team for this connection.");
}));
$("disconnect").addEventListener("click", run(async () => { await request(`${connectionPath()}/disconnect`, {}); await refresh(); notice("Host connection disconnected."); }));
$("health").addEventListener("click", run(async () => { const result = await request(`${connectionPath()}/health`, {}); $("details").textContent = JSON.stringify(result, null, 2); notice("Miro connection checked."); }));
$("add-board").addEventListener("click", () => addBoard());
$("new").addEventListener("click", () => edit());
$("refresh").addEventListener("click", run(refresh));
$("load-directory").addEventListener("click", run(async () => { state.directory = $("directory").value.trim(); edit(); await refresh(); }));
run(refresh)();
