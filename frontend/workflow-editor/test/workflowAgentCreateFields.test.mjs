import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

test("agent create inspector fields are gated by execution backend", () => {
  assert.match(app, /const AGENT_CREATE_NODE_TYPE = "effect:agent:create";/);
  assert.match(app, /function agentCreateFieldVisibleForBackend\(/);
  assert.match(app, /function agentCreateFieldOptionsForBackend\(/);
  assert.match(app, /if \(field\.key === "execution_backend"\) return true;/);
  assert.match(app, /if \(!executionBackend\) return false;/);
  assert.match(
    app,
    /selectedNode\?\.type === AGENT_CREATE_NODE_TYPE[\s\S]*agentCreateFieldVisibleForBackend\(field, executionBackend\)/,
  );
  assert.match(
    app,
    /selectedNode\.type === AGENT_CREATE_NODE_TYPE[\s\S]*pruneAgentCreateBackendFieldValues\(nextKnownValues, selectedCatalogEntry\.fields\)/,
  );
  assert.match(
    app,
    /selectedNodeType === AGENT_CREATE_NODE_TYPE[\s\S]*agentCreateFieldOptionsForBackend\(/,
  );
});
