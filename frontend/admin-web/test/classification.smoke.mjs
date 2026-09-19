import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";
import { createServer } from "vite";
import { fulfillAdminShellBackgroundApiRequest } from "./adminShellHelpers.mjs";

const questions = {route: {type: "choice", instructions: "Choose a team", criteria: {support: "Issues", sales: "Purchases"}}};
const profile = {label: "Triage", enabled: true, provider: "jev", model: "jev-1.13.0", questions};
const parent = {enabled: true, default_provider: "jev", providers: {jev: {enabled: true, default_model: "jev-1.13.0"}}, profiles: {triage: profile}};
function initialSnapshot() {return {revision: "r1", config: {}, parent_config: parent, effective_config: parent, providers: [{id: "jev", supported_question_types: ["choice", "score", "boolean"], credential_configured: true}]};}

async function browserFixture(t) {
  const server = await createServer({root: fileURLToPath(new URL("..", import.meta.url)), server: {host: "127.0.0.1", port: 0}, logLevel: "error",
    plugins: [{name: "classification-fixture", configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (request.url !== "/classification-fixture") return next();
        const html = await server.transformIndexHtml(request.url, `<html><body><div id="root"></div><script type="module">
          import React from 'react'; import {createRoot} from 'react-dom/client';
          import {ClassificationConfiguration} from '/src/features/classification/ClassificationConfiguration.tsx';
          import '/src/styles.css';
          function Fixture() {
            const [scope,setScope]=React.useState('root'); const [locked,setLocked]=React.useState(false);
            return React.createElement(React.Fragment,null,
              React.createElement('button',{onClick:()=>setScope(scope==='root'?'root/other':'root')},'Switch directory'),
              React.createElement('button',{onClick:()=>setLocked(!locked)},'Toggle lock'),
              React.createElement(ClassificationConfiguration,{key:scope,directoryPath:scope,disabled:locked,onDirtyChange:()=>{}}));
          }
          createRoot(document.getElementById('root')).render(React.createElement(Fixture));
        </script></body></html>`);
        response.setHeader("Content-Type", "text/html"); response.end(html);
      });
    }}]});
  await server.listen(); t.after(() => server.close());
  const browser = await chromium.launch({headless: true}); t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.setDefaultTimeout(7000);
  const errors = []; page.on("pageerror", e => errors.push(e.message));
  return {page, errors, baseUrl: `http://127.0.0.1:${server.httpServer.address().port}`};
}

test("classification profiles preserve failed edits, test samples, and honor inheritance and locking", {timeout: 60000}, async t => {
  const {page, errors, baseUrl} = await browserFixture(t);
  let snapshot = initialSnapshot(), reject = true;
  const saves = [], samples = [];
  await page.route("**/api/classification**", async route => {
    const request = route.request();
    if (request.method() === "GET") return route.fulfill({json: snapshot});
    const payload = request.postDataJSON();
    if (request.method() === "POST") {
      samples.push(payload);
      return route.fulfill({json: {answers: {route: {type: "choice", choice: "support", confidence: .5, probabilities: {support: .85, sales: .15}}}, usage: {input_tokens: 10}, duration_ms: 15}});
    }
    saves.push(payload);
    if (reject) {reject = false; return route.fulfill({status: 409, json: {error: "Classification configuration changed. Reload before saving."}});}
    snapshot = {...snapshot, revision: "r2", config: payload.config, effective_config: {...parent, ...payload.config, profiles: {...parent.profiles, ...payload.config.profiles}}};
    return route.fulfill({json: snapshot});
  });
  await page.goto(baseUrl + "/classification-fixture");
  await page.getByLabel("Profile", {exact: true}).selectOption("triage");
  await page.getByLabel("Instructions", {exact: true}).fill("Route by purchase intent");
  await page.getByRole("button", {name: "Add question", exact: true}).click();
  await page.getByLabel("Type", {exact: true}).nth(1).selectOption("boolean");
  await page.getByLabel("Question ID", {exact: true}).nth(1).fill("urgent");
  await page.getByLabel("Instructions", {exact: true}).nth(1).fill("Does this need immediate help?");
  await page.getByRole("button", {name: "Save classification", exact: true}).click();
  await page.getByRole("alert").filter({hasText: "configuration changed"}).waitFor();
  assert.equal(await page.getByLabel("Instructions", {exact: true}).first().inputValue(), "Route by purchase intent");
  await page.getByRole("button", {name: "Save classification", exact: true}).click();
  await page.getByRole("status").filter({hasText: "saved"}).waitFor();
  assert.equal(saves.at(-1).config.profiles.triage.questions.urgent.type, "boolean");
  await page.getByText("Test with sample input", {exact: true}).click();
  await page.getByLabel("Sample input", {exact: true}).fill("The export is broken");
  await page.getByRole("button", {name: "Test evaluation", exact: true}).click();
  await page.getByLabel("Evaluation result").waitFor();
  assert.equal(samples.length, 1);
  assert.equal(samples[0].questions.route.instructions, "Route by purchase intent");
  assert.equal(saves.length, 2);
  await page.getByRole("button", {name: "Toggle lock", exact: true}).click();
  assert.equal(await page.getByRole("button", {name: "Test evaluation", exact: true}).isDisabled(), true);
  await page.getByRole("button", {name: "Toggle lock", exact: true}).click();
  await page.getByRole("button", {name: "Delete local override / restore inherited profile", exact: true}).click();
  assert.equal(await page.getByLabel("Instructions", {exact: true}).inputValue(), "Choose a team");
  await page.getByRole("button", {name: "Advanced configuration JSON", exact: true}).click();
  await page.getByLabel("Local classification overrides").fill('{"profiles":{"bad":null}}');
  assert.equal(await page.getByRole("button", {name: "Save classification", exact: true}).isDisabled(), true);
  await page.getByRole("button", {name: "Revert all local overrides", exact: true}).click();
  await page.setViewportSize({width: 390, height: 844});
  await mkdir(new URL("../../../output/playwright/", import.meta.url), {recursive: true});
  await page.screenshot({path: fileURLToPath(new URL("../../../output/playwright/classification-mobile.png", import.meta.url)), fullPage: true});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  await page.getByRole("button", {name: "Switch directory", exact: true}).click();
  await page.getByLabel("Profile", {exact: true}).selectOption("triage");
  await page.getByText("Test with sample input", {exact: true}).click();
  assert.equal(await page.getByLabel("Sample input", {exact: true}).inputValue(), "");
  assert.deepEqual(errors, []);
});

test("workflow inspector saves inline questions and switches to a fixed profile without stale JSON", {timeout: 60000}, async t => {
  const {page, errors, baseUrl} = await browserFixture(t);
  const field = (key, kind) => ({key, label: key === "state" ? "State" : key, kind, required: key === "state", bindable: true, read_only: false, options: [], item_fields: []});
  let document = {revision: "rev1", config_path: "workflow.json", layout_path: "layout.json", config_exists: true, layout_exists: true,
    config: {triggers: [], gates: [], effects: [], queries: [{id: "classify", display_name: "Classify", type: "query:classification:evaluate", config: {state: "Example", questions}}], edges: []},
    layout: {nodes: {classify: {x: 160, y: 140}}}, graphs: [{id: "graph", name: "Classification", directory_path: "root", node_ids: ["classify"], user_ids: []}], output_schemas: {}, validation_diagnostics: []};
  const saves = [];
  let profileSnapshot = initialSnapshot();
  const auth = {auth_mode: "none", auth_required: false, authenticated: true, username: "admin", display_name: "Admin", permissions: ["admin.owner"], effective_permissions: ["admin.owner"], privileged_operations: {unlocked: true}, available_login_methods: [], can_manage_users: true};
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (await fulfillAdminShellBackgroundApiRequest(route, pathname)) return;
    let json = {};
    if (pathname === "/api/auth/session") json = auth;
    else if (pathname === "/api/admin/state") json = {...auth, selected_directory_path: "root", directory_scoped_config_enabled: true, execution_backend: "libvirt_vm", assistant: {enabled: false, show_launcher_when_disabled: false}};
    else if (pathname === "/api/admin/deployment-status") json = {active: false};
    else if (pathname === "/api/classification") json = profileSnapshot;
    else if (pathname === "/api/workflow-editor/catalog") json = {worker_id: "trigger-orchestrator", nodes: [{kind: "query", type: "query:classification:evaluate", label: "Classification", fields: [field("state", "json"), field("profile_id", "string"), field("questions", "json"), field("provider", "string"), field("model", "string")], input_ports: [], output_ports: []}]};
    else if (pathname === "/api/workflow-editor/document") {
      if (route.request().method() === "PUT") {const payload = route.request().postDataJSON(); saves.push(payload); document = {...document, ...payload, revision: `rev${saves.length + 1}`};}
      json = document;
    } else if (pathname === "/api/configuration/state") json = {selected_directory_path: "root", directory_scoped_config_enabled: true, selected_directory: {path: "root"}, tree: {ok: true, data: {directory: {id: "root", name: "root", path: "root", parent_id: null, metadata: {}}, config_domains: [], children: []}}};
    else if (pathname === "/api/workflow-editor/runs") json = {runs: [], node_activity: {}};
    else if (pathname === "/api/workflow-editor/runs/stream") return route.fulfill({contentType: "text/event-stream", body: ": stream\n\n"});
    return route.fulfill({json});
  });
  await page.goto(baseUrl + "/#workflow", {waitUntil: "domcontentloaded"});
  await page.locator('.react-flow__node[data-id="classify"]').click();
  const inspector = page.locator(".sidebar-section.inspector").filter({hasText: "query:classification:evaluate"});
  await inspector.getByLabel("Instructions", {exact: true}).fill("New workflow instructions");
  await page.getByRole("button", {name: "Save workflow", exact: true}).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  assert.equal(saves.length, 1);
  const savedNode = () => saves.at(-1).config.queries[0];
  assert.equal(savedNode().config.questions.route.instructions, "New workflow instructions");
  assert.ok(savedNode().outputs.answers.properties.route.properties.choice);
  await inspector.getByLabel("Classification profile", {exact: true}).selectOption("triage");
  await page.getByRole("button", {name: "Save workflow", exact: true}).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  assert.deepEqual(savedNode().config, {state: "Example", profile_id: "triage"});
  profileSnapshot = structuredClone(profileSnapshot);
  profileSnapshot.effective_config.profiles.triage.questions = {urgent: {type: "boolean", instructions: "Urgent?"}};
  await inspector.getByRole("button", {name: "Refresh answer suggestions", exact: true}).click();
  await inspector.getByText("classify.answers.urgent.probability_true", {exact: true}).waitFor();
  await page.getByRole("button", {name: "Save workflow", exact: true}).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  assert.ok(savedNode().outputs.answers.properties.urgent.properties.probability_true);
  assert.deepEqual(savedNode().config, {state: "Example", profile_id: "triage"});
  assert.deepEqual(errors, []);
});

test("Smart switch saves branches and cutoffs and tests samples without changing bound input", {timeout: 60000}, async t => {
  const {page, errors, baseUrl} = await browserFixture(t);
  const field = (key, kind) => ({key, label: key === "state" ? "State" : key, kind, required: key === "state", bindable: true, read_only: false, options: [], item_fields: []});
  let document = {revision: "rev1", config_path: "workflow.json", layout_path: "layout.json", config_exists: true, layout_exists: true,
    config: {triggers: [], gates: [{id: "classify", display_name: "Smart switch", type: "gate:classifier:switch", config: {state: "Example", instructions: "Route the request", cases: [{port: "support", label: "Support", description: "Issues"}], default_port: "standard"}}], effects: [], queries: [], edges: []},
    layout: {nodes: {classify: {x: 160, y: 140}}}, graphs: [{id: "graph", name: "Classification", directory_path: "root", node_ids: ["classify"], user_ids: []}], output_schemas: {}, validation_diagnostics: []};
  const saves = [], samples = [];
  let profileSnapshot = initialSnapshot();
  const auth = {auth_mode: "none", auth_required: false, authenticated: true, username: "admin", display_name: "Admin", permissions: ["admin.owner"], effective_permissions: ["admin.owner"], privileged_operations: {unlocked: true}, available_login_methods: [], can_manage_users: true};
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (await fulfillAdminShellBackgroundApiRequest(route, pathname)) return;
    let json = {};
    if (pathname === "/api/auth/session") json = auth;
    else if (pathname === "/api/admin/state") json = {...auth, selected_directory_path: "root", directory_scoped_config_enabled: true, execution_backend: "libvirt_vm", assistant: {enabled: false, show_launcher_when_disabled: false}};
    else if (pathname === "/api/admin/deployment-status") json = {active: false};
    else if (pathname === "/api/classification") json = profileSnapshot;
    else if (pathname === "/api/classification/switch/evaluate") {
      samples.push(route.request().postDataJSON());
      json = {selected_choice: "support", selected_port: "standard", routing_reason: "below_probability", answers: {route: {probabilities: {support: .85, standard: .1, astra: .05}}}, duration_ms: 10};
    }
    else if (pathname === "/api/workflow-editor/catalog") json = {worker_id: "trigger-orchestrator", nodes: [{kind: "gate", type: "gate:classifier:switch", label: "Smart switch", fields: [field("state", "json"), field("instructions", "string"), {...field("cases", "object_list"), item_fields: [field("port", "string"), field("label", "string"), field("description", "string")]}, field("default_port", "string"), field("default_description", "string"), field("provider", "string"), field("model", "string"), field("min_probability", "number")], input_ports: [], output_ports: []}]};
    else if (pathname === "/api/workflow-editor/document") {
      if (route.request().method() === "PUT") {const payload = route.request().postDataJSON(); saves.push(payload); document = {...document, ...payload, revision: `rev${saves.length + 1}`};}
      json = document;
    } else if (pathname === "/api/configuration/state") json = {selected_directory_path: "root", directory_scoped_config_enabled: true, selected_directory: {path: "root"}, tree: {ok: true, data: {directory: {id: "root", name: "root", path: "root", parent_id: null, metadata: {}}, config_domains: [], children: []}}};
    else if (pathname === "/api/workflow-editor/runs") json = {runs: [], node_activity: {}};
    else if (pathname === "/api/workflow-editor/runs/stream") return route.fulfill({contentType: "text/event-stream", body: ": stream\n\n"});
    return route.fulfill({json});
  });
  await page.goto(baseUrl + "/#workflow", {waitUntil: "domcontentloaded"});
  await page.locator('.react-flow__node[data-id="classify"]').click();
  const inspector = page.locator(".sidebar-section.inspector").filter({hasText: "gate:classifier:switch"});
  await inspector.getByLabel("Branch description", {exact: true}).waitFor();
  await inspector.getByLabel("Routing instructions", {exact: true}).fill("Select the explicit request");
  await inspector.getByLabel("Branch description", {exact: true}).waitFor();
  await inspector.getByLabel("Branch description", {exact: true}).fill("An existing product needs help");
  await inspector.getByRole("button", {name: "Add branch", exact: true}).click();
  await inspector.getByLabel("Branch port", {exact: true}).nth(1).fill("astra");
  await inspector.getByLabel("Branch label", {exact: true}).nth(1).fill("Level 4");
  await inspector.getByLabel("Branch description", {exact: true}).nth(1).fill("Requests level 4");
  await inspector.getByLabel("Use probability cutoff", {exact: true}).check();
  await inspector.getByLabel("Minimum option probability", {exact: true}).fill("0.9");
  await inspector.getByText("Test with sample input", {exact: true}).click();
  await inspector.getByLabel("Sample input", {exact: true}).fill("Help with export");
  await inspector.getByRole("button", {name: "Test evaluation", exact: true}).click();
  await inspector.getByRole("status").filter({hasText: "Selected branch: standard"}).waitFor();
  assert.equal(samples[0].config.state, "Help with export");
  assert.equal(samples[0].config.cases[1].port, "astra");
  assert.equal(samples[0].config.min_probability, .9);
  await page.getByRole("button", {name: "Save workflow", exact: true}).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  const savedNode = () => saves.at(-1).config.gates[0];
  assert.equal(savedNode().config.cases[0].description, "An existing product needs help");
  assert.equal(savedNode().config.instructions, "Select the explicit request");
  assert.equal(savedNode().config.state, "Example");
  await page.reload({waitUntil: "domcontentloaded"});
  await page.locator('.react-flow__node[data-id="classify"]').click();
  assert.equal(await inspector.getByLabel("Minimum option probability", {exact: true}).inputValue(), "0.9");
  assert.equal(await inspector.getByLabel("Branch port", {exact: true}).nth(1).inputValue(), "astra");
  await page.locator('.react-flow__node[data-id="classify"] [data-handleid="astra"]').waitFor();
  await inspector.getByLabel("Use probability cutoff", {exact: true}).uncheck();
  await page.getByRole("button", {name: "Save workflow", exact: true}).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  assert.ok(savedNode().config.min_probability == null);
  await mkdir(fileURLToPath(new URL("../../../output/playwright", import.meta.url)), {recursive: true});
  await inspector.getByLabel("Routing instructions", {exact: true}).scrollIntoViewIfNeeded();
  await page.screenshot({path: fileURLToPath(new URL("../../../output/playwright/smart-switch.png", import.meta.url))});
  assert.deepEqual(errors, []);
});
