# 3. Trust Model and Security Boundaries

> **At a glance.** Evander assumes that agent-generated actions and isolated workloads are not entitled to the host's ambient authority. The host keeps secrets and policy, the workload receives an expiring session, and every useful crossing—models, MCP tools, packages, external HTTP, Git, and worker dispatch—uses a narrow surface with its own authentication and scope. Isolation reduces the blast radius; it does not replace policy, approval, provenance, or audit.

## Start with zones, not processes

The security model is clearer when the system is divided into trust zones:

| Zone | What it may hold | What it must not assume |
|---|---|---|
| Operator plane | Human identity, RBAC, privileged Admin Web session | That a browser login grants an agent capability |
| Host control plane | Real provider secrets, policy, sessions, workflow state, managed Git, audit | That data from a guest, webhook, model, or provider is trustworthy |
| Isolated execution | Source workspace, bounded session configuration, temporary credentials or proof tokens | General internet, host filesystem access, or ambient provider credentials |
| Worker VM | Worker bearer-token verifier, Docker runtime, durable session directories | Authority beyond jobs accepted from its configured control plane |
| External systems | Models, Azure DevOps, Git hosts, communication and HTTP targets, package sources | That a request claiming to be “the agent” is sufficient without the relevant relay/provider credential |

The default execution boundary is a libvirt VM. The additive container path places Docker jobs on a dedicated worker VM; the VM is still the stronger outer boundary. Containers and VMs do not have identical isolation properties, which is why Chapter 9 states their separate lifecycle and sandbox contracts.

![Evander trust zones and the narrow crossings between them](../../assets/evander-user-manual/diagrams/02-trust-zones.svg)

<p class="figure-caption">Figure 3.1 — Authority stays in its zone and crosses only through authenticated, scoped surfaces.</p>

## The authority chain

An action is permitted by an intersection, not by one master switch:

```text
directory policy
  AND active session grant
  AND authenticated/bound transport
  AND argument constraints
  AND provider/target/repository scope
  AND approval or workflow lease when required
```

Removing any term denies or prevents the action. This is intentional defense in depth. If Imani's Northstar session can connect to port 8000 but `send_email` is not visible, the socket does not upgrade the session. If the tool is visible but its recipient violates the directory and session allowlists, the call is rejected. If the payload is allowed but approval-required, execution pauses. If an approved payload changes, exact binding rejects the retry.

The human plane is separate. An operator's Admin Web permission to edit Northstar workflows does not become Imani's MCP permission. Conversely, a session granted a tool cannot edit users or unlock privileged Admin Web operations.

## Default-deny workload networking

Guests and worker containers do not receive general internet access by default. The host firewall admits explicit guest-to-host ports needed for controlled services. Those firewall openings are routing prerequisites, not capability grants.

The standard guest-facing paths are:

- SSH for restricted Git and controlled host/guest administration;
- the MCP gateway;
- model, NuGet, HTTP-target, and npm-audit relays;
- optional operator-deployed package caches.

The container worker defaults to Docker network mode `none`. A different mode is valid only when the worker deployment explicitly enables its egress policy and supplies an allowlist. The worker backend is not a general network escape hatch.

This posture changes how dependencies are supplied. Base VMs remain small; host-side bootstraps seed optional toolchains. Package traffic uses configured relays or caches. Large immutable worker toolchains belong in runner images or read-only cache mounts rather than being downloaded from arbitrary sites during a prompt.

## Secrets remain host-owned

Live secrets belong in host environment files, a secret manager feeding those environments, or other host-owned provider configuration. Directory JSON and workflow packs normally refer to an environment-variable name such as a PAT or token variable; they should not contain the secret value.

The OpenAI relay demonstrates the pattern: the guest is configured with a host relay URL and a placeholder API key suitable for client compatibility. The host relay authenticates upstream with the real credential and keeps the upstream client alive for the full streaming response. The guest never needs the real key.

Not every relay injects a host credential. The HTTP target relay is session-scoped and checks an allowlisted base URL, but forwards authentication headers and cookies supplied by the guest request. Its guarantee is target confinement, not secret substitution. Treating it as a host credential broker would be a serious design error.

Secrets can still leak through payloads, logs, prompts, source files, or provider responses if an operator places them there. Redaction reduces accidental disclosure in approval and audit views; it is not retroactive secret revocation. Rotate a credential whenever exposure is plausible.

## Session capability and MCP proof

A session manifest ties a workload to a directory, expiry, execution identity, allowed tools, approval behavior, allowlists, argument policies, quotas, and correlation metadata. It is the durable capability description.

MCP adds proof and binding:

1. A session-specific proof token is generated when the session is prepared. Session MCP-auth metadata retains the issued token for guest provisioning together with its SHA-256 verifier, so that metadata is secret-bearing and must not be exposed through ordinary operator payloads.
2. The client declares a session in the connection query or `x-evander-session-id` header and presents the proof token through a supported header or query field.
3. Once the MCP transport receives its `mcp-session-id`, Evander persists the transport-to-session binding in `runtime/mcp-transport-bindings.json`.
4. A later attempt to reuse that transport for a conflicting Evander session is rejected.
5. `tools/list` and tool invocation are filtered or denied for missing, invalid, or expired session context.

Passing a session ID as a tool argument is retained only as a compatibility shape; the gateway binds the authoritative session from the connection and rejects a conflict. Session identifiers are correlators, not bearer secrets.

## Tools, targets, and arguments

Tool policy has several independent dimensions:

- **Visibility:** whether the tool appears in the session's MCP catalog.
- **Directory denial:** an ancestor can mark a tool denied; a child cannot undo that deny.
- **Session grant:** the manifest or a valid workflow-scoped lease must grant it.
- **Argument policy:** required, fixed, pattern, enum, allowlist, and denylist rules constrain payload fields. Inherited and session rules both apply.
- **Resource allowlists:** contacts, scripts, channels, users, mailboxes, folders, files, search, and HTTP targets narrow provider-specific access.
- **Approval:** selected calls require a human decision bound to the request.

This lets Northstar grant Imani a useful tool without granting its most general form. A script tool can be restricted to named scripts; a communication tool can be restricted to configured target IDs; an HTTP tool can select only a session metadata alias whose base URL the host already accepted.

## Git is the code boundary

Evander moves code through Git, not through MCP file payloads. The host registers or refreshes an upstream into an Evander-owned bare repository. A session receives a managed repository ID; the control plane resolves the internal path and provisions a scoped SSH key. The restricted SSH wrapper limits permitted operations and refs.

Agent writes belong under `refs/heads/agents/<session-id>/<topic>`. Workflows consume the exact SHA from the triggering push, pin it, and promote or publish it with explicit lease checks. This prevents a time-of-check/time-of-use swap in which a mutable branch advances after review.

The managed repository and ephemeral host worktrees live beneath the runtime root. Exposing a trusted host checkout path to an agent would bypass the ownership, cleanup, and provenance model.

## Provider and worker boundaries

Provider adapters run on the host. They resolve settings for the selected directory and use host credentials to call Azure DevOps, communication systems, or other integrations. Provider output remains untrusted workflow input: schemas, gates, and bindings validate its shape before effects consume it.

The container worker API is authenticated with a bearer token retained by the control plane. Jobs receive a sanitized execution specification and explicit mounts/configuration. The protocol intentionally lacks general secret transport and arbitrary host-path transport. A compromised worker remains serious—it sees its session workspaces and can attack its own VM—but it should not automatically inherit unrelated control-plane secrets or repositories.

## Approval is a boundary, not absolution

An approval request includes a digest of the real payload while presenting a redacted copy for review. Exact-payload mode, expiry, use count, session, and tool checks prevent casual reuse. Operator decisions require identity and reason and enter the audit trail.

Approval does not prove the requested action is wise or the displayed summary is complete. The reviewer must inspect intended effect, relevant redacted fields, session, directory, tool, and surrounding workflow context. Highly sensitive content should be represented by safe references rather than hidden behind so much redaction that the decision becomes meaningless.

Workflow-scoped MCP leases are similarly narrow. They can add a tool for one session and workflow run, with expiry and usage limits. They cannot override a directory deny or eliminate a tool's per-invocation approval requirement.

## Failure behavior and containment

| Failure or attack | Expected boundary response |
|---|---|
| Guest calls the public model endpoint directly | Default networking blocks it; the supported client points at the host relay. |
| Stolen session ID without proof token | MCP authentication fails; the ID alone is insufficient. |
| One MCP transport declares a second session | Persistent binding detects the conflict and rejects it. |
| Child directory tries to re-enable an ancestor-denied tool | Effective policy re-applies the inherited deny. |
| Approved invocation changes an argument | Exact payload digest check rejects it unless policy explicitly chose a broader mode. |
| Agent pushes outside its namespace | Restricted SSH/ref enforcement rejects the write. |
| Workflow pack contains a secret value | Validation cannot make that safe; remove it, rotate the secret, and replace it with an env reference. |
| Audit SQLite indexing fails | Append-only JSONL remains the canonical record; the index can be rebuilt. |
| Worker cleanup fails | Host records `cleanup_failed`; operators retain evidence and retry cleanup. |
| Host relay is compromised | The relay's upstream credential is exposed; isolate the host and rotate it. Workload isolation does not contain a host compromise. |

## Practical security runbook

For an unexpected capability, work from outside inward:

1. Identify the runtime directory and session ID.
2. Confirm the session is active and bound to the expected directory and execution target.
3. Inspect effective directory policy and provenance, not just the child overlay.
4. Inspect the session's allowed tools and argument/resource allowlists.
5. Inspect MCP transport binding, approval or lease IDs, and the matching tool-call audit record.
6. Verify the relay, Git wrapper, or provider enforced its own target scope.

Useful first checks include:

```bash
evander-cli status --runtime-dir runtime --json
evander-cli list-sessions --runtime-dir runtime --all
evander-cli directories config-show root/northstar-platform/mission-control \
  --domain policy --runtime-dir runtime
sudo nft list ruleset
sudo ss -ltnp
```

If a secret may have crossed a boundary, stop the affected session or service, preserve relevant logs and audit records, rotate the credential at its authority source, update the host secret, and then restart/reissue the smallest affected scope. Deleting a redacted UI record does not invalidate a credential.

## Source trail

- `docs/architecture.md`, `docs/execution-backends.md`, `docs/container-worker-backend.md`, and `docs/git-remotes.md` — supported trust and transport boundaries.
- `config/firewall.env.example`, `scripts/apply_firewall.sh`, and `scripts/git_ssh_command_wrapper.sh` — network defaults and restricted Git enforcement.
- `src/evander/models.py`, `src/evander/policy.py`, and `src/evander/policy_config.py` — session capabilities, tool decisions, argument rules, approvals, and inherited denies.
- `src/evander/gateway/app.py`, `src/evander/gateway/session_context.py`, `src/evander/session_auth.py`, and `src/evander/repositories/mcp_transport_bindings.py` — MCP proof and persistent connection binding.
- `src/evander/relays/openai.py` and `src/evander/relays/http_target.py` — contrasting model-secret and target-confinement relay semantics.
- `src/evander/admin/services/managed_git_service.py` and `src/evander/worker/` — managed Git and worker trust boundaries.
- `tests/test_policy.py`, `tests/test_gateway.py`, `tests/test_firewall_defaults.py`, and `tests/test_managed_git_workflow_contract.py` — boundary enforcement tests.
