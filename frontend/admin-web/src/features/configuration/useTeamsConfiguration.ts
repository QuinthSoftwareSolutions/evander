import { useEffect, useRef, useState } from "react";
import { fetchJson } from "../admin/api";

const TEAMS_AUTH_CONFIGURATION_TAB_KEY = "people_communications";
type TeamsAuthStatusView = {
  directoryPath: string;
  backend: string;
  state: string;
  identityId: string | null;
  accountIdentifier: string | null;
  tenantId: string | null;
  scopes: string[];
  message: string | null;
  issuedAt: string | null;
  expiresAt: string | null;
};

type TeamsAuthLoginSessionView = {
  loginSessionId: string;
  directoryPath: string;
  identityId: string | null;
  tenantId: string | null;
  scopes: string[];
  message: string | null;
  verificationUri: string | null;
  verificationUriComplete: string | null;
  userCode: string | null;
  intervalSeconds: number | null;
  createdAt: string | null;
  expiresAt: string | null;
};

export type TeamsChannelLookupResultView = {
  teamId: string;
  channelId: string | null;
  displayName: string;
  membershipType: string | null;
  webUrl: string | null;
  allowlistEntry: string | null;
};

type TeamsChannelLookupView = {
  directoryPath: string;
  backend: string;
  teamId: string;
  channelName: string | null;
  matchCount: number;
  channels: TeamsChannelLookupResultView[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeDirectoryPath(directoryPath: string | null | undefined): string {
  const trimmed = directoryPath?.trim() ?? "";
  return trimmed || "root";
}

function readRecordString(record: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed) {
        return trimmed;
      }
    } else if (typeof value === "number" && Number.isFinite(value)) {
      return String(value);
    }
  }
  return null;
}

function readRecordStringArray(record: Record<string, unknown>, keys: string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) {
      return value
        .map((item) => (typeof item === "string" ? item.trim() : String(item ?? "").trim()))
        .filter(Boolean);
    }
  }
  return [];
}

function readRecordNumber(record: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number.parseFloat(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return null;
}

function readRecordBoolean(record: Record<string, unknown>, keys: string[]): boolean | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "boolean") {
      return value;
    }
    if (typeof value === "string") {
      const normalized = value.trim().toLowerCase();
      if (normalized === "true") return true;
      if (normalized === "false") return false;
    }
  }
  return null;
}

function readNestedRecord(record: Record<string, unknown>, keys: string[]): Record<string, unknown> | null {
  for (const key of keys) {
    const value = record[key];
    if (isRecord(value)) {
      return value;
    }
  }
  return null;
}

function normalizeTeamsAuthState(state: string | null): string {
  const normalized = state?.trim().toLowerCase() ?? "";
  if (!normalized) return "unknown";
  if (normalized === "reauth" || normalized === "reauthorize") return "reauth_required";
  if (normalized === "logged-out") return "logged_out";
  return normalized;
}

export function formatTeamsAuthState(state: string | null): string {
  const normalized = normalizeTeamsAuthState(state);
  if (normalized === "reauth_required") return "Reauth required";
  if (normalized === "logged_out") return "Logged out";
  if (normalized === "authenticated") return "Authenticated";
  if (normalized === "pending" || normalized === "polling") return "Pending";
  if (normalized === "unknown") return "Unknown";
  return normalized.replace(/_/g, " ");
}

function isTeamsAuthPendingState(state: string | null): boolean {
  const normalized = normalizeTeamsAuthState(state);
  return normalized === "pending" || normalized === "polling" || normalized === "in_progress";
}

function normalizeTeamsAuthStatus(value: unknown): TeamsAuthStatusView | null {
  if (!isRecord(value)) return null;
  const source = readNestedRecord(value, ["status", "data", "payload"]) ?? value;
  const state = readRecordString(source, ["state", "status", "auth_state", "authentication_state"]);
  const pending = readRecordBoolean(source, ["pending", "polling", "in_progress"]);
  return {
    directoryPath: normalizeDirectoryPath(readRecordString(source, ["directory_path", "directoryPath"])),
    backend: readRecordString(source, ["backend", "teams_backend", "provider_backend"]) ?? "unknown",
    state: normalizeTeamsAuthState(state ?? (pending ? "pending" : null)),
    identityId: readRecordString(source, ["identity_id", "identityId", "account_identity_id"]),
    accountIdentifier: readRecordString(source, ["account_identifier", "accountIdentifier", "account", "email"]),
    tenantId: readRecordString(source, ["tenant_id", "tenantId"]),
    scopes: readRecordStringArray(source, ["scopes", "granted_scopes", "requested_scopes"]),
    message: readRecordString(source, ["message", "detail", "status_message"]),
    issuedAt: readRecordString(source, ["issued_at", "issuedAt", "created_at", "createdAt"]),
    expiresAt: readRecordString(source, ["expires_at", "expiresAt"]),
  };
}

function normalizeTeamsAuthLoginSession(value: unknown): TeamsAuthLoginSessionView | null {
  if (!isRecord(value)) return null;
  const source = readNestedRecord(value, ["login_session", "session", "data", "payload"]) ?? value;
  const loginSessionId = readRecordString(source, ["login_session_id", "loginSessionId", "session_id", "sessionId"]);
  if (!loginSessionId) return null;
  return {
    loginSessionId,
    directoryPath: normalizeDirectoryPath(readRecordString(source, ["directory_path", "directoryPath"])),
    identityId: readRecordString(source, ["identity_id", "identityId", "account_identity_id"]),
    tenantId: readRecordString(source, ["tenant_id", "tenantId"]),
    scopes: readRecordStringArray(source, ["scopes", "granted_scopes", "requested_scopes"]),
    message: readRecordString(source, ["message", "detail", "status_message"]),
    verificationUri: readRecordString(source, ["verification_uri", "verificationUri"]),
    verificationUriComplete: readRecordString(source, ["verification_uri_complete", "verificationUriComplete"]),
    userCode: readRecordString(source, ["user_code", "userCode"]),
    intervalSeconds: readRecordNumber(source, ["interval_seconds", "intervalSeconds"]),
    createdAt: readRecordString(source, ["created_at", "createdAt"]),
    expiresAt: readRecordString(source, ["expires_at", "expiresAt"]),
  };
}

function normalizeTeamsChannelLookupResult(value: unknown): TeamsChannelLookupView | null {
  if (!isRecord(value)) return null;
  const source = readNestedRecord(value, ["result", "data", "payload"]) ?? value;
  const channels = Array.isArray(source.channels) ? source.channels : [];
  return {
    directoryPath: normalizeDirectoryPath(readRecordString(source, ["directory_path", "directoryPath"])),
    backend: readRecordString(source, ["backend", "teams_backend", "provider_backend"]) ?? "unknown",
    teamId: readRecordString(source, ["team_id", "teamId"]) ?? "",
    channelName: readRecordString(source, ["channel_name", "channelName"]),
    matchCount: readRecordNumber(source, ["match_count", "matchCount"]) ?? channels.length,
    channels: channels.map((item) => {
      const channel = isRecord(item) ? item : {};
      return {
        teamId: readRecordString(channel, ["team_id", "teamId"]) ?? "",
        channelId: readRecordString(channel, ["channel_id", "channelId"]),
        displayName: readRecordString(channel, ["display_name", "displayName"]) ?? "",
        membershipType: readRecordString(channel, ["membership_type", "membershipType"]),
        webUrl: readRecordString(channel, ["web_url", "webUrl"]),
        allowlistEntry: readRecordString(channel, ["allowlist_entry", "allowlistEntry"]),
      };
    }),
  };
}

export function useTeamsConfiguration(selectedDirectoryPath: string, selectedTab: string) {
  const [teamsAuthStatus, setTeamsAuthStatus] = useState<TeamsAuthStatusView | null>(null);
  const [teamsAuthLoginSession, setTeamsAuthLoginSession] = useState<TeamsAuthLoginSessionView | null>(null);
  const [teamsAuthNotice, setTeamsAuthNotice] = useState<{ kind: "success" | "warning" | "info"; message: string } | null>(null);
  const [teamsAuthError, setTeamsAuthError] = useState<string | null>(null);
  const [teamsAuthLoading, setTeamsAuthLoading] = useState(false);
  const [teamsAuthSigningIn, setTeamsAuthSigningIn] = useState(false);
  const [teamsAuthCompleting, setTeamsAuthCompleting] = useState(false);
  const [teamsAuthSigningOut, setTeamsAuthSigningOut] = useState(false);
  const [teamsAuthPollTimeoutSeconds, setTeamsAuthPollTimeoutSeconds] = useState("5");
  const [teamsChannelLookupTeamId, setTeamsChannelLookupTeamId] = useState("");
  const [teamsChannelLookupChannelName, setTeamsChannelLookupChannelName] = useState("");
  const [teamsChannelLookupIncludeAll, setTeamsChannelLookupIncludeAll] = useState(false);
  const [teamsChannelLookupResult, setTeamsChannelLookupResult] = useState<TeamsChannelLookupView | null>(null);
  const [teamsChannelLookupLoading, setTeamsChannelLookupLoading] = useState(false);
  const [teamsChannelLookupError, setTeamsChannelLookupError] = useState<string | null>(null);
  const teamsAuthDirectoryPathRef = useRef(normalizeDirectoryPath(selectedDirectoryPath));
  async function handleTeamsAuthLogin() {
    const directoryPath = normalizeDirectoryPath(selectedDirectoryPath);
    setTeamsAuthSigningIn(true);
    setTeamsAuthError(null);
    setTeamsAuthNotice(null);
    try {
      const payload = await fetchJson<Record<string, unknown>>(
        `/api/teams-auth/login?directory_path=${encodeURIComponent(directoryPath)}`,
        {
          method: "POST",
          body: JSON.stringify({}),
        },
      );
      const nextSession = normalizeTeamsAuthLoginSession(payload);
      if (!nextSession) {
        throw new Error("Teams login did not return a device-code session.");
      }
      setTeamsAuthLoginSession(nextSession);
      setTeamsAuthNotice({
        kind: "info",
        message: "Device-code login started. Open the verification page and enter the code shown below.",
      });
    } catch (reason) {
      setTeamsAuthError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setTeamsAuthSigningIn(false);
    }
  }

  async function handleTeamsAuthComplete() {
    if (!teamsAuthLoginSession) {
      setTeamsAuthError("Start a device-code login before checking its status.");
      return;
    }
    const directoryPath = normalizeDirectoryPath(selectedDirectoryPath);
    const timeoutValue = Number.parseInt(teamsAuthPollTimeoutSeconds, 10);
    const timeoutSeconds = Number.isFinite(timeoutValue) && timeoutValue > 0 ? Math.min(timeoutValue, 30) : undefined;
    setTeamsAuthCompleting(true);
    setTeamsAuthError(null);
    try {
      const payload = await fetchJson<Record<string, unknown>>(
        `/api/teams-auth/complete?directory_path=${encodeURIComponent(directoryPath)}`,
        {
          method: "POST",
          body: JSON.stringify({
            login_session_id: teamsAuthLoginSession.loginSessionId,
            ...(timeoutSeconds ? { timeout_seconds: timeoutSeconds } : {}),
          }),
        },
      );
      const nextStatus = normalizeTeamsAuthStatus(payload);
      if (nextStatus && isTeamsAuthPendingState(nextStatus.state)) {
        setTeamsAuthStatus(nextStatus);
        setTeamsAuthNotice({
          kind: "warning",
          message: nextStatus.message ?? "Teams sign-in is still pending. Try again in a few seconds.",
        });
        return;
      }
      setTeamsAuthStatus(nextStatus);
      setTeamsAuthLoginSession(null);
      setTeamsAuthNotice({
        kind: "success",
        message: nextStatus?.message ?? "Teams sign-in completed.",
      });
      await refreshTeamsAuthStatus(directoryPath);
    } catch (reason) {
      setTeamsAuthError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setTeamsAuthCompleting(false);
    }
  }

  async function handleTeamsAuthLogout() {
    const directoryPath = normalizeDirectoryPath(selectedDirectoryPath);
    setTeamsAuthSigningOut(true);
    setTeamsAuthError(null);
    try {
      const payload = await fetchJson<Record<string, unknown>>(
        `/api/teams-auth/logout?directory_path=${encodeURIComponent(directoryPath)}`,
        {
          method: "POST",
          body: JSON.stringify({}),
        },
      );
      setTeamsAuthStatus(normalizeTeamsAuthStatus(payload));
      setTeamsAuthLoginSession(null);
      setTeamsAuthNotice({
        kind: "success",
        message: `Teams auth signed out for ${directoryPath}.`,
      });
      await refreshTeamsAuthStatus(directoryPath);
    } catch (reason) {
      setTeamsAuthError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setTeamsAuthSigningOut(false);
    }
  }

  async function refreshTeamsAuthStatus(directoryPath: string) {
    const normalizedDirectoryPath = normalizeDirectoryPath(directoryPath);
    setTeamsAuthLoading(true);
    try {
      const payload = await fetchJson<Record<string, unknown>>(
        `/api/teams-auth/status?directory_path=${encodeURIComponent(normalizedDirectoryPath)}`,
      );
      const nextStatus = normalizeTeamsAuthStatus(payload);
      setTeamsAuthStatus(nextStatus);
      setTeamsAuthError(null);
      return nextStatus;
    } catch (reason) {
      setTeamsAuthError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setTeamsAuthLoading(false);
    }
  }

  async function handleTeamsChannelLookup() {
    const directoryPath = normalizeDirectoryPath(selectedDirectoryPath);
    const teamId = teamsChannelLookupTeamId.trim();
    const channelName = teamsChannelLookupIncludeAll ? teamsChannelLookupChannelName.trim() || null : teamsChannelLookupChannelName.trim();
    if (!teamId) {
      setTeamsChannelLookupError("Team id is required.");
      setTeamsChannelLookupResult(null);
      return;
    }
    if (!teamsChannelLookupIncludeAll && !channelName) {
      setTeamsChannelLookupError("Channel name is required unless all channels are requested.");
      setTeamsChannelLookupResult(null);
      return;
    }
    setTeamsChannelLookupLoading(true);
    setTeamsChannelLookupError(null);
    try {
      const query = new URLSearchParams({
        directory_path: directoryPath,
        team_id: teamId,
        include_all: String(teamsChannelLookupIncludeAll),
      });
      if (channelName) {
        query.set("channel_name", channelName);
      }
      const payload = await fetchJson<Record<string, unknown>>(`/api/teams-auth/channels?${query.toString()}`);
      const nextResult = normalizeTeamsChannelLookupResult(payload);
      if (!nextResult) {
        throw new Error("Teams channel lookup returned an unexpected payload.");
      }
      setTeamsChannelLookupResult(nextResult);
    } catch (reason) {
      setTeamsChannelLookupResult(null);
      setTeamsChannelLookupError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setTeamsChannelLookupLoading(false);
    }
  }

  useEffect(() => {
    async function refresh() {
      if (selectedTab !== TEAMS_AUTH_CONFIGURATION_TAB_KEY) {
        return;
      }
      const directoryPath = normalizeDirectoryPath(selectedDirectoryPath);
      const previousDirectoryPath = teamsAuthDirectoryPathRef.current;
      teamsAuthDirectoryPathRef.current = directoryPath;
      if (previousDirectoryPath !== directoryPath) {
        setTeamsAuthStatus(null);
        setTeamsAuthLoginSession(null);
        setTeamsAuthNotice(null);
      }
      setTeamsAuthError(null);
      await refreshTeamsAuthStatus(directoryPath);
    }

    void refresh();
    return () => {};
  }, [selectedDirectoryPath, selectedTab]);

  useEffect(() => {
    setTeamsChannelLookupResult(null);
    setTeamsChannelLookupError(null);
  }, [selectedDirectoryPath]);

  const teamsAuthBusy = teamsAuthLoading || teamsAuthSigningIn || teamsAuthCompleting || teamsAuthSigningOut;
  const teamsAuthDirectoryPath = normalizeDirectoryPath(selectedDirectoryPath);
  return { teamsAuthStatus, setTeamsAuthStatus, teamsAuthLoginSession, setTeamsAuthLoginSession, teamsAuthNotice, setTeamsAuthNotice, teamsAuthError, setTeamsAuthError, teamsAuthLoading, setTeamsAuthLoading, teamsAuthSigningIn, setTeamsAuthSigningIn, teamsAuthCompleting, setTeamsAuthCompleting, teamsAuthSigningOut, setTeamsAuthSigningOut, teamsAuthPollTimeoutSeconds, setTeamsAuthPollTimeoutSeconds, teamsChannelLookupTeamId, setTeamsChannelLookupTeamId, teamsChannelLookupChannelName, setTeamsChannelLookupChannelName, teamsChannelLookupIncludeAll, setTeamsChannelLookupIncludeAll, teamsChannelLookupResult, setTeamsChannelLookupResult, teamsChannelLookupLoading, setTeamsChannelLookupLoading, teamsChannelLookupError, setTeamsChannelLookupError, teamsAuthBusy, teamsAuthDirectoryPath, handleTeamsAuthLogin, handleTeamsAuthComplete, handleTeamsAuthLogout, refreshTeamsAuthStatus, handleTeamsChannelLookup };
}
