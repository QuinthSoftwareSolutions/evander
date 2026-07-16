# 3. Trust Model and Security Boundaries

> **At a glance.** An agent may need to write code, call tools, and reach outside services, but it should not inherit all the power of the computer running Evander. Three promises define the boundary: host credentials stay on the host, the agent receives controlled routes rather than open internet access, and every action remains limited by the current job's permissions. Evander checks those promises at several points so that one mistaken or compromised component does not automatically unlock everything else.

## Three promises to keep in mind

The full security design becomes easier to follow when it is reduced to three promises.

1. **The agent cannot freely use host credentials.** When the agent harness[^agent-harness] sends a prompt to the configured AI model, the request goes through Evander. The real provider key stays on the host; the temporary workspace does not receive a copy.
2. **The agent cannot freely reach the internet.** If it needs a package or an approved test system, Evander provides a specific route. That route does not become permission to browse or contact arbitrary destinations.
3. **The agent cannot exceed this job's permissions.** A telemetry-retry job may inspect one work item, use selected tools, and work in one registered repository. It does not thereby gain access to every project, recipient, script, or administrative control.

Everything else in this chapter explains how Evander keeps one or more of those promises. On a first read, the promises and examples are enough. The sidebars give the exact enforcement details needed for security review, administration, and incident response.

## What Evander protects—and from whom

Evander separates people, control, temporary work, and outside systems. Each area is trusted for a different purpose:

| Area | What belongs there | What crossing the boundary requires |
|---|---|---|
| Operator access | A person's sign-in, role, approvals, and privileged Admin Web state | A human permission must never be silently reused as an agent permission. |
| Evander host | Real provider credentials, policy, sessions, workflow state, managed Git, and audit evidence | Input from an agent, model, webhook, or provider must be checked before it changes trusted state. |
| Temporary workspace | The job's source checkout, tools, and limited session configuration | Access to host files, outside services, or another project must be explicitly provided. |
| Worker machine | The container runtime and session workspaces for jobs assigned to that worker | A worker may run accepted jobs; it does not choose unrelated control-plane authority. |
| Outside services | Models, Git hosts, work trackers, communication systems, package sources, and approved HTTP targets | Requests pass through the relevant credentialed provider or restricted relay. Merely claiming to be an agent is not enough. |

![Evander trust zones and the narrow crossings between them](../../assets/evander-user-manual/diagrams/02-trust-zones.svg)

<p class="figure-caption">Figure 3.1 — Authority stays in its zone and crosses only through authenticated, scoped surfaces.</p>

The figure's practical message is simple: the temporary worker reaches useful capabilities through Evander; it does not inherit the host or operator's general authority.

> <span class="sidebar-kicker">Technical sidebar</span> **Execution boundaries are not interchangeable**
>
> The default execution boundary is a disposable `libvirt_vm`. The additive `container_worker_vm` backend runs disposable Docker containers inside a dedicated worker VM; that VM remains the stronger outer boundary. Containers and VMs do not have identical isolation properties, so Chapter 9 defines their lifecycle and sandbox contracts separately. Human access is independently controlled by authentication, role-based access control (RBAC), directory scope, and the transient privileged-operation lock.

## Permission is a series of checks

A request is allowed only when every applicable check succeeds. No single switch grants general access:

```text
right project rules
  AND active job
  AND verified connection
  AND allowed tool and input
  AND allowed destination
  AND approval when required
= request may run
```

Read the chain from top to bottom: every line must be true, and passing one line does not replace any of the others.

For example:

- A session may reach the tool gateway, but it still cannot use an email tool that was not granted.
- The email tool may be visible, but a recipient outside the job's approved list is still rejected.
- An operator may approve one sensitive request, but changing that request's input makes it a different request.
- An operator's permission to edit a workflow does not become the agent's permission to edit users, unlock Admin Web, or call another tool.

Together, these independent checks provide **defense in depth**[^defense-in-depth]. If a check cannot decide safely, it **fails closed**[^fail-closed].

> <span class="sidebar-kicker">Deeper detail</span> **The exact authority intersection**
>
> The implementation evaluates directory policy, an active session grant, authenticated and session-bound transport, argument constraints, provider/target/repository scope, and any required approval or workflow-scoped lease. Removing any term denies or prevents the action. Network reachability is only a routing prerequisite; a session ID is only a correlator; tool visibility is only one policy result; and approval is valid only for its bounded request.

## Promise 1: host credentials stay on the host

Suppose a failing test needs analysis. The agent harness sends the relevant prompt and context to the configured AI model through Evander's model relay. The relay adds the real provider credential on the trusted host. The model's response returns to the agent, but the provider key does not.

The same ownership principle applies to work trackers, communication providers, and other host integrations. The agent asks for an allowed action; a host-side component uses the credential needed to perform it. A workflow or project setting normally names the host environment variable that contains a secret—it does not contain the secret value itself.

This arrangement limits routine exposure, but it cannot make careless handling harmless. A credential pasted into source code, a prompt, a tool argument, or a log has crossed the boundary. Redaction can reduce what later screens display; it cannot make an exposed credential trustworthy again.

> <span class="sidebar-kicker">Technical sidebar</span> **Two relays with different promises**
>
> The OpenAI relay demonstrates host-owned authentication. The guest receives a host relay URL and a placeholder API key for client compatibility. The relay authenticates upstream with the real credential and keeps the upstream client alive for the complete streaming response.
>
> The HTTP target relay has a different contract. It confines a session to an allowlisted base URL but forwards authentication headers and cookies supplied by the guest request. It does not inject a host credential. Treating target confinement as secret substitution would overstate the boundary.

> <span class="sidebar-kicker">For administrators</span> **Where secrets belong**
>
> Live values belong in host environment files, a secret manager that feeds those environments, or another host-owned provider configuration. Directory JSON and workflow packs should refer to environment-variable names. Secret-bearing session authentication metadata must not be exposed through ordinary operator payloads. If exposure is plausible, preserve evidence and rotate the credential at its authority source; deleting or redacting a record is not revocation.

## Promise 2: the agent gets controlled routes, not open internet

Evander uses **default-deny egress**[^default-deny-egress]. Suppose the telemetry-retry job needs a package and access to a provisioned test API. Evander can provide a package cache and one approved HTTP target. The agent can use those routes for the job, but it does not gain a general path to unrelated websites or services.

The same distinction applies to every reachable host port. Reaching the gateway does not grant every tool. Reaching SSH does not grant every repository or branch. Reaching a package service does not turn it into a general web proxy. The service at the end of the route performs its own scope and identity checks.

This changes how environments are prepared. Optional toolchains can be seeded from the host, package traffic can use controlled relays or caches, and larger worker toolchains can be placed in runner images or read-only cache mounts. A prompt should not need arbitrary internet access simply to recreate a known development environment.

> <span class="sidebar-kicker">Technical sidebar</span> **Network enforcement**
>
> Guests do not receive general internet access by default. The host firewall admits explicit guest-to-host routes: SSH on port `22`, MCP on `8000`, the model relay on `11434`, the NuGet relay on `11666`, the HTTP target relay on `11777`, and the npm-audit facade on `11881`. Optional package caches commonly use Nexus on `8081` and apt-cacher-ng on `3142`. An open route remains subject to session and service policy.
>
> Separately, the host uses controlled SSH into libvirt VMs for bootstrap, status, log, and maintenance operations. That host-to-guest administrative path is not a guest session capability and should not be confused with the guest-to-host restricted-Git route.
>
> The container worker defaults jobs to Docker network mode `none`. A deployment may choose a different mode only with explicit worker egress configuration and an allowlist. The worker backend is not a general network escape hatch.

## Promise 3: permission belongs to this job

A **session** is the job's temporary identity and permission card. It applies **least privilege**[^least-privilege] and connects the work to a project, an expiry time, and an execution environment. When it expires, its earlier ID does not preserve permission to continue.

For the telemetry-retry job, the session might allow the agent to inspect work item `MC-248`, run two named test scripts, use one managed repository, and publish a report only after approval. A review job in the same project may receive a different tool set. Neither session automatically inherits what the other can do.

Every tool request is then checked in the context of that session. The agent cannot widen its own permission by writing a different session ID into a tool argument, choosing a new recipient, or changing an approved payload.

> <span class="sidebar-kicker">Technical sidebar</span> **What the session manifest controls**
>
> The manifest binds a session to its directory, expiry, selected execution identity, allowed and approval-required tools, resource allowlists, argument policies, quotas, and correlation metadata. Tool policy has independent dimensions: directory denial, session or workflow-lease grant, catalog visibility, argument constraints, provider-specific resource allowlists, and per-invocation approval. An ancestor directory deny cannot be undone by a child.
>
> Argument rules can require fields, fix values, match patterns or enumerations, and apply allowlists or denylists. Resource rules can narrow contacts, scripts, channels, users, mailboxes, folders, files, search, and HTTP targets. Inherited rules and session rules both apply.

> <span class="sidebar-kicker">Deeper detail</span> **How MCP proves which session is calling**
>
> 1. Evander generates a session-specific proof token while preparing the session. MCP-auth metadata retains the issued token for guest provisioning together with its SHA-256 verifier, making that metadata secret-bearing.
> 2. The client declares the session in the connection query or `x-evander-session-id` header and presents the proof through a supported header or query field.
> 3. After MCP assigns its `mcp-session-id`, Evander persists the transport-to-session binding in `runtime/mcp-transport-bindings.json`.
> 4. Reusing that transport for a conflicting Evander session is rejected.
> 5. `tools/list` and each invocation are filtered or denied when session context is missing, invalid, conflicting, or expired.
>
> A session ID supplied as a tool argument remains only as a compatibility shape. The authenticated connection supplies the authoritative identity and rejects a conflict. Session identifiers are correlators, not bearer secrets.

## Code follows a separate controlled route

Evander transports source code through Git rather than embedding files in tool messages. The agent receives access to a registered repository, works in its own checkout, and can write only through the Git route and branch area granted to the session.

The important operator-level result is that review and release refer to one exact code version. A branch name can move; a commit SHA identifies the snapshot that was actually produced. The release path therefore uses the reviewed SHA, not whichever version happens to occupy a familiar branch name later.

> <span class="sidebar-kicker">Deeper detail</span> **Managed-Git enforcement**
>
> The host registers or refreshes an upstream into an Evander-owned bare repository. The session receives a `managed_repo_id`; the control plane resolves the internal path and provisions a scoped SSH key. The restricted SSH wrapper constrains operations and refs. Agent writes belong below `refs/heads/agents/<session-id>/<topic>`.
>
> Workflows consume the exact SHA from the triggering push, pin it, and promote or publish it with explicit lease checks. This prevents a **TOCTOU race**[^toctou] in which a mutable branch advances after review. Managed repositories and ephemeral worktrees stay below the runtime root; exposing a trusted host checkout would bypass ownership, cleanup, and provenance controls.

## Outside systems and worker machines remain separate

Evander may trust a host credential to contact a work tracker without trusting everything the tracker returns. A title, comment, webhook, or model response is still input. Workflows check its shape and route before an effect is allowed to change trusted state.

A worker machine is similarly trusted to run accepted jobs, not to invent new ones. If a worker is compromised, the incident is serious: it can see its assigned session workspaces and attack its own VM. The protocol is designed so that this does not automatically reveal unrelated control-plane secrets or repositories.

> <span class="sidebar-kicker">Technical sidebar</span> **Provider and worker enforcement**
>
> Provider adapters run on the host, resolve settings for the selected directory, and use host credentials for Azure DevOps, communications, and other integrations. Provider output remains untrusted workflow input; schemas, gates, and bindings validate it before effects consume it.
>
> The container worker API uses a bearer token retained by the control plane. Jobs receive a sanitized execution specification and explicit mounts and configuration. The protocol intentionally omits general secret transport and arbitrary host-path transport.

## Approval is a boundary, not absolution

Some actions remain sensitive even when the tool, target, and input are otherwise allowed. Evander pauses those actions and asks an authorized operator to decide. The approval shows which session and tool are involved, the intended effect, when the request expires, and a safe-to-display view of the input.

Approval does not prove that the action is wise. The operator still needs to inspect the directory, job, tool, destination, intended effect, and surrounding workflow. If redaction hides so much that the decision cannot be understood, the safer design is to pass a reference to protected content rather than ask for a blind approval.

An approval also does not promote the session. It authorizes the bounded request while all other checks remain in place.

> <span class="sidebar-kicker">Deeper detail</span> **Exact binding and workflow leases**
>
> An approval record binds a digest of the real payload to its session, tool, expiry, payload mode, and maximum use count while presenting a redacted copy for review. Operator decisions require identity and reason and enter the audit trail. In exact-payload mode, changing an argument invalidates the retry.
>
> A workflow-scoped MCP lease can add a tool for one session and workflow run with expiry and usage limits. It cannot override a directory deny or remove a tool's per-invocation approval requirement.

## When a boundary is challenged

The expected response is usually denial, a controlled pause, or a retained failure record—not silent expansion of authority:

| Failure or attack | Expected boundary response |
|---|---|
| Agent calls the public model endpoint directly | Default networking blocks it; the supported client points at the host relay. |
| Someone has a session ID but not its proof token | MCP authentication fails; the ID alone is insufficient. |
| One MCP connection declares a second session | The persisted binding detects the conflict and rejects it. |
| A child directory tries to re-enable an ancestor-denied tool | Effective policy reapplies the inherited deny. |
| An approved invocation changes an argument | Exact payload binding rejects it unless policy deliberately selected a broader mode. |
| The agent pushes outside its branch area | Restricted SSH and ref enforcement reject the write. |
| A workflow pack contains a secret value | Remove it, rotate the credential, and replace the value with a host environment reference. |
| Audit indexing fails | Append-only JSONL remains the canonical tool-call audit; the SQLite index can be rebuilt. |
| Worker cleanup fails | The host records `cleanup_failed`; evidence remains available while an administrator retries cleanup. |
| A host relay is compromised | Its upstream credential may be exposed. Isolate the host and rotate the credential; workload isolation does not contain a host compromise. |

## If you are concerned about access or a secret

Start with the observable job, not host firewall commands.

1. In Admin Web, select the directory where the work ran and open the relevant session.
2. Record what looked wrong: the tool or service, target, approximate time, and expected behavior.
3. Capture the session ID, workflow run or trace ID, approval request ID, tool-call record, and execution or VM ID when available.
4. If your role permits it, use the supported control to stop or contain the affected session. Otherwise contact an Evander administrator. Do not delete session, approval, audit, or workflow records.
5. If a credential may have appeared in source, a prompt, a payload, a response, or a log, treat the exposure as plausible until checked. Preserve evidence and ask the credential owner to rotate it at the source.

Deleting a redacted UI record, destroying a temporary workspace, or removing a session from a list does not revoke an external credential. Rotation at the system that issued the credential is the decisive step.

> <span class="sidebar-kicker">Administrator runbook</span> **Host-level security checks**
>
> Work from the reported directory and session outward:
>
> 1. Confirm the runtime directory, session status, expiry, directory binding, and execution target.
> 2. Inspect effective directory policy and provenance rather than only the child overlay.
> 3. Compare allowed tools, argument rules, resource allowlists, and any workflow-scoped lease.
> 4. Inspect MCP transport binding, approval state, and the matching append-only tool-call audit event.
> 5. Verify that the relay, restricted Git wrapper, provider, or worker enforced its own target scope.
> 6. For possible secret exposure, contain the smallest affected service or session, preserve logs and audit evidence, rotate the credential at its authority source, update host secret storage, and then restart or reissue only the affected scope.
>
> Useful first checks include:
>
> ```bash
> evander-cli status --runtime-dir runtime --json
> evander-cli list-sessions --runtime-dir runtime --all
> evander-cli directories config-show root/northstar-platform/mission-control \
>   --domain policy --runtime-dir runtime
> sudo nft list ruleset
> sudo ss -ltnp
> ```

[^agent-harness]: The agent harness is the program that coordinates the agent's work: it sends prompts and context to the configured model, presents tools, and applies the model's responses in the working session.

[^defense-in-depth]: Defense in depth uses several independent controls so that bypassing or misconfiguring one does not remove the others.

[^fail-closed]: A check fails closed when an error or uncertainty causes denial rather than permission.

[^default-deny-egress]: Egress is outbound network traffic. Default-deny means it is blocked unless a specific route has been allowed.

[^least-privilege]: Least privilege gives each job only the access needed for its current purpose and duration.

[^toctou]: TOCTOU means “time of check to time of use”: the state validated during review changes before the later action uses it.

## Source trail

- `docs/architecture.md`, `docs/execution-backends.md`, `docs/container-worker-backend.md`, and `docs/git-remotes.md` — supported trust and transport boundaries.
- `config/firewall.env.example`, `scripts/apply_firewall.sh`, and `scripts/git_ssh_command_wrapper.sh` — network defaults and restricted Git enforcement.
- `src/evander/models.py`, `src/evander/policy.py`, and `src/evander/policy_config.py` — session capabilities, tool decisions, argument rules, approvals, and inherited denies.
- `src/evander/gateway/app.py`, `src/evander/gateway/session_context.py`, `src/evander/session_auth.py`, and `src/evander/repositories/mcp_transport_bindings.py` — MCP proof and persistent connection binding.
- `src/evander/relays/openai.py` and `src/evander/relays/http_target.py` — contrasting model-secret and target-confinement relay semantics.
- `src/evander/admin/services/managed_git_service.py` and `src/evander/worker/` — managed Git and worker trust boundaries.
- `tests/test_policy.py`, `tests/test_gateway.py`, `tests/test_firewall_defaults.py`, and `tests/test_managed_git_workflow_contract.py` — boundary enforcement tests.
