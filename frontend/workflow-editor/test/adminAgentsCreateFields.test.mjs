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

test("create form state carries backend-aware provider and image selections", () => {
  assert.match(page, /type AgentCreateFormState = \{[\s\S]*?agentProvider: string;[\s\S]*?\};/);
  assert.match(page, /type AgentCreateFormState = \{[\s\S]*?vmImage: string;[\s\S]*?\};/);
  // emptyCreateForm must seed both image/provider fields so the modal opens with defaults.
  assert.match(page, /function emptyCreateForm\([\s\S]*?agentProvider: "",[\s\S]*?vmImage: "",[\s\S]*?\}/);
});

test("buildCreatePayload gates provider and image fields by backend", () => {
  const source = buildCreatePayloadSource();
  // agent_provider is emitted for both backends, but only when set.
  assert.match(source, /if \(agentProvider\) payload\.agent_provider = agentProvider;/);
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
    "discord_channels",
    "teams_channel_allowlist",
    "email_mailboxes",
    "email_folders",
  ]) {
    assert.match(source, new RegExp(`payload\\.${field}`));
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
  assert.match(page, /buildCreatePayload\(createForm, selectedDirectoryPath\)/);
});

test("provider options are filtered by execution_backends against the selected backend", () => {
  assert.match(
    page,
    /createProviderOptions = \(createOptions\?\.agent_providers \?\? \[\]\)\.filter\(\(option\) =>\s*option\.execution_backends\.includes\(createForm\.backend\),?\s*\)/,
  );
  // Switching backend must drop a now-illegal provider (e.g. copilot on container).
  assert.match(
    page,
    /option\.execution_backends\.includes\(nextBackend\)[\s\S]*?agentProvider:\s*[\s\S]*?stillValid \? current\.agentProvider : "",/,
  );
});

test("VM image select lives in the libvirt block; container image select in the container block", () => {
  const createModal = createModalSource();
  // The libvirt-only field group renders the VM image select bound to vmImage.
  assert.match(
    createModal,
    /createForm\.backend === "libvirt_vm" \?[\s\S]*?<span>VM image<\/span>[\s\S]*?value=\{createForm\.vmImage\}/,
  );
  // VM image options come from the libvirt template catalog.
  assert.match(createModal, /createOptions\?\.libvirt\.templates \?\? \[\]\)\.map/);
  // The container path renders the image-alias select from the container catalog.
  assert.match(
    createModal,
    /<span>Container image selector<\/span>[\s\S]*?createOptions\.container_worker\.images\.map/,
  );
  assert.match(createModal, /value=\{createForm\.containerImage\}/);
});

test("create modal exposes structured initial policy controls without post-create-only fields", () => {
  const createModal = createModalSource();
  for (const label of [
    "Initial policy",
    "Labels",
    "Allowed tools",
    "Allowed scripts",
    "Discord channels or targets",
    "Teams targets",
    "Email mailboxes",
    "Email folders",
    "Web search",
  ]) {
    assert.match(createModal, new RegExp(`>${label}<`));
  }
  assert.match(createModal, /StringMapEditor/);
  assert.match(createModal, /StringListEditor/);
  for (const label of [
    "Model",
    "TTL hours",
    "Codex skills",
  ]) {
    assert.doesNotMatch(createModal, new RegExp(`>${label}<`));
  }
});

test("create modal select wrapper owns generated caret positioning", () => {
  const createModal = createModalSource();
  assert.match(
    createModal,
    /<span>Container image selector<\/span>[\s\S]*?<span className="select-field-control">[\s\S]*?<select/,
  );
  assert.match(styles, /:has\(\.select-field-control\)::after \{[\s\S]*?content: none;/);
  assert.match(styles, /\.select-field-control::after \{[\s\S]*?top: 50%;[\s\S]*?transform: translateY\(-50%\);/);
});

test("the create modal never reintroduces upsert semantics", () => {
  // No upsert / existing-session controls belong in the direct-create modal.
  assert.doesNotMatch(page, /existing_agent_behavior/);
  assert.doesNotMatch(page, /allow_existing_session/);
});

test("request and options types expose the new create fields", () => {
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?agent_provider\?: string \| null;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?vm_image\?: string \| null;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?teams_channel_allowlist\?: string\[\];[\s\S]*?\};/);
  assert.match(types, /type AgentCreateProviderOption = \{[\s\S]*?execution_backends: AgentBackend\[\];[\s\S]*?\};/);
  assert.match(types, /type AgentCreateOptionsResponse = \{[\s\S]*?agent_providers: AgentCreateProviderOption\[\];[\s\S]*?libvirt: \{[\s\S]*?container_worker: \{[\s\S]*?\};/);
});
