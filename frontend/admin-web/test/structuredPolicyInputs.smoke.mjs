import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer } from "vite";

async function withStructuredInputsHarness(run) {
  const projectRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const harnessHtml = `
    <!doctype html>
    <html>
      <head><title>Structured policy inputs smoke</title></head>
      <body>
        <div id="root"></div>
        <script type="module">
          import React, { useState } from "react";
          import { createRoot } from "react-dom/client";
          import {
            JsonObjectKeyValueEditor,
            RegexListEditor,
            StringListEditor,
            StringMapEditor,
          } from "/src/features/admin/StructuredPolicyInputs.tsx";

          function Harness() {
            const [strings, setStrings] = useState(["alpha"]);
            const [map, setMap] = useState({ label: "old" });
            const [json, setJson] = useState({ nullable: null });
            const [regexes, setRegexes] = useState(["^old$"]);

            return React.createElement("main", {},
              React.createElement("section", { "data-testid": "strings" },
                React.createElement("button", { type: "button", onClick: () => setStrings(["external"]) }, "Reset strings"),
                React.createElement(StringListEditor, {
                  values: strings,
                  onChange: setStrings,
                  itemLabel: "String",
                }),
                React.createElement("output", { "data-testid": "strings-output" }, JSON.stringify(strings)),
              ),
              React.createElement("section", { "data-testid": "map" },
                React.createElement("button", { type: "button", onClick: () => setMap({ label: "external" }) }, "Reset map"),
                React.createElement(StringMapEditor, {
                  value: map,
                  onChange: setMap,
                  fieldLabel: "Labels",
                }),
                React.createElement("output", { "data-testid": "map-output" }, JSON.stringify(map)),
              ),
              React.createElement("section", { "data-testid": "json" },
                React.createElement("button", { type: "button", onClick: () => setJson({ nullable: null }) }, "Reset json"),
                React.createElement(JsonObjectKeyValueEditor, {
                  value: json,
                  onChange: setJson,
                  fieldLabel: "Metadata",
                }),
                React.createElement("output", { "data-testid": "json-output" }, JSON.stringify(json)),
              ),
              React.createElement("section", { "data-testid": "regex" },
                React.createElement("button", { type: "button", onClick: () => setRegexes(["^external$"]) }, "Reset regex"),
                React.createElement(RegexListEditor, {
                  values: regexes,
                  onChange: setRegexes,
                  fieldLabel: "Patterns",
                }),
                React.createElement("output", { "data-testid": "regex-output" }, JSON.stringify(regexes)),
              ),
            );
          }

          createRoot(document.getElementById("root")).render(React.createElement(Harness));
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
        name: "structured-policy-inputs-smoke-harness",
        configureServer(viteServer) {
          viteServer.middlewares.use("/structured-policy-inputs-smoke.html", async (req, res, next) => {
            try {
              const transformed = await viteServer.transformIndexHtml(
                req.originalUrl || req.url || "/structured-policy-inputs-smoke.html",
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
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  try {
    await run({ page, baseUrl: `http://127.0.0.1:${address.port}` });
  } finally {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  }
}

async function replaceText(control, text) {
  await control.click();
  await control.press("Control+A");
  await control.pressSequentially(text);
}

async function assertStillFocused(control) {
  assert.equal(await control.evaluate((element) => document.activeElement === element), true);
}

async function waitForControlValue(control, expected) {
  const deadline = Date.now() + 5000;
  let actual = null;
  while (Date.now() < deadline) {
    actual = await control.inputValue().catch(() => null);
    if (actual === expected) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  assert.equal(actual, expected);
}

async function outputJson(page, testId) {
  return JSON.parse(await page.getByTestId(testId).textContent());
}

test("structured row editors keep focus across accepted controlled updates", { timeout: 30000 }, async () => {
  await withStructuredInputsHarness(async ({ page, baseUrl }) => {
    await page.goto(`${baseUrl}/structured-policy-inputs-smoke.html`);

    const stringInput = page.getByTestId("strings").locator("input").first();
    await replaceText(stringInput, "bravo");
    await assertStillFocused(stringInput);
    assert.deepEqual(await outputJson(page, "strings-output"), ["bravo"]);
    await page.getByRole("button", { name: "Reset strings" }).click();
    await waitForControlValue(stringInput, "external");

    const mapValueInput = page.getByTestId("map").locator("input").nth(1);
    await replaceText(mapValueInput, "new");
    await assertStillFocused(mapValueInput);
    assert.deepEqual(await outputJson(page, "map-output"), { label: "new" });
    await page.getByRole("button", { name: "Reset map" }).click();
    await waitForControlValue(mapValueInput, "external");

    const jsonValueInput = page.getByTestId("json").locator("textarea").first();
    assert.equal(await jsonValueInput.inputValue(), "null");
    await replaceText(jsonValueInput, "true");
    await assertStillFocused(jsonValueInput);
    assert.deepEqual(await outputJson(page, "json-output"), { nullable: true });
    await page.getByRole("button", { name: "Reset json" }).click();
    await waitForControlValue(jsonValueInput, "null");

    const regexInput = page.getByTestId("regex").locator("input").first();
    await replaceText(regexInput, "^new$");
    await assertStillFocused(regexInput);
    assert.deepEqual(await outputJson(page, "regex-output"), ["^new$"]);
    await page.getByRole("button", { name: "Reset regex" }).click();
    await waitForControlValue(regexInput, "^external$");
  });
});
