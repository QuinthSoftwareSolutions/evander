# 5. Runtime Assembly, Configuration, and Directories

> **At a glance.** Evander can serve several organizations, teams, and projects without making
> each one repeat the same settings. Broad rules can live near the top of a directory tree, while
> a project records only what is different. When a job starts, Evander combines those layers and
> can show where the final values came from.
>
> For an operator, the practical lesson is simple: first select the directory that owns the work,
> then inspect the setting as it applies there. The technical sections later in this chapter explain
> the files, merge rules, stores, and startup path that make this possible.

## Start with the directory that owns the work

A directory is Evander's logical place for an organization, team, or project. It is not a folder
containing source code. Selecting a directory tells Evander which inherited configuration,
permissions, providers, repositories, workflows, and communication targets belong to the work.

Every installation begins with an immutable top-level directory called `root`. Child directories
form a tree. A project might therefore appear as:

```text
root
└── northstar-platform
    └── mission-control
```

If an operator selects `mission-control`, Evander starts with its built-in defaults, applies the
settings at `root`, then the settings at `northstar-platform`, and finally the project's own
overrides. The project does not have to copy every organization-wide setting merely to change its
VM template or provider project.

![Root-to-leaf directory configuration inheritance](../../assets/evander-user-manual/diagrams/04-directory-config.svg)

Read the figure from the broadest scope at the top to the selected project at the bottom. The
result is one validated set of settings for the job; the project layer does not become a separate
control plane.

<p class="figure-caption">Figure 5.1 — Evander combines defaults and ordered directory layers, while preserving broader tool denials.</p>

## What an operator may notice

The same screen can show a local value, an inherited value, or a value selected for one running
session. Those are different views of the same configuration story.

| What you see | What it usually means | Safe next step |
|---|---|---|
| A field is present although the project has no local value | The value came from a parent directory or a built-in default | Inspect the effective value and its source before adding an override |
| Changing a list removes inherited entries | Lists replace rather than extend the parent list | Enter the complete list intended for this project, or remove the override |
| A project cannot re-enable a denied tool | A broader security rule deliberately remains in force | Find the denying ancestor and ask for a security review there |
| A save is rejected | Validation, the selected directory, or workflow revision/lock protection prevented the change | Read the validation message; reload the current revision if necessary |
| A valid screen does not match a running session | The session may retain an explicit selection made when it was created | Inspect the session as well as the current directory configuration |
| Admin Web and a background service appear to see different state | The processes may be using different runtime roots or code revisions | Record the directory, service, and visible values for an administrator |

Do not copy an inherited value into the project merely to make it visible in the raw view. That
creates a new local override and can prevent later parent changes from reaching the project.

## Raw, effective, and selected values

Evander presents three useful views:

- **Raw** is exactly what one directory overrides.
- **Effective** is the validated result after defaults and the full path from `root` to the
  selected directory have been combined.
- **Selected runtime value** may additionally be pinned or overridden by session metadata, such as
  a model-provider mode or exact worker image reference selected when that session was created.

The Admin Web configuration surface and `directories config-show` expose raw and effective
information. Provenance—the recorded source of a value—answers “why is this here?” A field can be
effective even when no file exists at the leaf, because it came from a default or an ancestor.

Sessions carry a canonical directory path. Policy and provider resolution begin there, with
supported session-level selections added where defined. Administrative ownership checks and
validation therefore guard moves or deletions while sessions or workflows still refer to a
directory.

## A project example

Suppose `root` selects `libvirt_vm`, disables email sending, and denies a destructive MCP tool.
`root/northstar-platform` selects a provider project and an approval notification target.
`mission-control` selects a VM template and narrows the allowed contacts.

The project's implementation session receives the combined result. It does not duplicate the
root relay settings, and it cannot undo the destructive-tool denial. Its contacts list replaces
the inherited list rather than extending it. Provenance can show that the provider organization
came from `root` while the project name came from `northstar-platform`.

This pattern lets operators own project-specific automation while administrators maintain broader
policy without one enormous global configuration file.

## Troubleshooting configuration

Start in Admin Web with the directory that owns the work. Record the raw value, effective value,
and displayed source for the field in question. If a run or session is involved, also capture its
run ID or session ID and the value shown there.

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

Do not edit a runtime database, JSON state file, or live workflow pack to force the display into the
desired state. If the safe path is unclear, hand the administrator the selected directory, domain,
raw and effective values, provenance, validation message, relevant run/session ID, and approximate
time.

The remainder of this chapter is administrator and extension-author reference material.

## Administrator reference: five kinds of configuration-shaped data

Several Evander files contain JSON or environment variables, but they have different ownership and
update rules:

| Kind | Typical location | Purpose | Safe mutation path |
|---|---|---|---|
| Host bootstrap | `runtime/host-config.json` | Runtime root, listener addresses, core store paths, add-on loader | Setup/CLI and supported host configuration flow |
| Directory config | `runtime/directory-config/root/...` | Inheritable policy, providers, contacts, email, wake, VM, and automation behavior | Admin Web or `evander-cli directories config-set` |
| Workflow config | `config/trigger-orchestrator/config.json` and referenced packs | Graphs, nodes, edges, provider bindings, orchestration store settings | Admin Web revision/lock API; files are loading surfaces, not a concurrency bypass |
| Secrets/service env | `config/*.env`, provider JSON env references, optional `secrets.env` | Credentials and process-specific listener/auth settings | Host operator and secret-management process |
| Runtime state | JSON/SQLite beneath the runtime root | Sessions, approvals, queue/journal, Git registry, audit, add-ons | Domain CLI/Admin/API only; do not hand-edit |

The distinction prevents two recurring mistakes. A listener port belongs to host bootstrap or a
service environment, not a child project's provider overlay. Likewise, `orchestration.db` may
contain JSON columns, but it is durable state, not configuration to edit into a desired state.

### Bootstrapping a runtime

The repository-oriented first-run sequence is:

```bash
uv pip install -e '.[dev]'
evander-cli init-state --runtime-dir runtime
```

Initialization establishes `host-config.json`, the directory registry, the immutable root
directory, root directory-config files, and other required stores. The packaged `HostConfig`
default runtime is `/var/lib/evander`; commands and repository services commonly pass `runtime`
explicitly. Relative runtime paths are resolved, and default store paths follow the selected
runtime root.

Startup reads only the host bootstrap and root directory configuration. Diagnose those canonical
surfaces directly; gateway YAML is no longer accepted as bootstrap or migration input.

At startup, `load_runtime_gateway_config` combines the host bootstrap with root directory domains
into a validated `GatewayConfig`. Broader assembly then adds the repositories and services that a
process needs.

### The supported composition root

`src/evander/admin/composition.py::build_admin_runtime` (the `build_admin_runtime` composition
function) is the supported assembly surface for the
CLI, gateway integrations, Admin Web, workflow providers, and add-ons that need administration
services. It:

1. resolves the runtime directory;
2. loads or creates host bootstrap configuration;
3. opens the directory store and ensures `root` exists;
4. opens the directory-config store and ensures root domains exist;
5. resolves the current runtime gateway configuration;
6. builds the shared `RuntimeContext`;
7. opens the audit index and starts best-effort import from canonical JSONL;
8. composes narrow domain services for directories, sessions, approvals, issues, Git, workflows,
   workers, reports, skills, and other operator functions.

`RuntimeContext` is the lower-level shared bundle. It contains repositories and runtime services
such as sessions, approvals, tool approval leases, directories, MCP transport bindings, drafts,
email and communication stores, wake state, prompt queues, scripts, policy/provider resolvers,
`PolicyEngine`, and `AuditLogger`.

This assembly boundary matters to extensions. Reconstructing stores ad hoc can choose a different
runtime root, omit a migration, bypass a repository adapter, or create policy and provider
resolvers with different context. Add behavior through narrow services and the add-on/provider
registration surfaces rather than creating a second hidden control plane.

![Runtime composition from bootstrap files through resolved configuration to shared services](../../assets/evander-user-manual/diagrams/03-runtime-composition.svg)

<p class="figure-caption">Figure 5.2 — One supported composition path keeps CLI, gateway, Admin Web, workflows, and add-ons on the same runtime truth.</p>

### Directory invariants and authorization scope

The directory store rejects a missing parent, duplicate sibling name, invalid canonical path, and
cycles. Root cannot be moved into or replaced by a child. The same canonical path can scope:

- effective policy and provider settings;
- Admin Web RBAC assignments;
- session and workflow ownership;
- managed repositories and provider operations;
- communication targets and Team Add-on records.

A directory is a logical ownership and inheritance node whose configuration happens to be stored
under a matching runtime path; it is not a filesystem checkout.

### Configuration domains

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
| `connectors` | `connectors.json` | Named operations, drivers, and host-owned authentication profiles |
| `host_directories` | `host-directories.json` | Approved host filesystem resources and operation limits |

Classification profiles, model routes (including OpenRouter), and managed website login profiles
are fields in the `providers` domain. Communication targets use the revisioned
`communication-targets.db` store, not an extra directory-domain JSON file. Repository index settings
live in `code-index/state.db` and use the index configuration service.

Hyphenated CLI aliases such as `ado-worker` and `issue-setup` are normalized to canonical domain
names. Legacy domain files and metadata overlays are migration compatibility surfaces; new work
should use the canonical files.

### Resolution mechanics: root to leaf

To resolve the example project's `policy`, Evander starts with model defaults, applies any raw
`root/policy.json`, then `root/northstar-platform/policy.json`, then the leaf
`root/northstar-platform/mission-control/policy.json`. Each applied layer records directory ID,
path, domain, and overlay. Provenance maps effective leaf fields back to their source.

Merge semantics are deliberately simple:

- when both current and overlay values are objects, keys merge recursively;
- every other overlay value replaces the current value;
- therefore lists replace rather than concatenate;
- an explicit `null` replaces an inherited value with `null` if the domain schema permits it;
- the final effective payload is validated as a domain model.

A child can replace an inherited list of approval notification targets without inheriting every
root target. It cannot append one item merely by supplying a one-item list.

Policy adds one monotonic security rule: every MCP tool denied by any applied ancestor remains
denied in the effective result, even if a child overlay writes `denied: false`. This exception
prevents delegation from weakening an upstream prohibition while retaining ordinary replacement
semantics for other values.

### Runtime directories and durable stores

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
| `communication-targets.db` | Directory-scoped communication target definitions and revisions |
| `container-worker-assignments.db` | Durable session-to-worker connection assignments |
| `code-index/state.db` | Repository index settings, generations, and job state |
| `addons/` | Add-on-owned runtime state, including Team state when enabled |

The service and repository types define transaction and locking behavior. JSON stores use their
supported atomic mutation paths; SQLite stores use schema, busy timeout, WAL, and domain
transactions. A text editor provides none of those guarantees.

## Administrator runbook

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
3. Determine whether the field belongs to host, directory, workflow, secret, or state
   configuration.
4. Use Admin Web's current revision/lock path or `directories config-set`; never write a live
   workflow pack from a stale local copy.
5. Read back the raw and effective result.
6. Check the relevant worker/service log for reload acceptance.

For first-run or recovery initialization, `evander-cli init-state --runtime-dir runtime` is
idempotent setup, not a command to erase current state.

## Source trail

- `src/evander/host_config.py` and `src/evander/bootstrap.py` — host bootstrap,
  runtime-relative paths and root initialization.
- `src/evander/admin/composition.py` and `src/evander/runtime.py` — supported composition root and
  shared runtime services.
- `src/evander/directories.py` and `src/evander/directory_config.py` — tree invariants, domain
  files, ancestry merge, provenance, and validation.
- `src/evander/policy_config.py` and `src/evander/provider_config.py` — effective policy/provider
  resolution and monotonic tool denies.
- `src/evander/workflow/runtime_config.py` and
  `src/evander/admin/services/workflow_config_service.py` — workflow loading, validation, hot
  reload, revision, lock, and history behavior.
- `docs/configuration-reference.md` and `docs/operations.md` — field-level and operator guidance.
- `tests/test_directory_config.py`, `tests/test_directory_admin.py`, `tests/test_cli.py`, and
  `tests/test_admin_extracted_services.py` — inheritance, initialization, mutation, and assembly
  contracts.
