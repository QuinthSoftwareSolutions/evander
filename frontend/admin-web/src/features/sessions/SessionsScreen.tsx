import { FastModeSelect } from "../admin/FastModeSelect";
import { fastModeSelection, fastModeValue } from "../admin/fastMode";
import { CostCategoryInput } from "../reports/CostCategoryInput";
import { useEffect, useMemo, useState } from "react";

import { ReasoningEffortSelect } from "../admin/ReasoningEffortSelect";
import type { ReasoningEffortCatalog } from "../admin/reasoningEffort";
import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { JsonObjectKeyValueEditor, StringListEditor } from "../admin/StructuredPolicyInputs";
import { CleanupIcon, DeleteIcon, ExtendIcon, RevokeIcon, SettingsIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";
import { RecordPlaceholder, RecordWorkspace } from "../admin/RecordWorkspace";
import type {
  AgentCreateCommunicationTargetOption,
  AgentCreateEmailMailboxOption,
  AgentCreateHostDirectoryOption,
  EmailMailboxGrant,
  RuntimeImageInfo,
} from "../admin/types";
import { loadAgentCreateOptions } from "../agents/agentCreateShared";
import {
  CommunicationTargetCheckboxTable,
  EmailMailboxGrantEditor,
} from "../communications/SessionGrantEditors";
import { legacyEmailMailboxAccess } from "../communications/legacyCommunicationPolicy";
import { HttpTargetsEditor } from "../httpTargets/HttpTargetsEditor";
import {
  httpTargetsDraftFromMetadata,
  httpTargetsPayload,
  metadataWithoutHttpTargets,
  type HttpTargetDraftEntry,
} from "../httpTargets/httpTargetDraft";
import {
  HostDirectoryGrantSelector,
  hostDirectoryToolsForGrants,
} from "../hostDirectories/HostDirectoryGrantSelector";
import { isInheritedApprovalRequiredForTool, type SessionEffectiveToolPolicy } from "./sessionPolicy";
import { ApprovalSettingsModal, RenewSessionModal } from "./SessionPolicyModals";
import {
  DEFAULT_APPROVAL_SETTINGS,
  normalizeApprovalSettings,
  normalizeApprovalToolSettings,
  type ApprovalGrantSettings,
  type McpToolPolicyRule,
} from "./sessionApprovalSettings";
import {
  canToggleWakeScheduleEnabled,
  visibleWakeSchedules as filterVisibleWakeSchedules,
} from "./wakeScheduleVisibility";
import {
  metadataWithoutSessionModelProviderMode,
  SESSION_MODEL_PROVIDER_MODE_OPTIONS,
  sessionModelProviderModeFromMetadata,
  type SessionModelProviderModeSelection,
} from "./sessionModelProviderMode";

type EffectiveToolPolicy = SessionEffectiveToolPolicy & {
  allowed_tools?: string[];
  approval_tool_settings?: Record<string, ApprovalGrantSettings>;
  mcp_tool_policies?: Record<string, McpToolPolicyRule>;
  tool_argument_policies?: Record<string, unknown>;
};

type SessionRecord = {
  session_id: string;
  vm_id: string | null;
  vm_provider?: string | null;
  vm_provider_id?: string | null;
  model: string | null;
  reasoning_effort?: string | null;
  fast_mode?: boolean | null;
  cost_category?: string | null;
  agent_harness?: string;
  effective_model_provider_mode?: string;
  model_provider_mode?: string | null;
  directory_path: string;
  allowed_tools?: string[];
  connector_grants?: Record<string, string[]>;
  host_directory_grants?: Record<string, string[]>;
  approval_required_tools?: string[];
  approval_tool_settings?: Record<string, ApprovalGrantSettings>;
  approval_notification_target_ids?: string[];
  approval_notification_target_options?: Array<{ value: string; label: string }>;
  allowed_contact_ids?: string[] | null;
  communication_target_ids?: string[] | null;
  communication_target_options?: AgentCreateCommunicationTargetOption[];
  effective_tool_policy?: EffectiveToolPolicy | null;
  allowed_script_names?: string[];
  discord_channel_allowlist?: string[];
  teams_channel_allowlist?: string[];
  teams_chat_allowlist?: string[];
  teams_user_allowlist?: string[];
  email_mailbox_allowlist?: string[];
  email_folder_allowlist?: string[];
  email_folder_allowlist_by_mailbox?: Record<string, string[]>;
  email_mailbox_grants?: EmailMailboxGrant[] | null;
  email_mailbox_options?: AgentCreateEmailMailboxOption[];
  email_send_enabled?: boolean;
  communication_file_receive_enabled?: boolean;
  communication_file_send_enabled?: boolean;
  communication_file_max_bytes?: number | null;
  communication_file_allowed_mime_types?: string[];
  communication_file_allowed_extensions?: string[];
  communication_file_blocked_extensions?: string[];
  communication_file_require_approval_for_send?: boolean;
  web_search_enabled?: boolean;
  expires_at: string;
  created_at: string;
  metadata?: Record<string, unknown>;
  runtime_image?: RuntimeImageInfo | null;
  issue_number?: string | null;
  is_expired: boolean;
  container_lifecycle?: ContainerSessionLifecycle | null;
};

type ContainerSessionLifecycleAction = {
  supported: boolean;
  kind?: string | null;
  label?: string | null;
  reason?: string | null;
};

type ContainerSessionLifecycle = {
  state: string;
  last_heartbeat_at?: string | null;
  last_completion_at?: string | null;
  last_signal_at?: string | null;
  cleanup_attempted_at?: string | null;
  cleanup_completed_at?: string | null;
  cleanup_error?: string | null;
  actions?: {
    cleanup?: ContainerSessionLifecycleAction | null;
    recovery?: ContainerSessionLifecycleAction | null;
  } | null;
};

type SessionListResponse = {
  items: SessionRecord[];
  counts: {
    total: number;
    active: number;
    expired: number;
  };
  status_ids: {
    active: string[];
    expired: string[];
  };
};

type WakeSchedule = {
  request_id: string;
  session_id: string;
  prompt: string;
  cron?: string | null;
  resume_at?: string | null;
  resume_mode: string;
  status?: string | null;
  enabled: boolean;
  updated_at?: string | null;
  cancelled_at?: string | null;
  cancelled_by_session_id?: string | null;
  cancel_reason?: string | null;
  cancel_metadata?: Record<string, unknown>;
  schedule_kind: "cron" | "resume_at";
};

type WakeLedgerEntry = {
  request_id: string;
  session_id: string;
  source?: string | null;
  resume_at?: string | null;
  next_run_at?: string | null;
  scheduled_for?: string | null;
  schedule_cron?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  status: string;
  status_reason?: string | null;
  status_updated_at?: string | null;
  prompt_summary?: string | null;
  redacted_prompt?: string | null;
  latest_dispatch?: {
    dispatch_id: string;
    status: string;
    dispatched_at?: string | null;
    source?: string | null;
    error?: string | null;
  } | null;
  latest_dispatch_id?: string | null;
  latest_dispatch_status?: string | null;
  last_error?: string | null;
  last_error_at?: string | null;
  completed_at?: string | null;
  completion_evidence?: Record<string, unknown>;
  confirmation_state?: string | null;
  retry_available: boolean;
  failure_reason?: string | null;
  enabled: boolean;
};

type WakeScheduleKind = "cron" | "resume_at";
type WakeLedgerFilter = "all" | "pending" | "due" | "dispatching" | "queued" | "dispatched" | "failed" | "missed_or_late" | "completed_or_acknowledged";

type SessionDetailResponse = {
  session: SessionRecord;
  wake_schedules: WakeSchedule[];
  git_grants: ManagedGitGrant[];
  summary_json: Record<string, unknown>;
};

type ManagedGitGrant = {
  managed_repo_id: string;
  access_mode: "read-only" | "write";
  lifetime: "session" | "operation";
  execution_backend?: string;
  git_ssh_alias?: string;
  authorized_ref_prefix?: string;
};

type ManagedGitRepository = {
  managed_repo_id: string;
  display_name?: string;
  canonical_repository_url?: string;
  directory_path?: string;
};

type ManagedGitRepositoryListResponse = {
  repositories: ManagedGitRepository[];
};

type CatalogOption = {
  value: string;
  label: string;
  description?: string;
  group?: string;
  provider?: string;
  addon_id?: string;
};

type SessionCatalogResponse = {
  reasoning_effort_catalog?: ReasoningEffortCatalog;
  tool_options: CatalogOption[];
  model_options?: CatalogOption[];
  model_provider_modes?: CatalogOption[];
  resume_mode_options: CatalogOption[];
  renew_extension_presets: string[];
};

type SessionMutationResponse = {
  action: string;
  message: string;
  session?: SessionRecord;
};

type ToolOption = SessionCatalogResponse["tool_options"][number];

type TabKey = "active" | "expired";
type SessionPolicyDraft = {
  costCategory: string;
  modelProviderMode: SessionModelProviderModeSelection;
  allowedScriptNames: string[];
  connectorGrants: Record<string, unknown>;
  hostDirectoryGrants: Record<string, string[]>;
  approvalRequiredTools: string[];
  approvalToolSettings: Record<string, ApprovalGrantSettings>;
  approvalNotificationTargetIds: string[];
  allowedContactIds: string[] | null;
  communicationTargetIds: string[] | null;
  emailMailboxGrants: EmailMailboxGrant[] | null;
  discordChannelAllowlist: string[];
  teamsChannelAllowlist: string[];
  communicationFileReceiveEnabled: boolean;
  communicationFileSendEnabled: boolean;
  communicationFileMaxBytes: string;
  communicationFileAllowedMimeTypes: string[];
  communicationFileAllowedExtensions: string[];
  communicationFileBlockedExtensions: string[];
  communicationFileRequireApprovalForSend: boolean;
  webSearchEnabled: boolean;
  metadata: Record<string, unknown>;
  httpTargets: HttpTargetDraftEntry[];
  httpTargetsSourceError: string | null;
};

const POLL_INTERVAL_MS = 10000;
const TOOL_DESCRIPTION_FALLBACK = "Allow the session to invoke this MCP tool when the agent needs it.";
const TOOL_DESCRIPTIONS: Record<string, string> = {
  create_ado_backlog_item: "Create a new Azure DevOps backlog item on the host using the configured project defaults.",
  post_ado_backlog_comment: "Post a comment to an Azure DevOps backlog item, optionally with staged image artifacts.",
  update_ado_backlog_text: "Update Azure DevOps backlog description or acceptance criteria text, optionally with staged image artifacts.",
  update_ado_backlog_title: "Update the title of an Azure DevOps backlog item.",
  query_ado_backlog_items: "Search Azure DevOps backlog items with the host-side query filters and return matching work items.",
  list_ado_current_iteration_backlog_items: "List backlog items assigned to the current Azure DevOps iteration for the configured project.",
  get_ado_backlog_item: "Fetch a single Azure DevOps backlog item with its latest host-side details.",
  resolve_ado_identity: "Resolve an Azure DevOps user identity for reporting filters.",
  query_ado_work_item_activity: "Find Azure DevOps work items with matching created, changed, or commented activity.",
  create_local_issue: "Create a local issue record in the selected directory so the session can track work inside evander.",
  list_issues: "List local issues that the session is allowed to inspect in the current directory scope.",
  get_issue: "Read the details of a specific local issue, including status, metadata, and body content.",
  set_issue_plan: "Store or update the implementation plan attached to a local issue.",
  stage_artifact: "Copy a file or directory from the agent workspace into the session artifacts folder.",
  approve_issue: "Mark a local issue as approved so execution work can proceed.",
  commit_issue: "Move a local issue into the committed state once work is ready to start.",
  reject_issue: "Reject a local issue instead of approving it for execution.",
  claim_next_issue: "Claim the next available local issue that matches the current session scope.",
  complete_issue: "Mark a local issue as completed after the work has been finished.",
  get_agent_status: "Inspect the current guest agent state, including whether it is running and what it is doing.",
  get_agent_logs: "Fetch recent logs for the guest agent attached to this session.",
  prompt_agent: "Send a new prompt to the guest agent running in this session.",
  list_pending_agent_prompts: "List prompts queued for the guest agent that have not been handled yet.",
  respond_to_agent: "Reply to a pending agent prompt or approval-style checkpoint from the guest agent.",
  schedule_resume: "Create or update a host-managed wake schedule that resumes this session later.",
  cancel_scheduled_wake: "Cancel an obsolete host-managed wake schedule owned by this session.",
  run_script: "Run an approved host-side script on behalf of the session.",
  mssql_provision_database: "Provision a session-owned SQL Server database through the container worker.",
  mssql_list_databases: "List SQL Server databases owned by this session.",
  mssql_drop_database: "Drop a SQL Server database owned by this session.",
  copilot_host_auth: "Bridge host-managed GitHub Copilot authentication into the guest when that provider is in use.",
  discord_get_commands: "Read Discord command traffic that the host has ingested for this session or directory.",
  discord_post_status: "Post a Discord status update from the host on behalf of the session.",
  has_pending_discord_messages: "Check whether the session has Discord messages waiting to be reviewed.",
  list_unread_discord_messages: "List unread Discord messages available to the session.",
  list_all_discord_messages: "List all Discord messages visible to the session within its policy scope.",
  add_message_reaction: "Add a reaction to an approved Discord or Teams message from the host side.",
  communication_list_attachments: "List inbound attachment metadata from an allowlisted Discord, Teams, or email source.",
  communication_download_attachment: "Download one inbound attachment into the host communication artifact store.",
  list_pr_feedback_attachments: "List Azure DevOps files linked from an authorized pull request feedback item.",
  download_pr_feedback_attachment: "Download one authorized pull request feedback file into the host artifact store.",
  communication_copy_artifact_to_workspace: "Copy a staged communication artifact into the agent workspace.",
  discord_post_file: "Post staged communication file artifacts to an allowlisted Discord channel.",
  teams_get_messages: "Read Microsoft Teams messages made available to the session by the host ingest worker.",
  teams_post_status: "Post a Microsoft Teams status update from the host on behalf of the session.",
  teams_post_file: "Post staged communication file artifacts to an allowlisted Microsoft Teams target.",
  teams_meeting_get_events: "Read Teams meeting transcript and status events captured for this session.",
  teams_meeting_speak: "Speak into the active Teams meeting through the host-managed meeting bridge.",
  teams_meeting_stop: "Leave the active Teams meeting and close the host-side meeting binding.",
  list_contacts: "List directory contact ids and safe contact summaries without exposing phone numbers or email addresses.",
  make_phone_call: "Start host-side phone calls to directory-configured contact ids.",
  get_phone_call_result: "Read phone call status and transcripts for calls owned by the session.",
  submit_voice_query_response: "Answer delegated live voice questions for phone calls projected from this session.",
  email_list_unread: "List unread email messages available to the session through the host email provider.",
  email_get_message: "Open a specific email message that the session is allowed to inspect.",
  email_create_draft: "Create an email draft on the host without sending it yet.",
  email_create_draft_with_attachments: "Create an email draft with staged communication file artifacts attached.",
  email_send_approved: "Send a previously approved email draft through the host email provider.",
  email_send_approved_with_attachments: "Send a previously approved email with staged communication file artifacts attached.",
  approval_submit: "Submit an approval request when the session needs a guarded action reviewed by a human.",
  approval_check: "Check the current state of an approval request that the session previously submitted.",
  has_pending_feedback: "Check whether any pull request or review feedback is waiting for the session.",
  list_unread_feedback: "List unread review feedback items that need the session’s attention.",
  list_all_feedback: "List all review feedback visible to the session within its scope.",
  post_pr_feedback_reply: "Post a reply to pull request feedback using the host integration.",
  list_pull_requests: "List pull requests available to the session through the configured host provider.",
  get_pull_request: "Fetch the latest details for a specific pull request.",
  list_ado_pr_comment_activity: "Find Azure DevOps pull request comments matching an author and date range.",
  list_ado_pr_commit_activity: "Find Azure DevOps pull request commits matching an author and date range.",
  request_pr_review: "Request reviewers on a pull request from the host side.",
  post_pr_comment: "Post a pull request comment using the host integration.",
  post_pr_inline_comment: "Post an inline pull request comment at a specific diff position through the host integration.",
  submit_pr_review: "Submit a pull request review decision and review body from the host side.",
};

function toDatetimeLocalValue(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  const hours = `${date.getHours()}`.padStart(2, "0");
  const minutes = `${date.getMinutes()}`.padStart(2, "0");
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function sortTools(values: string[]): string[] {
  return [...values].sort((left, right) => left.localeCompare(right));
}

function dedupeStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const normalized = value.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

function formatNotificationTargetOptionLabel(option: { value: string; label: string }): string {
  const label = option.label.trim();
  const value = option.value.trim();
  if (!label || label === value) {
    return value;
  }
  return `${label} (${value})`;
}

function policyDraftFromSession(session: SessionRecord): SessionPolicyDraft {
  const httpTargetsDraft = httpTargetsDraftFromMetadata(session.metadata);
  const modelProviderMode = sessionModelProviderModeFromMetadata({
    model_provider_mode: session.model_provider_mode ?? session.metadata?.model_provider_mode,
  });
  return {
    modelProviderMode,
    costCategory: session.cost_category ?? "",
    allowedScriptNames: dedupeStrings(session.allowed_script_names ?? []),
    connectorGrants: { ...(session.connector_grants ?? {}) },
    hostDirectoryGrants: { ...(session.host_directory_grants ?? {}) },
    approvalRequiredTools: sortTools(session.approval_required_tools ?? []),
    approvalToolSettings: normalizeApprovalToolSettings(session.approval_tool_settings),
    approvalNotificationTargetIds: dedupeStrings(session.approval_notification_target_ids ?? []),
    allowedContactIds:
      session.allowed_contact_ids == null
        ? null
        : dedupeStrings(session.allowed_contact_ids),
    communicationTargetIds:
      session.communication_target_ids == null
        ? null
        : dedupeStrings(session.communication_target_ids),
    emailMailboxGrants:
      session.email_mailbox_grants == null
        ? null
        : session.email_mailbox_grants.map((grant) => ({
            ...grant,
            folders: dedupeStrings(grant.folders),
          })),
    discordChannelAllowlist: dedupeStrings(session.discord_channel_allowlist ?? []),
    teamsChannelAllowlist: dedupeStrings(session.teams_channel_allowlist ?? []),
    communicationFileReceiveEnabled: Boolean(session.communication_file_receive_enabled),
    communicationFileSendEnabled: Boolean(session.communication_file_send_enabled),
    communicationFileMaxBytes: session.communication_file_max_bytes == null ? "" : String(session.communication_file_max_bytes),
    communicationFileAllowedMimeTypes: dedupeStrings(session.communication_file_allowed_mime_types ?? []),
    communicationFileAllowedExtensions: dedupeStrings(session.communication_file_allowed_extensions ?? []),
    communicationFileBlockedExtensions: dedupeStrings(session.communication_file_blocked_extensions ?? []),
    communicationFileRequireApprovalForSend: Boolean(session.communication_file_require_approval_for_send),
    webSearchEnabled: Boolean(session.web_search_enabled),
    metadata: metadataWithoutSessionModelProviderMode(metadataWithoutHttpTargets(session.metadata)),
    httpTargets: httpTargetsDraft.entries,
    httpTargetsSourceError: httpTargetsDraft.sourceError,
  };
}

function parseNullableNonNegativeInteger(value: string, label: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`${label} must be a non-negative integer or blank.`);
  }
  return parsed;
}

function describeTool(tool: ToolOption): string {
  const catalogDescription = tool.description?.trim();
  if (catalogDescription) return catalogDescription;
  return TOOL_DESCRIPTIONS[tool.value] ?? `${tool.label} tool. ${TOOL_DESCRIPTION_FALLBACK}`;
}

function validateLeadingDotExtensionValue(value: string): string | null {
  const normalized = value.trim();
  if (!normalized) return null;
  if (!normalized.startsWith(".")) {
    return "Extension must start with a leading dot.";
  }
  if (normalized.length === 1) {
    return "Extension must include characters after the leading dot.";
  }
  return null;
}

function validateLeadingDotExtensions(values: readonly string[], fieldLabel: string): string | null {
  for (const [index, value] of values.entries()) {
    const normalized = value.trim();
    if (!normalized) continue;
    if (!normalized.startsWith(".")) {
      return `${fieldLabel} row ${index + 1} must start with a leading dot.`;
    }
    if (normalized.length === 1) {
      return `${fieldLabel} row ${index + 1} must include characters after the leading dot.`;
    }
  }
  return null;
}

const sessionListCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

function groupToolOption(tool: ToolOption): string {
  const declaredGroup = tool.group?.trim();
  if (declaredGroup && declaredGroup !== "Other") return declaredGroup;
  const { value } = tool;
  if ([
    "create_local_issue",
    "list_issues",
    "get_issue",
    "set_issue_plan",
    "approve_issue",
    "commit_issue",
    "reject_issue",
    "claim_next_issue",
    "complete_issue",
  ].includes(value)) {
    return "Issue intake";
  }
  if ([
    "create_ado_backlog_item",
    "post_ado_backlog_comment",
    "update_ado_backlog_text",
    "update_ado_backlog_title",
    "update_ado_backlog_tags",
    "query_ado_backlog_items",
    "list_ado_current_iteration_backlog_items",
    "get_ado_backlog_item",
    "delete_ado_backlog_item",
    "get_ado_daily_status_report_data",
    "resolve_ado_identity",
    "query_ado_work_item_activity",
  ].includes(value)) {
    return "Azure DevOps";
  }
  if ([
    "list_pull_requests",
    "get_pull_request",
    "list_ado_pr_comment_activity",
    "list_ado_pr_commit_activity",
    "request_pr_review",
    "post_pr_comment",
    "post_pr_inline_comment",
    "submit_pr_review",
    "post_pr_feedback_reply",
  ].includes(value)) {
    return "Pull requests";
  }
  if (value.startsWith("approval_")) {
    return "Approvals";
  }
  if ([
    "get_agent_status",
    "get_agent_logs",
    "prompt_agent",
    "list_pending_agent_prompts",
    "respond_to_agent",
    "consult_codebase",
    "get_codebase_consultation",
    "cancel_codebase_consultation",
    "submit_codebase_consultation_result",
    "schedule_resume",
    "cancel_scheduled_wake",
    "run_script",
    "copilot_host_auth",
    "get_workflow_snapshot",
  ].includes(value)) {
    return "Agent control";
  }
  if (value.startsWith("assistant_")) {
    return "Assistant";
  }
  if ([
    "discord_get_commands",
    "discord_post_status",
    "has_pending_discord_messages",
    "list_unread_discord_messages",
    "list_all_discord_messages",
  ].includes(value)) {
    return "Discord";
  }
  if (["add_message_reaction"].includes(value)) {
    return "Messaging";
  }
  if (
    ["list_contacts", "make_phone_call", "get_phone_call_result", "submit_voice_query_response"].includes(value)
  ) {
    return "Voice calling";
  }
  if (["mssql_provision_database", "mssql_list_databases", "mssql_drop_database"].includes(value)) {
    return "SQL Server";
  }
  if ([
    "communication_list_attachments",
    "communication_download_attachment",
    "list_pr_feedback_attachments",
    "download_pr_feedback_attachment",
    "stage_artifact",
    "communication_copy_artifact_to_workspace",
    "communication_stage_workspace_file",
    "discord_post_file",
    "teams_post_file",
    "list_inline_media",
    "download_inline_media",
  ].includes(value)) {
    return "Files";
  }
  if ([
    "teams_get_messages",
    "teams_post_status",
    "teams_meeting_get_events",
    "teams_meeting_speak",
    "teams_meeting_stop",
  ].includes(value)) {
    return "Teams";
  }
  if (value.startsWith("email_")) {
    return "Email";
  }
  if (["has_pending_feedback", "list_unread_feedback", "list_all_feedback"].includes(value)) {
    return "Feedback";
  }
  if (value.startsWith("classification_")) {
    return "Classification";
  }
  if (value.startsWith("semantic_")) {
    return "Semantic store";
  }
  if (["list_connectors", "call_connector"].includes(value)) {
    return "Connectors";
  }
  return "Other";
}

function groupTools(toolOptions: ToolOption[]) {
  const groups = new Map<string, ToolOption[]>();
  for (const tool of toolOptions) {
    const group = groupToolOption(tool);
    const current = groups.get(group);
    if (current) current.push(tool);
    else groups.set(group, [tool]);
  }
  const groupNames = [...groups.keys()].sort((left, right) => {
    if (left === "Other") return 1;
    if (right === "Other") return -1;
    return left.localeCompare(right, undefined, { sensitivity: "base" });
  });
  return groupNames
    .map((name) => ({ name, items: groups.get(name) ?? [] }))
    .map((group) => ({
      ...group,
      items: [...group.items].sort((left, right) => left.label.localeCompare(right.label, undefined, { sensitivity: "base" })),
    }));
}

function matchesFilter(item: SessionRecord, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  return [
    item.session_id,
    item.vm_id ?? "",
    item.vm_provider ?? "",
    item.vm_provider_id ?? "",
    item.issue_number ?? "",
    item.directory_path,
    item.model ?? "",
    item.container_lifecycle?.state ?? "",
    runtimeImageSearchText(item),
  ]
    .join(" ")
    .toLowerCase()
    .includes(normalized);
}

function runtimeImageSearchText(item: SessionRecord): string {
  const runtimeImage = item.runtime_image;
  if (!runtimeImage) return "";
  return [
    runtimeImage.backend,
    runtimeImage.kind,
    runtimeImage.label,
    runtimeImage.template_alias ?? "",
    runtimeImage.image_alias ?? "",
    runtimeImage.image_ref ?? "",
    runtimeImage.missing_reason ?? "",
  ].join(" ");
}

function sessionItemsForTab(response: SessionListResponse | null, tab: TabKey): SessionRecord[] {
  if (!response) return [];
  const ids = new Set(tab === "active" ? response.status_ids.active : response.status_ids.expired);
  return response.items.filter((item) => ids.has(item.session_id));
}

function runtimeImagePrimaryText(session: SessionRecord): string {
  const runtimeImage = session.runtime_image;
  if (!runtimeImage) return "Unavailable";
  if (runtimeImage.backend === "container_worker_vm") {
    return runtimeImage.image_ref ?? "Unavailable";
  }
  if (runtimeImage.missing_reason) {
    return runtimeImage.label;
  }
  return runtimeImage.template_alias ?? runtimeImage.label;
}

function runtimeImageTooltip(session: SessionRecord): string {
  const runtimeImage = session.runtime_image;
  if (!runtimeImage) return "Runtime image metadata unavailable.";
  if (runtimeImage.backend === "container_worker_vm") {
    const imageRef = runtimeImage.image_ref ?? "Unavailable";
    return runtimeImage.image_alias
      ? `${runtimeImage.label}: ${imageRef} (alias: ${runtimeImage.image_alias})`
      : `${runtimeImage.label}: ${imageRef}`;
  }
  if (runtimeImage.missing_reason) {
    return runtimeImage.label;
  }
  const imageRef = runtimeImage.image_ref ?? "Unavailable";
  if (runtimeImage.image_ref_sanitized) {
    return `${runtimeImage.label}: ${imageRef} (sanitized display from host-path metadata)`;
  }
  return runtimeImage.template_alias
    ? `${runtimeImage.label}: ${runtimeImage.template_alias} uses ${imageRef}`
    : `${runtimeImage.label}: ${imageRef}`;
}

function lifecycleLabel(session: SessionRecord): string {
  const state = session.container_lifecycle?.state?.trim();
  if (!state) return session.is_expired ? "Expired" : "Active";
  const labels: Record<string, string> = {
    prepared: "Prepared",
    starting: "Starting",
    running: "Running",
    stopped: "Stopped",
    failed: "Failed",
    abandoned: "Abandoned",
    cleanup_failed: "Cleanup failed",
    cleaned_up: "Cleaned up",
    unknown: "Unknown",
  };
  return labels[state] ?? state.replace(/_/g, " ");
}

function lifecycleClass(session: SessionRecord): string {
  const state = session.container_lifecycle?.state?.trim().toLowerCase();
  if (state === "running" || state === "starting") return "running";
  if (state === "failed" || state === "cleanup_failed" || state === "abandoned") return "failed";
  if (state) return "idle";
  return session.is_expired ? "failed" : "running";
}

function cleanupSupported(session: SessionRecord): boolean {
  return Boolean(session.container_lifecycle?.actions?.cleanup?.supported);
}

const WAKE_LEDGER_FILTER_OPTIONS: Array<{ value: WakeLedgerFilter; label: string }> = [
  { value: "all", label: "All statuses" },
  { value: "pending", label: "Pending" },
  { value: "due", label: "Due" },
  { value: "dispatching", label: "Dispatching" },
  { value: "queued", label: "Queued" },
  { value: "dispatched", label: "Dispatched" },
  { value: "failed", label: "Failed" },
  { value: "missed_or_late", label: "Missed or late" },
  { value: "completed_or_acknowledged", label: "Completed / acknowledged" },
];

function wakeLedgerStatusLabel(status: string): string {
  const normalized = status.trim().toLowerCase();
  const labels: Record<string, string> = {
    pending: "Pending",
    due: "Due",
    dispatching: "Dispatching",
    queued: "Queued",
    dispatched: "Dispatched",
    failed: "Failed",
    missed_or_late: "Missed / late",
    completed_or_acknowledged: "Completed / acknowledged",
  };
  return labels[normalized] ?? normalized.replace(/_/g, " ");
}

function wakeLedgerDisplayLabel(item: WakeLedgerEntry): string {
  if (item.status === "dispatched" && item.confirmation_state === "awaiting_confirmation") {
    return "Dispatched, awaiting confirmation";
  }
  return wakeLedgerStatusLabel(item.status);
}

function wakeLedgerStatusClass(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (normalized === "failed" || normalized === "missed_or_late") return "failed";
  if (normalized === "dispatching" || normalized === "queued" || normalized === "dispatched") {
    return "running";
  }
  return "idle";
}

function wakeLedgerMatchesFilter(item: WakeLedgerEntry, filter: WakeLedgerFilter): boolean {
  return filter === "all" || item.status === filter;
}

function formatWakeCompletionEvidence(evidence: Record<string, unknown> | undefined): string | null {
  const entries = Object.entries(evidence ?? {}).sort(([left], [right]) => left.localeCompare(right));
  if (entries.length === 0) return null;
  return entries
    .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join(" • ");
}

function tabForSessionId(response: SessionListResponse | null, sessionId: string): TabKey | null {
  if (!response || !sessionId) return null;
  if (response.status_ids.active.includes(sessionId)) {
    return "active";
  }
  if (response.status_ids.expired.includes(sessionId)) {
    return "expired";
  }
  return null;
}

export function SessionsScreen() {
  const { privilegedUnlocked, selectedDirectoryPath, setSelectedDirectoryPath } = useAdminUi();
  const [response, setResponse] = useState<SessionListResponse | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [detail, setDetail] = useState<SessionDetailResponse | null>(null);
  const [catalog, setCatalog] = useState<SessionCatalogResponse | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("active");
  const [selectedSection, setSelectedSection] = useState<"overview" | "access" | "schedules">("overview");
  const [sessionFilter, setSessionFilter] = useState("");
  const [renewExtension, setRenewExtension] = useState("1d");
  const [showRenewModal, setShowRenewModal] = useState(false);
  const [modelDraft, setModelDraft] = useState("");
  const [reasoningEffortDraft, setReasoningEffortDraft] = useState("");
  const [fastModeDraft, setFastModeDraft] = useState("");
  const [modelDirty, setModelDirty] = useState(false);
  const [modelBoundSessionId, setModelBoundSessionId] = useState("");
  const [toolsDraft, setToolsDraft] = useState<string[]>([]);
  const [toolsDirty, setToolsDirty] = useState(false);
  const [toolsBoundSessionId, setToolsBoundSessionId] = useState("");
  const [policyDraft, setPolicyDraft] = useState<SessionPolicyDraft>({
    costCategory: "",
    modelProviderMode: "",
    allowedScriptNames: [],
    connectorGrants: {},
    hostDirectoryGrants: {},
    approvalRequiredTools: [],
    approvalToolSettings: {},
    approvalNotificationTargetIds: [],
    allowedContactIds: null,
    communicationTargetIds: null,
    emailMailboxGrants: null,
    discordChannelAllowlist: [],
    teamsChannelAllowlist: [],
    communicationFileReceiveEnabled: false,
    communicationFileSendEnabled: false,
    communicationFileMaxBytes: "",
    communicationFileAllowedMimeTypes: [],
    communicationFileAllowedExtensions: [],
    communicationFileBlockedExtensions: [],
    communicationFileRequireApprovalForSend: false,
    webSearchEnabled: false,
    metadata: {},
    httpTargets: [],
    httpTargetsSourceError: null,
  });
  const [policyDirty, setPolicyDirty] = useState(false);
  const [policyBoundSessionId, setPolicyBoundSessionId] = useState("");
  const [policyMetadataError, setPolicyMetadataError] = useState<string | null>(null);
  const [metadataDirty, setMetadataDirty] = useState(false);
  const [connectorGrantsError, setConnectorGrantsError] = useState<string | null>(null);
  const [hostDirectoryOptions, setHostDirectoryOptions] = useState<
    AgentCreateHostDirectoryOption[]
  >([]);
  const [httpTargetsError, setHttpTargetsError] = useState<string | null>(null);
  const [httpTargetsDirty, setHttpTargetsDirty] = useState(false);
  const [wakePrompt, setWakePrompt] = useState("");
  const [wakeCron, setWakeCron] = useState("");
  const [wakeResumeAt, setWakeResumeAt] = useState("");
  const [wakeScheduleKind, setWakeScheduleKind] = useState<WakeScheduleKind>("cron");
  const [wakeMode, setWakeMode] = useState("same_context");
  const [wakeEnabled, setWakeEnabled] = useState(true);
  const [editingWakeRequestId, setEditingWakeRequestId] = useState("");
  const [showPastOneTimeWakeSchedules, setShowPastOneTimeWakeSchedules] = useState(false);
  const [wakeVisibilityNowMs, setWakeVisibilityNowMs] = useState(() => Date.now());
  const [wakeLedgerItems, setWakeLedgerItems] = useState<WakeLedgerEntry[]>([]);
  const [wakeLedgerFilter, setWakeLedgerFilter] = useState<WakeLedgerFilter>("all");
  const [wakeLedgerError, setWakeLedgerError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [approvalSettingsTool, setApprovalSettingsTool] = useState("");
  const [gitGrantRepoId, setGitGrantRepoId] = useState("");
  const [gitGrantAccessMode, setGitGrantAccessMode] = useState<"read-only" | "write">("read-only");
  const [gitRepositories, setGitRepositories] = useState<ManagedGitRepository[]>([]);

  async function refreshSessions(): Promise<SessionListResponse> {
    const query = new URLSearchParams({
      include_expired: "true",
      directory_path: selectedDirectoryPath,
      direct_only: "true",
    });
    const payload = await fetchJson<SessionListResponse>(`/api/admin/sessions?${query.toString()}`);
    setResponse(payload);
    return payload;
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [catalogPayload] = await Promise.all([
          fetchJson<SessionCatalogResponse>("/api/admin/sessions/catalog"),
          refreshSessions(),
        ]);
        if (cancelled) return;
        setCatalog(catalogPayload);
        setError(null);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    }

    void load();
    const timer = window.setInterval(() => void refreshSessions().catch(() => {}), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedDirectoryPath]);

  useEffect(() => {
    const timer = window.setInterval(() => setWakeVisibilityNowMs(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!selectedSessionId) {
      setWakeLedgerItems([]);
      setWakeLedgerError(null);
      return;
    }
    let cancelled = false;
    setWakeLedgerItems([]);
    setWakeLedgerError(null);
    fetchJson<{ items: WakeLedgerEntry[] }>(
      `/api/admin/scheduled-wakes?${new URLSearchParams({
        session_id: selectedSessionId,
        limit: "100",
        include_completed: "true",
      }).toString()}`,
    )
      .then((payload) => {
        if (cancelled) return;
        setWakeLedgerItems(payload.items ?? []);
      })
      .catch((reason) => {
        if (cancelled) return;
        setWakeLedgerItems([]);
        setWakeLedgerError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedSessionId]);

  useEffect(() => {
    const directoryPath = detail?.session.directory_path;
    if (!directoryPath) {
      setGitRepositories([]);
      return;
    }
    let cancelled = false;
    const query = new URLSearchParams({
      directory_path: directoryPath,
      include_descendants: "false",
    });
    fetchJson<ManagedGitRepositoryListResponse>(`/api/admin/git-repositories?${query.toString()}`)
      .then((payload) => {
        if (!cancelled) setGitRepositories(payload.repositories ?? []);
      })
      .catch(() => {
        if (!cancelled) setGitRepositories([]);
      });
    return () => {
      cancelled = true;
    };
  }, [detail?.session.directory_path]);

  useEffect(() => {
    const directoryPath = detail?.session.directory_path;
    if (!directoryPath) {
      setHostDirectoryOptions([]);
      return;
    }
    let cancelled = false;
    setHostDirectoryOptions([]);
    void loadAgentCreateOptions(directoryPath)
      .then((options) => {
        if (!cancelled) setHostDirectoryOptions(options.host_directories ?? []);
      })
      .catch(() => {
        if (!cancelled) setHostDirectoryOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [detail?.session.directory_path]);

  useEffect(() => {
    if (!selectedSessionId) {
      setDetail(null);
      setDetailLoading(false);
      setDetailError(null);
      resetWakeEditor();
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetail(null);
    setDetailError(null);
    resetWakeEditor();
    fetchJson<SessionDetailResponse>(`/api/admin/sessions/${encodeURIComponent(selectedSessionId)}`)
      .then((payload) => {
        if (cancelled) return;
        setDetail(payload);
        setDetailLoading(false);
        setDetailError(null);
        const sessionId = payload.session.session_id;
        if (modelBoundSessionId !== sessionId) {
          setModelDraft(payload.session.model ?? "");
          setReasoningEffortDraft(payload.session.reasoning_effort ?? "");
          setFastModeDraft(fastModeSelection(payload.session.fast_mode));
          setModelDirty(false);
          setModelBoundSessionId(sessionId);
        } else if (!modelDirty) {
          setModelDraft(payload.session.model ?? "");
          setReasoningEffortDraft(payload.session.reasoning_effort ?? "");
          setFastModeDraft(fastModeSelection(payload.session.fast_mode));
        }
        if (toolsBoundSessionId !== sessionId) {
          setToolsDraft(sortTools(payload.session.allowed_tools ?? []));
          setToolsDirty(false);
          setToolsBoundSessionId(sessionId);
        } else if (!toolsDirty) {
          setToolsDraft(sortTools(payload.session.allowed_tools ?? []));
        }
        if (policyBoundSessionId !== sessionId) {
          setPolicyDraft(policyDraftFromSession(payload.session));
          setPolicyDirty(false);
          setPolicyMetadataError(null);
          setMetadataDirty(false);
          setConnectorGrantsError(null);
          setHttpTargetsError(null);
          setHttpTargetsDirty(false);
          setPolicyBoundSessionId(sessionId);
        } else if (!policyDirty) {
          setPolicyDraft(policyDraftFromSession(payload.session));
          setPolicyMetadataError(null);
          setMetadataDirty(false);
          setConnectorGrantsError(null);
          setHttpTargetsError(null);
          setHttpTargetsDirty(false);
        }
        const visibleSchedules = filterVisibleWakeSchedules(payload.wake_schedules, {
          showPastOneTime: showPastOneTimeWakeSchedules,
          nowMs: Date.now(),
        });
        if (!editingWakeRequestId || !visibleSchedules.some((item) => item.request_id === editingWakeRequestId)) {
          const firstSchedule = visibleSchedules[0];
          if (firstSchedule) {
            applyWakeScheduleToEditor(firstSchedule);
          } else {
            resetWakeEditor();
          }
        }
      })
      .catch((reason) => {
        if (cancelled) return;
        setDetailLoading(false);
        setDetailError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedSessionId]);

  const visibleItems = useMemo(() => {
    const source = sessionItemsForTab(response, activeTab);
    return source
      .filter((item) => matchesFilter(item, sessionFilter))
      .sort((left, right) => sessionListCollator.compare(left.session_id, right.session_id));
  }, [activeTab, response, sessionFilter]);

  const selectedSessionTab = tabForSessionId(response, selectedSessionId);
  useEffect(() => {
    if (selectedSessionTab) setActiveTab(selectedSessionTab);
  }, [selectedSessionId, selectedSessionTab]);

  const selectedSession = useMemo(() => {
    const listItem = response?.items.find((item) => item.session_id === selectedSessionId) ?? null;
    if (detail?.session.session_id === selectedSessionId) {
      return listItem ? { ...detail.session, ...listItem } : detail.session;
    }
    return listItem;
  }, [detail?.session, response?.items, selectedSessionId]);

  const gitRepositoriesById = useMemo(
    () => new Map(gitRepositories.map((repository) => [repository.managed_repo_id, repository])),
    [gitRepositories],
  );
  const sortedGitGrants = useMemo(
    () => [...(detail?.git_grants ?? [])].sort((left, right) => {
      const leftName = gitRepositoriesById.get(left.managed_repo_id)?.display_name?.trim() || left.managed_repo_id;
      const rightName = gitRepositoriesById.get(right.managed_repo_id)?.display_name?.trim() || right.managed_repo_id;
      return sessionListCollator.compare(leftName, rightName);
    }),
    [detail?.git_grants, gitRepositoriesById],
  );

  const visibleWakeScheduleItems = useMemo(
    () => filterVisibleWakeSchedules(detail?.wake_schedules ?? [], {
      showPastOneTime: showPastOneTimeWakeSchedules,
      nowMs: wakeVisibilityNowMs,
    }),
    [detail?.wake_schedules, showPastOneTimeWakeSchedules, wakeVisibilityNowMs],
  );
  const hiddenPastOneTimeWakeScheduleCount = (detail?.wake_schedules.length ?? 0) - visibleWakeScheduleItems.length;
  const wakeScheduleCountLabel = detailLoading && selectedSessionId
    ? "Loading..."
    : hiddenPastOneTimeWakeScheduleCount > 0 && !showPastOneTimeWakeSchedules
    ? `${visibleWakeScheduleItems.length} shown, ${hiddenPastOneTimeWakeScheduleCount} hidden`
    : `${detail?.wake_schedules.length ?? 0} configured`;

  const selectedWakeSchedule = useMemo(
    () => visibleWakeScheduleItems.find((item) => item.request_id === editingWakeRequestId) ?? null,
    [editingWakeRequestId, visibleWakeScheduleItems],
  );
  const visibleWakeLedgerItems = useMemo(
    () => wakeLedgerItems.filter((item) => wakeLedgerMatchesFilter(item, wakeLedgerFilter)),
    [wakeLedgerFilter, wakeLedgerItems],
  );
  const mutationLocked = !privilegedUnlocked;
  const selectedWakeScheduleIsCancelled = selectedWakeSchedule?.status === "cancelled";
  const wakeEditorLocked = mutationLocked || selectedWakeScheduleIsCancelled;
  const groupedToolOptions = useMemo(() => groupTools(catalog?.tool_options ?? []), [catalog?.tool_options]);
  const approvalSettingsToolOption = useMemo(
    () => catalog?.tool_options.find((tool) => tool.value === approvalSettingsTool) ?? null,
    [approvalSettingsTool, catalog?.tool_options],
  );

  function effectiveRuleForTool(toolValue: string): McpToolPolicyRule | null {
    return selectedSession?.effective_tool_policy?.mcp_tool_policies?.[toolValue] ?? null;
  }

  function inheritedApprovalRequiredForTool(toolValue: string): boolean {
    return isInheritedApprovalRequiredForTool(
      toolValue,
      selectedSession?.effective_tool_policy,
      selectedSession?.approval_required_tools,
    );
  }

  function effectiveApprovalSettingsForTool(toolValue: string): ApprovalGrantSettings {
    return normalizeApprovalSettings(
      selectedSession?.effective_tool_policy?.approval_tool_settings?.[toolValue] ?? DEFAULT_APPROVAL_SETTINGS,
    );
  }

  function approvalSettingsDraftForTool(toolValue: string): ApprovalGrantSettings {
    return normalizeApprovalSettings(policyDraft.approvalToolSettings[toolValue] ?? effectiveApprovalSettingsForTool(toolValue));
  }

  function hasExplicitApprovalSettingsDraft(toolValue: string): boolean {
    return Object.prototype.hasOwnProperty.call(policyDraft.approvalToolSettings, toolValue);
  }

  function updateApprovalSettingsDraft(toolValue: string, settings: ApprovalGrantSettings) {
    setPolicyDraft((current) => ({
      ...current,
      approvalToolSettings: {
        ...current.approvalToolSettings,
        [toolValue]: normalizeApprovalSettings(settings),
      },
    }));
    setPolicyDirty(true);
    setStatus(null);
  }

  function resetApprovalSettingsDraft(toolValue: string) {
    setPolicyDraft((current) => ({
      ...current,
      approvalToolSettings: Object.fromEntries(
        Object.entries(current.approvalToolSettings).filter(([value]) => value !== toolValue),
      ),
    }));
    setPolicyDirty(true);
    setStatus(null);
  }

  function openApprovalSettings(toolValue: string) {
    setApprovalSettingsTool(toolValue);
  }

  function resetWakeEditor() {
    setEditingWakeRequestId("");
    setWakePrompt("");
    setWakeCron("");
    setWakeResumeAt("");
    setWakeScheduleKind("cron");
    setWakeMode("same_context");
    setWakeEnabled(true);
  }

  function applyWakeScheduleToEditor(item: WakeSchedule) {
    setEditingWakeRequestId(item.request_id);
    setWakePrompt(item.prompt);
    setWakeCron(item.cron ?? "");
    setWakeResumeAt(toDatetimeLocalValue(item.resume_at));
    setWakeScheduleKind(item.schedule_kind === "resume_at" ? "resume_at" : "cron");
    setWakeMode(item.resume_mode);
    setWakeEnabled(item.enabled);
  }

  function setScheduleKind(kind: WakeScheduleKind) {
    setWakeScheduleKind(kind);
    if (kind === "cron") {
      setWakeResumeAt("");
    } else {
      setWakeCron("");
    }
  }

  function bindWakeSchedule(requestId: string, schedules = detail?.wake_schedules ?? []) {
    const item = schedules.find((schedule) => schedule.request_id === requestId);
    if (!item) return;
    applyWakeScheduleToEditor(item);
  }

  async function refreshSelectedSessionDetail(sessionId: string) {
    const payload = await fetchJson<SessionDetailResponse>(`/api/admin/sessions/${encodeURIComponent(sessionId)}`);
    setDetail(payload);
    return payload;
  }

  async function saveGitGrant(clone: boolean) {
    if (!selectedSession || !gitGrantRepoId.trim()) return;
    const repoId = gitGrantRepoId.trim();
    setBusy(`${clone ? "git-clone" : "git-grant"}:${repoId}`);
    setError(null);
    setStatus(null);
    try {
      await fetchJson(
        `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/git-grants/${encodeURIComponent(repoId)}${clone ? "/clone" : ""}`,
        {
          method: clone ? "POST" : "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ access_mode: gitGrantAccessMode, lifetime: "session" }),
        },
      );
      await refreshSelectedSessionDetail(selectedSession.session_id);
      setGitGrantRepoId("");
      setStatus(
        clone
          ? `Cloned ${repoId} with persistent ${gitGrantAccessMode} access.`
          : `Granted ${gitGrantAccessMode} access to ${repoId}.`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function revokeGitGrant(grant: ManagedGitGrant) {
    if (!selectedSession) return;
    if (!window.confirm(`Revoke ${selectedSession.session_id}'s access to ${grant.managed_repo_id}? The checkout will be retained.`)) return;
    setBusy(`git-revoke:${grant.managed_repo_id}`);
    setError(null);
    setStatus(null);
    try {
      await fetchJson(
        `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/git-grants/${encodeURIComponent(grant.managed_repo_id)}`,
        { method: "DELETE" },
      );
      await refreshSelectedSessionDetail(selectedSession.session_id);
      setStatus(`Revoked repository access for ${grant.managed_repo_id}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function refreshSelectedSessionLedger(sessionId: string) {
    const payload = await fetchJson<{ items: WakeLedgerEntry[] }>(
      `/api/admin/scheduled-wakes?${new URLSearchParams({
        session_id: sessionId,
        limit: "100",
        include_completed: "true",
      }).toString()}`,
    );
    setWakeLedgerItems(payload.items ?? []);
    setWakeLedgerError(null);
    return payload.items ?? [];
  }

  async function runSessionAction(action: "renew" | "revoke" | "delete" | "cleanup") {
    if (!selectedSession) return;
    if (action === "renew" && !renewExtension.trim()) {
      setError("Renew extension is required.");
      return;
    }
    if (action === "delete" && !window.confirm(`Delete session ${selectedSession.session_id}? This cannot be undone.`)) {
      return;
    }
    if (action === "cleanup" && !window.confirm(`Clean up worker state for ${selectedSession.session_id}? Session history will remain visible.`)) {
      return;
    }
    if (action === "revoke" && !window.confirm(`Revoke session ${selectedSession.session_id}?`)) {
      return;
    }
    setBusy(action);
    try {
      const payload = await fetchJson<{ message: string }>(
        `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}${action === "delete" ? "" : `/${action}`}`,
        {
          method: action === "delete" ? "DELETE" : "POST",
          body: action === "renew" ? JSON.stringify({ extension: renewExtension.trim() }) : undefined,
        },
      );
      setStatus(payload.message);
      setError(null);
      if (action === "renew") {
        setShowRenewModal(false);
      }
      const refreshedSessions = await refreshSessions();
      const nextSelectedSessionId = action !== "delete" && refreshedSessions.items.some(
        (item) => item.session_id === selectedSession.session_id,
      ) ? selectedSession.session_id : "";
      if (action === "delete") {
        setSelectedSessionId("");
        if (!nextSelectedSessionId) {
          setDetail(null);
          setDetailError(null);
        }
      } else if (nextSelectedSessionId) {
        await refreshSelectedSessionDetail(nextSelectedSessionId);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  function toggleTool(toolValue: string) {
    const wasEnabled = toolsDraft.includes(toolValue);
    setToolsDirty(true);
    setStatus(null);
    setToolsDraft((current) => {
      const next = current.includes(toolValue) ? current.filter((value) => value !== toolValue) : [...current, toolValue];
      return sortTools(next);
    });
    if (wasEnabled) {
      const hadGate = policyDraft.approvalRequiredTools.includes(toolValue);
      const hadSettings = Boolean(policyDraft.approvalToolSettings[toolValue]);
      if (hadGate || hadSettings) {
        setPolicyDraft((current) => ({
          ...current,
          approvalRequiredTools: current.approvalRequiredTools.filter((value) => value !== toolValue),
          approvalToolSettings: Object.fromEntries(
            Object.entries(current.approvalToolSettings).filter(([value]) => value !== toolValue),
          ),
        }));
        setPolicyDirty(true);
        setStatus(null);
      }
    }
  }

  function updatePolicyDraft(update: Partial<SessionPolicyDraft>) {
    setPolicyDraft((current) => ({ ...current, ...update }));
    setPolicyDirty(true);
    setStatus(null);
  }

  function updateHostDirectoryGrants(nextGrants: Record<string, string[]>) {
    updatePolicyDraft({ hostDirectoryGrants: nextGrants });
    const requiredTools = hostDirectoryToolsForGrants(nextGrants);
    if (requiredTools.some((toolName) => !toolsDraft.includes(toolName))) {
      setToolsDraft((current) => sortTools([...current, ...requiredTools]));
      setToolsDirty(true);
    }
  }

  function openMailboxConfiguration() {
    if (!selectedSession) return;
    if (
      (modelDirty || toolsDirty || policyDirty)
      && !window.confirm("Open Configuration and discard unsaved session changes?")
    ) {
      return;
    }
    setSelectedDirectoryPath(selectedSession.directory_path);
    window.location.hash = "configuration";
  }

  function convertLegacyCommunicationPolicy() {
    if (!selectedSession) return;
    const next: Partial<SessionPolicyDraft> = {};
    if (policyDraft.communicationTargetIds === null) {
      next.communicationTargetIds = dedupeStrings([
        ...(selectedSession.discord_channel_allowlist ?? []),
        ...(selectedSession.teams_channel_allowlist ?? []),
        ...(selectedSession.teams_chat_allowlist ?? []),
        ...(selectedSession.teams_user_allowlist ?? []),
      ]);
    }
    if (policyDraft.emailMailboxGrants === null) {
      next.emailMailboxGrants = (selectedSession.email_mailbox_allowlist ?? []).map((mailboxId) => {
        const configured = (selectedSession.email_mailbox_options ?? []).find(
          (option) => option.mailbox_id === mailboxId,
        );
        const folders = dedupeStrings(
          selectedSession.email_folder_allowlist_by_mailbox?.[mailboxId]
            ?? selectedSession.email_folder_allowlist
            ?? configured?.default_folders
            ?? [],
        );
        return {
          mailbox_id: mailboxId,
          access: legacyEmailMailboxAccess({
            allowedTools: toolsDraft,
            folders,
            emailSendEnabled: Boolean(selectedSession.email_send_enabled),
          }),
          folders,
        };
      });
    }
    updatePolicyDraft(next);
  }

  function updateApprovalNotificationTargets(nextTargets: string[]) {
    updatePolicyDraft({ approvalNotificationTargetIds: dedupeStrings(nextTargets) });
  }

  function sessionPolicyValidationError(): string | null {
    return connectorGrantsError
      ?? httpTargetsError
      ?? policyMetadataError
      ?? validateLeadingDotExtensions(policyDraft.communicationFileAllowedExtensions, "Allowed extensions")
      ?? validateLeadingDotExtensions(policyDraft.communicationFileBlockedExtensions, "Blocked extensions");
  }

  function renderApprovalNotificationTargetEditor() {
    const knownTargets = selectedSession?.approval_notification_target_options ?? [];
    const entries = policyDraft.approvalNotificationTargetIds;
    const nextAvailableTarget = knownTargets.find((option) => !entries.includes(option.value)) ?? null;
    const addNotificationTargetDisabledReason = mutationLocked
      ? "Unlock privileged operations to edit approval notification targets."
      : knownTargets.length === 0
        ? "No notify-capable communication targets are available for this session."
        : nextAvailableTarget == null
          ? "All notify-capable targets are already selected."
          : null;

    const addNotificationTargetButton = (
      <button
        type="button"
        onClick={() => {
          if (!nextAvailableTarget) {
            return;
          }
          updateApprovalNotificationTargets([...entries, nextAvailableTarget.value]);
        }}
        disabled={mutationLocked || !nextAvailableTarget}
      >
        Add notification target
      </button>
    );

    return (
      <div className="string-list-selector">
        <div className="contacts-editor-header">
          <p className="muted-text">
            Select notify-capable communication targets from the selected session&apos;s directory.
          </p>
          <div className="inline-actions contacts-editor-actions">
            {addNotificationTargetDisabledReason ? (
              <Tooltip
                content={addNotificationTargetDisabledReason}
                focusable
                ariaLabel={addNotificationTargetDisabledReason}
              >
                {addNotificationTargetButton}
              </Tooltip>
            ) : (
              addNotificationTargetButton
            )}
          </div>
        </div>
        {entries.length === 0 ? (
          <div className="empty-state">
            {knownTargets.length
              ? "No approval notification targets selected."
              : "No notify-capable communication targets are currently available for this session."}
          </div>
        ) : null}
        {entries.map((entry, index) => {
          const rowOptions = dedupeStrings([
            entry,
            ...knownTargets
              .map((option) => option.value)
              .filter((option) => option === entry || !entries.includes(option)),
          ]);
          const knownTarget = knownTargets.find((option) => option.value === entry) ?? null;
          const isUnknownTarget = knownTarget == null;
          return (
            <div key={`approval-notification-target-${index}-${entry}`} className="string-list-selector-row">
              <label className="field-stack">
                <span>Notification target</span>
                <select
                  value={entry}
                  onChange={(event) => {
                    const nextEntries = [...entries];
                    nextEntries[index] = event.target.value;
                    updateApprovalNotificationTargets(nextEntries);
                  }}
                  disabled={mutationLocked}
                >
                  {rowOptions.map((option) => {
                    const optionLabel =
                      knownTargets.find((knownOption) => knownOption.value === option) ??
                      null;
                    return (
                      <option key={option} value={option}>
                        {option === entry && isUnknownTarget
                          ? `${option} (unknown target)`
                          : optionLabel
                            ? formatNotificationTargetOptionLabel(optionLabel)
                            : option}
                      </option>
                    );
                  })}
                </select>
              </label>
              <div className="inline-actions compact-actions">
                <button
                  type="button"
                  className="danger"
                  onClick={() =>
                    updateApprovalNotificationTargets(
                      entries.filter((_, itemIndex) => itemIndex !== index),
                    )
                  }
                  disabled={mutationLocked}
                >
                  Remove
                </button>
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  function toggleApprovalRequirement(toolValue: string) {
    if (!toolsDraft.includes(toolValue)) {
      return;
    }
    setPolicyDraft((current) => {
      const isEnabled = current.approvalRequiredTools.includes(toolValue);
      const nextTools = isEnabled
        ? current.approvalRequiredTools.filter((value) => value !== toolValue)
        : [...current.approvalRequiredTools, toolValue];
      const nextSettings = isEnabled
        ? Object.fromEntries(
            Object.entries(current.approvalToolSettings).filter(([value]) => value !== toolValue),
          )
        : current.approvalToolSettings;
      return {
        ...current,
        approvalRequiredTools: sortTools(nextTools),
        approvalToolSettings: nextSettings,
      };
    });
    setPolicyDirty(true);
    setStatus(null);
  }

  function policyApprovalToolSettingsPayload(): Record<string, ApprovalGrantSettings> {
    const enabledTools = new Set(toolsDraft);
    const result: Record<string, ApprovalGrantSettings> = {};
    for (const [toolName, settings] of Object.entries(policyDraft.approvalToolSettings)) {
      if (!enabledTools.has(toolName)) continue;
      result[toolName] = normalizeApprovalSettings(settings);
    }
    return result;
  }

  async function saveSessionSettings() {
    if (!selectedSession || (!modelDirty && !toolsDirty && !policyDirty)) return;
    if (modelDirty && modelDraft !== (selectedSession.model ?? "") && !modelDraft.trim()) {
      setError("Model is required.");
      setStatus(null);
      return;
    }
    if (policyDirty) {
      const validationError = sessionPolicyValidationError();
      if (validationError) {
        setError(validationError);
        return;
      }
    }

    setBusy("settings");
    try {
      const savedSections: Array<"model" | "tools" | "policy"> = [];

      if (modelDirty) {
        await fetchJson<SessionMutationResponse>(
          `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/model`,
          {
            method: "PUT",
            body: JSON.stringify({
              ...(modelDraft.trim() ? { model: modelDraft.trim() } : {}),
              reasoning_effort: reasoningEffortDraft || null,
              fast_mode: fastModeValue(fastModeDraft),
            }),
          },
        );
        setModelDirty(false);
        setModelBoundSessionId(selectedSession.session_id);
        savedSections.push("model");
      }

      if (toolsDirty) {
        await fetchJson<SessionMutationResponse>(
          `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/allowed-tools`,
          {
            method: "PUT",
            body: JSON.stringify({ allowed_tools: toolsDraft }),
          },
        );
        setToolsDirty(false);
        setToolsBoundSessionId(selectedSession.session_id);
        savedSections.push("tools");
      }

      if (policyDirty) {
        await fetchJson<SessionMutationResponse>(
          `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}`,
          {
            method: "PATCH",
            body: JSON.stringify({
              cost_category: policyDraft.costCategory.trim() || null,
              allowed_script_names: policyDraft.allowedScriptNames,
              model_provider_mode: policyDraft.modelProviderMode || null,
              allowed_contact_ids: policyDraft.allowedContactIds,
              communication_target_ids: policyDraft.communicationTargetIds,
              email_mailbox_grants: policyDraft.emailMailboxGrants,
              discord_channel_allowlist: policyDraft.discordChannelAllowlist,
              teams_channel_allowlist: policyDraft.teamsChannelAllowlist,
              communication_file_receive_enabled: policyDraft.communicationFileReceiveEnabled,
              communication_file_send_enabled: policyDraft.communicationFileSendEnabled,
              communication_file_max_bytes: parseNullableNonNegativeInteger(
                policyDraft.communicationFileMaxBytes,
                "Communication file max bytes",
              ),
              communication_file_allowed_mime_types: policyDraft.communicationFileAllowedMimeTypes,
              communication_file_allowed_extensions: policyDraft.communicationFileAllowedExtensions,
              communication_file_blocked_extensions: policyDraft.communicationFileBlockedExtensions,
              communication_file_require_approval_for_send: policyDraft.communicationFileRequireApprovalForSend,
              approval_notification_target_ids: policyDraft.approvalNotificationTargetIds,
              web_search_enabled: policyDraft.webSearchEnabled,
              approval_required_tools: sortTools(policyDraft.approvalRequiredTools),
              approval_tool_settings: policyApprovalToolSettingsPayload(),
              connector_grants: policyDraft.connectorGrants,
              host_directory_grants: policyDraft.hostDirectoryGrants,
              ...(metadataDirty ? { metadata: policyDraft.metadata } : {}),
              ...(httpTargetsDirty
                ? { http_targets: httpTargetsPayload(policyDraft.httpTargets) }
                : {}),
            }),
          },
        );
        setPolicyDirty(false);
        setPolicyMetadataError(null);
        setMetadataDirty(false);
        setConnectorGrantsError(null);
        setHttpTargetsError(null);
        setHttpTargetsDirty(false);
        setPolicyBoundSessionId(selectedSession.session_id);
        savedSections.push("policy");
      }

      if (savedSections.length > 0) {
        const [firstSection, ...restSections] = savedSections;
        const firstLabel = `${firstSection.charAt(0).toUpperCase()}${firstSection.slice(1)}`;
        const statusMessage = restSections.length === 0
          ? `${firstLabel} saved.`
          : restSections.length === 1
            ? `${firstLabel} and ${restSections[0]} saved.`
            : `${firstLabel}, ${restSections.slice(0, -1).join(", ")}, and ${restSections[restSections.length - 1]} saved.`;
        setStatus(statusMessage);
      }

      setError(null);
      await refreshSessions();
      await refreshSelectedSessionDetail(selectedSession.session_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function submitWakeSchedule(mode: "create" | "update") {
    if (!selectedSession) return;
    if (!wakePrompt.trim()) {
      setError("Wake prompt is required.");
      return;
    }
    if (wakeScheduleKind === "cron" && !wakeCron.trim()) {
      setError("Cron expression is required for recurring schedules.");
      return;
    }
    if (wakeScheduleKind === "resume_at" && !wakeResumeAt.trim()) {
      setError("Resume timestamp is required for one-time schedules.");
      return;
    }
    setBusy(mode === "create" ? "wake-create" : "wake-update");
    try {
      const path = mode === "create"
        ? `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/wake-schedules`
        : `/api/admin/sessions/wake-schedules/${encodeURIComponent(editingWakeRequestId)}`;
      const payload = await fetchJson<{ item: WakeSchedule }>(path, {
        method: mode === "create" ? "POST" : "PATCH",
        body: JSON.stringify({
          prompt: wakePrompt.trim(),
          cron: wakeScheduleKind === "cron" ? wakeCron.trim() || null : null,
          resume_at: wakeScheduleKind === "resume_at" && wakeResumeAt ? new Date(wakeResumeAt).toISOString() : null,
          resume_mode: wakeMode,
          enabled: wakeEnabled,
        }),
      });
      setStatus(
        mode === "create"
          ? `Created wake schedule for ${selectedSession.session_id}.`
          : `Updated wake schedule ${payload.item.request_id}.`,
      );
      setError(null);
      const refreshed = await refreshSelectedSessionDetail(selectedSession.session_id);
      bindWakeSchedule(payload.item.request_id, refreshed.wake_schedules);
      await refreshSelectedSessionLedger(selectedSession.session_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function toggleWakeScheduleEnabled(item: WakeSchedule) {
    setBusy(`wake-toggle-${item.request_id}`);
    try {
      await fetchJson<{ item: WakeSchedule }>(`/api/admin/sessions/wake-schedules/${encodeURIComponent(item.request_id)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !item.enabled }),
      });
      setStatus(`${!item.enabled ? "Enabled" : "Disabled"} wake schedule ${item.request_id}.`);
      setError(null);
      const refreshed = await refreshSelectedSessionDetail(item.session_id);
      if (editingWakeRequestId === item.request_id) {
        const updated = refreshed.wake_schedules.find((schedule) => schedule.request_id === item.request_id);
        setWakeEnabled(updated?.enabled ?? !item.enabled);
      }
      await refreshSelectedSessionLedger(item.session_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function deleteWakeSchedule(item: WakeSchedule) {
    if (!window.confirm(`Delete wake schedule ${item.request_id}?`)) {
      return;
    }
    setBusy(`wake-delete-${item.request_id}`);
    try {
      await fetchJson<{ ok: boolean }>(`/api/admin/sessions/wake-schedules/${encodeURIComponent(item.request_id)}`, {
        method: "DELETE",
      });
      setStatus(`Deleted wake schedule ${item.request_id}.`);
      setError(null);
      const refreshed = await refreshSelectedSessionDetail(item.session_id);
      setDetail(refreshed);
      await refreshSelectedSessionLedger(item.session_id);
      resetWakeEditor();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function retryWakeLedgerEntry(item: WakeLedgerEntry) {
    setBusy(`wake-ledger-retry-${item.request_id}`);
    try {
      const payload = await fetchJson<{ item: WakeLedgerEntry }>(
        `/api/admin/scheduled-wakes/${encodeURIComponent(item.request_id)}/retry`,
        {
          method: "POST",
          body: JSON.stringify({ run_now: true }),
        },
      );
      setStatus(`Retried wake schedule ${payload.item.request_id}.`);
      setError(null);
      await refreshSelectedSessionDetail(item.session_id);
      await refreshSelectedSessionLedger(item.session_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  function selectSession(sessionId: string) {
    if (sessionId !== selectedSessionId && (modelDirty || toolsDirty || policyDirty)
      && !window.confirm("Discard unsaved session changes and select another session?")) return false;
    if (sessionId !== selectedSessionId) {
      setModelDirty(false);
      setToolsDirty(false);
      setPolicyDirty(false);
    }
    setSelectedSessionId(sessionId);
    const tab = tabForSessionId(response, sessionId);
    if (tab) setActiveTab(tab);
  }

  return (
    <RecordWorkspace
      records={(response?.items ?? []).map((item) => ({
        id: item.session_id,
        href: `#sessions?${new URLSearchParams({ session_id: item.session_id })}`,
      }))}
      selectedId={selectedSessionId}
      onSelect={selectSession}
      recordRouteKeys="session_id"
      allowUnlistedSelection
    >
      <div className="feature-page sessions-page session-record">
        <section className="feature-sidebar">
          <div className="feature-sidebar-header">
            <div>
              <div className="eyebrow">Session control</div>
              <h1>Sessions</h1>
            </div>
          </div>
          {mutationLocked ? (
            <div className="callout warning-callout">
              Unlock privileged operations to renew, revoke, edit, or schedule sessions.
            </div>
          ) : null}
          <label className="field-stack">
            <span>Find session</span>
            <input
              type="search"
              value={sessionFilter}
              onChange={(event) => setSessionFilter(event.target.value)}
              placeholder="session, VM, issue, model"
            />
          </label>
          <div className="toolbar-grid sessions-filter-grid">
            <label className="field-stack">
              <span>State</span>
              <select value={activeTab} onChange={(event) => setActiveTab(event.target.value as TabKey)}>
                <option value="active">Active ({response?.counts.active ?? 0})</option>
                <option value="expired">Expired ({response?.counts.expired ?? 0})</option>
              </select>
            </label>
          </div>
          <div className="feature-list session-list">
            {visibleItems.map((item) => (
              <button
                key={item.session_id}
                type="button"
                className={`feature-list-item session-list-item${item.session_id === selectedSessionId ? " selected" : ""}`}
                onClick={() => selectSession(item.session_id)}
              >
                <div className="feature-list-title-row session-list-item-row">
                  <Tooltip content={`Session id: ${item.session_id}`}>
                    <strong>{item.session_id}</strong>
                  </Tooltip>
                  <span className={`status-pill session-state-pill state-${lifecycleClass(item)}`}>
                    {lifecycleLabel(item)}
                  </span>
                </div>
                {item.vm_id || item.issue_number || item.model ? (
                  <div className="feature-list-meta">
                    {[item.vm_id, item.issue_number ? `Issue ${item.issue_number}` : null, item.model].filter(Boolean).join(" · ")}
                  </div>
                ) : null}
                {item.container_lifecycle?.last_signal_at ? (
                  <div className="feature-list-meta">
                    Last signal {formatTimestamp(item.container_lifecycle.last_signal_at)}
                  </div>
                ) : null}
              </button>
            ))}
            {!response && !error ? <div className="empty-state" role="status">Loading sessions...</div> : null}
            {response && visibleItems.length === 0 ? (
              <div className="empty-state">No sessions matched the current view.</div>
            ) : null}
          </div>
        </section>
        <section className="feature-content">
          <div className="feature-toolbar" hidden={!selectedSession}>
            <div>
              <h2>{selectedSession?.session_id ?? "Select a session"}</h2>
              <p className="muted-text">
                {selectedSession
                  ? "Manage expiry, model and tool access, then inspect wake schedules and the live session summary."
                  : "Choose a session from the left to inspect and manage it."}
              </p>
            </div>
            {selectedSession ? (
              <div className="feature-toolbar-actions icon-actions">
                <Tooltip
                  content={
                    selectedSession.is_expired
                      ? "This session has expired and can no longer be resumed until it is renewed."
                      : "This session is active and can still be used by its guest agent."
                  }
                >
                  <span className={`status-pill state-${lifecycleClass(selectedSession)}`}>
                    {lifecycleLabel(selectedSession)}
                  </span>
                </Tooltip>
                <Tooltip
                  content={
                    selectedSession.vm_id
                      ? `Attached VM: ${selectedSession.vm_id}`
                      : "This session is not currently attached to a VM."
                  }
                >
                  <span className="panel-meta">{selectedSession.vm_id ?? "No VM"}</span>
                </Tooltip>
                <Tooltip content="Extend the session expiry without changing the current session identity or attached VM.">
                  <button
                    type="button"
                    className="primary"
                    onClick={() => setShowRenewModal(true)}
                    disabled={busy === "renew" || mutationLocked}
                    aria-label="Extend session"
                  >
                    <ExtendIcon />
                  </button>
                </Tooltip>
                {cleanupSupported(selectedSession) ? (
                  <Tooltip
                    content={
                      selectedSession.container_lifecycle?.actions?.cleanup?.reason ??
                      "Remove worker-side container state while keeping the session record visible for audit."
                    }
                  >
                    <button
                      type="button"
                      className="danger"
                      onClick={() => void runSessionAction("cleanup")}
                      disabled={busy === "cleanup" || mutationLocked}
                      aria-label={busy === "cleanup" ? "Cleaning up session" : "Clean up session"}
                    >
                      {busy === "cleanup" ? "…" : <CleanupIcon />}
                    </button>
                  </Tooltip>
                ) : null}
                <Tooltip content="Revoke the session so further guest access is denied even before the expiry time.">
                  <button
                    type="button"
                    onClick={() => void runSessionAction("revoke")}
                    disabled={busy === "revoke" || mutationLocked}
                    aria-label={busy === "revoke" ? "Revoking session" : "Revoke session"}
                  >
                    {busy === "revoke" ? "…" : <RevokeIcon />}
                  </button>
                </Tooltip>
                <Tooltip content="Delete the session record and remove its saved local state from the host control plane.">
                  <button
                    className="danger"
                    type="button"
                    onClick={() => void runSessionAction("delete")}
                    disabled={busy === "delete" || mutationLocked}
                    aria-label={busy === "delete" ? "Deleting session" : "Delete session"}
                  >
                    {busy === "delete" ? "…" : <DeleteIcon />}
                  </button>
                </Tooltip>
              </div>
            ) : null}
          </div>

          {error ? <div className="callout error-callout">{error}</div> : null}
          {detailError ? <div className="callout error-callout" role="alert">{detailError}</div> : null}
          {status ? <div className="callout success-callout">{status}</div> : null}
          {selectedSession ? (
            <div className="stack-layout">
              <nav className="inline-actions record-section-nav" aria-label="Session sections">
                <button type="button" className={selectedSection === "overview" ? "primary" : ""}
                  aria-pressed={selectedSection === "overview"} onClick={() => setSelectedSection("overview")}>
                  Overview
                </button>
                <button type="button" className={selectedSection === "access" ? "primary" : ""}
                  aria-pressed={selectedSection === "access"} onClick={() => setSelectedSection("access")}>
                  Model and access{modelDirty || toolsDirty || policyDirty ? " · Unsaved" : ""}
                </button>
                <button type="button" className={selectedSection === "schedules" ? "primary" : ""}
                  aria-pressed={selectedSection === "schedules"} onClick={() => setSelectedSection("schedules")}>
                  Wake schedules{visibleWakeScheduleItems.length ? ` (${visibleWakeScheduleItems.length})` : ""}
                </button>
              </nav>
              <article className="panel" hidden={selectedSection !== "overview"}>
                <div className="panel-header">
                  <h3>Session overview</h3>
                </div>
                <div className="panel-body">
                  <dl className="property-grid">
                    <div>
                      <dt>VM</dt>
                      <dd>{selectedSession.vm_id ? <a href={`#vms?${new URLSearchParams({ vm_name: selectedSession.vm_id })}`}>{selectedSession.vm_id}</a> : "None"}</dd>
                    </div>
                    <div>
                      <dt>Issue</dt>
                      <dd>{selectedSession.issue_number ?? "None"}</dd>
                    </div>
                    <div>
                      <dt>Directory</dt>
                      <dd>{selectedSession.directory_path}</dd>
                    </div>
                    <div>
                      <dt>Model</dt>
                      <dd>{selectedSession.model ?? "Unset"}</dd>
                      <dt>Cost category</dt>
                      <dd>{selectedSession.cost_category ?? "Uncategorized"}</dd>
                      <dt>Reasoning effort</dt>
                      <dd>{selectedSession.reasoning_effort ?? (selectedSession.agent_harness === "codex" ? "Default (high)" : "Provider default")}</dd>
                      <dt>Default speed</dt>
                      <dd>{selectedSession.fast_mode ? "Fast" : "Standard"}</dd>
                    </div>
                    {selectedSession.runtime_image ? (
                      selectedSession.runtime_image.backend === "container_worker_vm" ? (
                        <>
                          <div>
                            <dt>Container image</dt>
                            <dd>
                              <Tooltip content={runtimeImageTooltip(selectedSession)}>
                                <span>{runtimeImagePrimaryText(selectedSession)}</span>
                              </Tooltip>
                            </dd>
                          </div>
                          {selectedSession.runtime_image.image_alias ? (
                            <div>
                              <dt>Container image alias</dt>
                              <dd>{selectedSession.runtime_image.image_alias}</dd>
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <>
                          <div>
                            <dt>VM template</dt>
                            <dd>
                              <Tooltip content={runtimeImageTooltip(selectedSession)}>
                                <span>{runtimeImagePrimaryText(selectedSession)}</span>
                              </Tooltip>
                            </dd>
                          </div>
                          <div>
                            <dt>VM image ref</dt>
                            <dd>
                              <Tooltip content={runtimeImageTooltip(selectedSession)}>
                                <span>{selectedSession.runtime_image.image_ref ?? "Unavailable"}</span>
                              </Tooltip>
                            </dd>
                          </div>
                        </>
                      )
                    ) : null}
                    <div>
                      <dt>Created</dt>
                      <dd>{formatTimestamp(selectedSession.created_at)}</dd>
                    </div>
                    <div>
                      <dt>Expires</dt>
                      <dd>{formatTimestamp(selectedSession.expires_at)}</dd>
                    </div>
                  </dl>
                </div>
              </article>
              <details className="panel compact-disclosure session-repository-access" hidden={selectedSection !== "access"}>
                  <summary className="compact-disclosure-summary feature-list-title-row">
                    <h3>Repository access</h3>
                    <span className="panel-meta">{(detail?.git_grants ?? []).length} grants</span>
                  </summary>
                  <div className="panel-body stack-layout">
                    {sortedGitGrants.length > 0 ? (
                      <div className="session-git-grant-table-wrap">
                        <table className="session-git-grant-table">
                          <thead>
                            <tr>
                              <th scope="col">Repository ID</th>
                              <th scope="col">Name</th>
                              <th scope="col">URL</th>
                              <th scope="col">Access</th>
                              <th scope="col">
                                <span className="sr-only">Actions</span>
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {sortedGitGrants.map((grant) => {
                              const repository = gitRepositoriesById.get(grant.managed_repo_id);
                              const displayName = repository?.display_name?.trim();
                              const canonicalUrl = repository?.canonical_repository_url?.trim();
                              return (
                                <tr key={grant.managed_repo_id}>
                                  <td>
                                    <code>{grant.managed_repo_id}</code>
                                  </td>
                                  <td>{displayName || "Unavailable"}</td>
                                  <td className="session-git-grant-url">{canonicalUrl || "Unavailable"}</td>
                                  <td>
                                    {grant.access_mode} · {grant.lifetime} · {grant.execution_backend ?? "agent"}
                                  </td>
                                  <td className="session-git-grant-action">
                                    <button
                                      type="button"
                                      className="admin-shell-button admin-shell-button-muted"
                                      onClick={() => void revokeGitGrant(grant)}
                                      disabled={mutationLocked || busy === `git-revoke:${grant.managed_repo_id}`}
                                    >
                                      {busy === `git-revoke:${grant.managed_repo_id}` ? "Revoking…" : "Revoke"}
                                    </button>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <div className="empty-state">No managed repository grants.</div>
                    )}
                    <div className="feature-toolbar">
                      <label className="field-stack">
                        <span>Managed repository ID</span>
                        <select
                          value={gitGrantRepoId}
                          onChange={(event) => setGitGrantRepoId(event.target.value)}
                          disabled={mutationLocked}
                        >
                          <option value="">Select a repository</option>
                          {gitRepositories.map((repository) => (
                            <option key={repository.managed_repo_id} value={repository.managed_repo_id}>
                              {repository.display_name
                                ? `${repository.display_name} (${repository.managed_repo_id})`
                                : repository.managed_repo_id}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field-stack">
                        <span>Access</span>
                        <select
                          value={gitGrantAccessMode}
                          onChange={(event) => setGitGrantAccessMode(event.target.value as "read-only" | "write")}
                          disabled={mutationLocked}
                        >
                          <option value="read-only">Read only</option>
                          <option value="write">Write</option>
                        </select>
                      </label>
                      <button
                        type="button"
                        className="admin-shell-button"
                        onClick={() => void saveGitGrant(false)}
                        disabled={mutationLocked || !gitGrantRepoId.trim() || busy?.startsWith("git-")}
                      >
                        Grant access
                      </button>
                      <button
                        type="button"
                        className="admin-shell-button"
                        onClick={() => void saveGitGrant(true)}
                        disabled={mutationLocked || !gitGrantRepoId.trim() || busy?.startsWith("git-")}
                      >
                        Grant and clone
                      </button>
                    </div>
                    <p className="panel-meta">
                      Read-only session grants support repeated clone, fetch, and pull. Revocation leaves existing
                      checkout files in place.
                    </p>
                  </div>
              </details>
              {selectedSession.container_lifecycle ? (
                <article className="panel" hidden={selectedSection !== "overview"}>
                  <div className="panel-header">
                    <h3>Container lifecycle</h3>
                    <span className={`status-pill state-${lifecycleClass(selectedSession)}`}>
                      {lifecycleLabel(selectedSession)}
                    </span>
                  </div>
                  <div className="panel-body">
                    {selectedSession.container_lifecycle.cleanup_error ? (
                      <div className="callout error-callout">{selectedSession.container_lifecycle.cleanup_error}</div>
                    ) : null}
                    <dl className="property-grid">
                      <div>
                        <dt>Last signal</dt>
                        <dd>{formatTimestamp(selectedSession.container_lifecycle.last_signal_at)}</dd>
                      </div>
                      <div>
                        <dt>Last heartbeat</dt>
                        <dd>{formatTimestamp(selectedSession.container_lifecycle.last_heartbeat_at)}</dd>
                      </div>
                      <div>
                        <dt>Last completion</dt>
                        <dd>{formatTimestamp(selectedSession.container_lifecycle.last_completion_at)}</dd>
                      </div>
                      <div>
                        <dt>Cleanup attempted</dt>
                        <dd>{formatTimestamp(selectedSession.container_lifecycle.cleanup_attempted_at)}</dd>
                      </div>
                      <div>
                        <dt>Cleanup completed</dt>
                        <dd>{formatTimestamp(selectedSession.container_lifecycle.cleanup_completed_at)}</dd>
                      </div>
                      <div>
                        <dt>Recovery</dt>
                        <dd>
                          {selectedSession.container_lifecycle.actions?.recovery?.supported
                            ? (selectedSession.container_lifecycle.actions.recovery.label ?? "Available")
                            : (selectedSession.container_lifecycle.actions?.recovery?.reason ?? "Unavailable")}
                        </dd>
                      </div>
                    </dl>
                  </div>
                </article>
              ) : null}
              <div className="stack-layout">
                <article className="panel" hidden={selectedSection !== "access"}>
                  <div className="panel-header">
                    <h3>Model and access</h3>
                    <div className="feature-toolbar-actions session-panel-actions">
                      <button
                        type="button"
                        className="primary"
                        onClick={() => void saveSessionSettings()}
                        disabled={
                          busy === "settings" || mutationLocked || (!modelDirty && !toolsDirty && !policyDirty)
                        }
                      >
                        {busy === "settings" ? "Saving..." : "Save changes"}
                      </button>
                    </div>
                    <span className="panel-meta">
                      {policyBoundSessionId !== selectedSessionId
                        ? "Loading"
                        : modelDirty || toolsDirty || policyDirty
                          ? "Unsaved changes"
                          : "In sync"}
                    </span>
                  </div>
                  {policyBoundSessionId === selectedSessionId ? (
                    <div className="panel-body session-tools-panel">
                      <label className="field-stack">
                        <span>Model</span>
                        <input
                          list="session-model-suggestions"
                          value={modelDraft}
                          onChange={(event) => {
                            setModelDraft(event.target.value);
                            setModelDirty(true);
                            setStatus(null);
                          }}
                          placeholder="Required to start or resume the agent"
                          disabled={mutationLocked}
                        />
                        <datalist id="session-model-suggestions">
                          {(catalog?.model_options ?? []).map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </datalist>
                      </label>
                      <ReasoningEffortSelect catalog={catalog?.reasoning_effort_catalog}
                        harness={selectedSession.agent_harness || "codex"} model={modelDraft}
                        route={selectedSession.effective_model_provider_mode}
                        value={reasoningEffortDraft} disabled={mutationLocked}
                        onChange={(value) => { setReasoningEffortDraft(value); setModelDirty(true); setStatus(null); }} />
                      <FastModeSelect harness={selectedSession.agent_harness || "codex"}
                        route={selectedSession.effective_model_provider_mode}
                        value={fastModeDraft} disabled={mutationLocked}
                        onChange={(value) => { setFastModeDraft(value); setModelDirty(true); setStatus(null); }} />
                      <label className="field-stack">
                        <span>Model relay</span>
                        <p className="field-description">
                          Select where Codex model requests are routed. Directory default inherits the selected
                          directory&apos;s provider mode.
                        </p>
                        <select
                          value={policyDraft.modelProviderMode}
                          onChange={(event) =>
                            updatePolicyDraft({
                              modelProviderMode: event.target.value as SessionModelProviderModeSelection,
                            })
                          }
                          disabled={mutationLocked}
                        >
                          <option value="">Directory default</option>
                          {(catalog?.model_provider_modes?.length
                            ? catalog.model_provider_modes
                            : SESSION_MODEL_PROVIDER_MODE_OPTIONS
                          ).map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="field-stack">
                        <span>Allowed tools</span>
                        <div className="session-tool-groups">
                          {groupedToolOptions.map((group) => {
                            const enabledCount = group.items.filter((tool) => toolsDraft.includes(tool.value)).length;
                            const approvalRequiredCount = group.items.filter(
                              (tool) =>
                                toolsDraft.includes(tool.value) &&
                                (policyDraft.approvalRequiredTools.includes(tool.value) ||
                                  inheritedApprovalRequiredForTool(tool.value)),
                            ).length;
                            return (
                              <details key={group.name} className="compact-disclosure session-tool-group">
                                <summary className="compact-disclosure-summary session-tool-group-summary">
                                  <span>{group.name}</span>
                                  <span className="panel-meta">
                                    {enabledCount} of {group.items.length} enabled
                                    {approvalRequiredCount > 0 ? ` • ${approvalRequiredCount} approval required` : ""}
                                  </span>
                                </summary>
                                <div className="feature-list compact-list session-tool-list">
                                  {group.items.map((tool) => {
                                    const toolDescription = describeTool(tool);
                                    const isEnabled = toolsDraft.includes(tool.value);
                                    const toolRule = effectiveRuleForTool(tool.value);
                                    const isDenied = Boolean(toolRule?.denied);
                                    const isInheritedApprovalRequired = inheritedApprovalRequiredForTool(tool.value);
                                    const isLocalApprovalRequired = policyDraft.approvalRequiredTools.includes(
                                      tool.value,
                                    );
                                    const isApprovalRequired = isLocalApprovalRequired || isInheritedApprovalRequired;
                                    return (
                                      <div key={tool.value} className={`session-tool-row${isDenied ? " denied" : ""}`}>
                                        <label className="session-tool-row-main">
                                          <input
                                            type="checkbox"
                                            checked={isEnabled}
                                            onChange={() => toggleTool(tool.value)}
                                            disabled={mutationLocked || isDenied}
                                          />
                                          <Tooltip
                                            content={toolDescription}
                                            className="session-tool-copy"
                                            focusable
                                            ariaLabel={`${tool.label}: ${toolDescription}`}
                                          >
                                            <span>{tool.label}</span>
                                          </Tooltip>
                                        </label>
                                        <div className="session-tool-row-meta">
                                          {isDenied ? (
                                            <span className="status-pill state-failed session-tool-badge">
                                              Denied by policy
                                            </span>
                                          ) : null}
                                          {isInheritedApprovalRequired && !isDenied ? (
                                            <span className="status-pill state-idle session-tool-badge">
                                              Admin required
                                            </span>
                                          ) : null}
                                          {isApprovalRequired ? (
                                            <span className="status-pill state-idle session-tool-badge">
                                              Approval required
                                            </span>
                                          ) : null}
                                          <label className="checkbox-row session-tool-approval-row">
                                            <input
                                              type="checkbox"
                                              checked={isApprovalRequired}
                                              onChange={() => toggleApprovalRequirement(tool.value)}
                                              disabled={
                                                mutationLocked || !isEnabled || isDenied || isInheritedApprovalRequired
                                              }
                                              aria-label={`Require approval for ${tool.label}`}
                                            />
                                            <span>Require approval</span>
                                          </label>
                                          {isApprovalRequired && isEnabled && !isDenied ? (
                                            <Tooltip
                                              content={`Configure approval expiry, use limit, and argument matching for ${tool.label}.`}
                                            >
                                              <button
                                                type="button"
                                                className="session-tool-settings-button"
                                                onClick={() => openApprovalSettings(tool.value)}
                                                disabled={mutationLocked}
                                                aria-label={`Configure approval settings for ${tool.label}`}
                                              >
                                                <SettingsIcon />
                                              </button>
                                            </Tooltip>
                                          ) : null}
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              </details>
                            );
                          })}
                        </div>
                      </div>
                      <details className="compact-disclosure" open={Boolean(connectorGrantsError)}>
                        <summary className="compact-disclosure-summary">Integrations and host access</summary>
                        <div className="stack-layout">
                      <div className="field-stack">
                        <span>Allowed scripts</span>
                        <p className="field-description">
                          One script name per row. Blank rows are ignored, extra spaces are trimmed, and duplicates are
                          removed.
                        </p>
                        <StringListEditor
                          values={policyDraft.allowedScriptNames}
                          onChange={(nextValues) => updatePolicyDraft({ allowedScriptNames: nextValues })}
                          disabled={mutationLocked}
                          itemLabel="Script name"
                          addLabel="Add script"
                          placeholder="Enter a script name"
                        />
                      </div>
                      <div className="field-stack">
                        <span>Connector grants</span>
                        <p className="field-description">
                          Map each connector ID to a JSON array of operation names. The session must also allow the
                          list_connectors and call_connector MCP tools before the agent can discover or invoke grants.
                        </p>
                        <JsonObjectKeyValueEditor
                          value={policyDraft.connectorGrants}
                          onChange={(nextValue) => updatePolicyDraft({ connectorGrants: nextValue })}
                          onValidationChange={(validationError) => {
                            setConnectorGrantsError(validationError);
                            if (validationError) {
                              setPolicyDirty(true);
                              setStatus(null);
                            }
                          }}
                          fieldLabel="Connector grants"
                          disabled={mutationLocked}
                          addLabel="Add connector grant"
                          emptyState="No connector operations are granted to this session."
                        />
                      </div>
                      <div className="field-stack wide-field">
                        <span>Host directory grants</span>
                        <p className="field-description">
                          Select capabilities on named host directories. Selecting one also enables its dedicated
                          host_directory MCP tool; removing a grant leaves explicitly allowed tools unchanged.
                        </p>
                        <HostDirectoryGrantSelector
                          values={policyDraft.hostDirectoryGrants}
                          options={hostDirectoryOptions}
                          onChange={updateHostDirectoryGrants}
                          disabled={mutationLocked}
                        />
                      </div>
                      </div>
                      </details>
                      <details className="compact-disclosure" open={undefined}>
                        <summary className="compact-disclosure-summary">Contacts and communication</summary>
                        <div className="stack-layout">
                      <div className="field-stack">
                        <span>Approval notifications</span>
                        <p className="field-description">
                          Notification-only host targets that receive approval requests for this session. These IDs must
                          resolve to notify-capable communication targets in the selected directory.
                        </p>
                        {renderApprovalNotificationTargetEditor()}
                      </div>
                      <div className="field-stack wide-field">
                        <span>Contacts</span>
                        <p className="field-description">
                          Choose whether this session can use every contact visible in its directory or only an explicit
                          allowlist. An explicit allowlist with no contact IDs blocks access to every contact.
                        </p>
                        <div className="inline-actions compact-actions" role="radiogroup" aria-label="Contact access">
                          <label className="checkbox-row">
                            <input
                              type="radio"
                              name="session-contact-access-mode"
                              checked={policyDraft.allowedContactIds === null}
                              onChange={() => updatePolicyDraft({ allowedContactIds: null })}
                              disabled={mutationLocked}
                            />
                            <span>All directory contacts</span>
                          </label>
                          <label className="checkbox-row">
                            <input
                              type="radio"
                              name="session-contact-access-mode"
                              checked={policyDraft.allowedContactIds !== null}
                              onChange={() =>
                                updatePolicyDraft({ allowedContactIds: policyDraft.allowedContactIds ?? [] })
                              }
                              disabled={mutationLocked}
                            />
                            <span>Explicit allowlist</span>
                          </label>
                        </div>
                        {policyDraft.allowedContactIds === null ? (
                          <div className="callout">
                            This session inherits access to all contacts visible in its directory.
                          </div>
                        ) : (
                          <StringListEditor
                            values={policyDraft.allowedContactIds}
                            onChange={(nextValues) => updatePolicyDraft({ allowedContactIds: nextValues })}
                            disabled={mutationLocked}
                            itemLabel="Contact ID"
                            addLabel="Add contact"
                            placeholder="Enter a configured contact ID"
                            emptyState="No contacts are allowed."
                          />
                        )}
                      </div>
                      {policyDraft.communicationTargetIds === null || policyDraft.emailMailboxGrants === null ? (
                        <div className="field-stack wide-field">
                          <div className="callout">
                            This session uses the legacy communication policy. It remains unchanged until you explicitly
                            convert it to managed communication targets and mailbox grants.
                          </div>
                          <button
                            type="button"
                            className="admin-action-button compact"
                            disabled={mutationLocked}
                            onClick={convertLegacyCommunicationPolicy}
                          >
                            Convert to managed grants
                          </button>
                        </div>
                      ) : null}
                      {policyDraft.communicationTargetIds === null ? (
                        <>
                          <div className="field-stack">
                            <span>Legacy Discord allowlist</span>
                            <StringListEditor
                              values={policyDraft.discordChannelAllowlist}
                              onChange={(nextValues) => updatePolicyDraft({ discordChannelAllowlist: nextValues })}
                              disabled={mutationLocked}
                              itemLabel="Target id"
                              addLabel="Add allowlist row"
                              placeholder="channel-id or communication-target-id"
                            />
                          </div>
                          <div className="field-stack">
                            <span>Legacy Teams allowlist</span>
                            <StringListEditor
                              values={policyDraft.teamsChannelAllowlist}
                              onChange={(nextValues) => updatePolicyDraft({ teamsChannelAllowlist: nextValues })}
                              disabled={mutationLocked}
                              itemLabel="Target id"
                              addLabel="Add allowlist row"
                              placeholder="communication-target-id, team-id/channel-id, or chat:<chat-id>"
                            />
                          </div>
                        </>
                      ) : (
                        <div className="field-stack wide-field agent-communication-targets-field">
                          <span>Communication targets</span>
                          <p className="field-description">
                            Teams and Discord destinations plus outbound email recipients. These grants do not grant
                            mailbox access.
                          </p>
                          <CommunicationTargetCheckboxTable
                            values={policyDraft.communicationTargetIds}
                            options={selectedSession?.communication_target_options ?? []}
                            onChange={(nextValues) => updatePolicyDraft({ communicationTargetIds: nextValues })}
                            disabled={mutationLocked}
                            emptyState="No communication targets are granted to this session."
                          />
                        </div>
                      )}
                      {policyDraft.emailMailboxGrants !== null ? (
                        <div className="field-stack wide-field agent-communication-targets-field">
                          <span>Email mailbox access</span>
                          <p className="field-description">
                            Read-only access is independent of send destinations. Draft and send access are explicit
                            mailbox capabilities.
                          </p>
                          <EmailMailboxGrantEditor
                            grants={policyDraft.emailMailboxGrants}
                            options={selectedSession?.email_mailbox_options ?? []}
                            onChange={(nextGrants) => updatePolicyDraft({ emailMailboxGrants: nextGrants })}
                            disabled={mutationLocked}
                            emptyState={
                              (selectedSession?.email_mailbox_options ?? []).length > 0
                                ? "No mailbox accounts are granted to this session."
                                : "No mailbox accounts are available under this session's directory policy. Configure the mailbox allowlist and folder policy first."
                            }
                          />
                          {(selectedSession?.email_mailbox_options ?? []).length === 0 ? (
                            <div className="inline-actions compact-actions">
                              <button
                                type="button"
                                className="admin-action-button compact"
                                onClick={openMailboxConfiguration}
                              >
                                Configure mailbox access
                              </button>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                      </div>
                      </details>
                      <details className="compact-disclosure" open={Boolean(httpTargetsError || policyDraft.httpTargetsSourceError)}>
                        <summary className="compact-disclosure-summary">Web access</summary>
                        <div className="stack-layout">
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={policyDraft.webSearchEnabled}
                          onChange={(event) => updatePolicyDraft({ webSearchEnabled: event.target.checked })}
                          disabled={mutationLocked}
                        />
                        <span>Web search</span>
                      </label>
                      <div className="field-stack">
                        <span>HTTP targets</span>
                        <HttpTargetsEditor
                          entries={policyDraft.httpTargets}
                          onChange={(nextEntries) => {
                            setPolicyDraft((current) => ({
                              ...current,
                              httpTargets: nextEntries,
                              httpTargetsSourceError: null,
                            }));
                            setHttpTargetsDirty(true);
                            setPolicyDirty(true);
                            setStatus(null);
                          }}
                          onValidationChange={setHttpTargetsError}
                          sourceError={policyDraft.httpTargetsSourceError}
                          sessionId={selectedSession.session_id}
                          directoryPath={selectedSession.directory_path}
                          disabled={mutationLocked}
                        />
                      </div>
                      </div>
                      </details>
                      <details className="compact-disclosure" open={undefined}>
                        <summary className="compact-disclosure-summary">File transfer</summary>
                        <div className="stack-layout">
                      <div className="field-stack session-communication-files">
                        <span>Communication files</span>
                        <div className="session-communication-file-toggles">
                          <label className="checkbox-row">
                            <input
                              type="checkbox"
                              checked={policyDraft.communicationFileReceiveEnabled}
                              onChange={(event) =>
                                updatePolicyDraft({ communicationFileReceiveEnabled: event.target.checked })
                              }
                              disabled={mutationLocked}
                            />
                            <span>Receive inbound files</span>
                          </label>
                          <label className="checkbox-row">
                            <input
                              type="checkbox"
                              checked={policyDraft.communicationFileSendEnabled}
                              onChange={(event) =>
                                updatePolicyDraft({ communicationFileSendEnabled: event.target.checked })
                              }
                              disabled={mutationLocked}
                            />
                            <span>Send files</span>
                          </label>
                          <label className="checkbox-row">
                            <input
                              type="checkbox"
                              checked={policyDraft.communicationFileRequireApprovalForSend}
                              onChange={(event) =>
                                updatePolicyDraft({ communicationFileRequireApprovalForSend: event.target.checked })
                              }
                              disabled={mutationLocked}
                            />
                            <span>Require approval for sends</span>
                          </label>
                        </div>
                      </div>
                      <label className="field-stack">
                        <span>Communication file max bytes</span>
                        <p className="field-description">
                          Size in bytes. Enter a non-negative whole number; blank keeps no explicit session value and
                          falls back to the effective policy. 0 allows only zero-byte files.
                        </p>
                        <input
                          type="number"
                          min="0"
                          step="1"
                          inputMode="numeric"
                          value={policyDraft.communicationFileMaxBytes}
                          onChange={(event) => updatePolicyDraft({ communicationFileMaxBytes: event.target.value })}
                          disabled={mutationLocked}
                        />
                      </label>
                      <div className="field-stack">
                        <span>Allowed MIME types</span>
                        <p className="field-description">
                          One MIME type per row. Blank rows are ignored, extra spaces are trimmed, and duplicates are
                          removed.
                        </p>
                        <StringListEditor
                          values={policyDraft.communicationFileAllowedMimeTypes}
                          onChange={(nextValues) =>
                            updatePolicyDraft({ communicationFileAllowedMimeTypes: nextValues })
                          }
                          disabled={mutationLocked}
                          itemLabel="MIME type"
                          addLabel="Add MIME type"
                          placeholder="text/plain"
                        />
                      </div>
                      <div className="field-stack">
                        <span>Allowed extensions</span>
                        <p className="field-description">
                          One suffix per row, such as <code>.txt</code> or <code>.pdf</code>. Entries must start with a
                          leading dot, blank rows are ignored, and duplicates are removed.
                        </p>
                        <StringListEditor
                          values={policyDraft.communicationFileAllowedExtensions}
                          onChange={(nextValues) =>
                            updatePolicyDraft({ communicationFileAllowedExtensions: nextValues })
                          }
                          disabled={mutationLocked}
                          itemLabel="Extension"
                          addLabel="Add extension"
                          placeholder=".txt"
                          validateItem={(value) => validateLeadingDotExtensionValue(value)}
                        />
                      </div>
                      <div className="field-stack">
                        <span>Blocked extensions</span>
                        <p className="field-description">
                          One suffix per row, such as <code>.exe</code> or <code>.sh</code>. Entries must start with a
                          leading dot, blank rows are ignored, and duplicates are removed.
                        </p>
                        <StringListEditor
                          values={policyDraft.communicationFileBlockedExtensions}
                          onChange={(nextValues) =>
                            updatePolicyDraft({ communicationFileBlockedExtensions: nextValues })
                          }
                          disabled={mutationLocked}
                          itemLabel="Extension"
                          addLabel="Add extension"
                          placeholder=".exe"
                          validateItem={(value) => validateLeadingDotExtensionValue(value)}
                        />
                      </div>
                      </div>
                      </details>
                      <details className="compact-disclosure" open={Boolean(policyMetadataError)}>
                        <summary className="compact-disclosure-summary">Cost category and metadata</summary>
                        <div className="stack-layout">
                      <CostCategoryInput value={policyDraft.costCategory} directoryPath={selectedSession.directory_path}
                        disabled={mutationLocked} onChange={(costCategory) => updatePolicyDraft({ costCategory })} />
                      <p className="field-description">Applies to newly submitted prompts. Earlier costs keep their category.</p>
                      <div className="field-stack">
                        <span>Metadata</span>
                        <p className="field-description">
                          Must be a JSON object. Nested values are edited as JSON snippets and preserved when unchanged.
                        </p>
                        <JsonObjectKeyValueEditor
                          value={policyDraft.metadata}
                          onChange={(nextValue) => {
                            setMetadataDirty(true);
                            updatePolicyDraft({ metadata: nextValue });
                          }}
                          onValidationChange={(error) => {
                            setPolicyMetadataError(error);
                            if (error) {
                              setMetadataDirty(true);
                              setPolicyDirty(true);
                              setStatus(null);
                            }
                          }}
                          fieldLabel="Metadata"
                          disabled={mutationLocked}
                          addLabel="Add row"
                          emptyState="No metadata rows are configured."
                        />
                      </div>
                      </div>
                      </details>

                    </div>
                  ) : (
                    <p className="muted-text" role="status">
                      Loading access settings…
                    </p>
                  )}
                </article>
                <article className="panel" hidden={selectedSection !== "schedules"}>
                  <div className="panel-header">
                    <h3>Wake schedules</h3>
                    <div className="feature-toolbar-actions session-wake-actions">
                      <span className="panel-meta">{wakeScheduleCountLabel}</span>
                      {hiddenPastOneTimeWakeScheduleCount > 0 || showPastOneTimeWakeSchedules ? (
                        <label className="checkbox-row session-wake-history-toggle">
                          <input
                            type="checkbox"
                            checked={showPastOneTimeWakeSchedules}
                            onChange={(event) => setShowPastOneTimeWakeSchedules(event.target.checked)}
                          />
                          <span>Show past one-time</span>
                        </label>
                      ) : null}
                      <button
                        type="button"
                        onClick={resetWakeEditor}
                        disabled={
                          mutationLocked ||
                          (!editingWakeRequestId &&
                            !wakePrompt &&
                            !wakeCron &&
                            !wakeResumeAt &&
                            wakeMode === "same_context" &&
                            wakeEnabled)
                        }
                      >
                        Create new
                      </button>

                    </div>
                  </div>
                  <div className="panel-body">
                    <div className="feature-list compact-list">
                      {detailLoading && selectedSessionId ? (
                        <div className="empty-state">Loading wake schedules...</div>
                      ) : null}
                      {!detailLoading &&
                        visibleWakeScheduleItems.map((item) => (
                          <div key={item.request_id} className={`feature-list-item${editingWakeRequestId === item.request_id ? " selected" : ""}`}>
                            <button type="button" className="text-button" onClick={() => bindWakeSchedule(item.request_id)}
                              aria-label={`Edit wake schedule ${item.request_id}`}>

                            <div className="feature-list-title-row">
                              <strong>
                                {item.schedule_kind === "cron" ? item.cron : formatTimestamp(item.resume_at)}
                              </strong>
                              <span
                                className={`status-pill state-${item.status === "cancelled" ? "idle" : item.enabled ? "running" : "idle"}`}
                              >
                                {item.status === "cancelled" ? "Cancelled" : item.enabled ? "Enabled" : "Disabled"}
                              </span>
                            </div>
                            <div className="feature-list-meta">{item.prompt}</div>
                            <div className="feature-list-meta">{item.resume_mode}</div>
                            </button>
                            {canToggleWakeScheduleEnabled(item) ? (
                              <button type="button" onClick={() => void toggleWakeScheduleEnabled(item)}
                                disabled={busy !== null || mutationLocked}
                                aria-label={`${item.enabled ? "Disable" : "Enable"} wake schedule ${item.request_id}`}>
                                {busy === `wake-toggle-${item.request_id}` ? "Updating…" : item.enabled ? "Disable" : "Enable"}
                              </button>
                            ) : null}
                          </div>
                        ))}
                      {!detailLoading && detail?.wake_schedules.length === 0 ? (
                        <div className="empty-state">No wake schedules configured.</div>
                      ) : null}
                      {!detailLoading &&
                      (detail?.wake_schedules.length ?? 0) > 0 &&
                      visibleWakeScheduleItems.length === 0 ? (
                        <div className="empty-state">Past one-time schedules hidden.</div>
                      ) : null}
                    </div>
                    <h4>{editingWakeRequestId ? "Edit schedule" : "New schedule"}</h4>
                    <label className="field-stack">
                      <span>Prompt</span>
                      <textarea
                        rows={4}
                        value={wakePrompt}
                        onChange={(event) => setWakePrompt(event.target.value)}
                        disabled={wakeEditorLocked}
                      />
                    </label>
                    <div className="field-stack">
                      <span>Schedule type</span>
                      <div className="inline-actions compact-actions session-schedule-type-toggle">
                        <button
                          type="button"
                          className={wakeScheduleKind === "cron" ? "primary" : ""}
                          onClick={() => setScheduleKind("cron")}
                          disabled={wakeEditorLocked}
                        >
                          Recurring
                        </button>
                        <button
                          type="button"
                          className={wakeScheduleKind === "resume_at" ? "primary" : ""}
                          onClick={() => setScheduleKind("resume_at")}
                          disabled={wakeEditorLocked}
                        >
                          One time
                        </button>
                      </div>
                    </div>
                    {wakeScheduleKind === "cron" ? (
                      <label className="field-stack">
                        <span>Cron</span>
                        <input
                          value={wakeCron}
                          onChange={(event) => setWakeCron(event.target.value)}
                          placeholder="*/30 * * * *"
                          disabled={wakeEditorLocked}
                        />
                      </label>
                    ) : (
                      <label className="field-stack">
                        <span>Resume at</span>
                        <input
                          type="datetime-local"
                          value={wakeResumeAt}
                          onChange={(event) => setWakeResumeAt(event.target.value)}
                          disabled={wakeEditorLocked}
                        />
                      </label>
                    )}
                    <label className="field-stack">
                      <span>Resume mode</span>
                      <select
                        value={wakeMode}
                        onChange={(event) => setWakeMode(event.target.value)}
                        disabled={wakeEditorLocked}
                      >
                        {(catalog?.resume_mode_options ?? []).map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={wakeEnabled}
                        onChange={(event) => setWakeEnabled(event.target.checked)}
                        disabled={wakeEditorLocked}
                      />
                      <span>Enabled</span>
                    </label>
                    <div className="feature-toolbar-actions session-panel-actions">
                      <button
                        type="button"
                        className="primary"
                        onClick={() => void submitWakeSchedule(editingWakeRequestId ? "update" : "create")}
                        disabled={busy === "wake-create" || busy === "wake-update" || wakeEditorLocked}
                      >
                        {editingWakeRequestId
                          ? busy === "wake-update"
                            ? "Saving..."
                            : "Save schedule"
                          : busy === "wake-create"
                            ? "Creating..."
                            : "Create wake schedule"}
                      </button>
                      {selectedWakeSchedule ? (
                        <button
                          className="danger"
                          type="button"
                          onClick={() => void deleteWakeSchedule(selectedWakeSchedule)}
                          disabled={busy === `wake-delete-${selectedWakeSchedule.request_id}` || mutationLocked}
                        >
                          {busy === `wake-delete-${selectedWakeSchedule.request_id}`
                            ? "Deleting..."
                            : "Delete selected"}
                        </button>
                      ) : null}
                    </div>
                    <div className="wake-ledger-section">
                      <div className="panel-header wake-ledger-header">
                        <div>
                          <h4>Wake ledger</h4>
                          <p className="field-description">
                            Durable status entries for this session&apos;s scheduled wakes, including the latest
                            dispatch summary and retryability.
                          </p>
                        </div>
                        <div className="feature-toolbar-actions session-wake-actions">
                          <label className="field-stack wake-ledger-filter">
                            <span>Status</span>
                            <select
                              value={wakeLedgerFilter}
                              onChange={(event) => setWakeLedgerFilter(event.target.value as WakeLedgerFilter)}
                            >
                              {WAKE_LEDGER_FILTER_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <span className="panel-meta">{visibleWakeLedgerItems.length} shown</span>
                        </div>
                      </div>
                      {wakeLedgerError ? <div className="callout error-callout">{wakeLedgerError}</div> : null}
                      <div className="feature-list compact-list wake-ledger-list">
                        {visibleWakeLedgerItems.map((item) => (
                          <div key={item.request_id} className="feature-list-item wake-ledger-item">
                            <div className="feature-list-title-row">
                              <strong>{item.request_id}</strong>
                              <span className={`status-pill state-${wakeLedgerStatusClass(item.status)}`}>
                                {wakeLedgerDisplayLabel(item)}
                              </span>
                            </div>
                            <div className="feature-list-meta">
                              {item.prompt_summary ?? item.redacted_prompt ?? "Prompt unavailable"}
                            </div>
                            <div className="feature-list-meta">
                              {item.schedule_cron
                                ? `Cron ${item.schedule_cron}`
                                : `Resume ${formatTimestamp(item.resume_at ?? item.scheduled_for)}`}
                            </div>
                            <div className="feature-list-meta">
                              {item.source ? `Source ${item.source}` : "Source unavailable"}
                              {item.status_reason ? ` • ${item.status_reason}` : ""}
                            </div>
                            <div className="feature-list-meta">
                              Updated {formatTimestamp(item.updated_at)}
                              {item.status_updated_at ? ` • Status ${formatTimestamp(item.status_updated_at)}` : ""}
                            </div>
                            <div className="feature-list-meta">
                              Latest dispatch{" "}
                              {item.latest_dispatch
                                ? `${item.latest_dispatch.status} at ${formatTimestamp(item.latest_dispatch.dispatched_at)}`
                                : "none"}
                            </div>
                            {item.confirmation_state === "awaiting_confirmation" ? (
                              <div className="feature-list-meta">Awaiting confirmation</div>
                            ) : null}
                            {item.completed_at ? (
                              <div className="feature-list-meta">
                                Completion evidence{" "}
                                {formatWakeCompletionEvidence(item.completion_evidence) ?? "unavailable"}
                              </div>
                            ) : null}
                            {item.failure_reason ? (
                              <div className="callout warning-callout">{item.failure_reason}</div>
                            ) : null}
                            {!item.failure_reason && item.latest_dispatch?.error ? (
                              <div className="callout warning-callout">{item.latest_dispatch.error}</div>
                            ) : null}
                            <div className="feature-toolbar-actions compact-actions">
                              <span className="panel-meta">
                                {item.retry_available
                                  ? "Retry available"
                                  : item.completed_at
                                    ? `Completed ${formatTimestamp(item.completed_at)}`
                                    : item.enabled
                                      ? "Enabled"
                                      : "Disabled"}
                              </span>
                              {item.retry_available ? (
                                <button
                                  type="button"
                                  className="primary"
                                  onClick={() => void retryWakeLedgerEntry(item)}
                                  disabled={busy === `wake-ledger-retry-${item.request_id}` || mutationLocked}
                                >
                                  {busy === `wake-ledger-retry-${item.request_id}` ? "Retrying..." : "Retry now"}
                                </button>
                              ) : null}
                            </div>
                          </div>
                        ))}
                        {wakeLedgerItems.length === 0 ? (
                          <div className="empty-state">No wake ledger entries available for this session.</div>
                        ) : null}
                        {wakeLedgerItems.length > 0 && visibleWakeLedgerItems.length === 0 ? (
                          <div className="empty-state">No wake ledger entries match the selected status.</div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                </article>
              </div>
              <details className="panel compact-disclosure" hidden={selectedSection !== "overview"}>
                  <summary className="compact-disclosure-summary feature-list-title-row">
                    <h3>Session detail</h3>
                    <span className="panel-meta">{detailError ? "Unavailable" : "Live summary"}</span>
                  </summary>
                  <pre className="log-view">
                    {JSON.stringify(detail?.summary_json ?? selectedSession, null, 2)}
                  </pre>
              </details>
              <RenewSessionModal
                open={showRenewModal}
                sessionId={selectedSession.session_id}
                renewExtension={renewExtension}
                presets={catalog?.renew_extension_presets ?? []}
                busy={busy === "renew"}
                locked={mutationLocked}
                onChange={setRenewExtension}
                onClose={() => setShowRenewModal(false)}
                onSubmit={() => void runSessionAction("renew")}
              />
              <ApprovalSettingsModal
                open={Boolean(approvalSettingsTool && approvalSettingsToolOption)}
                toolLabel={approvalSettingsToolOption?.label ?? approvalSettingsTool}
                toolValue={approvalSettingsTool}
                settings={approvalSettingsDraftForTool(approvalSettingsTool)}
                inheritedSettings={effectiveApprovalSettingsForTool(approvalSettingsTool)}
                hardRule={effectiveRuleForTool(approvalSettingsTool)}
                busy={busy === "settings"}
                locked={mutationLocked}
                onApply={(settings) => updateApprovalSettingsDraft(approvalSettingsTool, settings)}
                onReset={() => resetApprovalSettingsDraft(approvalSettingsTool)}
                onClose={() => setApprovalSettingsTool("")}
                hasExplicitOverride={hasExplicitApprovalSettingsDraft(approvalSettingsTool)}
              />
            </div>
          ) : (
            <RecordPlaceholder>Select a session to view details</RecordPlaceholder>
          )}
        </section>
      </div>
    </RecordWorkspace>
  );
}
