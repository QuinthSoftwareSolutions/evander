import { useCallback, useEffect, useRef, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import type {
  CodexSubscriptionProfile,
  CodexSubscriptionProfilesResponse,
  CodexSubscriptionResetResponse,
} from "../admin/types";
import {
  forgetResetRedemptionAttempt,
  loadResetRedemptionAttempts,
  rememberResetRedemptionAttempt,
  RESET_REDEMPTION_STORAGE_EVENT,
  resetRedemptionKey,
  type ResetRedemptionAttempts,
} from "./resetRedemption";

type DeviceLogin = {
  login_id: string;
  status: string;
  verification_url?: string;
  user_code?: string;
  error?: string | null;
};

const SEVEN_DAYS_IN_MINUTES = 7 * 24 * 60;

function weeklyRemainingPercent(rateLimits: Record<string, unknown> | null): number | null {
  const byLimitId = rateLimits?.by_limit_id;
  const codexLimit = byLimitId && typeof byLimitId === "object"
    ? (byLimitId as Record<string, unknown>).codex
    : null;
  for (const bucket of [rateLimits?.default, codexLimit]) {
    if (!bucket || typeof bucket !== "object") continue;
    for (const windowName of ["primary", "secondary"] as const) {
      const window = (bucket as Record<string, unknown>)[windowName];
      if (!window || typeof window !== "object") continue;
      const { window_duration_minutes: duration, used_percent: used } = window as Record<string, unknown>;
      if (duration !== SEVEN_DAYS_IN_MINUTES || typeof used !== "number" || !Number.isFinite(used)) continue;
      return Math.max(0, Math.min(100, 100 - used));
    }
  }
  return null;
}

const percentFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

function bankedResetCount(rateLimits: Record<string, unknown> | null): number | null {
  const credits = rateLimits?.reset_credits;
  if (!credits || typeof credits !== "object") return null;
  const count = (credits as Record<string, unknown>).available_count;
  return typeof count === "number" && Number.isSafeInteger(count) && count >= 0 ? count : null;
}

function newRedemptionId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // Admin Web can also run over HTTP on a private host, where randomUUID is
  // unavailable. getRandomValues still provides a UUID for retry identity.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function SubscriptionsPage() {
  const { selectedDirectoryPath, privilegedUnlocked } = useAdminUi();
  const refreshGeneration = useRef(0);
  const [loading, setLoading] = useState(true);
  const [profiles, setProfiles] = useState<CodexSubscriptionProfile[]>([]);
  const [workerUrl, setWorkerUrl] = useState<string | null>(null);
  const [profileId, setProfileId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [pendingLogins, setPendingLogins] = useState<Record<string, DeviceLogin>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [resetAttempts, setResetAttempts] = useState<ResetRedemptionAttempts>({});
  const [resetStorageError, setResetStorageError] = useState<string | null>(null);
  const redemptionInFlight = useRef(false);

  const syncResetAttempts = useCallback(() => {
    try {
      setResetAttempts(loadResetRedemptionAttempts());
      setResetStorageError(null);
    } catch (reason: unknown) {
      setResetStorageError(`Reset redemption is unavailable because saved retry information cannot be read: ${reason instanceof Error ? reason.message : String(reason)}`);
    }
  }, []);

  useEffect(() => {
    syncResetAttempts();
    window.addEventListener("storage", syncResetAttempts);
    window.addEventListener(RESET_REDEMPTION_STORAGE_EVENT, syncResetAttempts);
    return () => {
      window.removeEventListener("storage", syncResetAttempts);
      window.removeEventListener(RESET_REDEMPTION_STORAGE_EVENT, syncResetAttempts);
    };
  }, [syncResetAttempts]);

  const query = useCallback(() => {
    const params = new URLSearchParams({ directory_path: selectedDirectoryPath || "root" });
    return `?${params.toString()}`;
  }, [selectedDirectoryPath]);

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    setLoading(true);
    try {
      const payload = await fetchJson<CodexSubscriptionProfilesResponse>(
        `/api/admin/codex-subscriptions${query()}`,
      );
      if (generation !== refreshGeneration.current) return;
      setProfiles(payload.profiles);
      setWorkerUrl(payload.worker_url);
      setError(null);
    } catch (reason: unknown) {
      if (generation === refreshGeneration.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (generation === refreshGeneration.current) setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    setProfiles([]);
    setWorkerUrl(null);
    setPendingLogins({});
    setNotice(null);
    void refresh();
    return () => { refreshGeneration.current += 1; };
  }, [refresh]);

  useEffect(() => {
    const active = Object.entries(pendingLogins).filter(([, login]) => login.status === "pending");
    if (!active.length) return undefined;
    const timer = window.setInterval(() => {
      for (const [pendingProfileId, login] of active) {
        void fetchJson<DeviceLogin>(
          `/api/admin/codex-subscriptions/${encodeURIComponent(pendingProfileId)}/device-login/${encodeURIComponent(login.login_id)}${query()}`,
        ).then((next) => {
          setPendingLogins((current) => ({ ...current, [pendingProfileId]: next }));
          if (next.status !== "pending") void refresh();
        }).catch(() => undefined);
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [pendingLogins, query, refresh]);

  async function createProfile() {
    setBusy("create");
    try {
      await fetchJson(`/api/admin/codex-subscriptions${query()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: profileId.trim(), display_name: displayName.trim() }),
      });
      setProfileId("");
      setDisplayName("");
      await refresh();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function patchProfile(profile: CodexSubscriptionProfile, update: Record<string, unknown>) {
    setBusy(`patch:${profile.profile_id}`);
    try {
      await fetchJson(
        `/api/admin/codex-subscriptions/${encodeURIComponent(profile.profile_id)}${query()}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(update),
        },
      );
      await refresh();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function renameProfile(profile: CodexSubscriptionProfile) {
    const nextName = window.prompt("Profile display name", profile.display_name)?.trim();
    if (nextName && nextName !== profile.display_name) {
      await patchProfile(profile, { display_name: nextName });
    }
  }

  async function startLogin(profile: CodexSubscriptionProfile) {
    setBusy(`login:${profile.profile_id}`);
    try {
      const login = await fetchJson<DeviceLogin>(
        `/api/admin/codex-subscriptions/${encodeURIComponent(profile.profile_id)}/device-login${query()}`,
        { method: "POST" },
      );
      setPendingLogins((current) => ({ ...current, [profile.profile_id]: login }));
      if (login.verification_url) window.open(login.verification_url, "_blank", "noopener,noreferrer");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function logout(profile: CodexSubscriptionProfile) {
    setBusy(`logout:${profile.profile_id}`);
    try {
      await fetchJson(
        `/api/admin/codex-subscriptions/${encodeURIComponent(profile.profile_id)}/session${query()}`,
        { method: "DELETE" },
      );
      await refresh();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  function resetAttemptKey(profile: CodexSubscriptionProfile): string {
    return resetRedemptionKey(workerUrl, profile.profile_id, profile.account_id);
  }

  async function redeemReset(profile: CodexSubscriptionProfile) {
    if (redemptionInFlight.current || !privilegedUnlocked || !workerUrl || !profile.account_id) return;
    redemptionInFlight.current = true;
    const generation = refreshGeneration.current;
    let submitted = false;
    try {
      const previousAttempt = loadResetRedemptionAttempts()[resetAttemptKey(profile)];
      if (!previousAttempt && !window.confirm(`Redeem one banked reset for ${profile.display_name}? This consumes one available reset.`)) return;
      const attempt = rememberResetRedemptionAttempt(previousAttempt ?? {
        worker_url: workerUrl, profile_id: profile.profile_id,
        account_id: profile.account_id, idempotency_key: newRedemptionId(),
      });
      setBusy(`reset:${profile.profile_id}`);
      setError(null);
      setNotice(null);
      submitted = true;
      const result = await fetchJson<CodexSubscriptionResetResponse>(
        `/api/admin/codex-subscriptions/${encodeURIComponent(profile.profile_id)}/reset-credits/consume${query()}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            idempotency_key: attempt.idempotency_key,
            expected_worker_url: attempt.worker_url,
            expected_account_id: attempt.account_id,
          }),
        },
      );
      if (!["reset", "alreadyRedeemed", "nothingToReset", "noCredit"].includes(result?.outcome) || result?.profile?.profile_id !== profile.profile_id) {
        throw new Error("Reset redemption returned an incomplete response");
      }
      forgetResetRedemptionAttempt(attempt);
      if (generation !== refreshGeneration.current) return;
      setProfiles((current) => current.map((item) => item.profile_id === profile.profile_id ? result.profile : item));
      const message = {
        reset: "One banked reset redeemed. Usage and reset count refreshed.",
        alreadyRedeemed: "This reset was already redeemed. Usage and reset count refreshed.",
        nothingToReset: "There is no eligible usage window to reset. No reset was consumed.",
        noCredit: "There are no banked resets available.",
      }[result.outcome];
      setNotice(`${profile.display_name}: ${message}`);
    } catch (reason: unknown) {
      if (generation === refreshGeneration.current) {
        const message = reason instanceof Error ? reason.message : String(reason);
        setError(submitted
          ? `${message}. Retry reset redemption to check the same attempt.`
          : `Reset redemption was not submitted: ${message}`);
      }
    } finally {
      redemptionInFlight.current = false;
      setBusy(null);
    }
  }

  return (
    <div className="feature-page subscriptions-page">
      <section className="feature-content">
        <div className="panel-header">
          <div>
            <h1>Subscriptions</h1>
            <p className="muted-text">{loading && !workerUrl ? "Loading subscription profiles…" : error && !workerUrl ? "Subscription profiles unavailable" : workerUrl ?? "No container worker configured"}</p>
          </div>
          <button className="admin-action-button compact" type="button" disabled={loading || busy !== null} onClick={() => void refresh()}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        {error ? <div className="callout error-callout">{error}</div> : null}
        {resetStorageError ? <div className="callout error-callout" role="alert">{resetStorageError}</div> : null}
        {notice ? <div className="callout" role="status">{notice}</div> : null}
        {!privilegedUnlocked ? <p className="callout warning-callout">Unlock privileged operations to create profiles, change account connections, or redeem resets.</p> : null}

        <details className="subscription-create" open={!loading && profiles.length === 0}>
          <summary>Create profile</summary>
          <div className="config-field-grid">
            <label className="field-stack">
              <span>Profile ID (cannot be changed later)</span>
              <input value={profileId} onChange={(event) => setProfileId(event.target.value)} placeholder="team-plus" />
            </label>
            <label className="field-stack">
              <span>Display name</span>
              <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Team Plus" />
            </label>
          </div>
          <button
            className="admin-action-button primary compact"
            type="button"
            disabled={!privilegedUnlocked || !profileId.trim() || !displayName.trim() || busy !== null || loading}
            onClick={() => void createProfile()}
          >
            {busy === "create" ? "Creating…" : "Create profile"}
          </button>
        </details>

        <div className="subscription-profiles">
          <div className="subscription-columns" aria-hidden="true"><span>Profile</span><span>Account</span><span>Plan</span><span>Activity</span><span>7-day remaining</span><span>Banked resets</span></div>
          {profiles.map((profile) => {
            const login = pendingLogins[profile.profile_id];
            const remainingPercent = weeklyRemainingPercent(profile.rate_limits);
            const resetCount = bankedResetCount(profile.rate_limits);
            const retryReset = Boolean(resetAttempts[resetAttemptKey(profile)]);
            return (
              <details className="subscription-profile" key={profile.profile_id} open={Boolean(login || profile.error)}>
                <summary className="subscription-profile-summary">
                  <span>
                    <strong>{profile.display_name}</strong>
                    <small>{profile.profile_id}</small>
                    <span
                      className={`status-pill ${profile.enabled && profile.signed_in ? "state-running" : "state-idle"}`}
                    >
                      {!profile.enabled ? "Disabled" : profile.signed_in ? "Signed in" : "Signed out"}
                    </span>
                  </span>
                  <span>{profile.signed_in ? profile.email ?? profile.account_id ?? "Signed in" : (
                    <button
                      className="admin-action-button compact"
                      type="button"
                      disabled={!privilegedUnlocked || !profile.enabled || busy !== null}
                      onClick={(event) => { event.preventDefault(); event.stopPropagation(); void startLogin(profile); }}
                    >
                      {busy === `login:${profile.profile_id}` ? "Starting login…" : "Login"}
                    </button>
                  )}</span>
                  <span>{profile.plan_type ?? profile.account_type ?? "Unknown"}</span>
                  <span>
                    {profile.active_job_count ?? 0} active · {profile.queued_prompt_count ?? 0} queued
                    {profile.error || login?.error ? <small className="error-text">Needs attention</small> : null}
                  </span>
                  <span className="subscription-weekly-remaining">
                    {remainingPercent === null ? "Unavailable" : `${percentFormatter.format(remainingPercent)}%`}
                    <small>7-day remaining</small>
                  </span>
                  <span className="subscription-banked-resets">
                    {resetCount === null ? "Unavailable" : resetCount}
                    <small>Banked resets</small>
                  </span>
                </summary>
                <div className="subscription-profile-body">
                  <div className="inline-actions compact-actions">
                    <button
                      className="admin-action-button compact"
                      type="button"
                      disabled={!privilegedUnlocked || !profile.enabled || !profile.signed_in || !profile.available || !workerUrl || !profile.account_id || resetStorageError !== null || busy !== null || loading || (!retryReset && (resetCount === null || resetCount === 0))}
                      onClick={() => void redeemReset(profile)}
                    >
                      {busy === `reset:${profile.profile_id}` ? "Redeeming…" : retryReset ? "Retry reset redemption" : "Redeem one reset"}
                    </button>
                    <button
                      className="admin-action-button compact"
                      type="button"
                      disabled={!privilegedUnlocked || busy !== null}
                      onClick={() => void renameProfile(profile)}
                    >
                      Rename
                    </button>
                    <button
                      className="admin-action-button compact"
                      type="button"
                      disabled={!privilegedUnlocked || busy !== null}
                      onClick={() => void patchProfile(profile, { enabled: !profile.enabled })}
                    >
                      {profile.enabled ? "Disable" : "Enable"}
                    </button>
                    {profile.signed_in ? <button
                      className="admin-action-button compact"
                      type="button"
                      disabled={!privilegedUnlocked || !profile.enabled || busy !== null}
                      onClick={() => void startLogin(profile)}
                    >
                      Reconnect account
                    </button> : null}
                    <button
                      className="admin-action-button compact danger"
                      type="button"
                      disabled={!privilegedUnlocked || !profile.signed_in || busy !== null}
                      onClick={() => void logout(profile)}
                    >
                      Logout
                    </button>
                  </div>
                  <dl className="property-grid">
                    <div>
                      <dt>Pinned account id</dt>
                      <dd>{profile.pinned_account_id ?? "Unpinned"}</dd>
                    </div>
                    <div>
                      <dt>Last refresh</dt>
                      <dd>{formatTimestamp(profile.last_refresh)}</dd>
                    </div>
                  </dl>
                  {profile.error ? <div className="callout error-callout">{profile.error}</div> : null}
                  {login ? (
                    <div className="callout">
                      Login: {login.status}. {login.user_code ? `Code ${login.user_code}.` : ""}
                      {login.verification_url ? (
                        <a href={login.verification_url} target="_blank" rel="noreferrer">
                          {" "}
                          Open login
                        </a>
                      ) : null}
                      {login.error ? ` ${login.error}` : ""}
                    </div>
                  ) : null}
                  {profile.rate_limits ? (
                    <details className="compact-disclosure">
                      <summary>Rate limits</summary>
                      <pre className="log-view compact-log-view">{JSON.stringify(profile.rate_limits, null, 2)}</pre>
                    </details>
                  ) : null}
                  {profile.usage ? (
                    <details className="compact-disclosure">
                      <summary>Usage</summary>
                      <pre className="log-view compact-log-view">{JSON.stringify(profile.usage, null, 2)}</pre>
                    </details>
                  ) : null}

                </div>
              </details>
            );
          })}
          {loading && !profiles.length ? <div className="empty-state" role="status">Loading subscription profiles…</div> : null}
          {!loading && !error && !profiles.length ? <div className="empty-state">No subscription profiles are available. Create a profile to connect an account.</div> : null}
        </div>
      </section>
    </div>
  );
}
