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

function makeSessionRecord(overrides = {}) {
  const effectiveToolPolicy = {
    allowed_tools: ["prompt_agent"],
    inherited_approval_required_tools: [],
    approval_required_tools: ["prompt_agent"],
    approval_tool_settings: {},
    mcp_tool_policies: {
      prompt_agent: {
        max_ttl_seconds: 7200,
        max_uses: 3,
        require_exact_payload: true,
      },
    },
    tool_argument_policies: {},
    ...overrides.effective_tool_policy,
  };
  return {
    session_id: "session-1",
    vm_id: "vm-1",
    model: "gpt-5.4",
    directory_path: "root/team",
    allowed_tools: ["prompt_agent"],
    approval_required_tools: ["prompt_agent"],
    approval_tool_settings: {},
    approval_notification_target_ids: ["notify-target-1"],
    approval_notification_target_options: [
      {
        value: "notify-target-1",
        label: "Notify target",
      },
    ],
    allowed_script_names: ["sync-session", "deploy-session"],
    discord_channel_allowlist: ["discord-channel-1", "communication-target-9"],
    teams_channel_allowlist: ["team-1/channel-2", "chat:chat-7", "communication-target-3"],
    communication_file_receive_enabled: false,
    communication_file_send_enabled: false,
    communication_file_max_bytes: null,
    communication_file_allowed_mime_types: ["text/plain", "application/pdf"],
    communication_file_allowed_extensions: [".txt"],
    communication_file_blocked_extensions: [".exe"],
    communication_file_require_approval_for_send: false,
    web_search_enabled: false,
    expires_at: "2026-05-18T12:00:00Z",
    created_at: "2026-05-17T12:00:00Z",
    metadata: {
      ticket: "INC-123",
      labels: {
        team: "ops",
      },
      reviewers: ["alice", "bob"],
    },
    issue_number: null,
    is_expired: false,
    ...overrides,
    effective_tool_policy: {
      ...effectiveToolPolicy,
      ...(overrides.effective_tool_policy ?? {}),
      approval_tool_settings: {
        ...effectiveToolPolicy.approval_tool_settings,
        ...((overrides.effective_tool_policy ?? {}).approval_tool_settings ?? {}),
      },
      mcp_tool_policies: {
        ...effectiveToolPolicy.mcp_tool_policies,
        ...((overrides.effective_tool_policy ?? {}).mcp_tool_policies ?? {}),
      },
      tool_argument_policies: {
        ...effectiveToolPolicy.tool_argument_policies,
        ...((overrides.effective_tool_policy ?? {}).tool_argument_policies ?? {}),
      },
    },
  };
}

async function createSessionsSmokeHarness(t, sessionRecord, routeAssertions = {}, overrides = {}) {
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
  const allowedToolsBodies = [];
  const policyBodies = [];
  const sessionListResponse = {
    items: [sessionRecord],
    active_items: [sessionRecord],
    expired_items: [],
    counts: {
      total: 1,
      active: 1,
      expired: 0,
    },
  };
  const sessionDetailResponse = {
    session: sessionRecord,
    wake_schedules: [],
    summary_json: {},
  };
  const sessionCatalogResponse = overrides.sessionCatalogResponse ?? {
    tool_options: [
      {
        value: "prompt_agent",
        label: "Prompt agent",
      },
    ],
    model_options: [
      {
        value: "claude-sonnet-4.5",
        label: "Claude Sonnet 4.5",
      },
      {
        value: "gemini-2.5-pro",
        label: "Gemini 2.5 Pro",
      },
    ],
    resume_mode_options: [
      {
        value: "same_context",
        label: "Same context",
      },
    ],
    renew_extension_presets: ["1d"],
  };
  const sessionResponse = overrides.sessionResponse ?? {
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
  const adminStateResponse = overrides.adminStateResponse ?? {
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
  const configurationStateResponse = overrides.configurationStateResponse ?? {
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

    if (pathname === "/api/admin/sessions/catalog") {
      await route.fulfill(jsonResponse(sessionCatalogResponse));
      return;
    }

    if (pathname === "/api/admin/sessions") {
      assert.equal(url.searchParams.get("include_expired"), "true");
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      await route.fulfill(jsonResponse(sessionListResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/session-1") {
      if (route.request().method() === "PATCH") {
        const body = JSON.parse(route.request().postData() ?? "{}");
        policyBodies.push(body);
        routeAssertions.assertPolicyBody?.(body);
        sessionRecord.approval_required_tools = body.approval_required_tools;
        sessionRecord.approval_tool_settings = body.approval_tool_settings;
        await route.fulfill(jsonResponse({ action: "updated", message: "Updated session policy.", session: sessionRecord }));
        return;
      }
      await route.fulfill(jsonResponse(sessionDetailResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/session-1/allowed-tools") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      allowedToolsBodies.push(body);
      routeAssertions.assertAllowedToolsBody?.(body);
      sessionRecord.allowed_tools = body.allowed_tools;
      await route.fulfill(jsonResponse({ action: "updated", message: "Updated allowed tools.", session: sessionRecord }));
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

  return {
    address,
    allowedToolsBodies,
    browser,
    consoleErrors,
    context,
    page,
    pageErrors,
    policyBodies,
    requestedEndpoints,
    server,
    sessionCatalogResponse,
    sessionDetailResponse,
    sessionListResponse,
    sessionRecord,
  };
}

test("admin shell sessions page clears approval gates when the allowed tool is removed", async (t) => {
  const {
    address,
    allowedToolsBodies,
    consoleErrors,
    page,
    pageErrors,
    policyBodies,
    requestedEndpoints,
    sessionRecord,
    server,
    browser,
    context,
  } = await createSessionsSmokeHarness(
    t,
    makeSessionRecord(),
    {
      assertPolicyBody(body) {
        assert.deepEqual(body.approval_notification_target_ids, sessionRecord.approval_notification_target_ids);
        assert.deepEqual(body.allowed_script_names, sessionRecord.allowed_script_names);
        assert.deepEqual(body.discord_channel_allowlist, sessionRecord.discord_channel_allowlist);
        assert.deepEqual(body.teams_channel_allowlist, sessionRecord.teams_channel_allowlist);
        assert.equal(body.communication_file_receive_enabled, false);
        assert.equal(body.communication_file_send_enabled, false);
        assert.equal(body.communication_file_require_approval_for_send, false);
        assert.deepEqual(body.communication_file_allowed_mime_types, sessionRecord.communication_file_allowed_mime_types);
        assert.deepEqual(body.communication_file_allowed_extensions, sessionRecord.communication_file_allowed_extensions);
        assert.deepEqual(body.communication_file_blocked_extensions, sessionRecord.communication_file_blocked_extensions);
        assert.equal(body.web_search_enabled, false);
        assert.deepEqual(body.metadata, sessionRecord.metadata);
        if (policyBodies.length === 1) {
          assert.deepEqual(body.approval_required_tools, ["prompt_agent"]);
          assert.deepEqual(body.approval_tool_settings, {
            prompt_agent: {
              ttl_seconds: 1800,
              max_uses: 2,
              payload_mode: "exact",
            },
          });
        } else {
          assert.deepEqual(body.approval_required_tools, []);
          assert.deepEqual(body.approval_tool_settings, {});
        }
      },
      assertAllowedToolsBody(body) {
        assert.deepEqual(body.allowed_tools, []);
      },
    },
  );

  await page.goto(`http://127.0.0.1:${address.port}/#sessions?session_id=session-1`, {
    waitUntil: "domcontentloaded",
  });

  const fieldDescription = (labelText) => page.locator(".field-stack").filter({ hasText: labelText });

  const navEntry = page
    .locator('nav[aria-label="Admin sections"] .admin-shell-tab')
    .filter({ hasText: "Sessions" });
  await ensureSectionsExpanded(page);
  await assert.doesNotReject(() => navEntry.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Sessions" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByLabel("Find session").waitFor({ state: "visible" }));
  assert.equal(await page.getByLabel("Find session").inputValue(), "session-1");
  assert.equal(await page.getByRole("combobox", { name: "State" }).inputValue(), "active");
  await assert.doesNotReject(() => page.locator(".session-list-item.selected").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.locator(".session-list-item.selected").getByText("session-1").waitFor({ state: "visible" }));
  await assert.doesNotReject(() =>
    fieldDescription("Allowed scripts")
      .getByText("One script name per row.")
      .waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    fieldDescription("Allowed scripts")
      .getByText("duplicates are removed.")
      .waitFor({ state: "visible" }),
  );
  assert.equal(await fieldDescription("Allowed scripts").locator("input").first().inputValue(), "sync-session");
  await assert.doesNotReject(() =>
    fieldDescription("Communication file max bytes")
      .getByText("Size in bytes. Enter a non-negative whole number; blank keeps no explicit session value and")
      .waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    fieldDescription("Communication file max bytes")
      .getByText("falls back to the effective policy. 0 allows only zero-byte files.")
      .waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    fieldDescription("Allowed MIME types")
      .getByText("One MIME type per row.")
      .waitFor({ state: "visible" }),
  );
  assert.equal(await fieldDescription("Allowed MIME types").locator("input").first().inputValue(), "text/plain");
  assert.equal(await fieldDescription("Allowed MIME types").locator("input").nth(1).inputValue(), "application/pdf");
  await assert.doesNotReject(() =>
    fieldDescription("Allowed extensions")
      .getByText("One suffix per row, such as")
      .waitFor({ state: "visible" }),
  );
  assert.equal(await fieldDescription("Allowed extensions").locator("input").first().inputValue(), ".txt");
  await assert.doesNotReject(() =>
    fieldDescription("Blocked extensions")
      .getByText("Entries must start with a")
      .waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    fieldDescription("Blocked extensions")
      .getByText("leading dot, blank rows are ignored, and duplicates are removed.")
      .waitFor({ state: "visible" }),
  );
  assert.equal(await fieldDescription("Blocked extensions").locator("input").first().inputValue(), ".exe");
  await assert.doesNotReject(() =>
    fieldDescription("Metadata")
      .getByText("Must be a JSON object.")
      .waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() => fieldDescription("Metadata").getByText("Nested values are edited as JSON snippets").waitFor({ state: "visible" }));
  const metadataSection = fieldDescription("Metadata");
  const labelsRow = metadataSection.locator(".structured-input-json-row").nth(1);
  await assert.doesNotReject(() => labelsRow.waitFor({ state: "visible" }));
  assert.equal(await labelsRow.locator("input").first().inputValue(), "labels");
  const labelsValueInput = labelsRow.locator("textarea").first();
  const labelsValueText = await labelsValueInput.inputValue();
  assert.match(labelsValueText, /"team": "ops"/);
  const modelInput = page.locator('input[list="session-model-suggestions"]');
  assert.equal(await modelInput.inputValue(), "gpt-5.4");
  assert.deepEqual(
    await page.locator("#session-model-suggestions option").evaluateAll((options) =>
      options.map((option) => ({
        value: option.getAttribute("value"),
        label: option.textContent?.trim() ?? "",
      })),
    ),
    [
      {
        value: "claude-sonnet-4.5",
        label: "Claude Sonnet 4.5",
      },
      {
        value: "gemini-2.5-pro",
        label: "Gemini 2.5 Pro",
      },
    ],
  );
  await assert.doesNotReject(() => page.getByText("No wake schedules configured.").waitFor({ state: "visible" }));
  await page.locator(".session-tool-group-summary").filter({ hasText: "Agent control" }).click();
  const promptAgentGroup = page.locator("details.session-tool-group[open]").filter({ hasText: "Agent control" });
  const promptAgentRow = promptAgentGroup.locator(".session-tool-row").filter({ hasText: "Prompt agent" });
  await assert.doesNotReject(() => promptAgentRow.waitFor({ state: "visible" }));

  const allowedTool = promptAgentRow.locator(".session-tool-row-main input[type='checkbox']").first();
  const approvalGate = promptAgentRow.locator(".session-tool-approval-row input[type='checkbox']").first();
  const saveButton = page.getByRole("button", { name: "Save changes" });
  const allowedExtensionsSection = fieldDescription("Allowed extensions");
  const allowedExtensionsRow = allowedExtensionsSection.locator(".string-list-selector-row").first();
  const allowedExtensionsInput = allowedExtensionsRow.locator("input").first();
  const approvalSettingsButton = promptAgentRow.getByRole("button", { name: "Configure approval settings for Prompt agent" });

  assert.equal(await allowedTool.isChecked(), true);
  assert.equal(await approvalGate.isChecked(), true);
  assert.equal(await approvalGate.isDisabled(), false);

  await approvalSettingsButton.click();
  const approvalSettingsDialog = page.getByRole("dialog", { name: "Prompt agent" });
  await assert.doesNotReject(() => approvalSettingsDialog.waitFor({ state: "visible" }));
  const modalText = await approvalSettingsDialog.innerText();
  assert.match(modalText, /grant expiry from parent policy/i);
  assert.match(modalText, /use limit from parent policy/i);
  assert.match(modalText, /argument matching from parent policy/i);
  assert.match(modalText, /grant expiry capped by policy maximum/i);
  assert.match(modalText, /use limit capped by policy maximum/i);
  assert.match(modalText, /argument matching capped by policy maximum/i);
  assert.doesNotMatch(modalText, /TTL seconds|Inherited TTL|Hard TTL|Inherited uses|Hard uses|Payload clamp|Exact payload/i);

  await approvalSettingsDialog.getByText("Grant expires after (seconds)", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip")
      .filter({ hasText: "How long the approval grant remains valid before it expires." })
      .waitFor({ state: "visible" }),
  );
  await approvalSettingsDialog.getByText("Max uses (blank = unlimited)", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip")
      .filter({ hasText: "How many times this approval grant can be reused. Leave blank for unlimited reuse unless the policy sets a maximum." })
      .waitFor({ state: "visible" }),
  );
  await approvalSettingsDialog.getByText("Argument matching", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip")
      .filter({ hasText: "Exact arguments only reuse the grant when submitted arguments match the approved arguments. May vary allows future calls with different arguments within the same grant limits." })
      .waitFor({ state: "visible" }),
  );

  const ttlInput = approvalSettingsDialog.getByLabel("Grant expires after (seconds)");
  const usesInput = approvalSettingsDialog.getByLabel("Max uses (blank = unlimited)");
  const matchingSelect = approvalSettingsDialog.getByLabel("Argument matching");
  assert.equal(await ttlInput.inputValue(), "3600");
  assert.equal(await usesInput.inputValue(), "1");
  assert.equal(await matchingSelect.inputValue(), "exact");
  assert.equal(await matchingSelect.isDisabled(), true);

  await ttlInput.fill("1800");
  await ttlInput.blur();
  await usesInput.fill("2");
  await usesInput.blur();
  await approvalSettingsDialog.getByRole("button", { name: "Done" }).first().click();
  await assert.doesNotReject(() => approvalSettingsDialog.waitFor({ state: "hidden" }));
  const firstPolicySave = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/admin/sessions/session-1" && response.request().method() === "PATCH",
  );
  await saveButton.evaluate((button) => button.click());
  await firstPolicySave;
  assert.equal(policyBodies.length, 1);
  assert.deepEqual(policyBodies[0].approval_tool_settings, {
    prompt_agent: {
      ttl_seconds: 1800,
      max_uses: 2,
      payload_mode: "exact",
    },
  });

  const policyBodiesBeforeInvalidSave = policyBodies.length;
  await labelsValueInput.fill("{bad");
  await saveButton.evaluate((button) => button.click());
  await assert.doesNotReject(() =>
    metadataSection.locator(".structured-input-error").first().waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    page.locator(".callout.error-callout").filter({ hasText: "Metadata row 2 contains invalid JSON" }).first().waitFor({ state: "visible" }),
  );
  assert.equal(policyBodies.length, policyBodiesBeforeInvalidSave);
  await labelsValueInput.fill(labelsValueText);

  await allowedExtensionsInput.fill("txt");
  await assert.doesNotReject(() =>
    allowedExtensionsSection.locator(".structured-input-error").filter({ hasText: "Extension must start with a leading dot." }).waitFor({ state: "visible" }),
  );
  const policyBodiesBeforeExtensionSave = policyBodies.length;
  await saveButton.evaluate((button) => button.click());
  await assert.doesNotReject(() =>
    page.locator(".callout.error-callout").filter({ hasText: "Allowed extensions row 1 must start with a leading dot." }).first().waitFor({ state: "visible" }),
  );
  assert.equal(policyBodies.length, policyBodiesBeforeExtensionSave);
  await allowedExtensionsInput.fill(".txt");
  await assert.doesNotReject(() =>
    allowedExtensionsSection.locator(".structured-input-error").filter({ hasText: "Extension must start with a leading dot." }).waitFor({ state: "hidden" }),
  );

  await allowedTool.uncheck();
  await page.waitForFunction(() => {
    const allowed = document.querySelector("details.session-tool-group[open] .session-tool-row-main input[type='checkbox']");
    const approval = document.querySelector("details.session-tool-group[open] .session-tool-approval-row input[type='checkbox']");
    return (
      allowed instanceof HTMLInputElement &&
      approval instanceof HTMLInputElement &&
      allowed.checked === false &&
      approval.checked === false
    );
  });
  assert.equal(await approvalGate.isChecked(), false);
  assert.equal(await approvalGate.isDisabled(), true);

  const secondToolsSave = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/admin/sessions/session-1/allowed-tools" && response.request().method() === "PUT",
  );
  const secondPolicySave = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/admin/sessions/session-1" && response.request().method() === "PATCH",
  );
  await saveButton.evaluate((button) => button.click());
  await Promise.all([secondToolsSave, secondPolicySave]);
  await assert.doesNotReject(() =>
    page.locator(".callout.success-callout").filter({ hasText: "Tools and policy saved." }).waitFor({ state: "visible" }),
  );
  assert.equal(await page.locator(".session-tools-panel .success-box").count(), 0);

  assert.equal(await page.evaluate(() => window.location.hash), "#sessions?session_id=session-1");
  assert.equal(await navEntry.count(), 1);
  assert.equal(await allowedToolsBodies.length, 1);
  assert.equal(await policyBodies.length, 2);
  assert.deepEqual(policyBodies[1].approval_required_tools, []);
  assert.deepEqual(policyBodies[1].approval_tool_settings, {});
  assert.deepEqual(sessionRecord.allowed_tools, []);
  assert.deepEqual(sessionRecord.approval_required_tools, []);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/sessions/session-1/allowed-tools"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/sessions/session-1"));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});

test("admin shell sessions page discards approval settings edits without saving them to the session", async (t) => {
  const sessionRecord = makeSessionRecord({
    effective_tool_policy: {
      mcp_tool_policies: {
        prompt_agent: {
          max_ttl_seconds: 7200,
          max_uses: 3,
          require_exact_payload: false,
        },
      },
    },
  });
  const {
    address,
    allowedToolsBodies,
    consoleErrors,
    page,
    pageErrors,
    policyBodies,
    requestedEndpoints,
  } = await createSessionsSmokeHarness(t, sessionRecord);

  await page.goto(`http://127.0.0.1:${address.port}/#sessions?session_id=session-1`, {
    waitUntil: "domcontentloaded",
  });

  await ensureSectionsExpanded(page);
  await page.locator(".session-tool-group-summary").filter({ hasText: "Agent control" }).click();
  const promptAgentGroup = page.locator("details.session-tool-group[open]").filter({ hasText: "Agent control" });
  const promptAgentRow = promptAgentGroup.locator(".session-tool-row").filter({ hasText: "Prompt agent" });
  await assert.doesNotReject(() => promptAgentRow.waitFor({ state: "visible" }));

  await page.getByRole("button", { name: "Configure approval settings for Prompt agent" }).click();
  const approvalModal = page.getByRole("dialog", { name: "Prompt agent" });
  await assert.doesNotReject(() => approvalModal.waitFor({ state: "visible" }));

  const ttlInput = approvalModal.getByLabel("Grant expires after (seconds)");
  const payloadSelect = approvalModal.getByLabel("Argument matching");
  await ttlInput.fill("999");
  await payloadSelect.selectOption("any");
  await page.getByRole("button", { name: "Discard changes" }).click();
  await assert.doesNotReject(() => approvalModal.waitFor({ state: "hidden" }));

  const saveButton = page.getByRole("button", { name: "Save changes" });
  assert.equal(await saveButton.isDisabled(), true);
  assert.equal(allowedToolsBodies.length, 0);
  assert.equal(policyBodies.length, 0);

  assert.ok(requestedEndpoints.includes("/api/admin/sessions/session-1"));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});

test("admin shell sessions page reset clears an existing approval settings override", async (t) => {
  const permissiveApprovalSettings = {
    ttl_seconds: 86400,
    max_uses: null,
    payload_mode: "any",
  };
  const {
    address,
    consoleErrors,
    page,
    pageErrors,
    policyBodies,
    requestedEndpoints,
  } = await createSessionsSmokeHarness(
    t,
    makeSessionRecord({
      approval_tool_settings: {
        prompt_agent: permissiveApprovalSettings,
      },
      effective_tool_policy: {
        approval_tool_settings: {
          prompt_agent: permissiveApprovalSettings,
        },
      },
    }),
    {
      assertPolicyBody(body) {
        assert.deepEqual(body.approval_required_tools, ["prompt_agent"]);
        assert.deepEqual(body.approval_tool_settings, {});
        assert.equal(Object.hasOwn(body.approval_tool_settings, "prompt_agent"), false);
      },
    },
  );

  await page.goto(`http://127.0.0.1:${address.port}/#sessions?session_id=session-1`, {
    waitUntil: "domcontentloaded",
  });

  await ensureSectionsExpanded(page);
  await page.locator(".session-tool-group-summary").filter({ hasText: "Agent control" }).click();
  const promptAgentGroup = page.locator("details.session-tool-group[open]").filter({ hasText: "Agent control" });
  const promptAgentRow = promptAgentGroup.locator(".session-tool-row").filter({ hasText: "Prompt agent" });
  await assert.doesNotReject(() => promptAgentRow.waitFor({ state: "visible" }));

  await page.getByRole("button", { name: "Configure approval settings for Prompt agent" }).click();
  const approvalModal = page.getByRole("dialog", { name: "Prompt agent" });
  await assert.doesNotReject(() => approvalModal.waitFor({ state: "visible" }));
  await page.waitForFunction(() => {
    const ttlLabel = Array.from(document.querySelectorAll("label.field-stack")).find((label) =>
      label.textContent?.includes("TTL seconds"),
    );
    const ttlInput = ttlLabel?.querySelector("input");
    return ttlInput instanceof HTMLInputElement && ttlInput.value === "86400";
  });
  assert.equal(await page.getByLabel("TTL seconds").inputValue(), "86400");
  assert.equal(await page.getByLabel("Payload").inputValue(), "any");

  await page.getByRole("button", { name: "Reset to inherited defaults" }).click();
  await assert.doesNotReject(() => approvalModal.waitFor({ state: "hidden" }));

  const saveButton = page.getByRole("button", { name: "Save changes" });
  assert.equal(await saveButton.isDisabled(), false);
  await saveButton.evaluate((button) => button.click());
  await assert.doesNotReject(() =>
    page.locator(".callout.success-callout").filter({ hasText: "Policy saved." }).waitFor({ state: "visible" }),
  );

  assert.equal(policyBodies.length, 1);
  assert.deepEqual(policyBodies[0].approval_tool_settings, {});
  assert.ok(requestedEndpoints.includes("/api/admin/sessions/session-1"));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});

test("admin shell sessions page exposes a tooltip for a disabled notification target add control", async (t) => {
  const sessionRecord = makeSessionRecord({
    approval_notification_target_ids: ["ops-notify"],
    approval_notification_target_options: [
      {
        value: "ops-notify",
        label: "Ops notify",
      },
    ],
  });
  const {
    address,
    page,
    pageErrors,
    consoleErrors,
  } = await createSessionsSmokeHarness(t, sessionRecord);

  await page.goto(`http://127.0.0.1:${address.port}/#sessions?session_id=session-1`, {
    waitUntil: "domcontentloaded",
  });

  await ensureSectionsExpanded(page);
  await page.locator(".session-tool-group-summary").filter({ hasText: "Agent control" }).click();
  const addTargetButton = page.getByRole("button", { name: "Add notification target" });
  assert.equal(await addTargetButton.isDisabled(), true);

  const disabledReason = "All notify-capable targets are already selected.";
  await page.getByLabel(disabledReason).hover();
  await assert.doesNotReject(() =>
    page.locator('[role="tooltip"]').filter({ hasText: disabledReason }).waitFor({ state: "visible" }),
  );

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});

test("admin shell sessions page shows wake schedule loading while the selected session detail is pending", async (t) => {
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

  let resolveDetail;
  const detailGate = new Promise((resolve) => {
    resolveDetail = resolve;
  });

  const sessionRecord = {
    session_id: "session-1",
    vm_id: "vm-1",
    model: "custom-model",
    directory_path: "root/team",
    allowed_tools: [],
    approval_required_tools: [],
    effective_tool_policy: {
      allowed_tools: [],
      inherited_approval_required_tools: [],
      approval_required_tools: [],
      approval_tool_settings: {},
      mcp_tool_policies: {},
      tool_argument_policies: {},
    },
    allowed_script_names: [],
    discord_channel_allowlist: [],
    teams_channel_allowlist: [],
    communication_file_receive_enabled: false,
    communication_file_send_enabled: false,
    communication_file_max_bytes: null,
    communication_file_allowed_mime_types: [],
    communication_file_allowed_extensions: [],
    communication_file_blocked_extensions: [],
    communication_file_require_approval_for_send: false,
    web_search_enabled: false,
    expires_at: "2026-05-18T12:00:00Z",
    created_at: "2026-05-17T12:00:00Z",
    metadata: {},
    issue_number: null,
    is_expired: false,
  };
  const sessionListResponse = {
    items: [sessionRecord],
    active_items: [sessionRecord],
    expired_items: [],
    counts: {
      total: 1,
      active: 1,
      expired: 0,
    },
  };
  const sessionDetailResponse = {
    session: sessionRecord,
    wake_schedules: [],
    summary_json: {},
  };
  const sessionCatalogResponse = {
    tool_options: [],
    model_options: [],
    resume_mode_options: [
      {
        value: "same_context",
        label: "Same context",
      },
    ],
    renew_extension_presets: ["1d"],
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
    if (resolveDetail) {
      resolveDetail();
    }
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;

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
      await route.fulfill(jsonResponse(adminStateResponse));
      return;
    }

    if (pathname === "/api/configuration/state") {
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/catalog") {
      await route.fulfill(jsonResponse(sessionCatalogResponse));
      return;
    }

    if (pathname === "/api/admin/sessions") {
      await route.fulfill(jsonResponse(sessionListResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/session-1") {
      await detailGate;
      await route.fulfill(jsonResponse(sessionDetailResponse));
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

  await page.goto(`http://127.0.0.1:${address.port}/#sessions?session_id=session-1`, {
    waitUntil: "domcontentloaded",
  });

  await assert.doesNotReject(() => page.getByRole("heading", { name: "Sessions" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Loading wake schedules...").waitFor({ state: "visible" }));

  resolveDetail();

  await assert.doesNotReject(() => page.getByText("No wake schedules configured.").waitFor({ state: "visible" }));

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});

test("admin shell sessions page shows runtime image details for libvirt, legacy, and container sessions", async (t) => {
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

  const explicitSession = {
    session_id: "libvirt-explicit",
    vm_id: "vm-explicit",
    model: "gpt-5.4",
    directory_path: "root/team",
    allowed_tools: [],
    approval_required_tools: [],
    allowed_script_names: [],
    discord_channel_allowlist: [],
    teams_channel_allowlist: [],
    communication_file_receive_enabled: false,
    communication_file_send_enabled: false,
    communication_file_max_bytes: null,
    communication_file_allowed_mime_types: [],
    communication_file_allowed_extensions: [],
    communication_file_blocked_extensions: [],
    communication_file_require_approval_for_send: false,
    web_search_enabled: false,
    expires_at: "2026-05-18T12:00:00Z",
    created_at: "2026-05-17T12:00:00Z",
    metadata: {
      libvirt_vm_template: {
        image_ref: "/var/lib/libvirt/images/agent-sandbox/dotnet-agent.qcow2",
        template_alias: "dotnet",
      },
    },
    runtime_image: {
      backend: "libvirt_vm",
      kind: "libvirt_vm_template",
      label: "Libvirt VM template",
      template_alias: "dotnet",
      image_ref: "dotnet-agent.qcow2",
      image_ref_sanitized: true,
      missing_reason: null,
    },
    issue_number: null,
    is_expired: false,
  };
  const legacySession = {
    session_id: "libvirt-legacy",
    vm_id: "vm-legacy",
    model: "gpt-5.4",
    directory_path: "root/team",
    allowed_tools: [],
    approval_required_tools: [],
    allowed_script_names: [],
    discord_channel_allowlist: [],
    teams_channel_allowlist: [],
    communication_file_receive_enabled: false,
    communication_file_send_enabled: false,
    communication_file_max_bytes: null,
    communication_file_allowed_mime_types: [],
    communication_file_allowed_extensions: [],
    communication_file_blocked_extensions: [],
    communication_file_require_approval_for_send: false,
    web_search_enabled: false,
    expires_at: "2026-05-18T12:00:00Z",
    created_at: "2026-05-17T12:00:00Z",
    metadata: { legacy: true },
    runtime_image: {
      backend: "libvirt_vm",
      kind: "libvirt_vm_template",
      label: "Legacy session: VM template metadata unavailable",
      template_alias: null,
      image_ref: null,
      image_ref_sanitized: false,
      missing_reason: "legacy_missing_libvirt_vm_template",
    },
    issue_number: null,
    is_expired: false,
  };
  const containerSession = {
    session_id: "container-session",
    vm_id: null,
    model: "gpt-5.4",
    directory_path: "root/team",
    allowed_tools: [],
    approval_required_tools: [],
    allowed_script_names: [],
    discord_channel_allowlist: [],
    teams_channel_allowlist: [],
    communication_file_receive_enabled: false,
    communication_file_send_enabled: false,
    communication_file_max_bytes: null,
    communication_file_allowed_mime_types: [],
    communication_file_allowed_extensions: [],
    communication_file_blocked_extensions: [],
    communication_file_require_approval_for_send: false,
    web_search_enabled: false,
    expires_at: "2026-05-18T12:00:00Z",
    created_at: "2026-05-17T12:00:00Z",
    metadata: {
      execution_backend: "container_worker_vm",
      container_worker: {
        image: "runner:latest",
        image_alias: "default",
      },
    },
    runtime_image: {
      backend: "container_worker_vm",
      kind: "container_worker_image",
      label: "Container worker image",
      image_alias: "default",
      image_ref: "runner:latest",
      missing_reason: null,
    },
    issue_number: null,
    is_expired: false,
    container_lifecycle: {
      state: "stopped",
      last_signal_at: "2026-05-18T12:10:00Z",
      actions: {
        cleanup: {
          supported: true,
          kind: "cleanup_container_session",
          label: "Cleanup session",
          reason: null,
        },
      },
    },
  };
  const sessionListResponse = {
    items: [explicitSession, legacySession, containerSession],
    active_items: [explicitSession, legacySession, containerSession],
    expired_items: [],
    counts: {
      total: 3,
      active: 3,
      expired: 0,
    },
  };
  const sessionDetailResponse = {
    session: explicitSession,
    wake_schedules: [],
    summary_json: {},
  };
  const sessionCatalogResponse = {
    tool_options: [],
    resume_mode_options: [],
    renew_extension_presets: ["1d"],
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
      await route.fulfill(jsonResponse(adminStateResponse));
      return;
    }

    if (pathname === "/api/configuration/state") {
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/catalog") {
      await route.fulfill(jsonResponse(sessionCatalogResponse));
      return;
    }

    if (pathname === "/api/admin/sessions") {
      await route.fulfill(jsonResponse(sessionListResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/libvirt-explicit") {
      await route.fulfill(jsonResponse(sessionDetailResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/libvirt-legacy") {
      await route.fulfill(
        jsonResponse({
          session: legacySession,
          wake_schedules: [],
          summary_json: {},
        }),
      );
      return;
    }

    if (pathname === "/api/admin/sessions/container-session") {
      await route.fulfill(
        jsonResponse({
          session: containerSession,
          wake_schedules: [],
          summary_json: {},
        }),
      );
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

  await page.goto(`http://127.0.0.1:${address.port}/#sessions?session_id=libvirt-explicit`, {
    waitUntil: "domcontentloaded",
  });

  await assert.doesNotReject(() => page.getByRole("heading", { name: "Sessions" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "libvirt-explicit" }).waitFor({ state: "visible" }));
  await assert.match(await page.locator(".feature-content").innerText(), /VM template/i);
  await assert.match(await page.locator(".feature-content").innerText(), /dotnet/);
  await assert.match(await page.locator(".feature-content").innerText(), /VM image ref/i);
  await assert.match(await page.locator(".feature-content").innerText(), /dotnet-agent\.qcow2/);

  await page.getByLabel("Find session").fill("");
  await page.locator(".session-list-item").filter({ hasText: "libvirt-legacy" }).click();
  await assert.match(await page.locator(".feature-content").innerText(), /Legacy session: VM template metadata unavailable/);

  await page.locator(".session-list-item").filter({ hasText: "container-session" }).click();
  await assert.match(await page.locator(".feature-content").innerText(), /Container image/i);
  await assert.match(await page.locator(".feature-content").innerText(), /runner:latest/);
  const cleanupButton = page.getByRole("button", { name: "Clean up session" });
  await assert.doesNotReject(() => cleanupButton.waitFor({ state: "visible" }));
  assert.equal((await cleanupButton.innerText()).trim(), "");
  const cleanupButtonBox = await cleanupButton.boundingBox();
  assert.ok(cleanupButtonBox, "expected cleanup toolbar button to have a bounding box");
  assert.ok(cleanupButtonBox.width <= 40, `expected cleanup toolbar button to stay icon-sized, got ${cleanupButtonBox.width}`);

  await page.getByPlaceholder("session, VM, issue, model").fill("runner:latest");
  await assert.equal(await page.locator(".session-list-item").filter({ hasText: "container-session" }).count(), 1);
  await assert.equal(await page.locator(".session-list-item").filter({ hasText: "libvirt-explicit" }).count(), 0);
  await page.getByPlaceholder("session, VM, issue, model").fill("dotnet");
  await assert.equal(await page.locator(".session-list-item").filter({ hasText: "libvirt-explicit" }).count(), 1);
  await assert.equal(await page.locator(".session-list-item").filter({ hasText: "container-session" }).count(), 0);

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
