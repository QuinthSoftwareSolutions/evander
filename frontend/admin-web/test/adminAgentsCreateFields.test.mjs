import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test, { before } from "node:test";
import { createServer } from "vite";

let buildCreatePayload;
let emptyCreateForm;
let parseDurationSeconds;
before(async () => {
  // Load the real module through the application resolver without opening a port.
  const server = await createServer({
    server: { middlewareMode: true },
    appType: "custom",
    logLevel: "error",
  });
  try {
    ({ buildCreatePayload, emptyCreateForm, parseDurationSeconds } = await server.ssrLoadModule(
      "/src/features/agents/agentCreateShared.ts",
    ));
  } finally {
    await server.close();
  }
}, { timeout: 10000 });

const agentsPage = readFileSync(
  new URL("../src/features/agents/AgentsPage.tsx", import.meta.url),
  "utf8",
);
const agentCreateDialog = readFileSync(
  new URL("../src/features/agents/AgentCreateDialog.tsx", import.meta.url),
  "utf8",
);
const subscriptionsPage = readFileSync(
  new URL("../src/features/subscriptions/SubscriptionsPage.tsx", import.meta.url),
  "utf8",
);

function sourceBetweenIn(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, `start marker not found: ${startMarker}`);
  const end = source.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(end, -1, `end marker not found: ${endMarker}`);
  assert.ok(end > start, `end marker must follow start marker: ${endMarker}`);
  return source.slice(start, end);
}

test("container Codex usage can switch per agent without transporting credentials", () => {
  for (const backend of ["libvirt_vm", "container_worker_vm"]) {
    for (const mode of ["metered_api", "chatgpt_subscription", "invalid", ""]) {
      const payload = buildCreatePayload({ ...emptyCreateForm(), backend,
        codexAuthMode: ` ${mode} `, subscriptionProfileId: " profile-one " }, "root");
      assert.equal(payload.codex_auth_mode,
        ["metered_api", "chatgpt_subscription"].includes(mode) ? mode : undefined);
      assert.equal(payload.subscription_profile_id, mode === "chatgpt_subscription" ? "profile-one" : undefined);
      for (const credential of ["access_token", "refresh_token", "api_key"]) {
        assert.equal(Object.hasOwn(payload, credential), false);
      }
    }
  }
  assert.match(
    agentCreateDialog,
    /showCreateCodexAuthMode =\s*createForm\.backend === "container_worker_vm"/,
  );
  assert.match(agentCreateDialog, /<option value="metered_api">Metered API<\/option>/);
  assert.match(
    agentCreateDialog,
    /<option value="chatgpt_subscription">ChatGPT subscription<\/option>/,
  );
  const editPayloadSource = sourceBetweenIn(
    agentsPage,
    "function buildCodexUsagePayload(form: AgentPolicyFormState): AgentPolicyUpdateRequest {",
    "function isActiveStatus(status: string): boolean {",
  );
  assert.match(editPayloadSource, /codex_auth_mode:/);
  assert.match(editPayloadSource, /formUsesSubscription\(form\)/);
  assert.match(editPayloadSource, /payload\.subscription_profile_id = form\.subscriptionProfileId\.trim\(\)/);
  assert.match(agentsPage, /subscriptionProfileId: policy\?\.subscription_profile_id \?\? ""/);
  assert.doesNotMatch(editPayloadSource, /allowed_tools|model:|ttl_hours|labels:|web_search_enabled|grants|http_targets/);
  assert.doesNotMatch(subscriptionsPage, /accessToken|refreshToken|OPENAI_API_KEY/);
  assert.match(subscriptionsPage, /\/api\/admin\/codex-subscriptions/);
  assert.match(subscriptionsPage, /workerUrl \?\? "No container worker configured"/);
  assert.match(subscriptionsPage, /Pinned account id/);
  assert.match(subscriptionsPage, /Rate limits/);
  assert.match(subscriptionsPage, /Queued prompts/);
  assert.match(subscriptionsPage, /login\.status === "pending"/);
});

test("create TTL duration accepts seconds through weeks and emits seconds", () => {
  const units = [
    [1, ["s", "sec", "secs", "second", "seconds"]],
    [60, ["m", "min", "mins", "minute", "minutes"]],
    [3600, ["h", "hr", "hrs", "hour", "hours"]],
    [86400, ["d", "day", "days"]],
    [604800, ["w", "week", "weeks"]],
  ];
  for (const [seconds, aliases] of units) {
    for (const alias of aliases) {
      const input = ` 2 ${alias.toUpperCase()} `;
      assert.equal(parseDurationSeconds(input), 2 * seconds, input);
      assert.equal(buildCreatePayload({ ...emptyCreateForm(), ttlDuration: input }, "root").ttl_seconds,
        2 * seconds, `payload TTL: ${input}`);
    }
  }
  for (const input of ["", " ", "0s", "-1m", "1.5h", "3", "1month", "seconds", `${"9".repeat(400)}s`]) {
    assert.equal(parseDurationSeconds(input), null, input);
    assert.equal(Object.hasOwn(buildCreatePayload({ ...emptyCreateForm(), ttlDuration: input }, "root"),
      "ttl_seconds"), false, `invalid TTL must be omitted: ${input}`);
  }
  assert.equal(parseDurationSeconds("1s"), 1);
});

test("buildCreatePayload gates provider and image fields by backend", () => {
  for (const backend of ["libvirt_vm", "container_worker_vm"]) {
    const form = {
      ...emptyCreateForm(), backend, vmName: " vm-one ", targetName: " container-one ",
      sessionId: " session-one ", ttlDuration: "2h", model: " model-one ",
      provider: " vm-provider ", vmUser: " vm-user ", agentProvider: " codex ",
      modelProviderMode: " openai_public ", vmImage: " vm-image ", containerImage: " runner:latest ",
      workerId: " worker-one ", dotnet: true, nuget: true, playwright: true,
      mssql: true, mssqlInitRoot: " /seed/database ",
      initialPolicy: {
        labels: { purpose: "test" }, webSearchEnabled: true,
        allowedTools: [" git ", "git", "", "read"], connectorGrants: { files: ["read"] },
        hostDirectoryGrants: { artifacts: ["read", "write"] },
        allowedScriptNames: [" build ", "build", ""],
        communicationTargetIds: [" target-one ", "target-one", ""],
        emailMailboxGrants: [{ mailbox_id: "inbox-one", access: "read", folders: [" Inbox ", "Inbox", ""] }],
        httpTargets: [{ id: "draft-one", alias: " docs ", baseUrl: " https://example.test/docs/ ",
          metadata: { purpose: "reference" }, managedWebsiteSession: "login-one" }],
        // Legacy and credential-bearing form fields must never leak into the request.
        discord_channels: ["legacy"], teams_channel_allowlist: ["legacy"],
        email_mailboxes: ["legacy"], email_folders: ["legacy"], codex_skills: ["legacy"],
      },
    };
    const common = {
      backend, session_id: "session-one", directory_path: "root/child", ttl_seconds: 7200,
      model: "model-one", agent_provider: "codex", model_provider_mode: "openai_public",
      dotnet: true, nuget: true, playwright: true, labels: { purpose: "test" },
      web_search_enabled: true, allowed_tools: ["git", "read"], connector_grants: { files: ["read"] },
      host_directory_grants: { artifacts: ["read", "write"] }, allowed_script_names: ["build"],
      communication_target_ids: ["target-one"],
      email_mailbox_grants: [{ mailbox_id: "inbox-one", access: "read", folders: ["Inbox"] }],
      http_targets: { targets: { docs: { base_url: "https://example.test/docs",
        metadata: { purpose: "reference" }, managed_website_session: "login-one" } } },
    };
    const target = backend === "libvirt_vm"
      ? { vm_name: "vm-one", provider: "vm-provider", vm_user: "vm-user", vm_image: "vm-image",
          mssql: true, mssql_init_root: "/seed/database" }
      : { target_name: "container-one", container_image: "runner:latest", worker_id: "worker-one" };
    assert.deepEqual(buildCreatePayload(form, " root/child "), { ...common, ...target }, backend);

    const emptyTarget = backend === "libvirt_vm"
      ? { vm_name: null, vm_user: "agent", mssql: false, mssql_init_root: null }
      : { target_name: null, container_image: null };
    assert.deepEqual(buildCreatePayload({ ...emptyCreateForm(), backend }, " "), {
      backend, session_id: null, directory_path: null, ttl_seconds: 604800,
      dotnet: false, nuget: false, playwright: false, ...emptyTarget,
      communication_target_ids: [], email_mailbox_grants: [],
    }, `empty optional fields: ${backend}`);
  }
  assert.equal(buildCreatePayload({ ...emptyCreateForm(), vmUser: " ", mssqlInitRoot: "/unused" }, "root").vm_user,
    "agent");
  assert.equal(buildCreatePayload({ ...emptyCreateForm(), mssqlInitRoot: "/unused" }, "root").mssql_init_root,
    null, "disabled MSSQL must omit its init root");
});
