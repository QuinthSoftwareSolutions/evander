import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer } from "vite";

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  };
}

function transcriptPayload(status, error = null) {
  return {
    thread_id: "thread-1",
    messages: [],
    assistant_capability_mode: "read-only",
    assistant_session: {
      status,
      error,
      updated_at: "2026-06-09T12:00:00Z",
    },
    updated_at: "2026-06-09T12:00:00Z",
  };
}

async function withChatLauncherHarness(run) {
  const projectRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const harnessHtml = `
    <!doctype html>
    <html>
      <head><title>Chat launcher smoke</title></head>
      <body>
        <div id="root"></div>
        <script type="module">
          import React from "react";
          import { createRoot } from "react-dom/client";
          import { ChatLauncher } from "/src/features/chat/ChatLauncher.tsx";
          import "/src/styles.css";

          createRoot(document.getElementById("root")).render(
            React.createElement(ChatLauncher, {
              enabled: true,
              assistantEnabled: true,
              showWhenDisabled: false,
            }),
          );
        </script>
      </body>
    </html>
  `;
  const server = await createServer({
    root: projectRoot,
    server: {
      host: "127.0.0.1",
      port: 0,
    },
    logLevel: "error",
    plugins: [
      {
        name: "chat-launcher-smoke-harness",
        configureServer(viteServer) {
          viteServer.middlewares.use("/chat-launcher-smoke.html", async (req, res, next) => {
            try {
              const transformed = await viteServer.transformIndexHtml(
                req.originalUrl || req.url || "/chat-launcher-smoke.html",
                harnessHtml,
              );
              res.statusCode = 200;
              res.setHeader("Content-Type", "text/html");
              res.end(transformed);
            } catch (error) {
              next(error);
            }
          });
        },
      },
    ],
  });
  await server.listen();
  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object" && "port" in address);
  const baseUrl = `http://127.0.0.1:${address.port}`;
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 390, height: 760 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  try {
    await run({ page, baseUrl });
  } finally {
    await browser.close();
    await server.close();
  }
}

test("chat launcher shows preparing then ready without blocking the pill", { timeout: 30000 }, async () => {
  await withChatLauncherHarness(async ({ page, baseUrl }) => {
    let releaseReadyStatus = () => {};
    const readyStatusAllowed = new Promise((resolve) => {
      releaseReadyStatus = resolve;
    });
    await page.route("**/api/chat/session", async (route) => {
      await route.fulfill(
        jsonResponse({
          user_id: "admin",
          thread_id: "thread-1",
          assistant_capability_mode: "read-only",
          assistant_session: {
            status: "preparing",
            error: null,
            updated_at: "2026-06-09T12:00:00Z",
          },
        }),
      );
    });
    await page.route("**/api/chat/transcript/thread-1", async (route) => {
      await route.fulfill(jsonResponse(transcriptPayload("preparing")));
    });
    await page.route("**/api/chat/stream/thread-1", async (route) => {
      await readyStatusAllowed;
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache" },
        body: [
          `event: snapshot\ndata: ${JSON.stringify(transcriptPayload("preparing"))}\n\n`,
          `event: snapshot\ndata: ${JSON.stringify(transcriptPayload("ready"))}\n\n`,
        ].join(""),
      });
    });

    await page.goto(`${baseUrl}/chat-launcher-smoke.html`);
    const pill = page.locator(".chat-pill");
    await pill.waitFor();
    assert.equal(await pill.isDisabled(), false);
    await page.waitForFunction(() => document.querySelector(".chat-pill")?.textContent?.includes("Preparing"));
    releaseReadyStatus();
    await page.waitForFunction(() => document.querySelector(".chat-pill")?.textContent?.includes("Ready"));
  });
});

test(
  "chat launcher exposes retry after prewarm failure while input remains enabled",
  { timeout: 30000 },
  async () => {
  await withChatLauncherHarness(async ({ page, baseUrl }) => {
    await page.route("**/api/chat/session", async (route) => {
      await route.fulfill(
        jsonResponse({
          user_id: "admin",
          thread_id: "thread-1",
          assistant_capability_mode: "read-only",
          assistant_session: {
            status: "preparing",
            error: null,
            updated_at: "2026-06-09T12:00:00Z",
          },
        }),
      );
    });
    await page.route("**/api/chat/transcript/thread-1", async (route) => {
      await route.fulfill(jsonResponse(transcriptPayload("failed", "vm offline")));
    });
    await page.route("**/api/chat/stream/thread-1", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache" },
        body: `event: snapshot\ndata: ${JSON.stringify(transcriptPayload("failed", "vm offline"))}\n\n`,
      });
    });
    await page.route("**/api/chat/session/prewarm", async (route) => {
      await route.fulfill(
        jsonResponse({
          user_id: "admin",
          thread_id: "thread-1",
          assistant_capability_mode: "read-only",
          assistant_session: {
            status: "preparing",
            error: null,
            updated_at: "2026-06-09T12:00:01Z",
          },
        }),
      );
    });

    await page.goto(`${baseUrl}/chat-launcher-smoke.html`);
    await page.waitForFunction(() => document.querySelector(".chat-pill")?.textContent?.includes("Failed"));
    await page.locator(".chat-pill").click();
    await page.locator(".chat-prewarm-warning").waitFor();
    await page.locator(".chat-prewarm-warning button", { hasText: "Retry" }).click();
    const textarea = page.locator("textarea[aria-label='Chat message']");
    await textarea.waitFor();
    assert.equal(await textarea.isDisabled(), false);
  });
  },
);
