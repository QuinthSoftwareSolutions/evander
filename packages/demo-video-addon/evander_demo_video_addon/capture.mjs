// Run in the isolated agent session. Uses the installed launch helper and its proxy/login policy.
import { mkdir, readFile, realpath, rename, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { performance } from "node:perf_hooks";
import { pathToFileURL } from "node:url";

const identifier = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/;
const targetIdentifier = /^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/;

async function save(file, value) {
  await writeFile(`${file}.tmp`, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  await rename(`${file}.tmp`, file);
}

async function startRecording(page, file) {
  if (typeof page.screencast?.start !== "function") {
    throw new Error("The seeded Playwright runtime must support page.screencast recording");
  }
  let firstFrame;
  let ready;
  const frameArrived = new Promise(resolve => { ready = resolve; });
  await page.screencast.start({
    path: file,
    size: { width: 1280, height: 720 },
    onFrame() {
      if (firstFrame === undefined) {
        firstFrame = performance.now();
        ready(firstFrame);
      }
    },
  });
  let timer;
  try {
    return await Promise.race([
      frameArrived,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("Recording produced no first frame within 10 seconds")), 10000);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export async function preflightCapture(chromium) {
  const helperPath = process.env.EVANDER_PLAYWRIGHT_LAUNCH_HELPER;
  if (!helperPath) throw new Error("Missing the seeded Playwright launch helper");
  const helper = await import(pathToFileURL(helperPath).href);
  if (typeof helper.launchSeededChromium !== "function" || typeof helper.createManagedWebsiteContext !== "function") {
    throw new Error("The seeded helper must support managed website contexts");
  }
  // An empty local page exercises the actual recording encoder without contacting an application.
  const directory = process.env.DEMO_VIDEO_PREFLIGHT_DIR;
  if (!directory) throw new Error("Set DEMO_VIDEO_PREFLIGHT_DIR to a disposable request directory");
  await mkdir(directory, { recursive: true });
  const browser = await helper.launchSeededChromium(chromium, { headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    try {
      const page = await context.newPage();
      await page.setContent("<p>Recording preflight</p>");
      const file = path.join(directory, "recording-preflight.webm");
      await startRecording(page, file);
      await page.screencast.stop();
      if ((await stat(file)).size < 100) throw new Error("The recording encoder produced no video");
      return { success: true, recording: file };
    } finally {
      await context.close();
    }
  } finally {
    await browser.close();
  }
}

export async function createRecorder({ chromium, jobDirectory, allowPublic = false }) {
  if (typeof allowPublic !== "boolean") throw new Error("allowPublic must be a boolean");
  if (allowPublic && !process.env.EVANDER_HTTP_TARGET_BROWSER_PROXY_URL) {
    throw new Error("Public recording requires the session's approved target proxy");
  }
  await mkdir(jobDirectory, { recursive: true });
  const root = await realpath(jobDirectory);
  const directory = path.join(root, "clips");
  await mkdir(directory, { recursive: true });
  if (await realpath(directory) !== directory) throw new Error("Recording directory cannot be a symlink");
  const helper = await import(pathToFileURL(process.env.EVANDER_PLAYWRIGHT_LAUNCH_HELPER).href);
  // Chromium otherwise bypasses proxies for loopback addresses. Public recordings must
  // route every network request through the session-scoped host proxy.
  const browser = await helper.launchSeededChromium(chromium, {
    headless: true,
    ...(allowPublic ? { args: ["--proxy-bypass-list=<-loopback>"] } : {}),
  });
  return {
    async recordScene({ id, targetAlias, setup, act, verify, access = "managed" }) {
      if (typeof id !== "string" || !identifier.test(id) || typeof targetAlias !== "string" || !targetIdentifier.test(targetAlias) || ![setup, act, verify].every(f => typeof f === "function")) {
        throw new Error("A scene needs a safe id, targetAlias, setup, act, and verify callbacks");
      }
      if (!["managed", "public"].includes(access) || (access === "public" && !allowPublic)) {
        throw new Error("Public recording must be explicitly enabled with the session proxy");
      }
      const receiptPath = path.join(directory, `${id}.json`);
      const clipPath = path.join(directory, `${id}.webm`);
      let previous;
      try { previous = JSON.parse(await readFile(receiptPath, "utf8")); }
      catch (error) { if (error.code !== "ENOENT") throw error; }
      if (previous) {
        if (previous.target_alias !== targetAlias) throw new Error("Scene target changed; use a fresh scene id");
        if ((previous.access ?? "managed") !== access) throw new Error("Scene access changed; use a fresh scene id");
        if (previous.state !== "completed") {
          throw new Error("This scene has an uncertain action outcome. Inspect the application before choosing a new scene id; it will not be replayed automatically.");
        }
        const content = await readFile(clipPath);
        if (createHash("sha256").update(content).digest("hex") !== previous.sha256) throw new Error("Completed recording has changed");
        return { ...previous, reused: true };
      }
      await save(receiptPath, { state: "recording", target_alias: targetAlias, access });
      let context;
      try {
        const options = { viewport: { width: 1280, height: 720 } };
        context = access === "managed"
          ? await helper.createManagedWebsiteContext(browser, targetAlias, options)
          : await browser.newContext(options);
        const page = await context.newPage();
        await setup(page);
        // Neither page creation nor encoder startup belongs to the recording clock.
        const epoch = await startRecording(page, clipPath);
        const markers = [];
        const mark = label => {
          const seconds = (performance.now() - epoch) / 1000;
          markers.push({ label, seconds });
          return seconds;
        };
        const start = mark("presentation-start");
        // Establish a readable starting frame before a fast click changes the page.
        // This is deliberate presentation time, not a wait for application readiness.
        await page.waitForTimeout(750);
        mark("action");
        await act(page, mark);
        await verify(page, mark);
        const result = mark("verified-result");
        // Keep a final frame for trimming/voiceover. This is presentation time, not a readiness wait.
        await page.waitForTimeout(750);
        const end = mark("end");
        await page.screencast.stop();
        await context.close();
        const receipt = {
          state: "completed", target_alias: targetAlias, access, clip: `clips/${id}.webm`,
          trim_start: start, trim_end: end, result_seconds: result, markers,
          sha256: createHash("sha256").update(await readFile(clipPath)).digest("hex"),
        };
        await save(receiptPath, receipt);
        return receipt;
      } catch (error) {
        await save(receiptPath, { state: "outcome_unknown", target_alias: targetAlias, access });
        throw error;
      } finally {
        await context?.close();
      }
    },
    async close() { await browser.close(); },
  };
}
