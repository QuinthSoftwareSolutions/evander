export function editableRecipe(view) {
  return Object.fromEntries(["view_id", "title", "sections", "text_budget"].map((key) => [key, view[key]]));
}

export function safeSourceUrl(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

export function createCaseContextController({ documentRoot, teamFetch, apiBase, getState, canMutate }) {
  let dialog, caseId, directoryPath, revision, recipes, selectedSnapshot = null;
  let savedView = null;
  let generation = 0;
  const element = (tag, text = "") => {
    const node = documentRoot.createElement(tag);
    node.textContent = text;
    return node;
  };
  async function request(path, body, method = "POST") {
    const response = await teamFetch(apiBase + path, body ? {
      method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    } : {});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || payload.message || `Request failed (${response.status})`);
    return payload;
  }
  function field(label, node) {
    const wrapper = element("label", label);
    wrapper.append(node);
    return wrapper;
  }
  function message(value) { dialog.querySelector("[data-context-status]").textContent = value; }
  function button(label, handler) {
    const node = element("button", label);
    node.type = "button";
    node.addEventListener("click", () => {
      node.disabled = true;
      Promise.resolve().then(handler).catch((error) => message(error.message)).finally(() => { node.disabled = false; });
    });
    return node;
  }
  function currentView() { return JSON.parse(dialog.querySelector("textarea").value); }
  function render(context, { draft = false } = {}) {
    if (!selectedSnapshot) revision = context.revision;
    const output = dialog.querySelector("[data-context-output]");
    output.replaceChildren();
    output.append(element("p", `Evidence revision ${context.revision}. ${context.complete ? "No reported omissions." : "Some evidence is missing, restricted, stale or abbreviated."}`));
    if (context.omissions?.length) {
      const warnings = element("details");
      warnings.append(element("summary", `${context.omissions.length} context warnings`),
        element("pre", JSON.stringify(context.omissions, null, 2)));
      output.append(warnings);
    }
    for (const section of context.sections || []) {
      const details = element("details");
      details.open = ["request", "decisions"].includes(section.name);
      details.append(element("summary", `${section.name} (${section.total})`));
      for (const record of section.records) {
        details.append(element("h4", `${record.entity_type}: ${record.id}`),
          element("pre", record.text), element("small", `Digest: ${record.provenance.digest}`));
      }
      let cursor = 0;
      const load = button("Read complete evidence / next page", async () => {
        const params = new URLSearchParams({ section: section.name, cursor: String(cursor) });
        let data;
        if (selectedSnapshot) {
          data = await request(`/case-context/snapshots/${encodeURIComponent(selectedSnapshot)}?${params}`);
        } else {
          params.set("view_id", context.view.view_id);
          params.set("view_version", String(context.view.version));
          params.set("directory_path", directoryPath);
          data = await request(`/cases/${encodeURIComponent(caseId)}/context/evidence?${params}`);
        }
        for (const record of data.evidence || data.records || []) {
          const complete = element("details");
          complete.append(element("summary", record.id), element("pre", JSON.stringify(record.content, null, 2)));
          for (const link of record.content.links || []) {
            const url = safeSourceUrl(link.url);
            if (!url) continue;
            const anchor = element("a", link.label || "Open source");
            anchor.href = url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
            complete.append(anchor);
          }
          details.append(complete);
        }
        cursor = data.next_cursor;
        if (cursor === null) load.remove();
      });
      if (!draft) details.append(load);
      else details.append(element("p", "Save this recipe to browse complete evidence with these filters."));
      output.append(details);
    }
  }
  async function live(view) {
    const token = ++generation;
    selectedSnapshot = null;
    const params = new URLSearchParams({ view_id: view.view_id, view_version: String(view.version), directory_path: directoryPath });
    const data = await request(`/cases/${encodeURIComponent(caseId)}/context?${params}`);
    if (token === generation) {
      savedView = data.view;
      render(data);
    }
  }
  async function open(id) {
    const token = ++generation;
    caseId = id;
    savedView = null;
    const state = getState();
    const item = state.cases[id];
    directoryPath = state.projects[item?.project_id]?.directory_path || "root";
    if (dialog) dialog.remove();
    dialog = element("dialog"); dialog.className = "case-context-dialog";
    dialog.append(element("h2", `Case context: ${item?.title || id}`));
    const status = element("p", "Loading context…"); status.dataset.contextStatus = ""; status.setAttribute("role", "status");
    const output = element("section"); output.dataset.contextOutput = "";
    dialog.append(status, button("Close", () => { generation++; dialog.close(); }), output);
    documentRoot.body.append(dialog); dialog.showModal();
    const data = await request(`/case-context/views?${new URLSearchParams({ directory_path: directoryPath })}`);
    if (token !== generation) return;
    revision = data.revision; recipes = data.views;
    const select = element("select");
    const editor = element("textarea"); editor.rows = 14; editor.spellcheck = false;
    for (const view of recipes) {
      const option = element("option", `${view.title} (v${view.version}, ${view.directory_path})`);
      option.value = view.id; select.append(option);
    }
    select.value = recipes.find((v) => v.view_id === "case-overview")?.id || recipes[0].id;
    const selected = () => recipes.find((v) => v.id === select.value);
    const update = async () => { editor.value = JSON.stringify(editableRecipe(selected()), null, 2); await live(selected()); };
    select.addEventListener("change", () => update().catch((e) => message(e.message)));
    const controls = element("div");
    controls.append(field("View", select));
    const edit = element("details");
    edit.append(element("summary", "Edit or create a view recipe"),
      element("p", "Change view_id to create a recipe. Section sequence, order (newest_first or oldest_first), limit, text_limit, kinds and statuses control selection. Saving creates a new version."),
      field("Recipe JSON", editor),
      button("Preview draft", async () => {
        selectedSnapshot = null;
        const token = ++generation;
        const data = await request("/case-context/preview", { case_id: caseId, directory_path: directoryPath, view: currentView() });
        if (token === generation) render(data, { draft: true });
      }),
      button("Save new version", async () => {
        if (!canMutate("save case-context view")) return;
        await request("/case-context/views", { directory_path: directoryPath, expected_revision: revision, view: currentView() }, "PUT");
        await open(caseId);
      }));
    const history = element("details");
    history.append(element("summary", "Recipe version history"));
    for (const view of data.history) {
      const effective = recipes.find((recipe) => recipe.view_id === view.view_id);
      if (effective?.directory_path === view.directory_path) {
        history.append(button(`${view.view_id} v${view.version} (${view.directory_path})`, async () => {
          editor.value = JSON.stringify(editableRecipe(view), null, 2); await live(view);
        }));
      } else {
        const shadowed = element("details");
        shadowed.append(element("summary", `${view.view_id} v${view.version} (${view.directory_path}, overridden here)`),
          element("pre", JSON.stringify(editableRecipe(view), null, 2)));
        history.append(shadowed);
      }
    }
    controls.append(edit, history, button("Freeze selected saved view", async () => {
      if (!canMutate("snapshot case context")) return;
      const view = savedView;
      if (!view) throw new Error("Load a saved view before freezing it.");
      const data = await request("/case-context/snapshots", { case_id: caseId, directory_path: directoryPath,
        view_id: view.view_id, view_version: view.version, expected_revision: revision, request_id: crypto.randomUUID() });
      selectedSnapshot = data.snapshot_id; render(data.context); revision = data.revision;
      message(`Frozen snapshot ${selectedSnapshot}`);
    }), button("Browse snapshots", async () => {
      const list = element("div");
      let cursor = 0;
      const more = button("More snapshots", loadSnapshots);
      async function loadSnapshots() {
        const data = await request(`/cases/${encodeURIComponent(caseId)}/context-snapshots?${new URLSearchParams({ directory_path: directoryPath, cursor: String(cursor) })}`);
        for (const snapshot of data.records) list.append(button(`${snapshot.view_id} — ${snapshot.created_at}`, async () => {
          const data = await request(`/case-context/snapshots/${encodeURIComponent(snapshot.id)}`);
          selectedSnapshot = snapshot.id; render(data.context); message(`Frozen snapshot ${snapshot.id}`);
        }));
        cursor = data.next_cursor;
        if (cursor !== null) list.append(more); else more.remove();
      }
      await loadSnapshots();
      controls.append(list);
    }));
    dialog.insertBefore(controls, output);
    await update(); message("Context is source material, not execution authority.");
  }
  documentRoot.addEventListener("click", (event) => {
    const target = event.target?.closest?.("[data-open-case-context]");
    if (target) open(target.dataset.openCaseContext).catch((error) => message(error.message));
  });
  return { open };
}
