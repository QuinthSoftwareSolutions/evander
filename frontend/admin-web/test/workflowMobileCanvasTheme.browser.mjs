import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { createServer } from "vite";
import {
  browserHookTimeout, browserTestTimeout, suiteBrowser, withTimeout,
} from "./support/browserHarness.mjs";

const browser = suiteBrowser();

function maxRgbChannel(backgroundColor) {
  const channels = backgroundColor.match(/\d+(?:\.\d+)?/g)?.map(Number) ?? [];
  return Math.max(...channels.slice(0, 3));
}

test("compact workflow canvas keeps dark theme background", { timeout: browserTestTimeout }, async (t) => {
  const projectRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const harnessHtml = `
    <!doctype html>
    <html>
      <head>
        <title>Workflow canvas theme smoke</title>
        <script>document.documentElement.dataset.theme = "dark";</script>
      </head>
      <body>
        <div class="admin-workflow-panel">
          <div class="workflow-editor-shell">
            <div class="workflow-canvas-stage">
              <div class="canvas-shell"></div>
            </div>
          </div>
        </div>
        <script type="module">
          import "/src/styles.css";
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
        name: "workflow-mobile-canvas-theme-harness",
        configureServer(viteServer) {
          viteServer.middlewares.use("/workflow-mobile-canvas-theme.html", async (req, res, next) => {
            try {
              const transformed = await viteServer.transformIndexHtml(
                req.originalUrl || req.url || "/workflow-mobile-canvas-theme.html",
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
  t.after(() => server.close(), { timeout: browserHookTimeout });
  await withTimeout(server.listen(), 15_000, "Vite did not start within 15 seconds");

  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object" && "port" in address);

  const context = await browser.newContext(t, {
    viewport: { width: 390, height: 740 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();

  await page.goto(`http://127.0.0.1:${address.port}/workflow-mobile-canvas-theme.html`, {
    waitUntil: "domcontentloaded",
  });

  const canvasBackground = await page.locator(".canvas-shell").evaluate((element) => {
    return getComputedStyle(element).backgroundColor;
  });

  assert.ok(
    maxRgbChannel(canvasBackground) < 80,
    `expected compact workflow canvas to use a dark background, got ${canvasBackground}`,
  );
});
