import assert from "node:assert/strict";
import test from "node:test";

import { mergeAgentDetailWithSummary } from "../src/features/agents/agentRecordMerge.ts";

test("selected agent detail retains rich fields while live summary fields win", () => {
  const detail = {
    agent_id: "agent-1",
    backend: "container_worker_vm",
    target_name: "Agent 1",
    session_id: "session-1",
    directory_path: "root/team",
    status: "idle",
    started_at: null,
    finished_at: null,
    updated_at: "2026-07-31T10:00:00Z",
    host: {
      execution_count: 2,
      active_execution_count: 0,
      metadata: { detail_only: true },
      session_policy: { model: "gpt-5.6" },
    },
    capabilities: {
      start: false,
      shutdown: false,
      resume: false,
      enable_disable: false,
      kill: false,
      logs: true,
      stop: false,
      destroy: true,
    },
    prompt_queue: {
      record_count: 1,
      coalesced_count: 1,
      oldest_created_at: "2026-07-31T09:00:00Z",
      newest_updated_at: "2026-07-31T09:00:00Z",
      records: [{ prompt_id: "prompt-1" }],
      truncated_count: 0,
      read_error: null,
    },
  };
  const summary = {
    ...detail,
    status: "running",
    updated_at: "2026-07-31T10:05:00Z",
    host: {
      execution_count: 3,
      active_execution_count: 1,
      latest_execution_id: "execution-3",
    },
    capabilities: {
      ...detail.capabilities,
      stop: true,
      destroy: false,
    },
    prompt_queue: {
      record_count: 2,
      coalesced_count: 2,
      oldest_created_at: "2026-07-31T09:00:00Z",
      newest_updated_at: "2026-07-31T10:04:00Z",
      truncated_count: 0,
      read_error: null,
    },
  };

  const merged = mergeAgentDetailWithSummary(detail, summary);

  assert.equal(merged.status, "running");
  assert.equal(merged.updated_at, "2026-07-31T10:05:00Z");
  assert.equal(merged.host.execution_count, 3);
  assert.equal(merged.host.active_execution_count, 1);
  assert.equal(merged.host.latest_execution_id, "execution-3");
  assert.deepEqual(merged.host.metadata, { detail_only: true });
  assert.deepEqual(merged.host.session_policy, { model: "gpt-5.6" });
  assert.equal(merged.capabilities.stop, true);
  assert.equal(merged.capabilities.destroy, false);
  assert.equal(merged.prompt_queue.record_count, 2);
  assert.deepEqual(merged.prompt_queue.records, [{ prompt_id: "prompt-1" }]);
});
