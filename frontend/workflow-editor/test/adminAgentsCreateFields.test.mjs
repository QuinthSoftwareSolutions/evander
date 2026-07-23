import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

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
const codexAuthPanel = readFileSync(
  new URL("../src/features/agents/CodexAuthPanel.tsx", import.meta.url),
  "utf8",
);
const sessionGrantEditors = readFileSync(
  new URL("../src/features/communications/SessionGrantEditors.tsx", import.meta.url),
  "utf8",
);
const legacyCommunicationPolicy = readFileSync(
  new URL("../src/features/communications/legacyCommunicationPolicy.ts", import.meta.url),
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

function parseDurationSecondsSource() {
  return sourceBetweenIn(
    agentCreateShared,
    "function parseDurationSeconds(value: string): number | null {",
    "export function buildCreatePayload(form: AgentCreateFormState, directoryPath: string): AgentCreateRequest {",
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
  const payloadSource = buildCreatePayloadSource();
  assert.match(
    payloadSource,
    /codexAuthMode === "metered_api" \|\| codexAuthMode === "chatgpt_subscription"/,
  );
  assert.match(payloadSource, /payload\.codex_auth_mode = codexAuthMode/);
  assert.match(
    agentCreateDialog,
    /showCreateCodexAuthMode =\s*createForm\.backend === "container_worker_vm"/,
  );
  assert.match(agentCreateDialog, /<option value="metered_api">Metered API<\/option>/);
  assert.match(
    agentCreateDialog,
    /<option value="chatgpt_subscription">ChatGPT subscription<\/option>/,
  );
  assert.match(agentsPage, /buildPolicyPayload\([\s\S]*?includeCodexAuthMode = false/);
  assert.match(agentsPage, /payload\.codex_auth_mode =/);
  assert.doesNotMatch(codexAuthPanel, /accessToken|refreshToken|OPENAI_API_KEY/);
  assert.match(codexAuthPanel, /\/api\/admin\/codex-auth\/device-login/);
  assert.match(codexAuthPanel, /Worker ChatGPT subscription/);
  assert.match(codexAuthPanel, /suffix\(login\.directoryPath\)/);
  assert.match(codexAuthPanel, /const originDirectoryPath = login\.directoryPath;/);
  assert.match(codexAuthPanel, /setLogin\(\{ \.\.\.payload, directoryPath: originDirectoryPath \}\);/);
  assert.match(codexAuthPanel, /login\?\.status === "pending"/);
});

test("create TTL duration accepts seconds through weeks and emits seconds", () => {
  const parser = parseDurationSecondsSource();
  assert.match(parser, /s: 1,/);
  assert.match(parser, /m: 60,/);
  assert.match(parser, /h: 60 \* 60,/);
  assert.match(parser, /d: 24 \* 60 \* 60,/);
  assert.match(parser, /w: 7 \* 24 \* 60 \* 60,/);
  assert.match(parser, /amount <= 0/);
  const payloadSource = buildCreatePayloadSource();
  assert.match(payloadSource, /const ttlSeconds = parseDurationSeconds\(form\.ttlDuration\);/);
  assert.match(payloadSource, /if \(ttlSeconds != null\) payload\.ttl_seconds = ttlSeconds;/);
});

test("buildCreatePayload gates provider and image fields by backend", () => {
  const source = buildCreatePayloadSource();
  // agent_provider is emitted for both backends, but only when set.
  assert.match(source, /if \(agentProvider\) payload\.agent_provider = agentProvider;/);
  assert.match(source, /if \(modelProviderMode\) payload\.model_provider_mode = modelProviderMode;/);
  // model is emitted for both backends, but only when set.
  assert.match(source, /const model = optionalText\(form\.model\);\s*if \(model\) payload\.model = model;/);
  // Seed/bootstrap capabilities are valid for both VM and container worker sessions.
  assert.match(
    source,
    /payload\.dotnet = form\.dotnet;\s*payload\.nuget = form\.nuget;\s*payload\.playwright = form\.playwright;\s*if \(form\.backend === "libvirt_vm"\)/,
  );
  // vm_image is emitted only inside the libvirt branch (before the `else`),
  // and container_image only in the container branch (after the `else`).
  assert.match(
    source,
    /form\.backend === "libvirt_vm"\)\s*\{[\s\S]*?if \(vmImage\) payload\.vm_image = vmImage;[\s\S]*?\} else \{[\s\S]*?payload\.container_image = /,
  );
  // container_image must not leak into the libvirt branch.
  const libvirtBranch = source.match(
    /form\.backend === "libvirt_vm"\)\s*\{([\s\S]*?)\} else \{/,
  );
  assert.ok(libvirtBranch, "libvirt branch of buildCreatePayload not found");
  assert.doesNotMatch(libvirtBranch[1], /payload\.container_image/);
  assert.doesNotMatch(libvirtBranch[1], /payload\.target_name/);
  // Initial policy fields are optional and emitted only after backend-specific target fields.
  assert.match(source, /const initialPolicy = form\.initialPolicy;/);
  for (const field of [
    "labels",
    "web_search_enabled",
    "allowed_tools",
    "allowed_script_names",
    "communication_target_ids",
    "email_mailbox_grants",
    "http_targets",
  ]) {
    assert.match(source, new RegExp(`payload\\.${field}`));
  }
  for (const field of [
    "discord_channels",
    "teams_channel_allowlist",
    "email_mailboxes",
    "email_folders",
  ]) {
    assert.doesNotMatch(source, new RegExp(`payload\\.${field}`));
  }
  assert.doesNotMatch(source, /payload\.codex_skills/);
});

test("create modal loads create-options for the selected directory", () => {
  assert.match(page, /\/api\/admin\/agents\/create-options/);
  assert.match(agentsPage, /directoryPath=\{selectedDirectoryPath\}/);
  assert.match(agentCreateDialog, /loadAgentCreateOptions\(directoryPath\)/);
  assert.match(page, /setCreateOptions\(options\)/);
});

test("create modal clears directory-scoped grants when the selected directory changes", () => {
  assert.match(
    agentCreateDialog,
    /useEffect\(\(\) => \{[\s\S]*?setCreateForm\(\(current\) => \(\{[\s\S]*?connectorGrants: \{\},[\s\S]*?\}\)\);[\s\S]*?loadAgentCreateOptions\(directoryPath\)[\s\S]*?\}, \[directoryPath\]\);/,
  );
});

test("legacy agent conversion uses the current complete communication policy", () => {
  const conversion = sourceBetweenIn(
    agentsPage,
    "function convertLegacyAgentCommunicationPolicy() {",
    "function openPromptForm() {",
  );

  for (const field of [
    "discordChannels",
    "teamsChannels",
    "teamsChats",
    "teamsUsers",
    "emailMailboxes",
    "emailFolderAllowlistByMailbox",
    "allowedTools",
  ]) {
    assert.match(conversion, new RegExp(`current\\.${field}`));
  }
  assert.doesNotMatch(conversion, /selectedAgentPolicy/);
  assert.match(conversion, /legacyEmailMailboxAccess/);
  assert.match(agentsPage, /teams_chat_allowlist: hostStringList\(rawPolicy, "teams_chat_allowlist"\)/);
  assert.match(agentsPage, /teams_user_allowlist: hostStringList\(rawPolicy, "teams_user_allowlist"\)/);
  assert.match(agentsPage, /email_folder_allowlist_by_mailbox: hostStringListMap/);
  for (const tool of [
    "email_create_draft",
    "email_create_draft_with_attachments",
    "email_send",
    "email_send_with_attachments",
    "email_send_approved",
    "email_send_approved_with_attachments",
  ]) {
    assert.match(legacyCommunicationPolicy, new RegExp(`"${tool}"`));
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

test("agent create and edit use the shared structured HTTP target contract", () => {
  const payloadSource = buildCreatePayloadSource();
  assert.match(payloadSource, /initialPolicy\.httpTargets\.length > 0/);
  assert.match(payloadSource, /payload\.http_targets = httpTargetsPayload\(initialPolicy\.httpTargets\)/);
  assert.match(agentCreateDialog, /onValidationChange=\{setCreateHttpTargetsValidationError\}/);
  assert.match(agentsPage, /httpTargetsDraftFromMetadata\(policy\?\.metadata\)/);
  assert.match(agentsPage, /includeHttpTargets/);
  assert.match(agentsPage, /payload\.http_targets = httpTargetsPayload\(form\.httpTargets\)/);
  assert.match(agentsPage, /setHttpTargetsDirty\(true\)/);
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
    "Approved container image alias for worker sessions. Leave blank to use the directory default runner image.",
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
