export function editableRecipe(view) {
  return Object.fromEntries(["view_id", "title", "sections", "text_budget"].map((key) => [key, view[key]]));
}

export function safeSourceUrl(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

const SECTION_COPY = {
  request: ["Request", "The case request, scope and current case details."],
  sources: ["Sources", "Captured source material and its freshness."],
  plans: ["Plans", "Tasks, working plans and supporting artifacts."],
  decisions: ["Decisions", "Recorded decisions, notes and rationale."],
  delivery: ["Delivery", "Deliverables produced for this case."],
  review: ["Review", "Review requests, decisions and findings."],
  verification: ["Verification", "Release and verification evidence."],
  conversation: ["Conversation", "Linked inbox and conversation history."],
};

const TOKEN_ACRONYMS = new Map([
  ["ado", "ADO"], ["api", "API"], ["id", "ID"], ["ids", "IDs"],
  ["json", "JSON"], ["mcp", "MCP"], ["pr", "PR"], ["sha", "SHA"],
  ["url", "URL"], ["urls", "URLs"],
]);

export function humanizeCaseContextToken(value) {
  return String(value || "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => TOKEN_ACRONYMS.get(part.toLowerCase()) || `${part[0].toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

export function caseContextWarningText(warning = {}) {
  const section = SECTION_COPY[warning.section]?.[0] || humanizeCaseContextToken(warning.section || "context");
  const subject = warning.id || warning.source_id || warning.case_id || warning.artifact_ref;
  const suffix = subject ? ` (${subject})` : "";
  switch (warning.kind) {
    case "truncated": return `${section} evidence was shortened in this overview${suffix}. Load complete records to read it all.`;
    case "artifact_omission": {
      const detail = typeof warning.detail === "string" && warning.detail.trim()
        ? `: ${warning.detail.trim()}`
        : "";
      return `Some content from the artifact bundle was omitted while it was read${suffix}${detail}.`;
    }
    case "paginated": return `${section} has ${warning.omitted_count || "more"} additional record${warning.omitted_count === 1 ? "" : "s"} not shown yet.`;
    case "restricted": return `Some ${section.toLowerCase()} evidence is outside your access scope.`;
    case "missing": return `No captured source evidence is linked to this case${suffix}.`;
    case "unavailable": return `Some ${section.toLowerCase()} evidence is no longer available${suffix}.`;
    case "stale": return `A captured source may be out of date${suffix}.`;
    case "capture_unavailable": return `Source capture was unavailable for this case${suffix}.`;
    case "incomplete_discussion": return `The captured source discussion may be incomplete${suffix}.`;
    case "removed_or_out_of_scope": return "Some evidence from the earlier context is no longer present or visible.";
    case "wake_budget": return "The context was shortened to fit the agent wake budget.";
    default: return `${section}: ${humanizeCaseContextToken(warning.kind || "context warning")}.`;
  }
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
  const classed = (tag, className, text = "") => {
    const node = element(tag, text);
    node.className = className;
    return node;
  };
  const sectionLabel = (name) => SECTION_COPY[name]?.[0] || humanizeCaseContextToken(name);
  const formatTimestamp = (value) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
  };
  const tokenClass = (value) => String(value || "neutral").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  const pill = (value, className = tokenClass(value)) => classed(
    "span", `state-pill ${className}`, humanizeCaseContextToken(value),
  );
  function structuredValue(value, key = "", depth = 0) {
    if (value === null || value === undefined || value === "") {
      return classed("span", "case-context-empty-value", "Not set");
    }
    if (typeof value === "boolean") return element("span", value ? "Yes" : "No");
    if (typeof value === "number") return element("span", String(value));
    if (typeof value === "string") {
      const url = safeSourceUrl(value);
      if (url && /(^|_)(url|uri|href|link)$/.test(key)) {
        const anchor = element("a", value);
        anchor.href = url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
        return anchor;
      }
      if ((/_at$|timestamp|date/.test(key)) && !Number.isNaN(Date.parse(value))) {
        const time = element("time", formatTimestamp(value));
        time.dateTime = value;
        time.title = value;
        return time;
      }
      if (/(^|_)(id|ids|sha|digest|ref|refs)$/.test(key)) return classed("code", "case-context-code", value);
      return classed(value.includes("\n") || value.length > 180 ? "div" : "span", "case-context-field-text", value);
    }
    if (Array.isArray(value)) {
      if (!value.length) return classed("span", "case-context-empty-value", "None");
      const list = classed("ul", "case-context-value-list");
      for (const item of value) {
        const row = element("li");
        row.append(structuredValue(item, key.replace(/s$/, ""), depth + 1));
        list.append(row);
      }
      return list;
    }
    if (typeof value === "object") {
      if (depth > 4) return classed("pre", "case-context-raw", JSON.stringify(value, null, 2));
      const fields = classed("dl", "case-context-field-grid nested");
      for (const [childKey, childValue] of Object.entries(value)) {
        fields.append(element("dt", humanizeCaseContextToken(childKey)));
        const detail = element("dd");
        detail.append(structuredValue(childValue, childKey, depth + 1));
        fields.append(detail);
      }
      return fields;
    }
    return element("span", String(value));
  }
  function recordContent(record) {
    if (record?.content && typeof record.content === "object") return record.content;
    try { return JSON.parse(record?.text || ""); } catch { return null; }
  }
  function recordHeading(content, record) {
    for (const key of ["title", "display_name", "name", "subject", "summary"]) {
      if (typeof content?.[key] === "string" && content[key].trim()) return [content[key], key];
    }
    return [`${humanizeCaseContextToken(record.entity_type)} record`, null];
  }
  function appendNarrativeBlock(parent, label, content) {
    const block = classed("div", "case-context-narrative-block");
    block.append(classed("div", "case-context-field-label", label), content);
    parent.append(block);
  }
  function appendSourceNarrative(parent, content) {
    const body = content?.body;
    if (body && typeof body === "object" && typeof body.content === "string" && body.content.trim()) {
      appendNarrativeBlock(parent, "Captured content", classed("div", "case-context-field-text", body.content));
    }

    const criteria = Array.isArray(content?.acceptance_criteria)
      ? content.acceptance_criteria.filter((criterion) => typeof criterion?.text === "string" && criterion.text.trim())
      : [];
    if (criteria.length) {
      const list = classed("ul", "case-context-value-list");
      for (const criterion of criteria) {
        const row = element("li");
        row.append(classed("span", "case-context-field-text", criterion.text));
        if (criterion.status) row.append(pill(criterion.status));
        list.append(row);
      }
      appendNarrativeBlock(parent, "Acceptance criteria", list);
    }

    const discussion = Array.isArray(content?.discussion)
      ? content.discussion.filter((entry) => typeof entry?.body === "string" && entry.body.trim())
      : [];
    if (discussion.length) {
      const list = classed("ul", "case-context-value-list");
      for (const entry of discussion) {
        const row = element("li");
        const attribution = [entry.author, entry.occurred_at ? formatTimestamp(entry.occurred_at) : null]
          .filter(Boolean).join(" · ");
        if (attribution) row.append(classed("div", "case-context-record-meta", attribution));
        row.append(classed("div", "case-context-field-text", entry.body));
        list.append(row);
      }
      appendNarrativeBlock(parent, "Discussion", list);
    }
  }
  function renderRecord(record, { complete = false } = {}) {
    const card = classed("article", "case-context-record");
    const content = recordContent(record);
    const [heading, headingKey] = recordHeading(content, record);
    const header = classed("header", "case-context-record-header");
    const title = element("h4", heading);
    const badges = classed("div", "case-context-record-badges");
    badges.append(pill(record.entity_type, "neutral"));
    const freshness = record.entity_type === "source" ? content?.effective_freshness_status : null;
    const status = freshness ?? content?.status ?? content?.state;
    if (status) badges.append(pill(freshness ? `Freshness: ${freshness}` : status, tokenClass(status)));
    if (record.truncated && !complete) badges.append(pill("Shortened", "warning"));
    header.append(title, badges);
    card.append(header);

    const timestamp = content?.updated_at || content?.created_at || record.provenance?.created_at;
    if (timestamp) {
      const meta = classed("p", "case-context-record-meta", `Recorded ${formatTimestamp(timestamp)}`);
      meta.title = timestamp;
      card.append(meta);
    }
    if (content) {
      const narrative = classed("div", "case-context-record-narrative");
      for (const key of ["summary", "description", "body", "rationale", "text", "message"]) {
        const value = content[key];
        if (key === headingKey || typeof value !== "string" || !value.trim()) continue;
        const block = classed("div", "case-context-narrative-block");
        block.append(classed("div", "case-context-field-label", humanizeCaseContextToken(key)));
        block.append(classed("div", "case-context-field-text", value));
        narrative.append(block);
      }
      if (record.entity_type === "source") appendSourceNarrative(narrative, content);
      if (narrative.children.length) card.append(narrative);

      const links = Array.isArray(content.links) ? content.links : [];
      const safeLinks = links.map((link) => ({ ...link, safeUrl: safeSourceUrl(link?.url) })).filter((link) => link.safeUrl);
      if (safeLinks.length) {
        const actions = classed("div", "case-context-source-links");
        for (const link of safeLinks) {
          const anchor = element("a", link.label || "Open source");
          anchor.href = link.safeUrl; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
          actions.append(anchor);
        }
        card.append(actions);
      }

      const technical = classed("details", "case-context-technical");
      technical.append(element("summary", "Record fields and provenance"));
      technical.append(structuredValue(content, "content"));
      const provenance = classed("dl", "case-context-field-grid provenance");
      provenance.append(element("dt", "Record ID"));
      const id = element("dd"); id.append(classed("code", "case-context-code", record.id)); provenance.append(id);
      if (record.provenance?.digest) {
        provenance.append(element("dt", "Evidence digest"));
        const digest = element("dd"); digest.append(classed("code", "case-context-code", record.provenance.digest)); provenance.append(digest);
      }
      technical.append(provenance);
      const raw = classed("details", "case-context-raw-disclosure");
      raw.append(element("summary", "Raw JSON"), classed("pre", "case-context-raw", JSON.stringify(content, null, 2)));
      technical.append(raw);
      card.append(technical);
    } else {
      card.append(classed("p", "case-context-truncated-copy", "This preview ends before the record can be formatted. Load complete records to read it."));
      card.append(classed("pre", "case-context-raw", record.text || "No preview available."));
    }
    return card;
  }
  async function request(path, body, method = "POST") {
    const response = await teamFetch(apiBase + path, body ? {
      method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    } : {});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || payload.message || `Request failed (${response.status})`);
    return payload;
  }
  function field(label, node) {
    const wrapper = classed("label", "case-context-field", label);
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
    const totalRecords = (context.sections || []).reduce((total, section) => total + Number(section.total || 0), 0);
    const overview = classed("header", "case-context-overview");
    const overviewCopy = classed("div", "case-context-overview-copy");
    overviewCopy.append(element("h3", context.view?.title || "Case context"));
    overviewCopy.append(classed(
      "p", "case-context-overview-meta",
      `${selectedSnapshot ? "Frozen snapshot" : draft ? "Draft preview" : "Live context"} · View version ${context.view?.version || 1} · Evidence revision ${context.revision}`,
    ));
    const overviewBadges = classed("div", "case-context-overview-badges");
    overviewBadges.append(pill(context.complete ? "Complete" : "Needs attention", context.complete ? "accepted" : "warning"));
    overviewBadges.append(pill(`${totalRecords} record${totalRecords === 1 ? "" : "s"}`, "neutral"));
    overview.append(overviewCopy, overviewBadges);
    output.append(overview);
    if (context.omissions?.length) {
      const warnings = classed("details", "case-context-warnings");
      warnings.open = true;
      warnings.append(element("summary", `${context.omissions.length} context warning${context.omissions.length === 1 ? "" : "s"}`));
      const list = element("ul");
      for (const warning of context.omissions) list.append(element("li", caseContextWarningText(warning)));
      warnings.append(list);
      output.append(warnings);
    }
    for (const section of context.sections || []) {
      const details = classed("details", "case-context-section");
      details.open = ["request", "decisions"].includes(section.name);
      const summary = classed("summary", "case-context-section-summary");
      const heading = classed("span", "case-context-section-heading");
      heading.append(element("strong", sectionLabel(section.name)));
      heading.append(element("small", SECTION_COPY[section.name]?.[1] || "Case evidence."));
      summary.append(heading, pill(`${section.total} record${section.total === 1 ? "" : "s"}`, "neutral"));
      details.append(summary);
      const records = classed("div", "case-context-record-list");
      const renderedRecords = new Map();
      for (const record of section.records) {
        const card = renderRecord(record);
        renderedRecords.set(`${record.entity_type}:${record.id}`, card);
        records.append(card);
      }
      if (!section.records.length) records.append(classed("p", "case-context-empty-section", "No evidence in this section."));
      details.append(records);
      let cursor = 0;
      const load = button("Load complete records", async () => {
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
          const key = `${record.entity_type}:${record.id}`;
          const complete = renderRecord(record, { complete: true });
          const existing = renderedRecords.get(key);
          if (existing) {
            existing.replaceChildren(...complete.children);
            existing.className = complete.className;
          } else {
            renderedRecords.set(key, complete);
            records.append(complete);
          }
        }
        cursor = data.next_cursor;
        if (cursor == null) load.remove();
        else load.textContent = "Load next record page";
      });
      const needsCompleteRecords = section.records.some((record) => record.truncated) || section.next_cursor != null;
      if (!draft && needsCompleteRecords) {
        const sectionActions = classed("div", "case-context-section-actions");
        sectionActions.append(load);
        details.append(sectionActions);
      }
      else if (draft && needsCompleteRecords) details.append(classed("p", "case-context-draft-note", "Save this recipe to browse complete evidence with these filters."));
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
    const dialogHeader = classed("header", "case-context-dialog-header");
    dialogHeader.append(element("h2", `Case context: ${item?.title || id}`), button("Close", () => { generation++; dialog.close(); }));
    const status = classed("p", "case-context-status", "Loading context…"); status.dataset.contextStatus = ""; status.setAttribute("role", "status");
    const output = classed("section", "case-context-output"); output.dataset.contextOutput = "";
    dialog.append(dialogHeader, status, output);
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
    const controls = classed("section", "case-context-controls");
    controls.append(field("View", select));
    const edit = classed("details", "case-context-configuration");
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
    const history = classed("details", "case-context-configuration");
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
    const actions = classed("div", "case-context-actions");
    actions.append(button("Freeze selected saved view", async () => {
      if (!canMutate("snapshot case context")) return;
      const view = savedView;
      if (!view) throw new Error("Load a saved view before freezing it.");
      const data = await request("/case-context/snapshots", { case_id: caseId, directory_path: directoryPath,
        view_id: view.view_id, view_version: view.version, expected_revision: revision, request_id: crypto.randomUUID() });
      selectedSnapshot = data.snapshot_id; render(data.context); revision = data.revision;
      message(`Frozen snapshot ${selectedSnapshot}`);
    }), button("Browse snapshots", async () => {
      const list = element("div");
      list.className = "case-context-snapshot-list";
      let cursor = 0;
      const more = button("More snapshots", loadSnapshots);
      async function loadSnapshots() {
        const data = await request(`/cases/${encodeURIComponent(caseId)}/context-snapshots?${new URLSearchParams({ directory_path: directoryPath, cursor: String(cursor) })}`);
        for (const snapshot of data.records) list.append(button(`${humanizeCaseContextToken(snapshot.view_id)} · ${formatTimestamp(snapshot.created_at)}`, async () => {
          const data = await request(`/case-context/snapshots/${encodeURIComponent(snapshot.id)}`);
          selectedSnapshot = snapshot.id; render(data.context); message(`Frozen snapshot ${snapshot.id}`);
        }));
        cursor = data.next_cursor;
        if (cursor !== null) list.append(more); else more.remove();
      }
      await loadSnapshots();
      controls.append(list);
    }));
    controls.append(edit, history, actions);
    dialog.insertBefore(controls, output);
    await update(); message("Context is source material, not execution authority.");
  }
  documentRoot.addEventListener("click", (event) => {
    const target = event.target?.closest?.("[data-open-case-context]");
    if (target) open(target.dataset.openCaseContext).catch((error) => message(error.message));
  });
  return { open };
}
