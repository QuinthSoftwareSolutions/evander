import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

const types = source("../src/features/admin/types.ts");
const selector = source(
  "../src/features/hostDirectories/HostDirectoryGrantSelector.tsx",
);
const createShared = source("../src/features/agents/agentCreateShared.ts");
const createDialog = source("../src/features/agents/AgentCreateDialog.tsx");
const agentsPage = source("../src/features/agents/AgentsPage.tsx");
const sessionsPage = source("../src/features/sessions/SessionsScreen.tsx");

test("host directory grant types and create options stay structured", () => {
  assert.match(
    types,
    /type HostDirectoryCapability = "list" \| "import" \| "delete";/,
  );
  assert.match(
    types,
    /type AgentCreateHostDirectoryOption = \{[\s\S]*?directory_id: string;[\s\S]*?capabilities: HostDirectoryCapability\[\];[\s\S]*?delete_requires_approval: boolean;/,
  );
  assert.match(
    types,
    /type AgentCreateOptionsResponse = \{[\s\S]*?host_directories: AgentCreateHostDirectoryOption\[\];/,
  );
  assert.match(types, /host_directory_grants\?: Record<string, string\[\]>;/);
  assert.match(types, /host_directory_grants: Record<string, string\[\]>;/);
});

test("selector exposes configured capabilities and maps them to dedicated tools", () => {
  assert.match(selector, /list: "host_directory_list"/);
  assert.match(selector, /import: "host_directory_import"/);
  assert.match(selector, /delete: "host_directory_delete"/);
  assert.match(selector, /directory\.capabilities\.map\(\(capability\) =>/);
  assert.match(selector, /directory\.delete_requires_approval/);
  assert.match(selector, />Approval</);
  assert.doesNotMatch(selector, /directory\.root/);
});

test("selector keeps unavailable grants visible and revocable without host paths", () => {
  assert.match(selector, /const unavailableGrants = Object\.entries\(values\)/);
  assert.match(selector, /option\?\.display_name \?\? directoryId/);
  assert.match(selector, />Unavailable grants</);
  assert.match(selector, /function revokeUnavailable/);
  assert.match(selector, />\s*Revoke unavailable\s*</);
  assert.doesNotMatch(selector, /\.root\b/);
});

test("agent creation posts grants, clears them on directory changes, and enables tools", () => {
  assert.match(
    createShared,
    /hostDirectoryGrants: Record<string, string\[\]>;/,
  );
  assert.match(createShared, /hostDirectoryGrants: \{\},/);
  assert.match(
    createShared,
    /payload\.host_directory_grants = initialPolicy\.hostDirectoryGrants;/,
  );
  assert.match(createDialog, /hostDirectoryGrants: \{\},/);
  assert.match(createDialog, /options=\{createOptions\?\.host_directories \?\? \[\]\}/);
  assert.match(
    createDialog,
    /hostDirectoryToolsForGrants\(nextValues\)/,
  );
});

test("agent editing does not duplicate the Sessions host directory grant editor", () => {
  assert.match(agentsPage, /host_directory_grants: Object\.fromEntries\(/);
  assert.doesNotMatch(agentsPage, /<HostDirectoryGrantSelector/);
  assert.doesNotMatch(agentsPage, /host_directory_grants: form\.hostDirectoryGrants,/);
});

test("session policy edit loads options, round-trips grants, and enables tools", () => {
  assert.match(
    sessionsPage,
    /host_directory_grants\?: Record<string, string\[\]>;/,
  );
  assert.match(
    sessionsPage,
    /hostDirectoryGrants: \{ \.\.\.\(session\.host_directory_grants \?\? \{\}\) \}/,
  );
  assert.match(
    sessionsPage,
    /loadAgentCreateOptions\(directoryPath\)[\s\S]*?setHostDirectoryOptions\(options\.host_directories \?\? \[\]\)/,
  );
  assert.match(
    sessionsPage,
    /host_directory_grants: policyDraft\.hostDirectoryGrants,/,
  );
  assert.match(sessionsPage, /function updateHostDirectoryGrants/);
  assert.match(sessionsPage, /hostDirectoryToolsForGrants\(nextGrants\)/);
  assert.match(sessionsPage, /setToolsDirty\(true\)/);
  assert.match(sessionsPage, /<HostDirectoryGrantSelector/);
});
