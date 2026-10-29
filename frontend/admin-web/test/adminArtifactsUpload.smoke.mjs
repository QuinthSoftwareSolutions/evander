import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import os from "node:os";
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

function artifactRecord(key) {
  return {
    key,
    storage_id: `${key}-storage`,
    archive_format: "tar.gz",
    filename: `${key.split("/").pop() || "artifact"}.tar.gz`,
    mime_type: "application/gzip",
    byte_count: 128,
    sha256: "a".repeat(64),
    published_at: "2026-05-18T10:00:00Z",
    session_id: "session-1",
    vm_name: "vm-1",
    execution_backend: "libvirt_vm",
    source_artifacts_root: "/artifacts",
    source_file_count: 1,
    source_byte_count: 128,
    metadata: {},
  };
}

test("artifacts space picker filters archives and upload keeps its success banner after closing", { timeout: 60_000 }, async (t) => {
  const projectRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const server = await createServer({
    root: projectRoot,
    server: {
      host: "127.0.0.1",
      port: 0,
    },
    logLevel: "error",
  });
  await server.listen();

  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object" && "port" in address, "expected Vite to bind an ephemeral port");

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(7000);

  const requestedEndpoints = [];
  const uploadRequests = [];
  const initialArtifact = artifactRecord("existing/artifact");
  const spaceArtifact = artifactRecord("team/backend/report");
  const uploadedArtifact = artifactRecord("uploads/example");
  let artifactItems = [initialArtifact];

  const sessionResponse = {
    auth_mode: "none",
    auth_required: false,
    authenticated: true,
    username: "admin",
    display_name: "Admin Operator",
    permissions: ["admin.owner"],
    effective_permissions: ["admin.owner"],
    can_manage_users: true,
    session_id: "session-123",
    available_login_methods: [],
    azure_sso_login_url: null,
    privileged_operations: {
      unlocked: true,
    },
  };
  const adminStateResponse = {
    selected_directory_path: "root",
    directory_scoped_config_enabled: false,
    execution_backend: "libvirt_vm",
    permissions: ["admin.owner"],
    effective_permissions: ["admin.owner"],
    can_manage_users: true,
    privileged_operations: {
      unlocked: true,
    },
    assistant: {
      enabled: false,
      show_launcher_when_disabled: false,
    },
  };
  const rootDirectoryNode = {
    directory: {
      id: "root",
      name: "root",
      parent_id: null,
      created_at: "2026-05-16T00:00:00Z",
      metadata: {},
      path: "root",
    },
    config_domains: [],
    children: [],
  };
  const configurationStateResponse = {
    directory_scoped_config_enabled: false,
    selected_directory_path: "root",
    execution_backend: "libvirt_vm",
    selected_directory: {
      path: "root",
      parent_path: null,
      can_create: false,
      can_delete: false,
    },
    tree: {
      ok: true,
      data: rootDirectoryNode,
      errors: [],
      validation_diagnostics: [],
    },
  };
  const artifactSpacesResponse = {
    success: true,
    revision: 4,
    count: 4,
    spaces: [
      {
        path: "/",
        parent_path: null,
        display_name: "Root",
        metadata: {},
      },
      {
        path: "/teams",
        parent_path: "/",
        display_name: "Teams",
        metadata: {},
      },
      {
        path: "/teams/team",
        parent_path: "/teams",
        display_name: "Team",
        metadata: {},
      },
      {
        path: "/teams/team/projects/backend",
        parent_path: "/teams/team",
        display_name: "Backend",
        metadata: {},
      },
    ],
  };

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    requestedEndpoints.push(`${pathname}${url.search}`);

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse(sessionResponse));
      return;
    }

    if (pathname === "/api/admin/deployment-status") {
      await route.fulfill(
        jsonResponse({
          active: false,
          message: null,
          source: null,
          started_at: null,
          updated_at: null,
          ttl_seconds: 0,
          stale: false,
        }),
      );
      return;
    }

    if (pathname === "/api/admin/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(adminStateResponse));
      return;
    }

    if (pathname === "/api/configuration/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/admin/artifact-spaces") {
      assert.equal(url.searchParams.get("recursive"), "true");
      assert.equal(url.searchParams.get("include_versions"), "false");
      await route.fulfill(jsonResponse(artifactSpacesResponse));
      return;
    }

    if (pathname === "/api/admin/artifacts") {
      const spacePath = url.searchParams.get("space_path");
      const items = spacePath === "/teams/team" ? [spaceArtifact] : artifactItems;
      if (spacePath) {
        assert.equal(spacePath, "/teams/team");
        assert.equal(url.searchParams.get("space_recursive"), "true");
      }
      await route.fulfill(
        jsonResponse({
          items,
          limit: 50,
          total: items.length,
          next_cursor: null,
        }),
      );
      return;
    }

    if (pathname === "/api/admin/artifacts/uploads") {
      const body = route.request().postData() ?? "";
      const keyMatch = body.match(/name="key"\r\n\r\n([^\r\n]+)/);
      const uploadedKey = keyMatch?.[1] ?? null;
      uploadRequests.push({
        method: route.request().method(),
        uploadedKey,
      });
      assert.equal(route.request().method(), "POST");
      assert.equal(uploadedKey, "uploads/example");
      artifactItems = [uploadedArtifact, initialArtifact];
      await route.fulfill(jsonResponse(uploadedArtifact));
      return;
    }

    await route.continue();
  });

  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  const uploadDir = mkdtempSync(path.join(os.tmpdir(), "evander-artifact-upload-"));
  const uploadFilePath = path.join(uploadDir, "sample.txt");
  writeFileSync(uploadFilePath, "artifact payload\n");

  await page.goto(`http://127.0.0.1:${address.port}/#artifacts`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByRole("heading", { name: "Artifacts", exact: true }).waitFor({ state: "visible" });
  await page.getByRole("heading", { name: "Archive list" }).waitFor({ state: "visible" });
  await page.waitForFunction(
    () => document.querySelector(".artifacts-table tbody tr .record-name")?.textContent === "existing/artifact",
    undefined,
    { timeout: 5000 },
  );
  const spacePickerTrigger = page.getByRole("button", { name: "Choose artifact space", exact: true });
  const spacePicker = page.getByRole("dialog", { name: "Choose artifact space", exact: true });
  assert.equal(await spacePicker.count(), 0);
  await spacePickerTrigger.click();
  await spacePicker.waitFor({ state: "visible" });
  assert.equal(await spacePickerTrigger.getAttribute("aria-expanded"), "true");
  const pickerBounds = await spacePicker.boundingBox();
  const sidebarBounds = await page.locator(".feature-sidebar").boundingBox();
  assert.ok(pickerBounds.width > sidebarBounds.width * 2, "space tree should have more room than the sidebar");
  await page.keyboard.press("Escape");
  await spacePicker.waitFor({ state: "detached" });
  assert.equal(await spacePickerTrigger.evaluate((element) => document.activeElement === element), true);
  assert.equal(await spacePickerTrigger.getAttribute("aria-expanded"), "false");
  assert.match(await spacePickerTrigger.innerText(), /All spaces/);

  await spacePickerTrigger.click();
  await spacePicker.getByRole("button", { name: "Expand /teams/team", exact: true }).click();
  await spacePicker.getByRole("button", { name: "Team /teams/team", exact: true }).click();
  await spacePicker.waitFor({ state: "detached" });
  assert.equal(await spacePickerTrigger.evaluate((element) => document.activeElement === element), true);
  assert.match(await spacePickerTrigger.innerText(), /\/teams\/team/);
  await page.waitForFunction(
    () => document.querySelector(".artifacts-table tbody tr .record-name")?.textContent === "team/backend/report",
    undefined,
    { timeout: 5000 },
  );
  assert.ok(
    requestedEndpoints.some((endpoint) => endpoint.includes("space_path=%2Fteams%2Fteam")),
    "expected artifact space filter request",
  );
  await spacePickerTrigger.click();
  const selectedSpaceButton = spacePicker.getByRole("button", { name: "Team /teams/team", exact: true });
  assert.equal(await selectedSpaceButton.getAttribute("aria-pressed"), "true");
  assert.equal(await selectedSpaceButton.evaluate((element) => document.activeElement === element), true);
  assert.equal(await spacePicker.getByRole("button", { name: "Collapse /teams/team", exact: true }).getAttribute("aria-expanded"), "true");
  assert.equal(await spacePicker.getByRole("button", { name: "Backend /teams/team/projects/backend", exact: true }).isVisible(), true);
  await spacePicker.getByRole("button", { name: "Close", exact: true }).click();
  await spacePicker.waitFor({ state: "detached" });
  assert.match(await spacePickerTrigger.innerText(), /\/teams\/team/);

  await page.setViewportSize({ width: 390, height: 844 });
  await spacePickerTrigger.click();
  const narrowBounds = await spacePicker.boundingBox();
  assert.ok(narrowBounds.x >= 0 && narrowBounds.x + narrowBounds.width <= 391, "picker should fit a narrow screen");
  assert.ok(narrowBounds.y >= 0 && narrowBounds.y + narrowBounds.height <= 845, "picker should fit the viewport height");
  await page.keyboard.press("Shift+Tab");
  assert.equal(await spacePicker.evaluate((element) => element.contains(document.activeElement)), true);
  await page.mouse.click(2, 2);
  await spacePicker.waitFor({ state: "detached" });
  assert.equal(await spacePickerTrigger.evaluate((element) => document.activeElement === element), true);
  assert.match(await spacePickerTrigger.innerText(), /\/teams\/team/);
  await page.setViewportSize({ width: 1280, height: 720 });
  await spacePickerTrigger.click();
  await spacePicker.getByRole("button", { name: "All spaces Unfiltered", exact: true }).click();
  await spacePicker.waitFor({ state: "detached" });
  await page.waitForFunction(
    () => document.querySelector(".artifacts-table tbody tr .record-name")?.textContent === "existing/artifact",
    undefined,
    { timeout: 5000 },
  );
  await page.getByRole("button", { name: "Upload artifact" }).click();

  const dialog = page.getByRole("dialog", { name: "Upload artifact" });
  await dialog.waitFor({ state: "visible" });
  await dialog.getByLabel("Artifact key").fill("uploads/example");
  await dialog.getByLabel("File").setInputFiles(uploadFilePath);
  await dialog.getByRole("button", { name: "Upload" }).click();

  await assert.doesNotReject(() => dialog.waitFor({ state: "detached" }));
  await assert.doesNotReject(() => page.getByText("Uploaded uploads/example.").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "uploads/example" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Archive list").waitFor({ state: "visible" }));
  assert.equal(await page.locator(".callout.success-callout").innerText(), "Uploaded uploads/example.");
  assert.equal(await page.locator(".artifacts-table tbody tr").first().locator(".record-name").innerText(), "uploads/example");
  assert.equal(uploadRequests.length, 1);
  assert.ok(
    requestedEndpoints.some((endpoint) => endpoint.startsWith("/api/admin/artifacts")),
    "expected artifact list requests",
  );
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
