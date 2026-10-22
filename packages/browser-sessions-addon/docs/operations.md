# Opt-in deployment

Read the [coordinated split migration](../../application-environments-addon/docs/operations.md) before upgrading a combined preview installation. Browser workers now launch only browsers; application recipes and databases move to Application Environments.

1. Build assets/extension ZIP and install this Python package into control-plane and worker environments. Subsequent standard deployments refresh the optional package on hosts with /etc/evander/browser-relay.env or /etc/evander/browser-worker.env; evander_browser_sessions_addon_enabled explicitly overrides discovery. After a successful refresh, both playbooks restart any already-running browser relay or supervisor without changing service enablement; stopped or missing optional units stay untouched.
2. Enable browser-sessions-addon through the loader; restart gateway, Admin Web and both orchestrators through normal deployment. When deploying a wheel separately from the core checkout, select evander_browser_sessions_addon in both host and trigger-orchestrator addon_loader.modules so package-directory discovery does not keep using the checkout copy. Use the configuration APIs and their lock/revision checks. Verify the loaded source and exercise creation/invitation through the actual MCP endpoint.
3. Require Admin sign-in, configure public HTTPS, and assign directory browser permissions.
4. Install the opt-in deploy/ units. The core installer does not install optional daemons.
5. Store distinct relay/worker tokens in root-readable env files. Relay uses EVANDER_BROWSER_RELAY_TOKEN; worker references must agree in both settings copies.
6. Seed approved browser images, Node and Playwright/Chromium through existing provisioning. Browser sessions never pull images or receive general internet.
7. Configure identical browser profiles in Admin Shared Browsers and worker JSON. The local copy stays an independent operator authority.
8. Route public paths with deploy/Caddyfile.fragment. Managed Admin Web Caddy deployments discover /etc/evander/browser-relay.env and retain the relay route. Override evander_admin_web_browser_relay_enabled to force it on or off; the relay upstream defaults to 127.0.0.1:11890. Permit control-plane traffic to the private supervisor, default 11891. Keep 11890 and /internal/* private.
9. Start with allocations_enabled false; enable after disposable-worker qualification.
10. Distribute the extension and test a synthetic approved target before operator-owned targets.

[worker.json](../examples/worker.json) uses loopback for local development. For a worker VM use a private listener, caller restrictions and TLS across untrusted networks. Browser containers require a reachable TLS relay URL ending in /_browser/endpoint, approved exactly in relay_urls. On workers without Internet egress, resolve that certified hostname to the control plane’s private address through operator-managed private DNS or a host entry. Keep the HTTPS hostname for certificate validation; do not disable TLS verification.

Set worker container_user to the numeric UID:GID used by the parent worker containers (default 1000:1000). Both values must be nonzero. The supervisor pins that identity for each browser and its private configuration files.

The root supervisor disables Python bytecode writes so optional package files remain owned by the worker deployment account. The supervisor is a trusted root component requiring Docker, iptables and bridge netfilter. It launches browsers without mounting checkouts. Applications and their workspace pins belong to Application Environments. Agents must not write supervisor configuration or access Docker. Browser IPv6 is disabled. The private control listener needs no public exposure.

Build examples/Dockerfile.browser after npm run build using a preseeded runner base. Browser images need Node 22+, Playwright and Chromium, seeded or mounted read-only. Override EVANDER_PLAYWRIGHT_MODULE / PLAYWRIGHT_MCP_EXECUTABLE_PATH for different locations; set PLAYWRIGHT_BROWSERS_PATH for a mounted cache.

## Configuration and recovery

The guided form offers workers/profiles plus Advanced JSON for unusual cache settings. Saves use revisions. Children inherit until a complete override is saved. Active cleanup stays pinned to original placement.

Disabling sharing rejects new actions and closes resources during reconciliation. Keep daemons available until release. Removing optional registration does not erase core dependencies; unavailable cleanup blocks checkout deletion.

- Worker unavailable: compare persisted assignment, worker ID, parent_worker_url and supervisor.
- Profile mismatch: update both operator-owned copies, then create another preview.
- Startup/process failure: stopped browser containers are detected during startup and periodic reconciliation. Close the failed preview to release its reservation, then inspect the worker record and bounded Docker logs labelled evander.resource=<session ID>. Close before retrying with a new request ID.
- Missing dependencies: seed the image/cache through approved provisioning.
- Local input denied: the host must grant. DevTools or enterprise/DLP policy may prevent attachment.
- Changed origin: the local host approves it; hosted destinations must come from current session HTTP grants or leased resource endpoints.
- Cleanup failed: retry on the original supervisor with original credentials. Preserve dependency rows and checkout data.
- Restart: preserve lease/config/output state. Accepted intents resume, interrupted starts fail, running leases remain and incomplete cleanup retries.

## Deployment qualification

Unit and UI tests use real Chrome and simulated Docker operations. On a dedicated disposable worker also verify scoped proxy access, denied direct egress, other-session and host isolation, image availability, login handoff, expiry while the control plane is stopped, restart and cleanup failures. Follow the Application Environments qualification guide for composition and SQL checks.

Session and task-worktree cleanup also consult dependency records on the worker. Normal control-plane cleanup releases them first. To allow direct worker-daemon cleanup to invoke the supervisor, expose the worker token environment reference to that trusted daemon too; otherwise it safely rejects cleanup while a preview is active. Set callback_url if the private supervisor is not on 127.0.0.1:11891.

For optional microphone transcription and spoken replies, see [voice setup and usage](voice.md). Provision the speech credential and FFmpeg executable in Admin Web and relay environments before enabling microphone transcription.

For inline local voice, deploy Browser Sessions 0.3.0 to the control-plane services and reload Chrome extension 0.3.0 after finishing any active test. No worker update is needed for this local sidebar flow. Start a fresh sharing session, expand Voice and enable the microphone. Previously installed extensions and their existing companion voice pages remain compatible with the upgraded server.


Extension 0.3.0 adds `tabCapture` and `offscreen` permissions for compatibility with protected frames injected by other extensions. Replace the extracted extension files and reload the unpacked extension after ending an active test; downloading the ZIP alone does not update an installed copy. Select the application tab and click Evander's toolbar icon before sharing. If Chrome blocks debugger attachment because of another extension, the sidebar identifies compatibility mode automatically. Keep other extensions enabled. The fallback operates the application DOM and captures the selected tab through a hidden extension document; it never removes another extension's frames or captures the currently active tab by accident. Stop sharing closes that document and its video tracks. Loss of the background worker stops capture after 30 seconds without frame requests. The host must handle native dialogs and any UI that rejects synthetic input locally. Enterprise policy and an already attached debugger remain setup errors; the fallback does not bypass them.


For full native control with protected extension frames, publish the optional [native helper](helper.md). Build its self-contained ZIPs before building the add-on wheel/deployment bundle; the relay serves only published runtime packages at `/_browser/helper`. The extension requests `nativeMessaging` only when the host chooses helper mode. No worker update is needed for this user-hosted helper path. Qualify native installation, signing and Chrome's consent prompt on each target OS before broad distribution.
