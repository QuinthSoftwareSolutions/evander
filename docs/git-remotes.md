# Managed Git Repositories

Evander keeps one host-owned bare repository for each canonical upstream inside a directory trust
boundary. Workflows, VM sessions, container-worker sessions, and Team projects address that
repository by `managed_repo_id`; they do not store or exchange host checkout paths.

This is a quarantine and transport boundary, not an execution sandbox. Agent-authored code still
runs only inside a VM or container. Host worktrees are used for narrow Git operations and must never
be used to run untrusted builds or tests.

## Repository identity and scope

The registry lives at `runtime/managed-git-repositories.json`. It is protected by a file lock and
updated with atomic replacement. Each record includes the owning directory, normalized canonical
repository identity and URL, provider, display name, default branch, refresh source, host commit
identity, internal bare-repository location, timestamps, and active bindings. It never contains
credentials or temporary worktree paths.

`managed_repo_id` is deterministic: `mr_` followed by 20 hexadecimal characters derived from the
normalized owning directory and canonical upstream identity.

Registration normalizes equivalent GitHub and Azure DevOps SSH/HTTPS URLs. Generic URLs have
credentials, query strings, fragments, and a trailing `.git` removed for identity matching. A
local source uses its canonical `origin` when one is available; otherwise its resolved absolute
path is the identity. Registration never changes the operator's source checkout.

An owning directory makes the repository available to its descendants. A descendant registration
of the same upstream reuses the ancestor-owned repository. Sibling directories are separate trust
boundaries and receive separate IDs and bare repositories, even for the same upstream.

## Register, inspect, and refresh

Register a local checkout or remote URL before a workflow or session clones it:

```bash
evander-cli git-repos register \
  --directory-path root/acme \
  --repo-source /srv/repos/acme

evander-cli git-repos register \
  --directory-path root/acme \
  --repo-source https://github.com/acme/platform.git \
  --default-branch main
```

Registration is idempotent for the same directory/upstream pair and refreshes canonical refs by
default. Use `--no-refresh` only when registration must be recorded before the source is reachable.

```bash
evander-cli git-repos list --directory-path root/acme
evander-cli git-repos show mr_0123456789abcdef0123 --directory-path root/acme/team-a
evander-cli git-repos refresh mr_0123456789abcdef0123
```

The equivalent privileged Admin API is:

- `GET /api/admin/git-repositories`
- `POST /api/admin/git-repositories`
- `GET /api/admin/git-repositories/{managed_repo_id}`
- `POST /api/admin/git-repositories/{managed_repo_id}/refresh`
- `DELETE /api/admin/git-repositories/{managed_repo_id}`
- `POST /api/admin/git-repositories/maintenance`
- `POST /api/admin/git-repositories/migrate`

Mutations require directory-scoped `admin.owner` permission and unlocked privileged operations.
Registration JSON contains `directory_path`, `repo_source`, optional `default_branch`, and optional
`refresh`. Maintenance accepts optional `managed_repo_id`. Migration accepts
`{"stage":"dry-run"}`, `{"stage":"apply"}`, or
`{"stage":"finalize","migration_id":"<id>"}`; stages cannot be combined.

## Clone and guest push flow

After registration, pass only the managed ID to VM and agent clone operations:

```bash
evander-cli clone-repo-into-vm vm-14084 \
  --managed-repo-id mr_0123456789abcdef0123 \
  --directory-path root/acme \
  --session-id issue-14084-session
```

`effect:vm:clone-repo` and `effect:agent:clone-repo` use the same contract. The control plane
resolves the internal restricted-SSH URL; neither the upstream URL nor the host bare path is sent
through workflow outputs. Container-worker clones follow the same path and
do not create a second cache mirror.

Writable sessions receive a dedicated SSH key. The forced command permits writes only to:

- `refs/heads/agents/<session-id>/**`
- that session's PR notes ref

It rejects writes to other agents, ordinary branches, `review/**`, `team/**`, tags, configuration,
or hooks. Read access remains repository- and directory-scoped. Set `git_access_mode` to
`read-only` when a container session needs fetch access only.

Guest checkouts provide `git sync-base`, `git push-agent`, and `git agent-pr-note`. A normal agent
push is:

```bash
git push-agent
```

The helper verifies the base and destination ancestry, pushes the current commit into the exact
session namespace, and publishes the configured notes ref. The bare repo's post-receive hook only
enqueues a structured event; it never receives upstream credentials and never publishes upstream.

## Ref ownership and refresh

Namespaces have one owner:

- canonical refresh owns ordinary `refs/heads/*` and tags;
- guests own their authorized `refs/heads/agents/**` namespace and session notes;
- host workflows own `refs/heads/review/**`, `refs/heads/team/**`, and `refs/evander/**`.

Registration and refresh reject an upstream that already contains branches under the reserved
`agents/`, `review/`, or `team/` prefixes.

A refresh fetches into `refs/evander/refresh/<operation-id>/**`, then applies canonical ref changes
with one `git update-ref --stdin` transaction. It never prunes guest, review, Team, notes, or
operation refs. Git remotes are not added or rewritten during operations because linked worktrees
share repository configuration.

## Pinned promotion, notes, and publication

`trigger:git:push` selects a repository by exact ID or managed-ID glob. Its payload includes the
managed ID, ref and branch, old and new commit hashes, session, notes ref, default branch, and
review branch. Downstream nodes must carry the trigger's exact new commit SHA.

The managed Git workflow contracts are:

| Surface | Required repository contract |
|---|---|
| `effect:git:ensure-managed-repo` | `directory_path`, transient `repo_source`; returns `managed_repo_id` |
| `effect:vm:clone-repo` / `effect:agent:clone-repo` | `managed_repo_id` plus a session/VM selector |
| `effect:git:promote-agent-branch` | `managed_repo_id`, `agent_branch`, `target_branch`, `expected_commit_sha` |
| `query:git:pr-note` | `managed_repo_id`, exact `commit_sha`, `notes_ref` |
| `effect:git:push-branch` / `effect:pr:create` | `managed_repo_id`, `source_ref`, `expected_commit_sha`, `destination_branch` |

Promotion verifies that the agent ref still equals `expected_commit_sha` and pins that object to a
host-only operation ref in the same ref transaction. A later agent push therefore cannot replace
the commit under review. A superseded event returns `source_ref_moved` instead of promoting the
newer tip implicitly. Updates of review and Team refs use compare-and-swap semantics.

PR-note lookup reads only the note attached to the pinned commit. It does not walk first parents.

Publication fetches the upstream destination into an operation-local ref, requires that destination
to be an ancestor of the approved SHA, and pushes the exact SHA with an explicit lease. Operations
for the same destination serialize; unrelated destinations can proceed concurrently. PR creation
reuses an existing PR for the same source/destination when possible.

## Temporary worktrees

Operations that need checked-out files, including merge consolidation and PR preparation, create a
unique detached worktree beneath:

```text
runtime/git-worktrees/<managed_repo_id>/<operation-id>/checkout
```

The root is mode `0700`. The checkout starts from a pinned host-only operation ref, never a shared
review or Team branch. Checkout hooks, LFS smudge, submodule initialization, inherited commit
signing, and interactive prompts are disabled. A secret-free manifest and per-operation lock are
stored beside it; no worktree path appears in workflow output or the managed-repository registry.

Success, failure, cancellation, and normal termination remove the worktree, prune its registration,
and compare-and-swap delete the operation ref. Worker startup and periodic maintenance reconcile
SIGKILL leftovers; routine reconciliation is rate-limited to at most once every five minutes. Only
lock-free managed worktrees older than the configured stale threshold are removed; the default
threshold is one hour.

## Credentials

Upstream authentication uses Evander's fixed host-owned askpass path and credential environment.
Tokens must not appear in repository URLs, Git config, command arguments, manifests, registry
records, logs, or workflow outputs. Interactive credential prompts are disabled.

Configure provider token environment names through directory provider configuration and expose the
actual secret only to the control-plane/effect-worker process. GitHub publication may use the host
`gh` credential integration; Azure DevOps publication sends the configured PAT in an HTTP
authorization header.

## Maintenance, retention, and deletion

Long-lived managed repositories and unpublished refs are retained when issues, sessions, or Team
projects are deleted. Those lifecycle operations remove only their bindings and SSH authorization.
There is no time-based repository deletion.

Run locked worktree reconciliation, worktree pruning, and Git maintenance explicitly:

```bash
evander-cli git-repos maintenance
evander-cli git-repos maintenance --managed-repo-id mr_0123456789abcdef0123
```

Delete only after confirming the repository is no longer needed:

```bash
evander-cli git-repos delete mr_0123456789abcdef0123
```

Deletion refuses a repository with active issue/project/session bindings, authorized sessions,
active or unreconciled worktrees, or unresolved migration state. There is no force-delete path.

## Breaking migration from legacy mirrors

The managed registry schema marker is required. A runtime with legacy path-based managed-Git state
fails preflight and directs the operator to the explicit migration. The new runtime does not
dual-read old checkout or bare-repository path fields.

Stop the trigger and effect workers before migration and ensure no Git operation or agent session is
active. Then inventory without writing:

```bash
evander-cli git-repos migrate --dry-run
```

The report groups legacy repositories by normalized directory/upstream identity and inventories
old bare repositories, cache mirrors and checkouts, Team projects, workflow packs, sessions, and
authorized-key bindings. Missing canonical metadata and preserved-ref collisions block apply. A
collision in agent, review, Team, note, or PR refs aborts that repository group; migration never
chooses a winner.

After resolving every blocker, apply explicitly:

```bash
evander-cli git-repos migrate --apply
```

Apply creates the registry and managed bare repositories, refreshes canonical refs, imports
preserved refs, and rewrites Team/session/issue/key/workflow bindings to managed IDs. Workflow pack
updates use the revision/lock service and abort on a concurrent edit. Stopped VM and container
sessions are marked for Git-remote reconciliation before their next wake.

Apply writes a manifest containing old/new mappings and verification hashes. Evander-owned old
repositories and cache checkouts leave active routing but remain read-only rollback artifacts.
User-owned source checkouts are never changed or deleted.

After services have restarted, smoke-test both a VM and container-worker path: managed clone,
restricted agent push, queued trigger, pinned promotion, exact note read, PR publication, and Team
release consolidation. Finalization is a separate operator decision:

```bash
evander-cli git-repos migrate --finalize <migration-id>
```

Finalize re-verifies registry records, refs, bindings, active keys, and the absence of legacy
configuration. It deletes only Evander-owned rollback artifacts named in that manifest. It never
uses a retention timer or deletes a user checkout.
