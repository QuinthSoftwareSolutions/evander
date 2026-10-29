import { useEffect, useRef, useState } from "react";
import { ApiError, fetchJson } from "../admin/api";

type Connection = {
  organization: string;
  directory_path: string;
  identity_id: string;
  display_name: string;
  email: string | null;
  updated_at?: string;
  has_pat: boolean;
  has_oauth: boolean;
  oauth_status: string | null;
};
type ConnectionsResponse = { connections: Connection[]; oauth_available: boolean };
const endpoint = "/api/admin/me/ado-connections";

export function PersonalAdoConnections({ directoryPath }: { directoryPath: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const errorPanel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [organization, setOrganization] = useState("");
  const [organizationInvalid, setOrganizationInvalid] = useState(false);
  const [pat, setPat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [oauthAvailable, setOauthAvailable] = useState(false);

  useEffect(() => {
    const url = new URL(window.location.href);
    const result = url.searchParams.get("ado_connection");
    if (!result) return;
    url.searchParams.delete("ado_connection");
    window.history.replaceState(window.history.state, "", url);
    setOpen(true);
    if (result === "connected") setNotice("Microsoft connection saved.");
    else if (result === "cancelled") setNotice("Microsoft connection cancelled. Your existing connections are unchanged.");
    else setError("Microsoft connection could not be completed. Try again and use the same ADO account as your saved PAT.");
  }, []);

  async function refresh() {
    const result = await fetchJson<ConnectionsResponse>(endpoint);
    setConnections(result.connections);
    setOauthAvailable(result.oauth_available);
  }
  useEffect(() => {
    if (!open) return;
    dialog.current?.showModal();
    let active = true;
    void fetchJson<ConnectionsResponse>(endpoint)
      .then((result) => { if (active) { setConnections(result.connections); setOauthAvailable(result.oauth_available); } })
      .catch((cause: unknown) => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [open]);

  useEffect(() => {
    if (error) {
      errorPanel.current?.focus();
      errorPanel.current?.scrollIntoView({ block: "nearest" });
    }
  }, [error]);

  function close() {
    dialog.current?.close();
    setOpen(false);
    setPat("");
    setError("");
    setNotice("");
    setOrganizationInvalid(false);
  }
  function validatedOrganization(value: string): string | null {
    const name = value.trim().replace(/\/+$/, "").replace(/^https:\/\/dev\.azure\.com\//, "");
    if (/^[A-Za-z0-9][A-Za-z0-9-]{0,99}$/.test(name)) {
      setOrganizationInvalid(false);
      return name.toLowerCase();
    }
    setOrganizationInvalid(true);
    setNotice("");
    setError("Enter your Azure DevOps organization name, for example contoso from dev.azure.com/contoso. An email address cannot be used here.");
    errorPanel.current?.focus();
    errorPanel.current?.scrollIntoView({ block: "nearest" });
    return null;
  }
  async function save() {
    const selectedOrganization = validatedOrganization(organization);
    if (!selectedOrganization) return;
    setBusy(true);
    setError("");
    setNotice("");
    const submittedPat = pat;
    setPat("");
    try {
      const result = await fetchJson<{ connection: Connection }>(endpoint, {
        method: "PUT",
        body: JSON.stringify({ organization: selectedOrganization, directory_path: directoryPath, pat: submittedPat }),
      });
      setNotice(`PAT saved. Connected as ${result.connection.display_name}.`);
      await refresh().catch(() => {
        setError("Your PAT was saved, but the connection list could not be refreshed. Close and reopen this dialog to reload it.");
      });
    } catch (cause) {
      if (cause instanceof ApiError && cause.status < 500) {
        setError(`PAT was not saved. ${cause.message.replace(/[.!?]$/, "")}. Re-enter the PAT to try again.`);
      } else {
        setError("Could not confirm whether the PAT was saved. Close and reopen this dialog to check your saved connections before trying again.");
      }
    } finally { setBusy(false); }
  }
  async function connectMicrosoft(selectedOrganization = organization) {
    const organizationName = validatedOrganization(selectedOrganization);
    if (!organizationName) return;
    setBusy(true);
    setError("");
    setNotice("");
    setPat("");
    try {
      const result = await fetchJson<{ authorization_url: string }>(endpoint + "/oauth/start", {
        method: "POST", body: JSON.stringify({ organization: organizationName, directory_path: directoryPath }),
      });
      window.location.assign(result.authorization_url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to connect with Microsoft.");
      setBusy(false);
    }
  }
  async function remove(connection: Connection, method?: "pat" | "oauth") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fetchJson(endpoint, { method: "DELETE", body: JSON.stringify({
        organization: connection.organization, directory_path: connection.directory_path, method,
      }) });
      setNotice("Connection removed. Future PRs will use any remaining personal connection, then the bot.");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to remove the connection.");
    } finally { setBusy(false); }
  }

  return <>
    <button type="button" className="admin-shell-button" onClick={() => setOpen(true)}>My ADO connection</button>
    {open ? <dialog ref={dialog} className="personal-ado-dialog" aria-labelledby="personal-ado-title"
      onCancel={(event) => { event.preventDefault(); if (!busy) close(); }}>
      <h2 id="personal-ado-title">My Azure DevOps connections</h2>
      <p>Allow the platform to create pull requests as your ADO account when you own the work item.</p>
      <p>This connection applies to <strong>{directoryPath}</strong> and its subdirectories.</p>
      <p>Microsoft OAuth is preferred when connected. A saved PAT is the fallback, followed by the bot.</p>
      {error ? <div ref={errorPanel} id="personal-ado-error" className="error-box personal-ado-error" role="alert" tabIndex={-1}>
        <strong>Connection error</strong>
        <p>{error}</p>
      </div> : null}
      {notice ? <p className="personal-ado-notice" role="status">{notice}</p> : null}
      <label>Azure DevOps organization (required)
        <input required type="text" name="ado-organization" autoComplete="off" autoCapitalize="none"
          spellCheck={false} data-1p-ignore="true" data-lpignore="true"
          aria-invalid={organizationInvalid}
          aria-describedby={`personal-ado-organization-help${organizationInvalid ? " personal-ado-error" : ""}`}
          value={organization} onChange={(event) => {
            setOrganization(event.target.value);
            if (organizationInvalid) { setOrganizationInvalid(false); setError(""); }
          }} placeholder="contoso" disabled={busy} />
      </label>
      <p id="personal-ado-organization-help">Required for both Microsoft OAuth and a personal access token.
        Use the organization from your Azure DevOps URL: https://dev.azure.com/<strong>contoso</strong>.</p>
      <div className="personal-ado-method">
        <h3>Microsoft OAuth</h3>
        <button type="button" className="admin-shell-button" disabled={busy || !organization || !oauthAvailable}
          onClick={() => void connectMicrosoft()}>Connect with Microsoft</button>
        {!oauthAvailable ? <p>Microsoft connections need to be enabled by your platform administrator. You can use a PAT now.</p> : null}
      </div>
      <form className="personal-ado-method" autoComplete="off" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <h3>Personal access token (PAT)</h3>
        <label>Personal access token
          <input required type="password" autoComplete="off" value={pat}
            onChange={(event) => setPat(event.target.value)} disabled={busy} />
        </label>
        <p>Use an organization-scoped PAT with Code: Read &amp; write. Replace it here before it expires.
          The platform verifies the account and never displays the saved token.</p>
        <button type="submit" className="admin-shell-button" disabled={busy || !pat || !organization}>
          {busy ? "Working…" : "Validate and save PAT"}
        </button>
      </form>
      <h3>Saved connections</h3>
      <ul>{connections.map((connection) => <li key={`${connection.organization}:${connection.directory_path}`}>
        <strong>{connection.display_name}</strong> — {connection.organization} ({connection.directory_path})
        {connection.has_oauth ? <p>Microsoft OAuth: {connection.oauth_status === "reconnect_required" ? "Reconnect required" : "Connected"}</p> : null}
        {connection.has_pat ? <p>Personal access token saved</p> : null}
        <button type="button" className="admin-shell-button" disabled={busy}
          onClick={() => { setOrganization(connection.organization); setNotice("Enter a replacement PAT for the selected directory."); }}
          hidden={connection.directory_path !== directoryPath || !connection.has_pat}>Replace PAT</button>
        {connection.has_oauth ? <>
          <button type="button" className="admin-shell-button" disabled={busy || !oauthAvailable}
            hidden={connection.directory_path !== directoryPath}
            onClick={() => void connectMicrosoft(connection.organization)}>Reconnect Microsoft</button>
          <button type="button" className="admin-shell-button" disabled={busy}
            onClick={() => void remove(connection, "oauth")}>Disconnect Microsoft</button>
        </> : null}
        {connection.has_pat ? <button type="button" className="admin-shell-button" disabled={busy}
          onClick={() => void remove(connection, "pat")}>Remove PAT</button> : null}
        <button type="button" className="admin-shell-button" disabled={busy}
          onClick={() => void remove(connection)}>Remove all</button>
      </li>)}</ul>
      <button type="button" className="admin-shell-button" onClick={close} disabled={busy}>Close</button>
    </dialog> : null}
  </>;
}
