import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

test("selected agents show dispatch settings, refresh new runs, and clear old values", { timeout: 45000 }, async (t) => {
  const server = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    server: { host: "127.0.0.1", port: 0 },
    logLevel: "error",
    plugins: [{
      name: "agent-runtime-settings-fixture",
      configureServer(server) {
        server.middlewares.use(async (request, response, next) => {
          if (request.url !== "/agent-settings-fixture") return next();
          response.setHeader("Content-Type", "text/html");
          response.end(await server.transformIndexHtml(request.url, `
            <html><body><div id="root"></div><script type="module">
            import React from 'react';
            import { createRoot } from 'react-dom/client';
            import { AgentsPage } from '/src/features/agents/AgentsPage.tsx';
            import '/src/design/baseline.css';
            import '/src/design/tokens.css';
            import '/src/design/controls.css';
            import '/src/design/layout.css';
            import '/src/design/collections.css';
            import '/src/design/dashboard.css';
            import '/src/design/workspaces.css';
            import '/src/design/workflow.css';
            import '/src/design/surfaces.css';
            document.documentElement.dataset.adminDesign = '';
            createRoot(document.getElementById('root')).render(React.createElement(AgentsPage));
            </script></body></html>`));
        });
      },
    }],
  });
  await server.listen();
  t.after(() => server.close());
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  const agent = (id, name, backend = "container_worker_vm") => ({
    agent_id: id, target_name: name, backend, session_id: id, directory_path: "root",
    status: "running", started_at: null, finished_at: null, updated_at: "2026-10-14T10:00:00Z",
    capabilities: {},
    host: {
      active_execution_count: 1, latest_execution_id: `${id}-run`,
      agent: { state: "running", dispatch_id: `${id}-run` },
    },
  });
  const alpha = agent("alpha", "Worker alpha");
  const beta = agent("beta", "Worker beta");
  const vm = agent("vm", "VM agent", "libvirt_vm");
  const agents = [alpha, beta, vm];
  let alphaSettings = [{
    execution_id: "alpha-run", model: "gpt-6.1-sol", fast_mode: false,
    fast_mode_source: "prompt", reasoning_effort: "low", reasoning_effort_source: "prompt",
  }];
  await page.route("**/api/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    let json = { items: [] };
    if (pathname === "/api/admin/agents") {
      json = { items: agents, directory_path: "root", direct_only: true };
    } else if (/\/api\/admin\/agents\/(alpha|beta|vm)$/.test(pathname)) {
      const selected = agents.find((item) => pathname.endsWith(`/${item.agent_id}`));
      if (selected === vm) {
        assert.equal(new URL(route.request().url()).searchParams.get("omit_vm_state"), "false");
      }
      json = {
        ...selected,
        host: {
          ...selected.host,
          session_policy: { model: "session-default-model", reasoning_effort: "max", fast_mode: true, metadata: {} },
          active_execution_settings: selected === alpha ? alphaSettings : selected === beta ? [{
            execution_id: "beta-run", model: null, fast_mode: null, reasoning_effort: null,
            fast_mode_source: null, reasoning_effort_source: null,
          }] : [{
            execution_id: "vm-run", model: "vm-dispatch-model", fast_mode: true,
            fast_mode_source: "session", reasoning_effort: null, reasoning_effort_source: "default",
          }],
        },
      };
    } else if (pathname.endsWith("/executions")) {
      json = { items: [], has_more: false, next_cursor: null };
    }
    await route.fulfill({ json });
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/agent-settings-fixture#agents?agent_id=alpha`);
  const panel = page.getByRole("region", { name: "Current run settings" });
  const value = (label) => panel.locator("dl > div").filter({ has: page.getByText(label, { exact: true }) }).locator("dd");
  await page.getByText("gpt-6.1-sol", { exact: true }).waitFor();
  assert.match(await value("Speed").innerText(), /^Standard\nPrompt override$/);
  assert.match(await value("Reasoning").innerText(), /^low\nPrompt override$/);
  assert.equal(await panel.getByText("session-default-model").count(), 0);
  assert.equal(await page.getByText("Unlock privileged operations to create, edit, stop, destroy, or resume targets.", { exact: true }).isVisible(), true);
  if (process.env.EVANDER_SCREENSHOT_PATH) {
    await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
    await page.screenshot({ path: process.env.EVANDER_SCREENSHOT_PATH });
  }

  await page.getByRole("button", { name: /Worker beta/ }).click();
  await panel.getByText("Not reported", { exact: true }).first().waitFor();
  assert.equal(await panel.getByText("Not reported", { exact: true }).count(), 3);
  assert.equal(await panel.getByText("gpt-6.1-sol").count(), 0);
  await page.getByRole("button", { name: /VM agent/ }).click();
  await panel.getByText("vm-dispatch-model", { exact: true }).waitFor();
  assert.match(await value("Speed").innerText(), /^Fast\nSession default at dispatch$/);
  assert.match(await value("Reasoning").innerText(), /^Provider default\nRuntime default at dispatch$/);

  await page.getByRole("button", { name: /Worker alpha/ }).click();
  await panel.getByText("gpt-6.1-sol", { exact: true }).waitFor();
  alphaSettings = [{
    execution_id: "alpha-next", model: "gpt-6-astra", fast_mode: "ultrafast",
    fast_mode_source: "session", reasoning_effort: "high", reasoning_effort_source: "session",
  }];
  alpha.updated_at = "2026-10-14T10:05:00Z";
  alpha.host.latest_execution_id = "alpha-next";
  await panel.getByText("gpt-6-astra", { exact: true }).waitFor();
  assert.match(await value("Speed").innerText(), /^Ultrafast\nSession default at dispatch$/);
  assert.equal(await panel.getByText("gpt-6.1-sol").count(), 0);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth), true);
  const positions = await panel.locator("dl > div").evaluateAll((fields) => fields.map((field) => field.getBoundingClientRect().top));
  assert.ok(positions[0] < positions[1] && positions[1] < positions[2], "narrow screens stack the three settings");

  alphaSettings.push({
    execution_id: "alpha-parallel", model: "parallel-model", fast_mode: true,
    fast_mode_source: "default", reasoning_effort: "medium", reasoning_effort_source: "default",
  });
  alpha.host.active_execution_count = 2;
  await panel.getByText("parallel-model", { exact: true }).waitFor();
  assert.equal(await panel.getByText("gpt-6-astra", { exact: true }).count(), 1);
  await panel.getByRole("heading", { name: "Current runs" }).waitFor();
  alpha.status = "idle";
  alpha.host.active_execution_count = 0;
  await panel.waitFor({ state: "hidden" });
  assert.deepEqual(errors, []);
});
