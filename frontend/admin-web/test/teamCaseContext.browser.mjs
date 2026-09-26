import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createServer } from "vite";
import { browserHookTimeout, listeningAddress, suiteBrowser, withTimeout } from "./support/browserHarness.mjs";

const browser = suiteBrowser();
const projectRoot = fileURLToPath(new URL("..", import.meta.url));
const repositoryRoot = fileURLToPath(new URL("../../..", import.meta.url));
const addonBase = `/@fs/${repositoryRoot}src/evander_team_addon/ui_static`;

test("case context identifier badges copy complete values inside a native modal, including clipboard fallback", { timeout: 60_000 }, async t => {
  const sha = "0123456789abcdef0123456789abcdef01234567";
  const opaque = "case_0123456789abcdef0123456789abcdef";
  const uuid = "01901234-5678-7abc-8def-0123456789ab";
  const record = {
    id: opaque,
    title: "Verification evidence",
    description: `Candidate ${sha} completes the delivery for ${opaque}. Review context ${uuid} before approving it.`,
    candidate_commit_sha: sha,
    verification_id: uuid,
    status: "ready",
  };
  const view = {
    id: "root:case-overview:1", view_id: "case-overview", title: "Case overview",
    directory_path: "root", version: 1, sections: [{ name: "request" }], text_budget: 2000,
  };
  const fixture = {
    revision: 1, view, complete: true, omissions: [],
    sections: [{ name: "request", total: 1, next_cursor: null, records: [{
      entity_type: "case", id: opaque, content: record, truncated: false,
      provenance: { digest: sha },
    }] }],
  };
  const server = await createServer({
    root: projectRoot,
    server: { host: "127.0.0.1", port: 0, fs: { allow: [repositoryRoot] } },
    logLevel: "error",
    plugins: [{ name: "case-context-browser-fixture", configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (request.url !== "/case-context-browser-fixture") return next();
        const html = await server.transformIndexHtml(request.url, `<!doctype html>
          <html lang="en" data-admin-design data-addon="team"><head>
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>Case context identifier fixture</title>
          <style>@import url("${addonBase}/team-ui.css") layer(admin-addon-structure);</style>
          <link rel="stylesheet" href="/src/design/tokens.css">
          <link rel="stylesheet" href="/src/design/controls.css">
          <link rel="stylesheet" href="/src/design/addons.css">
          <link rel="stylesheet" href="/src/design/team.css">
          </head><body><button type="button" data-open-case-context="case">Open case context</button>
          <script type="module">
            import { createCaseContextController } from '${addonBase}/team-case-context.js';
            import { attachIdentifierCopy } from '${addonBase}/team-identifiers.js';
            attachIdentifierCopy(document);
            createCaseContextController({
              documentRoot: document, apiBase: '', canMutate: () => false,
              getState: () => ({ cases: { case: { title: 'Review delivery', project_id: 'project' } },
                projects: { project: { directory_path: 'root' } } }),
              teamFetch: async url => ({ ok: true, json: async () => url.startsWith('/case-context/views?')
                ? { revision: 1, views: [window.contextFixture.view], history: [] } : window.contextFixture }),
            });
            document.body.dataset.ready = 'true';
          </script></body></html>`);
        response.setHeader("Content-Type", "text/html");
        response.end(html);
      });
    } }],
  });
  t.after(() => server.close(), { timeout: browserHookTimeout });
  await withTimeout(server.listen(), 15_000, "Case context fixture did not start within 15 seconds");
  const { port } = listeningAddress(server);
  const context = await browser.newContext(t, { viewport: { width: 1440, height: 1000 } });
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const page = await context.newPage();
  page.setDefaultTimeout(5_000);
  await page.addInitScript(data => { window.contextFixture = data; }, fixture);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${port}/case-context-browser-fixture`, { waitUntil: "domcontentloaded" });
  await page.locator('body[data-ready="true"]').waitFor();
  await page.getByRole("button", { name: "Open case context", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("heading", { name: "Verification evidence", exact: true }).waitFor();
  assert.equal(await dialog.evaluate(element => element.matches(":modal")), true);

  const copySha = dialog.getByRole("button", { name: `Copy SHA: ${sha}`, exact: true }).first();
  assert.equal(await copySha.getAttribute("title"), `${sha}\nClick to copy`);
  await copySha.focus();
  await copySha.press("Enter");
  await page.waitForFunction(() => document.querySelector('.identifier-badge[data-copy-state="copied"]'));
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), sha);
  assert.equal(await copySha.innerText(), "Copied");

  for (const [mode, value] of [["denied", opaque], ["unavailable", uuid]]) {
    await page.evaluate(mode => {
      window.fallbackCopies = [];
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: mode === "denied"
        ? { writeText: async () => { throw new DOMException("Denied for regression test", "NotAllowedError"); } }
        : undefined });
      document.execCommand = command => {
        const field = document.activeElement;
        window.fallbackCopies.push({ command, value: field.value,
          selected: field.value.slice(field.selectionStart, field.selectionEnd),
          insideModal: Boolean(field.closest("dialog")?.matches(":modal")) });
        return true;
      };
    }, mode);
    const textareasBefore = await dialog.locator("textarea").count();
    const copyId = dialog.getByRole("button", { name: `Copy ID: ${value}`, exact: true }).first();
    await copyId.focus();
    await copyId.press("Enter");
    await page.waitForFunction(() => window.fallbackCopies.length === 1);
    assert.deepEqual(await page.evaluate(() => window.fallbackCopies), [{
      command: "copy", value, selected: value, insideModal: true,
    }], `${mode} clipboard must copy the full value from within the modal`);
    assert.equal(await copyId.innerText(), "Copied");
    assert.equal(await copyId.evaluate(element => document.activeElement === element), true,
      "fallback copy restores keyboard focus to the activated badge");
    assert.equal(await dialog.locator("textarea").count(), textareasBefore,
      "temporary copy fields are removed after copying");
  }

  await dialog.getByText("Record fields and provenance", { exact: true }).click();
  await dialog.getByText("Raw JSON", { exact: true }).click();
  const raw = dialog.locator(".case-context-raw-disclosure pre");
  assert.deepEqual(JSON.parse(await raw.innerText()), record);
  assert.equal(await raw.getByRole("button").count(), 0);
  await dialog.getByText("Record fields and provenance", { exact: true }).click();

  await page.waitForFunction(() => !document.querySelector(".identifier-badge[data-copy-state]"));
  await mkdir("/tmp/evander-ui-audit", { recursive: true });
  await page.screenshot({ path: "/tmp/evander-ui-audit/case-identifiers-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await dialog.locator(".case-context-record").scrollIntoViewIfNeeded();
  assert.equal(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1), true,
    "narrow modal keeps identifier badges and prose within its width");
  await page.screenshot({ path: "/tmp/evander-ui-audit/case-identifiers-narrow.png" });
  assert.deepEqual(errors, []);
});
