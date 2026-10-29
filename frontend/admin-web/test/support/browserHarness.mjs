import assert from "node:assert/strict";
import { after } from "node:test";
import { chromium } from "playwright";

export const browserTestTimeout = 60_000;
export const browserHookTimeout = 10_000;

export async function withTimeout(operation, timeout, message) {
  let timer;
  try {
    return await Promise.race([
      operation,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(message)), timeout);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

// Share the browser process only. Cookies, storage, routes, pages, and fixture state
// belong to a fresh context for every case, including cases after a failed test.
export function suiteBrowser() {
  let browserPromise;
  after(async () => {
    const browser = await browserPromise?.catch(() => null);
    await browser?.close();
  }, { timeout: browserHookTimeout });
  return {
    async newContext(t, options = {}) {
      browserPromise ??= chromium.launch({ headless: true, timeout: 15_000 });
      const browser = await browserPromise;
      const context = await browser.newContext(options);
      t.after(() => context.close(), { timeout: browserHookTimeout });
      context.setDefaultTimeout(10_000);
      context.setDefaultNavigationTimeout(15_000);
      return context;
    },
  };
}

export function listeningAddress(server) {
  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object" && "port" in address,
    "expected Vite to bind an ephemeral port");
  return address;
}
