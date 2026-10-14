import assert from "node:assert/strict";
import test from "node:test";
import { browserHookTimeout, browserTestTimeout, suiteBrowser } from "./support/browserHarness.mjs";
import { websiteLoginFixture } from "./support/websiteLoginFixture.mjs";

const browser = suiteBrowser();

async function fixture(t, options) {
  const result = await websiteLoginFixture(options);
  t.after(() => result.server.close(), {timeout: browserHookTimeout});
  const context = await browser.newContext(t, {viewport: {width: 1280, height: 1000}});
  const page = await context.newPage();
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto(result.url);
  await page.getByRole("button", {name: "New website login", exact: true}).click();
  return {...result, page, errors};
}

async function customLogin(page) {
  await page.getByRole("radio", {name: /Custom login API/}).check();
  await page.getByLabel("Website address", {exact: true}).fill("https://portal.example.test/account");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByLabel("Login endpoint", {exact: true}).fill("https://identity.example.test/login");
  await page.getByLabel("Username", {exact: true}).fill("fixture-user");
  await page.getByLabel("Password", {exact: true}).fill("fixture-password");
}

test("guided login validates HTTPS, preserves a rejected save, and saves explicit mappings without exposing credentials", {timeout: browserTestTimeout}, async t => {
  const {page, state, errors} = await fixture(t, {failSave: true});
  assert.equal(await page.getByRole("button", {name: "Continue", exact: true}).isDisabled(), true);
  await page.getByRole("radio", {name: /Custom login API/}).check();
  await page.getByLabel("Website address", {exact: true}).fill("http://portal.example.test");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByRole("alert").filter({hasText: "HTTPS"}).waitFor();
  assert.equal(state.writes.length, 0);
  await customLogin(page);
  await page.getByText("The API uses different field names", {exact: true}).click();
  await page.getByLabel("Username field name", {exact: true}).fill("email");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByLabel("Value in login response", {exact: true}).fill("/session/access_token");
  await page.getByLabel("Browser storage key", {exact: true}).fill("sessionToken");
  await page.getByLabel("Keep this value in", {exact: true}).selectOption("session");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  assert.equal((await page.locator("body").innerText()).includes("fixture-password"), false);
  assert.equal(state.writes.length, 0);
  await page.getByRole("button", {name: "Save website login", exact: true}).click();
  await page.getByRole("alert").filter({hasText: "configuration changed"}).waitFor();
  await page.getByRole("button", {name: "Back", exact: true}).click();
  await page.getByRole("button", {name: "Back", exact: true}).click();
  assert.equal(await page.getByLabel("Password", {exact: true}).inputValue(), "fixture-password");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByRole("button", {name: "Save website login", exact: true}).click();
  await page.getByRole("heading", {name: "Website login saved", exact: true}).waitFor();
  const saved = state.writes.at(-1);
  assert.equal(saved.directory, "root/company");
  assert.equal(saved.body.expected_revision, "profile-1");
  assert.equal(saved.path, "/api/managed-website-sessions/portal.example.test");
  assert.equal(saved.body.profile.browser_origin, "https://portal.example.test");
  assert.deepEqual(saved.body.profile.credential_fields, {email: "email", password: "password"});
  assert.deepEqual(saved.body.credentials, {email: "fixture-user", password: "fixture-password"});
  assert.deepEqual(saved.body.profile.local_storage, {});
  assert.deepEqual(saved.body.profile.session_storage, {sessionToken: {response_pointer: "/session/access_token", encoding: "string"}});
  await page.getByRole("button", {name: "Test saved login", exact: true}).click();
  await page.getByRole("status").filter({hasText: "Login succeeded"}).waitFor();
  assert.equal(state.tests.length, 1);
  await page.getByRole("button", {name: "Edit advanced settings", exact: true}).click();
  assert.equal(await page.getByLabel("Value for password", {exact: true}).inputValue(), "");
  assert.deepEqual(errors, []);
});

test("WordPress setup derives subdirectory REST settings and checks configuration without asking for credentials", {timeout: browserTestTimeout}, async t => {
  const {page, state, errors} = await fixture(t, {sites: {
    main: {origin: "https://wordpress.example.test", rest_path: "/blog/index.php?rest_route=/", enabled: true, capabilities: ["cms", "browser"], recovery_verified: "fixture-stamp"},
    elsewhere: {origin: "https://another.example.test", enabled: true, capabilities: ["browser"]},
    content: {origin: "https://wordpress.example.test", enabled: true, capabilities: ["cms"]},
  }});
  await page.getByRole("radio", {name: /^WordPress/}).check();
  await page.getByLabel("Website address", {exact: true}).fill("https://wordpress.example.test/blog/");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByLabel("WordPress site", {exact: true}).selectOption("main");
  assert.equal(await page.getByRole("option", {name: "elsewhere", exact: true}).count(), 0);
  assert.equal(await page.getByRole("option", {name: "content", exact: true}).count(), 0);
  assert.equal(await page.locator('input[type="password"]').count(), 0);
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByRole("button", {name: "Save website login", exact: true}).click();
  await page.getByRole("heading", {name: "Website login saved", exact: true}).waitFor();
  const saved = state.writes[0].body;
  assert.equal(saved.profile.adapter, "wordpress");
  assert.deepEqual(saved.profile.adapter_config, {site_id: "main"});
  assert.equal(saved.profile.login_url, "https://wordpress.example.test/blog/index.php?rest_route=/evander/v1/browser/ticket");
  assert.equal(saved.profile.max_age_seconds, 900);
  assert.equal(saved.profile.credential_source, "environment");
  assert.deepEqual(saved.profile.credential_fields, {});
  assert.deepEqual(saved.credentials, {});
  assert.deepEqual(saved.profile.local_storage, {});
  assert.deepEqual(saved.profile.session_storage, {});
  assert.equal(state.tests.length, 0);
  await page.getByRole("button", {name: "Check WordPress configuration", exact: true}).click();
  await page.getByRole("status").filter({hasText: "adapter configuration verified"}).waitFor();
  assert.equal((await page.locator("body").innerText()).includes("Login succeeded"), false);
  await page.getByRole("button", {name: "New website login", exact: true}).click();
  assert.equal(await page.getByRole("button", {name: "Continue", exact: true}).isDisabled(), true);
  assert.equal(await page.getByRole("radio", {name: /^WordPress/}).isChecked(), false);
  assert.deepEqual(errors, []);
});

test("missing WordPress setup prevents saving, respects locking, and directory changes clear the draft", {timeout: browserTestTimeout}, async t => {
  const {page, state, errors} = await fixture(t, {unavailable: true});
  await page.getByRole("radio", {name: /^WordPress/}).check();
  await page.getByLabel("Website address", {exact: true}).fill("https://wordpress.example.test");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  const setup = page.getByRole("link", {name: /Open WordPress setup/});
  await setup.waitFor();
  assert.equal(await setup.getAttribute("href"), "/addons/wordpress-addon/?directory=root%2Fcompany");
  assert.equal(await page.getByRole("button", {name: "Continue", exact: true}).isDisabled(), true);
  assert.equal(state.writes.length, 0);
  state.unavailable = false;
  state.sites = {main: {origin: "https://wordpress.example.test", enabled: false, capabilities: ["browser"]}};
  await page.getByRole("button", {name: "Refresh WordPress connections", exact: true}).click();
  await page.getByLabel("WordPress site", {exact: true}).selectOption("main");
  await page.getByText(/This site still needs to be enabled/).waitFor();
  await page.getByRole("button", {name: "Toggle lock", exact: true}).click();
  assert.equal(await page.getByRole("button", {name: "Continue", exact: true}).isDisabled(), true);
  await page.getByRole("button", {name: "Switch directory", exact: true}).click();
  await page.locator(".website-login-wizard").waitFor({state: "detached"});
  assert.equal(state.writes.length, 0);
  assert.deepEqual(errors, []);
});

test("an incomplete WordPress login returns to the wizard with advanced settings intact", {timeout: browserTestTimeout}, async t => {
  const {page, state, errors} = await fixture(t, {sites: {
    main: {origin: "https://wordpress.example.test", enabled: true, capabilities: ["browser"], recovery_verified: "fixture-stamp"},
  }});
  await page.getByRole("radio", {name: /^WordPress/}).check();
  await page.getByLabel("Website address", {exact: true}).fill("https://wordpress.example.test");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByRole("button", {name: "Use advanced editor", exact: true}).click();
  await page.getByLabel("Profile ID", {exact: true}).fill("company-login");
  await page.getByLabel("Maximum age (seconds)", {exact: true}).fill("600");
  await page.getByLabel("Login timeout (seconds)", {exact: true}).fill("35");
  await page.getByRole("checkbox", {name: "Enabled", exact: true}).uncheck();
  await page.getByRole("button", {name: "Toggle lock", exact: true}).click();
  assert.equal(await page.getByRole("button", {name: "Return to WordPress wizard", exact: true}).isDisabled(), true);
  await page.getByRole("button", {name: "Toggle lock", exact: true}).click();
  await page.getByRole("button", {name: "Return to WordPress wizard", exact: true}).click();
  await page.getByRole("heading", {name: "WordPress connection", exact: true}).waitFor();
  assert.equal(state.writes.length, 0);
  await page.getByLabel("WordPress site", {exact: true}).selectOption("main");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByRole("button", {name: "Save website login", exact: true}).click();
  await page.getByRole("heading", {name: "Website login saved", exact: true}).waitFor();
  const saved = state.writes[0];
  assert.equal(saved.path, "/api/managed-website-sessions/company-login");
  assert.equal(saved.body.profile.enabled, false);
  assert.equal(saved.body.profile.max_age_seconds, 600);
  assert.equal(saved.body.profile.timeout_seconds, 35);
  assert.equal(saved.body.profile.login_url, "https://wordpress.example.test/wp-json/evander/v1/browser/ticket");
  assert.deepEqual(saved.body.profile.adapter_config, {site_id: "main"});
  assert.deepEqual(saved.body.credentials, {});
  assert.deepEqual(errors, []);
});

test("WordPress review resumes with its selected connection after repeated editor switches", {timeout: browserTestTimeout}, async t => {
  const {page, state, errors} = await fixture(t, {sites: {
    main: {origin: "https://wordpress.example.test", rest_path: "/blog/wp-json/", enabled: true, capabilities: ["browser"], recovery_verified: "fixture-stamp"},
  }});
  await page.getByRole("radio", {name: /^WordPress/}).check();
  await page.getByLabel("Website address", {exact: true}).fill("https://wordpress.example.test");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByLabel("WordPress site", {exact: true}).selectOption("main");
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByRole("button", {name: "Use advanced editor", exact: true}).click();
  await page.getByLabel("Maximum age (seconds)", {exact: true}).fill("720");
  await page.getByRole("button", {name: "Return to WordPress wizard", exact: true}).click();
  await page.getByRole("heading", {name: "Review & test", exact: true}).waitFor();
  await page.getByText("WordPress · main", {exact: true}).waitFor();
  await page.getByRole("button", {name: "Use advanced editor", exact: true}).click();
  assert.equal(await page.getByLabel("Maximum age (seconds)", {exact: true}).inputValue(), "720");
  await page.getByLabel("Renew before expiry (seconds)", {exact: true}).fill("45");
  await page.getByRole("button", {name: "Return to WordPress wizard", exact: true}).click();
  assert.equal(state.writes.length, 0);
  await page.getByRole("button", {name: "Save website login", exact: true}).click();
  await page.getByRole("heading", {name: "Website login saved", exact: true}).waitFor();
  assert.deepEqual(state.writes[0].body.profile.adapter_config, {site_id: "main"});
  assert.equal(state.writes[0].body.profile.login_url, "https://wordpress.example.test/blog/wp-json/evander/v1/browser/ticket");
  assert.equal(state.writes[0].body.profile.max_age_seconds, 720);
  assert.equal(state.writes[0].body.profile.renew_before_seconds, 45);
  assert.deepEqual(errors, []);
});

test("a second login starts a fresh wizard and cannot overwrite the first profile", {timeout: browserTestTimeout}, async t => {
  const {page, state, errors} = await fixture(t);
  await customLogin(page);
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByRole("button", {name: "Save website login", exact: true}).click();
  await page.getByRole("heading", {name: "Website login saved", exact: true}).waitFor();
  await page.getByRole("button", {name: "New website login", exact: true}).click();
  await customLogin(page);
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByRole("button", {name: "Continue", exact: true}).click();
  await page.getByText("Adjust profile name and session lifetime", {exact: true}).click();
  assert.equal(await page.getByLabel("Profile ID", {exact: true}).inputValue(), "portal.example.test-2");
  await page.getByLabel("Profile ID", {exact: true}).fill("portal.example.test");
  await page.getByRole("button", {name: "Save website login", exact: true}).click();
  await page.getByRole("alert").filter({hasText: "already in use"}).waitFor();
  assert.equal(state.writes.length, 1);
  await page.getByRole("button", {name: "Use advanced editor", exact: true}).click();
  assert.equal(await page.getByLabel("Login endpoint", {exact: true}).inputValue(), "https://identity.example.test/login");
  assert.equal(await page.getByLabel("Value for password", {exact: true}).inputValue(), "fixture-password");
  assert.deepEqual(errors, []);
});

for (const response of ["json", "cookies"]) {
  test("guided login saves " + response + " authentication and retains mappings in the advanced editor", {timeout: browserTestTimeout}, async t => {
    const {page, state, errors} = await fixture(t);
    await customLogin(page);
    await page.getByRole("button", {name: "Continue", exact: true}).click();
    await page.getByLabel("Login response contains", {exact: true}).selectOption(response);
    if (response === "json") {
      await page.getByLabel("Value in login response", {exact: true}).fill("/user");
    } else await page.getByLabel("Cookie list in response", {exact: true}).fill("/session/cookies");
    await page.getByRole("button", {name: "Continue", exact: true}).click();
    await page.getByRole("button", {name: "Save website login", exact: true}).click();
    await page.getByRole("heading", {name: "Website login saved", exact: true}).waitFor();
    const profile = state.writes[0].body.profile;
    if (response === "cookies") {
      assert.equal(profile.cookies_response_pointer, "/session/cookies");
      assert.deepEqual(profile.local_storage, {});
    } else assert.deepEqual(profile.local_storage, {currentUser: {response_pointer: "/user", encoding: "json"}});
    await page.getByRole("button", {name: "Edit advanced settings", exact: true}).click();
    assert.equal(await page.getByLabel("Login endpoint", {exact: true}).inputValue(), "https://identity.example.test/login");
    assert.deepEqual(errors, []);
  });
}
