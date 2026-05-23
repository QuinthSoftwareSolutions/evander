import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer } from "vite";

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  };
}

function createDirectoryTree() {
  return {
    directory: {
      id: "root",
      name: "root",
      parent_id: null,
      created_at: "2026-05-16T00:00:00Z",
      metadata: {},
      path: "root",
    },
    config_domains: [],
    children: [],
  };
}

function createSampleTutorialSteps(active) {
  return [
    {
      step_id: "launch",
      title: "Launch the sample",
      body: "Create the guided in-memory run without touching runtime stores.",
      status: active ? "completed" : "active",
      node_id: null,
    },
    {
      step_id: "inspect",
      title: "Inspect the run",
      body: "Open the existing inspector and review the simulated graph.",
      status: active ? "active" : "pending",
      node_id: "sample:first-run:run",
    },
    {
      step_id: "approval",
      title: "Review the approval boundary",
      body: "The gate stays visibly pending so the safety boundary is easy to inspect.",
      status: "pending",
      node_id: "sample-approval-boundary",
    },
    {
      step_id: "safe-stop",
      title: "Review the safe stop",
      body: "The final effect fails safely and remains resumable in-memory.",
      status: "pending",
      node_id: "sample-safe-stop",
    },
    {
      step_id: "reset",
      title: "Reset the sample",
      body: "Clear the sample back to an idle in-memory state.",
      status: "pending",
      node_id: null,
    },
  ];
}

function createSampleRunSummary() {
  return {
    event_id: "sample:first-run:run",
    run_state: "errored",
    trace_id: "sample:first-run:trace",
    source_node_id: "sample-trigger",
    source_trigger_type: "trigger:event",
    delivery_state: "delivered",
    occurred_at: "2026-05-16T12:00:00Z",
    latest_activity_at: "2026-05-16T12:00:08Z",
    active_nodes: [
      {
        node_id: "sample-trigger",
        status: "delivered",
        count: 1,
        source: "sample",
        started_at: "2026-05-16T12:00:00Z",
        finished_at: "2026-05-16T12:00:01Z",
      },
      {
        node_id: "sample-agent-prompt",
        status: "succeeded",
        count: 1,
        source: "sample",
        started_at: "2026-05-16T12:00:02Z",
        finished_at: "2026-05-16T12:00:04Z",
      },
      {
        node_id: "sample-approval-boundary",
        status: "waiting",
        count: 1,
        source: "sample",
        started_at: "2026-05-16T12:00:05Z",
      },
      {
        node_id: "sample-safe-stop",
        status: "failed",
        count: 1,
        source: "sample",
        started_at: "2026-05-16T12:00:07Z",
        finished_at: "2026-05-16T12:00:08Z",
        error: "Simulated safe stop: the sample never touches runtime stores.",
      },
    ],
    failed_node_id: "sample-safe-stop",
    failed_node_status: "failed",
    failed_at: "2026-05-16T12:00:08Z",
    error: "Simulated safe stop: the sample never touches runtime stores.",
    resumable: true,
  };
}

function createSampleDetail() {
  const launchedAt = "2026-05-16T12:00:00Z";
  const summary = createSampleRunSummary();
  return {
    ...summary,
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:00:10Z",
    matched_by: ["event_id"],
    lookup_candidates: [
      {
        event_id: summary.event_id,
        trace_id: summary.trace_id,
        latest_activity_at: summary.latest_activity_at,
        matched_by: ["event_id"],
      },
    ],
    recovery: {
      clear: {
        eligible: true,
        reason: "The guided sample can be cleared from in-memory state.",
        event_id: summary.event_id,
        source_event_id: summary.event_id,
      },
      resume: {
        eligible: true,
        reason: "The failed safe-stop node has a resumable in-memory payload.",
        event_id: summary.event_id,
        target_node_id: "sample-safe-stop",
        source_node_id: "sample-approval-boundary",
        source_trigger_type: "gate:await-agent",
        source_event_id: summary.event_id,
      },
    },
    trigger_event: {
      event_id: summary.event_id,
      trigger_id: "sample-trigger",
      trigger_type: "trigger:event",
      trace_id: summary.trace_id,
      occurred_at: launchedAt,
      delivery_state: "delivered",
      payload: {
        event_type: "sample:first-run",
        correlation_key: summary.trace_id,
        message: "Launch the guided in-memory sample.",
      },
    },
    graph: {
      config_revision: "sample:first-run:revision",
      config: {
        triggers: [
          {
            id: "sample-trigger",
            display_name: "Sample trigger",
            type: "trigger:event",
          },
        ],
        gates: [
          {
            id: "sample-approval-boundary",
            display_name: "Await approval",
            type: "gate:await-agent",
          },
        ],
        effects: [
          {
            id: "sample-agent-prompt",
            display_name: "Prompt simulated agent",
            type: "effect:agent:prompt",
          },
          {
            id: "sample-safe-stop",
            display_name: "Safe stop notification",
            type: "effect:notification:send",
          },
        ],
        queries: [],
        edges: [
          {
            source_id: "sample-trigger",
            target_id: "sample-agent-prompt",
          },
          {
            source_id: "sample-agent-prompt",
            target_id: "sample-approval-boundary",
          },
          {
            source_id: "sample-approval-boundary",
            target_id: "sample-safe-stop",
          },
        ],
      },
      snapshot: {
        revision: "sample:first-run:revision",
        captured_at: launchedAt,
        snapshot_type: "simulated_in_memory_sample",
        source: "admin.sample_workflow",
        node_count: 4,
        edge_count: 3,
        triggers: [
          {
            id: "sample-trigger",
            display_name: "Sample trigger",
            type: "trigger:event",
          },
        ],
        gates: [
          {
            id: "sample-approval-boundary",
            display_name: "Await approval",
            type: "gate:await-agent",
          },
        ],
        effects: [
          {
            id: "sample-agent-prompt",
            display_name: "Prompt simulated agent",
            type: "effect:agent:prompt",
          },
          {
            id: "sample-safe-stop",
            display_name: "Safe stop notification",
            type: "effect:notification:send",
          },
        ],
        queries: [],
        edges: [
          {
            source_id: "sample-trigger",
            target_id: "sample-agent-prompt",
          },
          {
            source_id: "sample-agent-prompt",
            target_id: "sample-approval-boundary",
          },
          {
            source_id: "sample-approval-boundary",
            target_id: "sample-safe-stop",
          },
        ],
        workflow_graphs: [
          {
            id: "sample:first-run:graph",
            name: "Sample first run",
            description: "Deterministic in-memory guided sample workflow.",
            directory_paths: ["root"],
            user_ids: ["operator"],
            node_ids: [
              "sample-trigger",
              "sample-agent-prompt",
              "sample-approval-boundary",
              "sample-safe-stop",
            ],
          },
        ],
      },
      data_availability: {
        graph_snapshot: "available",
        binding_trace: "available",
        approvals: "available",
        agent_links: "simulated_in_memory",
        communication_artifacts: "simulated_in_memory",
        tool_calls: "available",
        trigger_event: "available",
      },
    },
    tool_call_links: [
      {
        tool_name: "prompt_agent",
        created_at: "2026-05-16T12:00:02Z",
        status: "simulated_in_memory",
        decision: "allowed",
        request_id: "sample:first-run:request",
        correlation_id: "sample:first-run:dispatch",
        approval_request_id: "sample:first-run:approval",
        workflow_run_id: summary.event_id,
        trace_id: summary.trace_id,
        session_id: "sample:first-run:session",
        node_id: "sample-agent-prompt",
        node_type: "effect:agent:prompt",
        args_summary: "Prompt a simulated agent with the sample trigger payload.",
        result_summary: "The in-memory agent acknowledged the prompt without touching runtime stores.",
        result_status: "simulated_in_memory",
        result_empty: false,
        args: {
          prompt: "Launch the guided sample run.",
          trigger_event_id: summary.event_id,
          session_id: "sample:first-run:session",
        },
        result: {
          status: "simulated_in_memory",
          dispatch_id: "sample:first-run:dispatch",
          thread_id: "sample:first-run:thread",
        },
        redacted_args: {
          prompt: "Launch the guided sample run.",
          trigger_event_id: summary.event_id,
          session_id: "sample:first-run:session",
        },
        redacted_result: {
          status: "simulated_in_memory",
          dispatch_id: "sample:first-run:dispatch",
          thread_id: "sample:first-run:thread",
        },
      },
    ],
    nodes: [
      {
        node_id: "sample-trigger",
        node_type: "trigger:event",
        display_name: "Sample trigger",
        source_kind: "trigger_event",
        event_id: summary.event_id,
        trace_id: summary.trace_id,
        status: "delivered",
        started_at: "2026-05-16T12:00:00Z",
        finished_at: "2026-05-16T12:00:01Z",
        duration_ms: 1000,
        occurred_at: "2026-05-16T12:00:00Z",
        delivery_state: "delivered",
        resolved_config: {
          event_type: "sample:first-run",
          correlation_key: summary.trace_id,
        },
        binding_trace: {
          consumed_output_keys: [],
          entries: [],
        },
        input_payload: {
          event_type: "sample:first-run",
          correlation_key: summary.trace_id,
          message: "Launch the guided in-memory sample.",
        },
        outputs: {
          event_type: "sample:first-run",
          correlation_key: summary.trace_id,
        },
        error: null,
        config_revision: "sample:first-run:revision",
        missing_fields: [],
        records: [
          {
            record_kind: "trigger_event",
            event_id: summary.event_id,
            trace_id: summary.trace_id,
            node_id: "sample-trigger",
            node_type: "trigger:event",
            status: "delivered",
            started_at: "2026-05-16T12:00:00Z",
            finished_at: "2026-05-16T12:00:01Z",
            duration_ms: 1000,
            source_trigger_type: "trigger:event",
            occurred_at: "2026-05-16T12:00:00Z",
            input_payload: {
              event_type: "sample:first-run",
              correlation_key: summary.trace_id,
              message: "Launch the guided in-memory sample.",
            },
            outputs: {
              event_type: "sample:first-run",
              correlation_key: summary.trace_id,
            },
            resolved_config: {
              event_type: "sample:first-run",
              correlation_key: summary.trace_id,
            },
            binding_trace: {
              consumed_output_keys: [],
              entries: [],
            },
            config_revision: "sample:first-run:revision",
          },
        ],
        approval_links: [],
        agent_links: [],
        communication_artifact_links: [],
        tool_call_links: [],
        retry_resume_eligible: false,
      },
      {
        node_id: "sample-agent-prompt",
        node_type: "effect:agent:prompt",
        display_name: "Prompt simulated agent",
        source_kind: "effect_execution",
        execution_id: "sample:first-run:execution",
        event_id: summary.event_id,
        trace_id: summary.trace_id,
        status: "succeeded",
        started_at: "2026-05-16T12:00:02Z",
        finished_at: "2026-05-16T12:00:04Z",
        duration_ms: 2000,
        occurred_at: "2026-05-16T12:00:02Z",
        resolved_config: {
          session_id: "sample:first-run:session",
          vm_name: "sample-in-memory",
          prompt: "Launch the guided sample run.",
          dispatch_id: "sample:first-run:dispatch",
          thread_id: "sample:first-run:thread",
          execution_id: "sample:first-run:execution",
          artifact_ids: ["comm_0123456789abcdef0123456789abcdef"],
          request_id: "sample:first-run:approval",
        },
        binding_trace: {
          consumed_output_keys: ["sample-trigger.event_type"],
          entries: [
            {
              config_path: "$.prompt",
              expression: "${{sample-trigger.event_type}}",
              reference: "sample-trigger.event_type",
              source_node_id: "sample-trigger",
              output_path: "event_type",
              fallback_used: false,
              status: "resolved",
              resolved_value_preview: '"sample:first-run"',
              value_type: "str",
              error: null,
            },
          ],
        },
        input_payload: {
          event_type: "sample:first-run",
          correlation_key: summary.trace_id,
          request_id: "sample:first-run:approval",
        },
        outputs: {
          dispatch_id: "sample:first-run:dispatch",
          thread_id: "sample:first-run:thread",
          execution_id: "sample:first-run:execution",
          prompt_summary: "The sample prompt was handled in memory.",
        },
        error: null,
        config_revision: "sample:first-run:revision",
        missing_fields: [],
        records: [
          {
            record_kind: "effect_execution",
            execution_id: "sample:first-run:execution",
            event_id: summary.event_id,
            trace_id: summary.trace_id,
            node_id: "sample-agent-prompt",
            node_type: "effect:agent:prompt",
            status: "succeeded",
            started_at: "2026-05-16T12:00:02Z",
            finished_at: "2026-05-16T12:00:04Z",
            duration_ms: 2000,
            source_trigger_type: "trigger:event",
            occurred_at: "2026-05-16T12:00:02Z",
            input_payload: {
              event_type: "sample:first-run",
              correlation_key: summary.trace_id,
              request_id: "sample:first-run:approval",
            },
            outputs: {
              dispatch_id: "sample:first-run:dispatch",
              thread_id: "sample:first-run:thread",
              execution_id: "sample:first-run:execution",
              prompt_summary: "The sample prompt was handled in memory.",
            },
            resolved_config: {
              session_id: "sample:first-run:session",
              vm_name: "sample-in-memory",
              prompt: "Launch the guided sample run.",
              dispatch_id: "sample:first-run:dispatch",
              thread_id: "sample:first-run:thread",
              execution_id: "sample:first-run:execution",
              artifact_ids: ["comm_0123456789abcdef0123456789abcdef"],
              request_id: "sample:first-run:approval",
            },
            binding_trace: {
              consumed_output_keys: ["sample-trigger.event_type"],
              entries: [
                {
                  config_path: "$.prompt",
                  expression: "${{sample-trigger.event_type}}",
                  reference: "sample-trigger.event_type",
                  source_node_id: "sample-trigger",
                  output_path: "event_type",
                  fallback_used: false,
                  status: "resolved",
                  resolved_value_preview: '"sample:first-run"',
                  value_type: "str",
                  error: null,
                },
              ],
            },
            config_revision: "sample:first-run:revision",
          },
        ],
        approval_links: [],
        agent_links: [
          {
            node_id: "sample-agent-prompt",
            node_type: "effect:agent:prompt",
            session_id: "sample:first-run:session",
            vm_name: "sample-in-memory",
            agent_id: "sample:first-run:agent",
            backend: "simulated_in_memory",
            agent_backend: "simulated_in_memory",
            target_name: "sample-first-run",
            agent_available: false,
            agent_status: "simulated_in_memory",
            agent_missing_reason: "No real agent was launched for the in-memory sample.",
            session_available: false,
            session_status: "session_missing",
            session_missing_reason: "No real session was created for the in-memory sample.",
            session_href: null,
            log_available: false,
            log_status: "session_missing",
            log_missing_reason: "No log file exists for the in-memory sample.",
            prompt: "Launch the guided sample run.",
            prompt_summary: "Simulated prompt for the sample workflow.",
            prompt_status: "simulated_in_memory",
            result_summary: "The in-memory agent acknowledged the prompt.",
            result_status: "simulated_in_memory",
            result_empty: false,
            dispatch_id: "sample:first-run:dispatch",
            thread_id: "sample:first-run:thread",
            execution_id: "sample:first-run:execution",
            artifact_ids: ["comm_0123456789abcdef0123456789abcdef"],
            status: "simulated_in_memory",
            waiting: false,
            completed: true,
            log_reference: null,
          },
        ],
        communication_artifact_links: [
          {
            artifact_id: "comm_0123456789abcdef0123456789abcdef",
            session_id: "sample:first-run:session",
            provider: "sample",
            original_filename: "sample-agent-prompt.txt",
            filename: "sample-agent-prompt.txt",
            mime_type: "text/plain",
            byte_count: 512,
            sha256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            status: "simulated_in_memory",
            metadata: {
              source: "admin.sample_workflow",
              note: "In-memory artifact reference only.",
            },
          },
        ],
        tool_call_links: [
          {
            tool_name: "prompt_agent",
            created_at: "2026-05-16T12:00:02Z",
            status: "simulated_in_memory",
            decision: "allowed",
            request_id: "sample:first-run:request",
            correlation_id: "sample:first-run:dispatch",
            approval_request_id: "sample:first-run:approval",
            workflow_run_id: summary.event_id,
            trace_id: summary.trace_id,
            session_id: "sample:first-run:session",
            node_id: "sample-agent-prompt",
            node_type: "effect:agent:prompt",
            args_summary: "Prompt a simulated agent with the sample trigger payload.",
            result_summary: "The in-memory agent acknowledged the prompt without touching runtime stores.",
            result_status: "simulated_in_memory",
            result_empty: false,
            args: {
              prompt: "Launch the guided sample run.",
              trigger_event_id: summary.event_id,
              session_id: "sample:first-run:session",
            },
            result: {
              status: "simulated_in_memory",
              dispatch_id: "sample:first-run:dispatch",
              thread_id: "sample:first-run:thread",
            },
            redacted_args: {
              prompt: "Launch the guided sample run.",
              trigger_event_id: summary.event_id,
              session_id: "sample:first-run:session",
            },
            redacted_result: {
              status: "simulated_in_memory",
              dispatch_id: "sample:first-run:dispatch",
              thread_id: "sample:first-run:thread",
            },
          },
        ],
        retry_resume_eligible: false,
      },
      {
        node_id: "sample-approval-boundary",
        node_type: "gate:await-agent",
        display_name: "Await approval",
        source_kind: "gate_execution",
        execution_id: "sample:first-run:gate-execution",
        event_id: summary.event_id,
        trace_id: summary.trace_id,
        status: "waiting",
        started_at: "2026-05-16T12:00:05Z",
        duration_ms: null,
        occurred_at: "2026-05-16T12:00:05Z",
        resolved_config: {
          session_id: "sample:first-run:session",
          dispatch_id: "sample:first-run:dispatch",
          prompt: "Launch the guided sample run.",
        },
        binding_trace: {
          consumed_output_keys: ["sample-agent-prompt.dispatch_id"],
          entries: [
            {
              config_path: "$.dispatch_id",
              expression: "${{sample-agent-prompt.dispatch_id}}",
              reference: "sample-agent-prompt.dispatch_id",
              source_node_id: "sample-agent-prompt",
              output_path: "dispatch_id",
              fallback_used: false,
              status: "resolved",
              resolved_value_preview: '"sample:first-run:dispatch"',
              value_type: "str",
              error: null,
            },
          ],
        },
        input_payload: {
          session_id: "sample:first-run:session",
          dispatch_id: "sample:first-run:dispatch",
          approval_request_id: "sample:first-run:approval",
        },
        outputs: {
          status: "pending",
          approval_request_id: "sample:first-run:approval",
        },
        error: null,
        config_revision: "sample:first-run:revision",
        missing_fields: [],
        records: [
          {
            record_kind: "gate_execution",
            execution_id: "sample:first-run:gate-execution",
            event_id: summary.event_id,
            trace_id: summary.trace_id,
            node_id: "sample-approval-boundary",
            node_type: "gate:await-agent",
            status: "waiting",
            started_at: "2026-05-16T12:00:05Z",
            source_node_id: "sample-agent-prompt",
            source_trigger_type: "effect:agent:prompt",
            occurred_at: "2026-05-16T12:00:05Z",
            input_payload: {
              session_id: "sample:first-run:session",
              dispatch_id: "sample:first-run:dispatch",
              approval_request_id: "sample:first-run:approval",
            },
            outputs: {
              status: "pending",
              approval_request_id: "sample:first-run:approval",
            },
            resolved_config: {
              session_id: "sample:first-run:session",
              dispatch_id: "sample:first-run:dispatch",
              prompt: "Launch the guided sample run.",
            },
            binding_trace: {
              consumed_output_keys: ["sample-agent-prompt.dispatch_id"],
              entries: [
                {
                  config_path: "$.dispatch_id",
                  expression: "${{sample-agent-prompt.dispatch_id}}",
                  reference: "sample-agent-prompt.dispatch_id",
                  source_node_id: "sample-agent-prompt",
                  output_path: "dispatch_id",
                  fallback_used: false,
                  status: "resolved",
                  resolved_value_preview: '"sample:first-run:dispatch"',
                  value_type: "str",
                  error: null,
                },
              ],
            },
            config_revision: "sample:first-run:revision",
          },
        ],
        approval_links: [
          {
            request_id: "sample:first-run:approval",
            session_id: "sample:first-run:session",
            action_type: "effect:agent:prompt",
            tool_name: "prompt_agent",
            directory_path: "root",
            payload_digest: "sample:first-run:digest",
            status: "pending",
            approver: null,
            decision_at: null,
            decision_reason: null,
            expires_at: "2026-05-17T12:00:00Z",
            human_summary: "Approve the simulated prompt before a real agent would be contacted.",
          },
        ],
        agent_links: [],
        communication_artifact_links: [],
        tool_call_links: [],
        retry_resume_eligible: false,
      },
      {
        node_id: "sample-safe-stop",
        node_type: "effect:notification:send",
        display_name: "Safe stop notification",
        source_kind: "effect_execution",
        execution_id: "sample:first-run:safe-stop-execution",
        event_id: summary.event_id,
        trace_id: summary.trace_id,
        status: "failed",
        started_at: "2026-05-16T12:00:07Z",
        finished_at: "2026-05-16T12:00:08Z",
        duration_ms: 1000,
        occurred_at: "2026-05-16T12:00:07Z",
        error: "Simulated safe stop: the sample never touches runtime stores.",
        binding_trace: {
          consumed_output_keys: ["sample-approval-boundary.status"],
          entries: [
            {
              config_path: "$.status",
              expression: "${{sample-approval-boundary.status}}",
              reference: "sample-approval-boundary.status",
              source_node_id: "sample-approval-boundary",
              output_path: "status",
              fallback_used: false,
              status: "resolved",
              resolved_value_preview: '"pending"',
              value_type: "str",
              error: null,
            },
          ],
        },
        input_payload: {
          reason: "pending approval boundary",
          source_node_id: "sample-approval-boundary",
          source_trigger_type: "gate:await-agent",
          trace_id: summary.trace_id,
        },
        outputs: {
          notification_status: "suppressed",
          safe_stop: true,
          result_summary: "The sample stopped safely in memory.",
        },
        resolved_config: {
          channel: "operator-console",
          message: "Safe stop reached for the guided sample.",
          trace_id: summary.trace_id,
        },
        config_revision: "sample:first-run:revision",
        retry_resume_eligible: true,
        retry_resume_reason: "Failed node has a resumable input payload.",
        missing_fields: [],
        records: [
          {
            record_kind: "effect_execution",
            execution_id: "sample:first-run:safe-stop-execution",
            event_id: summary.event_id,
            trace_id: summary.trace_id,
            node_id: "sample-safe-stop",
            node_type: "effect:notification:send",
            status: "failed",
            started_at: "2026-05-16T12:00:07Z",
            finished_at: "2026-05-16T12:00:08Z",
            duration_ms: 1000,
            source_node_id: "sample-approval-boundary",
            source_trigger_type: "gate:await-agent",
            occurred_at: "2026-05-16T12:00:07Z",
            input_payload: {
              reason: "pending approval boundary",
              source_node_id: "sample-approval-boundary",
              source_trigger_type: "gate:await-agent",
              trace_id: summary.trace_id,
            },
            outputs: {
              notification_status: "suppressed",
              safe_stop: true,
              result_summary: "The sample stopped safely in memory.",
            },
            resolved_config: {
              channel: "operator-console",
              message: "Safe stop reached for the guided sample.",
              trace_id: summary.trace_id,
            },
            binding_trace: {
              consumed_output_keys: ["sample-approval-boundary.status"],
              entries: [
                {
                  config_path: "$.status",
                  expression: "${{sample-approval-boundary.status}}",
                  reference: "sample-approval-boundary.status",
                  source_node_id: "sample-approval-boundary",
                  output_path: "status",
                  fallback_used: false,
                  status: "resolved",
                  resolved_value_preview: '"pending"',
                  value_type: "str",
                  error: null,
                },
              ],
            },
            error: "Simulated safe stop: the sample never touches runtime stores.",
            published_event_id: "sample:first-run:safe-stop-notification",
            completion_event_enqueued_at: "2026-05-16T12:00:08Z",
            config_revision: "sample:first-run:revision",
          },
        ],
        approval_links: [],
        agent_links: [],
        communication_artifact_links: [],
        tool_call_links: [],
        retry_resume_eligible: true,
      },
    ],
    data_availability: {
      graph_snapshot: "available",
      binding_trace: "available",
      approvals: "available",
      agent_links: "simulated_in_memory",
      communication_artifacts: "simulated_in_memory",
      tool_calls: "available",
      trigger_event: "available",
    },
  };
}

function createSampleState(active) {
  const launchedAt = active ? "2026-05-16T12:00:00Z" : null;
  const tutorialSteps = createSampleTutorialSteps(active);
  return {
    active,
    launched_at: launchedAt,
    run: active ? createSampleRunSummary() : null,
    detail: active ? createSampleDetail() : null,
    tutorial_steps: tutorialSteps,
    current_step: active ? tutorialSteps[1] : tutorialSteps[0],
  };
}

async function dispatchWorkflowNodeMouseEvent(page, nodeId, eventType) {
  await page.locator(`.react-flow__node[data-id="${nodeId}"]`).evaluate((element, type) => {
    const canvas = element.closest(".canvas-shell") ?? document.body;
    const rect = canvas.getBoundingClientRect();
    const clientX = Math.max(rect.left + 24, Math.min(rect.right - 24, rect.left + rect.width / 2));
    const clientY = Math.max(rect.top + 24, Math.min(rect.bottom - 24, rect.top + rect.height / 2));
    const button = type === "contextmenu" ? 2 : 0;
    element.dispatchEvent(new MouseEvent(type, {
      bubbles: true,
      cancelable: true,
      button,
      buttons: button === 2 ? 2 : 1,
      clientX,
      clientY,
      view: window,
    }));
  }, eventType);
}

test("workflow editor guides, inspects, and resets the guided sample workflow", async (t) => {
  const projectRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const server = await createServer({
    root: projectRoot,
    server: {
      host: "127.0.0.1",
      port: 0,
    },
    logLevel: "error",
  });
  await server.listen();

  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object" && "port" in address, "expected Vite to bind an ephemeral port");

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();

  const requestedEndpoints = [];
  const pageErrors = [];
  const consoleErrors = [];
  let sampleActive = false;
  let sampleLaunchRequests = 0;

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  const sessionResponse = {
    auth_mode: "none",
    auth_required: false,
    authenticated: true,
    username: "admin",
    display_name: "Admin Operator",
    role: "admin",
    can_manage_users: true,
    session_id: "session-123",
    available_login_methods: [],
    azure_sso_login_url: null,
    privileged_operations: {
      unlocked: true,
    },
  };
  const adminStateResponse = {
    selected_directory_path: "root",
    directory_scoped_config_enabled: true,
    execution_backend: "libvirt_vm",
    role: "admin",
    can_manage_users: true,
    privileged_operations: {
      unlocked: true,
    },
    assistant: {
      enabled: false,
      show_launcher_when_disabled: false,
    },
  };
  const configurationStateResponse = {
    directory_scoped_config_enabled: true,
    selected_directory_path: "root",
    execution_backend: "libvirt_vm",
    selected_directory: {
      path: "root",
      parent_path: null,
      can_create: true,
      can_delete: false,
    },
    tree: {
      ok: true,
      data: createDirectoryTree(),
      errors: [],
      validation_diagnostics: [],
    },
  };
  const workflowDocument = {
    config_path: "workflow.json",
    layout_path: "workflow-layout.json",
    config_exists: true,
    layout_exists: true,
    revision: "rev-1",
    config: {
      triggers: [
        {
          id: "trigger-1",
          display_name: "Incoming command",
          type: "trigger:manual",
        },
      ],
      gates: [],
      effects: [
        {
          id: "effect-1",
          display_name: "Dispatch agent",
          type: "effect:agent",
          prompt: "Handle ticket",
        },
      ],
      queries: [],
      edges: [
        {
          source_id: "trigger-1",
          target_id: "effect-1",
          source_port: "completed",
        },
      ],
    },
    layout: {
      nodes: {
        "trigger-1": { x: 80, y: 120 },
        "effect-1": { x: 360, y: 120 },
      },
    },
    graphs: [
      {
        id: "graph-1",
        name: "Workflow smoke",
        description: null,
        directory_paths: ["root"],
        user_ids: [],
        node_ids: ["trigger-1", "effect-1"],
      },
    ],
    output_schemas: {},
    validation_diagnostics: [],
  };
  const workflowCatalog = {
    worker_id: "trigger-orchestrator",
    nodes: [
      {
        kind: "trigger",
        type: "trigger:manual",
        label: "Manual",
        fields: [],
        input_ports: [],
        output_ports: [{ key: "completed", label: "Completed" }],
      },
      {
        kind: "effect",
        type: "effect:agent",
        label: "Agent",
        fields: [
          {
            key: "prompt",
            label: "Prompt",
            kind: "string",
            required: false,
            bindable: false,
            read_only: false,
            multiline: false,
            description: null,
            placeholder: null,
            options: [],
            item_fields: [],
          },
        ],
        input_ports: [],
        output_ports: [],
      },
    ],
  };
  const workflowRuns = {
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:00:00Z",
    runs: [],
    node_activity: {},
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    requestedEndpoints.push(`${pathname}${url.search}`);

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse(sessionResponse));
      return;
    }

    if (pathname === "/api/admin/deployment-status") {
      await route.fulfill(
        jsonResponse({
          active: false,
          message: null,
          source: null,
          started_at: null,
          updated_at: null,
          ttl_seconds: 0,
          stale: false,
        }),
      );
      return;
    }

    if (pathname === "/api/admin/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(adminStateResponse));
      return;
    }

    if (pathname === "/api/configuration/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/workflow-editor/document") {
      await route.fulfill(jsonResponse(workflowDocument));
      return;
    }

    if (pathname === "/api/workflow-editor/catalog") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(workflowCatalog));
      return;
    }

    if (pathname === "/api/workflow-editor/runs") {
      await route.fulfill(jsonResponse(workflowRuns));
      return;
    }

    if (pathname === "/api/workflow-editor/sample-workflow") {
      if (route.request().method() === "DELETE") {
        sampleActive = false;
        await route.fulfill(jsonResponse(createSampleState(false)));
        return;
      }
      await route.fulfill(jsonResponse(createSampleState(sampleActive)));
      return;
    }

    if (pathname === "/api/workflow-editor/sample-workflow/launch") {
      sampleLaunchRequests += 1;
      sampleActive = true;
      await route.fulfill(jsonResponse(createSampleState(true)));
      return;
    }

    if (pathname === "/api/workflow-editor/run-detail") {
      assert.equal(url.searchParams.get("event_id"), "sample:first-run:run");
      await route.fulfill(jsonResponse(createSampleDetail()));
      return;
    }

    if (pathname === "/api/workflow-editor/runs/stream") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: {
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        },
        body: ": workflow run stream\n\n",
      });
      return;
    }

    if (pathname === "/favicon.ico") {
      await route.fulfill({
        status: 204,
        body: "",
      });
      return;
    }

    await route.fulfill(jsonResponse({ error: `Unhandled ${pathname}` }, 404));
  });

  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });

  await page.waitForSelector(".guided-sample-launcher");
  await assert.equal(await page.locator(".guided-sample-section").count(), 0);
  await assert.equal(await page.getByRole("button", { name: "First steps" }).count(), 1);
  await assert.equal(await page.getByRole("button", { name: "Sample run" }).count(), 1);
  await assert.equal(await page.getByRole("button", { name: "Launch sample" }).count(), 0);

  await page.getByRole("button", { name: "First steps" }).click();
  await page.getByRole("dialog", { name: "Welcome" }).waitFor({ state: "visible" });
  await page.getByText("This walkthrough covers the basics of configuring workflows.", { exact: true }).waitFor({
    state: "visible",
  });
  await page.getByRole("button", { name: "Exit" }).click();
  await page.waitForFunction(() => !document.querySelector(".workflow-tutorial-bubble"));

  await page.getByRole("button", { name: "First steps" }).click();
  await page.getByRole("dialog", { name: "Welcome" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("dialog", { name: "Select the trigger" }).waitFor({ state: "visible" });
  await dispatchWorkflowNodeMouseEvent(page, "tutorial-trigger", "click");
  await page.getByRole("dialog", { name: "Trigger entrypoints" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("dialog", { name: "Add a downstream node" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Prompt agent" }).click();
  await page.getByRole("dialog", { name: "Connect the nodes" }).waitFor({ state: "visible" });
  await dispatchWorkflowNodeMouseEvent(page, "tutorial-trigger", "contextmenu");
  await page.getByRole("menuitem", { name: "Completed" }).click();
  await dispatchWorkflowNodeMouseEvent(page, "tutorial-effect", "click");
  await page.getByRole("dialog", { name: "Bind the prompt" }).waitFor({ state: "visible" });
  const tutorialInspector = page.locator(".sidebar-section.inspector").filter({ hasText: "tutorial-effect" });
  await tutorialInspector.getByText("Prompt", { exact: true }).waitFor({ state: "visible" });
  assert.equal(await tutorialInspector.locator("textarea").first().isEnabled(), true);
  await page.getByRole("button", { name: "Exit" }).click();
  await page.waitForFunction(() => !document.querySelector(".workflow-tutorial-bubble"));
  await page.locator('.react-flow__node[data-id="trigger-1"]').waitFor({ state: "visible" });
  await page.locator('.react-flow__node[data-id="effect-1"]').waitFor({ state: "visible" });
  assert.equal(await page.locator('.react-flow__node[data-id="tutorial-trigger"]').count(), 0);
  assert.equal(await page.locator('.react-flow__node[data-id="tutorial-effect"]').count(), 0);

  await page.getByRole("button", { name: "Sample run" }).click();
  await page.waitForSelector(".guided-sample-section");
  await assert.equal(await page.getByRole("button", { name: "Launch sample" }).count(), 1);
  await page.locator(".guided-sample-current-step").getByText("Launch the sample", { exact: true }).waitFor({ state: "visible" });
  await page.locator(".guided-sample-step").filter({ hasText: "Launch the sample" }).first().waitFor({ state: "visible" });
  await page.locator(".guided-sample-step").filter({ hasText: "Inspect the run" }).first().waitFor({ state: "visible" });
  await page.locator(".guided-sample-step").filter({ hasText: "Review the approval boundary" }).first().waitFor({ state: "visible" });
  await page.locator(".guided-sample-step").filter({ hasText: "Review the safe stop" }).first().waitFor({ state: "visible" });
  await page.locator(".guided-sample-step").filter({ hasText: "Reset the sample" }).first().waitFor({ state: "visible" });

  await page.getByRole("button", { name: "Launch sample" }).click();
  await page.waitForFunction(
    () => [...document.querySelectorAll(".guided-sample-step.is-current")].some((element) => element.textContent?.includes("Inspect the run")),
  );
  await page.locator(".guided-sample-current-step").getByText("Inspect the run", { exact: true }).waitFor({ state: "visible" });
  await page.getByText("sample:first-run:run", { exact: true }).first().waitFor({ state: "visible" });
  await page.getByText("Simulated safe stop: the sample never touches runtime stores.", { exact: false }).first().waitFor({ state: "visible" });

  await page.getByRole("button", { name: "Inspect sample" }).click();
  await page.getByText("Inspected run", { exact: true }).waitFor({ state: "visible" });
  await page.getByText("sample:first-run:run", { exact: true }).first().waitFor({ state: "visible" });
  await page.getByText("Sample trigger", { exact: true }).first().waitFor({ state: "visible" });
  await page.getByText("Prompt simulated agent", { exact: true }).first().waitFor({ state: "visible" });
  await page.getByText("Await approval", { exact: true }).first().waitFor({ state: "visible" });
  await page.getByText("Safe stop notification", { exact: true }).first().waitFor({ state: "visible" });
  await page.getByText("Outputs", { exact: true }).first().click();
  await page.getByText("safe_stop", { exact: false }).first().waitFor({ state: "visible" });
  await page.getByText("Tool call prompt_agent", { exact: false }).first().waitFor({ state: "visible" });
  await page.locator('.react-flow__node[data-id="sample-agent-prompt"]').click();
  await page.getByText("Prompt simulated agent", { exact: true }).first().waitFor({ state: "visible" });
  await page.getByText("No real session was created for the in-memory sample.", { exact: true }).first().waitFor({ state: "visible" });
  await page.getByText("simulated_in_memory", { exact: false }).first().waitFor({ state: "visible" });
  await page.locator('.react-flow__node[data-id="sample-approval-boundary"]').click();
  await page.getByText("Approval sample:first-run:approval", { exact: false }).first().waitFor({ state: "visible" });

  await page.getByRole("button", { name: "Reset sample" }).click();
  await page.waitForFunction(() => !document.querySelector(".sidebar-section.inspected-run-panel"));
  await assert.equal(await page.getByText("Inspected run", { exact: true }).count(), 0);
  await assert.equal(await page.getByText("sample:first-run:run", { exact: true }).count(), 0);
  await assert.equal(await page.getByRole("button", { name: "Launch sample" }).count(), 1);
  await page.getByRole("button", { name: "Hide" }).click();
  await page.waitForFunction(() => !document.querySelector(".guided-sample-section"));
  await assert.equal(await page.getByRole("button", { name: "Launch sample" }).count(), 0);

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/auth/session"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/deployment-status"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/configuration/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/document"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/catalog?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/sample-workflow"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/sample-workflow/launch"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/run-detail?event_id=sample%3Afirst-run%3Arun"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs/stream"));
});
