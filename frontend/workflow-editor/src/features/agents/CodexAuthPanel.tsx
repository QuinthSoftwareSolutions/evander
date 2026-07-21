import { useCallback, useEffect, useState } from "react";

import { fetchJson } from "../admin/api";

type CodexAuthStatus = {
  available: boolean;
  signed_in: boolean;
  account_type: string | null;
  email: string | null;
  plan_type: string | null;
  last_refresh: string | null;
  access_token_expires_at: string | null;
  error: string | null;
};

type DeviceLogin = {
  login_id: string;
  status: "pending" | "completed" | "failed" | "cancelled" | string;
  verification_url: string | null;
  user_code: string | null;
  error: string | null;
};

export function CodexAuthPanel({
  directoryPath,
  privilegedUnlocked,
}: {
  directoryPath: string;
  privilegedUnlocked: boolean;
}) {
  const [status, setStatus] = useState<CodexAuthStatus | null>(null);
  const [login, setLogin] = useState<DeviceLogin | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const suffix = useCallback(() => {
    const query = new URLSearchParams({ directory_path: directoryPath || "root" });
    return `?${query.toString()}`;
  }, [directoryPath]);

  const refresh = useCallback(async () => {
    if (!privilegedUnlocked) {
      setStatus(null);
      return;
    }
    setLoading(true);
    try {
      const payload = await fetchJson<CodexAuthStatus>(`/api/admin/codex-auth${suffix()}`);
      setStatus(payload);
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [privilegedUnlocked, suffix]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!privilegedUnlocked || !login || login.status !== "pending") return;
    const timer = window.setInterval(() => {
      void fetchJson<DeviceLogin>(
        `/api/admin/codex-auth/device-login/${encodeURIComponent(login.login_id)}${suffix()}`,
      )
        .then((payload) => {
          setLogin(payload);
          if (payload.status === "completed") void refresh();
        })
        .catch((reason: unknown) => {
          setError(reason instanceof Error ? reason.message : String(reason));
        });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [login, privilegedUnlocked, refresh, suffix]);

  async function startLogin() {
    setLoading(true);
    try {
      const payload = await fetchJson<DeviceLogin>(
        `/api/admin/codex-auth/device-login${suffix()}`,
        { method: "POST" },
      );
      setLogin(payload);
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function cancelLogin() {
    if (!login) return;
    setLoading(true);
    try {
      const payload = await fetchJson<DeviceLogin>(
        `/api/admin/codex-auth/device-login/${encodeURIComponent(login.login_id)}${suffix()}`,
        { method: "DELETE" },
      );
      setLogin(payload);
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function logout() {
    if (!window.confirm("Sign this worker out of the ChatGPT subscription account?")) return;
    setLoading(true);
    try {
      const payload = await fetchJson<CodexAuthStatus>(`/api/admin/codex-auth${suffix()}`, {
        method: "DELETE",
      });
      setStatus(payload);
      setLogin(null);
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  const summary = !privilegedUnlocked
    ? "Unlock required"
    : status?.signed_in
      ? `${status.email ?? "Signed in"}${status.plan_type ? ` / ${status.plan_type}` : ""}`
      : status?.available === false
        ? "Unavailable"
        : "Not signed in";

  return (
    <details className="panel compact-disclosure">
      <summary className="panel-header compact-disclosure-summary">
        <span>Worker ChatGPT subscription</span>
        <span className="panel-meta">{loading ? "Checking..." : summary}</span>
      </summary>
      <div className="panel-body field-stack">
        {!privilegedUnlocked ? (
          <div className="callout warning-callout">Unlock privileged operations to view or change the worker account.</div>
        ) : null}
        {error || status?.error ? <div className="callout error-callout">{error ?? status?.error}</div> : null}
        {status?.signed_in ? (
          <p className="muted-text">
            Subscription turns use this worker-owned account. Metered turns continue to use the configured relay.
          </p>
        ) : null}
        {login?.status === "pending" ? (
          <div className="callout">
            <p>Open the verification page and enter code <strong>{login.user_code}</strong>.</p>
            <div className="inline-actions compact-actions">
              {login.verification_url ? (
                <a className="admin-action-button compact" href={login.verification_url} target="_blank" rel="noreferrer">
                  Open verification page
                </a>
              ) : null}
              <button type="button" className="admin-action-button compact" onClick={() => void cancelLogin()} disabled={loading}>
                Cancel
              </button>
            </div>
          </div>
        ) : null}
        {login?.status === "failed" ? <div className="callout error-callout">{login.error ?? "Device login failed."}</div> : null}
        <div className="inline-actions compact-actions">
          {!status?.signed_in ? (
            <button type="button" className="admin-action-button primary compact" onClick={() => void startLogin()} disabled={!privilegedUnlocked || loading}>
              Sign in with device code
            </button>
          ) : (
            <button type="button" className="admin-action-button danger compact" onClick={() => void logout()} disabled={!privilegedUnlocked || loading}>
              Sign out
            </button>
          )}
          <button type="button" className="admin-action-button compact" onClick={() => void refresh()} disabled={!privilegedUnlocked || loading}>
            Refresh
          </button>
        </div>
      </div>
    </details>
  );
}
