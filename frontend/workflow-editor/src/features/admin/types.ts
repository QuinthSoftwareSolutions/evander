export type AdminView = "workflow" | "vms" | "issues";

export type VmRecord = {
  vm_name: string;
  issue_number: string | null;
  session_ids: string[];
  labels: Record<string, string>;
  directory_paths: string[];
  libvirt_state: string;
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
