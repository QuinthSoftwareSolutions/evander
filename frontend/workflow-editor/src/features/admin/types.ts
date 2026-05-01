export type AdminView = "workflow" | "agents" | "vms" | "issues";

export type AgentBackend = "libvirt_vm" | "container_worker_vm";

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
  allowed_tools?: string[];
  allowed_script_names?: string[];
  discord_channels?: string[];
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
};

export type AgentPolicyUpdateRequest = {
  model?: string | null;
  ttl_hours?: number | null;
  labels?: Record<string, string>;
  allowed_tools?: string[];
  allowed_script_names?: string[];
  discord_channels?: string[];
  email_mailboxes?: string[];
  email_folders?: string[];
};

export type AgentSessionPolicy = {
  session_id: string;
  model: string | null;
  expires_at: string | null;
  allowed_tools: string[];
  allowed_script_names: string[];
  discord_channels: string[];
  email_mailboxes: string[];
  email_folders: string[];
  labels: Record<string, string>;
};

export type AgentListResponse = {
  items: AgentRecord[];
  directory_path: string;
  direct_only: boolean;
};

export type AgentLogResponse = {
  agent_id: string;
  backend: AgentBackend | null;
  lines: number;
  text: string;
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
