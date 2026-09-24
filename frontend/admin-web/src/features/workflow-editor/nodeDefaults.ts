import { type WorkflowNodeConfig, type WorkflowNodeKind } from "../../types";
import { SEMANTIC_SAMPLE_POINT_ID } from "./editorConstants";

export function workflowNode(
  id: string,
  displayName: string,
  type: string,
  config: Record<string, unknown> = {},
  outputs?: Record<string, unknown>,
): WorkflowNodeConfig {
  return {
    id,
    display_name: displayName,
    type,
    config,
    ...(outputs === undefined ? {} : { outputs }),
  };
}

export function createDefaultNode(kind: WorkflowNodeKind, type: string, id: string): WorkflowNodeConfig {
  if (kind === "trigger" && type === "trigger:cron") {
    return workflowNode(id, "New trigger", type, {
      expression: "*/15 * * * *",
      timezone: "UTC",
    });
  }
  if (kind === "trigger" && type === "trigger:event") {
    return workflowNode(id, "Internal event", type, {
      event_type: "custom.operator_event",
    });
  }
  if (kind === "trigger" && type === "trigger:http:webhook") {
    return workflowNode(id, "Webhook", type, {
      slug: `webhook-${id}`,
      secret_env: "WEBHOOK_SECRET",
      event_type: `webhook.webhook-${id}`,
    });
  }
  if (kind === "trigger" && type === "trigger:ticket:created") {
    return workflowNode(id, "Ticket created", type, {
      directories: ["root"],
    });
  }
  if (kind === "trigger" && type === "trigger:ticket:changed") {
    return workflowNode(id, "Ticket changed", type, {
      directories: ["root"],
    });
  }
  if (kind === "trigger" && type === "trigger:issue:comment-created") {
    return workflowNode(id, "Issue comment", type, {
      provider: "ado",
      directories: ["root"],
    });
  }
  if (kind === "trigger" && type === "trigger:discord:new-message") {
    return workflowNode(id, "Discord message", type, {
      discord_bot_token_env: "DISCORD_BOT_TOKEN",
      channel_ids: [],
      user_ids: [],
    });
  }
  if (kind === "trigger" && type === "trigger:teams:new-message") {
    return workflowNode(id, "Teams message", type, {
      targets: [],
      user_ids: [],
    });
  }
  if (kind === "trigger" && type === "trigger:communication:new-message") {
    return workflowNode(id, "Communication message", type, {
      communication_target_ids: [],
      directory_path: "root",
      author_ids: [],
      case_sensitive: false,
    });
  }
  if (kind === "trigger" && type === "trigger:approval:pending-created") {
    return workflowNode(id, "Pending approval", type, {
      directory_path: "root",
      action_types: [],
      tool_names: [],
    });
  }
  if (kind === "trigger" && type === "trigger:teams:direct-message") {
    return workflowNode(id, "Teams direct message", type, {
      chat_target_mode: "direct",
      direct_chat_user_upns: [],
      targets: [],
      sender_user_ids: [],
      message_filter: null,
    });
  }
  if (kind === "trigger" && type === "trigger:email:new-message") {
    return workflowNode(id, "Email message", type, {
      provider: "json_store",
      account_identifier: "default",
      mailbox_ids: [],
      auth_secret_refs: {}
    });
  }
  if (kind === "trigger" && type === "trigger:pull-request:new-feedback") {
    return workflowNode(id, "PR feedback", type, {
      provider: "local",
    });
  }
  if (kind === "trigger" && type === "trigger:ci:run:completed") {
    return workflowNode(id, "CI run completed", type, {
      provider: "github_actions",
      repo: "owner/repo",
      branches: [],
      conclusions: [],
      top: 50,
      include_existing: false,
    });
  }
  if (kind === "trigger" && type === "trigger:git:push") {
    return workflowNode(id, "Git push", type, {
      managed_repo_id_glob: "mr_*",
    });
  }
  if (kind === "trigger" && type === "trigger:agent:run:completed") {
    return workflowNode(id, "Agent run completed", type, {
      vm_user: "agent",
    });
  }
  if (kind === "gate" && type === "gate:filter") {
    return workflowNode(id, "New filter", type, {}, {});
  }
  if (kind === "gate" && type === "gate:map") {
    return workflowNode(id, "New map", type, {
      template: {}
    }, {});
  }
  if (kind === "gate" && type === "gate:aggregate") {
    return workflowNode(id, "Aggregate", type, {
      items: [],
      metrics: { total: { operation: "sum", path: "value" } }
    }, { count: { type: "integer" }, total: { type: "number" } });
  }
  if (kind === "gate" && type === "gate:deserialize") {
    return workflowNode(id, "Deserialize", type, {
      content: "${{node.payload_text}}",
      format: "auto",
    }, {});
  }
  if (kind === "gate" && type === "gate:serialize") {
    return workflowNode(id, "Serialize", type, {
      value: {},
      format: "json",
    }, { content: { type: "string" }, format: { type: "string" } });
  }
  if (kind === "gate" && type === "gate:merge") {
    return workflowNode(id, "New merge", type, {}, {});
  }
  if (kind === "gate" && type === "gate:classifier:switch") {
    return workflowNode(id, "Smart switch", type, {
      state: "", instructions: "Select the branch that best describes the input.",
      cases: [{port: "matched", label: "Matched", description: "The input matches the desired intent."}],
      default_port: "default", default_description: "None of the described routes clearly applies.",
    }, {});
  }
  if (kind === "gate" && type === "gate:switch") {
    return workflowNode(id, "Switch", type, {
      cases: [
        {
          port: "matched",
          when: { left: "${{trigger-1.status}}", equals: "ready" },
          label: "Matched",
        },
      ],
      default_port: "default",
    }, {});
  }
  if (kind === "gate" && type === "gate:debounce") {
    return workflowNode(id, "Debounce", type, {
      key: "debounce:${{system.execution.event_id}}",
      quiet_period: "PT2M",
      max_delay: "PT10M",
    }, {});
  }
  if (kind === "gate" && type === "gate:buffer") {
    return workflowNode(id, "Buffer", type, {
      key: "buffer:${{system.execution.trace_id}}",
      count: 10,
      timeout: "PT10M",
    }, {
      items: { type: "array" },
      count: { type: "integer" },
      release_reason: { type: "string" },
    });
  }
  if (kind === "gate" && type === "gate:dedupe") {
    return workflowNode(id, "Dedupe", type, {
      key: "dedupe:${{system.execution.event_id}}",
      ttl: "P7D",
    }, {});
  }
  if (kind === "gate" && type === "gate:await-agent") {
    return workflowNode(id, "Await agent", type, {
      session_id: "session-id",
    }, {});
  }
  if (kind === "gate" && type === "gate:for") {
    return workflowNode(id, "For each item", type, {
      items: [],
    });
  }
  if (kind === "gate" && type === "gate:while") {
    return workflowNode(id, "While", type, {
      when: { left: 0, greater_than: 0 },
      max_iterations: 3,
    });
  }
  if (kind === "effect" && type === "effect:agent:prompt") {
    return workflowNode(id, "New prompt", type, {
      prompt: "Describe the next action.",
    });
  }
  if (kind === "effect" && type === "effect:agent:exec") {
    return workflowNode(id, "Agent exec", type, {
      command: "echo hello",
    });
  }
  if (kind === "effect" && type === "effect:agent:destroy") {
    return workflowNode(id, "Agent destroy", type, {
      session_id: "session-id",
    });
  }
  if (kind === "effect" && type === "effect:agent:clone-repo") {
    return workflowNode(id, "Clone repo into agent", type, {
      session_id: "session-id",
      managed_repo_id: "mr_00000000000000000000",
      clone_branch: "main",
    });
  }
  if (kind === "effect" && type === "effect:agent:grant-repo-access") {
    return workflowNode(id, "Grant repo access", type, {
      session_id: "session-id",
      managed_repo_id: "mr_00000000000000000000",
      git_access_mode: "read-only",
    });
  }
  if (kind === "effect" && type === "effect:agent:revoke-repo-access") {
    return workflowNode(id, "Revoke repo access", type, {
      session_id: "session-id",
      managed_repo_id: "mr_00000000000000000000",
    });
  }
  if (kind === "effect" && type === "effect:semantic:ensure-collection") {
    return workflowNode(id, "Ensure semantic collection", type, {
      collection_name: "agent_memory",
    });
  }
  if (kind === "effect" && type === "effect:semantic:upsert") {
    return workflowNode(id, "Semantic upsert", type, {
      collection_name: "agent_memory",
      point_id: SEMANTIC_SAMPLE_POINT_ID,
      text: "Important context to remember.",
      payload: {}
    });
  }
  if (kind === "effect" && type === "effect:semantic:delete") {
    return workflowNode(id, "Semantic delete", type, {
      collection_name: "agent_memory",
      point_ids: [SEMANTIC_SAMPLE_POINT_ID],
    });
  }
  if (kind === "effect" && type === "effect:state:update") {
    return workflowNode(id, "Update workflow state", type, {
      namespace: "workflow",
      key: "cursor",
      operation: "set",
      value: {}
    });
  }
  if (kind === "effect" && type === "effect:http:request") {
    return workflowNode(id, "HTTP request", type, {
      method: "GET",
      url: "https://api.example.test/items",
      allowed_url_patterns: ["*"],
      check: false,
    });
  }
  if (kind === "effect" && type === "effect:runtime:diagnostics-snapshot") {
    return workflowNode(id, "Runtime diagnostics snapshot", type, {
      since: "24 hours ago",
      include_failed_units: true,
      include_journal: true,
      include_runtime_files: true,
      max_journal_lines_per_unit: 200,
      max_runtime_file_bytes: 120000,
      command_timeout_seconds: 10,
    });
  }
  if (kind === "effect" && type === "effect:vm:create") {
    return workflowNode(id, "New VM effect", type, {});
  }
  if (kind === "effect" && type === "effect:vm:clone-repo") {
    return workflowNode(id, "Clone repo into VM", type, {
      target: "session-id",
      managed_repo_id: "mr_00000000000000000000",
      clone_branch: "main",
    });
  }
  if (kind === "effect" && type === "effect:vm:bootstrap-python") {
    return workflowNode(id, "Bootstrap Python in VM", type, {
      host_checkout: "/path/to/repo",
      target_dir: "/home/agent/repo",
    });
  }
  if (kind === "effect" && type === "effect:vm:bootstrap-frontend") {
    return workflowNode(id, "Bootstrap frontend deps in VM", type, {
      host_checkout: "/path/to/repo",
      install_roots: ["/path/to/repo/Src/Clients"],
    });
  }
  if (kind === "effect" && type === "effect:vm:start") {
    return workflowNode(id, "VM start", type, {});
  }
  if (kind === "effect" && type === "effect:vm:stop") {
    return workflowNode(id, "VM stop", type, {
      timeout_seconds: 30,
    });
  }
  if (kind === "effect" && type === "effect:vm:exec") {
    return workflowNode(id, "New exec effect", type, {
      command: "echo hello",
    });
  }
  if (kind === "effect" && type === "effect:vm:copy-files") {
    return workflowNode(id, "New copy effect", type, {
      files: [{ destination_path: "/tmp/example.txt", content: "example" }],
    });
  }
  if (kind === "effect" && type === "effect:vm:delete-files") {
    return workflowNode(id, "Delete files", type, {
      paths: ["/tmp/example.txt"],
      missing_ok: true,
    });
  }
  if (kind === "effect" && type === "effect:event:emit") {
    return workflowNode(id, "Emit internal event", type, {
      event_type: "custom.operator_event",
      payload: {}
    });
  }
  if (kind === "effect" && type === "effect:event:schedule") {
    return workflowNode(id, "Schedule internal event", type, {
      event_type: "custom.operator_event",
      payload: {},
      delay_seconds: 60,
    });
  }
  if (kind === "effect" && type === "effect:issue:update-metadata") {
    return workflowNode(id, "Update issue metadata", type, {
      issue_id: "issue-1",
      patch: { "fields.workflow_status": "updated" },
      sync_proxy_cache: true,
    });
  }
  if (kind === "effect" && type === "effect:ado:update-work-item") {
    return workflowNode(id, "ADO update work item", type, {
      directory_path: "root",
      project: "Project name",
      work_item_id: "12345",
      state: "In Progress",
      expected_states: ["New", "To Do"],
      sync_proxy_cache: true,
    });
  }
  if (kind === "effect" &&
    (type === "effect:ado:add-work-item-tag" || type === "effect:ado:remove-work-item-tag")) {
    const isAdd = type === "effect:ado:add-work-item-tag";
    return workflowNode(id, isAdd ? "ADO add work item tag" : "ADO remove work item tag", type, {
      directory_path: "root",
      project: "Project name",
      work_item_id: "12345",
      tag: "tag-name",
      sync_proxy_cache: true,
    });
  }
  if (kind === "effect" && type === "effect:ado:pipeline-run") {
    return workflowNode(id, "ADO pipeline run", type, {
      directory_path: "root",
      pipeline_id: 12345,
      variables: {},
      template_parameters: {},
      timeout_seconds: 3600,
      poll_interval_seconds: 10,
      check: true,
    });
  }
  if (kind === "effect" && type === "effect:session:update-metadata") {
    return workflowNode(id, "Update session metadata", type, {
      metadata: {}
    });
  }
  if (kind === "effect" && type === "effect:session:renew-if-expired") {
    return workflowNode(id, "Renew expired or soon-expiring session", type, {
      session_id: "session-id",
      extend_by: "1d",
      renew_if_expires_within: "2h",
    });
  }
  if (kind === "effect" && type === "effect:session:revoke-mcp-tool-approval-lease") {
    return workflowNode(id, "Revoke MCP tool approval lease", type, {
      mcp_tool_approval_lease_id: "mcp-tool-approval-lease-id",
    });
  }
  if (kind === "effect" && type === "effect:artifact:stage") {
    return workflowNode(id, "Stage artifact", type, {
      session_id: "session-id",
      path: "repos/project/output/report.html",
      destination_path: "reports/report.html",
    });
  }
  if (kind === "effect" && type === "effect:artifact:publish-artifacts") {
    return workflowNode(id, "Publish artifacts", type, {
      session_id: "session-id",
      key: "runs/artifact-key",
    });
  }
  if (kind === "effect" && type === "effect:artifact:import-published") {
    return workflowNode(id, "Import published artifact", type, {
      session_id: "session-id",
      key: "runs/artifact-key",
      destination_path: "input",
      overwrite: false,
    });
  }
  if (kind === "effect" && type === "effect:git:ensure-managed-repo") {
    return workflowNode(id, "Ensure managed repository", type, {
      directory_path: "root",
      repo_source: "/path/to/repository",
    });
  }
  if (kind === "effect" && type === "effect:git:promote-agent-branch") {
    return workflowNode(id, "Promote agent branch", type, {
      managed_repo_id: "mr_00000000000000000000",
      agent_branch: "agents/session-1/topic",
      target_branch: "review/session-1/topic",
      expected_commit_sha: "0000000000000000000000000000000000000000",
    });
  }
  if (kind === "effect" && type === "effect:git:push-branch") {
    return workflowNode(id, "Publish branch", type, {
      managed_repo_id: "mr_00000000000000000000",
      source_ref: "refs/heads/review/session-1/topic",
      expected_commit_sha: "0000000000000000000000000000000000000000",
      destination_branch: "feature/topic",
    });
  }
  if (kind === "effect" && type === "effect:pr:create") {
    return workflowNode(id, "New PR effect", type, {
      managed_repo_id: "mr_00000000000000000000",
      source_ref: "refs/heads/review/session-1/topic",
      expected_commit_sha: "0000000000000000000000000000000000000000",
      destination_branch: "feature/topic",
      base_branch: "main",
    });
  }
  if (kind === "effect" && type === "effect:pull-request:update") {
    return workflowNode(id, "Update pull request", type, {
      provider: "azure_devops",
      repo: "repo-name",
      pr_id: "123",
      expected_statuses: ["active"],
    });
  }
  if (kind === "effect" &&
    (type === "effect:pull-request:add-tag" || type === "effect:pull-request:remove-tag")) {
    const isAdd = type === "effect:pull-request:add-tag";
    return workflowNode(id, isAdd ? "Add pull request tag" : "Remove pull request tag", type, {
      provider: "azure_devops",
      repo: "repo-name",
      pr_id: "123",
      tag: "tag-name",
    });
  }
  if (kind === "query" && type === "query:vm:run-state") {
    return workflowNode(id, "VM run state", type, {});
  }
  if (kind === "query" && type === "query:git:pr-note") {
    return workflowNode(id, "Git PR note", type, {
      managed_repo_id: "mr_00000000000000000000",
      commit_sha: "0000000000000000000000000000000000000000",
      notes_ref: "refs/notes/agents/session-1/pr",
    });
  }
  if (kind === "query" && type === "query:session-context") {
    return workflowNode(id, "Session context", type, {});
  }
  if (kind === "query" && type === "query:state:get") {
    return workflowNode(id, "Read workflow state", type, {
      namespace: "workflow",
      key: "cursor",
    });
  }
  if (kind === "query" && type === "query:agent:artifact-text") {
    return workflowNode(id, "Agent artifact text", type, {
      path: "report/summary.txt",
    });
  }
  if (kind === "query" && type === "query:issue:get") {
    return workflowNode(id, "Issue get", type, {
      issue_id: "issue-1",
    });
  }
  if (kind === "query" && type === "query:ado:work-items") {
    return workflowNode(id, "ADO work items", type, {
      top: 50,
      skip: 0,
    });
  }
  if (kind === "query" && type === "query:pull-request:list") {
    return workflowNode(id, "Pull request list", type, {
      provider: "azure_devops",
      repo: "repo-name",
      status: "active",
      top: 50,
      skip: 0,
    });
  }
  if (kind === "query" && type === "query:communication:messages") {
    return workflowNode(id, "Communication messages", type, {
      communication_target_id: "communication-target-id",
      limit: 50,
    });
  }
  if (kind === "query" && type === "query:pull-request:get") {
    return workflowNode(id, "Pull request get", type, {
      provider: "azure_devops",
      repo: "repo-name",
      pr_id: "123",
      include_reviewers: false,
      include_commits: false,
    });
  }
  if (kind === "query" && type === "query:semantic:store-status") {
    return workflowNode(id, "Semantic store status", type, {
      collection_name: "agent_memory",
    });
  }
  if (kind === "query" && type === "query:semantic:search") {
    return workflowNode(id, "Semantic search", type, {
      collection_name: "agent_memory",
      query_text: "What context is relevant?",
      limit: 10,
    });
  }
  if (kind === "query" && type === "query:semantic:get") {
    return workflowNode(id, "Semantic get", type, {
      collection_name: "agent_memory",
      point_ids: [SEMANTIC_SAMPLE_POINT_ID],
    });
  }
  return workflowNode(id, "New node", type, {});
}
