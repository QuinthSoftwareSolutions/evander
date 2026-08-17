# 15. Admin Operations, Observability, Backup, and Recovery

> **At a glance.** Admin Web is the main place for an operator to understand what Evander is doing and to make supported changes. Its pages are views onto the same live system used by the command line and background services, not a separate copy. When something goes wrong, this chapter shows how to begin with durable evidence, make the smallest safe intervention, and preserve enough context to recover.

## The mental model: one runtime, several views

Admin Web, the command line, and the background services are different ways of looking at or acting on the same Evander runtime. A browser page does not contain a separate copy of the truth. If the page and a saved record disagree, the discrepancy is evidence to investigate—not a reason to edit whichever file is easiest to reach.

Think of Admin Web as the supported front desk. It gives an operator a coherent view and safe controls; the durable records, service status, and external provider remain behind it. A green summary means that one check succeeded, not that every later part of a workflow is healthy.

| Surface | Best use | Authority |
|---|---|---|
| Admin Web | Interactive inspection and revision-checked operations | Calls live core/admin services |
| `evander-cli` | Automation, bootstrap, narrow diagnostics | Calls the same repositories/services where supported |
| systemd/journal | Process lifecycle and stderr/stdout history | Host service manager |
| runtime files/databases | Durable application state | Repository-specific source of truth |
| provider APIs | External issues, PRs, messages, calls | External provider |

> <span class="sidebar-kicker">Technical sidebar</span> **How the shared Admin runtime is assembled**
>
> Admin Web is not a second control plane. `build_admin_runtime` assembles focused services for sessions, issues/Git, VMs, workers, workflows, reports, approvals/audit, artifacts, configuration, add-ons, skills, capacity, and model usage around one `RuntimeContext`.
>
> A green dashboard card is a summarized observation. It does not replace the underlying session manifest, workflow run, effect attempt, service journal, or provider object.

![Evander Admin operations dashboard in dark mode for the synthetic Northstar directory](../../assets/evander-user-manual/screenshots/admin-dashboard.png)

<p class="figure-caption">Figure 15.1 — The dashboard is an orientation layer over live detail APIs, not a replacement source of truth. Sanitized Northstar fixture.</p>

## Operator surfaces

The core interface exposes:

- dashboard health and automation activity;
- VMs, issues, agents, sessions, and logs;
- approvals, tool visibility, and read-only tool-call audit;
- workflow packs, editor, runs, reports, events, and webhooks;
- Git repositories, artifacts, skills, add-ons, and Team navigation;
- configuration, workers, capacity, model pricing/usage, users, and groups;
- chat and operator steering where enabled.

Use the selected directory as part of every diagnosis. Directory inheritance changes effective provider, policy, communication-target, workflow, and repository visibility. A root owner can see more than a project-scoped workflow maintainer; comparing their screens without noting scope is misleading.

Session IDs are durable control-plane identities. Creating a session fails when that ID already
exists rather than replacing its manifest. Supported deletion revokes the session's managed-Git
grants and bindings and removes its legacy PR-routing entries plus matching feedback cursors/items;
it does not delete the provider-side pull request or comments. Record those effects before
recreating an ID during recovery.

## Authentication, authorization, and privileged unlock

Signing in, being permitted to change a project, and temporarily unlocking a sensitive host operation are three separate decisions. A user may be able to see a page without being able to change it, or be able to change ordinary project settings without being able to perform a sensitive host action.

Authentication answers *who*. RBAC answers *whether that identity can act in this directory*. Privileged unlock is an additional per-login-session guard for sensitive host mutations. It does not add a missing RBAC permission and should not be treated as a permanent administrator mode.

Approval gates are separate again. A privileged owner can configure policy but an agent's gated tool invocation still needs its matching, unexpired approval. The approved tool, session, and payload digest must match at execution time.

> <span class="sidebar-kicker">Deeper detail</span> **Admin identity and inherited permissions**
>
> Admin users and groups are stored in `runtime/admin-web.db`. Built-in groups include owner, operator, workflow maintainer, workflow viewer, auditor, and viewer. Permissions are directory-scoped and inherited downward. Direct workflow write implies workflow read; ownership expands to the full permission catalog.

## Revision-checked changes

Configuration, workflow packs, model pricing, add-on enablement, and other shared documents expose a content revision. The client sends the revision it read. If another operator saved first, Evander returns a conflict instead of overwriting newer work.

On conflict:

1. preserve your intended change outside the form;
2. reload the current document and read the other change;
3. merge deliberately;
4. save against the new revision.

Never work around a revision conflict by writing the live JSON file directly. Workflow pack edits also use lock and revision behavior; a checked-in example pack is not necessarily the live operator pack.

## Observability and evidence

Start in Admin Web with the selected directory, then open the record closest to the symptom: a run, session, approval, tool call, worker, artifact, or Team item. Record the exact status and identifiers before refreshing or retrying. Those details let an administrator move from the interface to the matching service and durable record.

### Administrator detail: health and capacity

Start broad:

```bash
evander-cli status --runtime-dir runtime --json
systemctl --failed
```

The capacity endpoint, `/api/admin/capacity-health`, reports control-plane disk, known storage roots, session samples, and unavailable/permission-denied telemetry explicitly. Built-in disk thresholds warn below 15% free and become critical below 5% unless configured otherwise. “Unknown” is not healthy.

### Administrator detail: journals and structured runtime records

Use service journals for process startup, reload, provider transport, and worker-loop failures:

```bash
journalctl -u evander-admin-web.service --since today
journalctl -u evander-trigger-orchestrator.service --since today
journalctl -u evander-effect-orchestrator.service --since today
```

Then move to the durable domain record: session manifest, trigger event, orchestration row, effect attempt, wake dispatch, managed Git operation, or add-on database. Correlate by stable IDs and exact SHA, not timestamps alone.

### Tool-call audit

`runtime/audit.jsonl` is the canonical append-only tool-call and policy evidence by default. `runtime/audit.db` is a rebuildable search index used by Admin Web; it is imported incrementally from JSONL. If the index is corrupt or incomplete, preserve the JSONL and rebuild the index:

```bash
evander-cli rebuild-audit-index --config runtime
```

Audit redaction intentionally removes configured sensitive fields. Absence of a secret from an audit row is expected, not evidence that no credential was used.

The remainder of the backup and restore material is administrator reference. An operator reporting a problem should preserve the visible records and identifiers; they should not copy, replace, or delete runtime databases.

## Administrator reference: durable state inventory

Before backup or repair, inventory the configured paths. Common current stores include:

![Configuration, JSON registries, SQLite stores, audit evidence, managed Git, and artifact blobs converge at a consistent backup boundary; derived indexes are rebuilt after restore.](../../assets/evander-user-manual/diagrams/09-runtime-stores.svg)

<p class="figure-caption">Figure 15.2 — A recoverable snapshot preserves every canonical store at one consistency boundary and rebuilds derived state only after restore.</p>

| Data | Typical location | Recovery role |
|---|---|---|
| Host/directory config | `runtime/host-config.json`, `runtime/directory-config/` | Effective runtime configuration |
| Sessions/issues/approvals and JSON repositories | paths below `runtime/` selected by config | Control-plane identity/state |
| Orchestration | `runtime/orchestration.db`, event journals | Workflow runs, jobs, effects |
| Audit | `runtime/audit.jsonl`; rebuildable `audit.db` | Security evidence/index |
| Admin identities | `runtime/admin-web.db` | Users, groups, grants |
| Managed Git | `runtime/managed-git-repositories.json`, `runtime/managed-git/` | Canonical cached objects and controlled refs |
| Published output | `runtime/published-artifacts/`, artifact-space metadata | Immutable deliverables and organization |
| Communications/wakes/usage | provider inbox DBs, wake DB, `model-usage.db` | Cursors, dispatches, observations |
| Add-on state | `runtime/addons/<id>/`, including Team `state.db` | Add-on-private durable records |
| VM and worker execution storage | libvirt images and configured worker storage | Running execution state, outside the runtime directory |

Ephemeral Git worktrees under `runtime/git-worktrees/` are reconstructible scratch space. Bare managed repositories are not the same thing and must not be discarded casually: host-only pinned and Team/review refs may not yet exist upstream.

Secrets and local service configuration—`config/*.env`, provider JSON, `secrets.env`, systemd environment, certificates—may live outside `runtime/`. Back them up separately with stricter permissions. Never place them in published artifacts or documentation captures.

## Administrator procedure: consistent backup

There is no single “copy this directory while everything runs” guarantee across JSON, SQLite WAL, bare Git, add-on databases, VM disks, and external providers. Choose a consistency boundary.

For a control-plane backup:

1. Record the Evander revision, installed package version, effective config paths, enabled add-ons, and current service list.
2. Quiesce writers: stop trigger/effect, provider ingest/wake, Admin Web, gateway mutations, worker control, and add-on services as appropriate. Keep the outage explicit and bounded.
3. Confirm no managed Git operation or Team lease is active. Do not freeze midway through an upstream push.
4. Back up `runtime/`, including SQLite `-wal`/`-shm` companions if any remain. With services stopped, a file-level snapshot is consistent; while live, use SQLite's backup API per database rather than `cp`.
5. Back up external execution storage only if recovery must resume those exact VMs/worker homes. Otherwise record that executions will be recreated.
6. Back up operator config and secrets separately with restrictive ownership.
7. Hash the backup, record its scope/time, and test restore into an isolated path.
8. Restart services in dependency order and run the health checks below.

External provider state is not captured by this backup. Record outstanding PRs, messages, and call IDs when an incident or migration requires end-to-end reconciliation.

## Administrator procedure: restore and recovery

Restore to a compatible checkout/package revision first. Keep services stopped while placing runtime and secrets. Restore file ownership and modes, then initialize only missing state—do not run `init-state` over a restored runtime without reviewing what it will seed.

Recommended order:

1. host config, directory config, secrets, and admin identity store;
2. runtime databases/JSON/journals and add-on state;
3. managed bare repositories and published artifacts;
4. optional VM disks or worker storage;
5. firewall and relay services;
6. Admin Web/gateway, then trigger/effect and provider/wake workers.

After startup, verify:

```bash
evander-cli status --runtime-dir runtime --json
./scripts/install_systemd_units.sh --preflight
```

Check SQLite integrity with the SQLite tooling appropriate to each database, inspect migration provenance where retained, and verify a read through the owning API. Rebuild only derived indexes such as `audit.db`. Do not replace a current SQLite authority with a retired JSON file; restore only the canonical database or an approved backup.

For a partially failed operation, prefer domain reconciliation over rollback by file editing. Examples: reconcile a managed Git operation against its pinned SHA and destination lease; reconcile an effect attempt against provider state; reconcile a Team lease/outbox entry through Team operations; reconcile a wake against its dispatch history.

## Failure behavior

An operator can use the first and third columns to describe what they see and which evidence an administrator should collect next.

| Symptom | Interpretation | Next evidence |
|---|---|---|
| Admin Web is unavailable | UI process/config/auth may be down; runtime can still be intact | service status/journal, then CLI and files |
| UI shows revision conflict | Concurrent write was detected | reload and merge; do not force file write |
| Tool Calls is empty but JSONL has rows | Search index is stale/corrupt | audit index status and rebuild |
| Dashboard says unknown capacity | Collection failed or lacks permission | endpoint errors and filesystem ownership |
| SQLite says locked | A live writer or abandoned operation holds access | identify owning service; do not delete WAL/lock files |
| Restored worker repeats effects | Store/provider boundary was captured inconsistently | inspect effect idempotency key and provider state before enabling workers |
| Team/Add-on data is missing after restore | Private add-on directory was omitted or wrong add-on not loaded | restored add-on state, enablement, restart, API read |

## Troubleshooting and operator handoff

In Admin Web, select the affected directory and record the expected outcome, the last completed step, and the exact visible error. Capture the session, workflow run or trace, source event, approval, effect, task, or release ID that is relevant; capture only the identifiers that exist for this incident.

If repeated work could change an external system, do not keep retrying while the result is uncertain. Tell the administrator what has already been attempted and whether an external issue, message, Git ref, or release may have changed. Do not delete audit rows, SQLite side files, workflow packs, leases, or worktrees to make the interface look clean.

## Administrator incident runbook

1. **Stabilize.** Stop only the component causing unsafe repeated mutations; preserve journals and state files before repair.
2. **Declare scope and time.** Record directory, session/workflow/effect/provider IDs, commit SHA, and first observed failure.
3. **Read durable truth.** Inspect the owning repository/database and provider. Treat UI caches and derived indexes as secondary.
4. **Build the event chain.** Trigger → workflow run/job → effect attempt → provider result → wake/session, or issue → session → managed ref → pinned SHA → publication.
5. **Classify uncertainty.** Distinguish “not attempted,” “attempt failed,” and “provider may have accepted.” The last case requires reconciliation before retry.
6. **Repair through the owner.** Use Admin/API/CLI service operations, migrations, maintenance, or an isolated restore. Avoid direct live edits.
7. **Verify both sides.** Check local state, external provider state, audit entry, and user-visible result.
8. **Capture prevention.** Add a focused regression test, update the runbook/CHANGELOG, and retain a redacted incident timeline.

## Source trail

- `src/evander/admin/composition.py`
- `src/evander/admin/web/app.py`
- `src/evander/admin/web/rbac.py`
- `src/evander/admin/web/shared.py`
- `src/evander/admin/services/operations_service.py`
- `src/evander/admin/services/approval_audit_service.py`
- `src/evander/admin/services/capacity_health_service.py`
- `src/evander/capacity_health.py`
- `src/evander/repositories/audit_index.py`
- `src/evander/admin/web/user_store.py`
- `docs/operations.md`
- `docs/admin-ui-user-manual.md`
- `scripts/install_systemd_units.sh`
- `tests/test_admin_web_rbac.py`
- `tests/test_admin_web_config_mutation_auth.py`
- `tests/test_admin_web_tool_calls.py`
- `tests/test_capacity_health.py`
- `tests/test_operations_admin_service.py`
