import assert from "node:assert/strict";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.DEMO_PLAYWRIGHT_MODULE);
const { createRecorder } = await import(pathToFileURL(process.env.DEMO_CAPTURE_HELPER).href);
process.env.PLAYWRIGHT_MCP_EXECUTABLE_PATH = process.env.DEMO_CHROMIUM_EXECUTABLE || chromium.executablePath();
const directory = process.env.DEMO_REVIEW_OUTPUT;
const proxy = process.env.EVANDER_HTTP_TARGET_BROWSER_PROXY_URL;

delete process.env.EVANDER_HTTP_TARGET_BROWSER_PROXY_URL;
await assert.rejects(
  createRecorder({ chromium, jobDirectory: path.join(directory, "missing-proxy"), allowPublic: true }),
  /requires the session's approved target proxy/,
);
process.env.EVANDER_HTTP_TARGET_BROWSER_PROXY_URL = proxy;

const recorder = await createRecorder({ chromium, jobDirectory: directory, allowPublic: true });
try {
  let actions = 0;
  const scene = {
    id: "public", targetAlias: "approved-site", access: "public",
    setup: async page => { assert.equal((await page.goto("http://approved.example.test/")).status(), 200); },
    act: async page => { actions++; await page.getByRole("button", { name: "Show production details" }).click(); },
    verify: async page => { assert.equal(await page.locator("#planned").textContent(), "480 minutes"); },
  };
  const first = await recorder.recordScene(scene);
  assert.equal(first.access, "public");
  assert.equal((await recorder.recordScene(scene)).reused, true);
  assert.equal(actions, 1);
  await assert.rejects(recorder.recordScene({ ...scene, access: "managed" }), /Scene access changed/);

  for (const [id, url] of [
    ["unapproved", "http://unapproved.example.test/"],
    ["loopback", `${proxy}/not-approved`],
  ]) {
    await assert.rejects(recorder.recordScene({
      ...scene, id,
      setup: async page => {
        assert.equal((await page.goto(url)).status(), 403);
        throw new Error("Proxy denied this target");
      },
    }), /Proxy denied this target/);
  }
  await assert.rejects(recorder.recordScene({
    ...scene, id: "missing-login", access: "managed", targetAlias: "missing-login",
  }), /Managed website authentication unavailable/);
} finally {
  await recorder.close();
}
console.log(JSON.stringify({ success: true }));
