import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

test("shared agent creation selects a worker and clears worker-specific choices", { timeout: 45000 }, async (t) => {
  const server = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    server: { host: "127.0.0.1", port: 0 },
    logLevel: "error",
    plugins: [{
      name: "worker-create-fixture",
      configureServer(server) {
        server.middlewares.use(async (request, response, next) => {
          if (request.url !== "/worker-create-fixture") return next();
          const html = await server.transformIndexHtml(request.url, `
            <html><body><div id="root"></div><script type="module">
            import React from 'react';
            import { createRoot } from 'react-dom/client';
            import { AgentCreateDialog } from '/src/features/agents/AgentCreateDialog.tsx';
            import '/src/styles.css';
            window.created = [];
            createRoot(document.getElementById('root')).render(React.createElement(AgentCreateDialog, {
              directoryPath: 'root', closeOnSuccess: false, onClose: () => {},
              onCreate: (payload) => window.created.push(payload),
            }));
            </script></body></html>`);
          response.setHeader("Content-Type", "text/html");
          response.end(html);
        });
      },
    }],
  });
  await server.listen();
  t.after(() => server.close());
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const fastRequest = Promise.withResolvers();
  const allowFastResponse = Promise.withResolvers();
  let delayFastResponse = true;
  await page.route("**/api/admin/agents/create-options**", async (route) => {
    const worker = new URL(route.request().url()).searchParams.get("worker_id") || "default";
    if (worker === "fast" && delayFastResponse) {
      delayFastResponse = false;
      fastRequest.resolve();
      await allowFastResponse.promise;
    }
    await route.fulfill({ json: {
      directory_path: "root", backends: ["libvirt_vm", "container_worker_vm"],
      reasoning_effort_catalog: {
        harness_levels: { codex: ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"], claude: ["low", "medium", "high", "xhigh", "max"] },
        model_rules: [{ pattern: "^gpt-4o$", levels: [] }, { pattern: "^claude-sonnet-4-6$", levels: ["low", "medium", "high", "max"] }],
      },
      agent_providers: [
        { value: "codex", label: "Codex", execution_backends: ["libvirt_vm", "container_worker_vm"] },
        { value: "claude", label: "Claude", execution_backends: ["libvirt_vm", "container_worker_vm"] },
      ], model_provider_modes: [], communication_targets: [],
      email_mailboxes: [], connectors: [], host_directories: [],
      libvirt: { default_template: "ubuntu", templates: [] },
      container_worker: {
        workers: [{ worker_id: "fast", display_name: "Fast worker" }],
        selected_worker_id: worker === "default" ? null : worker,
        default_image: `${worker}:latest`,
        images: [{ alias: `${worker}-image`, image_ref: `${worker}:special` }],
        codex_auth_mode: "chatgpt_subscription", model_provider_mode: "openai_public",
        subscription_profiles: [{ profile_id: `${worker}-account`, display_name: `${worker} account`, enabled: true }],
      },
    } });
  });
  const address = server.httpServer.address();
  await page.goto(`http://127.0.0.1:${address.port}/worker-create-fixture`);
  await page.getByRole("combobox", { name: /^Backend/ }).selectOption("container_worker_vm");
  const profile = page.getByRole("combobox", { name: /^Subscription profile/ });
  const image = page.getByRole("combobox", { name: /^Container image selector/ });
  const worker = page.getByRole("combobox", { name: /^Worker/ });
  await profile.selectOption("default-account");
  await image.selectOption("default-image");
  await worker.selectOption("fast");
  await fastRequest.promise;
  assert.equal(await page.getByRole("button", { name: "Create", exact: true }).isDisabled(), true);
  assert.equal(await profile.inputValue(), "");
  assert.equal(await image.inputValue(), "");
  allowFastResponse.resolve();
  await profile.selectOption("fast-account");
  await image.selectOption("fast-image");
  assert.equal(await profile.locator('option[value="default-account"]').count(), 0);
  const model = page.getByRole("textbox", { name: /^Model/ });
  const effort = page.getByRole("combobox", { name: /^Reasoning effort/ });
  await effort.selectOption("ultra");
  await model.fill("gpt-4o");
  assert.equal(await effort.inputValue(), "ultra", "retain an unsupported saved choice so it can be cleared");
  assert.equal(await page.getByRole("alert").count(), 1);
  await effort.selectOption("");
  assert.equal(await effort.isDisabled(), true);
  await model.fill("custom-deployment");
  await effort.selectOption("low");
  await page.getByRole("textbox", { name: /^Session(?! TTL)/ }).fill("chosen");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.waitForFunction(() => window.created.length === 1);
  const selected = await page.evaluate(() => window.created[0]);
  assert.equal(selected.reasoning_effort, "low");
  assert.equal(selected.worker_id, "fast");
  assert.equal(selected.container_image, "fast-image");
  assert.equal(selected.subscription_profile_id, "fast-account");

  await page.getByRole("combobox", { name: /^Backend/ }).selectOption("container_worker_vm");
  await profile.selectOption("default-account");
  assert.equal(await worker.inputValue(), "");
  await page.getByRole("textbox", { name: /^Session(?! TTL)/ }).fill("default-agent");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.waitForFunction(() => window.created.length === 2);
  assert.equal(await page.evaluate(() => Object.hasOwn(window.created[1], "worker_id")), false);
  assert.equal(await page.evaluate(() => Object.hasOwn(window.created[1], "reasoning_effort")), false);
  await page.getByRole("combobox", { name: /^Agent provider/ }).selectOption("claude");
  await model.fill("claude-sonnet-4-6");
  assert.equal(await effort.locator('option[value="xhigh"]').count(), 0);
  await effort.selectOption("max");
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  assert.deepEqual(errors, []);
});
