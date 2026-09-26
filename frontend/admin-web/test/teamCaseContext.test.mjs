import assert from "node:assert/strict";
import test from "node:test";
import {
  caseContextWarningText,
  createCaseContextController,
  humanizeCaseContextToken,
} from "../../../src/evander_team_addon/ui_static/team-case-context.js";
import { attachIdentifierCopy } from "../../../src/evander_team_addon/ui_static/team-identifiers.js";

class Element {
  constructor(tag, ownerDocument) {
    this.tag = tag;
    this.tagName = tag.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.attributes = new Map();
    this.children = [];
    this.dataset = {};
    this.listeners = {};
    this.textContent = "";
  }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
  append(...nodes) {
    for (const node of nodes) {
      node.parent = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    this.textContent = "";
    this.append(...nodes);
  }
  insertBefore(node, before) {
    node.parent = this;
    this.children.splice(this.children.indexOf(before), 0, node);
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this);
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) {
    const key = name.startsWith("data-") ? name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase()) : null;
    return this.attributes.get(name) ?? (key ? this.dataset[key] : this[name]) ?? null;
  }
  contains(node) { return [...this.walk()].includes(node); }
  closest(selector) {
    const attribute = selector.match(/\[data-([a-z-]+)\]$/)?.[1];
    const key = attribute?.replace(/-([a-z])/g, (_, char) => char.toUpperCase());
    for (let node = this; node; node = node.parent) {
      if (key && key in node.dataset) return node;
    }
    return null;
  }
  showModal() {}
  close() {}
  addEventListener(type, handler) { this.listeners[type] = handler; }
  *walk() {
    yield this;
    for (const child of this.children) yield* child.walk();
  }
  querySelector(selector) {
    const key = {
      "[data-context-status]": "contextStatus",
      "[data-context-output]": "contextOutput",
    }[selector];
    return [...this.walk()].find(node => key ? key in node.dataset : node.tag === selector);
  }
}

function testDocument() {
  const listeners = new Map();
  const copied = [];
  const doc = {
    nodeType: 9,
    copied,
    contains: node => doc.body.contains(node),
    createElement: tag => new Element(tag, doc),
    createTextNode: text => { const node = new Element("#text", doc); node.textContent = text; return node; },
    addEventListener(type, handler) { listeners.set(type, [...(listeners.get(type) ?? []), handler]); },
    removeEventListener(type, handler) { listeners.set(type, (listeners.get(type) ?? []).filter(item => item !== handler)); },
    dispatchEvent(event) { for (const handler of listeners.get(event.type) ?? []) handler(event); },
    defaultView: { navigator: { clipboard: { writeText: async value => { copied.push(value); } } } },
  };
  doc.body = doc.createElement("body");
  return doc;
}

function recipe(viewId, version, section) {
  return {
    id: `root:${viewId}:${version}`, directory_path: "root", view_id: viewId,
    title: viewId, version, sections: [{ name: section }], text_budget: 1000,
  };
}

for (const historicalId of ["case-overview", "customer-handoff"]) {
  test(`freezing historical ${historicalId} uses the displayed saved recipe`, async () => {
    const historical = recipe(historicalId, 1, "request");
    const current = recipe("case-overview", 2, "decisions");
    const views = historicalId === current.view_id
      ? [current]
      : [current, recipe(historicalId, 2, "verification")];
    const history = [historical, ...views];
    const requests = [];
    const doc = testDocument();
    const context = view => ({ revision: 3, view, complete: true, sections: [], omissions: [] });
    const controller = createCaseContextController({
      documentRoot: doc,
      apiBase: "",
      getState: () => ({
        cases: { case: { title: "Case", project_id: "project" } },
        projects: { project: { directory_path: "root" } },
      }),
      canMutate: () => true,
      teamFetch: async (url, options) => {
        const body = options?.body && JSON.parse(options.body);
        requests.push({ url, body });
        let payload;
        if (url.startsWith("/case-context/views?")) {
          payload = { revision: 3, views, history };
        } else if (url.startsWith("/cases/case/context?")) {
          const params = new URL("http://test" + url).searchParams;
          payload = context(history.find(view => view.view_id === params.get("view_id")
            && view.version === Number(params.get("view_version"))));
        } else if (url === "/case-context/snapshots") {
          payload = {
            snapshot_id: "frozen", revision: 4,
            context: context(history.find(view => view.view_id === body.view_id
              && view.version === body.view_version)),
          };
        } else {
          throw new Error(`Unexpected request: ${url}`);
        }
        return { ok: true, json: async () => payload };
      },
    });
    async function click(label) {
      const button = [...doc.body.walk()].find(node => node.tag === "button" && node.textContent === label);
      assert.ok(button, label);
      button.listeners.click();
      await new Promise(setImmediate);
    }
    const lastFreeze = () => requests.filter(request => request.url === "/case-context/snapshots").at(-1).body;

    await controller.open("case");
    await click(`${historicalId} v1 (root)`);
    assert.deepEqual(JSON.parse(doc.body.querySelector("textarea").value).sections, historical.sections);
    await click("Freeze selected saved view");
    assert.equal(lastFreeze().view_id, historical.view_id);
    assert.equal(lastFreeze().view_version, 1);

    // Returning to the dropdown must replace the historical selection too.
    const select = doc.body.querySelector("select");
    select.value = current.id;
    select.listeners.change();
    await new Promise(setImmediate);
    await click("Freeze selected saved view");
    assert.equal(lastFreeze().view_id, current.view_id);
    assert.equal(lastFreeze().view_version, 2);
  });
}

function visibleText(root) {
  return [...root.walk()].map(node => node.textContent).filter(Boolean).join("\n");
}

test("case context presents evidence as readable records with plain-language warnings", async () => {
  const view = { ...recipe("case-overview", 3, "request"), title: "Case overview" };
  const doc = testDocument();
  const context = {
    revision: 42,
    view,
    complete: false,
    omissions: [{ kind: "stale", section: "sources", source_id: "ado-14760" }],
    sections: [{
      name: "request",
      total: 1,
      next_cursor: null,
      records: [{
        entity_type: "case",
        id: "case-14760",
        text: JSON.stringify({
          id: "case-14760",
          title: "Export pallets are routed incorrectly",
          status: "open",
          description: "Operators need a safe route when fullness is unknown.",
          created_at: "2026-09-15T08:30:00Z",
        }),
        truncated: false,
        provenance: { digest: "digest-case", created_at: "2026-09-15T08:30:00Z" },
      }],
    }],
  };
  const controller = createCaseContextController({
    documentRoot: doc,
    apiBase: "",
    getState: () => ({
      cases: { case: { title: "Routing incident", project_id: "project" } },
      projects: { project: { directory_path: "root" } },
    }),
    canMutate: () => true,
    teamFetch: async (url) => ({
      ok: true,
      json: async () => url.startsWith("/case-context/views?")
        ? { revision: 42, views: [view], history: [] }
        : context,
    }),
  });

  await controller.open("case");

  const text = visibleText(doc.body);
  assert.match(text, /Case context: Routing incident/);
  assert.match(text, /Case overview/);
  assert.match(text, /Live context · View version 3 · Evidence revision 42/);
  assert.match(text, /Needs Attention/);
  assert.match(text, /A captured source may be out of date \(ado-14760\)/);
  assert.match(text, /Request/);
  assert.match(text, /The case request, scope and current case details/);
  assert.match(text, /Export pallets are routed incorrectly/);
  assert.match(text, /Open/);
  assert.match(text, /Operators need a safe route when fullness is unknown/);
  assert.match(text, /Record fields and provenance/);
  assert.match(text, /Evidence digest/);
});

test("loading complete evidence replaces a shortened preview and keeps only safe source links", async () => {
  const view = { ...recipe("case-overview", 1, "sources"), title: "Case overview" };
  const doc = testDocument();
  const requests = [];
  const controller = createCaseContextController({
    documentRoot: doc,
    apiBase: "",
    getState: () => ({
      cases: { case: { title: "Source check", project_id: "project" } },
      projects: { project: { directory_path: "root" } },
    }),
    canMutate: () => true,
    teamFetch: async (url) => {
      requests.push(url);
      let payload;
      if (url.startsWith("/case-context/views?")) {
        payload = { revision: 7, views: [view], history: [] };
      } else if (url.startsWith("/cases/case/context/evidence?")) {
        payload = {
          records: [{
            entity_type: "source",
            id: "source-1",
            content: {
              id: "source-1",
              title: "Customer work item",
              provider_type: "azure_devops",
              source_type: "work_item",
              external_identifier: "14760",
              external_version: "9",
              fetched_at: "2026-09-15T08:30:00Z",
              freshness_status: "current",
              effective_freshness_status: "current",
              body: {
                content: "Operators need a safe route when pallet fullness is unknown.",
                format: "markdown",
                metadata: {},
              },
              acceptance_criteria: [{
                text: "Unknown fullness may proceed to pickup.",
                external_id: "criterion-1",
                status: "approved",
                metadata: {},
              }],
              discussion: [{
                body: "The customer confirmed the post-pickup hold behavior.",
                external_id: "comment-1",
                author: "Ada Operator",
                occurred_at: "2026-09-15T09:00:00Z",
                metadata: {},
              }],
              links: [
                { label: "Open work item", url: "https://dev.azure.com/example/item/14760" },
                { label: "Unsafe", url: "javascript:alert(1)" },
              ],
            },
            provenance: { digest: "digest-source" },
          }],
          next_cursor: null,
        };
      } else {
        payload = {
          revision: 7,
          view,
          complete: false,
          omissions: [{ kind: "truncated", section: "sources", id: "source-1" }],
          sections: [{
            name: "sources",
            total: 1,
            next_cursor: null,
            records: [{
              entity_type: "source",
              id: "source-1",
              text: "{\"title\":\"Customer work item\",\"summary\":",
              truncated: true,
              provenance: { digest: "digest-source" },
            }],
          }],
        };
      }
      return { ok: true, json: async () => payload };
    },
  });

  await controller.open("case");
  assert.match(visibleText(doc.body), /preview ends before the record can be formatted/i);
  const load = [...doc.body.walk()].find(node => node.tag === "button" && node.textContent === "Load complete records");
  assert.ok(load);
  load.listeners.click();
  await new Promise(setImmediate);

  const text = visibleText(doc.body);
  assert.match(text, /Customer work item/);
  assert.match(text, /Freshness: Current/);
  assert.match(text, /Operators need a safe route when pallet fullness is unknown/);
  assert.match(text, /Unknown fullness may proceed to pickup/);
  assert.match(text, /Approved/);
  assert.match(text, /Ada Operator/);
  assert.match(text, /The customer confirmed the post-pickup hold behavior/);
  assert.doesNotMatch(text, /preview ends before/);
  const links = [...doc.body.walk()].filter(node => node.tag === "a");
  assert.deepEqual(links.map(link => link.textContent), ["Open work item", "https://dev.azure.com/example/item/14760"]);
  assert.equal(requests.filter(url => url.startsWith("/cases/case/context/evidence?")).length, 1);
});

test("case context labels preserve common technical acronyms", () => {
  assert.equal(humanizeCaseContextToken("candidate_commit_sha"), "Candidate Commit SHA");
  assert.equal(humanizeCaseContextToken("source_evidence_snapshot_ids"), "Source Evidence Snapshot IDs");
  assert.equal(
    caseContextWarningText({ kind: "restricted", section: "verification" }),
    "Some verification evidence is outside your access scope.",
  );
  const artifactWarning = caseContextWarningText({
    kind: "artifact_omission",
    section: "plans",
    artifact_ref: "artifact-space:/teams/team/large-plan",
    detail: "document too large: report.md",
  });
  assert.equal(
    artifactWarning,
    "Some content from the artifact bundle was omitted while it was read (artifact-space:/teams/team/large-plan): document too large: report.md.",
  );
  assert.doesNotMatch(artifactWarning, /Load complete records/);
});


test("case context condenses identifiers across evidence and copies exact values without altering technical content", async t => {
  const sha = "0123456789abcdef0123456789abcdef01234567";
  const opaque = "case_0123456789abcdef0123456789abcdef";
  const uuid = "01901234-5678-7abc-8def-0123456789ab";
  const view = { ...recipe("case-overview", 1, "sources"), title: "Case overview" };
  const content = {
    id: opaque,
    title: `Delivery for ${opaque}`,
    description: `Candidate ${sha}; context ${uuid}.`,
    candidate_commit_sha: sha,
    branch_ref: `feature/review-${sha}`,
    body: {
      content: `Inspect ${opaque}. Literal <img src=x onerror=alert(1)>. URL https://example.test/${sha} and `
        + "`git show " + sha + "`.\n```sh\nprintf '" + opaque + "'\n```",
    },
    acceptance_criteria: [{ text: `Confirm ${uuid}.` }],
    discussion: [{ author: "Reviewer", body: `Reviewed ${sha}.` }],
    links: [{ label: `Open commit ${sha}`, url: `https://example.test/commit/${sha}` }],
  };
  const context = {
    revision: 1, view, complete: false,
    omissions: [{ kind: "stale", section: "sources", source_id: opaque }],
    sections: [{ name: "sources", total: 1, next_cursor: null, records: [{
      entity_type: "source", id: opaque, content, truncated: false,
      provenance: { digest: sha },
    }] }],
  };
  const doc = testDocument();
  t.after(attachIdentifierCopy(doc));
  const controller = createCaseContextController({
    documentRoot: doc, apiBase: "", canMutate: () => true,
    getState: () => ({ cases: { case: { title: `Review ${opaque}`, project_id: "project" } },
      projects: { project: { directory_path: "root" } } }),
    teamFetch: async url => ({ ok: true, json: async () => url.startsWith("/case-context/views?")
      ? { revision: 1, views: [view], history: [] } : context }),
  });
  await controller.open("case");

  const all = [...doc.body.walk()];
  const badgeValue = node => node.tag === "button" && [sha, opaque, uuid].find(value =>
    Object.values(node.dataset).includes(value));
  const badges = all.filter(badgeValue);
  assert.ok(badges.some(node => badgeValue(node) === sha));
  assert.ok(badges.some(node => badgeValue(node) === opaque));
  assert.ok(badges.some(node => badgeValue(node) === uuid));
  assert.ok([...all.find(node => node.tag === "h2").walk()].some(badgeValue));
  assert.ok([...all.find(node => node.tag === "h4").walk()].some(badgeValue));
  assert.ok([...all.find(node => node.className === "case-context-warnings").walk()].some(badgeValue));
  for (const badge of badges) {
    const value = badgeValue(badge);
    assert.ok(badge.title.includes(value), "hover must reveal the full identifier");
    doc.dispatchEvent({ type: "click", target: badge, preventDefault() {}, stopPropagation() {} });
    await new Promise(setImmediate);
    assert.equal(doc.copied.at(-1), value);
  }

  const raw = all.find(node => node.tag === "pre" && node.textContent === JSON.stringify(content, null, 2));
  assert.ok(raw, "raw JSON retains complete original values");
  assert.equal(raw.children.length, 0);
  const link = all.find(node => node.tag === "a" && node.href === content.links[0].url);
  assert.equal(link.textContent, content.links[0].label);
  assert.equal(link.children.length, 0, "copy controls must not be nested in links");
  assert.equal(all.some(node => node.tag === "img" || node.tag === "script"), false);
  const sourceBody = all.find(node => node.className === "case-context-field-text" && node.textContent.includes("Literal <img"));
  assert.ok(sourceBody.textContent.includes(`https://example.test/${sha}`));
  assert.ok(sourceBody.textContent.includes("`git show " + sha + "`"));
  assert.ok(sourceBody.textContent.includes("```sh\nprintf '" + opaque + "'\n```"));
  assert.equal([...sourceBody.walk()].filter(badgeValue).length, 1, "URLs and code must keep their original text");
});
