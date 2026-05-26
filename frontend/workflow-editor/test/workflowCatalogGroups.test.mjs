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
    catalogNode("effect", "effect:state:update", "State update"),
    catalogNode("effect", "effect:agent:prompt", "Agent prompt"),
    catalogNode("effect", "effect:vm:start", "VM start"),
    catalogNode("effect", "effect:agent:exec", "Agent exec"),
    catalogNode("effect", "effect:pr:create", "PR create"),
    catalogNode("effect", "effect:pull-request:update", "Pull request update"),
    catalogNode("effect", "effect:issue:update-metadata", "Issue update metadata"),
    catalogNode("effect", "effect:ado:update-work-item", "ADO update work item"),
    catalogNode("effect", "effect:bash:script", "Bash script"),
    catalogNode("effect", "effect:http:request", "HTTP request"),
    catalogNode("effect", "effect:runtime:diagnostics-snapshot", "Runtime diagnostics snapshot"),
    catalogNode("effect", "effect:artifact:import-published", "Import published artifact"),
    catalogNode("effect", "effect:artifact:publish-artifacts", "Publish artifacts"),
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
        label: "HTTP",
        types: ["effect:http:request"],
      },
      {
        label: "Runtime diagnostics",
        types: ["effect:runtime:diagnostics-snapshot"],
      },
      {
        label: "Semantic store",
        types: ["effect:semantic:upsert"],
      },
      {
        label: "Runtime state",
        types: ["effect:state:update"],
      },
      {
        label: "Artifacts",
        types: ["effect:artifact:import-published", "effect:artifact:publish-artifacts"],
      },
      {
        label: "Issues & sessions",
        types: ["effect:ado:update-work-item", "effect:issue:update-metadata"],
      },
      {
        label: "Git & PRs",
        types: ["effect:pr:create", "effect:pull-request:update"],
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
    catalogNode("trigger", "trigger:communication:new-message", "Communication message"),
    catalogNode("trigger", "trigger:discord:new-message", "Discord message"),
    catalogNode("trigger", "trigger:teams:new-message", "Teams message"),
    catalogNode("trigger", "trigger:teams:direct-message", "Teams direct message"),
    catalogNode("trigger", "trigger:email:new-message", "Email message"),
    catalogNode("trigger", "trigger:ticket:created", "Ticket created"),
    catalogNode("trigger", "trigger:cron", "Cron"),
    catalogNode("trigger", "trigger:event", "Internal event"),
    catalogNode("trigger", "trigger:ci:run:completed", "CI run completed"),
    catalogNode("trigger", "trigger:http:webhook", "Webhook"),
    catalogNode("trigger", "trigger:git:push", "Git push"),
  ], "trigger");

  assert.deepEqual(
    groups.map((group) => ({
      label: group.label,
      types: group.entries.map((entry) => entry.type),
    })),
    [
      {
        label: "Scheduled",
        types: ["trigger:cron"],
      },
      {
        label: "Platform events",
        types: ["trigger:event", "trigger:http:webhook"],
      },
      {
        label: "Tickets & reviews",
        types: ["trigger:ticket:created"],
      },
      {
        label: "Communications",
        types: [
          "trigger:communication:new-message",
          "trigger:discord:new-message",
          "trigger:email:new-message",
          "trigger:teams:direct-message",
          "trigger:teams:new-message",
        ],
      },
      {
        label: "Git",
        types: ["trigger:ci:run:completed", "trigger:git:push"],
      },
    ],
  );
});

test("groups pull request query nodes with git metadata", () => {
  const groups = groupWorkflowNodeCatalogEntries([
    catalogNode("query", "query:pull-request:list", "Pull request list"),
    catalogNode("query", "query:pull-request:get", "Pull request get"),
    catalogNode("query", "query:git:pr-note", "Git PR note"),
    catalogNode("query", "query:issue:get", "Issue get"),
    catalogNode("query", "query:communication:messages", "Communication messages"),
    catalogNode("query", "query:state:get", "State get"),
  ], "query");

  assert.deepEqual(
    groups.map((group) => ({
      label: group.label,
      types: group.entries.map((entry) => entry.type),
    })),
    [
      {
        label: "Runtime state",
        types: ["query:state:get"],
      },
      {
        label: "Work tracking",
        types: ["query:issue:get"],
      },
      {
        label: "Communications",
        types: ["query:communication:messages"],
      },
      {
        label: "Git & PRs",
        types: ["query:git:pr-note", "query:pull-request:get", "query:pull-request:list"],
      },
    ],
  );
});

test("groups switch gate types with flow control", () => {
  const groups = groupWorkflowNodeCatalogEntries([
    catalogNode("gate", "gate:await-agent", "Await agent"),
    catalogNode("gate", "gate:for", "For each item"),
    catalogNode("gate", "gate:merge", "Merge"),
    catalogNode("gate", "gate:switch", "Switch"),
  ], "gate");

  const flowControl = groups.find((group) => group.label === "Flow control");
  assert.ok(flowControl);
  assert.deepEqual(flowControl.entries.map((entry) => entry.type), [
    "gate:await-agent",
    "gate:for",
    "gate:merge",
    "gate:switch",
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
