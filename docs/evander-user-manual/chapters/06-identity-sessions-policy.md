# 6. Identity, Sessions, RBAC, and Policy

> **At a glance.** Evander has two deliberately separate authorization planes. Human operators authenticate to Admin Web and receive directory-scoped RBAC permissions plus a transient privileged-operation lock. Agents receive expiring session manifests whose tools and resources are intersected with effective directory policy. Workflow, transport, provider, and external identities correlate actions, but none substitutes for either human RBAC or session capability.

## The identity stack

“Who did this?” can refer to several valid identities:

| Identity | Examples | What it governs |
|---|---|---|
| Human operator | An operator's Admin Web login | Reading or mutating operator surfaces through RBAC |
| Directory scope | `root/northstar-platform/mission-control` | Which inherited configuration and RBAC assignments apply |
| Agent/session | Imani's `session_id` and optional VM/provider binding | MCP visibility, allowlists, quotas, expiry, execution correlation |
| Workflow | Run ID, graph ID/revision, trace ID, node execution ID | Why the action was scheduled and which configuration produced it |
| MCP transport | Protocol `mcp-session-id` bound to an Evander session | Which session the connection is allowed to represent |
| Provider identity | ADO project, Git account, Teams app, relay credential | Which external service principal the host uses |
| Team identity | Mira, Priya, Imani, Rowan, and Riley as AI Team agents | Add-on task, wake, review, governance, and release responsibility |

The audit trail combines several of these so an operator can distinguish “an operator approved a request for Imani's session during workflow run X” from the less useful “a tool ran.”

## Admin Web authentication

Admin Web supports three current modes:

| Mode | Purpose |
|---|---|
| `none` | An explicitly unauthenticated/local mode. Appropriate only inside a trusted, otherwise protected operator boundary. |
| `local_password` | Local users and PBKDF2 password credentials, with an environment-supplied bootstrap/break-glass account. |
| `azure_sso` | Azure SSO identity mapped to the local user/RBAC store. |

If no mode is supplied, a configured admin password selects `local_password`; otherwise the process falls back to `none`. Production operators should set the mode deliberately rather than relying on that convenience behavior.

The durable user, group, assignment, and direct-grant store is SQLite at `runtime/admin-web.db`. Legacy JSON user data is migration input. Local passwords use PBKDF2-HMAC-SHA-256 with 200,000 iterations and a 32-byte random salt. The database stores a verifier, not the plaintext password.

Successful authentication creates an HMAC-signed `evander_admin_session` cookie. The default session TTL is 43,200 seconds (12 hours), with a minimum of 60 seconds. Set a stable `EVANDER_ADMIN_WEB_SESSION_SECRET`; if absent, the process generates one, so a restart invalidates existing cookies. Set secure-cookie and reverse-proxy/TLS behavior as part of a complete exposure design.

The first-owner bootstrap is guarded. The configured local bootstrap account or Azure bootstrap allowlist can establish the first usable owner, but later authorization comes from durable users and assignments. User-management logic protects the effective owner boundary so an operator cannot casually remove or disable the last owner.

## Directory-scoped RBAC

Admin RBAC has this permission catalog:

| Permission | Meaning |
|---|---|
| `admin.owner` | Full permission catalog |
| `users.manage` | Manage users and relevant assignments |
| `groups.manage` | Manage groups and permissions |
| `workflows.read` | Read workflow definitions |
| `workflows.write` | Modify workflow definitions; implies read for direct grants |
| `workflow_packs.manage` | Manage workflow packs |
| `workflow_runs.read` | Inspect workflow runs |
| `workflow_runs.control` | Operate workflow runs |
| `team_chats.read_all` | Read Team chats beyond ordinary participant scope |

Built-in groups are `owner`, `operator`, `workflow-maintainer`, `workflow-viewer`, `auditor`, and `viewer`. Owner and operator are required group definitions. `admin.owner` expands to the full catalog. Operator receives workflow read/write, pack management, run read/control, and Team chat read-all; it does not implicitly receive user/group management unless configured through another group or ownership.

A group assignment at a directory applies to that directory and descendants. Thus a Northstar assignment at `root/northstar-platform` applies to `mission-control` but not to an unrelated root child. Direct workflow grants are limited to workflow read or write, and write expands to read. Effective permission is the union of matching group assignments and matching direct workflow grants.

Scope inheritance is downward only. An assignment on `mission-control` does not authorize its parent or sibling.

## The privileged-operation lock

Authentication and RBAC answer whether an operator *may* perform an action. Sensitive mutation routes add a transient lock: the current authenticated Admin Web session must also be marked as privileged-unlocked.

Login begins locked. Calling the unlock endpoint marks the current cookie session in process memory; it is not a second password or SSO challenge. Lock, logout, cookie expiry, or process restart removes the mark. Routes call `request_has_privileged_operations`, optionally with a required permission and directory scope, so both authorization and the transient state must pass.

This is a guard against accidental clicks and unattended open consoles, not multi-factor authentication. It must not be described as a second human approval. Workflow/MCP approvals are a separate durable mechanism with payload binding and an audit decision.

## Session manifests: agent identity as a bounded capability

A `SessionManifest` includes:

- `session_id`, creation and expiry timestamps;
- VM, execution-provider, and model bindings where selected;
- canonical `directory_path`;
- allowed MCP tools, approval-required tools, and per-tool grant settings;
- allowed contact and script IDs;
- Discord, Teams, mailbox, folder, recipient, and domain allowlists;
- tool argument policies;
- communication file limits and enablement;
- web-search enablement and quotas;
- metadata for workflow, issue, Team, Git, HTTP-target, and execution correlation.

An empty or absent permission is not interpreted as broad ambient access. Unknown and expired sessions are denied. Renewal or reissue is an explicit operator/domain action; an old workload cannot extend its own expiry simply by continuing to send traffic.

Northstar can create Imani's session with only the tools needed to inspect the work item, operate the managed repository workflow, and report results. Rowan's review session can have a different tool set even though both belong to the same directory.

![Admin Web session detail for Imani's synthetic telemetry-retry session](../../assets/evander-user-manual/screenshots/admin-sessions.png)

<p class="figure-caption">Figure 6.1 — Session state, scope, backend, and tool grants meet in one operator view. Sanitized Northstar fixture.</p>

## How policy is evaluated

For a tool request, the policy engine effectively asks:

1. Does the session exist and remain unexpired?
2. What is the effective policy for its directory?
3. Is the tool denied by directory policy or a special capability profile?
4. Is it granted by `allowed_tools`, or by a valid workflow-scoped approval lease for this session and run?
5. Are any structural prerequisites present—for example a bound execution for resume behavior or a feedback stream for feedback tools?
6. Do inherited and session argument/resource rules permit this payload?
7. Does the tool require per-invocation approval, and is the presented approval valid?

Tool visibility and tool execution use the same underlying policy decisions, but visibility cannot pre-validate a future payload. A tool can appear in `tools/list` and still reject a disallowed recipient, script, target, or argument.

Directory policy supplies safe defaults: approval TTL 3,600 seconds, session TTL 28,800 seconds, email sending disabled, and communication file send/receive disabled. Live directory overlays may alter supported values. These defaults are not a substitute for explicitly reviewing a production root policy.

Argument-policy operators include required fields, fixed values, patterns, enums, allowlists, and denylists. The resolved directory rule list and the session rule list both apply; a permissive session rule does not erase a directory constraint. Within directory inheritance, ordinary list replacement follows the merge rules in Chapter 5.

## Workflow-scoped tool leases

A `SessionToolApprovalLease` temporarily grants named MCP tools to exactly one session and workflow run. It contains a lease ID, effect execution correlation, per-tool approval settings, optional notification targets, expiry, revocation time, and used counts.

The workflow context must present matching run and lease IDs. Use is consumed under the repository's atomic update path. A lease cannot:

- revive an expired session;
- cross to another session or workflow run;
- override an inherited directory deny;
- bypass argument/resource policy;
- remove mandatory or configured per-invocation approval.

This distinction is useful for the Northstar flow. The project's graph may reserve one tool for Imani during a bounded implementation step without putting that tool in every long-lived session. The lease says the workflow may expose the tool; a separate exact approval may still be required for a sensitive call.

## Failure behavior

| Symptom | Decision that failed | Typical cause |
|---|---|---|
| Admin Web redirects or returns unauthenticated | Human authentication | Missing/expired/bad cookie, restart with generated secret, SSO or password failure |
| Authenticated user sees read-only UI | RBAC | No matching directory assignment or direct grant |
| Mutation says privileged operations are locked | Transient guard | Current cookie session has not been unlocked or process restarted |
| User can edit Northstar but not a sibling | Directory scope | Expected descendant-only assignment behavior |
| Agent's `tools/list` is empty | Session/MCP identity | Unknown, expired, unproved, or incorrectly bound session |
| Tool is missing | Visibility policy | Not granted, directory-denied, profile-denied, or structurally unavailable |
| Visible tool rejects call | Payload policy | Argument, target, quota, lease, or approval failed |
| Lease works in one run but not another | Workflow scope | Run/session mismatch is intentional |
| Last owner mutation is rejected | Recovery invariant | Store prevents loss of the effective owner boundary |

## Practical runbook

For a human access issue, collect: auth mode, login, current directory, requested permission, matching group assignments, direct grants, and privileged-lock state. Do not inspect or copy password-verifier fields unless incident response requires it.

For an agent access issue:

```bash
evander-cli list-sessions --runtime-dir runtime --all
evander-cli directories config-show root/northstar-platform/mission-control \
  --domain policy --runtime-dir runtime
evander-cli list-approvals --runtime-dir runtime
```

Then use Admin Web's session tool-visibility view or the gateway audit entry to identify the exact reason. Compare all of the following before changing policy: normalized tool name, session expiry, directory, session grant, inherited deny, structural prerequisite, argument payload, workflow run/lease, approval request, and remaining uses.

Use the lifecycle commands rather than editing `sessions.json`:

```bash
evander-cli renew-session <session-id> --runtime-dir runtime
evander-cli revoke-session <session-id> --runtime-dir runtime
```

Check each command's `--help` for the required renewal/expiry arguments in the installed revision. Revoke the smallest affected session or lease when authority is no longer appropriate; changing a human's group does not automatically revoke an already issued agent session.

## Source trail

- `src/evander/admin/web/shared.py`, `src/evander/admin/web/user_store.py`, and `src/evander/admin/web/rbac.py` — authentication, cookies, password storage, groups, permission scope, and privileged lock.
- `src/evander/admin/web/users.py` and `tests/test_admin_web_user_store.py` — user/group administration, bootstrap, migration, and owner protection.
- `src/evander/models.py` — `SessionManifest`, `SessionToolApprovalLease`, and approval models.
- `src/evander/policy.py` and `src/evander/policy_config.py` — visibility, grants, denial, structural checks, arguments, approvals, and defaults.
- `src/evander/repositories/session_manifests.py` and `src/evander/repositories/session_tool_approval_leases.py` — durable session and lease mutation semantics.
- `tests/test_admin_web_rbac.py`, `tests/test_policy.py`, `tests/test_gateway.py`, and `tests/test_session_tool_approval_leases.py` — authorization-plane invariants.
