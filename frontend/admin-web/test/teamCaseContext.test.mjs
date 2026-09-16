import assert from "node:assert/strict";
import test from "node:test";
import {
  caseContextWarningText,
  createCaseContextController,
  humanizeCaseContextToken,
} from "../../../src/evander_team_addon/ui_static/team-case-context.js";

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.listeners = {};
    this.textContent = "";
  }
  append(...nodes) {
    for (const node of nodes) {
      node.parent = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }
  insertBefore(node, before) {
    node.parent = this;
    this.children.splice(this.children.indexOf(before), 0, node);
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this);
  }
  setAttribute() {}
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
    const doc = {
      body: new Element("body"), createElement: tag => new Element(tag), addEventListener() {},
    };
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
  const doc = {
    body: new Element("body"), createElement: tag => new Element(tag), addEventListener() {},
  };
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
  const doc = {
    body: new Element("body"), createElement: tag => new Element(tag), addEventListener() {},
  };
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
              summary: "The complete captured request and discussion.",
              effective_freshness_status: "current",
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
  assert.match(text, /The complete captured request and discussion/);
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
});
