# 7. MCP, Approvals, and Audit

> **At a glance.** An agent does not call arbitrary host functions. It asks Evander to use a named
> tool for the current job. Evander identifies the session, checks the job's rules and the specific
> request, pauses for a person when approval is required, and records the outcome.
>
> For an operator, the most important distinction is between **seeing a tool**, **being allowed to
> request an action**, and **having that exact sensitive action approved**. These are separate
> checks. The technical reference later in this chapter explains the MCP connection, proof tokens,
> payload binding, leases, and audit stores behind them.

## One tool request in plain language

MCP is the protocol the agent harness uses to discover and call Evander tools. A typical request
passes through six understandable stages:

1. Evander identifies the temporary session making the request.
2. It confirms that the session is active and that the tool belongs to this job.
3. It checks the requested recipient, target, file, or other arguments against policy.
4. If the action is sensitive, Evander creates a pending request instead of performing it.
5. After a decision, the agent retries the same request; Evander runs it only if every check still
   passes.
6. Evander records the decision and result for later inspection.

This is why a working connection does not imply that every tool or action is available. It is also
why an approval does not become permanent extra power.

![MCP tool invocation policy, approval, execution, and audit sequence](../../assets/evander-user-manual/diagrams/05-mcp-policy-sequence.svg)

Put simply, the figure shows one request moving through identity, policy, approval, execution, and
recordkeeping. A stop at any earlier stage prevents the host operation.

<p class="figure-caption">Figure 7.1 — Catalog visibility, invocation authorization, approval, execution, and audit are separate checks in one request envelope.</p>

## What an operator sees when approval is required

The agent's first call does not run the sensitive action. Evander creates a durable pending request
and tells the agent to wait for a decision. In Admin Web, the operator can see which session and
directory asked, the tool and intended effect, when the request expires, and a redacted summary of
the input.

Before deciding, verify four things:

1. **The job:** Is this the expected session, directory, and workflow context?
2. **The action:** Does the tool and intended effect match the work being performed?
3. **The boundary:** Is the displayed target or redacted input as narrow as expected?
4. **The timing:** Is the request still current, and is there a clear reason to perform it now?

Approve or deny with a useful reason. Approval does not execute the action; the agent must retry
the bound request. If the retry changes the payload, session, or tool under the ordinary exact
binding, Evander rejects it rather than treating the old approval as permission for something new.

![Admin Web approval detail bound to a synthetic operation](../../assets/evander-user-manual/screenshots/admin-approvals.png)

The operator receives a review surface, not the agent's authority or the host's provider
credentials. A delayed chat notification should lead back to this canonical request before a
decision is made.

<p class="figure-caption">Figure 7.2 — The operator sees the request identity, bound action, status, and decision surface without receiving ambient guest authority. Sanitized Northstar fixture.</p>

## Approval is bounded, not blanket elevation

The ordinary approval is tied to the exact request, has one use, and expires according to policy
(3,600 seconds by default). Directory policy can impose tighter limits. Once the use or time limit
is reached, the request no longer authorizes a retry.

A workflow can also make a tool temporarily available for one session and run. That temporary
grant answers “may this workflow step expose the tool?” An invocation approval answers “may this
exact sensitive call execute now?” Where both apply, both must pass.

Neither control can revive an expired session, override a broader directory denial, cross to a
different job, or bypass the request's argument rules.

## What the audit trail is for

Evander records allowed, blocked, denied, failed, and completed tool activity with the identities
that explain it. Operators can relate a tool call to a session, workflow, approval, and person
without relying on a vague message that “the tool ran.” Sensitive values are redacted recursively
before they are presented in ordinary audit views.

The audit view and the workflow run answer complementary questions:

- the audit trail explains the tool request, policy decision, approval, and result;
- workflow evidence explains why the request was scheduled and how the graph continued.

Redaction reduces accidental disclosure. It is not permission to place secrets in ordinary tool
arguments, messages, or workflow payloads.

## What an operator may notice

| What you see | What it means | Useful evidence to capture |
|---|---|---|
| A plain browser request to `/mcp` returns `406` | The endpoint may be listening, but the request did not speak MCP | HTTP status and headers; do not diagnose a dead gateway from this alone |
| The client receives HTTP `401` | Session proof is missing or invalid | Session ID, expiry, client header/query behavior, and time |
| The client receives `409` or a session-conflict error | The connection is already tied to a different session | Declared session and protocol connection ID |
| A tool is absent | Visibility policy or a structural prerequisite excluded it | Session, directory, expected tool, and visibility explanation |
| The first call returns an approval request | Evander paused as designed | Request ID, status, expiry, job, and intended effect |
| An approved retry reports payload mismatch | The retried arguments do not match the approved request | Original review summary and current redacted payload |
| An approved retry is exhausted or expired | Its use or time boundary has ended | Request settings, status, and decision/use history |
| An approval notification failed | Delivery failed; the durable request was not approved or denied | Request ID, delivery error, and whether it remains pending |
| Admin Web misses recent calls | The query index may be stale although the canonical log is intact | Runtime root, approximate time, and whether other audit entries appear |
| The canonical audit log lacks the event | The wrong runtime/process may be under inspection, or append did not occur | Running service configuration and gateway error/time |

## Troubleshooting an approval or tool call

Start with the request, session, and audit views in Admin Web. Record the request ID, session ID,
directory, workflow run or trace ID, tool, intended effect, status, expiry, remaining uses, decision
reason, and exact visible error. If the tool is absent rather than denied, say so explicitly.

Do not approve merely to clear a pending queue, and do not ask the agent to vary the request until
one retry happens to pass. If the request is unclear or broader than expected, deny it with a reason
that helps the workflow or operator correct the request. If recent audit entries are missing, an
administrator should check the canonical log before assuming the action was unrecorded.

The remainder of this chapter is administrator and protocol reference material.

## Administrator reference: MCP as a policy gateway

`evander-gateway` hosts a FastMCP server named “Evander Gateway” at the streamable `/mcp` path. It
is not a transparent proxy to arbitrary host functions. Registered tools adapt explicit host
services, and both discovery and invocation are session-aware.

The broad request path is:

```text
MCP HTTP request
  -> resolve declared/bound Evander session
  -> verify session proof token
  -> check active session and effective directory policy
  -> filter catalog or validate invocation arguments
  -> validate workflow lease and per-invocation approval
  -> dispatch one registered tool
  -> append audit event and best-effort index it
```

### Connection binding and proof

An MCP client initially declares its Evander session with the `session_id` query parameter or
`x-evander-session-id` header. It supplies the session proof token with one of the supported MCP
session-token query parameters or headers, normally `x-evander-mcp-session-token`.

The MCP protocol assigns an `mcp-session-id`. When that transport ID and a declared Evander session
first appear together, Evander persists their relationship in
`runtime/mcp-transport-bindings.json`. Future requests on the transport inherit the binding. A
request declaring a conflicting Evander session is rejected rather than silently rebound.

Session setup issues a random proof token and records MCP-auth metadata on the session, including a
SHA-256 verifier. Comparison is constant-time. Query-string tokens are redacted when the gateway
logs request URLs. Headers are preferred because URLs are more likely to appear in infrastructure
logs.

The `session_id` field remains optional on tool schemas for legacy clients. The wrapper supplies
the bound connection session and rejects a conflicting explicit field. An unbound caller cannot
choose another session merely by changing a tool argument.

### Catalog filtering

MCP `tools/list` is evaluated for the authenticated session. It includes only tools that are
registered, compatible with the session's capability profile, not directory-denied, granted by the
session or an eligible workflow lease, and structurally usable.

Structural examples include resume operations that need a bound execution and feedback operations
that need a configured feedback stream. A bad or missing proof can produce HTTP authentication
rejection before catalog evaluation; a valid session with no visible tools can legitimately
receive an empty catalog. Catalog visibility is not pre-approval, and argument/resource policy can
still reject a particular payload.

### Live project documentation

Three read-only built-in tools expose the current host documentation without copying the repository
into an execution target:

- `list_documentation` lists the permitted document paths, titles, and classifications;
- `search_documentation` performs bounded case-insensitive literal search, optionally within one
  document;
- `read_documentation` returns a bounded line range with pagination metadata.

They are normal session-grantable tools and use the same catalog filtering, invocation policy, and
audit envelope as other built-ins. Admin assistant capability profiles receive them by default;
other agents need an explicit session grant or an eligible workflow lease. The host service reads
files at invocation time from the authoritative/focused map and canonical technical-manual chapter
sources. It rejects unlisted paths, traversal and symlink escapes, historical records, generated
publication duplicates, the manual reading-path and design-history chapters, managed upstream
content, and arbitrary repository files.

### Invocation checks

The registered-tool wrapper applies these controls around the tool implementation:

1. bind the authoritative session from the request context;
2. require valid session proof and an active manifest;
3. enforce add-on registration/capability policy where the tool came from an add-on;
4. enforce effective directory and session argument/resource policy;
5. determine per-invocation approval settings;
6. validate and, when appropriate, consume approval or workflow-lease use;
7. dispatch the host operation;
8. audit allowed, blocked, denied, error, and result context with redaction.

The exact helper ordering can differ for specialized tool families, but none may treat a free-form
`session_id` as sufficient authority. Add-on tools enter the same gateway and remain subject to the
same connection, session, and audit envelope.

### The approval handshake and durable record

On the first invocation of an approval-required tool, the agent omits `approval_request_id`.
Evander does not run the effect. It creates or reuses a request whose ID is deterministic for the
session and invocation payload, then returns an error/instruction identifying the pending request
and asking the agent to retry after a decision.

The stored `ApprovalRequest` contains:

- request, session, directory, VM, tool/action, and intended-effect identity;
- a digest of the unredacted invocation payload;
- a recursively redacted payload for review plus a human summary;
- status, creation and expiry timestamps;
- effective grant settings and current use count;
- notification targets, deliveries, errors, and attempt state;
- approver, decision time, reason, and decision history.

The digest and review payload serve different purposes. Redaction limits disclosure to the
operator surface; hashing the original payload preserves exact binding even when a sensitive field
is hidden.

An operator supplies a non-empty reason when approving or denying. On retry, the policy engine
atomically validates status and optionally consumes use while checking expiry, payload mode,
session, tool, and maximum uses. The `approval_submit` and `approval_check` protocol tools do not
require their own approval, which would otherwise create an infinite loop.

### Grant settings and mandatory approval

The ordinary default is:

- exact payload binding;
- one use;
- an expiry derived from effective policy, 3,600 seconds by default.

Session per-tool settings can request a TTL, use count, and payload mode. Directory policy remains
the upper security boundary: it can cap TTL and use count and require exact payload. A broader
`ANY` payload mode is therefore an explicit policy choice, not an accidental consequence of a
redacted value.

Two built-in destructive operations are always stricter: `delete_ado_backlog_item` and
`semantic_delete_points` require exact-payload, one-use approval. This mandatory set is
intentionally small and explicit. Other tools become approval-required through effective directory
policy, session settings, communication-file rules, or specialized policy behavior.

A reusable request ID does not mean unlimited use. Once the configured use count is reached,
status becomes exhausted and the next attempt is rejected. A pending or approved request that
passes its deadline becomes expired. Denial, expiry, exhaustion, payload mismatch, wrong session,
wrong tool, and unknown request are distinct rejection states for audit and diagnosis.

### Workflow-scoped leases versus invocation approvals

A workflow may issue a `SessionToolApprovalLease` to expose a tool temporarily for one session and
workflow run. The MCP context presents the lease and workflow-run IDs, and consumption tracks
per-tool use.

| Control | Question |
|---|---|
| Workflow tool lease | May this workflow step temporarily add this tool to this session's catalog? |
| Invocation approval | May this exact sensitive call execute now? |

A lease cannot cross a session/run boundary, revive an expired session, override a directory deny,
or bypass invocation approval.

### Approval notifications

Effective policy or the session can select configured communication target IDs for pending
approvals. Request creation records queued/disabled status and delivery attempts. Notification
failure does not silently approve or deny the request; the durable approval remains pending until
decision or expiry.

Notifications carry a review prompt, not provider credentials. Operators should use the canonical
request in Admin Web or CLI for the final decision, especially if a chat notification is truncated
or delayed. A project can route an implementation session's request to an authorized operator
without revealing a raw address or phone number to the agent. The session sees the target ID and
approval outcome, while the host resolves contact and provider details.

### Canonical audit stream and query index

`AuditLogger` appends one JSON object per line to the configured audit path, normally
`runtime/audit.jsonl`. For tool calls, records can include:

- timestamp, directory, session, VM, agent role, and actor user ID;
- tool name, status, decision, and error summary;
- request, correlation, workflow run, trace, approval, and lease IDs;
- duration and redacted arguments/results.

The effective policy's redacted keys are applied recursively, with additional sensitive markers
guarded by the index. Proof-token query values are also redacted in transport logs.

`runtime/audit.db` is a SQLite search index built from the JSONL stream. It stores normalized
columns plus payload/search text and uses WAL. Appends try to index immediately; Admin runtime
startup can import historical tail data. If indexing fails, the JSONL append remains canonical
and the service logs a warning containing the failure class without the event payload.
Ordinary audit queries fall back to the JSONL source when the index is unavailable. An
unreadable source log raises `AuditLogUnavailableError`; the Tool Calls API responds with
HTTP 503 instead of presenting the failure as an empty history. Rebuilding the index must
not rewrite the source log.

Approval decisions create their own audit context, linked by request and session. Workflow
execution journals remain complementary to this audit evidence.

## Administrator failure reference

| Symptom | Meaning | First evidence |
|---|---|---|
| `/mcp` plain GET returns `406` | Endpoint is listening but request did not negotiate MCP | HTTP status/headers; use an MCP client for protocol health |
| HTTP `401` | Missing or invalid session proof | Session metadata, client header/query, expiry |
| HTTP/session conflict `409` or tool conflict error | Transport is already bound differently | `mcp-transport-bindings.json` and declared header/query |
| Tool absent from list | Visibility denial or missing structural prerequisite | Tool visibility explanation and effective policy |
| First call returns approval request | Expected controlled pause | `show-approval` and pending audit record |
| Approved retry says payload mismatch | Retried arguments differ from the approved digest | Original intended effect and current redacted payload |
| Approved retry says exhausted/expired | Use or time bound has ended | Request status/settings/history |
| Notification failed | Review delivery failed, not the durable request | Notification deliveries/error and pending queue health |
| Admin audit view misses recent calls | SQLite index stale or unavailable | Canonical `audit.jsonl`; rebuild index |
| Tool Calls returns HTTP 503 | Source audit log could not be read | Admin Web error log, configured audit path, permissions, storage health |
| JSONL lacks expected event | Wrong runtime, process, or code path; append itself did not occur | Running unit config and gateway log |

## Administrator runbook

For a pending request:

```bash
evander-cli list-approvals --runtime-dir runtime \
  --status pending \
  --directory-path root/northstar-platform/mission-control \
  --show-payload
evander-cli show-approval <request-id> --runtime-dir runtime
```

After confirming the session, tool, intended effect, expiry, exact redacted payload, and surrounding
workflow context:

```bash
evander-cli approve <request-id> <operator-id> \
  --approve \
  --reason "Telemetry retry test needs this bounded operation" \
  --runtime-dir runtime
```

To deny, use `--deny` and an equally useful reason. Never approve merely to clear queue health.

If the query index is stale but JSONL is intact:

```bash
evander-cli rebuild-audit-index --runtime-dir runtime --reset --json
```

For an MCP identity problem, compare the declared Evander session, protocol `mcp-session-id`,
proof-token header, manifest expiry, and persistent binding. Reissuing a session is safer than
hand-editing a binding when a guest was cloned or its session material was copied unexpectedly.

## Source trail

- `src/evander/gateway/server.py`, `src/evander/gateway/app.py`, and
  `src/evander/gateway/session_context.py` — MCP registration, middleware, catalog filtering,
  binding, and invocation context.
- `src/evander/gateway/documentation_tools.py` and
  `src/evander/admin/services/documentation_service.py` — policy-gated live documentation tools
  and the fail-closed current-document catalog.
- `src/evander/session_auth.py` and `src/evander/repositories/mcp_transport_bindings.py` —
  proof-token issuance/verification and persistent transport binding.
- `src/evander/policy.py`, `src/evander/policy_config.py`, and `src/evander/models.py` — tool
  visibility, approval creation/validation/consumption, settings, and durable records.
- `src/evander/approval_notifications.py` and
  `src/evander/admin/services/approval_audit_service.py` — notifications, operator decisions,
  queue health, and audit context.
- `src/evander/audit.py` and `src/evander/repositories/audit_index.py` — canonical JSONL append and
  queryable SQLite index.
- `tests/test_gateway.py`, `tests/test_policy.py`, `tests/test_approval_notifications.py`, and
  `tests/test_session_tool_approval_leases.py` — gateway, exact binding, notification, and lease
  behavior.
