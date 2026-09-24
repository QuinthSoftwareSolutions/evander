import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import test from "node:test";
import { chromium } from "playwright";
import { addonDesignResponses } from "./fixtures/adminAddonDesign.mjs";
import { fulfillAdminShellBackgroundApiRequest } from "./adminShellHelpers.mjs";

const repo = fileURLToPath(new URL("../../..", import.meta.url));
const session = {
  auth_mode: "none", auth_required: false, authenticated: true, username: "admin", display_name: "Admin",
  permissions: ["admin.owner"], effective_permissions: ["admin.owner"], can_manage_users: true,
  session_id: "fixture", available_login_methods: [], privileged_operations: { unlocked: true },
};

async function harness(t) {
  const server = spawn(`${repo}/.venv/bin/python`, [fileURLToPath(new URL("./fixtures/uiRolloutServer.py", import.meta.url))], {
    cwd: repo, env: { ...process.env, PYTHONPATH: `${repo}/src:${repo}/tests`, EVANDER_ADMIN_WEB_AUTH_MODE: "none" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  let errors = "";
  server.stdout.on("data", chunk => { output += chunk; });
  server.stderr.on("data", chunk => { errors += chunk; });
  t.after(async () => { if (server.exitCode === null) { server.kill(); await once(server, "exit"); } });
  let url;
  for (let attempt = 0; attempt < 100; attempt++) {
    url = output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];
    if (url && await fetch(`${url}/healthz`).then(response => response.ok).catch(() => false)) break;
    if (server.exitCode !== null) throw new Error(`Fixture stopped: ${errors}`);
    await delay(50);
  }
  assert.ok(url, `Fixture failed to start: ${errors}`);
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(() => localStorage.setItem("admin-web:density", "comfortable"));
  const writes = [];
  await context.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") writes.push(path);
    if (await fulfillAdminShellBackgroundApiRequest(route, path)) return;
    let data = addonDesignResponses[path] ?? {};
    if (path === "/api/auth/session") data = session;
    if (path === "/api/admin/state") data = {
      ...session, selected_directory_path: "root", directory_scoped_config_enabled: false, execution_backend: "libvirt_vm",
      assistant: { enabled: false, show_launcher_when_disabled: false },
      addons: [{ addon_id: "miro-addon", tab_key: "addon:miro-addon", label: "Miro", ui_path: "/addons/miro-addon/", api_base_path: "/api/addons/miro-addon" }],
    };
    if (path === "/api/configuration/state") data = { selected_directory_path: "root", selected_directory: { parent_path: null }, directory_scoped_config_enabled: false, tree: null };
    if (path === "/api/admin/deployment-status") data = { active: false };
    if (path === "/api/admin/skills") data = { items: [{ name: "alpha", size_bytes: 42, updated_at: "2026-09-23T10:00:00Z" }] };
    if (path === "/api/admin/skills/alpha") data = { skill: { name: "alpha", size_bytes: 42, updated_at: "2026-09-23T10:00:00Z", content: "# Alpha" } };
    await route.fulfill({ json: data });
  });
  return { url, context, writes };
}

function assetRequests(page) {
  const requests = [];
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (/\.(js|css)$/.test(path)) requests.push(path);
  });
  return requests;
}

test("UI switching preserves the route, warns before leaving drafts, remembers the choice, and loads one bundle", { timeout: 60000 }, async t => {
  const { url, context, writes } = await harness(t);
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  const requests = assetRequests(page);
  const pageErrors = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.goto(`${url}/?review=keep#skills?skill=alpha`);
  await page.locator(".skill-editor").waitFor();
  assert.equal(await page.getByLabel("UI version", { exact: true }).inputValue(), "classic");
  assert.ok(requests.some(path => path.startsWith("/classic/assets/")));
  assert.ok(!requests.some(path => path.startsWith("/assets/")), `Classic loaded New assets: ${requests}`);
  await page.locator(".skill-editor").fill("# Unsaved draft");
  page.once("dialog", async dialog => { assert.match(dialog.message(), /unsaved/i); await dialog.dismiss(); });
  await page.getByLabel("UI version", { exact: true }).selectOption("new");
  assert.equal(await page.locator(".skill-editor").inputValue(), "# Unsaved draft");
  assert.equal(new URL(page.url()).searchParams.get("admin_ui"), "classic");
  assert.equal((await context.cookies()).find(cookie => cookie.name === "admin_ui_version"), undefined);

  requests.length = 0;
  page.once("dialog", dialog => dialog.accept());
  await page.getByLabel("UI version", { exact: true }).selectOption("new");
  await page.waitForFunction(() => document.querySelector('script[data-admin-ui-version="new"]'));
  await page.locator(".skill-editor").waitFor();
  assert.equal(await page.getByLabel("Display density", { exact: true }).count(), 0);
  assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--ui-control-height").trim()), "32px");
  assert.equal(new URL(page.url()).hash, "#skills?skill=alpha");
  assert.equal(new URL(page.url()).searchParams.get("review"), "keep");
  assert.ok(requests.some(path => path.startsWith("/assets/")));
  assert.ok(!requests.some(path => path.startsWith("/classic/")), `New loaded Classic assets: ${requests}`);
  assert.equal((await context.cookies()).find(cookie => cookie.name === "admin_ui_version").value, "new");
  assert.equal(new URL(page.url()).searchParams.has("remember_admin_ui"), false);

  // Pages with their own unsaved-change guard can still veto the confirmed switch.
  await page.locator(".skill-editor").click();
  await page.evaluate(() => {
    window.testUnsavedGuard = event => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", window.testUnsavedGuard);
  });
  const dialogs = [];
  let veto;
  const vetoed = new Promise(resolve => {
    veto = async dialog => {
      dialogs.push(dialog.type());
      if (dialog.type() === "confirm") await dialog.accept();
      else { await dialog.dismiss(); resolve(); }
    };
    page.on("dialog", veto);
  });
  await Promise.all([page.getByLabel("UI version", { exact: true }).selectOption("classic"), vetoed]);
  assert.deepEqual(dialogs, ["confirm", "beforeunload"]);
  assert.equal(new URL(page.url()).searchParams.get("admin_ui"), "new");
  assert.equal((await context.cookies()).find(cookie => cookie.name === "admin_ui_version").value, "new");
  page.off("dialog", veto);
  await page.evaluate(() => window.removeEventListener("beforeunload", window.testUnsavedGuard));
  await page.evaluate(() => { location.hash = "#skills"; });
  await page.getByRole("heading", { name: "Select a skill to view details", exact: true }).waitFor();

  const other = await context.newPage();
  await other.goto(`${url}/#skills`);
  assert.equal(await other.getByLabel("UI version", { exact: true }).inputValue(), "new");
  await other.goto(`${url}/?admin_ui=classic#skills`);
  await other.locator(".skill-editor").waitFor();
  assert.equal(await other.getByLabel("UI version", { exact: true }).inputValue(), "classic");
  await page.reload();
  assert.equal(await page.getByLabel("UI version", { exact: true }).inputValue(), "new");
  await other.reload();
  assert.equal(await other.getByLabel("UI version", { exact: true }).inputValue(), "classic");
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.getByLabel("UI version", { exact: true }).isVisible(), true);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  }
  assert.deepEqual(writes, []);
  assert.deepEqual(pageErrors, []);
});

test("the standalone switcher can recover when the New UI application fails to load", { timeout: 30000 }, async t => {
  const { url, context } = await harness(t);
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  await page.route(url => url.pathname.startsWith("/assets/") && url.pathname.endsWith(".js"), route => route.abort());
  await page.goto(`${url}/?admin_ui=new#skills?skill=alpha`);
  await page.locator(".admin-ui-version-fallback").waitFor();
  page.once("dialog", dialog => dialog.accept());
  await page.getByLabel("UI version", { exact: true }).selectOption("classic");
  await page.locator(".skill-editor").waitFor();
  assert.equal(await page.getByLabel("UI version", { exact: true }).inputValue(), "classic");
  assert.equal(new URL(page.url()).hash, "#skills?skill=alpha");
  assert.equal(await page.locator(".admin-ui-version-fallback").count(), 0);
});

test("embedded add-ons follow their tab's UI version and load only matching assets", { timeout: 60000 }, async t => {
  const { url, context } = await harness(t);
  for (const version of ["classic", "new"]) {
    const page = await context.newPage();
    page.setDefaultTimeout(7000);
    const requests = assetRequests(page);
    await page.goto(`${url}/?admin_ui=${version}#addon:miro-addon`);
    const frame = page.frameLocator("iframe");
    await frame.locator("#connections button").first().waitFor();
    const source = await page.locator("iframe").getAttribute("src");
    assert.equal(source.includes("/classic/"), version === "classic");
    assert.match(source, new RegExp(`admin_ui=${version}`));
    const addonAssets = requests.filter(path => path.startsWith("/addons/miro-addon/"));
    assert.ok(addonAssets.some(path => path.endsWith("miro.js")));
    assert.ok(addonAssets.every(path => path.includes("/classic/") === (version === "classic")), addonAssets.join("\n"));
    assert.equal(await frame.locator("html").getAttribute("data-admin-design") !== null, version === "new");
    await page.close();
  }
});
