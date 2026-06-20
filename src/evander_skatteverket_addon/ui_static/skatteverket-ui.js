(function () {
  const apiBase = "/api/addons/skatteverket-addon";
  const connectionEl = document.getElementById("connection");
  const catalogEl = document.getElementById("catalog");
  const draftsEl = document.getElementById("drafts");
  const submissionsEl = document.getElementById("submissions");
  const refreshButton = document.getElementById("refresh");

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

  async function load() {
    refreshButton.disabled = true;
    try {
      const [status, catalog, state] = await Promise.all([
        fetch(`${apiBase}/connection-status`).then((response) => response.json()),
        fetch(`${apiBase}/catalog`).then((response) => response.json()),
        fetch(`${apiBase}/state`).then((response) => response.json()),
      ]);
      connectionEl.textContent = JSON.stringify(status, null, 2);
      renderList(catalogEl, catalog.apis || [], "No APIs", (item) => ({
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
    } catch (error) {
      connectionEl.textContent = String(error);
    } finally {
      refreshButton.disabled = false;
    }
  }

  refreshButton.addEventListener("click", load);
  load();
})();
