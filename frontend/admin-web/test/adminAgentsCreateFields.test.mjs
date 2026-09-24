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
const agentCreateShared = readFileSync(
  new URL("../src/features/agents/agentCreateShared.ts", import.meta.url),
  "utf8",
);
const subscriptionsPage = readFileSync(
  new URL("../src/features/subscriptions/SubscriptionsPage.tsx", import.meta.url),
  "utf8",
);
const sessionGrantEditors = readFileSync(
  new URL("../src/features/communications/SessionGrantEditors.tsx", import.meta.url),
  "utf8",
);
const page = [agentsPage, agentCreateDialog, agentCreateShared, sessionGrantEditors].join("\n");
const types = readFileSync(
  new URL("../src/features/admin/types.ts", import.meta.url),
  "utf8",
);
const styles = readFileSync(
  new URL("../src/styles.css", import.meta.url),
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

function sourceBetween(startMarker, endMarker) {
  return sourceBetweenIn(page, startMarker, endMarker);
}

function createModalSource() {
  return [agentCreateDialog, sessionGrantEditors].join("\n");
}

function buildCreatePayloadSource() {
  return sourceBetweenIn(
    agentCreateShared,
    "function buildCreatePayload(form: AgentCreateFormState, directoryPath: string): AgentCreateRequest {",
    "export function agentCreateTargetLabel(form: AgentCreateFormState): string {",
  );
}

function handleCreateSubmitSource() {
  return sourceBetweenIn(
    agentCreateDialog,
    "async function handleSubmit() {",
    "return (",
  );
}

function finishCreateAgentSource() {
  return sourceBetweenIn(
    agentsPage,
    "async function finishCreateAgent(createPayload: AgentCreateRequest) {",
    "function scheduleCreateAgent(createPayload: AgentCreateRequest) {",
  );
}

function scheduleCreateAgentSource() {
  return sourceBetweenIn(
    agentsPage,
    "function scheduleCreateAgent(createPayload: AgentCreateRequest) {",
    "function handleCreateAgentSubmit(createPayload: AgentCreateRequest, meta: AgentCreateSubmitMeta) {",
  );
}

function zIndexForSelector(selector) {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = styles.match(new RegExp(`${escapedSelector}\\s*\\{[\\s\\S]*?z-index:\\s*(\\d+);`));
  assert.ok(match, `missing z-index rule for ${selector}`);
  return Number(match[1]);
}

test("create form state carries backend-aware provider and image selections", () => {
  assert.match(page, /type AgentCreateFormState = \{[\s\S]*?agentProvider: string;[\s\S]*?\};/);
  assert.match(page, /type AgentCreateFormState = \{[\s\S]*?modelProviderMode: string;[\s\S]*?\};/);
  assert.match(page, /type AgentCreateFormState = \{[\s\S]*?model: string;[\s\S]*?\};/);
  assert.match(page, /type AgentCreateFormState = \{[\s\S]*?ttlDuration: string;[\s\S]*?\};/);
  assert.match(page, /type AgentCreateFormState = \{[\s\S]*?vmImage: string;[\s\S]*?\};/);
  assert.match(page, /type AgentCreateFormState = \{[\s\S]*?codexAuthMode: string;[\s\S]*?\};/);
  // emptyCreateForm must seed both image/provider fields so the modal opens with defaults.
  assert.match(page, /function emptyCreateForm\([\s\S]*?ttlDuration: "7d",[\s\S]*?model: "",[\s\S]*?agentProvider: "",[\s\S]*?modelProviderMode: "",[\s\S]*?vmImage: "",[\s\S]*?\}/);
});

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

test("worker auth is profile-aware and agent usage allows route changes", () => {
  assert.doesNotMatch(agentsPage, /<CodexAuthPanel/);
  assert.match(agentCreateDialog, /showCreateSubscriptionProfile/);
  assert.match(agentCreateDialog, /Select a ChatGPT subscription profile/);
  assert.match(agentCreateDialog, /subscription_profiles/);
  assert.match(agentsPage, /Subscription profile/);
  assert.match(agentsPage, /effective_subscription_profile_id/);
  assert.match(agentsPage, /You can change the profile later without replacing the agent session/);
  assert.match(
    agentsPage,
    /loadAgentCreateOptions\(selectedAgent\.directory_path, "", policy\.session_id\)/,
  );
  assert.doesNotMatch(agentsPage, /is immutable\. Create a new agent to change it/);
  assert.match(agentsPage, /function codexUsageForAgent\(/);
  assert.match(agentsPage, /hostRecord\(agent\.host, "codex_usage"\)/);
  assert.match(agentsPage, /effective_codex_auth_mode/);
  assert.match(agentsPage, /codex_auth_mode_source/);
  assert.match(agentsPage, /Codex usage: \{selectedAgentCodexUsage\.label\}/);
  assert.match(agentsPage, /agent-list-usage-row/);
  assert.match(agentsPage, /Directory default/);
});

test("agent list supports searching common agent identifiers", () => {
  assert.match(agentsPage, /const \[agentSearch, setAgentSearch\] = useState\(""\);/);
  assert.match(agentsPage, /const normalizedSearch = agentSearch\.trim\(\)\.toLocaleLowerCase\(\);/);
  assert.match(
    agentsPage,
    /item\.target_name,[\s\S]*item\.agent_id,[\s\S]*item\.session_id,[\s\S]*item\.directory_path,/,
  );
  assert.match(agentsPage, /<span>Search agents<\/span>/);
  assert.match(agentsPage, /type="search"/);
  assert.match(
    agentsPage,
    /if \(nextSelection\.agentId \|\| nextSelection\.sessionId\) \{[\s\S]*setAgentSearch\(""\);/,
  );
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

test("create modal loads create-options for the selected directory", () => {
  assert.match(page, /\/api\/admin\/agents\/create-options/);
  assert.match(agentsPage, /directoryPath=\{selectedDirectoryPath\}/);
  assert.match(agentCreateDialog, /loadAgentCreateOptions\(directoryPath, createForm\.workerId\)/);
  assert.match(page, /setCreateOptions\(options\)/);
});

test("create modal clears directory-scoped grants when the selected directory changes", () => {
  assert.match(
    agentCreateDialog,
    /useEffect\(\(\) => \{[\s\S]*?setCreateForm\(\(current\) => \(\{[\s\S]*?connectorGrants: \{\},[\s\S]*?\}\)\);[\s\S]*?\}, \[directoryPath\]\);/,
  );
});

test("agent editing leaves session-owned policy controls on the Sessions page", () => {
  const editModal = sourceBetweenIn(
    agentsPage,
    "{showEditForm && selectedAgent && selectedAgentCodexUsage ? (",
    "{showSkillInstallForm && selectedAgent ? (",
  );
  assert.match(editModal, /Edit Codex usage/);
  for (const duplicate of [
    "Model",
    "Renew TTL hours",
    "Labels",
    "HTTP targets",
    "Allowed tools",
    "Connector grants",
    "Host directory grants",
    "Web search",
    "Allowed scripts",
    "Approval notifications",
    "Communication targets",
    "Email mailbox access",
  ]) {
    assert.doesNotMatch(editModal, new RegExp(duplicate));
  }
});

test("create modal always posts the selected page directory", () => {
  assert.match(agentCreateDialog, /directoryPath: string;/);
  assert.doesNotMatch(agentCreateShared, /createForm\.directoryPath/);
  assert.doesNotMatch(agentCreateDialog, /<span>Directory<\/span>/);
  assert.match(page, /function buildCreatePayload\(form: AgentCreateFormState, directoryPath: string\)/);
  assert.match(page, /directory_path: optionalText\(directoryPath\)/);
  assert.match(agentsPage, /directoryPath=\{selectedDirectoryPath\}/);
  assert.match(agentCreateDialog, /buildCreatePayload\(submittedForm, directoryPath\)/);
});

test("create submit hands off creation and closes after synchronous scheduling", () => {
  const source = handleCreateSubmitSource();
  const createIndex = source.indexOf("const createPayload = buildCreatePayload(submittedForm, directoryPath);");
  const submitIndex = source.indexOf("await onCreate(createPayload, { form: submittedForm, targetLabel });");
  const closeIndex = source.indexOf("if (closeOnSuccess) onClose();");

  assert.notEqual(createIndex, -1, "create modal should build the create payload");
  assert.notEqual(submitIndex, -1, "create modal should hand off to the page submit callback");
  assert.notEqual(closeIndex, -1, "create modal should close on successful handoff");
  assert.ok(createIndex < submitIndex, "create payload must be built before submit callback");
  assert.ok(submitIndex < closeIndex, "modal should close after submit callback returns");
  assert.match(agentsPage, /scheduleCreateAgent\(createPayload\);/);
  assert.match(agentsPage, /setStatus\(`Queueing create for \$\{meta\.targetLabel\}\.\.\.`\);/);
  assert.doesNotMatch(source, /fetchJson<AgentActionResponse>/);
});

test("create request starts after the modal has a paint opportunity", () => {
  const source = scheduleCreateAgentSource();

  assert.match(source, /window\.requestAnimationFrame\(\(\) => \{/);
  assert.match(source, /window\.setTimeout\(\(\) => \{/);
  assert.match(source, /void finishCreateAgent\(createPayload\);/);
});

test("create submission uses async operations instead of synchronous setup", () => {
  const successSource = finishCreateAgentSource();

  assert.match(page, /\/api\/admin\/agents\/create-operations/);
  assert.match(successSource, /const operation = await submitAgentCreateOperation\(createPayload\);/);
  assert.match(successSource, /setCreateOperations\(\(current\) => \[/);
  assert.match(successSource, /Queued create for \$\{operation\.target_name\}\./);
  assert.match(successSource, /if \(operation\.status === "succeeded"\) \{/);
  assert.doesNotMatch(successSource, /\/api\/admin\/agents",/);
});

test("provider options are filtered by execution_backends against the selected backend", () => {
  assert.match(
    agentCreateDialog,
    /createProviderOptions = useMemo\(\s*\(\) => \(createOptions\?\.agent_providers \?\? \[\]\)\.filter\(\(option\) =>\s*option\.execution_backends\.includes\(createForm\.backend\),?\s*\)/,
  );
  // Switching backend must drop a now-illegal provider (e.g. copilot on container).
  assert.match(
    agentCreateDialog,
    /const nextAgentProvider =[\s\S]*?form\.agentProvider && stillValid \? form\.agentProvider : ""[\s\S]*?agentProvider: nextAgentProvider,/,
  );
  assert.match(agentCreateDialog, /nextAgentProvider && nextAgentProvider !== "codex"[\s\S]*?\? ""[\s\S]*?: form\.modelProviderMode/);
});

test("VM image select lives in the libvirt block; container image select in the container block", () => {
  const createModal = createModalSource();
  // The libvirt-only field group renders the VM image select bound to vmImage.
  assert.match(
    createModal,
    /createForm\.backend === "libvirt_vm" \?[\s\S]*?VM image[\s\S]*?value=\{createForm\.vmImage\}/,
  );
  // VM image options come from the libvirt template catalog.
  assert.match(createModal, /createOptions\?\.libvirt\.templates \?\? \[\]\)\.map/);
  // The container path renders the image-alias select from the container catalog.
  assert.match(
    createModal,
    /Container image selector[\s\S]*?createOptions\.container_worker\.images\.map/,
  );
  assert.match(createModal, /value=\{createForm\.containerImage\}/);
});

test("create modal exposes model override and structured initial policy controls without post-create-only fields", () => {
  const createModal = createModalSource();
  for (const label of [
    "Initial policy",
    "Labels",
    "Allowed tools",
    "Allowed scripts",
    "Communication targets",
    "Email mailbox access",
    "HTTP targets",
    "Capabilities",
    "Web search",
    "Model",
    "Model relay",
    "Session TTL",
  ]) {
    assert.match(createModal, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(createModal, /StringMapEditor/);
  assert.match(createModal, /StringListEditor/);
  assert.match(createModal, /HttpTargetsEditor/);
  assert.match(createModal, /CommunicationTargetCheckboxTable/);
  for (const label of [
    "Discord channels or targets",
    "Teams targets",
    "Email mailboxes",
    "Email folders",
  ]) {
    assert.doesNotMatch(createModal, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  for (const label of [
    "TTL hours",
    "Codex skills",
  ]) {
    assert.doesNotMatch(createModal, new RegExp(`>${label}<`));
  }
});

test("agent creation uses the shared structured HTTP target contract", () => {
  const payloadSource = buildCreatePayloadSource();
  assert.match(payloadSource, /initialPolicy\.httpTargets\.length > 0/);
  assert.match(payloadSource, /payload\.http_targets = httpTargetsPayload\(initialPolicy\.httpTargets\)/);
  assert.match(agentCreateDialog, /onValidationChange=\{setCreateHttpTargetsValidationError\}/);
  assert.doesNotMatch(agentsPage, /<HttpTargetsEditor/);
});

test("create modal groups create capabilities and communication targets", () => {
  const createModal = createModalSource();
  assert.match(createModal, /<fieldset className="agent-capability-group">/);
  assert.match(createModal, /className="agent-capability-grid"/);
  for (const label of [".NET", "NuGet", "MSSQL", "Playwright", "Web search"]) {
    assert.match(createModal, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(createModal, /checked=\{createForm\.dotnet\}/);
  assert.match(createModal, /checked=\{createForm\.nuget\}/);
  assert.match(createModal, /checked=\{createForm\.playwright\}/);
  assert.match(createModal, /\{createForm\.backend === "libvirt_vm" \? \(/);
  assert.match(createModal, /checked=\{createForm\.initialPolicy\.webSearchEnabled\}/);
  assert.match(createModal, /\{createForm\.backend === "libvirt_vm" && createForm\.mssql \? \(/);
  assert.match(createModal, /className="agent-capability-detail-row"/);
  assert.match(createModal, /className="field-stack agent-capability-mssql-root"/);
  assert.doesNotMatch(createModal, /disabled=\{!createForm\.mssql\}/);
  assert.doesNotMatch(createModal, /agent-policy-toggle-row/);
  assert.match(createModal, /className="field-stack wide-field agent-communication-targets-field"/);
  assert.match(createModal, /<table className="agent-communication-target-table">/);
  for (const heading of ["Select", "Target", "Provider", "Shape", "Capabilities"]) {
    assert.match(createModal, new RegExp(`>${heading}<`));
  }
  assert.match(styles, /\.agent-capability-group \{/);
  assert.match(styles, /\.agent-capability-detail-row \{/);
  assert.match(styles, /\.agent-communication-target-table \{/);
});

test("create modal label rows rely on tooltip help instead of a visible helper", () => {
  const createModal = createModalSource();
  assert.match(createModal, /<CreateFieldLabel tooltip="Key\/value metadata stored on the session and shown in agent lists\.">[\s\S]*?Labels[\s\S]*?<\/CreateFieldLabel>/);
  assert.doesNotMatch(createModal, /description="Key\/value rows for agent labels\./);
});

test("create modal select wrapper owns generated caret positioning", () => {
  const createModal = createModalSource();
  assert.match(
    createModal,
    /Container image selector[\s\S]*?<span className="select-field-control">[\s\S]*?<select/,
  );
  assert.match(styles, /:has\(\.select-field-control\)::after \{[\s\S]*?content: none;/);
  assert.match(styles, /\.select-field-control::after \{[\s\S]*?top: 50%;[\s\S]*?transform: translateY\(-50%\);/);
});

test("create modal explains provider and bootstrap fields with tooltips", () => {
  const createModal = createModalSource();
  for (const helpText of [
    "Execution target for the agent. VM agent launches a libvirt VM; Container worker prepares a worker session that runs in a managed container.",
    "Agent CLI to bootstrap and run in the target. Claude bootstraps Claude Code for VM agents; Directory default uses the configured provider default.",
    "Relay mode for Codex model requests. Leave blank to use the directory default; choose Local OpenAI for host-local Ollama.",
    "How long the created session remains active before expiry. Use s, m, h, d, or w, for example 30m, 8h, 7d, or 2w.",
    "Model stored on the session for runtime prompts and resumes. Leave blank to use the selected agent provider or directory default.",
    "VM infrastructure provider, not the model provider. Leave blank to use the directory default, usually libvirt.",
    "Approved libvirt template alias for the base qcow2 image. Leave blank to use the directory default.",
    "Approved container image alias for worker sessions. Leave blank to use the selected worker's default runner image.",
    "Session policy values written when the agent is created. Empty rows are ignored.",
  ]) {
    assert.match(createModal, new RegExp(helpText.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(createModal, /<CreateFieldLabel[\s\S]*?Agent provider[\s\S]*?<\/CreateFieldLabel>/);
  assert.match(createModal, /showCreateModelProviderMode \? \(/);
  assert.match(createModal, /<CreateFieldLabel[\s\S]*?Provider[\s\S]*?<\/CreateFieldLabel>/);
});

test("admin tooltips render above modal layers", () => {
  const tooltipZIndex = zIndexForSelector(".admin-tooltip-bubble");
  for (const selector of [
    ".modal-backdrop",
    ".nested-modal-backdrop",
    ".compact-workflow-sheet-backdrop",
  ]) {
    assert.ok(tooltipZIndex > zIndexForSelector(selector), `${selector} must stay below tooltips`);
  }
});

test("the create modal never reintroduces upsert semantics", () => {
  // No upsert / existing-session controls belong in the direct-create modal.
  assert.doesNotMatch(agentCreateDialog, /existing_agent_behavior/);
  assert.doesNotMatch(agentCreateDialog, /allow_existing_session/);
  assert.doesNotMatch(agentCreateShared, /existing_agent_behavior/);
  assert.doesNotMatch(agentCreateShared, /allow_existing_session/);
});

test("request and options types expose the new create fields", () => {
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?agent_provider\?: string \| null;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?model_provider_mode\?: string \| null;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?model\?: string \| null;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?ttl_seconds\?: number \| null;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?vm_image\?: string \| null;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?communication_target_ids\?: string\[\];[\s\S]*?\};/);
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?teams_channel_allowlist\?: string\[\];[\s\S]*?\};/);
  assert.match(types, /type AgentCreateProviderOption = \{[\s\S]*?execution_backends: AgentBackend\[\];[\s\S]*?\};/);
  assert.match(types, /type AgentCreateModelProviderModeOption = \{[\s\S]*?value: string;[\s\S]*?label: string;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateCommunicationTargetOption = \{[\s\S]*?metadata\?: Record<string, unknown>;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateOptionsResponse = \{[\s\S]*?agent_providers: AgentCreateProviderOption\[\];[\s\S]*?model_provider_modes: AgentCreateModelProviderModeOption\[\];[\s\S]*?communication_targets: AgentCreateCommunicationTargetOption\[\];[\s\S]*?libvirt: \{[\s\S]*?container_worker: \{[\s\S]*?\};/);
});
