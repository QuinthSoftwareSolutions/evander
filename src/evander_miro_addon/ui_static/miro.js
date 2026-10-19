import { onAddonPrivileges } from "./admin-design.js";

function applyTheme(theme) {
  if (theme !== "light" && theme !== "dark") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}
function storedTheme() {
  try {
    const theme = window.localStorage.getItem("admin-web:theme");
    return theme === "light" || theme === "dark" ? theme : null;
  } catch { return null; }
}
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
const applyPreferredTheme = () => applyTheme(storedTheme() || (systemTheme.matches ? "dark" : "light"));
applyPreferredTheme();
window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin || event.source !== window.parent) return;
  if (event.data?.type === "admin-addon-context") applyTheme(event.data.theme);
});
if (window.parent !== window) {
  window.parent.postMessage({ type: "admin-addon-ready" }, window.location.origin);
} else {
  systemTheme.addEventListener("change", applyPreferredTheme);
  window.addEventListener("storage", (event) => {
    if (event.key === "admin-web:theme" || event.key === null) applyPreferredTheme();
  });
}

const $ = (id) => document.getElementById(id);
const state = { connections: [], selected: null, directory: new URLSearchParams(location.search).get("directory_path") || "root" };
let privilegedUnlocked = false;
const lockedMessage = "Unlock privileged operations in Admin Web to edit settings or change Miro state.";
function applyPrivileges() {
  const inherited = Boolean(state.selected && state.selected.config.directory_path !== state.directory);
  document.querySelectorAll('[data-privileged-action], #new').forEach(control => {
    control.disabled = !privilegedUnlocked;
    control.title = !privilegedUnlocked ? lockedMessage : '';
  });
  document.querySelectorAll('#settings input, #settings button').forEach(control => {
    control.disabled = !privilegedUnlocked || inherited;
    control.title = !privilegedUnlocked ? lockedMessage : inherited ? `Load ${state.selected.config.directory_path} to edit this connection.` : '';
  });
  $('connection-id').disabled = Boolean(state.selected) || !privilegedUnlocked;
  for (const id of ['connect','disconnect','health']) $(id).disabled = !state.selected || inherited || !privilegedUnlocked;
  $('privilege-notice').hidden = privilegedUnlocked;
}
$("directory").value = state.directory;
const notice = (message, error = false) => { $("notice").textContent = message; $("notice").className = error ? "error" : ""; };
const run = (fn) => async (event) => { event?.preventDefault(); try { await fn(event); } catch (error) { notice(error.message, true); } };
async function request(path, payload) {
  if (payload !== undefined && !privilegedUnlocked) throw new Error(lockedMessage);
  const response = await fetch(`/api/addons/miro-addon${path}?directory_path=${encodeURIComponent(state.directory)}`, {
    credentials: "same-origin", method: payload === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" }, body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Miro request failed");
  return result;
}
function button(label, handler, mutation = false) { const element = document.createElement("button"); element.type = "button"; element.textContent = label; if(mutation) element.dataset.privilegedAction=''; element.addEventListener("click", run(async event => { if(mutation && !privilegedUnlocked) throw new Error(lockedMessage); await handler(event); })); return element; }
function edit(connection = null) {
  state.selected = connection;
  const config = connection?.config || {};
  $("editor-title").textContent = connection ? config.name : "New connection";
  $("connections").querySelectorAll("button").forEach(control => {
    control.setAttribute("aria-current", String(control.dataset.connectionId === config.id));
  });
  $("connection-id").value = config.id || ""; $("connection-id").disabled = Boolean(connection);
  $("name").value = config.name || "";
  $("callback").value = config.redirect_uri || `${location.origin}/api/addons/miro-addon/oauth/callback`;
  $("budget").value = config.daily_call_budget || 100;
  $("enabled").checked = config.enabled !== false; $("workflows").checked = Boolean(config.allow_workflows);
  $("create-boards").checked = Boolean(config.allow_create_boards);
  $("authorize").hidden = true;
  for (const id of ["connect", "disconnect", "health"]) $(id).disabled = !connection || config.directory_path !== state.directory;
  $("activity").replaceChildren(); $("details").textContent = "";
  $("grants").replaceChildren(); $("grant-session").replaceChildren();
  $("grant-board").value = "";
  applyPrivileges();
}
async function refresh() {
  const selectedId = state.selected?.config.id;
  state.connections = (await request("/connections")).connections;
  $("connections").replaceChildren();
  for (const connection of state.connections) {
    const config = connection.config;
    const control = button("", async () => { edit(connection); await activity(); });
    control.dataset.connectionId = config.id;
    const title = document.createElement("strong"); title.textContent = config.name;
    const status = document.createElement("span"); status.textContent = connection.connected ? "Connected" : "Not connected";
    const detail = document.createElement("small"); detail.textContent = `${connection.calls_today}/${config.daily_call_budget} calls today · ${config.directory_path}`;
    control.append(title, status, detail);
    $("connections").append(control);
  }
  const selected = state.connections.find((item) => item.config.id === selectedId) || state.connections[0];
  if (selected) { edit(selected); await activity(); }
  else edit();
  if (!state.connections.length) notice("No connections in this directory. Create one to get started.");
}
function connectionPath() { if (!state.selected) throw new Error("Save or select a connection first"); return `/connections/${encodeURIComponent(state.selected.config.id)}`; }
async function activity() {
  await refreshGrants();
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
    const operationDetails = document.createElement("details");
    const summary = document.createElement("summary"); summary.textContent = "Operation details";
    const payload = document.createElement("pre"); payload.textContent = JSON.stringify(operation, null, 2);
    operationDetails.append(summary, payload);
    row.append(operationDetails);
    if (operation.status === "uncertain" || operation.status === "running") {
      const path = `${connectionPath()}/operations/${encodeURIComponent(operation.operation_id)}`;
      row.append(button("Read back", async () => {
        $("details").textContent = JSON.stringify(await request(`${path}/reconcile`, {}), null, 2);
        await activity();
        $("details").scrollIntoView({ block: "nearest" });
        $("details").focus({ preventScroll: true });
      }, true));
      for (const [label, outcome] of [["Mark applied", "applied"], ["Mark not applied", "not_applied"]]) {
        row.append(button(label, async () => {
          const evidence = window.prompt("Describe the Miro readback evidence for this outcome. This does not replay the operation.");
          if (!evidence) return;
          await request(`${path}/resolve`, { outcome, evidence }); await activity();
        }, true));
      }
    }
    $("activity").append(row);
  }
  if (!result.operations.length && !result.subscriptions.length) $("activity").textContent = "No operations or polling subscriptions yet.";
  applyPrivileges();
}
async function refreshGrants() {
  const result = await request(`${connectionPath()}/session-grants`);
  const previous = $("grant-session").value;
  $("grant-session").replaceChildren();
  for (const session of result.sessions) {
    const option = document.createElement("option"); option.value = session.session_id;
    option.textContent = `${session.session_id} · ${session.directory_path}`;
    $("grant-session").append(option);
  }
  if ([...$("grant-session").options].some(option => option.value === previous)) $("grant-session").value = previous;
  state.grants = result.grants;
  $("grants").replaceChildren();
  for (const grant of result.grants) {
    const row = document.createElement("div"); row.className = "operation";
    const label = document.createElement("p");
    label.textContent = `${grant.session_id} · ${grant.board_id} · ${grant.access === 'edit' ? 'Read and edit' : 'Read'}${grant.active ? '' : ' · Revoked'}${grant.session_valid ? '' : ' · Session expired or replaced'}`;
    row.append(label, button("Change access", () => {
      if (!grant.session_valid) throw new Error("Select a current session to grant access again.");
      $("grant-session").value = grant.session_id; $("grant-board").value = grant.board_id;
      $("grant-access").value = grant.access; $("grant-form").scrollIntoView({block:'nearest'});
    }, true), button("Revoke", async () => {
      await request(`${connectionPath()}/session-grants/${encodeURIComponent(grant.grant_id)}/revoke`, {expected_revision:grant.revision});
      await refreshGrants(); notice("Session board access revoked.");
    }, true));
    $("grants").append(row);
  }
  if (!result.grants.length) $("grants").textContent = "No board access has been granted to sessions in this directory.";
  applyPrivileges();
}
$("grant-form").addEventListener("submit", run(async () => {
  const session = $("grant-session").value;
  const reference = $("grant-board").value.trim();
  let boardId = reference;
  if (reference.startsWith('https://miro.com/app/board/')) boardId = decodeURIComponent(new URL(reference).pathname.split('/')[3]);
  const current = (state.grants || []).find(g => g.session_id === session && g.board_id === boardId && g.session_valid);
  await request(`${connectionPath()}/session-grants`, {session_id:session, board_id:reference,
    access:$("grant-access").value, expected_revision:current?.revision || 0});
  await refreshGrants(); notice("Session board access saved.");
}));
$("settings").addEventListener("submit", run(async () => {
  if (state.selected && state.selected.config.directory_path !== state.directory) throw new Error(`Load ${state.selected.config.directory_path} to edit this inherited connection.`);
  const config = { id: $("connection-id").value.trim(), name: $("name").value.trim(), directory_path: state.directory,
    redirect_uri: $("callback").value.trim(), daily_call_budget: Number($("budget").value),
    enabled: $("enabled").checked,
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
$("new").addEventListener("click", () => edit());
$("refresh").addEventListener("click", run(refresh));
$("load-directory").addEventListener("click", run(async () => { state.directory = $("directory").value.trim(); edit(); await refresh(); }));
onAddonPrivileges(unlocked => { privilegedUnlocked=unlocked;applyPrivileges(); });
run(refresh)();
