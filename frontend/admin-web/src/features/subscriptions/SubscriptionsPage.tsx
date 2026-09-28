import { useCallback, useEffect, useRef, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import type {
  CodexSubscriptionProfile,
  CodexSubscriptionProfilesResponse,
} from "../admin/types";

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
        {!privilegedUnlocked ? <p className="callout warning-callout">Unlock privileged operations to create profiles or change account connections.</p> : null}

        <details className="subscription-create">
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
          <div className="subscription-columns" aria-hidden="true"><span>Profile</span><span>Account</span><span>Plan</span><span>Activity</span><span>7-day remaining</span></div>
          {profiles.map((profile) => {
            const login = pendingLogins[profile.profile_id];
            const remainingPercent = weeklyRemainingPercent(profile.rate_limits);
            return (
              <details className="subscription-profile" key={profile.profile_id}>
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
                  <span>{profile.email ?? profile.account_id ?? "Not signed in"}</span>
                  <span>{profile.plan_type ?? profile.account_type ?? "Unknown"}</span>
                  <span>
                    {profile.active_job_count ?? 0} active · {profile.queued_prompt_count ?? 0} queued
                    {profile.error || login?.error ? <small className="error-text">Needs attention</small> : null}
                  </span>
                  <span className="subscription-weekly-remaining">
                    {remainingPercent === null ? "Unavailable" : `${percentFormatter.format(remainingPercent)}%`}
                    <small>7-day remaining</small>
                  </span>
                </summary>
                <div className="subscription-profile-body">
                  <dl className="property-grid">
                    <div>
                      <dt>Account</dt>
                      <dd>{profile.email ?? profile.account_id ?? "Not signed in"}</dd>
                    </div>
                    <div>
                      <dt>Pinned account id</dt>
                      <dd>{profile.pinned_account_id ?? "Unpinned"}</dd>
                    </div>
                    <div>
                      <dt>Plan</dt>
                      <dd>{profile.plan_type ?? profile.account_type ?? "Unknown"}</dd>
                    </div>
                    <div>
                      <dt>Active jobs</dt>
                      <dd>{profile.active_job_count ?? 0}</dd>
                    </div>
                    <div>
                      <dt>Queued prompts</dt>
                      <dd>{profile.queued_prompt_count ?? 0}</dd>
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
                  <div className="inline-actions compact-actions">
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
                    <button
                      className="admin-action-button compact"
                      type="button"
                      disabled={!privilegedUnlocked || !profile.enabled || busy !== null}
                      onClick={() => void startLogin(profile)}
                    >
                      Login
                    </button>
                    <button
                      className="admin-action-button compact danger"
                      type="button"
                      disabled={!privilegedUnlocked || !profile.signed_in || busy !== null}
                      onClick={() => void logout(profile)}
                    >
                      Logout
                    </button>
                  </div>
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
