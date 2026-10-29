import { formatRelativeSeconds, formatTimestamp } from "../admin/api";
import { formatTeamsAuthState, type useTeamsConfiguration } from "./useTeamsConfiguration";

export function TeamsAuthConfiguration({ auth, loading, saving, mutationLocked }: {
  auth: ReturnType<typeof useTeamsConfiguration>;
  loading: boolean;
  saving: boolean;
  mutationLocked: boolean;
}) {
  const {
    teamsAuthStatus,
    teamsAuthLoginSession,
    teamsAuthNotice,
    teamsAuthError,
    teamsAuthLoading,
    teamsAuthSigningIn,
    teamsAuthCompleting,
    teamsAuthSigningOut,
    teamsAuthPollTimeoutSeconds,
    setTeamsAuthPollTimeoutSeconds,
    teamsAuthDirectoryPath,
    handleTeamsAuthLogin,
    handleTeamsAuthComplete,
    handleTeamsAuthLogout,
    refreshTeamsAuthStatus,
  } = auth;
  return (
    <article className="panel detail-panel configuration-summary-panel teams-auth-panel">
      <div className="panel-header">
        <div>
          <h3>Teams delegated auth</h3>
          <p className="muted-text">
            Inspect the host-side Microsoft Teams auth state, start device-code sign-in, and sign out without exposing credentials.
          </p>
        </div>
        <span className="panel-meta">{teamsAuthDirectoryPath}</span>
      </div>
      {teamsAuthNotice ? (
        <div
          className={`callout ${
            teamsAuthNotice.kind === "warning"
              ? "warning-callout"
              : teamsAuthNotice.kind === "info"
                ? "warning-callout"
                : "success-callout"
          }`}
        >
          {teamsAuthNotice.message}
        </div>
      ) : null}
      {teamsAuthError ? <div className="callout error-callout">{teamsAuthError}</div> : null}
      {teamsAuthLoading ? <div className="callout warning-callout">Refreshing Teams auth status...</div> : null}
      <div className="detail-stack compact-detail-stack">
        <div className="detail-grid teams-auth-summary-grid">
          <div>
            <span>Directory</span>
            <strong>{teamsAuthDirectoryPath}</strong>
          </div>
          <div>
            <span>Backend</span>
            <strong>{teamsAuthStatus?.backend ?? "n/a"}</strong>
          </div>
          <div>
            <span>State</span>
            <strong>{formatTeamsAuthState(teamsAuthStatus?.state ?? null)}</strong>
          </div>
          <div>
            <span>Identity</span>
            <strong>{teamsAuthStatus?.identityId ?? teamsAuthStatus?.accountIdentifier ?? "n/a"}</strong>
          </div>
          <div>
            <span>Tenant</span>
            <strong>{teamsAuthStatus?.tenantId ?? "n/a"}</strong>
          </div>
          <div>
            <span>Scopes</span>
            <strong>{teamsAuthStatus?.scopes.length ? teamsAuthStatus.scopes.join(", ") : "n/a"}</strong>
          </div>
          <div>
            <span>Issued</span>
            <strong>{formatTimestamp(teamsAuthStatus?.issuedAt)}</strong>
          </div>
          <div>
            <span>Expires</span>
            <strong>{formatTimestamp(teamsAuthStatus?.expiresAt)}</strong>
          </div>
        </div>
      </div>
      <div className="inline-actions compact-actions teams-auth-actions">
        <button
          type="button"
          onClick={() => void refreshTeamsAuthStatus(teamsAuthDirectoryPath)}
          disabled={loading || saving || teamsAuthLoading || teamsAuthSigningIn || teamsAuthCompleting || teamsAuthSigningOut}
        >
          {teamsAuthLoading ? "Refreshing..." : "Refresh status"}
        </button>
        <button
          type="button"
          className="primary"
          onClick={() => void handleTeamsAuthLogin()}
          disabled={mutationLocked || teamsAuthLoading || teamsAuthSigningIn || teamsAuthCompleting || teamsAuthSigningOut || Boolean(teamsAuthLoginSession)}
        >
          {teamsAuthSigningIn ? "Starting..." : "Start sign-in"}
        </button>
        <label className="field-stack teams-auth-timeout-field">
          <span>Poll timeout</span>
          <input
            type="number"
            min="1"
            max="30"
            value={teamsAuthPollTimeoutSeconds}
            onChange={(event) => setTeamsAuthPollTimeoutSeconds(event.target.value)}
            disabled={mutationLocked || teamsAuthLoading || teamsAuthSigningIn || teamsAuthCompleting || teamsAuthSigningOut}
          />
        </label>
        <button
          type="button"
          onClick={() => void handleTeamsAuthComplete()}
          disabled={mutationLocked || teamsAuthLoading || teamsAuthCompleting || teamsAuthSigningIn || teamsAuthSigningOut || !teamsAuthLoginSession}
        >
          {teamsAuthCompleting ? "Checking..." : "Check sign-in status"}
        </button>
        <button
          type="button"
          className="danger"
          onClick={() => void handleTeamsAuthLogout()}
          disabled={mutationLocked || teamsAuthLoading || teamsAuthSigningIn || teamsAuthCompleting || teamsAuthSigningOut || (!teamsAuthStatus && !teamsAuthLoginSession)}
        >
          {teamsAuthSigningOut ? "Signing out..." : "Sign out"}
        </button>
      </div>
      {teamsAuthLoginSession ? (
        <div className="teams-auth-session-card">
          <div className="panel-header">
            <div>
              <h4>Device-code login</h4>
              <p className="muted-text">
                Use the Microsoft verification page from another browser or device, then check sign-in status here.
              </p>
            </div>
            <span className="panel-meta">{teamsAuthLoginSession.loginSessionId}</span>
          </div>
          {teamsAuthLoginSession.message ? <div className="callout warning-callout">{teamsAuthLoginSession.message}</div> : null}
          <div className="detail-grid teams-auth-session-grid">
            <div>
              <span>Directory</span>
              <strong>{teamsAuthLoginSession.directoryPath}</strong>
            </div>
            <div>
              <span>Identity</span>
              <strong>{teamsAuthLoginSession.identityId ?? "n/a"}</strong>
            </div>
            <div>
              <span>Tenant</span>
              <strong>{teamsAuthLoginSession.tenantId ?? "n/a"}</strong>
            </div>
            <div>
              <span>Scopes</span>
              <strong>{teamsAuthLoginSession.scopes.length ? teamsAuthLoginSession.scopes.join(", ") : "n/a"}</strong>
            </div>
            <div>
              <span>Created</span>
              <strong>{formatTimestamp(teamsAuthLoginSession.createdAt)}</strong>
            </div>
            <div>
              <span>Expires</span>
              <strong>{formatTimestamp(teamsAuthLoginSession.expiresAt)}</strong>
            </div>
            <div>
              <span>Poll interval</span>
              <strong>{teamsAuthLoginSession.intervalSeconds == null ? "n/a" : formatRelativeSeconds(teamsAuthLoginSession.intervalSeconds)}</strong>
            </div>
          </div>
          <div className="teams-auth-code-line">
            <span>User code</span>
            <code>{teamsAuthLoginSession.userCode ?? "n/a"}</code>
          </div>
          <div className="teams-auth-link-list">
            {teamsAuthLoginSession.verificationUri ? (
              <div>
                <span>Verification URI</span>
                <a
                  className="teams-auth-uri"
                  href={teamsAuthLoginSession.verificationUri}
                  target="_blank"
                  rel="noreferrer"
                >
                  {teamsAuthLoginSession.verificationUri}
                </a>
              </div>
            ) : null}
            {teamsAuthLoginSession.verificationUriComplete ? (
              <div>
                <span>Verification URI complete</span>
                <a
                  className="teams-auth-uri"
                  href={teamsAuthLoginSession.verificationUriComplete}
                  target="_blank"
                  rel="noreferrer"
                >
                  {teamsAuthLoginSession.verificationUriComplete}
                </a>
              </div>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="empty-state">No active device-code login is in progress.</div>
      )}
    </article>

  );
}
