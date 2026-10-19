import type { FastMode } from "./fastMode";
import type { CyberAccessProgram } from "./CyberAccessProgramSelect";
import type { ReasoningEffortCatalog } from "./reasoningEffort";
export type AdminView =
  | "workflow"
  | "agents"
  | "addons"
  | "connectors"
  | "skills"
  | "vms"
  | "issues"
  | "sessions"
  | "tool-calls"
  | "configuration"
  | "users"
  | "workers";

export type AdminAddonLoaderConfig = {
  modules: string[];
  enabled_entry_points: boolean;
  enabled_addon_ids: string[] | null;
};

export type AdminAddonRuntimeWorkflowPack = {
  source: string;
  reference: string | null;
  path: string | null;
  exists: boolean;
  configured: boolean;
  refresh_available: boolean;
  error: string | null;
};

export type AdminAddonStatus = {
  candidate_key: string;
  addon_id: string | null;
  display_name: string;
  source: string;
  source_type: "module" | "entry_point" | string;
  entry_point_name: string | null;
  enabled: boolean;
  loaded: boolean;
  pending_enable: boolean;
  pending_disable: boolean;
  restart_required: boolean;
  configurable: boolean;
  has_admin_ui: boolean;
  has_admin_api: boolean;
  has_mcp_tools: boolean;
  has_connector_drivers: boolean;
  has_workflow_providers: boolean;
  workflow_node_types: string[];
  referenced_workflow_node_types: string[];
  runtime_workflow_pack?: AdminAddonRuntimeWorkflowPack | null;
  load_error: string | null;
  required_restart_services: string[];
};

export type AdminAddonState = {
  addon_loader: AdminAddonLoaderConfig;
  worker_addon_loader: AdminAddonLoaderConfig;
  explicit_enabled_addon_ids: string[];
  loaded_addon_ids: string[];
  addons: AdminAddonStatus[];
  revision: string;
  restart_required: boolean;
  required_restart_services: string[];
  catalog_error: string | null;
  workflow_reference_error: string | null;
  config_mismatch: boolean;
  config_paths: {
    host: string;
    trigger_orchestrator: string;
  };
};

export type AdminWebGroupRecord = {
  group_id: string;
  display_name: string;
  description: string | null;
  is_required: boolean;
  permissions: string[];
  created_at: string;
  updated_at: string;
};

export type AdminWebUserGroupAssignmentRecord = {
  login: string;
  group_id: string;
  directory_path: string;
  created_at?: string;
};

export type AdminWebWorkflowGrantRecord = {
  login: string;
  permission: "workflows.read" | "workflows.write";
  directory_path: string;
  created_at?: string;
};

export type AdminWebUserRecord = {
  login: string;
  display_name: string | null;
  contact_id: string | null;
  contact: AdminContactSummary | null;
  local_password_enabled: boolean;
  password_last_changed_at: string | null;
  group_assignments: AdminWebUserGroupAssignmentRecord[];
  workflow_grants: AdminWebWorkflowGrantRecord[];
  effective_permissions: string[];
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
};

export type AdminContactSummary = {
  id: string;
  name: string;
  description: string;
  email_address_count: number;
  phone_number_count: number;
  primary_phone_label: string | null;
};

export type AdminUserListResponse = {
  directory_path: string;
  directory_scoped_config_enabled: boolean;
  contacts: AdminContactSummary[];
  groups: AdminWebGroupRecord[];
  permission_catalog: string[];
  users: AdminWebUserRecord[];
};

export type AdminUserMutationResponse = {
  user: AdminWebUserRecord;
};

export type AdminGroupMutationResponse = {
  group: AdminWebGroupRecord;
};

export type AgentBackend = "libvirt_vm" | "container_worker_vm";

export type RuntimeImageInfo = {
  backend: AgentBackend;
  kind: "libvirt_vm_template" | "container_worker_image";
  label: string;
  template_alias?: string | null;
  image_alias?: string | null;
  image_ref: string | null;
  image_ref_sanitized?: boolean;
  missing_reason: string | null;
};

export type AgentLogView = "executions" | "threads";

export type AgentCapabilities = {
  start: boolean;
  shutdown: boolean;
  resume: boolean;
  enable_disable: boolean;
  kill: boolean;
  logs: boolean;
  stop: boolean;
  destroy: boolean;
};

export type AgentPromptQueueRecord = {
  reasoning_effort?: string | null;
  fast_mode?: FastMode | null;
  reasoning_effort_source?: "prompt" | "session" | "default" | null;
  fast_mode_source?: "prompt" | "session" | "default" | null;
  queue_key: string;
  effect_id: string;
  dispatch_id: string | null;
  target: string;
  vm_name: string;
  session_id: string | null;
  created_at: string;
  updated_at: string;
  coalesced_count: number;
  retry_count: number;
  max_retries: number | null;
  subscription_profile_id: string | null;
  queue_reason: string | null;
  retry_not_before: string | null;
  prompt_preview: string | null;
  prompt_length: number;
  metadata: Record<string, string>;
};

export type AgentPromptQueueSummary = {
  record_count: number;
  coalesced_count: number;
  oldest_created_at: string | null;
  newest_updated_at: string | null;
  // Agent-list summaries intentionally omit the individual queue records.
  // The selected-agent detail response supplies them.
  records?: AgentPromptQueueRecord[];
  truncated_count: number;
  read_error: string | null;
};

export type AgentExecutionSettings = {
  execution_id: string;
  model: string | null;
  reasoning_effort: string | null;
  fast_mode: boolean | "ultrafast" | null;
  reasoning_effort_source: "prompt" | "session" | "default" | null;
  fast_mode_source: "prompt" | "session" | "default" | null;
};

export type AgentRecord = {
  agent_id: string;
  backend: AgentBackend;
  target_name: string;
  session_id: string | null;
  directory_path: string;
  subscription_profile_id?: string | null;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
  host: Record<string, unknown> & { active_execution_settings?: AgentExecutionSettings[] };
  capabilities: AgentCapabilities;
  prompt_queue?: AgentPromptQueueSummary;
};

export type AgentExecutionRecord = {
  execution_id: string;
  session_id: string;
  worker_job_id: string;
  worker_url: string;
  target_name: string;
  status: string;
  last_status: string;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
  image: string | null;
  metadata: Record<string, unknown>;
};

export type AgentExecutionHistoryResponse = {
  agent_id: string;
  items: AgentExecutionRecord[];
  limit: number;
  has_more: boolean;
  next_cursor: string | null;
};

export type AgentCreateRequest = {
  cyber_access_program?: CyberAccessProgram | null;
  cost_category?: string | null;
  backend: AgentBackend;
  session_id?: string | null;
  directory_path?: string | null;
  model?: string | null;
  reasoning_effort?: string | null;
  fast_mode?: FastMode | null;
  ttl_seconds?: number | null;
  ttl_hours?: number | null;
  labels?: Record<string, string>;
  web_search_enabled?: boolean;
  allowed_tools?: string[];
  connector_grants?: Record<string, unknown>;
  host_directory_grants?: Record<string, string[]>;
  allowed_script_names?: string[];
  communication_target_ids?: string[];
  email_mailbox_grants?: EmailMailboxGrant[];
  http_targets?: HttpTargetsPayload;
  discord_channels?: string[];
  teams_channel_allowlist?: string[];
  email_mailboxes?: string[];
  email_folders?: string[];
  vm_name?: string | null;
  provider?: string | null;
  vm_user?: string | null;
  dotnet?: boolean;
  nuget?: boolean;
  mssql?: boolean;
  mssql_init_root?: string | null;
  playwright?: boolean;
  codex_skills?: string[];
  target_name?: string | null;
  container_image?: string | null;
  worker_id?: string | null;
  agent_provider?: string | null;
  model_provider_mode?: string | null;
  codex_auth_mode?: "metered_api" | "chatgpt_subscription" | null;
  subscription_profile_id?: string | null;
  vm_image?: string | null;
};

export type AgentCreateOperationStatus = "queued" | "running" | "succeeded" | "failed";

export type AgentCreateOperation = {
  operation_id: string;
  status: AgentCreateOperationStatus;
  backend: AgentBackend;
  target_name: string;
  session_id: string | null;
  directory_path: string;
  owner_id: string | null;
  owner_pid: number | null;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
};

export type AgentCreateOperationResponse = {
  operation: AgentCreateOperation;
};

export type AgentCreateOperationListResponse = {
  items: AgentCreateOperation[];
};

export type AgentCreateProviderOption = {
  value: string;
  label: string;
  execution_backends: AgentBackend[];
};

export type AgentCreateModelProviderModeOption = {
  value: string;
  label: string;
};

export type AgentCreateImageOption = {
  alias: string;
  image_ref: string;
};

export type AgentCreateCommunicationTargetOption = {
  value: string;
  label: string;
  description?: string | null;
  metadata?: Record<string, unknown>;
};

export type EmailMailboxAccess = "read" | "draft" | "send";

export type EmailMailboxGrant = {
  mailbox_id: string;
  access: EmailMailboxAccess;
  folders: string[];
};

export type AgentCreateEmailMailboxOption = {
  mailbox_id: string;
  provider: string;
  account_identifier: string;
  folders: string[];
  default_folders: string[];
  send_enabled: boolean;
};

export type AgentCreateConnectorOption = {
  connector_id: string;
  display_name: string;
  description: string;
  operations: Array<{
    name: string;
    description: string;
    access: "read" | "write";
    requires_approval: boolean;
    result_mode: "json" | "text" | "artifact";
  }>;
};

export type HostDirectoryCapability = "list" | "import" | "delete";

export type AgentCreateHostDirectoryOption = {
  directory_id: string;
  display_name: string;
  description: string;
  capabilities: HostDirectoryCapability[];
  delete_requires_approval: boolean;
};

export type AgentCreateOptionsResponse = {
  reasoning_effort_catalog?: ReasoningEffortCatalog;
  directory_path: string;
  backends: AgentBackend[];
  agent_providers: AgentCreateProviderOption[];
  model_provider_modes: AgentCreateModelProviderModeOption[];
  communication_targets: AgentCreateCommunicationTargetOption[];
  email_mailboxes: AgentCreateEmailMailboxOption[];
  connectors: AgentCreateConnectorOption[];
  host_directories: AgentCreateHostDirectoryOption[];
  libvirt: {
    default_template: string | null;
    templates: AgentCreateImageOption[];
  };
  container_worker: {
    workers?: { worker_id: string; display_name: string }[];
    selected_worker_id?: string | null;
    default_image: string | null;
    images: AgentCreateImageOption[];
    codex_auth_mode: "metered_api" | "chatgpt_subscription";
    model_provider_mode: string;
    subscription_profiles: CodexSubscriptionProfile[];
  };
};

export type AgentPolicyUpdateRequest = {
  cyber_access_program?: CyberAccessProgram | null;
  model?: string | null;
  reasoning_effort?: string | null;
  fast_mode?: FastMode | null;
  ttl_hours?: number | null;
  labels?: Record<string, string>;
  web_search_enabled?: boolean;
  allowed_tools?: string[];
  connector_grants?: Record<string, unknown>;
  host_directory_grants?: Record<string, string[]>;
  approval_required_tools?: string[];
  approval_notification_target_ids?: string[];
  communication_target_ids?: string[] | null;
  email_mailbox_grants?: EmailMailboxGrant[] | null;
  http_targets?: HttpTargetsPayload;
  codex_auth_mode?: "metered_api" | "chatgpt_subscription" | null;
  subscription_profile_id?: string | null;
  allowed_script_names?: string[];
  discord_channels?: string[];
  discord_channel_allowlist?: string[];
  teams_channel_allowlist?: string[];
  teams_chat_allowlist?: string[];
  teams_user_allowlist?: string[];
  metadata?: Record<string, unknown>;
  email_mailboxes?: string[];
  email_folders?: string[];
  email_folder_allowlist_by_mailbox?: Record<string, string[]>;
};

export type HttpTargetPayload = {
  base_url: string;
  managed_website_session?: string | null;
  metadata?: Record<string, unknown>;
};

export type HttpTargetsPayload = {
  targets: Record<string, HttpTargetPayload>;
};

export type AgentSessionPolicy = {
  cyber_access_program?: CyberAccessProgram | null;
  agent_harness?: string;
  effective_model_provider_mode?: string;
  reasoning_effort?: string | null;
  fast_mode?: FastMode | null;
  session_id: string;
  model: string | null;
  expires_at: string | null;
  allowed_tools: string[];
  connector_grants: Record<string, string[]>;
  host_directory_grants: Record<string, string[]>;
  approval_required_tools?: string[];
  approval_notification_target_ids: string[];
  approval_notification_target_options: Array<{ value: string; label: string }>;
  communication_target_ids: string[] | null;
  allowed_script_names: string[];
  discord_channels: string[];
  discord_channel_allowlist: string[];
  teams_channel_allowlist: string[];
  teams_chat_allowlist: string[];
  teams_user_allowlist: string[];
  metadata: Record<string, unknown>;
  email_mailboxes: string[];
  email_folders: string[];
  email_folder_allowlist_by_mailbox: Record<string, string[]>;
  email_mailbox_grants: EmailMailboxGrant[] | null;
  email_send_enabled: boolean;
  labels: Record<string, string>;
  web_search_enabled: boolean;
  codex_auth_mode: "metered_api" | "chatgpt_subscription" | null;
  subscription_profile_id: string | null;
  effective_subscription_profile_id: string | null;
  effective_codex_auth_mode: "metered_api" | "chatgpt_subscription";
  codex_auth_mode_source: "session_override" | "directory_default" | "provider_route";
};

export type CodexSubscriptionProfile = {
  profile_id: string;
  display_name: string;
  enabled: boolean;
  pinned_account_id: string | null;
  available: boolean;
  signed_in: boolean;
  account_id: string | null;
  account_type: string | null;
  email: string | null;
  plan_type: string | null;
  last_refresh: string | null;
  access_token_expires_at: string | null;
  rate_limits: Record<string, unknown> | null;
  usage: Record<string, unknown> | null;
  active_job_count: number;
  queued_prompt_count: number;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type CodexSubscriptionProfilesResponse = {
  directory_path: string;
  worker_url: string | null;
  profiles: CodexSubscriptionProfile[];
};

export type CodexSubscriptionResetResponse = {
  outcome: "reset" | "alreadyRedeemed" | "nothingToReset" | "noCredit";
  profile: CodexSubscriptionProfile;
};

export type AgentListResponse = {
  items: AgentRecord[];
  directory_path: string;
  direct_only: boolean;
};

export type AgentLogResponse = {
  agent_id: string;
  backend: AgentBackend | null;
  view?: AgentLogView;
  lines: number;
  text: string;
  limit?: number;
  offset?: number;
  returned_lines?: number;
  total_lines?: number | null;
  start_line?: number | null;
  end_line?: number | null;
  has_more_before?: boolean;
  query?: string | null;
  next_cursor?: string | null;
};

export type AgentActionResponse = {
  success: boolean;
  agent_id: string;
  backend: AgentBackend;
  target_name: string;
  action: string;
  message: string;
  status?: string | null;
  session_id?: string | null;
};

export type SkillLibraryItem = {
  name: string;
  size_bytes: number;
  updated_at: string;
};

export type SkillLibraryDetail = SkillLibraryItem & {
  content: string;
};

export type SkillListResponse = {
  items: SkillLibraryItem[];
};

export type SkillResponse = {
  skill: SkillLibraryDetail;
};

export type SkillMutationResponse = {
  skill: SkillLibraryItem;
  deleted?: boolean;
};

export type VmRecord = {
  vm_name: string;
  provider: string;
  issue_number: string | null;
  session_ids: string[];
  labels: Record<string, string>;
  directory_paths: string[];
  power_state: string;
  libvirt_state: string;
  provider_state?: string | null;
  ip_address: string | null;
  agent: {
    state: string;
    pid: number | null;
    dispatch_id: string | null;
    repo_names: string[];
    preferred_repo_name: string | null;
  };
  last_state_update_at: string | null;
};

export type VmListResponse = {
  items: VmRecord[];
  directory_path: string;
  direct_only: boolean;
};

export type VmLogResponse = {
  vm_name: string;
  lines: number;
  text: string;
};

export type VmActionResponse = {
  success: boolean;
  vm_name: string;
  action: string;
  message: string;
  thread_id?: string | null;
  target_dir?: string | null;
  guest_repo_name?: string | null;
  remote_url?: string | null;
};

export type IssueRecord = {
  issue_id: string;
  issue_number: string;
  title: string;
  body: string;
  description?: string | null;
  reason?: string | null;
  rejection_reason?: string | null;
  yaml_path: string;
  directory_path: string;
  managed_repo_id?: string | null;
  source_type: string;
  state?: string | null;
  url?: string | null;
  tags: string[];
  comments: Array<Record<string, unknown>>;
  source: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  remote_reference?: Record<string, unknown> | null;
  can_edit: boolean;
  can_delete: boolean;
  can_create_vm: boolean;
};

export type IssueListResponse = {
  items: IssueRecord[];
  directory_path: string;
  direct_only: boolean;
};

export type IssueDetail = {
  issue_id: string;
  issue_number: string;
  title: string;
  body: string;
  description?: string | null;
  state?: string | null;
  directory_path?: string | null;
  managed_repo_id?: string | null;
  yaml_path?: string | null;
  url?: string | null;
  source?: string | null;
  source_type?: string | null;
  provider?: string | null;
  remote_issue_id?: string | null;
  reason?: string | null;
  rejection_reason?: string | null;
  tags: unknown[];
  comments: unknown[];
  metadata: Record<string, unknown>;
  remote_reference?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
  can_edit: boolean;
  can_delete: boolean;
  can_create_vm: boolean;
  raw: Record<string, unknown>;
};

export type IssueYamlResponse = {
  issue_id: string;
  issue_number: string;
  path: string;
  yaml_text: string;
};

export type IssueCreateResponse = {
  issue: IssueRecord;
  vm_action?: VmActionResponse;
};
