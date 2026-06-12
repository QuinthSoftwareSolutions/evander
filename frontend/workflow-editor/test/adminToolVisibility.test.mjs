import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer } from "vite";

const WORKFLOW_SIDEBAR_STORAGE_KEY = "admin-web:workflow:sections-collapsed";
const THEME_STORAGE_KEY = "admin-web:theme";

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  };
}

async function createAdminPage(t) {
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

  const requestedEndpoints = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    requestedEndpoints.push(`${pathname}${url.search}`);

    if (pathname === "/api/admin/branding") {
      await route.fulfill(jsonResponse({}));
      return;
    }

    if (pathname === "/api/auth/session") {
      await route.fulfill(
        jsonResponse({
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
        }),
      );
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
        }),
      );
      return;
    }

    if (pathname === "/api/configuration/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(
        jsonResponse({
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
        }),
      );
      return;
    }

    if (pathname === "/api/admin/sessions") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "false");
      assert.equal(url.searchParams.get("include_expired"), "true");
      await route.fulfill(
        jsonResponse({
          items: [
            {
              session_id: "session-1",
              directory_path: "root/team",
              vm_id: "vm-1",
              vm_provider: "libvirt",
              expires_at: "2026-06-12T13:00:00Z",
              is_expired: false,
              capability_profile: null,
              policy_metadata: {},
            },
            {
              session_id: "session-2",
              directory_path: "root/team",
              vm_id: "vm-2",
              vm_provider: "libvirt",
              expires_at: "2026-06-11T13:00:00Z",
              is_expired: true,
              capability_profile: "assistant",
              policy_metadata: {
                assistant_mutation_policy: "locked",
              },
            },
          ],
          active_items: [],
          expired_items: [],
          counts: {
            total: 2,
            active: 1,
            expired: 1,
          },
        }),
      );
      return;
    }

    if (pathname === "/api/admin/tool-visibility") {
      assert.equal(url.searchParams.get("session_id"), "session-1");
      assert.equal(url.searchParams.get("workflow_run_id"), "workflow-7");
      assert.equal(url.searchParams.get("mcp_tool_approval_lease_id"), "lease-9");
      assert.equal(url.searchParams.get("mcp_transport_session_id"), "transport-1");
      await route.fulfill(
        jsonResponse({
          session_context: {
            session_id: "session-1",
            directory_path: "root/team",
            vm_id: "vm-1",
            vm_provider: "libvirt",
            expires_at: "2026-06-12T13:00:00Z",
            is_expired: false,
            capability_profile: null,
            policy_metadata: {},
          },
          query_context: {
            workflow_run_id: "workflow-7",
            mcp_tool_approval_lease_id: "lease-9",
          },
          binding_context: {
            state: "bound",
            manifest_state: "stale_manifest_possible",
            transport_session_id: "transport-1",
            bound_session_id: "session-1",
            last_tools_list_at: "2026-06-12T12:30:00Z",
            last_visible_tool_fingerprint: "fingerprint-1",
            message: "Transport transport-1 is bound to session session-1. The latest served tool manifest may be stale after policy changes.",
          },
          tools: [
            {
              tool_name: "create_ado_backlog_item",
              label: "Create backlog item",
              category: "Azure DevOps",
              provider: "azure_devops",
              visibility_state: "visible",
              available: true,
              visible: true,
              execution_state: "denied_at_execution",
              source: "session.allowed_tools",
              reason: "Visible to the session by policy.",
              requires_approval: true,
              approval_settings: {
                ttl_seconds: 3600,
                max_uses: 1,
                payload_mode: "exact",
              },
              last_denied_at: "2026-06-12T12:05:00Z",
              decision: "denied",
              error_summary: "blocked by execution policy",
              request_id: "request-1",
              approval_request_id: "approval-1",
              workflow_run_id: "workflow-7",
              mcp_tool_approval_lease_id: "lease-9",
              related_links: [
                { tab: "sessions", label: "Session", value: "session-1" },
                { tab: "approvals", label: "Approval", value: "approval-1" },
              ],
            },
            {
              tool_name: "run_script",
              label: "Run script",
              category: "Agent control",
              provider: "agent",
              visibility_state: "hidden_by_policy",
              available: false,
              visible: false,
              source: "directory.mcp_tool_policies",
              reason: "Denied by directory policy.",
              requires_approval: false,
              related_links: [],
            },
            {
              tool_name: "assistant_create_workflow",
              label: "Create workflow",
              category: "Agent control",
              provider: "agent",
              visibility_state: "hidden_by_policy",
              available: false,
              visible: false,
              source: "session.metadata.capability_profile",
              reason: "Assistant tools are hidden when capability_profile is missing.",
              requires_approval: false,
              related_links: [],
            },
            {
              tool_name: "list_unread_feedback",
              label: "Unread feedback",
              category: "Feedback",
              provider: "feedback",
              visibility_state: "unavailable_to_session",
              available: false,
              visible: false,
              source: "session.metadata.pr_feedback_streams",
              reason: "Feedback streams are unavailable to this session.",
              requires_approval: false,
              related_links: [],
            },
          ],
          counts: {
            total: 4,
            visible: 1,
            hidden_by_policy: 2,
            unavailable_to_session: 1,
            denied_at_execution: 1,
            unknown: 0,
            requires_approval: 1,
          },
          notices: [
            {
              kind: "stale_client_manifest",
              message: "MCP clients may cache a client-side tool manifest until they reconnect or refresh, so a server-side policy change may not be visible immediately.",
            },
            {
              kind: "missing_capability_profile",
              message: "Session session-1 does not define metadata.capability_profile; assistant tools are hidden by policy.",
            },
          ],
        }),
      );
      return;
    }

    await route.fulfill(jsonResponse({ error: `Unexpected endpoint: ${pathname}` }, 404));
  });

  return { address, page, requestedEndpoints };
}

test("admin tool visibility renders the effective policy inspector", async (t) => {
  const { address, page, requestedEndpoints } = await createAdminPage(t);
  await page.addInitScript((storageKey) => window.localStorage.removeItem(storageKey), WORKFLOW_SIDEBAR_STORAGE_KEY);
  await page.addInitScript((storageKey) => window.localStorage.removeItem(storageKey), THEME_STORAGE_KEY);

  await page.goto(
    `http://127.0.0.1:${address.port}/#tool-visibility?session_id=session-1&workflow_run_id=workflow-7&mcp_tool_approval_lease_id=lease-9&mcp_transport_session_id=transport-1`,
    { waitUntil: "domcontentloaded" },
  );

  await assert.doesNotReject(() => page.getByRole("heading", { name: "Tool Visibility" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("session-1").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Visible").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Hidden by policy").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Unavailable to session").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Denied at execution").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Stale manifest possible").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("blocked by execution policy").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("MCP clients can cache a tool manifest on the client side").waitFor({ state: "visible" }));

  const bodyText = await page.locator("body").innerText();
  assert.ok(!bodyText.includes("visible-secret"));
  assert.ok(!bodyText.includes("visible-result-secret"));

  assert.ok(
    requestedEndpoints.includes(
      "/api/admin/sessions?directory_path=root&direct_only=false&include_expired=true",
    ),
  );
  assert.ok(
    requestedEndpoints.includes(
      "/api/admin/tool-visibility?session_id=session-1&workflow_run_id=workflow-7&mcp_tool_approval_lease_id=lease-9&mcp_transport_session_id=transport-1",
    ),
  );
});
