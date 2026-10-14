import { useEffect, useRef, useState } from "react";
import { fetchJson } from "../admin/api";
import { makeDraft, type Draft, type Entry } from "./websiteLoginModel";
import "./website-login-wizard.css";

type WordPressSite = {origin: string; rest_path?: string; enabled: boolean; capabilities: string[]; recovery_verified?: string | null};
type Kind = "wordpress" | "json";
type ResponseKind = "token" | "json" | "cookies";

function httpsUrl(value: string, endpoint = false): URL {
  let url: URL;
  try {url = new URL(value.trim());} catch {throw new Error("Enter a complete HTTPS address, such as https://www.example.com.");}
  if (url.protocol !== "https:" || url.username || url.password || url.hash || (endpoint && url.search)) {
    throw new Error(endpoint ? "Use an HTTPS login endpoint without credentials, query parameters or a fragment." : "Use an HTTPS website address without credentials or a fragment.");
  }
  return url;
}

function suggestedId(origin: string, profiles: Entry[]): string {
  const base = new URL(origin).hostname.replace(/^www\./, "").replace(/[^A-Za-z0-9_.-]/g, "-").slice(0, 100) || "website";
  let id = base;
  for (let suffix = 2; profiles.some(profile => profile.id === id); suffix++) id = base + "-" + suffix;
  return id;
}

export function WebsiteLoginWizard({active, draft, directoryPath, profiles, locked, saved, update, onSave, onTest, onAdvanced, onClose}: {
  active: boolean; draft: Draft; directoryPath: string; profiles: Entry[]; locked: boolean; saved: boolean;
  update: (patch: Partial<Draft>) => void; onSave: () => void; onTest: () => void;
  onAdvanced: () => void; onClose: () => void;
}) {
  const [kind, setKind] = useState<Kind | null>(null);
  const [step, setStep] = useState(0);
  const [responseKind, setResponseKind] = useState<ResponseKind>("token");
  const [error, setError] = useState<string | null>(null);
  const [sites, setSites] = useState<Record<string, WordPressSite>>({});
  const [sitesLoading, setSitesLoading] = useState(false);
  const [sitesError, setSitesError] = useState(false);
  const [reload, setReload] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const steps = kind === "wordpress" ? ["Website", "WordPress connection", "Review & test"] : ["Website", "Sign in", "Browser access", "Review & test"];
  const finalStep = step === steps.length - 1;
  const wordpressPage = "/addons/wordpress-addon/?directory=" + encodeURIComponent(directoryPath);
  const selectedSite = sites[String(draft.profile.adapter_config?.site_id ?? "")];
  const matchingSites = Object.entries(sites).filter(([, site]) => site.origin === draft.profile.browser_origin && site.capabilities.includes("browser"));

  useEffect(() => {
    if (active) {heading.current?.focus(); setError(null);}
  }, [active, step]);
  useEffect(() => {
    if (kind !== "wordpress") return;
    let current = true;
    setSitesLoading(true); setSitesError(false); setSites({});
    fetchJson<{settings: {sites: Record<string, WordPressSite>}}>("/api/addons/wordpress-addon/settings?directory=" + encodeURIComponent(directoryPath))
      .then(data => {
        if (!data.settings?.sites) throw new Error("WordPress site settings unavailable");
        if (current) setSites(data.settings.sites);
      })
      .catch(() => {if (current) setSitesError(true);})
      .finally(() => {if (current) setSitesLoading(false);});
    return () => {current = false;};
  }, [kind, directoryPath, reload]);

  function chooseKind(value: Kind) {
    if (value === kind) return;
    const fresh = makeDraft();
    if (value === "wordpress") {
      fresh.profile = {...fresh.profile, adapter: "wordpress", adapter_config: {}, credential_source: "environment",
        credential_fields: {}, public_credential_fields: [], local_storage: {}, session_storage: {},
        cookies_response_pointer: null, max_age_seconds: 900};
      fresh.credentials = []; fresh.storage = [];
    }
    update({...fresh, id: draft.id, profile: {...fresh.profile, browser_origin: draft.profile.browser_origin}});
    setKind(value); setResponseKind("token"); setError(null);
  }

  function selectSite(id: string) {
    const site = sites[id];
    update({profile: {...draft.profile, adapter_config: site ? {site_id: id} : {},
      login_url: site ? site.origin + (site.rest_path ?? "/wp-json/") + "evander/v1/browser/ticket" : ""}});
  }

  function chooseResponse(value: ResponseKind) {
    setResponseKind(value);
    update({profile: {...draft.profile, cookies_response_pointer: value === "cookies" ? "/cookies" : null},
      storage: value === "cookies" ? [] : [{name: value === "token" ? "accessToken" : "currentUser", location: "local",
        pointer: value === "token" ? "/token" : "", encoding: value === "token" ? "string" : "json"}]});
  }

  function forward() {
    setError(null);
    try {
      if (step === 0) {
        if (!kind) throw new Error("Choose WordPress or a custom login API to continue.");
        const origin = httpsUrl(draft.profile.browser_origin).origin;
        update({id: draft.id || suggestedId(origin, profiles), profile: {...draft.profile, browser_origin: origin}});
      } else if (step === 1 && kind === "wordpress") {
        if (!selectedSite || !matchingSites.some(([id]) => id === draft.profile.adapter_config?.site_id)) {
          throw new Error("Choose a WordPress site configured for browser access in this directory.");
        }
        selectSite(String(draft.profile.adapter_config?.site_id));
      } else if (step === 1) {
        httpsUrl(draft.profile.login_url, true);
        const names = draft.credentials.map(credential => credential.name.trim());
        if (names.some(name => !name) || new Set(names).size !== names.length) throw new Error("Use different, nonempty names for the username and password fields.");
      }
      if (finalStep) {
        if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(draft.id)) throw new Error("Use letters, numbers, dots, underscores or hyphens for the profile ID.");
        if (!draft.existing && profiles.some(profile => profile.id === draft.id)) throw new Error("That profile ID is already in use. Choose another so the existing login is preserved.");
        onSave();
      } else setStep(step + 1);
    } catch (reason) {setError(reason instanceof Error ? reason.message : "Check these settings before continuing.");}
  }

  // Keep the step and connection selection while the shared draft is edited in the advanced editor.
  if (!active) return null;

  return <form className="website-login-wizard" autoComplete="off" onSubmit={event => {event.preventDefault(); if (!saved) forward();}}>
    <fieldset disabled={locked}>
      <legend>Set up a website login</legend>
      <ol className="website-login-steps" aria-label="Setup progress">
        {steps.map((label, index) => <li key={label} aria-current={index === step ? "step" : undefined}>
          <span aria-hidden="true">{index + 1}</span>{label}
        </li>)}
      </ol>
      <h3 ref={heading} tabIndex={-1}>{saved ? "Website login saved" : steps[step]}</h3>
      {error ? <div role="alert" className="callout error-callout">{error}</div> : null}

      {step === 0 ? <>
        <p>Choose how this website signs in. We will guide you through the settings it needs.</p>
        <div className="website-login-kinds" role="group" aria-label="Website type">
          <label className="website-login-kind"><input type="radio" name="website-kind" value="wordpress" checked={kind === "wordpress"} onChange={() => chooseKind("wordpress")} />
            <span><strong>WordPress</strong><span>Use the companion plugin and your configured WordPress connection.</span></span></label>
          <label className="website-login-kind"><input type="radio" name="website-kind" value="json" checked={kind === "json"} onChange={() => chooseKind("json")} />
            <span><strong>Custom login API</strong><span>For an application with a login endpoint that returns JSON.</span></span></label>
        </div>
        {kind ? <label className="field-stack"><span>Website address</span>
          <input required type="url" aria-label="Website address" aria-describedby="website-login-address-help" placeholder="https://www.example.com" value={draft.profile.browser_origin}
            onChange={event => update({id: "", profile: {...draft.profile, browser_origin: event.target.value, adapter_config: {}, login_url: ""}})} />
          <small id="website-login-address-help">Enter its HTTPS address. A page address is fine; we use the website origin.</small></label> : null}
        {kind === "json" ? <p className="field-description">This connection needs a JSON login API. Ordinary login forms, SSO and multi-factor sign-in need a supported adapter.</p> : null}
      </> : null}

      {step === 1 && kind === "wordpress" ? <>
        <p>Choose the WordPress connection for this website. Its connector already holds the username and application password.</p>
        {sitesLoading ? <p role="status">Loading WordPress connections…</p> : null}
        {!sitesLoading && (sitesError || matchingSites.length === 0) ? <div className="callout">
          <p>{sitesError ? "WordPress settings could not be loaded. Check that the WordPress add-on is enabled and available to this directory." : "There is no WordPress site with browser access configured for this address in this directory."}</p>
          <p>In the WordPress add-on, install the companion plugin on the website, connect its account, and configure this site's browser capability. Then return here.</p>
          <a href={wordpressPage} target="_blank" rel="noopener noreferrer">Open WordPress setup ↗</a>
        </div> : null}
        {!sitesLoading && matchingSites.length ? <label className="field-stack"><span>WordPress site</span>
          <select required aria-label="WordPress site" value={String(draft.profile.adapter_config?.site_id ?? "")} onChange={event => selectSite(event.target.value)}>
            <option value="">Choose a WordPress site</option>
            {matchingSites.map(([id]) => <option key={id} value={id}>{id}</option>)}
          </select></label> : null}
        {selectedSite && (!selectedSite.enabled || !selectedSite.recovery_verified) ? <p className="callout">This site still needs to be enabled after its recovery test. You can save the login now, but assistants cannot use it until that setup is complete.</p> : null}
        <button type="button" disabled={sitesLoading} onClick={() => setReload(reload + 1)}>Refresh WordPress connections</button>
        <p className="field-description">Temporary browser sessions are managed by the companion. No password or response mappings are needed on this screen.</p>
      </> : null}

      {step === 1 && kind === "json" ? <>
        <p>Use the login API address supplied by the application administrator. This is usually different from the sign-in page.</p>
        <label className="field-stack"><span>Login endpoint</span><input required type="url" placeholder="https://www.example.com/api/login"
          value={draft.profile.login_url} onChange={event => update({profile: {...draft.profile, login_url: event.target.value}})} /></label>
        <div className="website-login-grid">
          {draft.credentials.map((credential, index) => <label className="field-stack" key={index}><span>{index === 0 ? "Username" : "Password"}</span>
            <input required type={index === 0 ? "text" : "password"} autoComplete={index === 0 ? "off" : "new-password"}
              value={credential.value} onChange={event => update({credentials: draft.credentials.map((value, i) => i === index ? {...value, value: event.target.value} : value)})} /></label>)}
        </div>
        <p className="field-description">Credentials are saved in encrypted host storage. The assistant receives temporary authentication state.</p>
        <details><summary>The API uses different field names</summary>
          <p>Use the JSON request field names from the application's API documentation, for example email instead of username.</p>
          <div className="website-login-grid">{draft.credentials.map((credential, index) => <label className="field-stack" key={index}>
            <span>{index === 0 ? "Username field name" : "Password field name"}</span><input required value={credential.name}
              onChange={event => update({credentials: draft.credentials.map((value, i) => i === index ? {...value, name: event.target.value} : value)})} /></label>)}</div>
        </details>
      </> : null}

      {step === 2 && kind === "json" ? <>
        <p>Tell us which part of the login response the website uses to stay signed in. The application administrator can provide these two details.</p>
        <label className="field-stack"><span>Login response contains</span><select aria-label="Login response contains" value={responseKind} onChange={event => chooseResponse(event.target.value as ResponseKind)}>
          <option value="token">An access token</option><option value="json">A JSON object</option><option value="cookies">A list of browser cookies</option>
        </select></label>
        {responseKind === "cookies" ? <label className="field-stack"><span>Cookie list in response</span>
          <input aria-label="Cookie list in response" aria-describedby="website-login-cookie-help" value={draft.profile.cookies_response_pointer ?? ""} placeholder="/cookies"
            onChange={event => update({profile: {...draft.profile, cookies_response_pointer: event.target.value}})} />
          <small id="website-login-cookie-help">For a response with a cookies field, enter /cookies. Cookies must be HTTPS-only and scoped to this website.</small></label> : <>
          <div className="website-login-grid">
            <label className="field-stack"><span>Value in login response</span><input aria-label="Value in login response" aria-describedby="website-login-value-help" placeholder="/token" value={draft.storage[0]?.pointer ?? ""}
              onChange={event => update({storage: draft.storage.map((binding, index) => index === 0 ? {...binding, pointer: event.target.value} : binding)})} />
              <small id="website-login-value-help">For a token field, enter /token. For a nested result.token, enter /result/token. Leave blank for the whole response.</small></label>
            <label className="field-stack"><span>Browser storage key</span><input required aria-label="Browser storage key" aria-describedby="website-login-key-help" value={draft.storage[0]?.name ?? ""}
              onChange={event => update({storage: draft.storage.map((binding, index) => index === 0 ? {...binding, name: event.target.value} : binding)})} />
              <small id="website-login-key-help">The key the website reads, for example accessToken or currentUser.</small></label>
          </div>
          <label className="field-stack"><span>Keep this value in</span><select aria-label="Keep this value in" value={draft.storage[0]?.location ?? "local"}
            onChange={event => update({storage: draft.storage.map((binding, index) => index === 0 ? {...binding, location: event.target.value as "local" | "session"} : binding)})}>
            <option value="local">Local storage</option><option value="session">Session storage</option></select></label>
        </>}
      </> : null}

      {finalStep ? <>
        <p>{saved ? "The profile is saved. Check the connection before assigning it to an assistant." : "Review the connection before saving. No credentials are shown in this summary."}</p>
        <dl className="website-login-review">
          <dt>Website</dt><dd>{draft.profile.browser_origin}</dd>
          <dt>Connection</dt><dd>{kind === "wordpress" ? "WordPress · " + String(draft.profile.adapter_config?.site_id ?? "") : "Custom login API"}</dd>
          {kind === "json" ? <><dt>Login endpoint</dt><dd>{draft.profile.login_url}</dd>
            <dt>Browser access</dt><dd>{responseKind === "cookies" ? "Cookies from " + (draft.profile.cookies_response_pointer || "the whole response") :
              (draft.storage[0]?.pointer || "Whole response") + " → " + (draft.storage[0]?.location === "session" ? "session" : "local") + " storage: " + draft.storage[0]?.name}</dd></> : null}
          <dt>Directory</dt><dd>{directoryPath}</dd>
          <dt>Profile ID</dt><dd>{draft.id}</dd>
          <dt>Credentials</dt><dd>{kind === "wordpress" ? "Managed by the WordPress connector" : "Encrypted host storage"}</dd>
          <dt>Session lifetime</dt><dd>Up to {Math.round(draft.profile.max_age_seconds / 60)} minutes</dd>
        </dl>
        {!saved ? <details><summary>Adjust profile name and session lifetime</summary>
          <div className="website-login-grid">
            <label className="field-stack"><span>Profile ID</span><input required maxLength={128} pattern="[A-Za-z0-9][A-Za-z0-9_.-]*" value={draft.id} onChange={event => update({id: event.target.value})} /></label>
            <label className="field-stack"><span>Maximum session lifetime (minutes)</span><input required type="number" min={3} max={kind === "wordpress" ? 15 : 1440}
              value={draft.profile.max_age_seconds / 60} onChange={event => update({profile: {...draft.profile, max_age_seconds: Number(event.target.value) * 60}})} /></label>
          </div>
        </details> : null}
        {saved ? <>
          <button type="button" onClick={onTest}>{kind === "wordpress" ? "Check WordPress configuration" : "Test saved login"}</button>
          <p className="field-description">{kind === "wordpress" ? "This checks the saved profile's configuration. An actual browser login also needs an enabled site, recovery coverage and an authorized prepared change." : "The test signs in on the host and checks the returned authentication state. It does not test every website feature."}</p>
          <p>Next, select this profile under <strong>Managed website login</strong> in the assistant's HTTP targets. Saving a profile does not grant an assistant access.</p>
        </> : null}
      </> : null}

      <div className="inline-actions website-login-wizard-actions">
        {!saved && step > 0 ? <button type="button" onClick={() => setStep(step - 1)}>Back</button> : null}
        {!saved ? <button type="submit" className="primary" disabled={!kind || (step === 1 && kind === "wordpress" && (sitesLoading || !selectedSite))}>
          {finalStep ? "Save website login" : "Continue"}</button> : null}
        <button type="button" onClick={onClose}>{saved ? "Finish" : "Cancel setup"}</button>
        <button type="button" className="website-login-advanced" onClick={onAdvanced}>{saved ? "Edit advanced settings" : "Use advanced editor"}</button>
      </div>
    </fieldset>
  </form>;
}
