import assert from "node:assert/strict";
import test from "node:test";

import { collectionsFixture } from "./fixtures/adminCollections.mjs";
import { suiteBrowser } from "./support/browserHarness.mjs";

const browser = suiteBrowser();

test("populated session rows stay readable and filter controls stay aligned", { timeout: 60000 }, async (t) => {
  const fixture = await collectionsFixture();
  t.after(() => fixture.server.close());
  const context = await browser.newContext(t, { viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const items = Array.from({ length: 35 }, (_, index) => ({
    session_id: `operations-document-review-session-${index}`,
    vm_id: `review-worker-${index}`, model: "review-model", directory_path: "root",
    created_at: "2026-09-25T10:00:00Z", expires_at: "2099-09-25T10:00:00Z",
    is_expired: false, metadata: {}, allowed_tools: [], approval_required_tools: [],
    container_lifecycle: { state: "stopped", last_signal_at: "2026-09-25T10:00:00Z" },
  }));
  await page.route("**/api/admin/sessions**", (route) => route.fulfill({ json:
    new URL(route.request().url()).pathname.endsWith("/catalog")
      ? { tool_options: [], model_options: [], model_provider_modes: [] }
      : { items, counts: { total: 35, active: 35, expired: 0 }, status_ids: { active: items.map(item => item.session_id), expired: [] } },
  }));
  await page.route("**/api/admin/tool-calls**", (route) => route.fulfill({ json: { items: [], counts: {} } }));

  await page.goto(`${fixture.url}/#sessions`);
  await page.locator(".session-list-item").last().waitFor({ state: "attached" });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const rows = await page.locator(".session-list-item").evaluateAll(elements => elements.map(element => {
      const rect = element.getBoundingClientRect();
      return { top: rect.top, bottom: rect.bottom, height: element.clientHeight, content: element.scrollHeight };
    }));
    for (let index = 0; index < rows.length; index++) {
      assert.ok(rows[index].content <= rows[index].height + 1, `row ${index} contains its text at ${width}px`);
      if (index) assert.ok(rows[index].top >= rows[index - 1].bottom - 1, "rows do not overlap");
    }
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no page overflow");
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${fixture.url}/#tool-calls`);
  const checkbox = page.getByRole("checkbox", { name: "Direct directory only" });
  await checkbox.waitFor();
  const control = await page.getByRole("combobox", { name: "Status", exact: true }).boundingBox();
  const check = await checkbox.boundingBox();
  assert.ok(Math.abs(check.y + check.height / 2 - control.y - control.height / 2) <= 3,
    "checkbox aligns with the adjacent filter input");

  await page.goto(`${fixture.url}/#configuration`);
  const search = page.getByPlaceholder("Find setting");
  await search.waitFor();
  assert.equal(await search.evaluate(element => getComputedStyle(element).borderTopWidth), "0px",
    "embedded search field does not draw a second input border");

  await page.getByRole("button", { name: "Find page", exact: true }).click();
  const pageSearch = page.getByRole("searchbox", { name: "Search pages", exact: true });
  await pageSearch.click();
  const inputBox = await pageSearch.boundingBox();
  const resultsBox = await page.getByRole("listbox", { name: "Admin pages" }).boundingBox();
  assert.ok(resultsBox.y >= inputBox.y + inputBox.height, "page results do not cover the search input");
  await pageSearch.press("ArrowUp");
  const selected = page.getByRole("listbox", { name: "Admin pages" }).getByRole("option", { selected: true });
  await selected.waitFor();
  const selectedBox = await selected.boundingBox();
  assert.ok(selectedBox.y >= resultsBox.y && selectedBox.y + selectedBox.height <= resultsBox.y + resultsBox.height + 1,
    "keyboard selection scrolls into the visible result list");
  await pageSearch.fill("Workers");
  await pageSearch.press("Enter");
  await page.waitForURL("**/#workers");
  assert.deepEqual(errors, []);
});
