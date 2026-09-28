# Autonomous Git recovery

Team agents can recover typed Git incidents from a blocker dispatch using
`team_preview_git_recovery` and `team_apply_git_recovery`. Admin Web exposes the
same preview and apply operation on a blocker. Applying a preview requires its
token and a stable request ID; a changed source, policy, owner, or state requires
a fresh preview. Replay does not create another task or spend another attempt.

## Recovery decisions

| Diagnosed cause | Recovery |
| --- | --- |
| Missing workspace, stale receipt, or interrupted transport | Verify the original base and exact accepted sources, then prepare a distinct physical attempt. |
| Dirty task workspace | Capture the original index and working content as unapproved, immutable Git evidence; create replacement implementation work for review. |
| Incompatible contribution bases | Renew an ordinary snapshot on the same configured branch when policy permits; otherwise reimplement the requirements on the fixed baseline. |
| Missing reviewed object, moved source ref, contradictory provenance | Create reviewed replacement work when the original baseline is authoritative. Acceptance never transfers to reconstructed code. |
| Composition conflict | Assign the existing dependency-composition recovery, verify its reviewed resolution, and prepare the original task in a distinct worktree. |
| Publication destination advanced | Refresh the configured branch, prove that it contains the observed destination, and use the existing release-reintegration service to create a freshly gated candidate from the exact accepted sources. |
| Unknown failure or explicit policy conflict | Retain the evidence and report the precise missing prerequisite; do not guess a reset, merge, or replacement. |

Source consistency and baseline compatibility are checked before reference-repository
preparation for pinned tasks, again before composition, and on submission. Recovery
uses the ordinary source verifier, including immutable review refs. Finding a Git
object by SHA alone does not prove that it is the accepted source.

Blocker dispatches retain their narrow scope. The recovery tools authorize only the
recorded incident. Successor decisions route internally to the active configured
project lead; they do not grant general task creation, project, repository, or
publication authority. A missing eligible lead remains an explicit prerequisite.

## Baseline intent and limits

Project Git configuration includes:

```json
{
  "recovery": {
    "enabled": true,
    "automatic_same_branch_renewal": true,
    "preparation_retry_limit": 3,
    "candidate_generation_limit": 3
  }
}
```

Each limit accepts 0–10 and is charged durably across the work lineage. Preparation
retries change physical attempt identity without changing the logical task or
accepted source set. Candidate-changing attempts create new review work. Rejected
or cancelled recovery work still consumes its generation. Repeating request IDs,
changing error wording, restarting services, or moving to a recorded successor
does not replenish the budget.

Task creation accepts `git_constraints` through the agent tools, batch creation,
and Admin API:

```json
{
  "intent": "required",
  "required_base_commit_sha": "1111111111111111111111111111111111111111",
  "excluded_ancestor_shas": ["2222222222222222222222222222222222222222"]
}
```

Use `snapshot` for a renewable snapshot of the selected workstream. Explicitly
supplied base commits default to `required`; automatically selected bases default
to `snapshot`. Children inherit their parent's constraints and may not relax a
required or unknown intent. Exclusions accumulate. Existing records default to
`unknown`, so upgrade does not silently reinterpret an old pin. Constraints are
immutable after task creation. Required commits and excluded ancestry are enforced
on preparation, recovery, and candidate submission.
Legacy preparation identities survive the upgrade. Editing recovery policy does
not recreate checkouts or silently clear historical preparation blockers.
When setting `required_base_commit_sha`, supply the same full object ID in the
task's `git_base_commit_sha` and its concrete `git_base_branch`.

## Preservation and completion

Recovery records hold prior preparation attempts and Git contexts against cleanup
and control-plane session or VM teardown. A successful replacement does not release
those holds: it is not proof that every original file was archived. The retained
data must remain available for a separately evidenced archival/disposition operation.
There is no automatic deletion of retained recovery work in this feature.

Salvage publishes Git refs only in the original session's `agents/<session>/...`
namespace. It preserves staged content separately from working content, including
binary and untracked, non-ignored files. Ignored data stays in the original held
workspace. Unresolved indexes, submodules, non-regular files, unstable captures,
more than 20,000 files, or more than 512 MiB require narrower salvage; no partial
snapshot is treated as complete. No guest clean filter or hook is run by salvage.
Code is transported through Git, never through MCP bodies.

Replacement tasks retain the original requirements, case, authorship, work lineage,
and constraints. Their review and verification gates run normally. After acceptance,
the continuation checks policy and affected consumers again, then atomically retires
eligible predecessor work, rebinds ordinary consumers, creates fresh integration
boundaries for submitted consumers, and wakes affected agents. Submitted and accepted
history remains unchanged. Active reviews, executing consumers, unresolved descendants,
locked policies, or changed consumers defer adoption without rolling back acceptance.
The incident preview exposes that reason and can reauthorize continuation after the
prerequisite changes. The runtime also reconciles deferred continuations.

An exact-source retry is complete only after a verified Git context is activated.
A release recovery is complete only when its successor actually publishes. Merely
resolving the preparation blocker or creating a successor is not recovery success.

## Deployment and diagnosis

1. Back up Team state and deploy the worker before enabling new recovery actions on
   the control plane. Worker `/healthz` advertises `git_recovery: 1` and `git_salvage: 1`.
2. A recovery that needs these capabilities refuses an older worker; it never drops
   the preparation-attempt identity to get past an older request schema. Ordinary
   existing worker compatibility remains supported.
3. Libvirt uses the current host-generated preparation and salvage scripts. Its
   retained checkout and Git transport must match the host-owned session record.
4. Start with one project and inspect cause, base, composed start, candidate, observed
   destination, retry counts, retained work, and actual resumption in Admin Web.
   The operations view summarizes counts and mean recovery time for its loaded history.
5. Disable `git.recovery.enabled` to stop new actions. Existing source pins, review
   decisions, preservation holds, and audit evidence remain intact. State changes
   during review must be revalidated before adoption.

There is no bulk replay of historical incidents and no publication queue in this
change. Normal destination leases, release gates, and Git namespace checks remain
authoritative. See also [managed Git](git-remotes.md) and
[release recovery](release-recovery-and-verifier-policy.md).
