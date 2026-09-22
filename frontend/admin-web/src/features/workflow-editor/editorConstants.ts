import { type WorkflowNodeKind, type WorkflowOutputSchemas } from "../../types";

export const EMPTY_OUTPUT_SCHEMAS: WorkflowOutputSchemas = {};

export const RUNTIME_NODE_PULSE_DURATION_MS = 1400;

export const RUNTIME_NODE_PULSE_SEEN_KEY_CAP = 1000;

export const EFFECT_TARGET_KEYS = ["target", "session_id", "vm_name"] as const;

export const TARGET_SELECTOR_NODE_TYPES = new Set([
  "effect:vm:clone-repo",
  "effect:vm:bootstrap-python",
  "effect:vm:bootstrap-frontend",
  "effect:vm:start",
  "effect:vm:stop",
  "effect:vm:exec",
  "effect:vm:copy-files",
  "effect:vm:delete-files",
  "effect:agent:destroy",
  "effect:agent:clone-repo",
  "effect:agent:bootstrap-frontend",
  "effect:agent:copy-files",
  "effect:agent:prompt",
  "effect:agent:enqueue-prompt",
  "effect:agent:exec",
  "effect:artifact:stage",
  "effect:artifact:publish-artifacts",
  "effect:artifact:import-published",
  "effect:session:update-metadata",
  "effect:session:approve-mcp-tools-for-run",
  "effect:session:renew-if-expired",
  "query:session-context",
]);

export const SESSION_LIST_METADATA_FIELD_KEYS = [
  "metadata_equals",
  "metadata_exists",
  "metadata_prefix",
] as const;

export const COMMUNICATION_EFFECT_NODE_TYPES = new Set([
  "effect:communication:send-message",
  "effect:notification:send",
]);

export const SEMANTIC_SAMPLE_POINT_ID = "00000000-0000-4000-8000-000000000001";

export const NODE_GROUPS: ReadonlyArray<{
  kind: WorkflowNodeKind;
  label: string;
  description: string;
}> = [
  { kind: "trigger", label: "Triggers", description: "Start workflow execution from incoming events." },
  { kind: "gate", label: "Gates", description: "Transform, filter, or combine workflow data." },
  { kind: "effect", label: "Effects", description: "Perform side effects in VMs, agents, or Git providers." },
  { kind: "query", label: "Queries", description: "Read workflow state without producing a side effect." },
];

export const TOOL_GROUP_ORDER = [
  "Issue intake",
  "Azure DevOps",
  "Pull requests",
  "Approvals",
  "Agent control",
  "Messaging",
  "Voice calling",
  "SQL Server",
  "Files",
  "Discord",
  "Teams",
  "Email",
  "Feedback",
  "Assistant",
  "Semantic store",
  "Other",
] as const;

export const NODE_KIND_DESCRIPTIONS: Record<WorkflowNodeKind, string> = {
  trigger: "Triggers start workflow execution from incoming events.",
  gate: "Gates transform, filter, fan out, or combine workflow data between nodes.",
  effect: "Effects perform side effects such as VM actions, session updates, Git operations, or agent prompts.",
  query: "Queries read workflow or environment state without causing a side effect.",
};

export const NODE_TYPE_DESCRIPTIONS: Record<string, string> = {
  "trigger:cron": "Start a workflow on a recurring cron schedule.",
  "trigger:event": "Start when an internal workflow event is emitted.",
  "trigger:http:webhook": "Start from a signed HTTP webhook delivered to a public endpoint.",
  "trigger:ticket:created": "Start when a tracked ticket is created.",
  "trigger:ticket:changed": "Start when a tracked ticket changes state or metadata.",
  "trigger:issue:comment-created": "Start when a tracked issue receives a new comment.",
  "trigger:discord:new-message": "Start from new Discord messages that match the configured filters.",
  "trigger:teams:new-message": "Start from new Microsoft Teams messages that match the configured targets.",
  "trigger:communication:new-message": "Start from new messages on configured communication targets.",
  "trigger:approval:pending-created": "Start when a pending approval request is created.",
  "trigger:teams:direct-message": "Start from allowlisted one-on-one Microsoft Teams direct messages.",
  "trigger:email:new-message": "Start from newly ingested email messages for the configured mailbox scope.",
  "trigger:pull-request:new-feedback": "Start when new pull-request feedback arrives from the configured provider.",
  "trigger:pull-request:completed": "Start when a tracked pull request reaches a terminal state.",
  "trigger:ci:run:completed": "Start when a CI run completes on GitHub Actions or Azure DevOps.",
  "trigger:git:push": "Start from Git push events captured for a managed repository.",
  "trigger:agent:run:completed": "Start when a guest agent run completes.",
  "gate:filter": "Allow only matching events to continue downstream.",
  "gate:map": "Project upstream outputs into a new payload shape.",
  "gate:deserialize": "Parse a JSON or YAML string into object properties for downstream bindings.",
  "gate:serialize": "Serialize a bindable value as JSON or YAML text for downstream nodes.",
  "gate:merge": "Merge compatible inbound event streams into one downstream path.",
  "gate:switch": "Route activations to one of several downstream branches.",
  "gate:classifier:switch": "Use classification to route input to a described branch.",
  "gate:debounce": "Wait for a quiet period, then forward the latest activation.",
  "gate:buffer": "Collect activations by key and release them as a batch by count or timeout.",
  "gate:dedupe": "Suppress duplicate activations for the same key until a TTL expires.",
  "gate:await-agent": "Pause the workflow until a selected agent session completes.",
  "gate:aggregate": "Aggregate numeric fields from an array into flat summary outputs.",
  "gate:for": "Iterate over an array and execute the body branch for each item.",
  "gate:while": "Repeat a body branch while a bounded predicate remains true.",
  "effect:agent:prompt": "Send a prompt to an agent session.",
  "effect:agent:exec": "Run a shell command in an agent session.",
  "effect:agent:destroy": "Destroy an agent session and its backend target.",
  "effect:agent:clone-repo": "Clone a managed repository into an agent session.",
  "effect:agent:grant-repo-access": "Grant persistent exact-repository Git access to an agent session.",
  "effect:agent:revoke-repo-access": "Revoke one exact-repository Git grant from an agent session.",
  "effect:session:approve-mcp-tools-for-run": "Approve MCP tools for one agent session during this run.",
  "effect:session:revoke-mcp-tool-approval-lease": "Revoke an MCP tool approval lease for one agent session.",
  "effect:semantic:ensure-collection": "Ensure a semantic collection exists with the configured vector schema.",
  "effect:semantic:upsert": "Embed one text string and upsert it as one semantic point.",
  "effect:semantic:delete": "Delete semantic points by explicit point IDs.",
  "effect:state:update": "Set, patch, or delete graph-local workflow state.",
  "effect:http:request": "Perform a host-side raw HTTP GET, POST, or PUT request with URL allowlist control.",
  "effect:runtime:diagnostics-snapshot": "Capture a bounded, redacted host runtime diagnostics snapshot.",
  "effect:vm:create": "Create a VM for the workflow.",
  "effect:vm:clone-repo": "Clone a managed repository into a target VM.",
  "effect:vm:bootstrap-python": "Bootstrap a Python checkout and environment in a VM.",
  "effect:vm:bootstrap-frontend": "Bootstrap frontend dependencies in a VM.",
  "effect:vm:start": "Start an existing VM.",
  "effect:vm:stop": "Stop an existing VM.",
  "effect:vm:exec": "Run a shell command inside a VM.",
  "effect:vm:copy-files": "Copy rendered file content into a VM.",
  "effect:vm:delete-files": "Delete files from a VM.",
  "effect:event:emit": "Emit an internal workflow event for other graphs.",
  "effect:event:schedule": "Schedule a one-shot internal workflow event for later delivery.",
  "effect:issue:update-metadata": "Patch metadata on a tracked issue.",
  "effect:ado:pipeline-run": "Queue an Azure DevOps pipeline run and wait for its terminal result.",
  "effect:ado:update-work-item": "Update an Azure DevOps work item with an optional state guard.",
  "effect:ado:add-work-item-tag": "Add a tag to an Azure DevOps work item when it is not already present.",
  "effect:ado:remove-work-item-tag": "Remove a tag from an Azure DevOps work item when it is present.",
  "effect:session:update-metadata": "Patch session metadata on the host.",
  "effect:session:renew-if-expired": "Renew an expired or soon-expiring session.",
  "effect:artifact:stage": "Copy a file or directory from /home/agent into /artifacts.",
  "effect:artifact:publish-artifacts": "Publish the session /artifacts folder, or selected source paths, as an immutable tar.gz archive.",
  "effect:artifact:import-published": "Import a published artifact archive into a session artifacts folder.",
  "effect:git:ensure-managed-repo": "Register or refresh a directory-scoped managed repository.",
  "effect:git:promote-agent-branch": "Promote a pinned agent commit into a host-owned managed-repository ref.",
  "effect:git:push-branch": "Publish a pinned managed-repository commit with an explicit lease.",
  "effect:pr:create": "Publish a pinned commit and create or reuse its pull request.",
  "effect:pull-request:update": "Update pull request metadata or tags.",
  "effect:pull-request:add-tag": "Add a tag to a pull request when it is not already present.",
  "effect:pull-request:remove-tag": "Remove a tag from a pull request when it is present.",
  "query:vm:run-state": "Read the current run state for a VM.",
  "query:git:pr-note": "Read the note attached to an exact commit in a managed repository.",
  "query:pull-request:list": "List provider pull requests.",
  "query:communication:messages": "Read messages from a read-capable Teams or Discord target.",
  "query:pull-request:get": "Get a provider pull request by id.",
  "query:session-context": "Read session context and metadata.",
  "query:state:get": "Read graph-local workflow state for the current persisted workflow graph.",
  "query:agent:artifact-text": "Read a UTF-8 text file from an agent session artifacts folder.",
  "query:issue:get": "Read a local issue record by id.",
  "query:ado:work-items": "Query Azure DevOps work items.",
  "query:semantic:store-status": "Read semantic store health and collection status.",
  "query:semantic:search": "Search a semantic collection with an embedded text query.",
  "query:semantic:get": "Retrieve semantic points by explicit point IDs.",
};

export const RUNTIME_POLL_INTERVAL_MS = 5000;

export const NODE_STATUS_ORDER = [
  "failed",
  "running",
  "waiting",
  "completion_pending",
  "inflight",
  "pending",
  "completed",
  "succeeded",
  "skipped",
] as const;

export const WORKFLOW_SIDEBAR_WIDTH_STORAGE_KEY = "workflow-editor:sidebar-width";

export const WORKFLOW_TUTORIAL_PROGRESS_API_PATH = "/api/admin/user-preferences/workflow-tutorial-progress";

export const WORKFLOW_SIDEBAR_DEFAULT_WIDTH = 320;

export const WORKFLOW_SIDEBAR_MIN_WIDTH = 320;

export const WORKFLOW_SIDEBAR_MAX_WIDTH = 760;

export const WORKFLOW_SIDEBAR_CANVAS_MIN_WIDTH = 420;

export const WORKFLOW_SIDEBAR_RESIZE_STEP = 32;

export const WORKFLOW_COMPACT_BREAKPOINT = 980;

export const WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH = 24;

export const WORKFLOW_UNDO_HISTORY_LIMIT = 50;

export const NODE_TYPE_DRAG_DATA_MIME = "application/x-evander-workflow-node-type";

export const TOOL_ARGUMENT_POLICY_STRUCTURED_NODE_TYPES = new Set([
  "effect:agent:create",
  "effect:vm:create",
]);

export const ADO_QUERY_MODE_STRUCTURED_FILTERS = "structured_filters";

export const ADO_QUERY_MODE_MANUAL_WIQL = "manual_wiql";

export const ADO_QUERY_MODE_NODE_TYPES = new Set([
  "query:ado:work-items",
  "query:remote-issues",
  "trigger:ado:work-item-matching",
  "trigger:ado:work-item-comment-created",
  "trigger:remote-issue-matching",
  "trigger:remote-issue-comment-created",
]);

export const ADO_STRUCTURED_FILTER_FIELD_KEYS = new Set([
  "current_iteration",
  "team",
  "iteration",
  "area_path",
  "states",
  "types",
  "tags_any",
  "text_search",
]);

export const AGENT_CREATE_NODE_TYPE = "effect:agent:create";

export const CLIENT_RECOMPUTED_DIAGNOSTIC_CODES = new Set([
  "agent_create_missing_execution_backend",
]);
