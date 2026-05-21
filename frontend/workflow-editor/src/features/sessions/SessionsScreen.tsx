import { useEffect, useMemo, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { DeleteIcon, ExtendIcon, RevokeIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";
import { readAdminHashQuery } from "../admin/hash";

type SessionRecord = {
  session_id: string;
  vm_id: string | null;
  model: string | null;
  directory_path: string;
  allowed_tools: string[];
  approval_required_tools?: string[];
  allowed_script_names: string[];
  discord_channel_allowlist: string[];
  teams_channel_allowlist: string[];
  communication_file_receive_enabled: boolean;
  communication_file_send_enabled: boolean;
  communication_file_max_bytes: number | null;
  communication_file_allowed_mime_types: string[];
  communication_file_allowed_extensions: string[];
  communication_file_blocked_extensions: string[];
  communication_file_require_approval_for_send: boolean;
  expires_at: string;
  created_at: string;
  metadata: Record<string, unknown>;
  issue_number?: string | null;
  is_expired: boolean;
};

type SessionListResponse = {
  items: SessionRecord[];
  active_items: SessionRecord[];
  expired_items: SessionRecord[];
  counts: {
    total: number;
    active: number;
    expired: number;
  };
};

type WakeSchedule = {
  request_id: string;
  session_id: string;
  prompt: string;
  cron?: string | null;
  resume_at?: string | null;
  resume_mode: string;
  enabled: boolean;
  updated_at?: string | null;
  schedule_kind: "cron" | "resume_at";
};

type WakeScheduleKind = "cron" | "resume_at";

type SessionDetailResponse = {
  session: SessionRecord;
  wake_schedules: WakeSchedule[];
  summary_json: Record<string, unknown>;
};

type SessionCatalogResponse = {
  tool_options: Array<{ value: string; label: string }>;
  resume_mode_options: Array<{ value: string; label: string }>;
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
  allowedScriptNames: string;
  approvalRequiredTools: string[];
  discordChannelAllowlist: string;
  teamsChannelAllowlist: string;
  communicationFileReceiveEnabled: boolean;
  communicationFileSendEnabled: boolean;
  communicationFileMaxBytes: string;
  communicationFileAllowedMimeTypes: string;
  communicationFileAllowedExtensions: string;
  communicationFileBlockedExtensions: string;
  communicationFileRequireApprovalForSend: boolean;
  metadataJson: string;
};

const POLL_INTERVAL_MS = 10000;
const TOOL_DESCRIPTION_FALLBACK = "Allow the session to invoke this MCP tool when the agent needs it.";

const TOOL_DESCRIPTIONS: Record<string, string> = {
  create_ado_backlog_item: "Create a new Azure DevOps backlog item on the host using the configured project defaults.",
  post_ado_backlog_comment: "Post a comment to an Azure DevOps backlog item, optionally with staged image artifacts.",
  update_ado_backlog_text: "Update Azure DevOps backlog description or acceptance criteria text, optionally with staged image artifacts.",
  query_ado_backlog_items: "Search Azure DevOps backlog items with the host-side query filters and return matching work items.",
  list_ado_current_iteration_backlog_items: "List backlog items assigned to the current Azure DevOps iteration for the configured project.",
  get_ado_backlog_item: "Fetch a single Azure DevOps backlog item with its latest host-side details.",
  create_local_issue: "Create a local issue record in the selected directory so the session can track work inside agent-sandbox.",
  list_issues: "List local issues that the session is allowed to inspect in the current directory scope.",
  get_issue: "Read the details of a specific local issue, including status, metadata, and body content.",
  set_issue_plan: "Store or update the implementation plan attached to a local issue.",
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
  communication_copy_artifact_to_workspace: "Copy a staged communication artifact into the agent workspace.",
  discord_post_file: "Post staged communication file artifacts to an allowlisted Discord channel.",
  teams_get_messages: "Read Microsoft Teams messages made available to the session by the host ingest worker.",
  teams_post_status: "Post a Microsoft Teams status update from the host on behalf of the session.",
  teams_post_file: "Post staged communication file artifacts to an allowlisted Microsoft Teams target.",
  teams_meeting_get_events: "Read Teams meeting transcript and status events captured for this session.",
  teams_meeting_speak: "Speak into the active Teams meeting through the host-managed meeting bridge.",
  teams_meeting_stop: "Leave the active Teams meeting and close the host-side meeting binding.",
  list_contacts: "List directory contact ids and safe contact summaries without exposing phone numbers or email addresses.",
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

function parseList(value: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of value.split(/[\n,]/)) {
    const normalized = item.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

function formatList(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0).join("\n");
}

function policyDraftFromSession(session: SessionRecord): SessionPolicyDraft {
  return {
    allowedScriptNames: formatList(session.allowed_script_names),
    approvalRequiredTools: sortTools(session.approval_required_tools ?? []),
    discordChannelAllowlist: formatList(session.discord_channel_allowlist),
    teamsChannelAllowlist: formatList(session.teams_channel_allowlist),
    communicationFileReceiveEnabled: Boolean(session.communication_file_receive_enabled),
    communicationFileSendEnabled: Boolean(session.communication_file_send_enabled),
    communicationFileMaxBytes: session.communication_file_max_bytes == null ? "" : String(session.communication_file_max_bytes),
    communicationFileAllowedMimeTypes: formatList(session.communication_file_allowed_mime_types),
    communicationFileAllowedExtensions: formatList(session.communication_file_allowed_extensions),
    communicationFileBlockedExtensions: formatList(session.communication_file_blocked_extensions),
    communicationFileRequireApprovalForSend: Boolean(session.communication_file_require_approval_for_send),
    metadataJson: JSON.stringify(session.metadata ?? {}, null, 2),
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

function parseJsonObject(value: string, label: string): Record<string, unknown> {
  const trimmed = value.trim();
  if (!trimmed) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error(`${label} must be valid JSON.`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed as Record<string, unknown>;
}

function describeTool(tool: ToolOption): string {
  return TOOL_DESCRIPTIONS[tool.value] ?? `${tool.label} tool. ${TOOL_DESCRIPTION_FALLBACK}`;
}

const sessionListCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

const TOOL_GROUP_ORDER = [
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
  "Other",
] as const;

function groupToolOption(tool: ToolOption): string {
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
    "query_ado_backlog_items",
    "list_ado_current_iteration_backlog_items",
    "get_ado_backlog_item",
  ].includes(value)) {
    return "Azure DevOps";
  }
  if ([
    "list_pull_requests",
    "get_pull_request",
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
    "schedule_resume",
    "run_script",
    "copilot_host_auth",
  ].includes(value)) {
    return "Agent control";
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
  if (["list_contacts", "make_phone_call", "get_phone_call_result"].includes(value)) {
    return "Voice calling";
  }
  if (["mssql_provision_database", "mssql_list_databases", "mssql_drop_database"].includes(value)) {
    return "SQL Server";
  }
  if ([
    "communication_list_attachments",
    "communication_download_attachment",
    "communication_copy_artifact_to_workspace",
    "communication_stage_workspace_file",
    "discord_post_file",
    "teams_post_file",
    "email_create_draft_with_attachments",
    "email_send_approved_with_attachments",
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
  if (["email_list_unread", "email_get_message", "email_create_draft", "email_send_approved"].includes(value)) {
    return "Email";
  }
  if (["has_pending_feedback", "list_unread_feedback", "list_all_feedback"].includes(value)) {
    return "Feedback";
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
  return TOOL_GROUP_ORDER
    .map((name) => ({ name, items: groups.get(name) ?? [] }))
    .filter((group) => group.items.length > 0)
    .map((group) => ({
      ...group,
      items: [...group.items].sort((left, right) => left.label.localeCompare(right.label, undefined, { sensitivity: "base" })),
    }));
}

function matchesFilter(item: SessionRecord, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  return [item.session_id, item.vm_id ?? "", item.issue_number ?? "", item.directory_path, item.model ?? ""]
    .join(" ")
    .toLowerCase()
    .includes(normalized);
}

function pickSessionIdForTab(response: SessionListResponse, tab: TabKey, preferredSessionId?: string): string {
  const tabItems = tab === "active" ? response.active_items : response.expired_items;
  if (preferredSessionId && tabItems.some((item) => item.session_id === preferredSessionId)) {
    return preferredSessionId;
  }
  return tabItems[0]?.session_id ?? "";
}

function sessionIdFromHash(): string {
  return readAdminHashQuery().get("session_id")?.trim() ?? "";
}

function tabForSessionId(response: SessionListResponse | null, sessionId: string): TabKey | null {
  if (!response || !sessionId) return null;
  if (response.active_items.some((item) => item.session_id === sessionId)) {
    return "active";
  }
  if (response.expired_items.some((item) => item.session_id === sessionId)) {
    return "expired";
  }
  return null;
}

function RenewSessionModal({
  open,
  sessionId,
  renewExtension,
  presets,
  busy,
  locked,
  onChange,
  onClose,
  onSubmit,
}: {
  open: boolean;
  sessionId: string;
  renewExtension: string;
  presets: string[];
  busy: boolean;
  locked: boolean;
  onChange: (value: string) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  if (!open) return null;
  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card session-renew-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-renew-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <div className="eyebrow">Session renewal</div>
            <h2 id="session-renew-modal-title">Extend session</h2>
            <p>Choose how long to extend <strong>{sessionId}</strong>.</p>
          </div>
          <div className="inline-actions">
            <button type="button" className="admin-shell-button admin-shell-button-muted" onClick={onClose}>
              Cancel
            </button>
          </div>
        </div>
        <div className="modal-body">
          <label className="field-stack">
            <span>Extend by</span>
            <input
              autoFocus
              list="renew-extension-presets-modal"
              value={renewExtension}
              onChange={(event) => onChange(event.target.value)}
              disabled={locked || busy}
            />
            <datalist id="renew-extension-presets-modal">
              {presets.map((preset) => (
                <option key={preset} value={preset} />
              ))}
            </datalist>
          </label>
          <div className="feature-toolbar-actions session-renew-actions">
                  <button type="button" className="primary" onClick={onSubmit} disabled={locked || busy}>
                    {busy ? "Extending..." : "Extend"}
                  </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function SessionsScreen() {
  const { privilegedUnlocked, selectedDirectoryPath } = useAdminUi();
  const [response, setResponse] = useState<SessionListResponse | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [deepLinkSessionId, setDeepLinkSessionId] = useState(() => sessionIdFromHash());
  const [detail, setDetail] = useState<SessionDetailResponse | null>(null);
  const [catalog, setCatalog] = useState<SessionCatalogResponse | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("active");
  const [sessionFilter, setSessionFilter] = useState("");
  const [renewExtension, setRenewExtension] = useState("1d");
  const [showRenewModal, setShowRenewModal] = useState(false);
  const [modelDraft, setModelDraft] = useState("");
  const [modelDirty, setModelDirty] = useState(false);
  const [modelBoundSessionId, setModelBoundSessionId] = useState("");
  const [toolsDraft, setToolsDraft] = useState<string[]>([]);
  const [toolsDirty, setToolsDirty] = useState(false);
  const [toolsBoundSessionId, setToolsBoundSessionId] = useState("");
  const [policyDraft, setPolicyDraft] = useState<SessionPolicyDraft>({
    allowedScriptNames: "",
    approvalRequiredTools: [],
    discordChannelAllowlist: "",
    teamsChannelAllowlist: "",
    communicationFileReceiveEnabled: false,
    communicationFileSendEnabled: false,
    communicationFileMaxBytes: "",
    communicationFileAllowedMimeTypes: "",
    communicationFileAllowedExtensions: "",
    communicationFileBlockedExtensions: "",
    communicationFileRequireApprovalForSend: false,
    metadataJson: "{}",
  });
  const [policyDirty, setPolicyDirty] = useState(false);
  const [policyBoundSessionId, setPolicyBoundSessionId] = useState("");
  const [wakePrompt, setWakePrompt] = useState("");
  const [wakeCron, setWakeCron] = useState("");
  const [wakeResumeAt, setWakeResumeAt] = useState("");
  const [wakeScheduleKind, setWakeScheduleKind] = useState<WakeScheduleKind>("cron");
  const [wakeMode, setWakeMode] = useState("same_context");
  const [wakeEnabled, setWakeEnabled] = useState(true);
  const [editingWakeRequestId, setEditingWakeRequestId] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [modelStatus, setModelStatus] = useState<string | null>(null);
  const [toolsStatus, setToolsStatus] = useState<string | null>(null);
  const [policyStatus, setPolicyStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const syncDeepLink = () => {
      const nextSessionId = sessionIdFromHash();
      setDeepLinkSessionId(nextSessionId);
      if (nextSessionId) {
        setSelectedSessionId(nextSessionId);
        setSessionFilter(nextSessionId);
      }
    };

    syncDeepLink();
    window.addEventListener("hashchange", syncDeepLink);
    return () => {
      window.removeEventListener("hashchange", syncDeepLink);
    };
  }, []);

  async function refreshSessions(): Promise<SessionListResponse> {
    const query = new URLSearchParams({
      include_expired: "true",
      directory_path: selectedDirectoryPath,
      direct_only: "true",
    });
    const payload = await fetchJson<SessionListResponse>(`/api/admin/sessions?${query.toString()}`);
    setResponse(payload);
    const targetTab = tabForSessionId(payload, deepLinkSessionId);
    if (targetTab) {
      setActiveTab((current) => (current === targetTab ? current : targetTab));
      setSelectedSessionId(deepLinkSessionId);
      setSessionFilter(deepLinkSessionId);
    }
    setSelectedSessionId((current) => {
      if (deepLinkSessionId) {
        return payload.items.some((item) => item.session_id === deepLinkSessionId) ? deepLinkSessionId : current;
      }
      const nextSelected = pickSessionIdForTab(payload, activeTab, current);
      if (nextSelected) {
        return nextSelected;
      }
      if (current && payload.items.some((item) => item.session_id === current)) {
        return current;
      }
      return payload.active_items[0]?.session_id ?? payload.expired_items[0]?.session_id ?? "";
    });
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
  }, [activeTab, deepLinkSessionId, selectedDirectoryPath]);

  useEffect(() => {
    if (deepLinkSessionId) {
      return;
    }
    const nextItems = activeTab === "active" ? response?.active_items ?? [] : response?.expired_items ?? [];
    if (selectedSessionId && nextItems.some((item) => item.session_id === selectedSessionId)) {
      return;
    }
    setSelectedSessionId(nextItems[0]?.session_id ?? "");
  }, [activeTab, deepLinkSessionId, response?.active_items, response?.expired_items, selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId) {
      setDetail(null);
      setDetailError(null);
      setEditingWakeRequestId("");
      return;
    }
    let cancelled = false;
    fetchJson<SessionDetailResponse>(`/api/admin/sessions/${encodeURIComponent(selectedSessionId)}`)
      .then((payload) => {
        if (cancelled) return;
        setDetail(payload);
        setDetailError(null);
        const sessionId = payload.session.session_id;
        if (modelBoundSessionId !== sessionId) {
          setModelDraft(payload.session.model ?? "");
          setModelDirty(false);
          setModelStatus(null);
          setModelBoundSessionId(sessionId);
        } else if (!modelDirty) {
          setModelDraft(payload.session.model ?? "");
        }
        if (toolsBoundSessionId !== sessionId) {
          setToolsDraft(sortTools(payload.session.allowed_tools));
          setToolsDirty(false);
          setToolsStatus(null);
          setToolsBoundSessionId(sessionId);
        } else if (!toolsDirty) {
          setToolsDraft(sortTools(payload.session.allowed_tools));
        }
        if (policyBoundSessionId !== sessionId) {
          setPolicyDraft(policyDraftFromSession(payload.session));
          setPolicyDirty(false);
          setPolicyStatus(null);
          setPolicyBoundSessionId(sessionId);
        } else if (!policyDirty) {
          setPolicyDraft(policyDraftFromSession(payload.session));
        }
        if (!editingWakeRequestId || !payload.wake_schedules.some((item) => item.request_id === editingWakeRequestId)) {
          const firstSchedule = payload.wake_schedules[0];
          if (firstSchedule) {
            setEditingWakeRequestId(firstSchedule.request_id);
            setWakePrompt(firstSchedule.prompt);
            setWakeCron(firstSchedule.cron ?? "");
            setWakeResumeAt(toDatetimeLocalValue(firstSchedule.resume_at));
            setWakeMode(firstSchedule.resume_mode);
            setWakeEnabled(firstSchedule.enabled);
          } else {
            setEditingWakeRequestId("");
            setWakePrompt("");
            setWakeCron("");
            setWakeResumeAt("");
            setWakeMode("same_context");
            setWakeEnabled(true);
          }
        }
      })
      .catch((reason) => {
        if (cancelled) return;
        setDetailError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedSessionId]);

  const visibleItems = useMemo(() => {
    const source = activeTab === "active" ? response?.active_items ?? [] : response?.expired_items ?? [];
    return source
      .filter((item) => matchesFilter(item, sessionFilter))
      .sort((left, right) => sessionListCollator.compare(left.session_id, right.session_id));
  }, [activeTab, response?.active_items, response?.expired_items, sessionFilter]);

  useEffect(() => {
    if (selectedSessionId && visibleItems.some((item) => item.session_id === selectedSessionId)) {
      return;
    }
    setSelectedSessionId(visibleItems[0]?.session_id ?? "");
  }, [selectedSessionId, visibleItems]);

  const selectedSession = useMemo(
    () => response?.items.find((item) => item.session_id === selectedSessionId) ?? detail?.session ?? null,
    [detail?.session, response?.items, selectedSessionId],
  );

  const selectedWakeSchedule = useMemo(
    () => detail?.wake_schedules.find((item) => item.request_id === editingWakeRequestId) ?? null,
    [detail?.wake_schedules, editingWakeRequestId],
  );
  const mutationLocked = !privilegedUnlocked;
  const groupedToolOptions = useMemo(() => groupTools(catalog?.tool_options ?? []), [catalog?.tool_options]);

  function resetWakeEditor() {
    setEditingWakeRequestId("");
    setWakePrompt("");
    setWakeCron("");
    setWakeResumeAt("");
    setWakeScheduleKind("cron");
    setWakeMode("same_context");
    setWakeEnabled(true);
  }

  function setScheduleKind(kind: WakeScheduleKind) {
    setWakeScheduleKind(kind);
    if (kind === "cron") {
      setWakeResumeAt("");
    } else {
      setWakeCron("");
    }
  }

  function bindWakeSchedule(requestId: string) {
    const item = detail?.wake_schedules.find((schedule) => schedule.request_id === requestId);
    if (!item) return;
    setEditingWakeRequestId(item.request_id);
    setWakePrompt(item.prompt);
    setWakeCron(item.cron ?? "");
    setWakeResumeAt(toDatetimeLocalValue(item.resume_at));
    setWakeScheduleKind(item.schedule_kind === "resume_at" ? "resume_at" : "cron");
    setWakeMode(item.resume_mode);
    setWakeEnabled(item.enabled);
  }

  async function refreshSelectedSessionDetail(sessionId: string) {
    const payload = await fetchJson<SessionDetailResponse>(`/api/admin/sessions/${encodeURIComponent(sessionId)}`);
    setDetail(payload);
    return payload;
  }

  async function runSessionAction(action: "renew" | "revoke" | "delete") {
    if (!selectedSession) return;
    if (action === "renew" && !renewExtension.trim()) {
      setError("Renew extension is required.");
      return;
    }
    if (action === "delete" && !window.confirm(`Delete session ${selectedSession.session_id}? This cannot be undone.`)) {
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
      const nextSelectedSessionId = action === "delete"
        ? pickSessionIdForTab(refreshedSessions, activeTab)
        : pickSessionIdForTab(refreshedSessions, activeTab, selectedSession.session_id);
      if (action === "delete") {
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

  async function saveModel() {
    if (!selectedSession) return;
    if (!modelDraft.trim()) {
      setModelStatus("Model is required.");
      return;
    }
    setBusy("model");
    try {
      const payload = await fetchJson<SessionMutationResponse>(
        `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/model`,
        {
          method: "PUT",
          body: JSON.stringify({ model: modelDraft.trim() }),
        },
      );
      setStatus(payload.message);
      setModelStatus("Saved.");
      setModelDirty(false);
      setModelBoundSessionId(selectedSession.session_id);
      setError(null);
      await refreshSessions();
      await refreshSelectedSessionDetail(selectedSession.session_id);
    } catch (reason) {
      setModelStatus(null);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  function toggleTool(toolValue: string) {
    const wasEnabled = toolsDraft.includes(toolValue);
    setToolsDirty(true);
    setToolsStatus(null);
    setToolsDraft((current) => {
      const next = current.includes(toolValue) ? current.filter((value) => value !== toolValue) : [...current, toolValue];
      return sortTools(next);
    });
    if (wasEnabled) {
      const hadGate = policyDraft.approvalRequiredTools.includes(toolValue);
      if (hadGate) {
        setPolicyDraft((current) => ({
          ...current,
          approvalRequiredTools: current.approvalRequiredTools.filter((value) => value !== toolValue),
        }));
        setPolicyDirty(true);
        setPolicyStatus(null);
      }
    }
  }

  function updatePolicyDraft(update: Partial<SessionPolicyDraft>) {
    setPolicyDraft((current) => ({ ...current, ...update }));
    setPolicyDirty(true);
    setPolicyStatus(null);
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
      return {
        ...current,
        approvalRequiredTools: sortTools(nextTools),
      };
    });
    setPolicyDirty(true);
    setPolicyStatus(null);
  }

  async function saveTools() {
    if (!selectedSession) return;
    setBusy("tools");
    try {
      const payload = await fetchJson<SessionMutationResponse>(
        `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/allowed-tools`,
        {
          method: "PUT",
          body: JSON.stringify({ allowed_tools: toolsDraft }),
        },
      );
      setStatus(payload.message);
      setToolsStatus("Saved.");
      setToolsDirty(false);
      setToolsBoundSessionId(selectedSession.session_id);
      setError(null);
      await refreshSessions();
      await refreshSelectedSessionDetail(selectedSession.session_id);
    } catch (reason) {
      setToolsStatus(null);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function saveSessionSettings() {
    if (!selectedSession || (!modelDirty && !toolsDirty && !policyDirty)) return;
    if (modelDirty && !modelDraft.trim()) {
      setModelStatus("Model is required.");
      return;
    }

    setBusy("settings");
    try {
      if (modelDirty) {
        const payload = await fetchJson<SessionMutationResponse>(
          `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/model`,
          {
            method: "PUT",
            body: JSON.stringify({ model: modelDraft.trim() }),
          },
        );
        setStatus(payload.message);
        setModelStatus("Saved.");
        setModelDirty(false);
        setModelBoundSessionId(selectedSession.session_id);
      }

      if (toolsDirty) {
        const payload = await fetchJson<SessionMutationResponse>(
          `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/allowed-tools`,
          {
            method: "PUT",
            body: JSON.stringify({ allowed_tools: toolsDraft }),
          },
        );
        setStatus(payload.message);
        setToolsStatus("Saved.");
        setToolsDirty(false);
        setToolsBoundSessionId(selectedSession.session_id);
      }

      if (policyDirty) {
        const payload = await fetchJson<SessionMutationResponse>(
          `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}`,
          {
            method: "PATCH",
            body: JSON.stringify({
              allowed_script_names: parseList(policyDraft.allowedScriptNames),
              discord_channel_allowlist: parseList(policyDraft.discordChannelAllowlist),
              teams_channel_allowlist: parseList(policyDraft.teamsChannelAllowlist),
              communication_file_receive_enabled: policyDraft.communicationFileReceiveEnabled,
              communication_file_send_enabled: policyDraft.communicationFileSendEnabled,
              communication_file_max_bytes: parseNullableNonNegativeInteger(
                policyDraft.communicationFileMaxBytes,
                "Communication file max bytes",
              ),
              communication_file_allowed_mime_types: parseList(policyDraft.communicationFileAllowedMimeTypes),
              communication_file_allowed_extensions: parseList(policyDraft.communicationFileAllowedExtensions),
              communication_file_blocked_extensions: parseList(policyDraft.communicationFileBlockedExtensions),
              communication_file_require_approval_for_send: policyDraft.communicationFileRequireApprovalForSend,
              approval_required_tools: sortTools(policyDraft.approvalRequiredTools),
              metadata: parseJsonObject(policyDraft.metadataJson, "Metadata"),
            }),
          },
        );
        setStatus(payload.message);
        setPolicyStatus("Saved.");
        setPolicyDirty(false);
        setPolicyBoundSessionId(selectedSession.session_id);
      }

      setError(null);
      await refreshSessions();
      await refreshSelectedSessionDetail(selectedSession.session_id);
    } catch (reason) {
      if (modelDirty) setModelStatus(null);
      if (toolsDirty) setToolsStatus(null);
      if (policyDirty) setPolicyStatus(null);
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
      bindWakeSchedule(payload.item.request_id);
      setDetail(refreshed);
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
      setDetail(refreshed);
      bindWakeSchedule(item.request_id);
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
      resetWakeEditor();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="feature-page sessions-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Session control</div>
            <h1>Sessions</h1>
          </div>
        </div>
        {mutationLocked ? <div className="callout warning-callout">Unlock privileged operations to renew, revoke, edit, or schedule sessions.</div> : null}
        <label className="field-stack">
          <span>Find session</span>
          <input value={sessionFilter} onChange={(event) => setSessionFilter(event.target.value)} placeholder="session, VM, issue, model" />
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
              onClick={() => setSelectedSessionId(item.session_id)}
            >
              <div className="feature-list-title-row session-list-item-row">
                <Tooltip content={`Session id: ${item.session_id}`}>
                  <strong>{item.session_id}</strong>
                </Tooltip>
                <span className={`status-pill session-state-pill state-${item.is_expired ? "failed" : "running"}`}>{item.is_expired ? "Expired" : "Active"}</span>
              </div>
            </button>
          ))}
          {visibleItems.length === 0 ? <div className="empty-state">No sessions matched the current view.</div> : null}
        </div>
      </section>
      <section className="feature-content">
        <div className="feature-toolbar">
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
              <Tooltip content={selectedSession.is_expired ? "This session has expired and can no longer be resumed until it is renewed." : "This session is active and can still be used by its guest agent."}>
                <span className={`status-pill state-${selectedSession.is_expired ? "failed" : "running"}`}>
                  {selectedSession.is_expired ? "Expired" : "Active"}
                </span>
              </Tooltip>
              <Tooltip content={selectedSession.vm_id ? `Attached VM: ${selectedSession.vm_id}` : "This session is not currently attached to a VM."}>
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
        {status ? <div className="callout success-callout">{status}</div> : null}
        {selectedSession ? (
          <div className="stack-layout">
            <article className="panel">
              <div className="panel-header">
                <h3>Session overview</h3>
              </div>
              <div className="panel-body">
                <dl className="property-grid">
                  <div><dt>VM</dt><dd>{selectedSession.vm_id ?? "None"}</dd></div>
                  <div><dt>Issue</dt><dd>{selectedSession.issue_number ?? "None"}</dd></div>
                  <div><dt>Directory</dt><dd>{selectedSession.directory_path}</dd></div>
                  <div><dt>Model</dt><dd>{selectedSession.model ?? "Unset"}</dd></div>
                  <div><dt>Created</dt><dd>{formatTimestamp(selectedSession.created_at)}</dd></div>
                  <div><dt>Expires</dt><dd>{formatTimestamp(selectedSession.expires_at)}</dd></div>
                </dl>
              </div>
            </article>
            <div className="feature-grid">
              <article className="panel">
                <div className="panel-header">
                  <h3>Model and access</h3>
                  <span className="panel-meta">{modelDirty || toolsDirty || policyDirty ? "Unsaved changes" : "In sync"}</span>
                </div>
                <div className="panel-body session-tools-panel">
                  <label className="field-stack">
                    <span>Model</span>
                    <input
                      list="session-model-suggestions"
                      value={modelDraft}
                      onChange={(event) => {
                        setModelDraft(event.target.value);
                        setModelDirty(true);
                        setModelStatus(null);
                      }}
                      placeholder="Required to start or resume the agent"
                      disabled={mutationLocked}
                    />
                    <datalist id="session-model-suggestions">
                      <option value="gpt-5.4" />
                      <option value="gpt-5.4-mini" />
                      <option value="gpt-5.4-nano" />
                    </datalist>
                  </label>
                  {modelStatus ? <div className="success-box">{modelStatus}</div> : null}
                  <div className="field-stack">
                    <span>Allowed tools</span>
                    <div className="session-tool-groups">
                      {groupedToolOptions.map((group) => {
                        const enabledCount = group.items.filter((tool) => toolsDraft.includes(tool.value)).length;
                        const approvalRequiredCount = group.items.filter(
                          (tool) => toolsDraft.includes(tool.value) && policyDraft.approvalRequiredTools.includes(tool.value),
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
                                const isApprovalRequired = policyDraft.approvalRequiredTools.includes(tool.value);
                                return (
                                  <div key={tool.value} className="session-tool-row">
                                    <label className="session-tool-row-main">
                                      <input
                                        type="checkbox"
                                        checked={isEnabled}
                                        onChange={() => toggleTool(tool.value)}
                                        disabled={mutationLocked}
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
                                      {isApprovalRequired ? (
                                        <span className="status-pill state-idle session-tool-badge">Approval required</span>
                                      ) : null}
                                      <label className="checkbox-row session-tool-approval-row">
                                        <input
                                          type="checkbox"
                                          checked={isApprovalRequired}
                                          onChange={() => toggleApprovalRequirement(tool.value)}
                                          disabled={mutationLocked || !isEnabled}
                                          aria-label={`Require approval for ${tool.label}`}
                                        />
                                        <span>Require approval</span>
                                      </label>
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
                  {toolsStatus ? <div className="success-box">{toolsStatus}</div> : null}
                  <label className="field-stack">
                    <span>Allowed scripts</span>
                    <textarea
                      rows={4}
                      value={policyDraft.allowedScriptNames}
                      onChange={(event) => updatePolicyDraft({ allowedScriptNames: event.target.value })}
                      disabled={mutationLocked}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Discord channel allowlist</span>
                    <textarea
                      rows={4}
                      value={policyDraft.discordChannelAllowlist}
                      onChange={(event) => updatePolicyDraft({ discordChannelAllowlist: event.target.value })}
                      disabled={mutationLocked}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Teams target allowlist</span>
                    <textarea
                      rows={4}
                      value={policyDraft.teamsChannelAllowlist}
                      onChange={(event) => updatePolicyDraft({ teamsChannelAllowlist: event.target.value })}
                      placeholder="team-id/channel-id or chat:<chat-id>"
                      disabled={mutationLocked}
                    />
                  </label>
                  <div className="field-stack session-communication-files">
                    <span>Communication files</span>
                    <div className="session-communication-file-toggles">
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={policyDraft.communicationFileReceiveEnabled}
                          onChange={(event) => updatePolicyDraft({ communicationFileReceiveEnabled: event.target.checked })}
                          disabled={mutationLocked}
                        />
                        <span>Receive inbound files</span>
                      </label>
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={policyDraft.communicationFileSendEnabled}
                          onChange={(event) => updatePolicyDraft({ communicationFileSendEnabled: event.target.checked })}
                          disabled={mutationLocked}
                        />
                        <span>Send files</span>
                      </label>
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={policyDraft.communicationFileRequireApprovalForSend}
                          onChange={(event) => updatePolicyDraft({ communicationFileRequireApprovalForSend: event.target.checked })}
                          disabled={mutationLocked}
                        />
                        <span>Require approval for sends</span>
                      </label>
                    </div>
                  </div>
                  <label className="field-stack">
                    <span>Communication file max bytes</span>
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
                  <label className="field-stack">
                    <span>Allowed MIME types</span>
                    <textarea
                      rows={3}
                      value={policyDraft.communicationFileAllowedMimeTypes}
                      onChange={(event) => updatePolicyDraft({ communicationFileAllowedMimeTypes: event.target.value })}
                      disabled={mutationLocked}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Allowed extensions</span>
                    <textarea
                      rows={3}
                      value={policyDraft.communicationFileAllowedExtensions}
                      onChange={(event) => updatePolicyDraft({ communicationFileAllowedExtensions: event.target.value })}
                      disabled={mutationLocked}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Blocked extensions</span>
                    <textarea
                      rows={3}
                      value={policyDraft.communicationFileBlockedExtensions}
                      onChange={(event) => updatePolicyDraft({ communicationFileBlockedExtensions: event.target.value })}
                      disabled={mutationLocked}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Metadata JSON</span>
                    <textarea
                      rows={8}
                      value={policyDraft.metadataJson}
                      onChange={(event) => updatePolicyDraft({ metadataJson: event.target.value })}
                      disabled={mutationLocked}
                    />
                  </label>
                  {policyStatus ? <div className="success-box">{policyStatus}</div> : null}
                  <div className="feature-toolbar-actions session-panel-actions">
                    <button
                      type="button"
                      className="primary"
                      onClick={() => void saveSessionSettings()}
                      disabled={busy === "settings" || mutationLocked || (!modelDirty && !toolsDirty && !policyDirty)}
                    >
                      {busy === "settings" ? "Saving..." : "Save changes"}
                    </button>
                  </div>
                </div>
              </article>
              <article className="panel">
                <div className="panel-header">
                  <h3>Wake schedules</h3>
                  <div className="feature-toolbar-actions session-wake-actions">
                    <span className="panel-meta">{detail?.wake_schedules.length ?? 0} configured</span>
                    <button type="button" onClick={resetWakeEditor} disabled={mutationLocked || (!editingWakeRequestId && !wakePrompt && !wakeCron && !wakeResumeAt && wakeMode === "same_context" && wakeEnabled)}>
                      Create new
                    </button>
                    {selectedWakeSchedule ? (
                      <button type="button" onClick={() => void toggleWakeScheduleEnabled(selectedWakeSchedule)} disabled={busy === `wake-toggle-${selectedWakeSchedule.request_id}` || mutationLocked}>
                        {selectedWakeSchedule.enabled ? "Disable selected" : "Enable selected"}
                      </button>
                    ) : null}
                  </div>
                </div>
                <div className="panel-body">
                  <label className="field-stack">
                    <span>Prompt</span>
                    <textarea rows={4} value={wakePrompt} onChange={(event) => setWakePrompt(event.target.value)} disabled={mutationLocked} />
                  </label>
                  <div className="field-stack">
                    <span>Schedule type</span>
                    <div className="inline-actions compact-actions session-schedule-type-toggle">
                      <button
                        type="button"
                        className={wakeScheduleKind === "cron" ? "primary" : ""}
                        onClick={() => setScheduleKind("cron")}
                        disabled={mutationLocked}
                      >
                        Recurring
                      </button>
                      <button
                        type="button"
                        className={wakeScheduleKind === "resume_at" ? "primary" : ""}
                        onClick={() => setScheduleKind("resume_at")}
                        disabled={mutationLocked}
                      >
                        One time
                      </button>
                    </div>
                  </div>
                  {wakeScheduleKind === "cron" ? (
                    <label className="field-stack">
                      <span>Cron</span>
                      <input value={wakeCron} onChange={(event) => setWakeCron(event.target.value)} placeholder="*/30 * * * *" disabled={mutationLocked} />
                    </label>
                  ) : (
                    <label className="field-stack">
                      <span>Resume at</span>
                      <input type="datetime-local" value={wakeResumeAt} onChange={(event) => setWakeResumeAt(event.target.value)} disabled={mutationLocked} />
                    </label>
                  )}
                  <label className="field-stack">
                    <span>Resume mode</span>
                    <select value={wakeMode} onChange={(event) => setWakeMode(event.target.value)} disabled={mutationLocked}>
                      {(catalog?.resume_mode_options ?? []).map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="checkbox-row">
                    <input type="checkbox" checked={wakeEnabled} onChange={(event) => setWakeEnabled(event.target.checked)} disabled={mutationLocked} />
                    <span>Enabled</span>
                  </label>
                  <div className="feature-toolbar-actions session-panel-actions">
                    <button
                      type="button"
                      className="primary"
                      onClick={() => void submitWakeSchedule(editingWakeRequestId ? "update" : "create")}
                      disabled={busy === "wake-create" || busy === "wake-update" || mutationLocked}
                    >
                      {editingWakeRequestId ? (busy === "wake-update" ? "Saving..." : "Save schedule") : (busy === "wake-create" ? "Creating..." : "Create wake schedule")}
                    </button>
                    {selectedWakeSchedule ? (
                      <button
                        className="danger"
                        type="button"
                        onClick={() => void deleteWakeSchedule(selectedWakeSchedule)}
                        disabled={busy === `wake-delete-${selectedWakeSchedule.request_id}` || mutationLocked}
                      >
                        {busy === `wake-delete-${selectedWakeSchedule.request_id}` ? "Deleting..." : "Delete selected"}
                      </button>
                    ) : null}
                  </div>
                  <div className="feature-list compact-list">
                    {(detail?.wake_schedules ?? []).map((item) => (
                      <button
                        key={item.request_id}
                        type="button"
                        className={`feature-list-item${editingWakeRequestId === item.request_id ? " selected" : ""}`}
                        onClick={() => bindWakeSchedule(item.request_id)}
                      >
                        <div className="feature-list-title-row">
                          <strong>{item.schedule_kind === "cron" ? item.cron : formatTimestamp(item.resume_at)}</strong>
                          <span className={`status-pill state-${item.enabled ? "running" : "idle"}`}>{item.enabled ? "Enabled" : "Disabled"}</span>
                        </div>
                        <div className="feature-list-meta">{item.prompt}</div>
                        <div className="feature-list-meta">{item.resume_mode}</div>
                      </button>
                    ))}
                    {detail?.wake_schedules.length === 0 ? <div className="empty-state">No wake schedules configured.</div> : null}
                  </div>
                </div>
              </article>
            </div>
            <article className="panel">
              <details className="compact-disclosure">
                <summary className="compact-disclosure-summary feature-list-title-row">
                  <h3>Session detail</h3>
                  <span className="panel-meta">{detailError ? "Unavailable" : "Live summary"}</span>
                </summary>
                <pre className="log-view">
                  {detailError
                    ? detailError
                    : JSON.stringify(detail?.summary_json ?? selectedSession, null, 2)}
                </pre>
              </details>
            </article>
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
          </div>
        ) : (
          <div className="empty-state">Select a session to inspect its details.</div>
        )}
      </section>
    </div>
  );
}
