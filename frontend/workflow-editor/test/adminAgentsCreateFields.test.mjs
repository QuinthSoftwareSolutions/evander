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

test("create form state carries backend-aware provider and image selections", () => {
  assert.match(page, /type AgentCreateFormState = \{[\s\S]*?agentProvider: string;[\s\S]*?\};/);
  assert.match(page, /type AgentCreateFormState = \{[\s\S]*?vmImage: string;[\s\S]*?\};/);
  // emptyCreateForm must seed both new fields so the modal opens with defaults.
  assert.match(page, /function emptyCreateForm\([\s\S]*?agentProvider: "",[\s\S]*?vmImage: "",[\s\S]*?\}/);
});

test("buildCreatePayload gates provider and image fields by backend", () => {
  // agent_provider is emitted for both backends, but only when set.
  assert.match(page, /if \(agentProvider\) payload\.agent_provider = agentProvider;/);
  // vm_image is emitted only inside the libvirt branch (before the `else`),
  // and container_image only in the container branch (after the `else`).
  assert.match(
    page,
    /form\.backend === "libvirt_vm"\)\s*\{[\s\S]*?if \(vmImage\) payload\.vm_image = vmImage;[\s\S]*?\} else \{[\s\S]*?payload\.container_image = /,
  );
  // container_image must not leak into the libvirt branch.
  const libvirtBranch = page.match(
    /form\.backend === "libvirt_vm"\)\s*\{([\s\S]*?)\} else \{/,
  );
  assert.ok(libvirtBranch, "libvirt branch of buildCreatePayload not found");
  assert.doesNotMatch(libvirtBranch[1], /payload\.container_image/);
  assert.doesNotMatch(libvirtBranch[1], /payload\.target_name/);
});

test("create modal loads create-options for the selected directory", () => {
  assert.match(page, /\/api\/admin\/agents\/create-options/);
  assert.match(page, /loadAgentCreateOptions\(selectedDirectoryPath\)/);
  assert.match(page, /setCreateOptions\(options\)/);
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
  // The libvirt-only field group renders the VM image select bound to vmImage.
  assert.match(
    page,
    /createForm\.backend === "libvirt_vm" \?[\s\S]*?<span>VM image<\/span>[\s\S]*?value=\{createForm\.vmImage\}/,
  );
  // VM image options come from the libvirt template catalog.
  assert.match(page, /createOptions\?\.libvirt\.templates \?\? \[\]\)\.map/);
  // The container path renders the image-alias select from the container catalog.
  assert.match(
    page,
    /<span>Container image selector<\/span>[\s\S]*?createOptions\.container_worker\.images\.map/,
  );
  assert.match(page, /value=\{createForm\.containerImage\}/);
});

test("the create modal never reintroduces upsert semantics", () => {
  // No upsert / existing-session controls belong in the direct-create modal.
  assert.doesNotMatch(page, /existing_agent_behavior/);
  assert.doesNotMatch(page, /allow_existing_session/);
});

test("request and options types expose the new create fields", () => {
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?agent_provider\?: string \| null;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateRequest = \{[\s\S]*?vm_image\?: string \| null;[\s\S]*?\};/);
  assert.match(types, /type AgentCreateProviderOption = \{[\s\S]*?execution_backends: AgentBackend\[\];[\s\S]*?\};/);
  assert.match(types, /type AgentCreateOptionsResponse = \{[\s\S]*?agent_providers: AgentCreateProviderOption\[\];[\s\S]*?libvirt: \{[\s\S]*?container_worker: \{[\s\S]*?\};/);
});
