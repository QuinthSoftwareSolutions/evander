import { useEffect, useRef, useState } from "react";
import { fetchJson } from "../admin/api";

type Connection = {
  organization: string;
  directory_path: string;
  identity_id: string;
  display_name: string;
  email: string | null;
  updated_at?: string;
};
const endpoint = "/api/admin/me/ado-connections";

export function PersonalAdoConnections({ directoryPath }: { directoryPath: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [organization, setOrganization] = useState("");
  const [pat, setPat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function refresh() {
    const result = await fetchJson<{ connections: Connection[] }>(endpoint);
    setConnections(result.connections);
  }
  useEffect(() => {
    if (!open) return;
    dialog.current?.showModal();
    let active = true;
    void fetchJson<{ connections: Connection[] }>(endpoint)
      .then((result) => { if (active) setConnections(result.connections); })
      .catch((cause: unknown) => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [open]);

  function close() {
    dialog.current?.close();
    setOpen(false);
    setPat("");
    setError("");
    setNotice("");
  }
  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    const submittedPat = pat;
    setPat("");
    try {
      const result = await fetchJson<{ connection: Connection }>(endpoint, {
        method: "PUT",
        body: JSON.stringify({ organization, directory_path: directoryPath, pat: submittedPat }),
      });
      setNotice(`Connected as ${result.connection.display_name}.`);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to validate the PAT.");
    } finally { setBusy(false); }
  }
  async function remove(connection: Connection) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fetchJson(endpoint, { method: "DELETE", body: JSON.stringify({
        organization: connection.organization, directory_path: connection.directory_path,
      }) });
      setNotice("Connection removed. Future PRs will use the bot when no personal connection is available.");
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
      {error ? <p role="alert">{error}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <label>Organization
          <input required value={organization} onChange={(event) => setOrganization(event.target.value)}
            placeholder="Your ADO organization" disabled={busy} />
        </label>
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
      <ul>{connections.map((connection) => <li key={`${connection.organization}:${connection.directory_path}`}>
        <strong>{connection.display_name}</strong> — {connection.organization} ({connection.directory_path})
        <button type="button" className="admin-shell-button" disabled={busy}
          onClick={() => { setOrganization(connection.organization); setNotice("Enter a replacement PAT for the selected directory."); }}
          hidden={connection.directory_path !== directoryPath}>Replace</button>
        <button type="button" className="admin-shell-button" disabled={busy}
          onClick={() => void remove(connection)}>Remove</button>
      </li>)}</ul>
      <button type="button" className="admin-shell-button" onClick={close} disabled={busy}>Close</button>
    </dialog> : null}
  </>;
}
