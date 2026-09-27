import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

async function fixture(t, { permissions = ["privacy.read", "privacy.cases", "privacy.approve"], handleDetail } = {}) {
  const server = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    server: { host: "127.0.0.1", port: 0 }, logLevel: "error",
    plugins: [{ name: "privacy-evidence-fixture", configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (request.url !== "/privacy-fixture") return next();
        const html = await server.transformIndexHtml(request.url, `<html><body><div id="root"></div><script type="module">
          import React from 'react'; import { createRoot } from 'react-dom/client';
          import { PrivacyPage } from '/src/features/privacy/PrivacyPage.tsx';
          function Fixture() {
            const [scope, setScope] = React.useState('root');
            return React.createElement(React.Fragment, null,
              React.createElement('button', {onClick: () => setScope(scope === 'root' ? 'root/team' : 'root')}, 'Switch directory'),
              React.createElement(PrivacyPage, {selectedDirectoryPath: scope, privilegedUnlocked: false}));
          }
          createRoot(document.getElementById('root')).render(React.createElement(Fixture));
        </script></body></html>`);
        response.setHeader("Content-Type", "text/html"); response.end(html);
      });
    }}],
  });
  let browser;
  t.after(async () => { await browser?.close(); await server.close(); });
  await server.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.setDefaultTimeout(7000);
  const requests = [];
  await page.route("**/api/privacy**", async route => {
    const url = new URL(route.request().url());
    const scope = url.searchParams.get("directory_path");
    if (url.pathname.includes("/records/")) {
      const [, kind, id] = url.pathname.split("/records/")[1].match(/^([^/]+)\/(.+)$/);
      requests.push({ scope, kind, id });
      const detail = {
        id, scope, directory_path: scope, record_kind: kind, revision: "revision-1",
        controller_ref: "saved-controller", instruction_ref: "saved-instruction",
        verification_reference: "saved-verification", evidence_reference: "saved-evidence",
        reason: "saved-hold-reason", record_ids: ["selected-artifact"],
      };
      if (handleDetail) return handleDetail(route, detail);
      return route.fulfill({ json: detail });
    }
    const row = kind => [{ id: `${kind}-1`, scope, status: "open" }];
    return route.fulfill({ json: {
      directory_path: scope, revision: "revision-1", permissions,
      cases: row("case"), holds: row("hold"), incidents: row("incident"), followups: row("followup"),
    } });
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/privacy-fixture`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "Inspect stored evidence", exact: true }).waitFor();
  return { page, requests };
}

test("privacy evidence can be reopened while locked for every supported record type", { timeout: 30000 }, async t => {
  const { page, requests } = await fixture(t);
  for (const kind of ["case", "hold", "incident", "followup"]) {
    await page.locator("summary").filter({ hasText: `${kind}-1` }).click();
    await page.getByRole("button", { name: `Inspect evidence: ${kind}-1`, exact: true }).click();
    const detail = page.getByRole("region", { name: "Stored privacy evidence", exact: true });
    await detail.getByRole("heading", { name: `Stored evidence: ${kind}-1` }).waitFor();
    assert.match(await detail.innerText(), kind === "hold" ? /selected-artifact/ : /saved-evidence/);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Inspect stored evidence", exact: true }).waitFor();
  }
  assert.deepEqual(requests.map(r => r.kind), ["case", "hold", "incident", "followup"]);
  assert.ok(requests.every(r => r.scope === "root"));
});

test("privacy metadata permission does not enable evidence reads", { timeout: 30000 }, async t => {
  const { page, requests } = await fixture(t, { permissions: ["privacy.read"] });
  await page.getByLabel("Evidence record ID", { exact: true }).fill("case-1");
  assert.equal(await page.getByRole("button", { name: "Read stored evidence", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: /^Inspect evidence:/ }).count(), 0);
  assert.deepEqual(requests, []);
});

test("privacy evidence rejects scope, identity and revision mismatches", { timeout: 30000 }, async t => {
  const { page } = await fixture(t, { handleDetail: (route, detail) => {
    const changes = {
      "wrong-scope": { scope: "root/other" }, "wrong-id": { id: "other" },
      "wrong-kind": { record_kind: "incident" }, "stale": { revision: "old-revision" },
    };
    return route.fulfill({ json: { ...detail, ...changes[detail.id] } });
  } });
  for (const id of ["wrong-scope", "wrong-id", "wrong-kind", "stale"]) {
    await page.getByLabel("Evidence record ID", { exact: true }).fill(id);
    await page.getByRole("button", { name: "Read stored evidence", exact: true }).click();
    await page.getByRole("alert").waitFor();
    assert.equal(await page.getByText(/saved-evidence/).count(), 0);
    assert.equal(await page.getByRole("heading", { name: /^Stored evidence:/ }).count(), 0);
  }
});

test("privacy evidence discards late responses after a directory change", { timeout: 30000 }, async t => {
  let pending, result;
  const { page } = await fixture(t, { handleDetail: (route, detail) => { pending = route; result = detail; } });
  await page.getByLabel("Evidence record ID", { exact: true }).fill("case-1");
  const requested = page.waitForRequest(request => request.url().includes("/records/case/case-1"));
  await page.getByRole("button", { name: "Read stored evidence", exact: true }).click();
  await requested;
  const switched = page.waitForResponse(response => response.url().endsWith("directory_path=root%2Fteam"));
  await page.getByRole("button", { name: "Switch directory", exact: true }).click();
  await switched;
  await page.getByRole("heading", { name: "Inspect stored evidence", exact: true }).waitFor();
  assert.ok(pending);
  await pending.fulfill({ json: result });
  assert.equal(await page.getByRole("heading", { name: /^Stored evidence:/ }).count(), 0);
  assert.equal(await page.getByText(/saved-evidence/).count(), 0);
});
