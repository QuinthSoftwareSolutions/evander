# 24. Team Git Promotion and Release Gates

> **At a glance.** Team Git moves reviewed, pinned commits through internal agent, review, feature, and release branches before one lease-protected canonical push. Release packages freeze accepted deliverables, verification evidence, waivers, reviewed change manifests, commands, reviewers, scope, and candidate SHA. Failed candidate verification or rejected release review requires changed reviewed content and a new release; retryable infrastructure or blocked-process failures create immutable successor attempts.

## The repository boundary

A Team project stores only a core <code>managed_repo_id</code>, directory scope, optional base branch, and canonical release prefix. The central managed-Git registry owns the upstream identity and host bare repository. Team commands never accept a trusted host checkout path as repository authority.

All Team branches before canonical publication live in that managed bare repository:

| Stage | Deterministic shape | Purpose |
|---|---|---|
| Ephemeral agent | <code>agents/&lt;session&gt;/&lt;assignment&gt;</code> | Disposable worker output |
| Review | <code>team/&lt;team&gt;/reviews/&lt;source-id&gt;</code> | Captured reviewed source |
| Feature | <code>team/&lt;team&gt;/features/&lt;task&gt;-&lt;agent&gt;</code> | Consolidated accepted task work |
| Internal release | <code>team/&lt;team&gt;/releases/&lt;release&gt;</code> | Consolidated candidate |
| Canonical | default <code>releases/&lt;project&gt;/&lt;release&gt;</code> | The only Team branch pushed upstream |

Branch validation rejects whitespace, control characters, colon, a leading slash, <code>..</code>, and a leading <code>refs/</code>. Operators may configure the canonical prefix, but it remains a validated plain branch name.

## Intent, effect, and evidence

Every promotion is a durable <code>GitPromotionRecord</code>. It records promotion kind, source refs and pinned SHAs, expected commit, target branch, state, output, and error.

The flow has three steps:

1. An authorized MCP/Admin command records promotion intent and durable outbox event.
2. The workflow pack invokes the relevant managed-Git effect.
3. <code>effect:team:record-git-promotion</code> attaches the actual success/failure and updates related Team evidence.

The command's success means intent was recorded. The promotion record and effect output say whether Git succeeded.

Managed-Git effects use operation-scoped worktrees and temporary refs. Target updates are compare-and-swap operations. Conflicts fail; Evander never auto-resolves them. Temporary resources are removed on success and failure.

## The promotion ladder

### Agent or ephemeral output → review

<code>team_publish_review_branch</code> captures a persistent deliverable's validated agent branch/commit or an accepted ephemeral assignment branch into a deterministic review ref. The actor must be the producer/parent, an authorized superior, project lead, or manager as applicable.

### Review → feature

<code>team_update_feature_branch</code> consolidates accepted current deliverables for one task. Each source must match the commit actually accepted by review. The resulting reviewed manifest freezes:

- base and result tree;
- included commits;
- changed paths;
- source deliverable/review evidence.

### Feature → internal release

<code>team_request_release</code> selects accepted deliverables from one project. If needed, prerequisite feature promotions are created first. Release consolidation merges pinned feature commits into the internal release branch and freezes the candidate SHA and tree.

### Internal release → canonical

Only after every gate passes does the workflow create the canonical-push promotion. Core <code>effect:git:push-branch</code> performs expected-commit and destination-lease preflight, then pushes the approved release commit to the canonical branch. <code>effect:team:mark-release-published</code> records canonical branch, published commit, timestamp, and accepted package state.

The internal release branch is not the published branch operators should test or advertise. The release package's <code>canonical_branch</code> and <code>published_commit_sha</code> are the publication truth.

![Pinned Team Git promotion and release-gate flow](../../assets/evander-user-manual/diagrams/16-team-release.svg)

<p class="figure-caption">Figure 24.1 — Reviewed commits move through internal refs and immutable gates before the sole canonical push.</p>

## Creating a release package

The caller must be:

- the project lead;
- the top-level Team manager; or
- an agent whose same-project role grants <code>request_release</code>.

All selected deliverables must be accepted and belong to one project. Artifact-only releases are allowed without Git configuration. A Git release requires project Git configuration and an explicit release policy unless <code>allow_ungated=true</code> was deliberately configured.

The release package freezes a typed manifest containing:

- deliverable IDs, verification status, evidence refs, and waiver records;
- accepted immutable deliverable reviews;
- reviewed change manifests;
- candidate commit and tree;
- scope-validation result;
- ordered verification commands;
- release-review requirements;
- actor and timestamp evidence.

This prevents policy drift after request time from silently redefining what was approved.

## Verification evidence and waivers

Deliverable verification is enforced at release time:

| Status | Release rule |
|---|---|
| <code>passed</code> | Eligible without a verification waiver. |
| <code>failed</code> | Cannot be waived. Fix, re-review, and submit new evidence. |
| missing, <code>partial</code>, <code>not_run</code>, or <code>unknown</code> | Requires a reasoned waiver from project lead, Team manager, or authenticated operator. |

A role with <code>request_release</code> alone cannot grant the waiver. Each waiver records deliverable, original status, reason, actor kind and ID, and timestamp.

A waiver acknowledges incomplete verification evidence; it does not waive reviewed-scope mismatch.

## Release policy

<code>Project.release_policy</code> selects:

- <code>container_worker_vm</code> verification backend;
- optional container image;
- ordered commands, each with name, working directory, argv, and timeout;
- required reviewer/profile pairs;
- or explicit <code>allow_ungated=true</code>.

Without <code>allow_ungated</code>, at least one command or review requirement is required.

The verifier creates a disposable worker session, clones by <code>managed_repo_id</code>, checks out the exact candidate SHA, runs commands in order, records bounded results, and destroys the session. Release reviewers decide that same candidate SHA under their profiles.

## Scope validation

Feature promotion records the exact reviewed commits and changed paths. Release consolidation computes the candidate's changes and compares them with the union of reviewed manifests.

An extra commit or path outside that union is a hard blocker. It cannot be waived. This catches accidental merge content and deliberate attempts to smuggle unreviewed scope into the candidate.

For Northstar, if the reviewed telemetry change covers <code>src/telemetry/retry.py</code> and its tests but the release candidate also changes an authentication policy file, the release stops even if all tests pass.

## Gate outcomes and retries

Candidate identity never changes within one release package.

### Terminal candidate failures

A verification command returning nonzero means the candidate failed. The release is rejected. A release-review decision of <code>rejected</code> also means the candidate is defective. Repair the content, create and accept new deliverables, and request a new release with a new reviewed tree.

The system refuses a cosmetic new release of the same failed deliverables and tree.

### Retryable process failures

Verification timeout or infrastructure failure can be retryable. A release review recorded <code>blocked</code> with a concrete process/evidence blocker can also be retried. <code>team_retry_release_gate</code> creates a linked immutable successor attempt against the same candidate SHA with reason and requester identity. It never edits the old run or review.

Use <code>blocked</code> only when the review process cannot complete. Use <code>rejected</code> for an actual candidate defect.

### Git recovery

<code>team_recover_release</code> is different. It recovers failed prerequisite feature or internal release Git promotions. It is not a verification or review-gate retry mechanism.

Legacy nonterminal packages without current immutable candidate evidence are rejected with <code>legacy_unpinned_release</code> and must be recreated. Historical terminal records remain readable.

## Northstar release trace

1. Rowan accepts Imani's current telemetry deliverable and pinned commit.
2. Priya promotes its review evidence into the task feature branch.
3. Riley requests release <code>telemetry-retry-2026-07</code>.
4. The package freezes deliverables, reviews, reviewed paths, commands, required release reviewers, candidate SHA, and tree.
5. A disposable verification worker checks out that SHA and runs unit and integration commands.
6. Scope validation confirms no unreviewed paths entered consolidation.
7. Required release reviews accept the same SHA.
8. Canonical push publishes it to the configured <code>releases/mission-control/...</code> branch under a destination lease.
9. The accepted release package records the canonical branch and published SHA.

If integration verification times out, Riley may request an audited retry against the same SHA. If it exits nonzero, Imani must change and re-review the content; Riley cannot retry it into approval.

## Failure patterns

| Failure | Correct interpretation |
|---|---|
| Source branch moved after review | Promotion uses the pinned reviewed commit if it remains contained; a moving tip is not silently substituted. |
| Merge conflict | Deterministic consolidation failed; Evander does not auto-resolve. Create reviewed conflict-resolution content. |
| Compare-and-swap target mismatch | Another operation advanced the target. Re-read promotion truth and decide whether a new intent is valid. |
| Missing release policy | Git publication is blocked unless explicitly ungated. Configure policy; do not bypass the gate. |
| Unreviewed changed path | Hard scope failure, never waiver-eligible. Rebuild from reviewed content. |
| Verification command nonzero | Terminal candidate defect. New content and release required. |
| Verification infrastructure error | Potentially retryable immutable successor run. |
| Release review rejected | Terminal candidate defect. |
| Release review blocked | Retryable only with a concrete process/evidence blocker. |
| Release package says accepted but upstream seems different | Compare canonical branch/published SHA, promotion record, core push effect, and remote ref. Do not inspect only the internal release branch. |

## Practical runbook

Before release:

1. Confirm every selected deliverable is current, accepted, and in one project.
2. Verify each Git source resolves to the pinned reviewed commit.
3. Inspect reviewed manifests and expected path union.
4. Resolve incomplete verification with an authorized, reasoned waiver or new evidence.
5. Validate the project release policy and worker image/commands/reviewers.

During release:

1. Track release package, prerequisite promotions, candidate SHA/tree, verification run, release reviews, and scope validation.
2. Treat outbox acceptance and effect completion as separate facts.
3. Retry only typed retryable failures through <code>team_retry_release_gate</code>.
4. Use <code>team_recover_release</code> only for eligible Git promotion failure.

After release:

1. Read <code>canonical_branch</code>, <code>published_commit_sha</code>, and <code>published_at</code> from the package.
2. Match them to the successful canonical-push promotion and core Git effect.
3. Verify the remote ref equals the approved SHA.
4. Preserve the immutable manifest and all gate attempts as release evidence.

## Source trail

- <code>src/evander_team_addon/git_naming.py</code> — deterministic internal and canonical branch names and validation.
- <code>src/evander_team_addon/services/commands/git_release.py</code> and <code>services/git_release_service.py</code> — promotion/release authority, intent, recovery, and state transitions.
- <code>src/evander_team_addon/services/domain/git_promotion.py</code>, <code>release_packages.py</code>, and <code>release_gates.py</code> — pinned sources, manifests, scope, verification, reviews, and retries.
- <code>src/evander_team_addon/providers.py</code> — publish, consolidate, verify, record, and mark-published effects.
- <code>src/evander_team_addon/workflow_packs.py</code> — review, feature, release, verification, and canonical-push routes.
- <code>src/evander/admin/services/managed_git_service.py</code> and core Git effects — managed bare repositories, operation worktrees, leases, compare-and-swap, and remote push.
- <code>docs/addons.md</code> and <code>docs/git-remotes.md</code> — Team release and core managed-Git contracts.
- <code>tests/test_team_addon_git_effects.py</code>, <code>tests/test_team_addon_mcp.py</code>, and <code>tests/test_team_addon_runtime.py</code> — promotion, scope, gates, retry, recovery, and publication behavior.
