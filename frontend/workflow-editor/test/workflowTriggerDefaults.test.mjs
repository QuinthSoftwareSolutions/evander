import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const catalogGroups = readFileSync(new URL("../src/workflowCatalogGroups.ts", import.meta.url), "utf8");

test("adds the communication trigger and session renewal descriptions and default node shapes", () => {
  assert.match(
    app,
    /"trigger:communication:new-message": "Start from new messages on configured communication targets\."/,
  );
  assert.match(
    app,
    /"effect:session:renew-if-expired": "Renew an expired or soon-expiring session\."/,
  );
  assert.match(
    catalogGroups,
    /description: "Start from inbound chat, provider-neutral communication targets, Teams, Discord, or email activity\."/,
  );
  assert.match(
    app,
    /if \(kind === "trigger" && type === "trigger:communication:new-message"\) \{\n\s+return \{\n\s+id,\n\s+display_name: "Communication message",\n\s+type,\n\s+communication_target_ids: \[],\n\s+directory_path: "root",\n\s+author_ids: \[],\n\s+case_sensitive: false,\n\s+\};\n\s+\}/,
  );
  assert.match(
    app,
    /if \(kind === "effect" && type === "effect:session:renew-if-expired"\) \{\n\s+return \{\n\s+id,\n\s+display_name: "Renew expired or soon-expiring session",\n\s+type,\n\s+session_id: "session-id",\n\s+extend_by: "1d",\n\s+renew_if_expires_within: "2h",\n\s+\};\n\s+\}/,
  );
});
