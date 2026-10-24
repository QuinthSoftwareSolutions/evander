import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { browserHookTimeout, browserTestTimeout, suiteBrowser, withTimeout } from "./support/browserHarness.mjs";

const browser = suiteBrowser();

test("event definitions replace inherited schemas, preserve other definitions, and remove overrides", { timeout: browserTestTimeout }, async (t) => {
  const inherited = { event_type: "custom.answer", label: "Answer", source: "Platform", payload_schema: { type: "object", properties: { answer: { type: "string" } }, required: ["answer"] } };
  const unrelated = { label: "Other event", payload_schema: { type: "object" } };
  let snapshot = { revision: "events-1", config: { events: { "custom.other": unrelated } }, events: [inherited], inherited_events: [inherited] };
  const writes = [];
  const { page, errors } = await fixture(t, "EventsConfiguration", "configuration", async (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON(); writes.push(body);
      snapshot = { ...snapshot, revision: `events-${writes.length + 1}`, config: body.config };
    }
    await route.fulfill({ json: snapshot });
  });
  await page.getByLabel("Event", { exact: true }).selectOption("custom.answer");
  assert.equal(await page.getByRole("button", { name: "Override in this directory", exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "Unlock operations", exact: true }).click();
  await page.getByRole("button", { name: "Override in this directory", exact: true }).click();
  const schema = page.getByLabel("Payload JSON Schema", { exact: true });
  await schema.fill("{");
  assert.equal(await page.getByRole("button", { name: "Save events", exact: true }).isDisabled(), true);
  const replacement = { type: "object", properties: { count: { type: "integer" } }, required: ["count"] };
  await schema.fill(JSON.stringify(replacement));
  await page.getByRole("button", { name: "Save events", exact: true }).click();
  await page.getByText("Event definitions saved.", { exact: true }).waitFor();
  assert.equal(writes[0].expected_revision, "events-1");
  assert.deepEqual(writes[0].config.events["custom.answer"].payload_schema, replacement);
  assert.deepEqual(writes[0].config.events["custom.other"], unrelated);
  await page.getByRole("button", { name: "Remove local definition", exact: true }).click();
  assert.equal(await schema.isDisabled(), true);
  assert.deepEqual(JSON.parse(await schema.inputValue()), inherited.payload_schema);
  await page.getByRole("button", { name: "Save events", exact: true }).click();
  await page.getByText("Event definitions saved.", { exact: true }).waitFor();
  assert.equal(writes[1].expected_revision, "events-2");
  assert.deepEqual(writes[1].config.events, { "custom.other": unrelated });
  assert.deepEqual(errors, []);
});

test("event schema save conflicts retain the operator draft", { timeout: browserTestTimeout }, async (t) => {
  const definition = { label: "Answer", payload_schema: { type: "object" } };
  const { page, errors } = await fixture(t, "EventsConfiguration", "configuration", async (route) => {
    if (route.request().method() === "PUT") return route.fulfill({ status: 409, json: { error: "Event definitions changed. Reload before saving again." } });
    await route.fulfill({ json: { revision: "events-1", config: { events: { "custom.answer": definition } }, events: [], inherited_events: [] } });
  });
  await page.getByLabel("Event", { exact: true }).selectOption("custom.answer");
  await page.getByRole("button", { name: "Unlock operations", exact: true }).click();
  await page.getByLabel("Event label", { exact: true }).fill("Edited answer");
  await page.getByRole("button", { name: "Save events", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Event definitions changed" }).waitFor();
  assert.equal(await page.getByLabel("Event label", { exact: true }).inputValue(), "Edited answer");
  assert.equal(await page.getByRole("button", { name: "Save events", exact: true }).isDisabled(), false);
  assert.deepEqual(errors, []);
});

async function fixture(t, component, feature, routeHandler, hash = "") {
  const html = `<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module">
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import { AdminUiContext } from '/src/features/admin/AdminContext.tsx';
    import { ${component} } from '/src/features/${feature}/${component}.tsx';
    import '/src/styles.css';
    document.documentElement.dataset.adminDesign = '';
    function Fixture() {
      const [unlocked, setUnlocked] = React.useState(false);
      const context = { authMode: 'local_password', canManageUsers: true, currentUserId: 'operator',
        directoryScopedConfigEnabled: true, directoryTree: null, selectedDirectoryPath: 'root', privilegedUnlocked: unlocked };
      return React.createElement(React.Fragment, null,
        React.createElement('button', { onClick: () => setUnlocked(true) }, 'Unlock operations'),
        React.createElement(AdminUiContext.Provider, { value: context },
          React.createElement(${component}, { selectedDirectoryPath: 'root', directoryPath: 'root', disabled: !unlocked, onDirtyChange: () => {}, privilegedUnlocked: unlocked })));
    }
    createRoot(document.getElementById('root')).render(React.createElement(Fixture));
  </script></body></html>`;
  const server = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), logLevel: "error", server: { host: "127.0.0.1", port: 0 },
    plugins: [{ name: "configuration-ux-fixture", configureServer(vite) {
      vite.middlewares.use("/configuration-ux", async (_request, response) => {
        response.setHeader("Content-Type", "text/html");
        response.end(await vite.transformIndexHtml("/configuration-ux", html));
      });
    } }],
  });
  t.after(() => server.close(), { timeout: browserHookTimeout });
  await withTimeout(server.listen(), 15_000, "Configuration UX fixture did not start");
  const context = await browser.newContext(t);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", routeHandler);
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/configuration-ux${hash}`);
  return { page, errors };
}

function addon(id, enabled) {
  return { candidate_key: id, addon_id: id, display_name: `${id} extension`, source: `example_${id}`, source_type: "module",
    entry_point_name: null, enabled, loaded: enabled, configurable: true, load_error: null,
    has_admin_ui: false, has_admin_api: false, has_mcp_tools: false, has_connector_drivers: false, has_workflow_providers: false,
    workflow_node_types: [], referenced_workflow_node_types: [], required_restart_services: ["evander-admin-web"] };
}

function phoneRoutingState(maps, revision = "phone-1") {
  const snapshot = { key: "people_communications", revision, raw: { exists: true, config: { inbound_phone_caller_maps: maps } },
    effective: { config: { inbound_phone_caller_maps: maps }, payload: {}, validation_diagnostics: [], errors: [] },
    parent_defaults: { directory_path: null, config: {}, payload: {} } };
  return { directory_scoped_config_enabled: true, selected_directory_path: "root",
    selected_directory: { path: "root", parent_path: null, can_create: false, can_delete: false },
    tabs: [{ key: "people_communications", title: "People & Communications", underlying_domains: ["providers"],
      sections: [{ key: "phone_calls", title: "Phone calls", fields: [{ source_domain: "providers", field_name: "inbound_phone_caller_maps",
        label: "Caller routing", kind: "json", options: [], scope: "directory", root_starts_editable: true, tooltip: "Assign callers to agent sessions.",
        session_options: [{ session_id: "issue-worker", directory_path: "root/builds", name: "Issue worker" }],
        phone_route_options: [{ route_id: "shared-phone", did: "+15550000100", called_number: null, directory_path: "root" }] }] }] }],
    tab_snapshots: { people_communications: snapshot },
    directory_overrides: { field_count: 1, domain_count: 1, exists: true, overrides: {}, errors: [], payload: {} } };
}

test("phone caller map chooses an agent session, persists its scope, and keeps an empty DID enabled", { timeout: browserTestTimeout }, async (t) => {
  let maps = [];
  let revision = "phone-1";
  const writes = [];
  const { page, errors } = await fixture(t, "ConfigurationScreen", "configuration", async (route) => {
    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON();
      writes.push(payload);
      maps = payload.config.inbound_phone_caller_maps;
      revision = `phone-${writes.length + 1}`;
      return route.fulfill({ json: { write_result: { ok: true, errors: [] }, tab_snapshot: phoneRoutingState(maps, revision).tab_snapshots.people_communications } });
    }
    return route.fulfill({ json: phoneRoutingState(maps, revision) });
  }, "#configuration?config_tab=people_communications&config_section=phone_calls");
  const addNumber = page.getByRole("button", { name: "Add called number", exact: true });
  await addNumber.waitFor();
  assert.equal(await addNumber.isDisabled(), true);
  await page.getByRole("button", { name: "Unlock operations", exact: true }).click();
  await addNumber.click();
  await page.getByLabel("Phone route for called number 1", { exact: true }).selectOption({ label: "shared-phone — +15550000100 (root)" });
  await page.getByRole("button", { name: "Add caller", exact: true }).click();
  await page.getByLabel("Caller phone number 1 for map 1", { exact: true }).fill("+15550000201");
  await page.getByLabel("Agent session 1 for map 1", { exact: true }).selectOption({ label: "Issue worker — issue-worker (root/builds)" });
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.getByText("Saved People & Communications overrides for root.", { exact: true }).waitFor();
  assert.equal(writes[0].expected_revision, "phone-1");
  assert.deepEqual(writes[0].config.inbound_phone_caller_maps, [{ did: "+15550000100", route_id: "shared-phone", callers: [
    { caller_number: "+15550000201", session_id: "issue-worker", directory_path: "root/builds" },
  ] }]);
  await page.reload();
  await page.getByLabel("Caller phone number 1 for map 1", { exact: true }).waitFor();
  assert.equal(await page.getByLabel("Caller phone number 1 for map 1", { exact: true }).inputValue(), "+15550000201");
  await page.getByRole("button", { name: "Unlock operations", exact: true }).click();
  await page.getByRole("button", { name: "Remove caller", exact: true }).click();
  await page.getByText("No callers assigned. All calls to this number will be rejected.", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.getByText("Saved People & Communications overrides for root.", { exact: true }).waitFor();
  assert.equal(writes[1].expected_revision, "phone-2");
  assert.deepEqual(writes[1].config.inbound_phone_caller_maps, [{ did: "+15550000100", route_id: "shared-phone", callers: [] }]);
  assert.deepEqual(errors, []);
});

test("phone caller map preserves unavailable targets and retains a repaired draft after a revision conflict", { timeout: browserTestTimeout }, async (t) => {
  const maps = [{ did: "+15550000100", route_id: "shared-phone", callers: [
    { caller_number: "+15550000201", session_id: "issue-worker", directory_path: "root/old" },
  ] }];
  const writes = [];
  const { page, errors } = await fixture(t, "ConfigurationScreen", "configuration", async (route) => {
    if (route.request().method() === "PUT") {
      writes.push(route.request().postDataJSON());
      return route.fulfill({ status: 409, json: { error: "Revision changed" } });
    }
    return route.fulfill({ json: phoneRoutingState(maps) });
  }, "#configuration?config_tab=people_communications&config_section=phone_calls");
  const select = page.getByLabel("Agent session 1 for map 1", { exact: true });
  await select.waitFor();
  assert.equal(await select.locator("option:checked").innerText(), "Unavailable: issue-worker (root/old)");
  await page.getByRole("button", { name: "Unlock operations", exact: true }).click();
  await select.selectOption({ label: "Issue worker — issue-worker (root/builds)" });
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.getByText("This configuration tab changed on the server. Reload this tab before saving again; your draft is still visible.", { exact: true }).waitFor();
  assert.equal(writes[0].config.inbound_phone_caller_maps[0].callers[0].directory_path, "root/builds");
  assert.equal(await select.locator("option:checked").innerText(), "Issue worker — issue-worker (root/builds)");
  assert.deepEqual(errors, []);
});

test("selected add-on enablement preserves other drafts and saves with its revision", { timeout: browserTestTimeout }, async (t) => {
  let state = { addons: [addon("alpha", false), addon("beta", true)], explicit_enabled_addon_ids: ["beta"],
    loaded_addon_ids: ["beta"], revision: "addons-1", restart_required: false, required_restart_services: [],
    catalog_error: null, workflow_reference_error: null, config_mismatch: false };
  const writes = [];
  let finishLoading;
  const loadingGate = new Promise((resolve) => { finishLoading = resolve; });
  const { page, errors } = await fixture(t, "AddonsPage", "addons", async (route) => {
    const request = route.request();
    if (request.method() === "GET") await loadingGate;
    if (request.method() === "PUT") {
      writes.push(request.postDataJSON());
      state = { ...state, explicit_enabled_addon_ids: request.postDataJSON().enabled_addon_ids, revision: "addons-2", restart_required: true,
        required_restart_services: ["evander-admin-web"] };
    }
    await route.fulfill({ json: state });
  }, "#addons?addon=alpha");
  await page.getByRole("heading", { name: "Add-ons", exact: true }).waitFor();
  assert.equal(await page.getByText("Enablement is in sync with running services.", { exact: true }).count(), 0);
  finishLoading();
  const enableAlpha = page.getByRole("checkbox", { name: "Enable alpha extension", exact: true });
  await enableAlpha.waitFor();
  assert.equal(await enableAlpha.isDisabled(), true);
  await page.getByRole("button", { name: "Unlock operations", exact: true }).click();
  await enableAlpha.check();
  await page.locator(".addon-list-item").filter({ hasText: "beta extension" }).click();
  await page.getByRole("checkbox", { name: "Enable beta extension", exact: true }).uncheck();
  await page.locator(".addon-list-item").filter({ hasText: "alpha extension" }).click();
  assert.equal(await enableAlpha.isChecked(), true);
  await page.getByRole("button", { name: "Save enablement", exact: true }).click();
  await page.getByText("Saved add-on enablement.", { exact: false }).waitFor();
  assert.deepEqual(writes, [{ enabled_addon_ids: ["alpha"], expected_revision: "addons-1" }]);
  await page.getByRole("heading", { name: "Restart impact", exact: true }).waitFor();
  assert.deepEqual(errors, []);
});

function user(login, displayName) {
  return { login, display_name: displayName, contact_id: null, contact: null, local_password_enabled: true,
    group_assignments: [], workflow_grants: [], effective_permissions: [], created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z", last_login_at: null };
}

test("user search retains the selected draft and the single save includes access assignments", { timeout: browserTestTimeout }, async (t) => {
  let selected = user("alpha@example.test", "Alpha Operator");
  const other = user("beta@example.test", "Beta Operator");
  const writes = [];
  const { page, errors } = await fixture(t, "UsersPage", "users", async (route) => {
    const request = route.request();
    if (request.method() === "PATCH") {
      const data = request.postDataJSON();
      writes.push(data);
      selected = { ...selected, ...data };
      return route.fulfill({ json: { user: selected } });
    }
    return route.fulfill({ json: { directory_path: "root", directory_scoped_config_enabled: true, contacts: [],
      groups: [{ group_id: "reviewers", display_name: "Reviewers", is_required: false, permissions: ["workflows.read"] }],
      permission_catalog: ["workflows.read", "workflows.write"], users: [selected, other] } });
  }, "#users?login=alpha%40example.test");
  await page.getByLabel("Display name", { exact: true }).fill("Alpha Reviewer");
  await page.getByLabel("Find user", { exact: true }).fill("beta");
  assert.equal(await page.locator(".user-list-item").count(), 1);
  assert.match(await page.locator(".user-list-item").innerText(), /Beta Operator/);
  assert.equal(await page.getByLabel("Display name", { exact: true }).inputValue(), "Alpha Reviewer");
  await page.getByLabel("Find user", { exact: true }).fill("");
  await page.getByRole("button", { name: "Add group", exact: true }).click();
  await page.getByRole("button", { name: "Add grant", exact: true }).click();
  await page.getByLabel("Workflow permission", { exact: true }).selectOption("workflows.write");
  assert.equal(await page.getByRole("button", { name: "Save", exact: true }).count(), 1);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText("Saved alpha@example.test.", { exact: true }).waitFor();
  assert.deepEqual(writes, [{ login: "alpha@example.test", display_name: "Alpha Reviewer",
    group_assignments: [{ group_id: "reviewers", directory_path: "root" }],
    workflow_grants: [{ permission: "workflows.write", directory_path: "root" }] }]);
  assert.deepEqual(errors, []);
});

test("host health warnings and incomplete quarantine recovery stay visible with collapsed diagnostics", { timeout: browserTestTimeout }, async (t) => {
  const definition = { display_name: "Shared inbox", description: "Operator files", root: "/srv/example/inbox",
    capabilities: ["list", "import", "delete"], delete_requires_approval: true, disabled: false,
    max_import_bytes: 1048576, max_import_entries: 100, max_watch_entries: 1000 };
  let entries = [{ recovery_id: "recovery-1", original_path: "report.csv", kind: "file", size: 128,
    recovery_status: "missing_payload", restore_available: false, purge_available: true }];
  const writes = [];
  const { page, errors } = await fixture(t, "HostDirectoriesPage", "hostDirectories", async (route) => {
    const request = route.request();
    const endpoint = new URL(request.url());
    assert.equal(endpoint.searchParams.get("directory_path"), "root");
    if (request.method() === "DELETE") {
      writes.push(endpoint.pathname);
      entries = [];
      return route.fulfill({ json: {} });
    }
    if (endpoint.pathname.endsWith("/quarantine")) return route.fulfill({ json: { directory_path: "root", directory_id: "inbox", entries } });
    return route.fulfill({ json: { directory_path: "root", revision: "directories-1", config: { host_directories: { inbox: definition } },
      effective_config: { host_directories: { inbox: definition } }, provenance: {}, host_directories: [{ ...definition, directory_id: "inbox",
        defined_here: true, health: { available: false, status: "unavailable", error: "Host path requires attention." } }] } });
  }, "#host-directories?directory_id=inbox");
  await page.getByText("Host path requires attention.", { exact: true }).waitFor();
  const diagnostic = page.locator("details").filter({ has: page.getByText("Host path checks", { exact: false }) });
  assert.equal(await diagnostic.getAttribute("open"), null);
  await page.getByText("Recovery record needs attention:", { exact: false }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Restore", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "Purge permanently", exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "Unlock operations", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "Restore", exact: true }).isDisabled(), true);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Purge permanently", exact: true }).click();
  await page.getByText("Purged report.csv.", { exact: true }).waitFor();
  assert.deepEqual(writes, ["/api/admin/host-directories/inbox/quarantine/recovery-1/purge"]);
  assert.deepEqual(errors, []);
});

test("pricing rule search preserves incomplete patterns, blocks invalid saves, and edits its source record", { timeout: browserTestTimeout }, async (t) => {
  const rule = (id, model, input) => ({ id, provider: "openai", models: [model], model_patterns: [],
    effective_from: null, effective_to: null, currency: "usd", rates_per_1m_tokens: { input, cached_input: null, output: "3" },
    batch_multiplier: null, service_tier: null, long_context: null });
  let catalog = { version: "1", currency: "usd", description: null, sources: [],
    rules: [rule("zeta", "model-zeta", "1"), rule("alpha", "model-alpha", "2")] };
  let revision = "pricing-1";
  const writes = [];
  const status = () => ({ status: "ok", path: "/tmp/example-pricing.json", path_source: "default", env_var: "EVANDER_MODEL_PRICING_PATH",
    ...catalog, catalog, rule_count: catalog.rules.length, revision, editable: true, write_requires_privileged_unlock: true, error: null, error_class: null });
  const { page, errors } = await fixture(t, "ConfigurationScreen", "configuration", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/configuration/model-pricing") {
      if (request.method() === "PUT") {
        const payload = request.postDataJSON();
        writes.push(payload);
        catalog = payload.catalog;
        revision = "pricing-2";
      }
      return route.fulfill({ json: status() });
    }
    return route.fulfill({ json: { directory_scoped_config_enabled: true, selected_directory_path: "root",
      selected_directory: { path: "root", parent_path: null, can_create: false, can_delete: false },
      tabs: [{ key: "models_assistant_knowledge", title: "Models, Assistant & Knowledge", help_text: null, underlying_domains: [], sections: [] }],
      tab_snapshots: { models_assistant_knowledge: { key: "models_assistant_knowledge", revision: "config-1", raw: { exists: false, config: {} },
        effective: { config: {}, payload: {}, validation_diagnostics: [], errors: [] }, parent_defaults: { directory_path: null, config: {}, payload: {} } } },
      directory_overrides: { field_count: 0, domain_count: 0, exists: false, overrides: {}, errors: [], payload: {} } } });
  }, "#configuration?config_tab=models_assistant_knowledge&config_section=model-pricing");
  const search = page.getByRole("searchbox", { name: "Search pricing rules", exact: true });
  await search.waitFor();
  assert.equal(await page.getByLabel("Rule id", { exact: true }).count(), 0);
  await search.fill("model-alpha");
  await page.locator(".record-collection-summary").filter({ hasText: "alpha" }).click();
  await page.getByLabel("Input rate", { exact: true }).fill("9");
  await page.getByRole("button", { name: "Unlock operations", exact: true }).click();
  await page.getByRole("button", { name: "Add pattern", exact: true }).click();
  await page.getByRole("textbox", { name: /^Pattern/ }).fill("^model(");
  const save = page.getByRole("button", { name: "Save pricing", exact: true });
  assert.equal(await save.isDisabled(), true);
  await page.getByRole("alert").filter({ hasText: "Pricing rule alpha, model pattern 1" }).waitFor();
  await page.locator(".record-collection-summary").filter({ hasText: "alpha" }).click();
  assert.equal(await page.getByRole("textbox", { name: /^Pattern/ }).count(), 0);
  assert.equal(await save.isDisabled(), true);
  await search.fill("model-zeta");
  await page.locator(".record-collection-summary").filter({ hasText: "zeta" }).click();
  await page.getByRole("alert").filter({ hasText: "Pricing rule alpha, model pattern 1" }).waitFor();
  assert.equal(await save.isDisabled(), true);
  await search.fill("model-alpha");
  await page.locator(".record-collection-summary").filter({ hasText: "alpha" }).click();
  assert.equal(await page.getByRole("textbox", { name: /^Pattern/ }).inputValue(), "^model(");
  assert.equal(writes.length, 0);
  await page.getByRole("textbox", { name: /^Pattern/ }).fill("^model-alpha$");
  assert.equal(await save.isDisabled(), false);
  await page.getByRole("button", { name: "Duplicate", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('input[aria-label="Search pricing rules"]').value === "");
  assert.equal(await search.inputValue(), "");
  assert.equal(await page.getByLabel("Input rate", { exact: true }).inputValue(), "9");
  await page.getByLabel("Rule id", { exact: true }).fill("alpha-copy-custom");
  await page.getByRole("button", { name: "Save pricing", exact: true }).click();
  await page.getByText("Saved model pricing catalog.", { exact: true }).waitFor();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].expected_revision, "pricing-1");
  assert.deepEqual(writes[0].catalog.rules.map((entry) => [entry.id, entry.models, entry.rates_per_1m_tokens.input]), [
    ["zeta", ["model-zeta"], "1"], ["alpha", ["model-alpha"], "9"], ["alpha-copy-custom", ["model-alpha"], "9"],
  ]);
  assert.deepEqual(writes[0].catalog.rules.map((entry) => entry.model_patterns), [[], ["^model-alpha$"], ["^model-alpha$"]]);
  assert.deepEqual(errors, []);
});


test("Teams auth retains pending login across utility navigation and renders only public fields", { timeout: browserTestTimeout }, async (t) => {
  const secrets = ["private-device-value", "private-access-value", "private-refresh-value", "/private/auth/store"];
  const privateFields = { device_code: secrets[0], access_token: secrets[1], refresh_token: secrets[2], store_path: secrets[3] };
  let authState = "logged_out";
  let checks = 0;
  const completions = [];
  const state = phoneRoutingState([]);
  const { page, errors } = await fixture(t, "ConfigurationScreen", "configuration", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/teams-auth/status") return route.fulfill({ json: { state: authState, directory_path: "root", backend: "fixture", ...privateFields } });
    if (path === "/api/teams-auth/login") return route.fulfill({ json: { login_session: { login_session_id: "login-1", user_code: "ABCD-EFGH", verification_uri: "https://example.invalid/login", ...privateFields } } });
    if (path === "/api/teams-auth/complete") {
      completions.push(request.postDataJSON());
      checks += 1;
      if (checks === 1) return route.fulfill({ json: { state: "pending", message: "Awaiting fixture sign-in", ...privateFields } });
      authState = "authenticated";
      return route.fulfill({ json: { state: authState, ...privateFields } });
    }
    if (path === "/api/teams-auth/logout") {
      authState = "logged_out";
      return route.fulfill({ json: { state: authState, ...privateFields } });
    }
    if (path === "/api/configuration/model-pricing") return route.fulfill({ json: { status: "ok", catalog: { version: "1", currency: "usd", sources: [], rules: [] }, revision: "pricing-1", editable: false } });
    return route.fulfill({ json: state });
  }, "#configuration?config_tab=people_communications&config_section=teams-auth");
  const start = page.getByRole("button", { name: "Start sign-in", exact: true });
  await start.waitFor();
  assert.equal(await start.isDisabled(), true);
  await page.getByRole("button", { name: "Unlock operations", exact: true }).click();
  await start.click();
  await page.getByText("ABCD-EFGH", { exact: true }).waitFor();
  await page.evaluate(() => { window.location.hash = "#configuration?config_tab=people_communications&config_section=effective-config"; });
  await page.getByRole("heading", { name: "Effective configuration", exact: true, level: 3 }).waitFor();
  await page.evaluate(() => { window.location.hash = "#configuration?config_tab=people_communications&config_section=teams-auth"; });
  await page.getByText("ABCD-EFGH", { exact: true }).waitFor();
  const complete = page.getByRole("button", { name: "Check sign-in status", exact: true });
  await complete.click();
  await page.getByText("Awaiting fixture sign-in", { exact: true }).waitFor();
  assert.equal(await page.getByText("ABCD-EFGH", { exact: true }).count(), 1);
  await complete.click();
  await page.getByText("Teams sign-in completed.", { exact: true }).waitFor();
  assert.equal(await page.getByText("ABCD-EFGH", { exact: true }).count(), 0);
  assert.deepEqual(completions, [{ login_session_id: "login-1", timeout_seconds: 5 }, { login_session_id: "login-1", timeout_seconds: 5 }]);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.getByText("Teams auth signed out for root.", { exact: true }).waitFor();
  for (const value of secrets) assert.equal((await page.locator("body").textContent()).includes(value), false);
  assert.deepEqual(errors, []);
});
