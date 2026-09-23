import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { chromium } from "playwright";
import { addonDesignResponses } from "./fixtures/adminAddonDesign.mjs";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
const packages = { bokio: "evander_bokio_addon", "mssql-seed": "evander_mssql_seed_addon", miro: "evander_miro_addon", figma: "evander_figma_addon", skatteverket: "evander_skatteverket_addon" };

test("standalone add-ons share host appearance, preserve fields through locking, and fit narrow screens", { timeout: 90000 }, async t => {
  const writes = [];
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (req.method !== "GET") { writes.push(url.pathname); res.writeHead(405); res.end('{}'); return; }
    if (addonDesignResponses[url.pathname]) { res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(addonDesignResponses[url.pathname])); return; }
    if (url.pathname === "/") {
      const addon = url.searchParams.get("addon");
      if (!packages[addon]) { res.writeHead(404); res.end(); return; }
      res.setHeader("Content-Type", "text/html");
      res.end(`<!doctype html><title>Add-on context fixture</title><style>body{margin:0}iframe{border:0;width:100%;height:100dvh;display:block}</style><iframe title="Add-on" src="/addons/${addon}-addon/ui/index.html"></iframe><script>
        window.context={theme:'light',privileged_operations:{unlocked:false}};
        window.publish=()=>{const frame=document.querySelector('iframe');frame.contentWindow.postMessage({type:'admin-addon-context',...window.context},location.origin);frame.contentWindow.postMessage({type:'admin-privileged-operations',...window.context},location.origin);};
        addEventListener('message',event=>{if(event.origin===location.origin&&event.data?.type==='admin-addon-ready')window.publish();});
        document.querySelector('iframe').addEventListener('load',window.publish);
      </script>`);
      return;
    }
    const match = url.pathname.match(/^\/addons\/([a-z-]+)-addon\/ui\/([a-zA-Z0-9_.-]+)$/);
    if (match && packages[match[1]]) {
      try {
        const content = await readFile(path.join(repoRoot, "src", packages[match[1]], "ui_static", match[2]));
        res.setHeader("Content-Type", match[2].endsWith(".js") ? "text/javascript" : match[2].endsWith(".css") ? "text/css" : "text/html");
        res.end(content); return;
      } catch {}
    }
    res.writeHead(404); res.end('{}');
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const output = path.join(repoRoot, "output/playwright");
  await mkdir(output, { recursive: true });

  const cases = [
    { id: "bokio", ready: ".action-row", action: '#action-detail button', draft: null },
    { id: "mssql-seed", ready: "#sidecarsBody input", action: "#saveButton", draft: '#sidecarsBody input[placeholder="Optional"]' },
    { id: "miro", ready: "#connections button", action: '#settings button[type="submit"]', draft: "#name" },
    { id: "figma", ready: "#connections .card", action: '#connection-form button', draft: '#connection-form input[name="client_id"]' },
    { id: "skatteverket", ready: "#api-picker input", action: "#save-connection", draft: '#connection-form input[name="display_name"]' },
  ];
  for (const item of cases) await t.test(item.id, async () => {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addInitScript(() => localStorage.setItem("admin-web:density", "comfortable"));
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    try {
      await page.goto(`http://127.0.0.1:${server.address().port}/?addon=${item.id}`);
      const frame = page.frames().find(candidate => candidate !== page.mainFrame());
      await frame.locator(item.ready).first().waitFor({ state: "attached" });
      if (item.id === "miro") await frame.locator("#connections button").first().click();
      if (item.id === "figma") {
        await frame.getByText("Add or update a connection", { exact: true }).click();
        await frame.getByRole("button", { name: "Refresh history", exact: true }).click();
        await frame.getByText("update_design · uncertain", { exact: true }).waitFor();
      }
      if (item.id === "skatteverket") await frame.locator(".skv-connection-settings > summary").click();
      const action = frame.locator(item.action).first();
      await action.waitFor();
      assert.equal(await action.isDisabled(), true, `${item.id} must start locked`);
      await page.evaluate(() => { window.context.privileged_operations.unlocked = true; window.publish(); });
      await frame.waitForFunction(selector => !document.querySelector(selector)?.disabled, item.action);
      if (item.draft) await frame.locator(item.draft).fill("Review draft");
      await page.evaluate(() => { window.context.privileged_operations.unlocked = false; window.publish(); });
      await frame.waitForFunction(selector => document.querySelector(selector)?.disabled, item.action);
      if (item.draft) assert.equal(await frame.locator(item.draft).inputValue(), "Review draft", "locking preserves the draft");
      await page.evaluate(() => { window.context.privileged_operations.unlocked = true; window.publish(); });
      await frame.waitForFunction(selector => !document.querySelector(selector)?.disabled, item.action);
      if (item.draft) assert.equal(await frame.locator(item.draft).inputValue(), "Review draft", "unlocking preserves the draft");

      for (const theme of ["light", "dark"]) for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 900 });
        await page.evaluate(theme => { window.context.theme = theme; window.publish(); }, theme);
        await frame.waitForFunction(theme => document.documentElement.dataset.theme === theme, theme);
        assert.equal(await frame.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--ui-control-height").trim()), "32px");
        assert.equal(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, `${item.id} ${theme} ${width}px must fit the viewport`);
        await page.screenshot({ path: path.join(output, `rollout-addon-${item.id}-${theme}-${width}.png`) });
      }
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  });
  assert.deepEqual(writes, [], "appearance and lock changes must not submit records");
});
