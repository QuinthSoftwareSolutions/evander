# 5. Runtime Assembly, Configuration, and Directories

> **At a glance.** Evander separates host bootstrap settings from inheritable directory settings, workflow definitions, host secrets, and durable runtime state. `build_admin_runtime` is the supported composition root for operator and integration surfaces. Directory configuration resolves from immutable `root` through the selected ancestry: objects merge recursively, while lists, scalars, and `null` replace. Effective configuration and its provenance—not one JSON overlay—determine behavior.

## Five kinds of configuration-shaped data

Several Evander files contain JSON or environment variables, but they have different ownership and update rules:

| Kind | Typical location | Purpose | Safe mutation path |
|---|---|---|---|
| Host bootstrap | `runtime/host-config.json` | Runtime root, listener addresses, core store paths, add-on loader | Setup/CLI and supported host configuration flow |
| Directory config | `runtime/directory-config/root/...` | Inheritable policy, providers, contacts, email, wake, VM, and automation behavior | Admin Web or `evander-cli directories config-set` |
| Workflow config | `config/trigger-orchestrator/config.json` and referenced packs | Graphs, nodes, edges, provider bindings, orchestration store settings | Admin Web revision/lock API; files are loading surfaces, not a concurrency bypass |
| Secrets/service env | `config/*.env`, provider JSON env references, optional `secrets.env` | Credentials and process-specific listener/auth settings | Host operator and secret-management process |
| Runtime state | JSON/SQLite beneath the runtime root | Sessions, approvals, queue/journal, Git registry, audit, add-ons | Domain CLI/Admin/API only; do not hand-edit |

The distinction prevents two recurring mistakes. First, a listener port belongs to host bootstrap or service environment, not a child project's provider overlay. Second, `orchestration.db` may contain JSON columns, but it is a durable state database, not configuration to edit into a desired state.

## Bootstrapping a runtime

The repository-oriented first-run sequence is:

```bash
uv pip install -e '.[dev]'
evander-cli init-state --runtime-dir runtime
```

Initialization establishes `host-config.json`, the directory registry, the immutable root directory, root directory-config files, and other required stores. The packaged `HostConfig` default runtime is `/var/lib/evander`; commands and repository services commonly pass `runtime` explicitly. Relative runtime paths are resolved, and default store paths follow the selected runtime root.

Legacy gateway YAML is accepted as explicit migration or bootstrap input. It is not the current live baseline. Once host and root directory configuration exist, diagnose those current surfaces rather than repeatedly editing an old YAML file.

At startup, `load_runtime_gateway_config` combines the host bootstrap with root directory domains into a validated `GatewayConfig`. Broader assembly then adds the repositories and services that a process needs.

## The supported composition root

`src/evander/admin/composition.py::build_admin_runtime` is the supported assembly surface for the CLI, gateway integrations, Admin Web, workflow providers, and add-ons that need administration services. It:

1. resolves an explicit legacy path only when supplied or when the input is clearly YAML;
2. loads or creates host bootstrap configuration;
3. opens the directory store and ensures `root` exists;
4. opens the directory-config store and ensures root domains exist;
5. resolves the current runtime gateway configuration;
6. builds the shared `RuntimeContext`;
7. opens the audit index and starts best-effort import from canonical JSONL;
8. composes narrow domain services for directories, sessions, approvals, issues, Git, workflows, workers, reports, skills, and other operator functions.

`RuntimeContext` is the lower-level shared bundle. It contains repositories and runtime services such as sessions, approvals, tool approval leases, directories, MCP transport bindings, drafts, email and communication stores, wake state, prompt queues, scripts, policy/provider resolvers, `PolicyEngine`, and `AuditLogger`.

This assembly boundary matters to extensions. Reconstructing stores ad hoc can choose a different runtime root, omit a migration, bypass a repository adapter, or create policy and provider resolvers with different context. Add behavior through narrow services and the add-on/provider registration surfaces rather than creating a second hidden control plane.

![Runtime composition from bootstrap files through resolved configuration to shared services](../../assets/evander-user-manual/diagrams/03-runtime-composition.svg)

<p class="figure-caption">Figure 5.1 — One supported composition path keeps CLI, gateway, Admin Web, workflows, and add-ons on the same runtime truth.</p>

## Directories are configuration and authorization scope

Every installation has an immutable top-level directory named and addressed as `root`. Child directories form a tree. The directory store rejects a missing parent, duplicate sibling name, invalid canonical path, and cycles. Root cannot be moved into or replaced by a child.

Northstar can therefore be represented as:

```text
root
└── northstar-platform
    └── mission-control
```

The same canonical path can scope:

- effective policy and provider settings;
- Admin Web RBAC assignments;
- session and workflow ownership;
- managed repositories and provider operations;
- communication targets and Team Add-on records.

A directory is not a filesystem checkout. It is a logical ownership and inheritance node whose configuration happens to be stored under a matching runtime path.

## Configuration domains

The current directory-config store recognizes these canonical domains:

| Domain | File | Examples of responsibility |
|---|---|---|
| `policy` | `policy.json` | Session TTL, tool denies/approvals, arguments, communication/file rules, redaction |
| `providers` | `providers.json` | Azure DevOps, Discord, Teams, execution backend, worker, model/relay and Git provider settings |
| `contacts` | `contacts.json` | Named communication destinations without exposing raw addresses to sessions |
| `email` | `email.json` | Mailbox/provider settings |
| `wake` | `wake.json` | Wake and resume provider behavior |
| `ado_worker` | `ado-worker.json` | Directory-owned ADO worker behavior |
| `vm` | `vm.json` | Libvirt templates and VM defaults |
| `issue_setup` | `issue-setup.json` | Issue-session bootstrap choices |
| `pr_template` | `pr-template.json` | Pull-request template content/settings |
| `automation` | `automation.json` | Directory automation configuration |

Hyphenated CLI aliases such as `ado-worker` and `issue-setup` are normalized to canonical domain names. Legacy domain files and metadata overlays are migration compatibility surfaces; new work should use the canonical files.

## Resolution: root to leaf

To resolve Northstar's `policy`, Evander starts with model defaults, applies any raw `root/policy.json`, then `root/northstar-platform/policy.json`, then the leaf `root/northstar-platform/mission-control/policy.json`. Each applied layer records directory ID, path, domain, and overlay. Provenance maps effective leaf fields back to their source.

Merge semantics are deliberately simple:

- when both current and overlay values are objects, keys merge recursively;
- every other overlay value replaces the current value;
- therefore lists replace rather than concatenate;
- an explicit `null` replaces an inherited value with `null` if the domain schema permits it;
- the final effective payload is validated as a domain model.

For example, a child can replace an inherited list of approval notification targets without inheriting every root target. It cannot append one item merely by supplying a one-item list. Operators should inspect the effective result after a list change.

Policy adds one monotonic security rule: every MCP tool denied by any applied ancestor remains denied in the effective result, even if a child overlay writes `denied: false`. This exception prevents delegation from weakening an upstream prohibition while retaining ordinary replacement semantics for other values.

![Root-to-leaf directory configuration inheritance](../../assets/evander-user-manual/diagrams/04-directory-config.svg)

<p class="figure-caption">Figure 5.2 — Effective configuration is validated after ordered root-to-leaf overlay, with inherited tool denials remaining monotonic.</p>

## Raw, effective, and selected values

Three views answer different questions:

- **Raw** is exactly what one directory overrides.
- **Effective** is the validated merge through the full ancestry plus defaults.
- **Selected runtime value** may additionally be pinned or overridden by session metadata—for example a model-provider mode or the exact worker image reference selected when the session was created.

The Admin Web configuration surface and `directories config-show` expose raw and effective information. Use provenance to answer “why is this value here?” A root overlay may be absent because the value came from the code default; a leaf may have no file because it intentionally inherits.

Sessions carry a canonical directory path. Policy resolution uses that path for current effective policy. Provider resolution also begins there, with supported session-level selections layered where defined. Moving or deleting directory ownership while sessions or workflows refer to it is therefore guarded by administrative ownership checks and validation.

## Runtime directories and durable stores

The precise set grows with enabled features, but these paths are foundational:

| Path below the selected runtime | Role |
|---|---|
| `host-config.json` | Host bootstrap configuration |
| `directories.json` | Directory tree records |
| `directory-config/` | Canonical raw directory overlays |
| `sessions.json` | Session manifests through the repository adapter |
| `approvals.json` | Per-invocation approval requests |
| `session-tool-approval-leases.json` | Workflow-scoped temporary MCP grants |
| `mcp-transport-bindings.json` | Persistent MCP transport-to-session binding |
| `orchestration.db` | Default trigger queue, workflow execution and snapshot state |
| `audit.jsonl` / `audit.db` | Canonical audit stream and query index |
| `managed-git-repositories.json` | Managed-repository registry |
| `managed-git/` / `git-worktrees/` | Evander-owned bare repositories and ephemeral host worktrees |
| `admin-web.db` | Admin users, groups, assignments, and grants |
| `addons/` | Add-on-owned runtime state, including Team state when enabled |

The service and repository types define transaction and locking behavior. JSON stores use their supported atomic mutation paths; SQLite stores use schema, busy timeout, WAL, and domain transactions. A text editor provides none of those guarantees.

## Northstar configuration in practice

Suppose root selects `libvirt_vm`, disables email sending, and denies a destructive MCP tool. `root/northstar-platform` selects a provider project and approval notification target. `mission-control` selects a VM template and narrows allowed contacts.

Imani's telemetry-retry session receives the effective intersection. The project does not have to duplicate root relay settings. It cannot undo the destructive-tool deny. Its contacts list replaces the inherited list rather than extending it. If an operator inspects the provider setting, provenance can show that the organization came from root while the project name came from `northstar-platform`.

This same structure lets operators set team-wide policy while owning project-specific automation without copying one large global configuration file.

## Failure behavior

| Symptom | Likely cause | Safe response |
|---|---|---|
| Service shows empty runtime | Different `--runtime-dir` or unit environment | Inspect the running unit and `host-config.json`; do not copy state blindly. |
| Child list lost inherited entries | Lists replace, not concatenate | Write the full intended child list or remove the override. |
| Child cannot re-enable a tool | An ancestor deny is monotonic | Change the denying ancestor after a security review; do not patch runtime output. |
| Configuration save is rejected | Domain schema, directory path, revision, or lock conflict | Correct the validation error or reload the current revision. |
| Hot-running process retains older workflow | Invalid reload kept last-known-good | Inspect worker log and Admin Web validation; fix and publish a new valid revision. |
| Legacy YAML edit has no effect | Current runtime has migrated to host/root configuration | Inspect `host-config.json` and root domains. |
| Admin and gateway disagree | Independently launched processes used different roots or code revisions | Compare command lines, environments, checkout revision, and resolved config. |
| SQLite/JSON becomes corrupt after manual edit | Domain transaction/locking was bypassed | Stop writers, preserve evidence, restore from backup or supported reconstruction; do not keep editing. |

## Practical runbook

Inspect rather than infer:

```bash
evander-cli status --runtime-dir runtime --json
evander-cli directories show root/northstar-platform/mission-control \
  --runtime-dir runtime
evander-cli directories config-show root/northstar-platform/mission-control \
  --domain policy --runtime-dir runtime
evander-cli directories config-show root/northstar-platform/mission-control \
  --domain providers --runtime-dir runtime
```

Before a mutation:

1. Record the selected runtime and current directory path.
2. Read the raw overlay, effective value, provenance, and validation errors.
3. Determine whether the field belongs to host, directory, workflow, secret, or state configuration.
4. Use Admin Web's current revision/lock path or `directories config-set`; never write a live workflow pack from a stale local copy.
5. Read back the raw and effective result.
6. Check the relevant worker/service log for reload acceptance.

For first-run or recovery initialization, `evander-cli init-state --runtime-dir runtime` is idempotent setup, not a command to erase current state.

## Source trail

- `src/evander/host_config.py` and `src/evander/bootstrap.py` — host bootstrap, runtime-relative paths, legacy import, and root initialization.
- `src/evander/admin/composition.py` and `src/evander/runtime.py` — supported composition root and shared runtime services.
- `src/evander/directories.py` and `src/evander/directory_config.py` — tree invariants, domain files, ancestry merge, provenance, and validation.
- `src/evander/policy_config.py` and `src/evander/provider_config.py` — effective policy/provider resolution and monotonic tool denies.
- `src/evander/workflow/runtime_config.py` and `src/evander/admin/services/workflow_config_service.py` — workflow loading, validation, hot reload, revision, lock, and history behavior.
- `docs/configuration-reference.md` and `docs/operations.md` — field-level and operator guidance.
- `tests/test_directory_config.py`, `tests/test_directory_admin.py`, `tests/test_cli.py`, and `tests/test_admin_extracted_services.py` — inheritance, initialization, mutation, and assembly contracts.
