# 6. Identity, Sessions, RBAC, and Policy

> **At a glance.** Evander treats a signed-in person and a working agent as different kinds of
> identity. A person signs in to Admin Web and receives responsibility for particular operator
> functions and directories. An agent receives a temporary session describing what one job may
> use.
>
> This separation is why an audit record can say which person approved an action, which bounded
> agent session performed it, and which workflow asked for it. It also means that changing a
> person's access and revoking an agent's session are separate actions.

## Two identities, two kinds of responsibility

An operator's login answers: **which parts of Evander may this person view or change?** A session
answers: **what may this agent use for this particular job, and for how long?** Neither identity
automatically inherits the other's authority.

Three practical boundaries follow:

1. Signing in to Admin Web does not create an agent session.
2. An agent session does not let the agent act as the signed-in operator.
3. A workflow, approval, or visible tool can add context to a session, but none turns it into
   unrestricted host access.

The directory connects these worlds without merging them. A person may be responsible for one
directory and its descendants; an agent session also names the directory whose policy applies to
that job.

## What an operator may notice

| What you see | What it usually means | Useful next step |
|---|---|---|
| You can sign in, but a page or edit is unavailable | Authentication succeeded, but your effective group assignments do not grant that action in the selected directory | Confirm the directory and ask an owner to review your assignment |
| You can work in one project but not its sibling | Directory assignments flow downward to descendants, not sideways to siblings | Check that the correct project is selected |
| A sensitive edit says privileged operations are locked | Your current browser session is authorized but its temporary safety lock is still engaged | Review the action, then use the supported unlock control if appropriate |
| An agent has no visible tools | The session may be expired, unproved, incorrectly bound, or intentionally granted no tools | Record the session ID, directory, expiry, and visible status |
| A tool is visible but its call is denied | Seeing the tool did not pre-approve every recipient, target, argument, lease, or sensitive action | Capture the tool name, request, denial, session ID, and run ID |
| A workflow lease works in one run but not another | The lease is deliberately tied to one session and workflow run | Compare the session, run, lease, and expiry rather than broadening policy |

These distinctions are useful evidence. “The page loaded but the edit was denied” points to a
different boundary than “I could not sign in,” just as “the agent saw the tool but one call was
rejected” differs from “the tool was absent.”

## A session is the job's permission card

When Evander creates a session, it records the job's identity, directory, expiry, selected
execution context, and allowed capabilities. Most empty or absent permissions do not become broad
access. Two newer communication fields deliberately preserve a narrower compatibility distinction:
missing or `null` means that an older session still uses its legacy allowlists, while an explicit
empty list means no managed communication destinations or mailbox accounts. Unknown and expired
sessions are denied, and an old workload cannot prolong its own authority merely by continuing to
send traffic.

Two sessions in the same project can therefore differ. An implementation session may receive only
the tools needed to inspect a work item, operate the managed-repository workflow, and report its
result, while a review session receives a different set. Renewal or reissue is an explicit
operator or domain action.

An older session can show a **legacy communication policy** callout on the Sessions page. Converting
it prepares managed destination and mailbox selections for review; it does not save them
immediately. Review the proposed rows—especially outbound email recipients—then save the session
policy. Selecting no rows and saving is an explicit denial, not a return to legacy behavior.

![Admin Web detail for a synthetic implementation session](../../assets/evander-user-manual/screenshots/admin-sessions.png)

The operator view brings together the session's state, directory, execution backend, expiry, and
tool grants. It is the right starting point when an agent can work but cannot perform one expected
action.

<p class="figure-caption">Figure 6.1 — Session state, scope, backend, and tool grants meet in one operator view. Sanitized Northstar fixture.</p>

## The privileged-operations lock is a safety catch

Some sensitive Admin Web changes require both the necessary effective permission and an unlocked browser session.
Login begins locked. Unlocking marks only the current authenticated Admin Web session; locking,
logging out, cookie expiry, or an Admin Web restart removes that mark.

This is protection against an accidental click or an unattended open console. It is not a second
password, multi-factor authentication, or a human approval for an agent's tool call. Workflow and
MCP approvals are separate durable decisions bound to the requested action.

## How Evander decides whether an agent may use a tool

For an operator, the decision can be read as a short sequence of questions:

1. Is this a real, unexpired session?
2. Which directory owns it, and what rules apply there?
3. Is the tool forbidden, allowed for the session, or temporarily exposed by this workflow?
4. Does this type of tool have everything it needs—for example, an execution to resume?
5. Are the requested target and arguments within the job's limits?
6. Does this particular action require human approval?
7. If approval or a temporary workflow grant is presented, is it still valid for this session and
   run?

A tool can appear in the agent's catalog and still reject one disallowed recipient, script,
target, or argument. Visibility says the tool may be relevant to the job; it does not predict every
future request.

## Troubleshooting access

For a human-access problem, record the authentication mode if known, login name, selected
directory, page or action requested, exact message, and whether the privileged-operations lock is
shown as locked. An owner or administrator can then inspect the matching group assignments and
direct grants without exposing password-verifier data.

For an agent-access problem, start with the session page and the relevant tool-call or workflow
record. Capture:

- session ID, directory, expiry, and backend or VM binding if shown;
- tool name and whether it was absent or merely denied on invocation;
- exact visible reason and the safe-to-share request summary;
- workflow run and lease IDs, and approval request ID if present;
- time of the attempt and remaining-use information if displayed.

Do not edit `sessions.json`, broaden the root policy, or change a human's group merely to make one
agent call succeed. Those actions affect different boundaries. Ask an administrator to revoke the
smallest affected session or lease when authority is no longer appropriate.

The remainder of this chapter is administrator and security-reference material.

## Administrator reference: the identity stack

“Who did this?” can refer to several valid identities:

| Identity | Examples | What it governs |
|---|---|---|
| Human operator | An operator's Admin Web login | Reading or mutating operator surfaces through RBAC |
| Directory scope | `root/northstar-platform/mission-control` | Which inherited configuration and RBAC assignments apply |
| Agent/session | An implementation session's `session_id` and optional VM/provider binding | MCP visibility, allowlists, quotas, expiry, execution correlation |
| Workflow | Run ID, graph ID/revision, trace ID, node execution ID | Why the action was scheduled and which configuration produced it |
| MCP transport | Protocol `mcp-session-id` bound to an Evander session | Which session the connection is allowed to represent |
| Provider identity | ADO project, Git account, Teams app, relay credential | Which external service principal the host uses |
| Team identity | Team manager, project lead, task owner, reviewer, and release agent IDs | Add-on task, wake, review, governance, and release responsibility |

The audit trail combines several of these so it can distinguish “an operator approved a request
for the implementation session during workflow run X” from the less useful “a tool ran.”

### Admin Web authentication

Admin Web supports three current modes:

| Mode | Purpose |
|---|---|
| `none` | An explicitly unauthenticated/local mode. Appropriate only inside a trusted, otherwise protected operator boundary. |
| `local_password` | Local users and PBKDF2 password credentials, with an environment-supplied bootstrap/break-glass account. |
| `azure_sso` | Azure SSO identity mapped to the local user/RBAC store. |

If no mode is supplied, a configured admin password selects `local_password`; otherwise the
process falls back to `none`. Production administrators should set the mode deliberately rather
than relying on that convenience behavior.

The durable user, group, assignment, and direct-grant store is SQLite at `runtime/admin-web.db`.
Retired JSON user files are not read by the runtime. Local passwords use PBKDF2-HMAC-SHA-256 with 200,000
iterations and a 32-byte random salt. The database stores a verifier, not the plaintext password.

Successful authentication creates an HMAC-signed `evander_admin_session` cookie. The default
session TTL is 43,200 seconds (12 hours), with a minimum of 60 seconds. Set a stable
`EVANDER_ADMIN_WEB_SESSION_SECRET`; if absent, the process generates one, so a restart invalidates
existing cookies. Secure-cookie and reverse-proxy/TLS behavior belong to the complete exposure
design.

The first-owner bootstrap is guarded. The configured local bootstrap account or Azure bootstrap
allowlist can establish the first usable owner, but later authorization comes from durable users
and assignments. User-management logic protects the effective owner boundary so an operator
cannot casually remove or disable the last owner.

### Directory-scoped role-based access control

Role-based access control (RBAC) uses this permission catalog:

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

Built-in groups are `owner`, `operator`, `workflow-maintainer`, `workflow-viewer`, `auditor`, and
`viewer`. Owner and operator are required group definitions. `admin.owner` expands to the full
catalog. Operator receives workflow read/write, pack management, run read/control, and Team chat
read-all; it does not implicitly receive user/group management unless configured through another
group or ownership.

A group assignment at a directory applies to that directory and descendants. Thus an assignment
at `root/northstar-platform` applies to `mission-control` but not to an unrelated root child.
Direct workflow grants are limited to workflow read or write, and write expands to read. Effective
permission is the union of matching group assignments and matching direct workflow grants. Scope
inheritance is downward only: an assignment on `mission-control` does not authorize its parent or
sibling.

Some resources are worker-global rather than directory-owned. In particular, the one ChatGPT
subscription account shared by a container worker can be inspected, signed in, cancelled during
login, or signed out only by an authenticated operator with privileged operations unlocked and
effective `admin.owner` permission at `root`. Ownership of only a child directory is insufficient,
because changing that account affects subscription-mode agents from every directory on the worker.

### Privileged-operation lock implementation

Authentication and RBAC answer whether an operator *may* perform an action. Sensitive mutation
routes also require the current authenticated Admin Web session to be marked as
privileged-unlocked.

Calling the unlock endpoint marks the current cookie session in process memory; it is not a second
password or SSO challenge. Routes call `request_has_privileged_operations`, optionally with a
required permission and directory scope, so both authorization and transient state must pass.

### Session manifest fields

A `SessionManifest` includes:

- `session_id`, creation and expiry timestamps;
- VM, execution-provider, and model bindings where selected;
- canonical `directory_path`;
- allowed MCP tools, approval-required tools, and per-tool grant settings;
- allowed contact and script IDs;
- nullable `communication_target_ids` for Teams and Discord destinations plus outbound email
  recipients;
- nullable structured `email_mailbox_grants` for mailbox `read`, `draft`, or `send` access and
  per-mailbox folders, alongside legacy Discord, Teams, mailbox, folder, recipient, and domain
  allowlists retained for older sessions;
- an optional `codex_auth_mode` override for container-worker Codex sessions;
- tool argument policies;
- communication file limits and enablement;
- web-search enablement and quotas;
- metadata for workflow, issue, Team, Git, HTTP-target, and execution correlation.

### Canonical communication grants and legacy conversion

The canonical fields divide two kinds of authority that legacy allowlists combined:

| Session field | Grants | Does not grant |
|---|---|---|
| `communication_target_ids` | Named Teams and Discord destinations, and named outbound email recipients | Access to read or draft within an email mailbox |
| `email_mailbox_grants` | One mailbox account, an access level (`read`, `draft`, or `send`), and explicit folders | Permission to deliver mail to arbitrary recipients |

Both fields are nullable for compatibility. Missing or `null` keeps the corresponding legacy
resolution path; `[]` is canonical and denies all access in that category even if old allowlist
fields still contain values. A canonical target that has since been disabled or removed fails
closed instead of falling back to its raw provider address.

The Sessions-page conversion is explicit and reviewable. It proposes destination IDs from legacy
Discord channels and Teams channel, chat, and one-on-one user allowlists, plus mailbox grants from
the old mailbox/folder fields. Mailbox access is inferred conservatively from the session's current
email tools and send flag: read-only remains `read`, draft-capable becomes `draft`, and an actually
send-capable session becomes `send`; draft and send proposals include the required `drafts` folder.
Legacy recipient strings are not silently turned into managed email targets. Select the intended
configured email recipient targets before saving if the converted session must send mail.

### Policy evaluation details

The policy engine's effective tool-request sequence is:

1. require an existing, unexpired session;
2. resolve effective policy for its directory;
3. apply directory denial and any special capability profile;
4. require the tool in `allowed_tools` or in a valid workflow-scoped approval lease for this
   session and run;
5. require structural prerequisites such as a bound execution for resume behavior or a feedback
   stream for feedback tools;
6. apply inherited and session argument/resource rules to the payload;
7. require and validate per-invocation approval where configured.

Tool visibility and execution use the same underlying policy decisions, but visibility cannot
pre-validate a future payload.

Directory policy supplies safe defaults: approval TTL 3,600 seconds, session TTL 28,800 seconds,
email sending disabled, and communication file send/receive disabled. Live directory overlays may
alter supported values. These defaults are not a substitute for explicitly reviewing a production
root policy.

Argument-policy operators include required fields, fixed values, patterns, enums, allowlists, and
denylists. The resolved directory rule list and the session rule list both apply; a permissive
session rule does not erase a directory constraint. Within directory inheritance, ordinary list
replacement follows the merge rules in Chapter 5.

### Workflow-scoped tool leases

A `SessionToolApprovalLease` temporarily grants named MCP tools to exactly one session and workflow
run. It contains a lease ID, effect execution correlation, per-tool approval settings, optional
notification targets, expiry, revocation time, and used counts.

The workflow context must present matching run and lease IDs. Use is consumed under the
repository's atomic update path. A lease cannot:

- revive an expired session;
- cross to another session or workflow run;
- override an inherited directory deny;
- bypass argument/resource policy;
- remove mandatory or configured per-invocation approval.

A project's graph may reserve one tool for the implementation agent during a bounded step without
putting that tool in every long-lived session. The lease says the workflow may expose the tool; a
separate exact approval may still be required for a sensitive call.

## Failure reference

| Symptom | Decision that failed | Typical cause |
|---|---|---|
| Admin Web redirects or returns unauthenticated | Human authentication | Missing/expired/bad cookie, restart with generated secret, SSO or password failure |
| Authenticated user sees read-only UI | RBAC | No matching directory assignment or direct grant |
| Mutation says privileged operations are locked | Transient guard | Current cookie session has not been unlocked or process restarted |
| User can edit Northstar but not a sibling | Directory scope | Expected descendant-only assignment behavior |
| Agent's `tools/list` is empty | Session/MCP identity | Unknown, expired, unproved, or incorrectly bound session |
| Tool is missing | Visibility policy | Not granted, directory-denied, profile-denied, or structurally unavailable |
| Visible tool rejects call | Payload policy | Argument, target, quota, lease, or approval failed |
| Legacy communication conversion cannot be saved | Canonical resource validation | A proposed raw/old destination no longer resolves to an enabled managed target, or a mailbox/folder is unavailable under effective policy |
| Lease works in one run but not another | Workflow scope | Run/session mismatch is intentional |
| Last owner mutation is rejected | Recovery invariant | Store prevents loss of the effective owner boundary |

## Administrator runbook

For a human-access issue, collect the auth mode, login, current directory, requested permission,
matching group assignments, direct grants, and privileged-lock state. Do not inspect or copy
password-verifier fields unless incident response requires it.

For an agent-access issue:

```bash
evander-cli list-sessions --runtime-dir runtime --all
evander-cli directories config-show root/northstar-platform/mission-control \
  --domain policy --runtime-dir runtime
evander-cli list-approvals --runtime-dir runtime
```

Then use Admin Web's session tool-visibility view or the gateway audit entry to identify the exact
reason. Compare the normalized tool name, session expiry, directory, session grant, inherited deny,
structural prerequisite, argument payload, workflow run/lease, approval request, and remaining
uses before changing policy.

Use lifecycle commands rather than editing `sessions.json`:

```bash
evander-cli renew-session <session-id> --runtime-dir runtime
evander-cli revoke-session <session-id> --runtime-dir runtime
```

Check each command's `--help` for the required renewal/expiry arguments in the installed revision.
Revoke the smallest affected session or lease when authority is no longer appropriate; changing a
human's group does not automatically revoke an already issued agent session.

## Source trail

- `src/evander/admin/web/shared.py`, `src/evander/admin/web/user_store.py`, and
  `src/evander/admin/web/rbac.py` — authentication, cookies, password storage, groups, permission
  scope, and privileged lock.
- `src/evander/admin/web/users.py` and `tests/test_admin_web_user_store.py` — user/group
  administration, bootstrap, migration, and owner protection.
- `src/evander/admin/web/codex_auth.py` — privileged root-owner authorization for the worker-global
  ChatGPT subscription account.
- `src/evander/admin/services/session_helpers.py` and
  `frontend/admin-web/src/features/sessions/SessionsScreen.tsx` — canonical session-policy
  updates and explicit legacy communication conversion.
- `src/evander/models.py` — `SessionManifest`, `SessionToolApprovalLease`, and approval models.
- `src/evander/policy.py` and `src/evander/policy_config.py` — visibility, grants, denial,
  structural checks, arguments, approvals, and defaults.
- `src/evander/repositories/session_manifests.py` and
  `src/evander/repositories/session_tool_approval_leases.py` — durable session and lease mutation
  semantics.
- `tests/test_admin_web_rbac.py`, `tests/test_policy.py`, `tests/test_gateway.py`, and
  `tests/test_session_tool_approval_leases.py` — authorization-plane invariants.
