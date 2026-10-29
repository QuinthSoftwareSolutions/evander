import assert from "node:assert/strict";
import test from "node:test";

import {
  configurationDestinationsForTab,
  configurationFieldDomId,
  configurationHash,
  resolveConfigurationLocation,
  searchConfiguration,
} from "../src/features/configuration/configurationNavigation.ts";

test("website logins are navigable and searchable in access configuration", () => {
  const access = {key: "access_approvals_audit", title: "Access, Approvals & Audit", help_text: null,
    underlying_domains: ["providers"], sections: []};
  assert.ok(configurationDestinationsForTab(access).some(d => d.key === "website-logins"));
  const location = resolveConfigurationLocation([access], configurationHash(access.key, "website-logins"));
  assert.equal(location.destinationKey, "website-logins");
  assert.ok(searchConfiguration([access], "website logins").some(r => r.destinationKey === "website-logins"));
});

const tabs = [
  {
    key: "people_communications",
    title: "People & Communications",
    help_text: "Messaging, email, and operator contacts.",
    underlying_domains: ["providers", "policy"],
    sections: [
      {
        key: "teams",
        title: "Teams",
        fields: [
          {
            source_domain: "providers",
            field_name: "teams_timeout_seconds",
            label: "Teams timeout seconds",
            kind: "float",
            options: [],
            scope: "directory",
            root_starts_editable: false,
            tooltip: "Maximum wait for Microsoft Teams provider requests.",
          },
        ],
      },
    ],
  },
  {
    key: "agent_runtime_launch",
    title: "Agent Runtime & Launch",
    help_text: "Execution and worker settings.",
    underlying_domains: ["providers"],
    sections: [
      {
        key: "container-worker-settings",
        title: "Container worker settings",
        fields: [
          {
            source_domain: "providers",
            field_name: "container_worker_job_timeout_seconds",
            label: "Job timeout seconds",
            kind: "int",
            options: [],
            scope: "directory",
            root_starts_editable: false,
            tooltip: "Maximum duration of a container worker job.",
          },
        ],
      },
    ],
  },
];

test("workflow event configuration remains reachable without role settings", () => {
  const events = { key: "automation_assignment", title: "Workflow Events", sections: [] };
  const location = resolveConfigurationLocation([events], configurationHash(events.key, "events"));
  assert.equal(location.destinationKey, "events");
  assert.ok(searchConfiguration([events], "events").some(result => result.destinationKey === "events"));
});

test("configuration destinations expose settings and explicit utilities", () => {
  assert.deepEqual(
    configurationDestinationsForTab(tabs[0]).map((destination) => destination.key),
    ["teams", "teams-auth", "validation", "directory-overrides", "effective-config", "parent-defaults"],
  );
});

test("configuration hashes and locations preserve area, section, and field", () => {
  const hash = configurationHash("agent_runtime_launch", "container-worker-settings", "container_worker_job_timeout_seconds");
  assert.equal(
    hash,
    "#configuration?config_tab=agent_runtime_launch&config_section=container-worker-settings&config_field=container_worker_job_timeout_seconds",
  );
  assert.deepEqual(resolveConfigurationLocation(tabs, hash), {
    tabKey: "agent_runtime_launch",
    destinationKey: "container-worker-settings",
    fieldName: "container_worker_job_timeout_seconds",
  });
});

test("unknown configuration locations fall back to the default area and first setting", () => {
  assert.deepEqual(resolveConfigurationLocation(tabs, "#configuration?config_tab=missing&config_section=missing&config_field=missing"), {
    tabKey: "people_communications",
    destinationKey: "teams",
    fieldName: null,
  });
});

test("configuration search finds friendly and technical terms with friendly matches first", () => {
  const friendly = searchConfiguration(tabs, "job timeout");
  assert.equal(friendly[0]?.kind, "field");
  assert.equal(friendly[0]?.fieldName, "container_worker_job_timeout_seconds");

  const technical = searchConfiguration(tabs, "container_worker_job");
  assert.equal(technical.some((result) => result.fieldName === "container_worker_job_timeout_seconds"), true);

  const domain = searchConfiguration(tabs, "providers teams_timeout");
  assert.equal(domain.some((result) => result.fieldName === "teams_timeout_seconds"), true);

  const utility = searchConfiguration(tabs, "delegated auth");
  assert.equal(utility[0]?.destinationKey, "teams-auth");
});

test("field DOM ids are stable and selector-safe", () => {
  assert.equal(
    configurationFieldDomId("people_communications", "field.with spaces"),
    "configuration-field-people_communications-field-with-spaces",
  );
});
