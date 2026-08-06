import assert from "node:assert/strict";
import { test } from "node:test";

import { isInheritedApprovalRequiredForTool } from "../src/features/sessions/sessionPolicy.ts";

test("local session approval requirements stay removable", () => {
  assert.equal(
    isInheritedApprovalRequiredForTool(
      "prompt_agent",
      {
        inherited_approval_required_tools: [],
        approval_required_tools: ["prompt_agent"],
        mcp_tool_policies: {},
      },
      ["prompt_agent"],
    ),
    false,
  );
});

test("inherited approval requirements stay forced", () => {
  assert.equal(
    isInheritedApprovalRequiredForTool(
      "prompt_agent",
      {
        inherited_approval_required_tools: ["prompt_agent"],
        approval_required_tools: ["prompt_agent"],
        mcp_tool_policies: {},
      },
      [],
    ),
    true,
  );
  assert.equal(
    isInheritedApprovalRequiredForTool(
      "prompt_agent",
      {
        approval_required_tools: ["prompt_agent"],
        mcp_tool_policies: {},
      },
      [],
    ),
    true,
  );
  assert.equal(
    isInheritedApprovalRequiredForTool(
      "prompt_agent",
      {
        approval_required_tools: [],
        mcp_tool_policies: {
          prompt_agent: {
            require_approval: true,
          },
        },
      },
      ["prompt_agent"],
    ),
    true,
  );
});
