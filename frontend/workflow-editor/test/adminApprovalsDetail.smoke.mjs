import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer } from "vite";

import { ensureSectionsExpanded } from "./adminShellHelpers.mjs";

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  };
}

test("admin shell approvals detail renders intended effect and payload digest", async (t) => {
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
  const context = await browser.newContext({ viewport: { width: 960, height: 900 } });
  const page = await context.newPage();

  const requestedEndpoints = [];
  const approvalRequest = {
    request_id: "approval-1",
    session_id: "session-1",
    action_type: "mcp_tool_invocation",
    tool_name: "email_send_approved",
    payload_digest: "digest-approval-1",
    human_summary: "Approve MCP tool get_ado_backlog_item for session auto-dev-roadmap-lead-root-example-truman-auto-dev",
    intended_effect: "Send the approved email payload to the recipient.",
    directory_path: "root/team",
    vm_id: "vm-1",
    status: "pending",
    created_at: "2026-05-18T10:00:00Z",
    expires_at: "2026-05-18T11:00:00Z",
    payload: {
      tool_name: "email_send_approved",
      session_id: "session-1",
    },
    decision_history: [],
  };
  const approvalIndexResponse = {
    items: [approvalRequest],
    health: {
      total: 1,
      pending: 1,
      approved: 0,
      denied: 0,
      expired: 0,
      oldest_pending_age_seconds: 90,
    },
  };
  const approvalDetailResponse = {
    request: approvalRequest,
    redacted_payload: {
      tool_name: "email_send_approved",
      session_id: "session-1",
      secret: "<redacted>",
    },
    audit_context: [],
    age_seconds: 90,
    expires_in_seconds: 3_600,
    payload_summary: '{"body":"hello","token":"<redacted>"}',
    approval_diagnostics: {
      request_id: "approval-1",
      status: "pending",
      action_type: "mcp_tool_invocation",
      tool_name: "email_send_approved",
      payload_digest: "digest-approval-1",
      approver: "admin",
      decision_at: null,
      decision_reason: null,
      created_at: "2026-05-18T10:00:00Z",
      expires_at: "2026-05-18T11:00:00Z",
      age_seconds: 90,
      expires_in_seconds: 3_600,
      payload_summary: '{"body":"hello","token":"<redacted>"}',
      approved_payload: {
        body: "hello",
        token: "<redacted>",
      },
      requested_payload: {
        body: "hello",
        token: "<redacted>",
      },
      blocked_tool_call_diagnostics: [
        {
          approval_request_id: "approval-1",
          status: "blocked",
          rejection_kind: "payload_mismatch",
          reason: "Payload mismatch for approval-1",
          payload_match: false,
          approved_payload_digest: "digest-approval-1",
          attempted_payload_digest: "attempted-digest-1",
          payload_summary: '{"body":"hello","token":"<redacted>"}',
          approved_payload_summary: {
            body: "hello",
            token: "<redacted>",
          },
          attempted_payload_summary: {
            body: "hello-v2",
            token: "<redacted>",
            extra: "value",
          },
          differences: {
            changed: {
              entries: [
                {
                  path: "arguments.body",
                  approved_value: "hello",
                  attempted_value: "hello-v2",
                },
              ],
              truncated: false,
            },
            missing: {
              entries: [
                {
                  path: "arguments.optional_field",
                  approved_value: "needed",
                },
              ],
              truncated: false,
            },
            extra: {
              entries: [
                {
                  path: "arguments.trace_id",
                  attempted_value: "trace-1",
                },
              ],
              truncated: false,
            },
            truncated: false,
          },
        },
      ],
    },
  };
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
      unlocked: true,
    },
  };
  const adminStateResponse = {
    selected_directory_path: "root",
    directory_scoped_config_enabled: false,
    execution_backend: "libvirt_vm",
    role: "admin",
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

    if (pathname === "/api/admin/approvals") {
      assert.equal(url.searchParams.get("statuses"), null);
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "false");
      await route.fulfill(jsonResponse(approvalIndexResponse));
      return;
    }

    if (pathname === "/api/admin/approvals/approval-1") {
      await route.fulfill(jsonResponse(approvalDetailResponse));
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

  await page.goto(`http://127.0.0.1:${address.port}/#approvals?request_id=approval-1&decision=approve`, {
    waitUntil: "domcontentloaded",
  });

  const navEntry = page
    .locator('nav[aria-label="Admin sections"] .admin-shell-tab')
    .filter({ hasText: "Approvals" });
  await ensureSectionsExpanded(page);
  await assert.doesNotReject(() => navEntry.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Approvals" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "approval-1" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Summary" }).waitFor({ state: "visible" }));
  assert.equal(await page.getByRole("heading", { name: "Queue health" }).count(), 0);
  await assert.doesNotReject(() =>
    page.locator(".approval-review-summary p.muted-copy").filter({ hasText: "Intended effect:" }).waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    page.locator(".approval-review-summary").getByText("Send the approved email payload to the recipient.", { exact: true }).waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    page.locator(".approval-review-summary p.muted-copy").filter({ hasText: "Arguments hash:" }).waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    page.locator(".approval-review-summary").getByText("digest-approval-1", { exact: true }).waitFor({ state: "visible" }),
  );
  await page.locator(".approval-review-summary").getByText("digest-approval-1", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page
      .getByRole("tooltip")
      .filter({ hasText: "compact fingerprint of the submitted arguments" })
      .waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Approval payload diagnostic" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() =>
    page
      .getByText(
        "Diagnostic only: the diff explains why the payload was rejected, but it does not loosen the approval boundary.",
        {
          exact: true,
        },
      )
      .waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() => page.getByText("Mismatch", { exact: true }).first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Approved arguments" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Submitted arguments" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Rejection reason", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("arguments.body", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("arguments.optional_field", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("arguments.trace_id", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByLabel("Approver").waitFor({ state: "visible" }));
  await assert.doesNotReject(() =>
    page.waitForFunction(() => document.activeElement?.textContent === "Approve"),
  );
  const approvalCard = page.locator(".approval-list-item").first();
  await assert.doesNotReject(() => approvalCard.waitFor({ state: "visible" }));
  const badgeLayout = await approvalCard.evaluate((element) => {
    const titleRow = element.querySelector(".approval-list-item-title-row");
    const title = element.querySelector(".approval-list-item-title-row strong");
    const badge = element.querySelector(".approval-list-item-title-row .status-pill");
    if (!(titleRow instanceof HTMLElement) || !(title instanceof HTMLElement) || !(badge instanceof HTMLElement)) {
      return null;
    }
    const cardBox = element.getBoundingClientRect();
    const titleBox = title.getBoundingClientRect();
    const badgeBox = badge.getBoundingClientRect();
    return {
      cardClientWidth: element.clientWidth,
      cardRight: cardBox.right,
      cardScrollWidth: element.scrollWidth,
      titleRight: titleBox.right,
      badgeLeft: badgeBox.left,
      badgeRight: badgeBox.right,
    };
  });
  assert.ok(badgeLayout, "expected approval card title and status badge");
  assert.ok(
    badgeLayout.badgeRight <= badgeLayout.cardRight + 1,
    `approval status badge should stay inside the card: badgeRight=${badgeLayout.badgeRight}, cardRight=${badgeLayout.cardRight}`,
  );
  assert.ok(
    badgeLayout.cardScrollWidth <= badgeLayout.cardClientWidth + 1,
    `approval card should not create horizontal overflow: scrollWidth=${badgeLayout.cardScrollWidth}, clientWidth=${badgeLayout.cardClientWidth}`,
  );
  assert.ok(
    badgeLayout.titleRight <= badgeLayout.badgeLeft,
    `approval title should wrap before the badge column: titleRight=${badgeLayout.titleRight}, badgeLeft=${badgeLayout.badgeLeft}`,
  );

  const bodyText = await page.locator("body").innerText();
  assert.doesNotMatch(bodyText, /approved-secret/i);
  assert.doesNotMatch(bodyText, /attempted-secret/i);

  assert.equal(await page.evaluate(() => window.location.hash), "#approvals?request_id=approval-1&decision=approve");
  assert.equal(await navEntry.count(), 1);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/approvals/approval-1"));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});

test("admin shell approvals detail uses signed-in approver and offers revoke for valid approvals", async (t) => {
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
  const context = await browser.newContext({ viewport: { width: 960, height: 900 } });
  const page = await context.newPage();

  const requestedEndpoints = [];
  const approvalRequest = {
    request_id: "approval-1",
    session_id: "session-1",
    action_type: "mcp_tool_invocation",
    tool_name: "email_send_approved",
    payload_digest: "digest-approval-1",
    human_summary: "Approve sending the email draft.",
    intended_effect: "Send the approved email payload to the recipient.",
    directory_path: "root",
    vm_id: "vm-1",
    status: "approved",
    created_at: "2026-05-18T10:00:00Z",
    expires_at: "2026-05-18T11:00:00Z",
    approver: "Ops Lead",
    decision_at: "2026-05-18T10:05:00Z",
    decision_reason: "safe",
    payload: {
      tool_name: "email_send_approved",
      session_id: "session-1",
    },
    decision_history: [
      {
        status: "approved",
        approver: "Ops Lead",
        reason: "safe",
        decided_at: "2026-05-18T10:05:00Z",
      },
    ],
  };
  const approvalIndexResponse = {
    items: [approvalRequest],
    health: {
      total: 1,
      pending: 0,
      approved: 1,
      denied: 0,
      expired: 0,
      oldest_pending_age_seconds: null,
    },
  };
  const approvalDetailResponse = {
    request: approvalRequest,
    redacted_payload: {
      tool_name: "email_send_approved",
      session_id: "session-1",
      secret: "<redacted>",
    },
    audit_context: [],
    age_seconds: 300,
    expires_in_seconds: 3_300,
  };
  const sessionResponse = {
    auth_mode: "local_password",
    auth_required: true,
    authenticated: true,
    username: "taylor",
    display_name: "Taylor Operator",
    role: "admin",
    can_manage_users: true,
    session_id: "session-123",
    available_login_methods: ["password"],
    azure_sso_login_url: null,
    privileged_operations: {
      unlocked: true,
    },
  };
  const adminStateResponse = {
    selected_directory_path: "root",
    directory_scoped_config_enabled: false,
    execution_backend: "libvirt_vm",
    role: "admin",
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
  let revokePayload = null;

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    requestedEndpoints.push(`${route.request().method()} ${pathname}${url.search}`);

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

    if (pathname === "/api/admin/approvals") {
      assert.equal(url.searchParams.get("statuses"), null);
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "false");
      await route.fulfill(jsonResponse(approvalIndexResponse));
      return;
    }

    if (pathname === "/api/admin/approvals/approval-1/revoke") {
      revokePayload = route.request().postDataJSON();
      assert.deepEqual(revokePayload, { reason: "No longer safe." });
      approvalRequest.status = "denied";
      approvalRequest.approver = "Taylor Operator";
      approvalRequest.decision_reason = "No longer safe.";
      approvalRequest.decision_at = "2026-05-18T10:10:00Z";
      approvalRequest.decision_history.push({
        status: "denied",
        approver: "Taylor Operator",
        reason: "No longer safe.",
        decided_at: "2026-05-18T10:10:00Z",
      });
      approvalDetailResponse.expires_in_seconds = 3_000;
      await route.fulfill(jsonResponse({ request: approvalRequest }));
      return;
    }

    if (pathname === "/api/admin/approvals/approval-1") {
      await route.fulfill(jsonResponse(approvalDetailResponse));
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

  await page.goto(`http://127.0.0.1:${address.port}/#approvals?request_id=approval-1`, {
    waitUntil: "domcontentloaded",
  });

  await ensureSectionsExpanded(page);
  await assert.doesNotReject(() => page.getByRole("heading", { name: "approval-1" }).waitFor({ state: "visible" }));
  assert.equal(await page.getByLabel("Approver").count(), 0);
  await assert.doesNotReject(() =>
    page.getByText("Decision will be recorded as Taylor Operator.", { exact: true }).waitFor({ state: "visible" }),
  );
  assert.equal(await page.getByRole("button", { name: "Approve", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "Deny", exact: true }).count(), 0);
  const revokeButton = page.getByRole("button", { name: "Revoke" });
  await assert.doesNotReject(() => revokeButton.waitFor({ state: "visible" }));
  await revokeButton.hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("Revoking invalidates this grant for all future use.", { exact: true }).waitFor({ state: "visible" }),
  );

  await page.getByLabel("Reason").fill("No longer safe.");
  await revokeButton.click();
  await assert.doesNotReject(() => page.getByText("Revoked approval-1.", { exact: true }).waitFor({ state: "visible" }));

  assert.deepEqual(revokePayload, { reason: "No longer safe." });
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "POST /api/admin/approvals/approval-1/revoke"));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
