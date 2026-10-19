# Opt-in deployment

1. Build assets/extension ZIP and install this Python package into control-plane and worker environments.
2. Enable browser-sessions-addon through the loader; restart gateway, Admin Web and effect worker through normal deployment.
3. Require Admin sign-in, configure public HTTPS, and assign directory browser permissions.
4. Install the opt-in deploy/ units. The core installer does not install optional daemons.
5. Store distinct relay/worker tokens in root-readable env files. Relay uses EVANDER_BROWSER_RELAY_TOKEN; worker references must agree in both settings copies.
6. Seed approved app/browser images, Node, Playwright/Chromium and toolchain caches through existing provisioning. Previews never pull images or receive general internet.
7. Configure identical profiles in Admin Shared Browsers and worker JSON. The local copy stays an independent operator authority.
8. Route public paths with deploy/Caddyfile.fragment. Permit control-plane traffic to the private supervisor, default 11891. Keep 11890 and /internal/* private.
9. Start with allocations_enabled false; enable after disposable-worker qualification.
10. Distribute the extension and test the synthetic recipe before operator-owned application recipes.

[worker.json](../examples/worker.json) uses loopback for local development. For a worker VM use a private listener, caller restrictions and TLS across untrusted networks. Browser containers require a reachable TLS relay URL ending in /_browser/endpoint, approved exactly in relay_urls.

The supervisor is a trusted root component requiring Docker, iptables and bridge netfilter. Agents must not write its configuration or access Docker. Preview IPv6 is disabled. Neither raw app ports nor supervisor need public exposure.

Build examples/Dockerfile.browser after npm run build using a preseeded runner base. App images need /usr/bin/timeout. Browser images need Node 22+, Playwright and Chromium, seeded or mounted read-only. Override EVANDER_PLAYWRIGHT_MODULE / PLAYWRIGHT_MCP_EXECUTABLE_PATH for different locations; set PLAYWRIGHT_BROWSERS_PATH for a mounted cache.

## Configuration and recovery

The guided form offers workers/profiles plus Advanced JSON for unusual cache/egress settings. Saves use revisions. Children inherit until a complete override is saved. Active cleanup stays pinned to original placement.

Disabling sharing rejects new actions and closes resources during reconciliation. Keep daemons available until release. Removing optional registration does not erase core dependencies; unavailable cleanup blocks checkout deletion.

- Worker unavailable: compare persisted assignment, worker ID, parent_worker_url and supervisor.
- Profile mismatch: update both operator-owned copies, then create another preview.
- Startup/process failure: stopped application and browser containers are detected during startup and periodic reconciliation. Close the failed preview to release its reservation, then inspect the worker record and bounded Docker logs labelled evander.browser=<session ID>. Close before retrying with a new request ID.
- Missing dependencies: seed the image/cache through approved provisioning.
- Local input denied: the host must grant. DevTools or enterprise/DLP policy may prevent attachment.
- Changed origin: the local host approves it; hosted origins derive from declared ports.
- Cleanup failed: retry on the original supervisor with original credentials. Preserve dependency rows and checkout data.
- Restart: preserve lease/config/output state. Accepted intents resume, interrupted starts fail, running leases remain and incomplete cleanup retries.

## Deployment qualification

Tests use real Chrome and simulated Docker operations. They do not qualify kernel filtering, actual application recipes or SQL Server. On a dedicated disposable worker verify denied app/browser egress, cross-preview/host isolation, image/cache availability, scoped migrations/login handoff, expiry while control plane is stopped, restart, SQL release and checkout preservation after cleanup failure. These checks do not authorize broadening a live firewall.

Session and task-worktree cleanup also consult dependency records on the worker. Normal control-plane cleanup releases them first. To allow direct worker-daemon cleanup to invoke the supervisor, expose the worker token environment reference to that trusted daemon too; otherwise it safely rejects cleanup while a preview is active. Set callback_url if the private supervisor is not on 127.0.0.1:11891.

For optional microphone transcription and spoken replies, see [voice setup and usage](voice.md). Provision the speech credential in Admin Web and relay environments before enabling the directory setting.
