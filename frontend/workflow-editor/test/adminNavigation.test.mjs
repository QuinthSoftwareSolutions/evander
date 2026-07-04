import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer } from "vite";

const WORKFLOW_SIDEBAR_STORAGE_KEY = "admin-web:workflow:sections-collapsed";
const PRIVILEGE_UNLOCKED_STORAGE_KEY = "admin-web:privileges-unlocked";
const THEME_STORAGE_KEY = "admin-web:theme";

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  };
}

const sessionResponse = {
  auth_mode: "none",
  auth_required: false,
  authenticated: true,
  username: "admin",
  display_name: "Admin Operator",
  role: "admin",
  can_manage_users: true,
  session_id: "session-123",
  available_login_methods: [],
  azure_sso_login_url: null,
  privileged_operations: {
    unlocked: false,
  },
};

const adminStateResponse = {
  selected_directory_path: "root",
  directory_scoped_config_enabled: false,
  execution_backend: "libvirt_vm",
  role: "admin",
  can_manage_users: true,
  privileged_operations: {
    unlocked: false,
  },
  assistant: {
    enabled: false,
    show_launcher_when_disabled: false,
  },
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
    data: {
      directory: {
        id: "root",
        name: "root",
        parent_id: null,
        created_at: "2026-06-09T00:00:00Z",
        metadata: {},
        path: "root",
      },
      config_domains: [],
      children: [],
    },
    errors: [],
    validation_diagnostics: [],
  },
};

async function createAdminPage(t, options = {}) {
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

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;

    if (pathname === "/api/admin/branding") {
      await route.fulfill(jsonResponse({}));
      return;
    }

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
      await route.fulfill(
        jsonResponse({
          ...adminStateResponse,
          assistant: options.assistant ?? adminStateResponse.assistant,
          addons: options.addons ?? [],
        }),
      );
      return;
    }

    if (pathname === "/api/admin/user-notifications") {
      await route.fulfill(
        jsonResponse(
          options.notifications ?? {
            user_id: "admin",
            unread_count: 0,
            notifications: [],
          },
        ),
      );
      return;
    }

    if (pathname === "/api/admin/user-notifications/read") {
      await route.fulfill(
        jsonResponse({
          user_id: "admin",
          marked_notification_ids: ["notif-1"],
          unread_count: 0,
          notifications: [],
        }),
      );
      return;
    }

    if (pathname === "/api/chat/session") {
      await route.fulfill(
        jsonResponse({
          user_id: "admin",
          thread_id: "thread-1",
          assistant_capability_mode: "read-only",
          assistant_session: {
            status: "ready",
            error: null,
            updated_at: "2026-06-09T12:00:00Z",
          },
        }),
      );
      return;
    }

    if (pathname === "/api/chat/transcript/thread-1") {
      await route.fulfill(
        jsonResponse({
          thread_id: "thread-1",
          messages: [],
          assistant_capability_mode: "read-only",
          assistant_session: {
            status: "ready",
            error: null,
            updated_at: "2026-06-09T12:00:00Z",
          },
          updated_at: "2026-06-09T12:00:00Z",
        }),
      );
      return;
    }

    if (pathname === "/api/chat/stream/thread-1") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache" },
        body: "",
      });
      return;
    }

    if (pathname === "/api/configuration/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/admin/skills") {
      await route.fulfill(jsonResponse({ items: [] }));
      return;
    }

    if (pathname === "/api/admin/agents") {
      await route.fulfill(
        jsonResponse({
          items: [],
          directory_path: "root",
          direct_only: true,
        }),
      );
      return;
    }

    if (pathname === "/api/admin/approvals") {
      await route.fulfill(
        jsonResponse({
          items: [],
          health: {
            total: 0,
            pending: 0,
            approved: 0,
            denied: 0,
            expired: 0,
            oldest_pending_age_seconds: null,
          },
        }),
      );
      return;
    }

    await route.fulfill(jsonResponse({ error: `Unexpected endpoint: ${pathname}` }, 404));
  });

  return { address, page };
}

test("admin shell shows full section navigation on fresh load", async (t) => {
  const { address, page } = await createAdminPage(t);
  await page.addInitScript((storageKey) => window.localStorage.removeItem(storageKey), WORKFLOW_SIDEBAR_STORAGE_KEY);

  await page.goto(`http://127.0.0.1:${address.port}/#agents`, {
    waitUntil: "domcontentloaded",
  });

  const shell = page.locator(".admin-shell");
  await shell.waitFor({ state: "visible" });
  assert.equal(
    await shell.evaluate((element) => element.classList.contains("workflow-sidebar-collapsed")),
    false,
  );
  await assert.doesNotReject(() => page.getByRole("button", { name: "Collapse sections" }).waitFor({ state: "visible" }));

  for (const label of ["Dashboard", "Agents", "Workflow"]) {
    await assert.doesNotReject(() => page.getByRole("button", { name: new RegExp(`^${label}\\b`) }).waitFor({ state: "visible" }));
  }
  assert.equal(await page.evaluate((storageKey) => window.localStorage.getItem(storageKey), WORKFLOW_SIDEBAR_STORAGE_KEY), "false");
});

test("admin shell persisted collapsed navigation remains usable", async (t) => {
  const { address, page } = await createAdminPage(t);
  await page.addInitScript((storageKey) => window.localStorage.setItem(storageKey, "true"), WORKFLOW_SIDEBAR_STORAGE_KEY);

  await page.goto(`http://127.0.0.1:${address.port}/#agents`, {
    waitUntil: "domcontentloaded",
  });

  const shell = page.locator(".admin-shell");
  await shell.waitFor({ state: "visible" });
  assert.equal(
    await shell.evaluate((element) => element.classList.contains("workflow-sidebar-collapsed")),
    true,
  );
  await assert.doesNotReject(() => page.getByRole("button", { name: "Expand sections" }).waitFor({ state: "visible" }));

  const navTabs = page.locator('nav[aria-label="Admin sections"] .admin-shell-tab');
  assert.ok((await navTabs.count()) > 3);
  await assert.doesNotReject(() => page.getByRole("button", { name: "Agents", exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("button", { name: "Flow", exact: true }).waitFor({ state: "visible" }));
  assert.equal(await page.locator('nav[aria-label="Admin sections"] .admin-shell-tab small:visible').count(), 0);

  await navTabs.filter({ hasText: "Approvals" }).click();
  await page.waitForFunction(() => window.location.hash === "#approvals");
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Approvals" }).waitFor({ state: "visible" }));
  assert.equal(await page.evaluate((storageKey) => window.localStorage.getItem(storageKey), WORKFLOW_SIDEBAR_STORAGE_KEY), "true");
});

test("admin shell lock toggle is local UI state", async (t) => {
  const { address, page } = await createAdminPage(t);
  const privilegedRequests = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith("/api/admin/privileged/")) {
      privilegedRequests.push(pathname);
    }
  });
  await page.addInitScript(
    (storageKey) => window.sessionStorage.removeItem(storageKey),
    PRIVILEGE_UNLOCKED_STORAGE_KEY,
  );

  await page.goto(`http://127.0.0.1:${address.port}/#dashboard`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByRole("button", { name: "Unlock", exact: true }).click();
  await assert.doesNotReject(() => page.getByRole("button", { name: "Lock", exact: true }).waitFor({ state: "visible" }));
  assert.equal(
    await page.evaluate((storageKey) => window.sessionStorage.getItem(storageKey), PRIVILEGE_UNLOCKED_STORAGE_KEY),
    "true",
  );

  await page.getByRole("button", { name: "Lock", exact: true }).click();
  await assert.doesNotReject(() => page.getByRole("button", { name: "Unlock", exact: true }).waitFor({ state: "visible" }));
  assert.equal(
    await page.evaluate((storageKey) => window.sessionStorage.getItem(storageKey), PRIVILEGE_UNLOCKED_STORAGE_KEY),
    "false",
  );
  assert.deepEqual(privilegedRequests, []);
});

test("admin shell renders enabled add-on navigation in an iframe", async (t) => {
  const { address, page } = await createAdminPage(t, {
    addons: [
      {
        addon_id: "team-addon",
        display_name: "Team Add-on",
        tab_key: "addon:team-addon",
        label: "Teams",
        short_label: "Teams",
        description: "Hierarchical agent-team orchestration.",
        api_base_path: "/api/addons/team-addon",
        ui_path: "/addons/team-addon/",
      },
    ],
  });
  let addonFrameRequests = 0;
  await page.route("**/addons/team-addon/**", async (route) => {
    addonFrameRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>Team Add-on</title><main>Team Add-on</main>",
    });
  });

  await page.goto(`http://127.0.0.1:${address.port}/#addon:team-addon`, {
    waitUntil: "domcontentloaded",
  });

  await assert.doesNotReject(() => page.getByRole("button", { name: /^Teams\b/ }).waitFor({ state: "visible" }));
  const frame = page.locator('iframe.admin-addon-frame[title="Teams"]');
  await frame.waitFor({ state: "visible" });
  assert.equal(await frame.getAttribute("sandbox"), "allow-forms allow-modals allow-same-origin allow-scripts");
  const initialFrameSrc = await frame.getAttribute("src");
  assert.ok(initialFrameSrc);
  const initialFrameUrl = new URL(initialFrameSrc, `http://127.0.0.1:${address.port}`);
  assert.equal(
    initialFrameUrl.pathname,
    "/addons/team-addon/",
  );
  assert.equal(initialFrameUrl.searchParams.get("directory_path"), "root");
  assert.equal(initialFrameUrl.searchParams.get("privileged_unlocked"), "false");
  await assert.doesNotReject(() => page.frameLocator('iframe[title="Teams"]').getByText("Team Add-on").waitFor({ state: "visible" }));

  await page.getByRole("button", { name: "Unlock", exact: true }).click();
  await assert.doesNotReject(() => page.getByRole("button", { name: "Lock", exact: true }).waitFor({ state: "visible" }));
  assert.equal(await frame.getAttribute("src"), initialFrameSrc);
  assert.equal(addonFrameRequests, 1);
});

test("admin shell shows unread user notifications from the header", async (t) => {
  const { address, page } = await createAdminPage(t, {
    notifications: {
      user_id: "admin",
      unread_count: 2,
      notifications: [
        {
          id: "notif-1",
          title: "New Team chat message",
          body: "Miranda: Review is ready",
          source: "team-chat",
          source_id: "chatmsg-1",
          target_url: "#addon:team-addon",
          metadata: {},
          created_at: "2026-06-26T10:00:00Z",
          read_at: null,
        },
      ],
    },
  });

  await page.goto(`http://127.0.0.1:${address.port}/#dashboard`, {
    waitUntil: "domcontentloaded",
  });

  const notificationButton = page.getByRole("button", { name: "2 unread notifications" });
  await notificationButton.waitFor({ state: "visible" });
  await notificationButton.click();
  await assert.doesNotReject(() => page.getByRole("menuitem", { name: /New Team chat message/ }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Miranda: Review is ready").waitFor({ state: "visible" }));
});

test("admin shell keeps mobile section access and dashboard content in the first viewport", async (t) => {
  for (const storedCollapseState of [null, "true"]) {
    const { address, page } = await createAdminPage(t);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(
      ({ sidebarStorageKey, themeStorageKey, collapseState }) => {
        if (collapseState === null) {
          window.localStorage.removeItem(sidebarStorageKey);
        } else {
          window.localStorage.setItem(sidebarStorageKey, collapseState);
        }
        window.localStorage.setItem(themeStorageKey, "light");
      },
      {
        sidebarStorageKey: WORKFLOW_SIDEBAR_STORAGE_KEY,
        themeStorageKey: THEME_STORAGE_KEY,
        collapseState: storedCollapseState,
      },
    );

    await page.goto(`http://127.0.0.1:${address.port}/#dashboard`, {
      waitUntil: "domcontentloaded",
    });

    await page.locator(".admin-shell").waitFor({ state: "visible" });
    const navTabs = page.locator('nav[aria-label="Admin sections"] .admin-shell-tab');
    assert.ok(
      (await navTabs.count()) > 3,
      `expected mobile section buttons when stored collapse state is ${storedCollapseState ?? "unset"}`,
    );
    assert.equal(await page.locator(".admin-shell-nav-header:visible").count(), 0);
    assert.equal(await page.locator('nav[aria-label="Admin sections"] .admin-shell-tab small:visible').count(), 0);

    for (const label of ["Ops", "Agents", "Flow"]) {
      await assert.doesNotReject(() => page.getByRole("button", { name: label, exact: true }).waitFor({ state: "visible" }));
    }
    await assert.doesNotReject(() => page.getByRole("button", { name: "Choose directory", exact: true }).waitFor({ state: "visible" }));
    await assert.doesNotReject(() => page.getByRole("button", { name: "Dark mode", exact: true }).waitFor({ state: "visible" }));
    await assert.doesNotReject(() => page.getByRole("button", { name: "Unlock", exact: true }).waitFor({ state: "visible" }));

    const firstDashboardPanel = page.locator(".dashboard-page .dashboard-panel").first();
    await assert.doesNotReject(() => firstDashboardPanel.waitFor({ state: "visible" }));
    const panelBox = await firstDashboardPanel.boundingBox();
    assert.ok(panelBox, "expected first dashboard card to have a layout box");
    assert.ok(
      panelBox.y < 844,
      `expected first dashboard card in the first mobile viewport, got y=${panelBox.y} with stored collapse state ${storedCollapseState ?? "unset"}`,
    );
  }
});

test("admin shell anchors expanded chat launcher to the viewport", async (t) => {
  const { address, page } = await createAdminPage(t, {
    assistant: {
      enabled: true,
      show_launcher_when_disabled: false,
    },
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => window.localStorage.setItem("admin-web:chat:ui", "expanded"));

  await page.goto(`http://127.0.0.1:${address.port}/#dashboard`, {
    waitUntil: "domcontentloaded",
  });

  const panel = page.locator(".chat-panel");
  await panel.waitFor({ state: "visible" });
  const panelBox = await panel.boundingBox();
  assert.ok(panelBox, "expected expanded chat panel bounds");
  const viewport = page.viewportSize();
  assert.ok(viewport, "expected viewport size");
  assert.ok(panelBox.y >= 0, `expected chat panel to stay below the viewport top, got y=${panelBox.y}`);
  assert.ok(
    viewport.height - (panelBox.y + panelBox.height) <= 24,
    `expected chat panel to be anchored to viewport bottom, got bottom gap ${viewport.height - (panelBox.y + panelBox.height)}`,
  );
  assert.equal(await page.locator(".admin-shell-header .chat-launcher").count(), 0);
});
