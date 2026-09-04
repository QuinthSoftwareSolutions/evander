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
  permissions: ["admin.owner"],
  effective_permissions: ["admin.owner"],
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
  permissions: ["admin.owner"],
  effective_permissions: ["admin.owner"],
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
  tabs: [
    {
      key: "people_communications",
      title: "People & Communications",
      help_text: "Messaging and notification settings.",
      underlying_domains: ["providers", "policy"],
      sections: [
        {
          key: "teams",
          title: "Teams",
          fields: [
            {
              source_domain: "providers",
              field_name: "teams_timeout_seconds",
              label: "Teams timeout seconds",
              kind: "float",
              options: [],
              scope: "directory",
              root_starts_editable: false,
              tooltip: "Maximum wait for Microsoft Teams provider requests.",
            },
          ],
        },
        {
          key: "communication-files",
          title: "Communication files",
          fields: [
            {
              source_domain: "policy",
              field_name: "communication_file_max_bytes",
              label: "Maximum communication file bytes",
              kind: "int",
              options: [],
              scope: "directory",
              root_starts_editable: false,
              tooltip: "Maximum file size accepted for communication attachments.",
            },
          ],
        },
      ],
    },
    {
      key: "agent_runtime_launch",
      title: "Agent Runtime & Launch",
      help_text: "Execution and container worker settings.",
      underlying_domains: ["providers"],
      sections: [
        {
          key: "container-worker-settings",
          title: "Container worker settings",
          fields: [
            {
              source_domain: "providers",
              field_name: "container_worker_job_timeout_seconds",
              label: "Job timeout seconds",
              kind: "int",
              options: [],
              scope: "directory",
              root_starts_editable: false,
              tooltip: "Maximum duration of a container worker job.",
            },
          ],
        },
      ],
    },
  ],
  tab_snapshots: {
    people_communications: {
      key: "people_communications",
      title: "People & Communications",
      revision: "people-rev-1",
      raw: { exists: true, config: { teams_timeout_seconds: 5, communication_file_max_bytes: 25000000 } },
      effective: {
        config: { teams_timeout_seconds: 5, communication_file_max_bytes: 25000000 },
        validation_diagnostics: [],
        errors: [],
        payload: { teams_timeout_seconds: 5, communication_file_max_bytes: 25000000 },
      },
      parent_defaults: { directory_path: null, config: {}, payload: {} },
    },
    agent_runtime_launch: {
      key: "agent_runtime_launch",
      title: "Agent Runtime & Launch",
      revision: "runtime-rev-1",
      raw: { exists: true, config: { container_worker_job_timeout_seconds: 900 } },
      effective: {
        config: { container_worker_job_timeout_seconds: 900 },
        validation_diagnostics: [],
        errors: [],
        payload: { container_worker_job_timeout_seconds: 900 },
      },
      parent_defaults: { directory_path: null, config: {}, payload: {} },
    },
  },
  directory_overrides: {
    directory_path: "root",
    exists: true,
    domain_count: 2,
    field_count: 3,
    overrides: {},
    errors: [],
    payload: {},
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
  let browser;
  let context;
  let page;
  await t.after(async () => {
    await page?.close().catch(() => {});
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    await server.close().catch(() => {});
  });

  await server.listen();

  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object" && "port" in address, "expected Vite to bind an ephemeral port");

  browser = await chromium.launch({ headless: true });
  context = await browser.newContext();
  page = await context.newPage();

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;

    if (pathname === "/api/admin/branding") {
      await route.fulfill(jsonResponse({}));
      return;
    }

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse(options.session ?? sessionResponse));
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
          ...(options.adminState ?? adminStateResponse),
          assistant: options.assistant ?? adminStateResponse.assistant,
          addons: options.addons ?? [],
        }),
      );
      return;
    }

    if (pathname === "/api/admin/privileged/unlock" || pathname === "/api/admin/privileged/lock") {
      const unlocked = pathname.endsWith("/unlock");
      if (options.handlePrivilegedRequest) {
        await options.handlePrivilegedRequest({ route, pathname, unlocked });
        return;
      }
      await route.fulfill(
        jsonResponse({
          message: unlocked ? "Privileged operations unlocked." : "Privileged operations locked.",
          privileged_operations: {
            unlocked,
          },
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
      await route.fulfill(jsonResponse(options.configurationState ?? configurationStateResponse));
      return;
    }

    if (pathname.startsWith("/api/configuration/domains/") && route.request().method() === "PUT") {
      const tabKey = decodeURIComponent(pathname.slice("/api/configuration/domains/".length));
      const requestPayload = route.request().postDataJSON();
      options.configurationSaveRequests?.push({ tabKey, payload: requestPayload });
      const sourceState = options.configurationState ?? configurationStateResponse;
      const sourceSnapshot = sourceState.tab_snapshots[tabKey];
      await route.fulfill(jsonResponse({
        write_result: { ok: true, data: {}, errors: [], validation_diagnostics: [] },
        tab_snapshot: {
          ...sourceSnapshot,
          revision: `${sourceSnapshot.revision}-saved`,
          raw: { exists: true, config: requestPayload.config },
          effective: {
            ...sourceSnapshot.effective,
            config: requestPayload.config,
            payload: requestPayload.config,
          },
        },
      }));
      return;
    }

    if (pathname === "/api/configuration/model-pricing") {
      await route.fulfill(jsonResponse({
        status: "ok",
        path: "/tmp/model-pricing.json",
        path_source: "default",
        env_var: "EVANDER_MODEL_PRICING_PATH",
        version: "1",
        currency: "usd",
        description: null,
        sources: [],
        rule_count: 0,
        rules: [],
        revision: "pricing-rev-1",
        catalog: { version: "1", currency: "usd", description: null, sources: [], rules: [] },
        editable: true,
        write_requires_privileged_unlock: true,
        error: null,
        error_class: null,
      }));
      return;
    }

    if (pathname === "/api/teams-auth/status") {
      await route.fulfill(jsonResponse({
        directory_path: "root",
        backend: "stub",
        state: "not_configured",
        scopes: [],
      }));
      return;
    }

    if (pathname === "/api/admin/skills") {
      await route.fulfill(jsonResponse({ items: [] }));
      return;
    }

    if (pathname === "/api/admin/git-repositories") {
      const method = route.request().method();
      if (method === "GET") {
        options.gitRepositoryRequests?.push({
          directoryPath: url.searchParams.get("directory_path"),
          includeDescendants: url.searchParams.get("include_descendants"),
        });
        await route.fulfill(jsonResponse({ repositories: options.gitRepositories ?? [] }));
        return;
      }
      if (method === "POST") {
        const payload = route.request().postDataJSON();
        options.gitRepositoryMutations?.push({ action: "register", payload });
        const repository = {
          managed_repo_id: "mr_registered",
          directory_path: payload.directory_path,
          canonical_repository_identity: "azure_devops:exampleindustry/project/receptionist",
          canonical_repository_url: payload.repo_source,
          provider: "azure_devops",
          display_name: "exampleindustry/Project/Receptionist",
          default_branch: payload.default_branch ?? "main",
          host_commit_identity: { name: "Truman Bot", email: "truman@example.com" },
          bindings: [],
        };
        options.gitRepositories = [...(options.gitRepositories ?? []), repository];
        await route.fulfill(jsonResponse({ repository }));
        return;
      }
    }

    if (pathname.startsWith("/api/admin/git-repositories/")) {
      const suffix = pathname.slice("/api/admin/git-repositories/".length);
      const repositoryId = decodeURIComponent(suffix.split("/")[0]);
      const repository = (options.gitRepositories ?? []).find(
        (item) => item.managed_repo_id === repositoryId,
      );
      const method = route.request().method();
      if (suffix.endsWith("/refresh") && method === "POST") {
        options.gitRepositoryMutations?.push({ action: "refresh", repositoryId });
        await route.fulfill(jsonResponse({ repository: { ...repository, refreshed_at: "2026-08-05T10:00:00Z" } }));
        return;
      }
      if (suffix.endsWith("/host-commit-identity") && method === "PUT") {
        const payload = route.request().postDataJSON();
        options.gitRepositoryMutations?.push({ action: "identity", repositoryId, payload });
        await route.fulfill(jsonResponse({ repository: { ...repository, host_commit_identity: payload } }));
        return;
      }
      if (method === "DELETE") {
        options.gitRepositoryMutations?.push({ action: "delete", repositoryId });
        options.gitRepositories = (options.gitRepositories ?? []).filter(
          (item) => item.managed_repo_id !== repositoryId,
        );
        await route.fulfill(jsonResponse({ deleted: true, repository }));
        return;
      }
    }

    if (pathname === "/api/admin/git-repositories/maintenance") {
      options.gitRepositoryMutations?.push({ action: "maintenance", payload: route.request().postDataJSON() });
      await route.fulfill(jsonResponse({ maintenance: { success: true } }));
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

    if (pathname === "/api/admin/codex-subscriptions") {
      await route.fulfill(
        jsonResponse({
          directory_path: "root",
          worker_url: "http://worker.test:11888",
          profiles: options.subscriptionProfiles ?? [],
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

    if (pathname === "/api/admin/sessions/catalog") {
      await route.fulfill(
        jsonResponse({
          tool_options: [],
          model_options: [],
          resume_mode_options: [],
          renew_extension_presets: [],
        }),
      );
      return;
    }

    if (pathname === "/api/admin/sessions") {
      const items = options.sessions ?? [];
      await route.fulfill(
        jsonResponse({
          items,
          counts: {
            total: items.length,
            active: items.filter((item) => !item.is_expired).length,
            expired: items.filter((item) => item.is_expired).length,
          },
          status_ids: {
            active: items.filter((item) => !item.is_expired).map((item) => item.session_id),
            expired: items.filter((item) => item.is_expired).map((item) => item.session_id),
          },
        }),
      );
      return;
    }

    if (pathname.startsWith("/api/admin/sessions/")) {
      const sessionId = decodeURIComponent(pathname.slice("/api/admin/sessions/".length));
      const session = (options.sessions ?? []).find((item) => item.session_id === sessionId);
      await route.fulfill(
        session
          ? jsonResponse({ session, wake_schedules: [], git_grants: [], summary_json: {} })
          : jsonResponse({ error: "Unknown session" }, 404),
      );
      return;
    }

    if (pathname === "/api/admin/scheduled-wakes") {
      await route.fulfill(jsonResponse({ items: [] }));
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

  for (const label of ["Dashboard", "Agents", "Subscriptions", "Workflow"]) {
    await assert.doesNotReject(() => page.getByRole("button", { name: new RegExp(`^${label}\\b`) }).waitFor({ state: "visible" }));
  }
  assert.equal(await page.evaluate((storageKey) => window.localStorage.getItem(storageKey), WORKFLOW_SIDEBAR_STORAGE_KEY), "false");
});

test("subscriptions page shows worker profile identity and capacity", async (t) => {
  const { address, page } = await createAdminPage(t, {
    subscriptionProfiles: [
      {
        profile_id: "team-a",
        display_name: "Team A",
        enabled: true,
        pinned_account_id: "account-a",
        available: true,
        signed_in: true,
        account_id: "account-a",
        account_type: "chatgpt",
        email: "team-a@example.test",
        plan_type: "plus",
        last_refresh: "2026-09-02T10:00:00Z",
        access_token_expires_at: null,
        rate_limits: { default: { primary: { used_percent: 25 } } },
        usage: { summary: { lifetime_tokens: 1234 } },
        active_job_count: 2,
        queued_prompt_count: 3,
        error: null,
        created_at: "2026-09-01T10:00:00Z",
        updated_at: "2026-09-02T10:00:00Z",
      },
    ],
  });

  await page.goto(`http://127.0.0.1:${address.port}/#subscriptions`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByRole("heading", { name: "Subscriptions" }).waitFor({ state: "visible" });
  await page.getByRole("heading", { name: "Team A" }).waitFor({ state: "visible" });
  const pageText = await page.locator(".feature-page").innerText();
  assert.match(pageText, /http:\/\/worker\.test:11888/);
  assert.match(pageText, /team-a@example\.test/);
  assert.match(pageText, /Active jobs\s*2/i);
  assert.match(pageText, /Queued prompts\s*3/i);
  assert.match(pageText, /Pinned account id\s*account-a/i);
});

test("admin shell groups every destination and exposes descriptions as tooltips", async (t) => {
  const { address, page } = await createAdminPage(t);

  await page.goto(`http://127.0.0.1:${address.port}/#agents`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator(".admin-shell").waitFor({ state: "visible" });

  assert.deepEqual(
    await page.locator(".admin-shell-nav-group-label").allTextContents(),
    ["Overview", "Operations", "Automation", "Extensions", "Infrastructure", "Governance"],
  );
  assert.equal(await page.locator('nav[aria-label="Admin sections"] .admin-shell-tab').count(), 21);
  assert.equal(await page.locator('nav[aria-label="Admin sections"] .admin-shell-tab small').count(), 0);

  await page.getByRole("button", { name: "Agents", exact: true }).hover();
  await assert.doesNotReject(() => page.getByRole("tooltip").getByText("Active and historical execution targets.").waitFor({ state: "visible" }));
});

test("managed Git page registers and maintains repositories through the Admin API", async (t) => {
  const gitRepositoryRequests = [];
  const gitRepositoryMutations = [];
  const gitRepositories = [
    {
      managed_repo_id: "mr_existing",
      directory_path: "root",
      canonical_repository_identity: "github:example/existing",
      canonical_repository_url: "https://github.com/example/existing.git",
      provider: "github",
      display_name: "example/existing",
      default_branch: "main",
      host_commit_identity: { name: "Truman Bot", email: "truman@example.com" },
      bindings: [],
    },
  ];
  const { address, page } = await createAdminPage(t, {
    session: {
      ...sessionResponse,
      privileged_operations: { unlocked: true },
    },
    adminState: {
      ...adminStateResponse,
      privileged_operations: { unlocked: true },
    },
    gitRepositories,
    gitRepositoryRequests,
    gitRepositoryMutations,
  });

  await page.goto(`http://127.0.0.1:${address.port}/#git-repositories`, {
    waitUntil: "domcontentloaded",
  });
  await page.getByRole("heading", { level: 1, name: "Managed Git", exact: true }).waitFor({ state: "visible" });
  assert.deepEqual(gitRepositoryRequests[0], {
    directoryPath: "root",
    includeDescendants: "true",
  });

  await page.getByLabel("Repository source").fill("https://dev.azure.com/exampleindustry/Project/_git/Receptionist");
  await page.getByLabel("Default branch").fill("main");
  await page.getByRole("button", { name: "Register repository", exact: true }).click();
  await page.getByText("Registered exampleindustry/Project/Receptionist.", { exact: true }).waitFor({ state: "visible" });
  assert.deepEqual(gitRepositoryMutations[0], {
    action: "register",
    payload: {
      directory_path: "root",
      repo_source: "https://dev.azure.com/exampleindustry/Project/_git/Receptionist",
      default_branch: "main",
      refresh: true,
    },
  });

  await page.getByRole("button", { name: "Refresh upstream", exact: true }).click();
  await page.getByText("Refreshed exampleindustry/Project/Receptionist from its upstream.", { exact: true }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Run maintenance", exact: true }).click();
  await page.getByText("Maintenance completed for exampleindustry/Project/Receptionist.", { exact: true }).waitFor({ state: "visible" });

  const identityPanel = page.locator(".managed-git-identity-panel");
  await identityPanel.getByLabel("Name").fill("Repository Bot");
  await identityPanel.getByLabel("Email").fill("repository-bot@example.com");
  await identityPanel.getByRole("button", { name: "Save identity", exact: true }).click();
  await page.getByText("Updated the host commit identity for exampleindustry/Project/Receptionist.", { exact: true }).waitFor({ state: "visible" });

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete repository", exact: true }).click();
  await page.getByText("Deleted exampleindustry/Project/Receptionist.", { exact: true }).waitFor({ state: "visible" });
  assert.deepEqual(gitRepositoryMutations.map((item) => item.action), ["register", "refresh", "maintenance", "identity", "delete"]);
  assert.deepEqual(gitRepositoryMutations[2].payload, {
    managed_repo_id: "mr_registered",
    directory_path: "root",
  });
  assert.deepEqual(gitRepositoryMutations[3].payload, {
    name: "Repository Bot",
    email: "repository-bot@example.com",
  });
});

test("quick navigator filters destinations and supports keyboard navigation", async (t) => {
  const { address, page } = await createAdminPage(t);

  await page.goto(`http://127.0.0.1:${address.port}/#dashboard`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator(".admin-shell").waitFor({ state: "visible" });

  await page.keyboard.press("Control+k");
  await assert.doesNotReject(() => page.getByRole("dialog", { name: "Find a page" }).waitFor({ state: "visible" }));
  const search = page.getByRole("searchbox", { name: "Search pages" });
  assert.equal(await search.evaluate((element) => element === document.activeElement), true);
  await search.press("ArrowUp");
  assert.equal(await page.getByRole("option", { name: /Users & Groups/ }).getAttribute("aria-selected"), "true");
  await search.press("ArrowDown");
  assert.equal(await page.getByRole("option", { name: /Dashboard/ }).getAttribute("aria-selected"), "true");
  await search.fill("managed codex");
  assert.equal(await page.getByRole("option").count(), 1);
  await assert.doesNotReject(() => page.getByRole("option", { name: /Skills/ }).getByText("Automation").waitFor({ state: "visible" }));
  await search.press("Enter");
  await page.waitForFunction(() => window.location.hash === "#skills");
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Skills", exact: true }).waitFor({ state: "visible" }));

  await page.getByRole("button", { name: "Find page", exact: true }).click();
  await page.getByRole("searchbox", { name: "Search pages" }).fill("definitely not a destination");
  await assert.doesNotReject(() => page.getByText("No pages match “definitely not a destination”.").waitFor({ state: "visible" }));
  await page.getByRole("searchbox", { name: "Search pages" }).press("Shift+Tab");
  const closeButton = page.getByRole("button", { name: "Close page finder" });
  assert.equal(await closeButton.evaluate((element) => element === document.activeElement), true);
  await closeButton.press("Escape");
  assert.equal(await page.getByRole("dialog", { name: "Find a page" }).count(), 0);
});

test("configuration navigation focuses sections, searches technical fields, and preserves drafts", async (t) => {
  const configurationSaveRequests = [];
  const { address, page } = await createAdminPage(t, {
    session: {
      ...sessionResponse,
      privileged_operations: { unlocked: true },
    },
    adminState: {
      ...adminStateResponse,
      directory_scoped_config_enabled: true,
      privileged_operations: { unlocked: true },
    },
    configurationState: {
      ...configurationStateResponse,
      directory_scoped_config_enabled: true,
    },
    configurationSaveRequests,
  });

  await page.goto(
    `http://127.0.0.1:${address.port}/#configuration?config_tab=agent_runtime_launch&config_section=container-worker-settings&config_field=container_worker_job_timeout_seconds`,
    { waitUntil: "domcontentloaded" },
  );
  await page.locator(".configuration-page").waitFor({ state: "visible" });
  await assert.doesNotReject(() => page.getByRole("heading", { level: 2, name: "Container worker settings", exact: true }).waitFor({ state: "visible" }));
  const runtimeField = page.locator("#configuration-field-agent_runtime_launch-container_worker_job_timeout_seconds");
  await assert.doesNotReject(() => runtimeField.waitFor({ state: "visible" }));
  assert.equal(await runtimeField.getByRole("spinbutton").inputValue(), "900");
  assert.equal(await runtimeField.getByRole("spinbutton").evaluate((element) => element === document.activeElement), true);

  const search = page.getByRole("combobox", { name: "Find setting" });
  await search.fill("teams_timeout_seconds");
  const teamsResult = page.getByRole("option", { name: /Teams timeout seconds/ });
  await assert.doesNotReject(() => teamsResult.waitFor({ state: "visible" }));
  await teamsResult.click();
  await assert.doesNotReject(() => page.getByRole("heading", { level: 2, name: "Teams", exact: true }).waitFor({ state: "visible" }));
  assert.match(page.url(), /config_tab=people_communications/);
  assert.match(page.url(), /config_section=teams/);
  assert.match(page.url(), /config_field=teams_timeout_seconds/);

  const teamsInput = page.locator("#configuration-field-people_communications-teams_timeout_seconds").getByRole("spinbutton");
  await teamsInput.fill("12");
  const configurationNav = page.getByRole("navigation", { name: "Configuration areas and sections" });
  await configurationNav.getByRole("button", { name: /Communication files/ }).click();
  await assert.doesNotReject(() => page.getByRole("heading", { level: 2, name: "Communication files", exact: true }).waitFor({ state: "visible" }));
  await configurationNav.getByRole("button", { name: "Teams 1", exact: true }).click();
  assert.equal(await teamsInput.inputValue(), "12");
  assert.match(await configurationNav.getByRole("button", { name: /People & Communications/ }).innerText(), /unsaved/i);

  await configurationNav.getByRole("button", { name: "Validation", exact: true }).click();
  await assert.doesNotReject(() => page.getByRole("heading", { level: 2, name: "Validation", exact: true }).waitFor({ state: "visible" }));
  assert.match(page.url(), /config_section=validation/);

  await page.getByRole("button", { name: "Save tab" }).click();
  await page.waitForFunction(() => document.body.textContent?.includes("Saved People & Communications overrides for root."));
  assert.equal(configurationSaveRequests.length, 1);
  assert.equal(configurationSaveRequests[0].tabKey, "people_communications");
  assert.deepEqual(configurationSaveRequests[0].payload.config, {
    teams_timeout_seconds: 12,
    communication_file_max_bytes: 25000000,
  });
  assert.equal(configurationSaveRequests[0].payload.expected_revision, "people-rev-1");

  await page.setViewportSize({ width: 720, height: 900 });
  await assert.doesNotReject(() => page.getByRole("combobox", { name: "Configuration area", exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("combobox", { name: "Section or utility", exact: true }).waitFor({ state: "visible" }));
});

test("quick navigator includes dynamic add-on destinations in Extensions", async (t) => {
  const { address, page } = await createAdminPage(t, {
    addons: [
      {
        addon_id: "team-addon",
        display_name: "Team Add-on",
        tab_key: "addon:team-addon",
        label: "Teams",
        short_label: "Teams",
        description: "Team-oriented add-on surface.",
        api_base_path: "/api/addons/team-addon",
        ui_path: "/addons/team-addon/",
      },
    ],
  });

  await page.goto(`http://127.0.0.1:${address.port}/#dashboard`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator(".admin-shell").waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Find page", exact: true }).click();
  await page.getByRole("searchbox", { name: "Search pages" }).fill("Teams");

  const option = page.getByRole("option", { name: /Teams/ });
  await assert.doesNotReject(() => option.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => option.getByText("Extensions").waitFor({ state: "visible" }));
});

test("quick navigator shortcut does not replace another open modal", async (t) => {
  const { address, page } = await createAdminPage(t);

  await page.goto(`http://127.0.0.1:${address.port}/#dashboard`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator(".admin-shell").waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Choose directory", exact: true }).click();
  await assert.doesNotReject(() => page.getByRole("dialog", { name: "Choose directory" }).waitFor({ state: "visible" }));
  await page.keyboard.press("Control+k");
  assert.equal(await page.getByRole("dialog", { name: "Find a page" }).count(), 0);
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
  assert.equal(await page.locator(".admin-shell-nav-group-label:visible").count(), 0);
  await assert.doesNotReject(() => page.getByRole("button", { name: "Agents", exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("button", { name: "Flow", exact: true }).waitFor({ state: "visible" }));
  assert.equal(await page.locator('nav[aria-label="Admin sections"] .admin-shell-tab small:visible').count(), 0);

  await navTabs.filter({ hasText: "Approvals" }).click();
  await page.waitForFunction(() => window.location.hash === "#approvals");
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Approvals" }).waitFor({ state: "visible" }));
  assert.equal(await page.evaluate((storageKey) => window.localStorage.getItem(storageKey), WORKFLOW_SIDEBAR_STORAGE_KEY), "true");
});

test("admin shell lock toggle syncs backend privilege state", async (t) => {
  const { address, page } = await createAdminPage(t);
  const privilegedRequests = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith("/api/admin/privileged/")) {
      privilegedRequests.push(pathname);
    }
  });
  await page.addInitScript(
    (storageKey) => window.sessionStorage.setItem(storageKey, "true"),
    PRIVILEGE_UNLOCKED_STORAGE_KEY,
  );

  await page.goto(`http://127.0.0.1:${address.port}/#dashboard`, {
    waitUntil: "domcontentloaded",
  });

  await assert.doesNotReject(() => page.getByRole("button", { name: "Unlock", exact: true }).waitFor({ state: "visible" }));
  assert.equal(
    await page.evaluate((storageKey) => window.sessionStorage.getItem(storageKey), PRIVILEGE_UNLOCKED_STORAGE_KEY),
    "false",
  );

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
  assert.deepEqual(privilegedRequests, ["/api/admin/privileged/unlock", "/api/admin/privileged/lock"]);
});

test("admin shell clears local privilege state while lock request is pending", async (t) => {
  let markLockRequestStarted = () => {};
  let releaseLockRequest = () => {};
  const lockRequestStarted = new Promise((resolve) => {
    markLockRequestStarted = resolve;
  });
  const lockRequestReleased = new Promise((resolve) => {
    releaseLockRequest = resolve;
  });
  t.after(() => releaseLockRequest());

  const { address, page } = await createAdminPage(t, {
    async handlePrivilegedRequest({ route, unlocked }) {
      if (!unlocked) {
        markLockRequestStarted();
        await lockRequestReleased;
      }
      await route.fulfill(
        jsonResponse({
          message: unlocked ? "Privileged operations unlocked." : "Privileged operations locked.",
          privileged_operations: {
            unlocked,
          },
        }),
      );
    },
  });

  await page.goto(`http://127.0.0.1:${address.port}/#dashboard`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByRole("button", { name: "Unlock", exact: true }).click();
  await assert.doesNotReject(() => page.getByRole("button", { name: "Lock", exact: true }).waitFor({ state: "visible" }));
  await page.getByRole("button", { name: "Lock", exact: true }).click();
  await lockRequestStarted;

  await assert.doesNotReject(() => page.getByRole("button", { name: "Unlock", exact: true }).waitFor({ state: "visible" }));
  assert.equal(
    await page.evaluate((storageKey) => window.sessionStorage.getItem(storageKey), PRIVILEGE_UNLOCKED_STORAGE_KEY),
    "false",
  );

  releaseLockRequest();
  await assert.doesNotReject(() => page.getByRole("button", { name: "Unlock", exact: true }).waitFor({ state: "visible" }));
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
      body: `<!doctype html>
        <title>Team Add-on</title>
        <main>Team Add-on <a href="/review-bundle.zip" download>Download review bundle</a></main>
        <script>
          window.addEventListener("message", (event) => {
            const data = event.data || {};
            if (data.type === "admin-addon-context") {
              document.documentElement.dataset.theme = data.theme;
              document.body.dataset.privilegedUnlocked =
                String(Boolean(data.privileged_operations?.unlocked));
            } else if (data.type === "admin-privileged-operations") {
              document.body.dataset.privilegedUnlocked =
                String(Boolean(data.privileged_operations?.unlocked));
            }
          });
          window.parent.postMessage({ type: "admin-addon-ready" }, window.location.origin);
        </script>`,
    });
  });
  await page.addInitScript(
    (storageKey) => window.localStorage.setItem(storageKey, "dark"),
    THEME_STORAGE_KEY,
  );
  await page.route("**/review-bundle.zip", async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        "content-disposition": 'attachment; filename="review-bundle.zip"',
      },
      body: "review bundle fixture",
    });
  });

  await page.goto(
    `http://127.0.0.1:${address.port}/#addon:team-addon?view=my-human-reviews&human_review_request_id=review-1&directory_path=ignored&privileged_unlocked=true&unsafe=drop-me`,
    { waitUntil: "domcontentloaded" },
  );

  await assert.doesNotReject(() => page.getByRole("button", { name: /^Teams\b/ }).waitFor({ state: "visible" }));
  const frame = page.locator('iframe.admin-addon-frame[title="Teams"]');
  await frame.waitFor({ state: "visible" });
  assert.equal(
    await frame.getAttribute("sandbox"),
    "allow-downloads allow-forms allow-modals allow-same-origin allow-scripts",
  );
  const initialFrameSrc = await frame.getAttribute("src");
  assert.ok(initialFrameSrc);
  const initialFrameUrl = new URL(initialFrameSrc, `http://127.0.0.1:${address.port}`);
  assert.equal(
    initialFrameUrl.pathname,
    "/addons/team-addon/",
  );
  assert.equal(initialFrameUrl.searchParams.get("directory_path"), "root");
  assert.equal(initialFrameUrl.searchParams.get("privileged_unlocked"), "false");
  assert.equal(initialFrameUrl.searchParams.get("view"), "my-human-reviews");
  assert.equal(initialFrameUrl.searchParams.get("human_review_request_id"), "review-1");
  assert.equal(initialFrameUrl.searchParams.get("unsafe"), null);
  await assert.doesNotReject(() => page.frameLocator('iframe[title="Teams"]').getByText("Team Add-on").waitFor({ state: "visible" }));
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.frameLocator('iframe[title="Teams"]').getByRole("link", { name: "Download review bundle" }).click(),
  ]);
  assert.equal(download.suggestedFilename(), "review-bundle.zip");
  await page
    .frameLocator('iframe[title="Teams"]')
    .locator('html[data-theme="dark"]')
    .waitFor({ state: "attached" });
  await page
    .frameLocator('iframe[title="Teams"]')
    .locator('body[data-privileged-unlocked="false"]')
    .waitFor({ state: "attached" });

  await page.getByRole("button", { name: "Unlock", exact: true }).click();
  await assert.doesNotReject(() => page.getByRole("button", { name: "Lock", exact: true }).waitFor({ state: "visible" }));
  await page
    .frameLocator('iframe[title="Teams"]')
    .locator('body[data-privileged-unlocked="true"]')
    .waitFor({ state: "attached" });
  await page.getByRole("button", { name: "Light mode", exact: true }).click();
  await page
    .frameLocator('iframe[title="Teams"]')
    .locator('html[data-theme="light"]')
    .waitFor({ state: "attached" });
  await page.getByRole("button", { name: "Lock", exact: true }).click();
  await page
    .frameLocator('iframe[title="Teams"]')
    .locator('body[data-privileged-unlocked="false"]')
    .waitFor({ state: "attached" });
  assert.equal(await frame.getAttribute("src"), initialFrameSrc);
  assert.equal(addonFrameRequests, 1);

  await page.evaluate(() => {
    window.location.hash =
      "#addon:team-addon?view=my-human-reviews&human_review_request_id=review-2&unsafe=still-drop-me";
  });
  await page.waitForFunction(() => {
    const src = document
      .querySelector('iframe.admin-addon-frame[title="Teams"]')
      ?.getAttribute("src");
    return Boolean(src && new URL(src, window.location.origin).searchParams.get("human_review_request_id") === "review-2");
  });
  const updatedFrameSrc = await frame.getAttribute("src");
  assert.notEqual(updatedFrameSrc, initialFrameSrc);
  const updatedFrameUrl = new URL(updatedFrameSrc, `http://127.0.0.1:${address.port}`);
  assert.equal(updatedFrameUrl.searchParams.get("view"), "my-human-reviews");
  assert.equal(updatedFrameUrl.searchParams.get("human_review_request_id"), "review-2");
  assert.equal(updatedFrameUrl.searchParams.get("unsafe"), null);
  assert.equal(addonFrameRequests, 2);
});

test("admin shell gives embedded add-ons a usable mobile viewport", async (t) => {
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
  await page.route("**/addons/team-addon/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>Team Add-on</title><main>Team Add-on mobile viewport</main>",
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`http://127.0.0.1:${address.port}/#addon:team-addon`, {
    waitUntil: "domcontentloaded",
  });

  const frame = page.locator('iframe.admin-addon-frame[title="Teams"]');
  await frame.waitFor({ state: "visible" });
  const frameBox = await frame.boundingBox();
  assert.ok(frameBox, "expected mobile add-on frame bounds");
  assert.ok(frameBox.height >= 670, `expected a usable mobile frame height, got ${frameBox.height}`);
  assert.ok(frameBox.width <= 390, `expected frame to stay within the mobile viewport, got ${frameBox.width}`);
});

test("Team add-on session navigation opens the selected session", async (t) => {
  const session = {
    session_id: "session-team-worker",
    vm_id: null,
    model: "gpt-5.4",
    directory_path: "root",
    expires_at: "2026-06-18T12:00:00Z",
    created_at: "2026-06-17T12:00:00Z",
    is_expired: false,
  };
  const { address, page } = await createAdminPage(t, {
    sessions: [session],
    addons: [
      {
        addon_id: "team-addon",
        display_name: "Team Add-on",
        tab_key: "addon:team-addon",
        label: "Teams",
        short_label: "Teams",
        description: "Team-oriented add-on surface.",
        api_base_path: "/api/addons/team-addon",
        ui_path: "/addons/team-addon/",
      },
    ],
  });
  await page.route("**/addons/team-addon/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: `
        <button type="button" id="open-session">Open session</button>
        <script>
          document.querySelector("#open-session").addEventListener("click", () => {
            window.parent.postMessage(
              { type: "admin-navigate-to-session", session_id: "session-team-worker" },
              window.location.origin,
            );
          });
        </script>
      `,
    });
  });

  await page.goto(`http://127.0.0.1:${address.port}/#addon:team-addon`, {
    waitUntil: "domcontentloaded",
  });
  await page.frameLocator(".admin-addon-frame").getByRole("button", { name: "Open session" }).click();
  await page.waitForFunction(() => window.location.hash === "#sessions?session_id=session-team-worker");
  await page.getByRole("heading", { name: "Sessions" }).waitFor({ state: "visible" });
  await page.locator(".session-list-item.selected", { hasText: "session-team-worker" }).waitFor({ state: "visible" });
});

test("admin shell shows unread user notifications from the header", async (t) => {
  const { address, page } = await createAdminPage(t, {
    addons: [
      {
        addon_id: "team-addon",
        display_name: "Team Add-on",
        tab_key: "addon:team-addon",
        label: "Teams",
        short_label: "Teams",
        description: "Team-oriented add-on surface.",
        api_base_path: "/api/addons/team-addon",
        ui_path: "/addons/team-addon/",
      },
    ],
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
          metadata: { conversation_id: "conversation-1" },
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
  await page.getByRole("menuitem", { name: /New Team chat message/ }).click();
  await page.waitForFunction(
    () => window.location.hash === "#addon:team-addon?view=chat&conversation_id=conversation-1",
  );
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
    assert.equal(await page.locator(".admin-shell-nav-group-label:visible").count(), 0);
    assert.equal(await page.locator('nav[aria-label="Admin sections"] .admin-shell-tab small:visible').count(), 0);

    await assert.doesNotReject(() => page.getByRole("button", { name: "Find page", exact: true }).waitFor({ state: "visible" }));
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
