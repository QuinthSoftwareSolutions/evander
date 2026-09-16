import assert from "node:assert/strict";
import test from "node:test";
import { createCaseContextController } from "../../../src/evander_team_addon/ui_static/team-case-context.js";

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
