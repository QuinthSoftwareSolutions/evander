# 11. Managed Git, Artifacts, and Code Consultations

> **At a glance.** Source code, produced files, approved inbound host files, and a quick question about a repository are different kinds of things, so Evander moves them in different ways. Git carries exact versions of code; artifact storage carries durable result files; a Host Directory imports from one approved root; a consultation gives a temporary reader one bounded question. Choosing the right route keeps the result traceable without exposing arbitrary folders on the host.

## Three kinds of movement

It helps to begin with the thing being moved:

- **Source code** belongs in managed Git. Git gives Evander an exact commit to review and publish.
- **Reports, bundles, and other produced files** belong in artifact storage. Publishing makes one immutable archive; an artifact space can organize versions without rewriting old archives.
- **Approved inbound files on the host** belong in a Host Directory. An agent receives named
  list/import/delete capabilities and relative paths, never the absolute root.
- **A bounded question about code** belongs in a code consultation. Evander pins one repository snapshot, gives a temporary worker read-only access, accepts one structured answer, and cleans the worker up.

The matching identities are:

| Need | Correct surface | Durable identity |
|---|---|---|
| Clone, review, merge, or publish source code | Managed Git | `managed_repo_id` + ref + exact commit SHA |
| Preserve generated files or a deliverable | Published artifact | immutable artifact key + archive SHA-256 |
| Inspect or import from one approved host folder | Host Directory | resource ID + relative path + revision |
| Organize versions of published outputs | Artifact space | space path + logical artifact ID + version |
| Ask a bounded question about a code snapshot | Code consultation | consultation ID + managed repo + pinned commit |

A host checkout path is not a public repository identity. `/artifacts` is not a Git remote. An artifact key is not a mutable “latest” file. A consultation is not a persistent Team member. Keeping those distinctions sharp prevents a large class of provenance and cleanup errors.

These are separate data planes, and host paths remain private implementation details in every flow.

![Managed Git carries a session-owned agent ref through an exact-SHA host pin, review, and lease-protected upstream publication.](../../assets/evander-user-manual/diagrams/08-managed-git.svg)

Read the diagram from left to right: the agent can propose a commit in its own area, but the host separately pins the exact version, reviews it, and publishes it under a fresh check of the destination.

<p class="figure-caption">Figure 11.1 — Ref ownership and exact-SHA pinning keep guest work, host review, and canonical publication as separate authority transitions.</p>

## One change, three routes

For the Northstar Platform telemetry-retry change:

1. The implementation session clones `mission-control` through its `managed_repo_id` and pushes only to the branch area assigned to that session.
2. The workflow records and pins the exact commit produced by that push. Review and release use that commit, not merely the latest version under a movable branch name.
3. The session may stage a test report under `/artifacts`. Publishing it under a key such as the following creates one immutable archive:

   ```text
   northstar-platform/mission-control/telemetry-retry/dispatch-8/test-report
   ```

4. If another agent needs one answer about that exact source snapshot, a code consultation can read the pinned commit without becoming a permanent Team role or receiving write access.

A newer report gets a new artifact key. A later code change gets a new commit. Keeping those identities distinct makes it possible to say precisely what was reviewed, what file bundle was retained, and which snapshot supported an answer.

## What an operator may notice

| What you see | What it usually tells you | Useful information to capture |
|---|---|---|
| A branch has moved, but review still shows an earlier commit | Evander pinned the exact submitted SHA instead of silently following the branch. | Managed repository, session, agent ref, and expected/reviewed SHA |
| Publication stops after the destination changes | The destination lease prevented stale automation from overwriting newer upstream work. | Managed repository, destination ref, approved SHA, and visible lease error |
| Publishing the same artifact key fails | Published keys are immutable; the original archive remains unchanged. | Artifact key, source session, intended new key, and error |
| An artifact space edit reports a conflict | Another writer changed the space revision first. | Space path, logical artifact ID, expected/current revision, and intended change |
| A consultation remains queued | The directory/profile may be at capacity, or the request may be waiting within its deadline. | Consultation ID, directory/profile, status, deadline, and source session |
| A consultation ends but cleanup is incomplete | Evander retains the terminal record and does not release capacity prematurely. | Consultation ID, worker session/job, cleanup status, and cleanup error |

## Reading common failures

The safe outcome is often a refusal rather than an automatic recovery. That refusal preserves the exact source, archive, or snapshot the workflow intended to use.

| Failure | Safe outcome |
|---|---|
| Managed source ref moves before promotion | Promotion returns `source_ref_moved`; no newer SHA is substituted. |
| Upstream destination changes before publication | The explicit lease fails; no blind overwrite occurs. |
| Merge conflict in an operation worktree | The operation fails and cleans up; Evander does not auto-resolve agent code. |
| Guest pushes outside its namespace | The forced SSH command rejects the update. |
| Duplicate published artifact key | Publication fails; the existing artifact remains unchanged. |
| Unsafe artifact path, symlink, or archive member | Read/stage/import rejects it instead of escaping the artifact root. |
| Artifact-space revision is stale | Mutation returns a revision conflict; reload and reapply intentionally. |
| Consultation exceeds capacity | It remains queued until capacity is released or its deadline expires. |
| Consultation result cites invalid lines/path | Result submission fails validation; the snapshot remains authoritative. |
| Consultation worker cleanup fails | The terminal record is retained and capacity is not released prematurely. |
| Host Directory entry changes after listing | Import/delete rejects the stale revision; list again before deciding. |
| Host Directory root or child is a symlink/mount crossing | The operation fails closed rather than following it. |

## Troubleshooting: what to hand to an administrator

Begin with the kind of object involved and its durable identity:

1. For source code, record the directory, `managed_repo_id`, session ID, relevant ref, and exact commit SHA.
2. For a published file bundle, record the artifact key, source session, publication status, and archive SHA-256 if shown. For an artifact-space conflict, also record the space path and revision.
3. For a Host Directory, record its resource ID, relative path, revision or recovery ID, and
   operation; never paste the operator-only absolute root into agent-visible notes.
4. For a code consultation, record the consultation ID, source session, pinned commit, status, deadline, and cleanup state.
5. Copy the exact refusal or error and note the last successful stage.
6. Give those details to an Evander administrator. Do not move branch labels to “match,” overwrite an artifact, delete a retained cleanup record, hand-edit Host Directory quarantine, or manually release a consultation snapshot or capacity slot.

The remainder of this chapter is managed-Git, Host Directory, artifact, consultation, and administrator reference material.

## Administrator reference: managed repository identity and trust scope

Evander keeps one host-owned bare repository for each canonical upstream *within an owning directory trust boundary*. The registry is `runtime/managed-git-repositories.json`; bare repositories live below `runtime/managed-git/`. Consumers address a repository by a deterministic ID shaped as `mr_` plus 20 hexadecimal characters.

Registration normalizes equivalent GitHub and Azure DevOps SSH/HTTPS URLs. It strips credentials, query strings, fragments, and a trailing `.git` from generic identities. A local checkout uses its canonical `origin` when available, otherwise its resolved path establishes identity. Registration never rewrites the operator's checkout.

Directory inheritance matters:

- an owning directory makes its managed repository visible to descendants;
- a descendant registration of the same upstream reuses the ancestor-owned repository;
- siblings are separate trust boundaries and receive separate managed IDs even for the same upstream;
- an operation must be authorized against the repository owner, not merely against a caller-supplied child path.

The registry contains canonical identity, provider, display name, default branch, refresh source, host commit identity, internal repository location, timestamps, and active bindings. It must not contain provider credentials or temporary worktree paths.

## Administrator reference: ref ownership

The bare repository is a controlled exchange, not a shared free-for-all.

| Namespace | Owner |
|---|---|
| ordinary `refs/heads/*` and tags | canonical refresh |
| `refs/heads/agents/<session-id>/**` and that session's notes | the authorized guest session |
| `refs/heads/review/**`, `refs/heads/team/**`, and `refs/evander/**` | host workflows |

Registration rejects an upstream that already uses reserved top-level branch prefixes such as `agents/`, `review/`, or `team/`.

Canonical refresh first fetches into an operation-scoped namespace under `refs/evander/refresh/`, then updates the ordinary refs in one `git update-ref --stdin` transaction. It does not prune agent, review, Team, note, or operation refs.

A writable guest receives a dedicated SSH key with a forced command. It can read only its authorized repository scope and write only its exact agent-branch namespace and notes ref. It cannot write ordinary branches, another session's branch, review/Team refs, tags, hooks, or configuration. A fetch-only session uses `git_access_mode: read-only` and receives no write path.

Guest clones include three helpers:

- `git sync-base` refreshes the expected base safely;
- `git push-agent` validates ancestry and pushes the current commit into the session namespace;
- `git agent-pr-note` attaches the structured PR note to the configured notes ref.

The bare repository's post-receive hook only enqueues a structured trigger event. It never receives an upstream token and never publishes to the canonical remote.

### Refresh credentials and broker failures

Normal guest clone, fetch, pull, <code>ls-remote</code>, and archive reads ask
the host-owned refresh broker to update the exact managed repository before
serving refs. Azure DevOps credential names resolve from the repository's
effective directory configuration:

- <code>azure_devops_pr_pat_env</code> defaults to
  <code>AZURE_DEVOPS_PR_PAT</code>;
- <code>azure_devops_pat_env</code> defaults to
  <code>AZURE_DEVOPS_PAT</code>; and
- Managed Git uses the first populated PR PAT variable, then the populated
  general PAT variable.

If a PAT is selected while the registered Azure DevOps refresh source uses SSH,
Evander gives only that Git child process an in-memory
<code>url.&lt;canonical-https-url&gt;.insteadOf=&lt;ssh-source&gt;</code>
override. It does not change the registry, canonical repository identity,
stored refresh source, persistent Git configuration, or operator checkout.

A failed broker request never returns raw Git output. It returns a sanitized
message, exception category, and one stable code:
<code>broker_busy</code>,
<code>upstream_host_key_verification_failed</code>,
<code>upstream_host_resolution_failed</code>,
<code>upstream_authentication_failed</code>,
<code>upstream_connection_failed</code>,
<code>upstream_tls_verification_failed</code>,
<code>local_ref_update_failed</code>,
<code>upstream_ref_policy_rejected</code>, or the fallback
<code>upstream_refresh_failed</code>. The restricted read fails rather than
serving stale refs.

## Administrator reference: pinned promotion and publication

A `trigger:git:push` event names the managed repository, changed ref, old and new SHA, session, notes ref, default branch, and derived review branch. Downstream logic must carry the event's exact new SHA.

Promotion checks that the source agent ref still equals `expected_commit_sha`, then pins that object under a host-only operation ref in the same transaction. If the agent has pushed again, the event becomes `source_ref_moved`; Evander does not silently review the newer commit. Review and Team target updates use compare-and-swap.

Publication is equally strict. When Northstar Platform's release agent publishes the reviewed change:

1. Fetch the upstream destination into an operation-local ref.
2. Verify that destination is an ancestor of the approved SHA.
3. Push the exact approved SHA with an explicit destination lease.
4. Serialize operations for the same destination while allowing unrelated destinations to proceed.

This makes a race visible instead of letting stale automation overwrite a newer upstream branch. PR creation may reuse an existing PR for the same source/destination, but it does not weaken the exact-SHA push contract. Azure DevOps can briefly leave <code>lastMergeSourceCommit</code> on the preceding push; Evander confirms the expected SHA against the highest-ID PR iteration within a bounded window before accepting the reused PR. A source branch that genuinely moved still produces a conflict.

### Ephemeral host worktrees

Most managed Git operations manipulate bare refs. Operations that genuinely require files create a unique detached worktree at:

```text
runtime/git-worktrees/<managed_repo_id>/<operation-id>/checkout
```

It starts from a pinned host-only ref. Hooks, LFS smudge, submodule initialization, inherited commit signing, credential prompts, and interactive prompts are disabled. A secret-free manifest and operation lock sit beside the checkout.

The worktree is scratch space, not an agent sandbox. Never execute agent-authored builds or tests there. Success, failure, cancellation, and normal termination remove the worktree, prune its registration, and compare-and-swap delete its operation ref. Startup and periodic maintenance reconcile abandoned worktrees only when their lock is free and they are older than the configured stale threshold, one hour by default.

### Persistent Team task-worktree cleanup

Persistent Team coding tasks use a different lifecycle from ephemeral host
operation worktrees. Resolution or reassignment creates a durable
<code>WorktreeCleanupCandidate</code>, and
<code>effect:team:wake-due-agents</code> reconciles it under the same
session/repository coordination used by preparation.

The node's <code>worktree_cleanup_dry_run</code> setting is tri-state:

| Value | Behavior |
|---|---|
| <code>true</code> | Run safety audits only; never attempt physical removal. |
| <code>false</code> | Permit the first eligible reconciliation pass to attempt physical removal immediately. |
| omitted | Run a dry safety pass first, mark the candidate <code>dry_run_safe</code>, then attempt physical removal on a later eligible pass after <code>worktree_cleanup_retry_seconds</code>. |

Every physical attempt revalidates Team references, retained commit evidence,
repository, path, branch, <code>HEAD</code>, and dirty-file state before and
under the execution backend's repository lock. A refusal remains visible as a
deferred or <code>recovery_required</code> candidate; managed-repository
maintenance and recursive deletion are not substitutes for this lifecycle.

## Administrator reference: artifact lifecycle

### Host Directory intake and recovery

Host Directories are defined in the directory-scoped `host-directories.json` domain. Each named
resource fixes an absolute non-root host directory, available capabilities, size/entry bounds,
delete approval, and enabled state. Session `host_directory_grants` select exact
`list`/`import`/`delete` capabilities. The dedicated MCP tools resolve every component relative to
an already-open directory descriptor; parent traversal, symlinks, special files, and
mount-boundary crossings are rejected. Agent responses never contain the configured root.
When an existing libvirt session is reconciled with new Host Directory grants, Evander adds the
dedicated tools implied by those capabilities while preserving its unrelated allowed tools.
The Admin health operation opens the root and, when `delete` is enabled, creates and removes a
private quarantine probe so an unwritable recovery area fails before agents receive access.

Listing returns relative metadata plus a revision. File import copies bounded bytes into a
session-owned communication artifact; directory import creates a deterministic `tar.gz`. That
handle still needs the normal, separately granted artifact-copy path before bytes enter the
workspace. Delete requires the listed revision and an explicit recursive flag for a non-empty
directory. By default it also requires one-use exact-payload approval. The host moves the entry
under `.evander-trash` and returns a recovery ID; Admin Web can restore it to an absent original
destination or permanently purge it. V1 has no automatic purge. Scope markers and directory
fsyncs make interrupted operations diagnosable: missing-payload or corrupt-metadata records remain
visible to the owning directory, cannot be restored, and can be explicitly purged.

`trigger:host-directory:file-added` uses the existing trigger worker. The first scan baselines
existing regular files; later files must keep the same identity, size, and modification time for
two polls and the configured settle interval. Directories, symlinks, and updates to a known file do
not emit. Replacing a known path is still an update unless a scan first observes it absent; a
delete observed before re-add creates a new event. Events carry the resource ID, relative path,
size, timestamps, and revision, never the root.

### Session artifacts

An agent stages output under `/artifacts` (or the configured `EVANDER_ARTIFACTS_DIR`). Paths must be relative, normalized, and free of empty, dot, or parent segments. Host-side readers open path components without following symlinks and require a regular file. These checks defend against a workspace symlink redirecting a supposedly safe artifact read outside its root.

Session artifacts are working output. They are not durable publication until a workflow or operator publishes them.

### Published artifacts

`effect:artifact:publish-artifacts` collects selected regular files, creates a `tar.gz`, and writes it to the host `PublishedArtifactStore` below `runtime/published-artifacts/`. A published key is immutable: trying to publish the same key again fails rather than replacing history.

Metadata includes the key, storage ID, archive filename and format, byte count, SHA-256, publication time, source session, execution backend, source file count/bytes, and caller metadata. The storage ID is derived from the key, while the recorded SHA-256 verifies the actual archive bytes. Import copies the stored archive into a target session and extracts only safe archive members below its artifact root.

For the telemetry retry work, Northstar Platform might publish:

```text
northstar-platform/mission-control/telemetry-retry/dispatch-8/test-report
```

That key always identifies that exact archive. A newer report receives a new key.

### Artifact spaces

An artifact space is a mutable, permissioned index over immutable published artifacts. Paths are normalized absolute logical paths such as `/northstar-platform/mission-control/releases`. Each logical `artifact_id` receives monotonically increasing versions that point to published artifact keys.

Space state in `runtime/artifact-spaces.json` carries a revision. Mutations can supply `expected_revision`; stale writers receive a conflict instead of overwriting another operator's change. Grants distinguish read, write, and manage subjects. Empty grants preserve the open/default behavior; once grants are populated, the caller must have the corresponding subject capability.

The split is intentional: the artifact blob remains immutable, while the space can answer “what is the latest accepted `mission-control` release report?” without changing any old archive.

## Administrator reference: request-scoped codebase consultations

Use `consult_codebase` when an agent needs a bounded answer about a repository snapshot but does not need to assign work to a persistent Team agent. The caller supplies a `managed_repo_id`, a question, and either a source ref, exact SHA, or a repository with a default branch. Evander resolves and records one exact commit before queueing the work.

The lifecycle is:

1. Authenticate the source session and validate the named consultation profile.
2. Resolve the managed repository inside the caller's directory scope and pin the exact commit under a consultation snapshot ref.
3. Insert a record in `runtime/agent-consultations.db`; an idempotency key is unique per source session and may not be reused for a different request.
4. Publish the durable `agent.consultation.requested` internal event. The initial MCP call may wait briefly, but the gateway transaction does not remain open for the entire model run.
5. The hidden system workflow claims capacity for the directory/profile, creates a `container_worker_vm` session, and grants it only `submit_codebase_consultation_result`.
6. Clone the managed repository read-only, detach at the pinned commit, write a one-time result token, and prompt the configured model with web search disabled.
7. Accept one structured result: answer, zero or more path/line evidence entries, confidence from 0 to 1, and explicit limitations. Evidence is validated against the pinned snapshot.
8. Finalize, cancel, fail, or time out; destroy the worker, revoke its Git access, release the snapshot pin, and only then release profile capacity.

Queue time counts against the consultation deadline. `max_concurrency` is per directory/profile. Fully cleaned terminal records older than `agent_consultation_retention_days` are compacted on new requests; records with incomplete cleanup remain for diagnosis.

The result token is bound to the worker session, stored only as a hash in the database, and consumed once. The signing secret comes from the host environment named by `agent_consultation_token_secret_env`. Never place the secret value in provider configuration.

## Administrator runbook

Register and inspect the Northstar Platform `mission-control` repository:

```bash
evander-cli git-repos register \
  --directory-path root/northstar-platform/mission-control \
  --repo-source /srv/repos/mission-control

evander-cli git-repos list --directory-path root/northstar-platform/mission-control
evander-cli git-repos show <managed-repo-id> --directory-path root/northstar-platform/mission-control
```

Set the identity for subsequent host-created consolidation commits on only that managed
repository:

```bash
evander-cli git-repos set-host-identity <managed-repo-id> \
  --directory-path root/northstar-platform/mission-control \
  --name "Release Automation" \
  --email "release-automation@example.com"
```

This repository-locked operation does not change directory defaults, other managed repositories,
agent checkout identities, or existing commits.

Normal agent reads refresh automatically. The restricted SSH wrapper validates the exact
repository, then asks `evander-managed-git-refresh.service` to fetch upstream before serving clone,
fetch, pull, `ls-remote`, or archive data. If the broker or upstream is unavailable, the read fails
instead of silently returning stale refs; the agent never receives upstream credentials.

Clone it into an existing VM session without exposing the host path:

```bash
evander-cli clone-repo-into-vm <vm-name> \
  --managed-repo-id <managed-repo-id> \
  --directory-path root/northstar-platform/mission-control \
  --session-id <session-id>
```

Run routine reconciliation and Git maintenance:

```bash
evander-cli git-repos maintenance
evander-cli git-repos maintenance --managed-repo-id <managed-repo-id>
```

Delete only after `git-repos show` confirms there are no issue, project, session, SSH, worktree, or migration bindings. There is no force-delete path.

For a consultation, verify that the gateway, trigger orchestrator, effect orchestrator, model relay, and container worker are healthy; confirm the caller has `consult_codebase` and `get_codebase_consultation` (plus `cancel_codebase_consultation` if needed); then request and poll by consultation ID. If it stalls, inspect in this order:

1. the row in `runtime/agent-consultations.db`;
2. the internal event and orchestration run;
3. container session/job status and logs;
4. the consultation snapshot ref in the managed repository;
5. cleanup status and error before attempting to release or delete anything manually.

For a legacy mirror deployment, do not improvise a partial conversion. Stop trigger/effect workers, run `evander-cli git-repos migrate --dry-run`, resolve every blocker, use `--apply`, execute the documented VM and container smoke path, and only then run `--finalize <migration-id>`. Apply and finalize are intentionally separate decisions.

## Source trail

- `src/evander/admin/services/managed_git_service.py`, `managed_git_runtime_service.py`, and `managed_git_worktree_service.py` — registry identity, ref transactions, restricted SSH integration, runtime grants, worktrees, publication, maintenance, and migration.
- `scripts/git_ssh_command_wrapper.sh`, `scripts/guest/push_agent.sh`, and `scripts/guest/sync_base.sh` — guest capability and helper enforcement.
- `src/evander/session_artifacts.py`, `src/evander/effects/providers/artifacts.py`, and `src/evander/published_artifacts.py` — safe artifact paths, publication, and immutable storage.
- `src/evander/artifact_spaces.py` and `src/evander/admin/services/artifact_service.py` — logical spaces, grants, versions, and revision checking.
- `src/evander/consultations.py`, `src/evander/code_consultation_service.py`, `src/evander/gateway/consultation_tools.py`, and `src/evander/effects/providers/code_consultation.py` — persisted consultations, one-time result submission, worker lifecycle, and cleanup behind the shared domain service.
- `docs/git-remotes.md` and `docs/operations.md` — supported managed-Git and consultation runbooks.
- `tests/test_managed_git_service.py`, `tests/test_managed_git_runtime_service.py`, `tests/test_managed_git_worktree_service.py`, `tests/test_managed_git_workflow_contract.py`, `tests/test_published_artifacts.py`, `tests/test_artifact_spaces.py`, and `tests/test_consultations.py` — current contract coverage.
