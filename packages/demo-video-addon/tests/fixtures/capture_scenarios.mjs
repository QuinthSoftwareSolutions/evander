import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.DEMO_PLAYWRIGHT_MODULE);
const { createRecorder, preflightCapture } = await import(pathToFileURL(process.env.DEMO_CAPTURE_HELPER).href);
const output = process.env.DEMO_REVIEW_OUTPUT;
const origin = process.env.DEMO_FIXTURE_ORIGIN;
const observations = {};
process.env.PLAYWRIGHT_MCP_EXECUTABLE_PATH = chromium.executablePath();
process.env.DEMO_VIDEO_PREFLIGHT_DIR = path.join(output, "preflight");
await mkdir(output, { recursive: true });
const preflight = await preflightCapture(chromium);
await writeFile(path.join(output, "preflight.json"), JSON.stringify(preflight));

async function scenario(name, steps) {
  const jobDirectory = path.join(output, name);
  const recorder = await createRecorder({ chromium, jobDirectory });
  const receipts = {};
  try {
    for (const step of steps) {
      receipts[step.id] = await recorder.recordScene({ targetAlias: "demo-admin", ...step });
    }
  } finally { await recorder.close(); }
  await writeFile(path.join(jobDirectory, "recorded.json"), JSON.stringify(receipts, null, 2));
}

await scenario("mixed", [
  { id: "starting-score", setup: page => page.goto(origin),
    act: async page => { await page.getByRole("button", { name: "Show production details" }).click(); },
    verify: async page => { assert.equal(await page.locator("#score").textContent(), "52.5%"); assert.equal(await page.locator("#planned").textContent(), "480 minutes"); } },
  { id: "exclude-breaks", setup: page => page.goto(origin),
    act: async page => { await page.getByLabel("Exclude planned breaks").check(); },
    verify: async page => { assert.equal(await page.locator("#score").textContent(), "60.0%"); observations.checkbox = await page.getByLabel("Exclude planned breaks").boundingBox(); } },
  { id: "see-details", setup: async page => { await page.goto(origin); await page.getByLabel("Exclude planned breaks").check(); },
    act: async page => { await page.getByRole("button", { name: "Show production details" }).click(); await page.locator("#details").evaluate(element => element.scrollIntoView({block: "center", behavior: "instant"})); },
    verify: async page => { assert.equal(await page.locator("#planned").textContent(), "420 minutes"); observations.details = await page.locator("#details").boundingBox(); } },
]);
await writeFile(path.join(output, "mixed", "observations.json"), JSON.stringify(observations));
await scenario("bugfix", [
  { id: "saved-note", setup: async page => { await page.goto(origin + "?mode=bugfix"); await page.getByLabel("Production note").fill("The morning run is complete."); },
    act: page => page.getByRole("button", { name: "Save note" }).click(),
    verify: async page => { assert.equal(await page.locator("#save-status").textContent(), "Saved"); assert.equal(await page.evaluate(() => localStorage.getItem("production-note")), "The morning run is complete."); } },
  { id: "changed-note", setup: async page => { await page.goto(origin + "?mode=bugfix"); await page.getByLabel("Production note").fill("The morning run is complete."); await page.getByRole("button", { name: "Save note" }).click(); },
    act: page => page.getByLabel("Production note").fill("The morning run is complete. Check the final pallet."),
    verify: async page => { assert.equal(await page.locator("#save-status").textContent(), "Unsaved changes"); assert.equal(await page.evaluate(() => localStorage.getItem("production-note")), "The morning run is complete."); } },
]);
await scenario("backend", [
  { id: "context", setup: async page => { await page.goto(origin); await page.getByLabel("Exclude planned breaks").check(); },
    act: async page => { await page.getByRole("button", { name: "Show production details" }).click(); },
    verify: async page => {
      assert.equal(await page.locator("#planned").textContent(), "420 minutes");
      const response = await page.request.get(origin + "/report-summary");
      assert.equal(response.status(), 200);
      const report = await response.json();
      assert.equal(report.effectiveness_percent, "60.0");
      assert.equal(report.planned_minutes, 420);
      await writeFile(path.join(output, "backend/report-response.json"), JSON.stringify(report, null, 2));
    } },
]);

// Recovery must not repeat completed or uncertain browser mutations.
const recorder = await createRecorder({ chromium, jobDirectory: path.join(output, "recovery") });
let issued = 0;
try {
  const first = { id: "complete", targetAlias: "demo-admin", setup: page => page.goto(origin), act: async () => { issued++; }, verify: async () => {} };
  await recorder.recordScene(first);
  assert.equal((await recorder.recordScene(first)).reused, true);
  assert.equal(issued, 1);
  const uncertain = { ...first, id: "uncertain", act: async () => { issued++; throw new Error("Synthetic failure after an action"); } };
  await assert.rejects(recorder.recordScene(uncertain), /Synthetic failure/);
  await assert.rejects(recorder.recordScene(uncertain), /uncertain action outcome/);
  assert.equal(issued, 2);
} finally { await recorder.close(); }
console.log(JSON.stringify({ success: true, recordings: 6, recovery_checks: 2 }));
