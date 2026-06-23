export type AdminView =
  | "workflow"
  | "agents"
  | "skills"
  | "vms"
  | "issues"
  | "sessions"
  | "tool-calls"
  | "configuration"
  | "users"
  | "workers";

export type AdminWebUserRole = "admin" | "regular";

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
  role: AdminWebUserRole;
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
  queue_key: string;
  effect_id: string;
  target: string;
  vm_name: string;
  session_id: string | null;
  created_at: string;
  updated_at: string;
  coalesced_count: number;
  prompt_preview: string | null;
  prompt_length: number;
  metadata: Record<string, string>;
};

export type AgentPromptQueueSummary = {
  record_count: number;
  coalesced_count: number;
  oldest_created_at: string | null;
  newest_updated_at: string | null;
  records: AgentPromptQueueRecord[];
  truncated_count: number;
  read_error: string | null;
};

export type AgentRecord = {
  agent_id: string;
  backend: AgentBackend;
  target_name: string;
  session_id: string | null;
  directory_path: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
  host: Record<string, unknown>;
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

export type AgentCreateRequest = {
  backend: AgentBackend;
  session_id?: string | null;
  directory_path?: string | null;
  model?: string | null;
  ttl_hours?: number | null;
  labels?: Record<string, string>;
  web_search_enabled?: boolean;
  allowed_tools?: string[];
  allowed_script_names?: string[];
  communication_target_ids?: string[];
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
  agent_provider?: string | null;
  model_provider_mode?: string | null;
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

export type AgentCreateOptionsResponse = {
  directory_path: string;
  backends: AgentBackend[];
  agent_providers: AgentCreateProviderOption[];
  model_provider_modes: AgentCreateModelProviderModeOption[];
  communication_targets: AgentCreateCommunicationTargetOption[];
  libvirt: {
    default_template: string | null;
    templates: AgentCreateImageOption[];
  };
  container_worker: {
    default_image: string | null;
    images: AgentCreateImageOption[];
  };
};

export type AgentPolicyUpdateRequest = {
  model?: string | null;
  ttl_hours?: number | null;
  labels?: Record<string, string>;
  web_search_enabled?: boolean;
  allowed_tools?: string[];
  approval_required_tools?: string[];
  approval_notification_target_ids?: string[];
  allowed_script_names?: string[];
  discord_channels?: string[];
  discord_channel_allowlist?: string[];
  teams_channel_allowlist?: string[];
  metadata?: Record<string, unknown>;
  email_mailboxes?: string[];
  email_folders?: string[];
};

export type AgentSessionPolicy = {
  session_id: string;
  model: string | null;
  expires_at: string | null;
  allowed_tools: string[];
  approval_required_tools?: string[];
  approval_notification_target_ids: string[];
  approval_notification_target_options: Array<{ value: string; label: string }>;
  allowed_script_names: string[];
  discord_channels: string[];
  discord_channel_allowlist: string[];
  teams_channel_allowlist: string[];
  metadata: Record<string, unknown>;
  email_mailboxes: string[];
  email_folders: string[];
  labels: Record<string, string>;
  web_search_enabled: boolean;
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
  repo_source?: string | null;
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
  repo_source?: string | null;
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
