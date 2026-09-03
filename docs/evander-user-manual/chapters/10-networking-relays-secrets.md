# 10. Networking, Relays, Packages, and Secrets

> **At a glance.** An agent is useful only if it can reach the models, code, packages, tools, and test systems needed for its job. Evander provides controlled doors to those services instead of opening the whole network or copying host secrets into the agent's workspace. This chapter explains what each door is for and why network reachability is never treated as permission by itself.

## Controlled doors, not an open corridor

An isolated workspace does not normally browse the internet or receive the host's provider credentials. Instead, it reaches a small set of Evander-owned services. Each service has one job and checks the request appropriate to that job.

| What the agent needs | Controlled route | Plain-language boundary |
|---|---|---|
| Evander tools | MCP gateway | Show and run only the tools allowed for this active session. |
| A model | Model relay, or the worker's brokered subscription route | Keep provider keys and named ChatGPT profiles outside the agent workspace and constrain the upstream destination. |
| Packages | Package relay, facade, or prepared cache | Reach configured package sources without opening a general download route. |
| A named test or application environment | HTTP target relay | Reach only a target alias recorded for this session; use the application's own guest-held login. |
| Source code | Restricted managed Git over SSH | Read or write only the repository scope and refs granted to this session. |

Three ideas keep this model understandable:

1. **A working connection is not permission.** The route can work while a later session, target, tool, or policy check denies the action.
2. **Different doors handle credentials differently.** The model and private NuGet relays add the host-owned upstream credential they are designed to protect. The HTTP target relay deliberately does not; it forwards authentication already held by the guest.
3. **A narrow failure is useful evidence.** A package-cache miss, an expired session, and an application login failure occur at different boundaries and should not be “fixed” by opening general network access.

The host resolves the session and directory rules, supplies an upstream credential only where a relay owns that responsibility, removes Evander transport secrets before forwarding, and records the consequential activity. Default-deny forwarding supports those checks; it does not replace them.

## A request through a controlled door

Suppose the implementation agent for Northstar Platform needs to test telemetry retry behavior.

- A model request normally goes to Evander's model relay. Evander checks the session, selects the configured provider, removes guest transport credentials, adds the host's provider credential, and records available usage information. An explicitly selected public-OpenAI subscription turn on a container worker instead receives in-memory authentication from the worker broker and uses its restricted public proxy.
- A package restore goes through the appropriate relay or cache. The route may serve a public feed, add a host-held private-feed credential, or use already prepared package content.
- A request to the provisioned `mission-control` environment uses a session alias such as `mission-control-api`. The agent supplies its legitimate application credential; the relay confines the destination and does not turn that URL into general internet permission.

The agent may experience all three as ordinary development traffic. Their security contracts are deliberately different behind the scenes.

## What an operator may notice

| What you see | What it usually tells you | Useful information to capture |
|---|---|---|
| The agent cannot reach any controlled service | Session routing, workspace networking, or bootstrap state may be wrong. | Session ID, backend, exact connection error, and whether the shell was opened before the latest bootstrap |
| Tools work, but model calls fail | The MCP route is healthy; model policy, provider configuration, credential, or upstream service needs attention. | Session ID, model, provider mode if shown, status/error, and approximate time |
| Public packages work, but a private feed fails | The package route is present; the private-feed credential or feed configuration may be missing. | Feed name, package, restore error, session ID, and whether public packages still work |
| An HTTP target returns `401` | The request may have reached the target application, which rejected the guest-managed login. | Target alias, application status, session ID, and a redacted description of the authentication used |
| An HTTP target alias returns `404` | That alias is not present in the session contract. | Session ID, requested alias, directory, and expected workflow |
| A restore reports content unavailable | Direct internet is intentionally absent; the relay or cache may not contain or reach the requested package. | Ecosystem, package/version, configured source, and exact restore error |

Do not include secret values, session tokens, private keys, or full authorization headers in a support handoff. Names of configured environment variables and redacted errors are usually enough to locate the boundary.

## Reading common failures

An operator can use the first column to describe the failure. The remaining columns tell an administrator what safe outcome to expect and which boundary to repair.

| Failure | Expected behavior | Interpretation |
|---|---|---|
| Missing model provider key | Relay returns a service error before upstream use. | Fix the service environment named by effective provider config. |
| Expired or bad session token | Session relay request is rejected (`401`/`403` or WebSocket policy close). | Repair session/bootstrap state; do not bypass session routing. |
| Hosted web search requested but disabled | Relay returns a policy error and logs the denial. | Enable only on the session that genuinely needs it. |
| Unsupported provider path | Relay returns `501`, validation error, or WebSocket policy close. | Use a supported API; the relay is not a generic reverse proxy. |
| Subscription profile has no capacity | The original Codex prompt remains durably queued without a metered fallback. | Have a root owner inspect **Subscriptions**; do not copy profile credentials into the job. |
| Azure NuGet PAT missing | Public NuGet may work; Azure feed returns `503`. | Set the configured PAT environment variable for the NuGet unit. |
| HTTP target alias missing | Relay returns `404`. | Add the alias through the session/workflow metadata contract. |
| HTTP target redirect changes origin | Relay returns `502` rather than following it. | Correct the target base URL or application redirect behavior. |
| Package restore misses cache | Restore may fail because direct internet is unavailable. | Prewarm the cache or make the appropriate relay/cache reachable. |
| SSE transport closes after final bytes | Relay logs `relay_stream_transport_terminated`; the client may still have a complete response. | Correlate chunks/bytes and provider events before treating it as data loss. |

## Troubleshooting: what to hand to an administrator

Start with the session and one failing request:

1. Record the directory, run or trace ID, session ID, execution backend, and time of the request.
2. Name the door that failed: tool, model, package/feed, Git, or HTTP target alias.
3. Copy the exact status and error, but redact credentials and authorization material.
4. Note which nearby route still works—for example, “MCP tools work, but model calls fail” or “public NuGet works, but the Azure feed returns `503`.”
5. Give those details to an Evander administrator. Do not add firewall ports, copy a host key into the workspace, or bypass the relay as a diagnostic shortcut.

If a secret may have been exposed, preserve the audit evidence and contact the administrator responsible for that credential so it can be rotated. Deleting a visible record does not revoke a credential.

The remainder of this chapter is network, protocol, and administrator reference material.

## Administrator reference: doors and network boundaries

The exact service doors and default ports are:

- MCP on port `8000` is the tool-capability door;
- the model relay on `11434` is the inference door;
- the NuGet relay on `11666`, npm audit facade on `11881`, and optional Nexus cache on `8081` are package doors;
- the HTTP target relay on `11777` is a session-specific door to a named external origin;
- host SSH on `22` is the managed Git transport door;
- apt-cacher-ng on `3142`, when enabled, is a worker-provisioning door rather than an agent application dependency.

The checked-in firewall example allows those host TCP ports and drops other forwarded traffic from the libvirt bridge. A deployment may deliberately change the list, but adding a port is a security decision: it creates network reachability that the application may not further mediate. Container-worker networking has its own worker-side egress policy and must be configured explicitly; the libvirt firewall is not a container policy.

MCP is therefore a central capability boundary, but not the only reachable host service. Every direct relay must enforce its own narrow contract.

## Administrator reference: session routing and the guest environment

Cloud-init writes `/etc/evander/session.env`, and the shell profile loads it for the `agent` user. The file contains routing data such as session identity, MCP URL/token, model relay URL, HTTP-target relay prefix, and package locations. It also sets a placeholder `OPENAI_API_KEY` so tools that insist on an API-key variable can start. That placeholder is not the upstream provider secret.

A shell that was already open before a bootstrap repair may need to reload the canonical environment:

```bash
source ~/.profile
```

Session-bearing relay requests are authenticated with the session's MCP token. The token may be carried in a supported header, query parameter, or generated auth path, depending on the client. Relays remove it from the upstream query and headers. The session manifest must exist and be active; the model relay also rejects expired sessions.

## Administrator reference: model inference relay

`evander-openai-relay` exposes a session-scoped model surface while resolving the actual provider from effective directory and session configuration. Public OpenAI is the default when no mode override is supplied. The current modes are:

| Mode | Upstream shape | Current boundary |
|---|---|---|
| `openai_public` | OpenAI-compatible HTTP, SSE, and supported Responses WebSocket | Host injects the OpenAI key. |
| `azure_openai` | Azure OpenAI deployments selected through the model-to-deployment map | Host injects the Azure key; managed identity is not part of this implementation. |
| `local_openai` | OpenAI-compatible HTTP/SSE endpoint such as Ollama | WebSocket/realtime is rejected; a configured placeholder bearer may be used upstream. |
| `anthropic` | `GET /v1/models`, `POST /v1/messages`, and `POST /v1/messages/count_tokens` | Host injects `x-api-key`; WebSocket/realtime is rejected. |

The general OpenAI-compatible HTTP allowlist is `GET /v1/models` and `POST` to `/v1/responses`, `/v1/responses/compact`, `/v1/chat/completions`, `/v1/completions`, or `/v1/embeddings`. Only `/v1/responses` is accepted on the OpenAI-compatible WebSocket surface. Unsupported paths fail before they become arbitrary proxy access.

The relay removes guest `Authorization`, `x-api-key`, hop-by-hop headers, content length/encoding, host, and the Evander session token before building the upstream request. It then supplies the provider credential selected by host configuration. For streaming responses, the upstream client remains alive until the response iterator finishes, so raw SSE bytes can be forwarded without truncating the stream.

Hosted OpenAI web search is denied unless the session manifest explicitly sets `web_search_enabled: true`. The relay examines both HTTP request bodies and WebSocket messages, rather than trusting a client-side Codex flag alone. Model use is recorded in `runtime/model-usage.db` when the provider supplies token usage; completed responses without a usage block are recorded as `usage_unavailable`.

### Subscription routing remains provider-specific

ChatGPT subscription authentication is an optional container-worker Codex route, not a new
credential accepted by every provider. A `chatgpt_subscription` preference becomes effective only
when the turn's model provider mode is `openai_public`. The disposable job gets an in-memory token
snapshot from the selected worker profile broker and reaches only allowlisted OpenAI/ChatGPT HTTPS hosts through
the worker's CONNECT proxy; it receives neither the broker's `auth.json` nor direct public egress.

For `local_openai` and `azure_openai`, Evander deliberately computes the turn as `metered_api` and
keeps the configured private endpoint and API-key/relay behavior. It leaves the stored session
preference unchanged. This distinction matters when diagnosing a session that moves between
provider modes: seeing `chatgpt_subscription` in session policy does not mean a local Ollama or
Azure request was sent with the worker profile.

Subscription authentication also fails closed. An absent login, disabled profile, refresh failure,
rate limit, broker ownership failure, or restricted-proxy failure does not cause a quiet retry
against the host's metered OpenAI credential. Capacity deferrals retain the original prompt in a
per-profile FIFO queue. Chapter 9 covers profile ownership and lifecycle.

### Local-model routing is still a relay boundary

A host-local model is not a reason to connect the guest directly to Ollama. Keep the agent pointed at Evander's session-scoped relay and configure `local_openai_api_base_url` on the host. If the relay listens on wildcard port `11434`, it rejects the ambiguous default of calling a loopback service on that same port. Bind the relay to the guest-facing address or move the local model to another private port, such as `127.0.0.1:11435`.

## Administrator reference: package paths

### NuGet

`evander-nuget-relay` exposes two feed roots:

```text
/nuget/nuget-org/...
/nuget/azure/...
```

The public feed needs no host credential. The Azure Artifacts feed reads the PAT from the configured host environment variable and adds Basic authorization upstream. Service-index JSON is rewritten so clients continue through the relay; package bytes are streamed. Origin-rooted Azure packaging paths are accepted only for feed IDs learned from the configured service index, preventing the relay from becoming an arbitrary Azure DevOps proxy.

`bootstrap_nuget_vm.sh` creates a guest NuGet configuration with writable caches in `/home/agent/.nuget`, the relay feeds, and an optional read-only fallback package directory at `/opt/evander/nuget-cache-ro`. Container workers use their own worker-level read-only cache and session-local writable cache.

### PyPI, npm, apt, and npm audit

The optional Nexus service on `8081` is the worker/application cache for PyPI and npm. It is separate from apt-cacher-ng on `3142`, which Ansible uses while provisioning a worker VM.

The npm audit protocol is a special case. `evander-npm-audit-facade` sends ordinary package metadata and tarball requests to the configured package registry, normally Nexus, but sends only the two npm audit POST endpoints to the audit registry:

```text
/-/npm/v1/security/advisories/bulk
/-/npm/v1/security/audits/quick
```

This lets `npm audit` work without turning the worker's npm registry setting into a direct public package route. The facade does not inject an npm credential.

## Administrator reference: session-scoped HTTP targets

The HTTP target relay differs deliberately from the model and NuGet relays: **it forwards guest-managed authorization and cookies**. It does not inject a host secret for the target.

Each session manifest contains named HTTP-target metadata, normally under `metadata.http_targets`. A request uses the session and alias:

```text
$HTTP_TARGET_RELAY_BASE_URL/session/<session-id>/target/<alias>/path
```

Operators can manage the same contract without editing raw metadata: use **HTTP targets** in the
Agents create/edit forms or in **Sessions → Model and access**. Each row accepts an alias and
HTTP(S) base URL. Existing per-target workflow metadata is preserved, and clearing all rows writes
an explicit empty target set so legacy APP environment metadata cannot restore access implicitly.

The relay:

1. authenticates the Evander session token;
2. resolves the alias only from that session manifest;
3. combines the configured base path with validated, traversal-safe path segments;
4. removes the Evander token and hop-by-hop headers but preserves application headers, including guest `Authorization`;
5. makes one request without automatically following redirects;
6. rewrites a same-origin redirect back through the relay and rejects a redirect outside the configured origin.

This is useful while the implementation agent verifies telemetry retry behavior in Northstar Platform's provisioned `mission-control` environment. A workflow can add `mission-control-api` and `mission-control-ui` aliases to the session, while the agent authenticates to those applications using credentials it legitimately holds. The public environment URL does not become a general network permission.

## Administrator reference: where secrets belong

Provider configuration stores *environment-variable names*, URLs, IDs, and non-secret routing metadata. The environment of the relevant host service stores the values. Typical examples are `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_DEVOPS_PAT`, `AZURE_ARTIFACTS_PAT`, `DISCORD_BOT_TOKEN`, `EVANDER_WORKER_TOKEN`, and bridge/provider tokens.

Keep secrets out of:

- session manifests and workflow payloads;
- Git remote URLs, config, ref names, and registry records;
- guest images and cloud-init templates;
- command arguments and logs;
- add-on state and communication-target addresses.

The repo-root `secrets.env` can be loaded by supported systemd templates, but it is still an operator-managed secret file. Check its ownership and mode. A health endpoint may report whether a key was loaded; it must not return the key.

There are narrow exceptions where a guest receives a short-lived or scoped credential: the MCP session token, a session-scoped restricted Git key, a one-time response/consultation token, or an ephemeral OpenAI Realtime client secret. These are capabilities with a bounded audience and purpose, not copies of the long-lived host credential.

### Microsoft Graph mailbox credentials

A Graph mailbox can use a static access-token environment reference for compatibility, but the
preferred application path names three host environment variables: tenant ID, client ID, and
client secret. Evander validates that the client-credentials set is complete, lazily exchanges the
values at the tenant's OAuth 2.0 token endpoint using the Graph `.default` scope, and sends only the
acquired bearer token to Microsoft Graph. Neither the application secret nor the acquired token is
written to a guest, session manifest, communication target, or prompt.

Acquired tokens live in a process-local cache keyed by the complete credential identity, including
authority, tenant, client, secret, and scope. The cache is shared across provider object rebuilds,
serializes concurrent acquisition for the same identity, and refreshes shortly before expiry.
Changing a credential value selects a new cache entry, so host-side secret rotation does not keep
using the old token. Acquisition errors are redacted; operators should record the mailbox ID and
environment-variable names, never their values.

## Administrator runbook

First verify the listeners locally on the control plane:

```bash
curl -fsS http://127.0.0.1:11434/healthz
curl -fsS http://127.0.0.1:11666/healthz
curl -fsS http://127.0.0.1:11777/healthz
curl -fsS http://127.0.0.1:11881/healthz
```

Then verify service and firewall state:

```bash
sudo systemctl status evander-openai-relay.service
sudo systemctl status evander-nuget-relay.service
sudo systemctl status evander-http-target-relay.service
sudo systemctl status evander-npm-audit-facade.service
sudo nft list table inet evander
```

Inspect relay logs without printing service environments:

```bash
sudo journalctl -u evander-openai-relay.service -n 200 --no-pager
sudo journalctl -u evander-nuget-relay.service -n 200 --no-pager
```

Finally test *from the execution environment*, because host-local success does not prove guest or worker routing. For Codex in a VM:

```bash
source ~/.profile
codex exec --skip-git-repo-check -C /tmp "Reply with exactly: relay works"
```

For NuGet, inspect the generated sources and perform a restore that is expected to be satisfiable by the relay/cache:

```bash
dotnet nuget list source
dotnet restore <solution-or-project>
```

When diagnosing, preserve the separation of concerns: a DNS or firewall failure is not an MCP policy failure; a `401` from the HTTP target application is not an Evander token failure if the relay accepted the request; a provider `429` is not worker unreachability. Follow one request from session identity, to relay decision, to upstream response.

## Source trail

- `config/firewall.env.example` and `scripts/apply_firewall.sh` — guest-facing host-port defaults and nftables forwarding policy.
- `config/cloud-init-user-data.yaml` and `scripts/bootstrap_codex_vm.sh` — guest session environment and relay-aware Codex configuration.
- `src/evander/relays/openai.py` — session authentication, provider routing, endpoint allowlists, web-search policy, streaming, and usage capture.
- `src/evander/effects/providers/agent_session.py`, `src/evander/codex_auth.py`, and
  `src/evander/worker/codex_egress_proxy.py` — public-only subscription selection, preserved
  private-provider routing, and restricted worker egress.
- `src/evander/email/graph_provider.py` — host-side Graph client-credentials exchange, shared
  credential-keyed token cache, and renewal.
- `src/evander/relays/nuget.py` and `scripts/bootstrap_nuget_vm.sh` — public/private feed proxying, URL rewriting, and guest NuGet layout.
- `src/evander/relays/http_target.py` — session aliases, guest-managed auth, path validation, and redirect confinement.
- `src/evander/relays/npm_audit_facade.py` — split npm package/audit routing.
- `tests/test_openai_relay.py`, `tests/test_codex_auth_modes.py`,
  `tests/test_email_providers.py`, `tests/test_nuget_relay.py`, `tests/test_app_relay.py`,
  `tests/test_npm_audit_facade.py`, and `tests/test_firewall_defaults.py` — relay, private-route,
  Graph credential, and network-boundary regression coverage.
