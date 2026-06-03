import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const catalogGroups = readFileSync(new URL("../src/workflowCatalogGroups.ts", import.meta.url), "utf8");

function sourceBetween(startText, endText) {
  const start = app.indexOf(startText);
  assert.notEqual(start, -1, `missing start marker: ${startText}`);
  const end = app.indexOf(endText, start);
  assert.notEqual(end, -1, `missing end marker: ${endText}`);
  return app.slice(start, end);
}

test("adds the approval and communication trigger descriptions and default node shapes", () => {
  assert.match(
    app,
    /"trigger:communication:new-message": "Start from new messages on configured communication targets\."/,
  );
  assert.match(
    app,
    /"trigger:approval:pending-created": "Start when a pending approval request is created\."/,
  );
  assert.match(
    app,
    /"effect:session:renew-if-expired": "Renew an expired or soon-expiring session\."/,
  );
  assert.match(
    catalogGroups,
    /description: "Start from tickets, approval requests, tracked issues, pull requests, or review requests\."/,
  );
  assert.match(
    app,
    /if \(kind === "trigger" && type === "trigger:communication:new-message"\) \{\n\s+return \{\n\s+id,\n\s+display_name: "Communication message",\n\s+type,\n\s+communication_target_ids: \[],\n\s+directory_path: "root",\n\s+author_ids: \[],\n\s+case_sensitive: false,\n\s+\};\n\s+\}/,
  );
  assert.match(
    app,
    /if \(kind === "trigger" && type === "trigger:approval:pending-created"\) \{\n\s+return \{\n\s+id,\n\s+display_name: "Pending approval",\n\s+type,\n\s+directory_path: "root",\n\s+action_types: \[],\n\s+tool_names: \[],\n\s+\};\n\s+\}/,
  );
  assert.match(
    app,
    /if \(kind === "effect" && type === "effect:session:renew-if-expired"\) \{\n\s+return \{\n\s+id,\n\s+display_name: "Renew expired or soon-expiring session",\n\s+type,\n\s+session_id: "session-id",\n\s+extend_by: "1d",\n\s+renew_if_expires_within: "2h",\n\s+\};\n\s+\}/,
  );
});

test("create panel does not default-expand the triggers group", () => {
  const createGroupBranch = sourceBetween(
    'className="create-group"',
    '<summary className="create-group-summary">',
  );

  assert.match(createGroupBranch, /open=\{hasTutorialAllowedNodeType\}/);
  assert.doesNotMatch(createGroupBranch, /group\.kind === "trigger"/);
});
