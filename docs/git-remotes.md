# Managed Git Repositories

Evander keeps one host-owned bare repository for each canonical upstream inside a directory trust
boundary. Workflows, VM sessions, container-worker sessions, and Team projects address that
repository by `managed_repo_id`; they do not store or exchange host checkout paths.

This is a quarantine and transport boundary, not an execution sandbox. Agent-authored code still
runs only inside a VM or container. Host worktrees are used for narrow Git operations and must never
be used to run untrusted builds or tests.

The restricted Git account does not need the shared host secret environment file. Setup and the
systemd installer remove its legacy named read/traversal ACLs and close the file's group/other
permissions, preserving unrelated named ACL entries. Managed refresh uses the existing host
refresh surface. A Git account sharing the control-plane UID still shares its authority; account
names alone do not isolate secrets. The new [credential-store key](credentials.md) is provisioned
only to its explicit control-plane consumers.

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

Admin Web's **Git Repositories** page (`/#git-repositories`) provides the same operator lifecycle
surface for the selected directory: register a canonical source, inspect the stable repository ID
and bindings, refresh upstream refs, run maintenance, set a host commit identity, or delete an
unbound repository. Privileged operations must be unlocked for mutations; the backend retains its
normal directory-ownership, binding, and credential protections.

```bash
evander-cli git-repos list --directory-path root/acme
evander-cli git-repos show mr_0123456789abcdef0123 --directory-path root/acme/team-a
evander-cli git-repos refresh mr_0123456789abcdef0123
```

Set the author and committer identity for host-created consolidation commits on one managed
repository without changing directory defaults or other repositories:

```bash
evander-cli git-repos set-host-identity mr_0123456789abcdef0123 \
  --directory-path root/acme \
  --name "Release Automation" \
  --email "release-automation@example.com"
```

The operation acquires the repository's exclusive operation lock, updates only that registry
record, and affects subsequent host-created commits. It does not rewrite existing commits or
change agent checkout identities.

## Materialize a Team release candidate for human testing

An authorized human reviewer can copy the supported command from
**Teams → Delivery → release detail → Test this candidate**. Its shape is:

```bash
evander-cli team checkout-release-candidate <release-package-id> \
  --target ./release-candidate-review \
  --expected-commit-sha <candidate-sha> \
  --runtime-dir /absolute/path/to/evander/runtime
```

The command reads the durable Team release package, resolves its project and
managed repository internally, and verifies that the immutable release ref
still equals the recorded candidate commit and tree. It then creates a new
detached checkout at `--target`. The target's parent must exist, while the
target itself must not: existing paths are refused even when empty. Paths
inside Evander's runtime directory are also refused.

This is the supported operator alternative to discovering
`runtime/managed-git/*.git` paths. Those private paths are deliberately absent
from the UI and command. The absolute runtime root is included so the copied
command remains valid when run from another repository or working directory;
it is configuration identity, not a private repository path. The resulting checkout has only the sanitized
canonical repository URL as `origin`; it retains no private managed-repository
or temporary-worktree path.

When the Admin Web is remote, an authorized reviewer can instead use
**Download review bundle** on the human-review request. The ZIP is a
source-only export of the verified candidate and includes
`EVANDER-REVIEW.json` with the pinned ref, commit, tree, and release-review
provenance. It is not a Git checkout: unpack it and run the repository's usual
dependency setup locally, such as `dotnet restore` or `npm ci` where relevant.

The explicit refresh command is an operator diagnostic and recovery surface. Normal managed Git
reads do not require it: restricted SSH keys provisioned by Evander require the host-owned
`evander-managed-git-refresh.service` to refresh the exact authorized repository before
`git-upload-pack` or `git-upload-archive` serves refs. This covers clone, fetch, pull,
`ls-remote`, and archive reads. If the broker or upstream is unavailable, the Git read fails with
a stale-ref refusal instead of succeeding from an old mirror. The SSH wrapper never receives
provider credentials and cannot request a different repository. Broker failures expose a
sanitized message, stable `error_code`, and exception category without returning raw Git output.
The codes are `broker_busy`, `upstream_host_key_verification_failed`,
`upstream_host_resolution_failed`, `upstream_authentication_failed`,
`upstream_connection_failed`, `upstream_tls_verification_failed`,
`local_ref_update_failed`, `upstream_ref_policy_rejected`, and the fallback
`upstream_refresh_failed`.

The equivalent privileged Admin API is:

- `GET /api/admin/git-repositories`
- `POST /api/admin/git-repositories`
- `GET /api/admin/git-repositories/{managed_repo_id}`
- `POST /api/admin/git-repositories/{managed_repo_id}/refresh`
- `PUT /api/admin/git-repositories/{managed_repo_id}/host-commit-identity`
- `DELETE /api/admin/git-repositories/{managed_repo_id}`
- `POST /api/admin/git-repositories/maintenance`
- `GET /api/admin/sessions/{session_id}/git-grants`
- `PUT /api/admin/sessions/{session_id}/git-grants/{managed_repo_id}`
- `POST /api/admin/sessions/{session_id}/git-grants/{managed_repo_id}/clone`
- `DELETE /api/admin/sessions/{session_id}/git-grants/{managed_repo_id}`

Mutations require directory-scoped `admin.owner` permission and unlocked privileged operations.
Registration JSON contains `directory_path`, `repo_source`, optional `default_branch`, and optional
`refresh`. Host-identity JSON contains required `name` and `email`. Maintenance accepts optional
`managed_repo_id`.

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

A persistent agent can hold grants for multiple repositories. Each `(session, managed_repo_id)`
pair receives its own deterministic guest key name and SSH host alias, while the host forced
command remains scoped to exactly that repository. Granting or revoking one repository does not
replace another repository's key, SSH config block, metadata, or authorization.

Use the session grant commands when access should exist independently of a clone:

```bash
evander-cli git-repos grants session-123
evander-cli git-repos grant session-123 \
  --managed-repo-id mr_0123456789abcdef0123 \
  --access-mode read-only
evander-cli git-repos revoke-grant session-123 \
  --managed-repo-id mr_0123456789abcdef0123
```

Standalone grants are session-persistent. A read-only grant therefore supports repeated clone,
fetch, pull, `ls-remote`, and archive operations until it is explicitly revoked or the session is
destroyed. It does not permit pushes. To provision access and clone in one operation:

```bash
evander-cli git-repos clone-into-agent session-123 \
  --managed-repo-id mr_0123456789abcdef0123 \
  --access-mode read-only \
  --lifetime session
```

Clone effects and `clone-into-agent` also accept `--lifetime operation`. That temporary form is
removed after the clone finishes and is intended for disposable consultations. It is not a
standalone grant and cannot be used for later refreshes.

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

Pull-time refresh requests travel over `/run/evander/managed-git-refresh.sock`. The broker accepts
only same-host peer credentials and registry-backed `mr_<20-hex>` identifiers derived from the
SSH key's already-validated exact repository path. Existing per-repository locks serialize
overlapping refreshes. There is intentionally no freshness TTL: a successful read reflects an
upstream fetch performed for that request.

## Pinned promotion, notes, and publication

`trigger:git:push` selects a repository by exact ID or managed-ID glob. Its payload includes the
managed ID, ref and branch, old and new commit hashes, session, notes ref, default branch, and
review branch. Downstream nodes must carry the trigger's exact new commit SHA.

The managed Git workflow contracts are:

| Surface | Required repository contract |
|---|---|
| `effect:git:ensure-managed-repo` | `directory_path`, transient `repo_source`; returns `managed_repo_id` |
| `effect:vm:clone-repo` / `effect:agent:clone-repo` | `managed_repo_id` plus a session/VM selector |
| `effect:agent:grant-repo-access` | session/VM selector, `managed_repo_id`, access mode; creates a session-persistent grant |
| `effect:agent:revoke-repo-access` | session/VM selector and exact `managed_repo_id` |
| `effect:git:promote-agent-branch` | `managed_repo_id`, `agent_branch`, `target_branch`, `expected_commit_sha` |
| `query:git:merge-base` | `managed_repo_id`, `target_branch`, exact `candidate_commit_sha`; returns the pinned target and merge-base SHAs |
| `query:git:pr-note` | `managed_repo_id`, exact `commit_sha`, `notes_ref` |
| `effect:git:push-branch` / `effect:pr:create` | `managed_repo_id`, `source_ref`, `expected_commit_sha`, `destination_branch` |

Promotion verifies that the agent ref still equals `expected_commit_sha` and pins that object to a
host-only operation ref in the same ref transaction. A later agent push therefore cannot replace
the commit under review. A superseded event returns `source_ref_moved` instead of promoting the
newer tip implicitly. Updates of review and Team refs use compare-and-swap semantics.

PR-note lookup reads only the note attached to the pinned commit. It does not walk first parents.

Merge-base lookup refreshes only the requested upstream target branch, pins the observed target
commit, requires the candidate object to exist in the managed repository, and returns their unique
merge base. Workflows should keep this PR-content boundary separate from publication lease bases.

Publication fetches the upstream destination into an operation-local ref, requires that destination
to be an ancestor of the approved SHA, and pushes the exact SHA with an explicit lease. Operations
for the same destination serialize; unrelated destinations can proceed concurrently. PR creation
reuses an existing PR for the same source/destination when possible. Azure DevOps may briefly
report the preceding source commit in `lastMergeSourceCommit` after a push. Before accepting a
reused PR, Evander checks the highest-ID PR iteration for the expected source commit within a
bounded confirmation window; a genuinely moved source still returns a conflict.

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

Team task worktrees are a separate lifecycle. A persistent coding task keeps a
deterministic physical checkout for the same session/repository/task identity so
later dispatches can resume it, while each dispatch receives a fresh
`GitWorkContext` bound to that dispatch's immutable scope. Team prompt and UI
payloads map task, project, managed repository, worktree, branch, and context;
the base repository checkout is control-only.

When a task resolves or is reassigned away from the checkout owner, Team creates
a durable `WorktreeCleanupCandidate`. The workflow field
`worktree_cleanup_dry_run` deliberately has three modes:

- `true` performs safety audits only and never attempts physical removal;
- `false` permits an eligible candidate's first reconciliation pass to attempt
  physical removal immediately; and
- omitted performs a dry run first, records `dry_run_safe`, and schedules the
  physical attempt for a later pass after `worktree_cleanup_retry_seconds`.

Every mode refuses removal while a preparing/leased dispatch or active Git
context references the checkout, while retained commit evidence is incomplete,
or when repository/path/branch/`HEAD`/dirty-file checks disagree. Preparation
and cleanup share a session/repository coordinator lock; a physical attempt
repeats Git inspection under the execution backend's repository lock immediately
before `git worktree remove` and pruning.

Ignored caches may be reclaimed only after tracked and non-ignored untracked
source checks pass. A refusal remains `recovery_required` with the exact task,
repository, path, branch, failed check, timestamp, byte counts, exact ignored
file count, full-inventory SHA-256, and a deterministic sample of at most 200
paths. Successful physical removal is audited before obsolete unreferenced
context metadata is pruned. Do not use managed-Git operation maintenance or a
recursive delete as a substitute for this task-checkout recovery path.

## Credentials

Upstream authentication uses Evander's fixed host-owned askpass path and credential environment.
For Azure DevOps, `azure_devops_pr_pat_env` defaults to `AZURE_DEVOPS_PR_PAT` and
`azure_devops_pat_env` defaults to `AZURE_DEVOPS_PAT`. Managed Git tries the first
populated PR PAT variable, then the populated general PAT variable. If it selects
a PAT for a registered Azure DevOps SSH refresh source, the Git child process
receives an in-memory `url.<canonical-https-url>.insteadOf=<ssh-source>` override.
The stored refresh source, canonical repository identity, registry, and operator
checkout are not rewritten.

Tokens must not appear in repository URLs, persistent Git config, command
arguments, manifests, registry records, logs, or workflow outputs. Interactive
credential prompts are disabled.

Configure provider token environment names through directory provider configuration and expose the
actual secret only to the control-plane/effect-worker process. GitHub publication may use the host
`gh` credential integration; Azure DevOps publication sends the configured PAT in an HTTP
authorization header.

Team releases may pass `publication_context_id` to `effect:pr:create` to use the captured
ADO work-item assignee's Microsoft OAuth connection or personal PAT. Registered users manage these connections through
**My ADO connection** in Admin Web. The context is bound to the repository, directory, and
approved commit; task Git author metadata cannot select credentials. OAuth is preferred, with
the same account's saved PAT as fallback. With the default `allow_bot_fallback` publication
policy, mixed-owner releases and missing or rejected personal credentials use the configured bot.
Set `publication_policy: require_personal` to require the captured assignee's personal connection
and reject missing attribution or an existing PR with another creator before pushing. It also
prevents bot fallback during uncertain-creation reconciliation. Publication reports the
intended and actual creator and any fallback reason; existing PR creators remain unchanged.
New Team publication contexts also capture originating work-item IDs from the release's
deliverables and nested contributions in the repository's ADO organization. PR creation and
reuse add native Pull Request links to those items using the selected connection. Retries
preserve existing links. Results include `work_item_ids`, `linked_work_item_ids`, and
`work_item_linked`; a failed association returns `work_item_link_warning` while retaining
the created PR. Older contexts without captured IDs require an explicit `work_item_id` to
link an item; no IDs are inferred from task descriptions or branch names.
See [Personal Azure DevOps PR connections](operations.md#personal-azure-devops-pr-connections)
for storage, rotation, and reconciliation behavior.

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

Deletion refuses a repository with active issue/project/session bindings, authorized sessions, or
active or unreconciled worktrees. There is no force-delete path.
## Repository indexes

Opt-in repository indexes retain exact commits under `refs/evander/code-index/BUILD_ID`
with `code_indexes` bindings. Builds clone through normal read-only session grants.
Retention releases each pin with an expected-commit check after worker cleanup.
Team queries can reuse a proven indexed ancestor without granting arbitrary commit
access: responses identify the source and requested commits and exclude changed paths.
Guest commits/pushes and Team preparation do not trigger compiler indexing.
See [Repository indexing](repository-indexing.md) for access controls and portable exports.
