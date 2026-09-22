import { readWorkflowEditorSource } from "./workflowEditorSource.mjs";
import assert from "node:assert/strict";
import test from "node:test";

const app = readWorkflowEditorSource();

test("agent create inspector fields are gated by execution backend", () => {
  assert.match(app, /const AGENT_CREATE_NODE_TYPE = "effect:agent:create";/);
  assert.match(app, /function agentCreateFieldVisibleForBackend\(/);
  assert.match(app, /function agentCreateFieldOptionsForBackend\(/);
  assert.match(app, /function validateWorkflowAgentCreateDiagnostics\(/);
  assert.match(app, /const CLIENT_RECOMPUTED_DIAGNOSTIC_CODES = new Set\(/);
  assert.match(app, /"agent_create_missing_execution_backend"/);
  assert.match(app, /remoteOnlyWorkflowDiagnostics\(backendValidationDiagnostics\)/);
  assert.match(app, /function fieldSelectionOnly\(/);
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
  assert.match(app, /field\.options\.length > 0 \|\| fieldSelectionOnly\(field\)/);
});
