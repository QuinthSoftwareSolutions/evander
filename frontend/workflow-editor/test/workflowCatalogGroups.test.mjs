import assert from "node:assert/strict";
import test from "node:test";

import {
  groupWorkflowNodeCatalogEntries,
} from "../src/workflowCatalogGroups.ts";

function catalogNode(kind, type, label) {
  return {
    kind,
    type,
    label,
    fields: [],
    input_ports: [],
    output_ports: [],
  };
}

test("groups effect node types by related functionality", () => {
  const groups = groupWorkflowNodeCatalogEntries([
    catalogNode("effect", "effect:semantic:upsert", "Semantic upsert"),
    catalogNode("effect", "effect:agent:prompt", "Agent prompt"),
    catalogNode("effect", "effect:vm:start", "VM start"),
    catalogNode("effect", "effect:agent:exec", "Agent exec"),
    catalogNode("effect", "effect:pr:create", "PR create"),
    catalogNode("effect", "effect:issue:update-metadata", "Issue update metadata"),
    catalogNode("effect", "effect:ado:update-work-item", "ADO update work item"),
    catalogNode("effect", "effect:bash:script", "Bash script"),
    catalogNode("effect", "effect:communication:send-message", "Send message"),
    catalogNode("effect", "effect:notification:send", "Send notification"),
  ], "effect");

  assert.deepEqual(
    groups.map((group) => ({
      label: group.label,
      types: group.entries.map((entry) => entry.type),
    })),
    [
      {
        label: "Agents",
        types: ["effect:agent:exec", "effect:agent:prompt"],
      },
      {
        label: "VMs",
        types: ["effect:vm:start"],
      },
      {
        label: "Shell & host scripts",
        types: ["effect:bash:script"],
      },
      {
        label: "Semantic store",
        types: ["effect:semantic:upsert"],
      },
      {
        label: "Issues & sessions",
        types: ["effect:ado:update-work-item", "effect:issue:update-metadata"],
      },
      {
        label: "Git & PRs",
        types: ["effect:pr:create"],
      },
      {
        label: "Communications",
        types: ["effect:communication:send-message"],
      },
      {
        label: "Events & notifications",
        types: ["effect:notification:send"],
      },
    ],
  );
});

test("groups trigger node types into domain families", () => {
  const groups = groupWorkflowNodeCatalogEntries([
    catalogNode("trigger", "trigger:teams:new-message", "Teams message"),
    catalogNode("trigger", "trigger:ticket:created", "Ticket created"),
    catalogNode("trigger", "trigger:cron", "Cron"),
    catalogNode("trigger", "trigger:event", "Internal event"),
    catalogNode("trigger", "trigger:git:push", "Git push"),
  ], "trigger");

  assert.deepEqual(groups.map((group) => group.label), [
    "Scheduled",
    "Platform events",
    "Tickets & reviews",
    "Communications",
    "Git",
  ]);
});

test("falls back to namespace groups for unknown node types", () => {
  const groups = groupWorkflowNodeCatalogEntries([
    catalogNode("query", "query:cloud-run:job", "Cloud job"),
    catalogNode("query", "query:cloud-run:status", "Cloud status"),
    catalogNode("query", "query:feature-flag:get", "Feature flag"),
  ], "query");

  assert.deepEqual(
    groups.map((group) => ({
      label: group.label,
      types: group.entries.map((entry) => entry.type),
    })),
    [
      {
        label: "Cloud Run",
        types: ["query:cloud-run:job", "query:cloud-run:status"],
      },
      {
        label: "Feature Flag",
        types: ["query:feature-flag:get"],
      },
    ],
  );
});
