import {useEffect, useRef, useState} from "react";
import {fetchJson} from "../admin/api";

type AuthResult = {
  authenticated: boolean; status: string; login_session_id?: string;
  user_code?: string; verification_uri?: string;
};

export function ManagedTeamsSignIn({directoryPath, credentialRef, label, disabled, onUpdated}: {
  directoryPath: string; credentialRef: string; label: string; disabled: boolean;
  onUpdated: () => Promise<void>;
}) {
  const [login, setLogin] = useState<AuthResult | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const mounted = useRef(true);

  async function request(action: "status" | "login" | "complete" | "logout") {
    const sequence = ++generation.current;
    setBusy(true); setError(null);
    try {
      const result = await fetchJson<AuthResult>(
        `/api/teams-auth/${action}?directory_path=${encodeURIComponent(directoryPath)}&credential_ref=${encodeURIComponent(credentialRef)}`,
        action === "status" ? undefined : {
          method: "POST",
          body: JSON.stringify(action === "complete" ? {login_session_id: login?.login_session_id, timeout_seconds: 5} : {}),
        },
      );
      if (!mounted.current || generation.current !== sequence) return;
      if (result.status === "device_code_pending") {
        setLogin(result); setStatus("Waiting for Microsoft sign-in.");
      } else {
        setStatus(result.authenticated ? "Signed in." : "Signed out.");
        if (action !== "status") {setLogin(null); await onUpdated();}
      }
    } catch (reason) {
      if (mounted.current && generation.current === sequence) {
        setError(reason instanceof Error ? reason.message : "Teams sign-in failed.");
      }
    } finally {
      if (mounted.current && generation.current === sequence) setBusy(false);
    }
  }

  useEffect(() => {
    mounted.current = true;
    void request("status");
    return () => {mounted.current = false; generation.current++;};
  }, []);

  const locked = disabled || busy;
  const verificationUrl = login?.verification_uri?.startsWith("https://") ? login.verification_uri : null;
  return <section className="teams-auth-session-card" aria-label={`Teams sign-in for ${label}`}>
    <h4>Teams sign-in: {label}</h4>
    <p>Manage this shared identity from its owning directory, {directoryPath}.</p>
    {status ? <p role="status">{status}</p> : null}
    {error ? <div role="alert" className="error-box">{error}</div> : null}
    {login ? <p>Enter code <code>{login.user_code}</code>{verificationUrl ? <> at <a href={verificationUrl} target="_blank" rel="noreferrer">Microsoft sign-in</a></> : null}, then check sign-in status.</p> : null}
    <div className="inline-actions">
      <button disabled={locked} onClick={() => void request("login")}>{login ? "Restart sign-in" : "Start sign-in"}</button>
      <button disabled={locked || !login} onClick={() => void request("complete")}>Check sign-in status</button>
      <button disabled={locked} onClick={() => void request("logout")}>Sign out</button>
    </div>
  </section>;
}
