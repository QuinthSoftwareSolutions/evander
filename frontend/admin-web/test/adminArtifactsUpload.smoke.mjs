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

test("admin shell artifacts upload keeps the success banner visible after the modal closes", async (t) => {
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
  await page.getByRole("heading", { name: "Published artifacts" }).waitFor({ state: "visible" });
  await page.waitForFunction(
    () => document.querySelector(".artifacts-table tbody tr strong")?.textContent === "existing/artifact",
    undefined,
    { timeout: 5000 },
  );
  await page.locator(".artifact-space-tree-button", { hasText: "/teams/team" }).click();
  await page.waitForFunction(
    () => document.querySelector(".artifacts-table tbody tr strong")?.textContent === "team/backend/report",
    undefined,
    { timeout: 5000 },
  );
  assert.ok(
    requestedEndpoints.some((endpoint) => endpoint.includes("space_path=%2Fteams%2Fteam")),
    "expected artifact space filter request",
  );
  await page.locator(".artifact-space-tree-all").click();
  await page.waitForFunction(
    () => document.querySelector(".artifacts-table tbody tr strong")?.textContent === "existing/artifact",
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
  await assert.doesNotReject(() => page.getByText("Published artifacts").waitFor({ state: "visible" }));
  assert.equal(await page.locator(".callout.success-callout").innerText(), "Uploaded uploads/example.");
  assert.equal(await page.locator(".artifacts-table tbody tr").first().locator("strong").innerText(), "uploads/example");
  assert.equal(uploadRequests.length, 1);
  assert.ok(
    requestedEndpoints.some((endpoint) => endpoint.startsWith("/api/admin/artifacts")),
    "expected artifact list requests",
  );
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
