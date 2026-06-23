import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(
  new URL("../src/features/agents/AgentsPage.tsx", import.meta.url),
  "utf8",
);
const types = readFileSync(
  new URL("../src/features/admin/types.ts", import.meta.url),
  "utf8",
);
const styles = readFileSync(
  new URL("../src/styles.css", import.meta.url),
  "utf8",
);

function sourceBetween(startMarker, endMarker) {
  const start = page.indexOf(startMarker);
  const end = page.indexOf(endMarker);
  assert.notEqual(start, -1, `start marker not found: ${startMarker}`);
  assert.notEqual(end, -1, `end marker not found: ${endMarker}`);
  assert.ok(end > start, `end marker must follow start marker: ${endMarker}`);
  return page.slice(start, end);
}

function createModalSource() {
  return sourceBetween("{showCreateForm ? (", "{showEditForm && selectedAgent ? (");
}

function buildCreatePayloadSource() {
  return sourceBetween(
    "function buildCreatePayload(form: AgentCreateFormState, directoryPath: string): AgentCreateRequest {",
    "function buildPolicyPayload(form: AgentPolicyFormState): AgentPolicyUpdateRequest {",
  );
}

function parseDurationSecondsSource() {
  return sourceBetween(
    "function parseDurationSeconds(value: string): number | null {",
    "type AgentListLoadOptions = {",
  );
}

function handleCreateAgentSource() {
  return sourceBetween(
    "function handleCreateAgent() {",
    "function openEditForm() {",
  );
}

function finishCreateAgentSource() {
  return sourceBetween(
    "async function finishCreateAgent(createPayload: AgentCreateRequest) {",
    "function scheduleCreateAgent(createPayload: AgentCreateRequest) {",
  );
}

function scheduleCreateAgentSource() {
  return sourceBetween(
    "function scheduleCreateAgent(createPayload: AgentCreateRequest) {",
    "function handleCreateAgent() {",
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
  // emptyCreateForm must seed both image/provider fields so the modal opens with defaults.
  assert.match(page, /function emptyCreateForm\([\s\S]*?ttlDuration: "7d",[\s\S]*?model: "",[\s\S]*?agentProvider: "",[\s\S]*?modelProviderMode: "",[\s\S]*?vmImage: "",[\s\S]*?\}/);
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
  assert.match(page, /loadAgentCreateOptions\(selectedDirectoryPath\)/);
  assert.match(page, /setCreateOptions\(options\)/);
});

test("create modal always posts the selected page directory", () => {
  assert.doesNotMatch(page, /directoryPath: string;/);
  assert.doesNotMatch(page, /createForm\.directoryPath/);
  assert.doesNotMatch(page, /<span>Directory<\/span>/);
  assert.match(page, /function buildCreatePayload\(form: AgentCreateFormState, directoryPath: string\)/);
  assert.match(page, /directory_path: optionalText\(directoryPath\)/);
  assert.match(page, /buildCreatePayload\(submittedForm, selectedDirectoryPath\)/);
});

test("create submit dismisses the modal before waiting for agent creation", () => {
  const source = handleCreateAgentSource();
  const closeIndex = source.indexOf("setShowCreateForm(false);");
  const scheduleIndex = source.indexOf("scheduleCreateAgent(createPayload);");

  assert.notEqual(closeIndex, -1, "create handler should close the modal");
  assert.notEqual(scheduleIndex, -1, "create handler should schedule the create request");
  assert.ok(closeIndex < scheduleIndex, "create modal must close before the long-running create request is scheduled");
  assert.match(source, /const createPayload = buildCreatePayload\(submittedForm, selectedDirectoryPath\);/);
  assert.match(source, /setCreateSubmitting\(true\);/);
  assert.match(source, /setStatus\(`Queueing create for \$\{targetLabel\}\.\.\.`\);/);
  assert.doesNotMatch(source, /setInlineValidation\(message\);/);
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
    page,
    /createProviderOptions = \(createOptions\?\.agent_providers \?\? \[\]\)\.filter\(\(option\) =>\s*option\.execution_backends\.includes\(createForm\.backend\),?\s*\)/,
  );
  // Switching backend must drop a now-illegal provider (e.g. copilot on container).
  assert.match(
    page,
    /const nextAgentProvider =[\s\S]*?current\.agentProvider && stillValid \? current\.agentProvider : ""[\s\S]*?agentProvider: nextAgentProvider,/,
  );
  assert.match(page, /nextAgentProvider && nextAgentProvider !== "codex"[\s\S]*?\? ""[\s\S]*?: current\.modelProviderMode/);
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
    "Web search",
    "Model",
    "Model relay",
    "Session TTL",
  ]) {
    assert.match(createModal, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(createModal, /StringMapEditor/);
  assert.match(createModal, /OptionMultiSelectEditor/);
  assert.match(createModal, /StringListEditor/);
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
  assert.doesNotMatch(page, /existing_agent_behavior/);
  assert.doesNotMatch(page, /allow_existing_session/);
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
