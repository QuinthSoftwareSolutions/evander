import assert from "node:assert/strict";
import test from "node:test";

import {
  isStructuredToolArgumentPolicies,
  toolArgumentPolicyDraftRowsFromValue,
  toolArgumentPolicyDraftRowsToValue,
  validateToolArgumentPolicyDraftRows,
} from "../src/workflowToolArgumentPolicies.ts";

test("converts structured tool argument policies to draft rows and back", () => {
  const policies = {
    run_script: [
      {
        id: "policy",
        constraints: [
          {
            id: "script",
            path: "script_name",
            operator: "fixed",
            value: "seed-demo",
            case_sensitive: true,
          },
          {
            id: "mode",
            path: "mode",
            operator: "allowlist",
            values: ["fast", 2, true, { nested: "value" }],
            case_sensitive: false,
          },
        ],
      },
      {
        id: "second",
        name: "Friendly policy",
        constraints: [
          {
            id: "pattern",
            path: "script_name",
            operator: "pattern",
            pattern: "^seed-.*$",
            case_sensitive: false,
          },
        ],
      },
    ],
  };

  assert.equal(isStructuredToolArgumentPolicies(policies), true);

  const rows = toolArgumentPolicyDraftRowsFromValue(policies);
  assert.ok(rows);
  assert.equal(rows[0].toolName, "run_script");
  assert.equal(rows[0].rules[0].constraints[0].valueText, "seed-demo");
  assert.equal(rows[0].rules[0].constraints[1].valuesText, 'fast\n2\ntrue\n{"nested":"value"}');
  assert.equal(rows[0].rules[1].constraints[0].pattern, "^seed-.*$");
  assert.equal(rows[0].rules[1].constraints[0].caseSensitive, false);

  const roundTrip = toolArgumentPolicyDraftRowsToValue(rows);
  assert.deepEqual(roundTrip.errors, []);
  assert.deepEqual(roundTrip.value, {
    run_script: [
      {
        id: "policy",
        name: null,
        constraints: [
          {
            id: "script",
            path: "script_name",
            operator: "fixed",
            value: "seed-demo",
            values: null,
            pattern: null,
            case_sensitive: true,
          },
          {
            id: "mode",
            path: "mode",
            operator: "allowlist",
            value: null,
            values: ["fast", 2, true, { nested: "value" }],
            pattern: null,
            case_sensitive: false,
          },
        ],
      },
      {
        id: "second",
        name: "Friendly policy",
        constraints: [
          {
            id: "pattern",
            path: "script_name",
            operator: "pattern",
            value: null,
            values: null,
            pattern: "^seed-.*$",
            case_sensitive: false,
          },
        ],
      },
    ],
  });
});

test("falls back to raw mode for unsupported shapes without mutating the source object", () => {
  const unsupported = {
    run_script: [
      {
        id: "policy",
        extra: "drop me",
        constraints: [
          {
            id: "script",
            path: "script_name",
            operator: "unknown",
            value: "seed-demo",
          },
        ],
      },
    ],
  };
  const snapshot = JSON.parse(JSON.stringify(unsupported));

  assert.equal(isStructuredToolArgumentPolicies(unsupported), false);
  assert.equal(toolArgumentPolicyDraftRowsFromValue(unsupported), null);
  assert.deepEqual(unsupported, snapshot);
  assert.equal(toolArgumentPolicyDraftRowsFromValue(["not-an-object"]), null);
  assert.equal(toolArgumentPolicyDraftRowsFromValue({ run_script: [{ id: "policy" }] }), null);
});

test("falls back to raw mode for structurally valid policies using unknown catalog tools", () => {
  const futurePolicy = {
    future_tool: [
      {
        id: "policy",
        constraints: [
          {
            id: "required",
            path: "payload",
            operator: "required",
          },
        ],
      },
    ],
  };

  assert.equal(isStructuredToolArgumentPolicies(futurePolicy), true);
  assert.equal(isStructuredToolArgumentPolicies(futurePolicy, ["run_script"]), false);
  assert.equal(toolArgumentPolicyDraftRowsFromValue(futurePolicy, ["run_script"]), null);
  assert.deepEqual(toolArgumentPolicyDraftRowsFromValue(futurePolicy, ["future_tool"])?.[0].toolName, "future_tool");
});

test("reports field-level validation errors for invalid draft rows", () => {
  const rows = [
    {
      toolName: "run_script",
      rules: [
        {
          id: "policy",
          name: "",
          constraints: [
            {
              id: "script",
              path: "",
              operator: "fixed",
              valueText: "",
              valuesText: "",
              pattern: "",
              caseSensitive: true,
            },
            {
              id: "script",
              path: "mode",
              operator: "allowlist",
              valueText: "",
              valuesText: "",
              pattern: "",
              caseSensitive: true,
            },
            {
              id: "pattern",
              path: "pattern_path",
              operator: "pattern",
              valueText: "",
              valuesText: "",
              pattern: "[",
              caseSensitive: true,
            },
          ],
        },
        {
          id: "policy",
          name: "",
          constraints: [],
        },
      ],
    },
    {
      toolName: "run_script",
      rules: [
        {
          id: "secondary",
          name: "",
          constraints: [
            {
              id: "mode",
              path: "mode",
              operator: "required",
              valueText: "",
              valuesText: "",
              pattern: "",
              caseSensitive: true,
            },
          ],
        },
      ],
    },
    {
      toolName: " ",
      rules: [
        {
          id: "blank-tool",
          name: "",
          constraints: [
            {
              id: "blank",
              path: "path",
              operator: "required",
              valueText: "",
              valuesText: "",
              pattern: "",
              caseSensitive: true,
            },
          ],
        },
      ],
    },
  ];

  const result = toolArgumentPolicyDraftRowsToValue(rows);
  const paths = result.errors.map((error) => error.path);

  assert.equal(result.value, null);
  assert.ok(paths.includes("tool_argument_policies.run_script[0].constraints[0].path"));
  assert.ok(paths.includes("tool_argument_policies.run_script[0].constraints[0].valueText"));
  assert.ok(paths.includes("tool_argument_policies.run_script[0].constraints[1].id"));
  assert.ok(paths.includes("tool_argument_policies.run_script[0].constraints[1].valuesText"));
  assert.ok(paths.includes("tool_argument_policies.run_script[0].constraints[2].pattern"));
  assert.ok(paths.includes("tool_argument_policies.run_script[1].id"));
  assert.ok(paths.includes("tool_argument_policies.run_script[1].constraints"));
  assert.ok(paths.includes("tool_argument_policies.run_script.toolName"));
  assert.ok(paths.includes("tool_argument_policies[2].toolName"));

  const messages = result.errors.map((error) => error.message);
  assert.ok(messages.some((message) => message.includes("duplicate rule id")));
  assert.ok(messages.some((message) => message.includes("duplicate constraint id")));
  assert.ok(messages.some((message) => message.includes("pattern is not a valid JavaScript regular expression")));
  assert.ok(messages.some((message) => message.includes("valuesText must contain at least one value")));
  assert.ok(messages.some((message) => message.includes("valueText must be a non-empty string")));
  assert.ok(messages.some((message) => message.includes("toolName must be a non-empty string")));
});

test("validateToolArgumentPolicyDraftRows returns the same validation errors without converting a value", () => {
  const errors = validateToolArgumentPolicyDraftRows([
    {
      toolName: "run_script",
      rules: [
        {
          id: "",
          name: "",
          constraints: [
            {
              id: "",
              path: "",
              operator: "",
              valueText: "",
              valuesText: "",
              pattern: "",
              caseSensitive: true,
            },
          ],
        },
      ],
    },
  ]);

  assert.ok(errors.some((error) => error.path === "tool_argument_policies.run_script[0].id"));
  assert.ok(errors.some((error) => error.path === "tool_argument_policies.run_script[0].constraints[0].id"));
  assert.ok(errors.some((error) => error.path === "tool_argument_policies.run_script[0].constraints[0].path"));
  assert.ok(errors.some((error) => error.path === "tool_argument_policies.run_script[0].constraints[0].operator"));
});
