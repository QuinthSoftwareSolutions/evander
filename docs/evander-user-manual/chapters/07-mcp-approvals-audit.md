# 7. MCP, Approvals, and Audit

> **At a glance.** The MCP gateway turns one host endpoint into a session-specific tool catalog. It binds each MCP transport to an Evander session, verifies session proof, filters tools, validates every invocation, and records the decision. Approval-required calls use a two-step protocol: create a durable request without executing, obtain a reasoned human decision, then retry the bound payload before expiry and use exhaustion. JSONL audit is canonical; SQLite is its query index.

## MCP is a policy gateway

`evander-gateway` hosts a FastMCP server named “Evander Gateway” at the streamable `/mcp` path. It is not a transparent proxy to arbitrary host functions. Registered tools are adapters over explicit host services, and both discovery and invocation are session-aware.

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

This order explains why “the gateway is reachable” and “the tool is usable” are different observations.

![MCP tool invocation policy, approval, execution, and audit sequence](../../assets/evander-user-manual/diagrams/05-mcp-policy-sequence.svg)

<p class="figure-caption">Figure 7.1 — Catalog visibility, invocation authorization, approval, execution, and audit are separate checks in one request envelope.</p>

## Connection binding and proof

An MCP client initially declares its Evander session with the `session_id` query parameter or `x-evander-session-id` header. It supplies the session proof token with one of the supported MCP session token query parameters or headers, normally `x-evander-mcp-session-token`.

The MCP protocol itself assigns an `mcp-session-id`. When that transport ID and a declared Evander session first appear together, Evander persists their relationship in `runtime/mcp-transport-bindings.json`. Future requests on the transport inherit the binding. A request that declares a conflicting Evander session is rejected rather than silently rebound.

Session setup issues a random proof token and records MCP-auth metadata on the session, including a SHA-256 verifier. Comparison is constant-time. Query-string tokens are redacted when the gateway logs request URLs. Headers are preferred because URLs are more likely to appear in infrastructure logs.

The `session_id` field remains optional on tool schemas for legacy clients. The wrapper supplies the bound connection session and rejects a conflicting explicit field. An unbound caller cannot choose another session merely by changing a tool argument.

## Catalog filtering

MCP `tools/list` is evaluated for the authenticated session. The result contains only tools that are registered, compatible with the session's capability profile, not directory-denied, granted by the session or an eligible workflow lease, and structurally usable.

Structural examples include resume operations that need a bound execution and feedback operations that need a configured feedback stream. This avoids advertising a tool that cannot make sense for the session even before arguments are known.

A bad or missing proof can produce an HTTP authentication rejection before catalog evaluation. An otherwise valid session with no visible tools can legitimately receive an empty catalog. Inspect the status and audit/policy reason rather than treating both outcomes as “MCP returned nothing.”

Catalog visibility is not pre-approval. A visible tool can still require an approval request when invoked, and argument/resource policy can reject a particular payload.

## Invocation checks

The gateway's registered-tool wrapper performs the current invocation controls around the tool implementation:

1. bind the authoritative session from the request context;
2. require valid session proof and an active manifest;
3. enforce add-on registration/capability policy where the tool came from an add-on;
4. enforce effective directory and session argument/resource policy;
5. determine per-invocation approval settings;
6. validate and, when appropriate, consume approval or workflow-lease use;
7. dispatch the host operation;
8. audit allowed, blocked, denied, error, and result context with redaction.

The exact helper ordering can differ for specialized tool families, but none may treat a free-form `session_id` as sufficient authority. Add-on tools register into the same gateway and remain subject to the same connection, session, and audit envelope.

## The approval handshake

When Imani invokes an approval-required tool during the telemetry-retry work, the first call omits `approval_request_id`. Evander does not run the effect. It creates or reuses a request whose ID is deterministic for the session and invocation payload, then returns an error/instruction identifying the pending request and asking the agent to retry after a decision.

The stored `ApprovalRequest` contains:

- request, session, directory, VM, tool/action, and intended-effect identity;
- a digest of the unredacted invocation payload;
- a recursively redacted payload for review plus a human summary;
- status, creation and expiry timestamps;
- effective grant settings and current use count;
- notification targets, deliveries, errors, and attempt state;
- approver, decision time, reason, and decision history.

The digest and review payload serve different purposes. Redaction limits disclosure to the operator surface; hashing the original payload preserves exact binding even when a sensitive field is hidden.

An operator approves or denies a pending request and supplies a non-empty reason. An approval does not itself execute the action. Imani retries the tool with the request ID. The policy engine atomically validates status and optionally consumes use while checking expiry, payload mode, session, tool, and maximum uses.

The `approval_submit` and `approval_check` protocol tools do not require their own approval, which would otherwise create an infinite loop.

![Admin Web approval detail bound to the telemetry-retry operation](../../assets/evander-user-manual/screenshots/admin-approvals.png)

<p class="figure-caption">Figure 7.2 — The operator sees the request identity, bound action, status, and decision surface without receiving ambient guest authority. Sanitized Northstar fixture.</p>

## Grant settings and limits

The ordinary default is:

- exact payload binding;
- one use;
- an expiry derived from effective policy, 3,600 seconds by default.

Session per-tool settings can request a TTL, use count, and payload mode. Directory policy remains the upper security boundary: it can cap TTL and use count and require exact payload. A broader `ANY` payload mode is therefore an explicit policy choice, not an accidental consequence of a redacted value.

Two built-in destructive operations are always stricter: `delete_ado_backlog_item` and `semantic_delete_points` require exact-payload, one-use approval. This mandatory set is intentionally small and explicit. Other tools become approval-required through effective directory policy, session settings, communication-file rules, or specialized policy behavior.

A reusable request ID does not mean unlimited use. Once the configured use count is reached, status becomes exhausted and the next attempt is rejected. A pending or approved request that passes its deadline becomes expired. Denial, expiry, exhaustion, payload mismatch, wrong session, wrong tool, and unknown request are distinct rejection states for audit and diagnosis.

## Workflow-scoped leases versus invocation approvals

A workflow may issue a `SessionToolApprovalLease` to expose a tool temporarily for one session and workflow run. The MCP context presents the lease and workflow-run IDs, and consumption tracks per-tool use.

The two controls answer different questions:

| Control | Question |
|---|---|
| Workflow tool lease | May this workflow step temporarily add this tool to this session's catalog? |
| Invocation approval | May this exact sensitive call execute now? |

A lease cannot cross a session/run boundary, revive an expired session, override a directory deny, or bypass invocation approval. Where both apply, both must pass.

## Approval notifications

Effective policy or the session can select configured communication target IDs for pending approvals. Request creation records queued/disabled status and delivery attempts. Notification failure does not silently approve or deny the request; the durable approval remains pending until decision or expiry.

Notifications carry a review prompt, not provider credentials. Operators should use the canonical request in Admin Web or CLI for the final decision, especially if a chat notification is truncated or delayed.

Northstar can route Imani's approval request to an authorized operator without revealing a raw address or phone number to the agent. The session sees the target ID and approval outcome, while the host resolves the contact/provider details.

## Audit: canonical stream and query index

`AuditLogger` appends one JSON object per line to the configured audit path, normally `runtime/audit.jsonl`. For tool calls, records can include:

- timestamp, directory, session, VM, agent role, and actor user ID;
- tool name, status, decision, and error summary;
- request, correlation, workflow run, trace, approval, and lease IDs;
- duration and redacted arguments/results.

The effective policy's redacted keys are applied recursively, with additional sensitive markers guarded by the index. Proof-token query values are also redacted in transport logs. Redaction is designed to reduce accidental disclosure; it should not be used as permission to place secrets in ordinary payloads.

`runtime/audit.db` is a SQLite search index built from the JSONL stream. It stores normalized columns plus the payload/search text and uses WAL. Appends try to index immediately; Admin runtime startup can import historical tail data. If indexing fails, the JSONL append remains canonical. Rebuilding the index must not rewrite the source log.

Approval decisions create their own audit context, linked by request and session. Workflow execution journals are complementary: audit explains tool policy/decision activity, while orchestration state explains graph routing and node execution.

## Failure behavior

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
| JSONL lacks expected event | Wrong runtime, process, or code path; append itself did not occur | Running unit config and gateway log |

## Practical runbook

For a pending Northstar request:

```bash
evander-cli list-approvals --runtime-dir runtime \
  --status pending \
  --directory-path root/northstar-platform/mission-control \
  --show-payload
evander-cli show-approval <request-id> --runtime-dir runtime
```

After confirming session, tool, intended effect, expiry, exact redacted payload, and surrounding workflow context:

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

For an MCP identity problem, compare the declared Evander session, protocol `mcp-session-id`, proof-token header, manifest expiry, and persistent binding. Reissuing a session is safer than hand-editing a binding when a guest was cloned or its session material was copied unexpectedly.

## Source trail

- `src/evander/gateway/server.py`, `src/evander/gateway/app.py`, and `src/evander/gateway/session_context.py` — MCP registration, middleware, catalog filtering, binding, and invocation context.
- `src/evander/session_auth.py` and `src/evander/repositories/mcp_transport_bindings.py` — proof-token issuance/verification and persistent transport binding.
- `src/evander/policy.py`, `src/evander/policy_config.py`, and `src/evander/models.py` — tool visibility, approval creation/validation/consumption, settings, and durable records.
- `src/evander/approval_notifications.py` and `src/evander/admin/services/approval_audit_service.py` — notifications, operator decisions, queue health, and audit context.
- `src/evander/audit.py` and `src/evander/repositories/audit_index.py` — canonical JSONL append and queryable SQLite index.
- `tests/test_gateway.py`, `tests/test_policy.py`, `tests/test_approval_notifications.py`, and `tests/test_session_tool_approval_leases.py` — gateway, exact binding, notification, and lease behavior.
