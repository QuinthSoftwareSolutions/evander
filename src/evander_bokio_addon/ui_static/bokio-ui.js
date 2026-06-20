const apiBase = "/api/addons/bokio-addon";

async function getJson(path) {
  const response = await fetch(`${apiBase}${path}`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || response.statusText);
  }
  return payload;
}

async function postJson(path, payload = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function renderJson(target, payload) {
  target.textContent = JSON.stringify(payload, null, 2);
}

function renderActions(actions) {
  const container = document.getElementById("actions");
  container.replaceChildren();
  if (!actions.length) {
    container.textContent = "No prepared actions.";
    return;
  }
  for (const action of actions) {
    const row = document.createElement("article");
    row.className = "action";
    const header = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = action.kind || action.action_id;
    const status = document.createElement("span");
    status.className = "status";
    status.textContent = action.status || "unknown";
    header.append(title, status);
    const details = document.createElement("pre");
    details.textContent = JSON.stringify(action, null, 2);
    row.append(header, details);
    if (action.status === "pending_approval") {
      const approve = document.createElement("button");
      approve.type = "button";
      approve.textContent = "Approve";
      approve.addEventListener("click", async () => {
        await postJson(`/prepared-actions/${action.action_id}/approve`);
        await refresh();
      });
      row.append(approve);
    }
    container.append(row);
  }
}

async function refresh() {
  const summary = document.getElementById("connection-summary");
  const company = document.getElementById("company");
  summary.textContent = "Refreshing...";
  try {
    const health = await getJson("/health");
    summary.textContent = `${health.status} - ${health.settings.company_id || "no company id"}`;
    try {
      renderJson(company, await getJson("/company"));
    } catch (error) {
      renderJson(company, { error: error.message });
    }
    const actions = await getJson("/prepared-actions");
    renderActions(actions.actions || []);
  } catch (error) {
    summary.textContent = error.message;
  }
}

document.getElementById("refresh").addEventListener("click", refresh);
refresh();
