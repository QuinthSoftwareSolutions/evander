# Release recovery and verifier policy

## Boundaries

The platform owns safe execution, immutable provenance, concurrency, retention,
and permission checks. A project's release policy owns command interpretation,
baseline exceptions, integration ownership/instructions, and source retirement.
No framework, Team name, branch name, or known product failure is recognized by
the platform. Existing review, release, publication and Git lease gates remain in place.

Customer-specific evaluators, policies, tests, incident notes and evidence belong
in operator-owned storage outside this repository, not even in ignored directories.
Checked-in examples and regression fixtures must use synthetic identities and
exercise generic contracts; customer behavior remains project/workflow configuration.

## Reintegrating accepted work

Open the source release in Admin Web and choose **Reintegrate onto updated base**.
Alternatively, use **Reintegrate accepted work** on its project-scoped case; if
multiple current releases qualify, choose by their contribution titles and dates.
The preview refreshes its managed repository and shows the project's configured
base branch and exact commit, accepted contribution titles, eligible owners, and
any proposed retirement. It does not ask the operator to enter task IDs.

Choose the owner if no default is available, then **Create integration task**.
The submitted preview must still match the current Team revision, contribution
provenance, configured policy and resolved base. A conflict requires a fresh
preview; it never silently repins. A network retry uses the same request ID and
returns the existing task. The task keeps the release's work lineage, authorship,
work-item attribution and (when shared by all contributions) case association.
It pins accepted deliverable IDs as well as the base. Superseded source inputs
block preparation rather than silently selecting different contributions.

Normal dependency composition and its typed merge-conflict recovery are used.
Both reintegration and operator dependency-recovery requests persist their wake
intent transactionally and immediately request outbox publication.

Project `release_policy.reintegration` configuration:

```json
{
  "enabled": true,
  "owner_agent_id": null,
  "instructions": "Merge the pinned accepted contributions onto the pinned base, verify, and submit an integration deliverable.",
  "retire_source_release": false
}
```

The default owner is the configured project lead, or the sole eligible project
agent when no lead is eligible. Explicit owners must be active assigned project
agents. Retirement is opt-in and uses the existing release cancellation rules;
published releases and unrelated correction work are never cancelled by it.
The same operation is available through `team_reintegrate_release` (preview then
submit), restricted to the configured lead in an active project-scoped dispatch.
The HTTP endpoint is `POST /api/addons/team-addon/release-packages/{id}/reintegration`;
it requires directory-owner permission and privileged operations unlocked.

## Restart-safe execution

The frozen command sequence is executed in a workspace generation. If the
orchestrator resumes an interrupted sequence, it destroys the old session,
allocates a new generation/session, and reruns **every** prerequisite command.
Passed commands from an incomplete workspace cannot skip installation or build
steps in a cleaned checkout. Previous command evidence remains in generation
history. Fully persisted terminal evidence is reused, with cleanup reconciled.
An already-deleted verifier counts as cleaned up; other deletion errors still block
the release.
Per-run thread coalescing and a cross-process file lease prevent overlapping
execution; generation checks reject stale progress and final results.

`verification_max_generations` defaults to 3 (range 1–10), frozen into the
candidate manifest. Exhaustion or cleanup failure blocks on infrastructure,
not candidate correction. A deliberate operator retry is a separate verification
attempt under the existing retry API; changing live policy does not rewrite a
previously frozen manifest. The UI shows generation history and last recorded
progress; a running state alone is not evidence that commands are progressing.

## Structured evidence (opt-in)

Existing commands default to `result_protocol: "exit_code"`, no baseline
exceptions, and `command_failure_action: "correction"` for compatibility.
Use `command_failure_action: "block"` when a failed command should stop for
assessment instead of automatically opening a candidate correction task.
After repairing the prerequisite, an operator can use **Retry verification
gate** to create another attempt for the same frozen candidate. This retry requires
privileged operations unlocked and a reason; agents cannot retry command-blocked gates.

For `result_protocol: "json-v1"`, the platform supplies
`EVANDER_VERIFICATION_CANDIDATE_SHA` and `EVANDER_VERIFICATION_BASE_SHA` from the
frozen manifest. The configured evaluator must emit exactly one stdout line:

```text
EVANDER_VERIFICATION_RESULT={"schema_version":1,"outcome":"baseline_exception","explanation":"Matching pinned baseline failures; not a clean build","candidate_commit_sha":"<exact candidate SHA>","base_commit_sha":"<exact frozen base SHA>","artifact_refs":["/artifacts/comparison.json"]}
```

Outcomes are `passed`, `baseline_exception`, `failed`, or `infrastructure`.
The evaluator exits zero when it successfully determines an outcome, including
a negative policy decision. A nonzero evaluator exit, timeout or worker failure
cannot be overridden by a positive report. Missing, duplicate, malformed or
mismatched reports fail closed as infrastructure. Baseline exceptions additionally
require `allow_baseline_exceptions: true`, the frozen base SHA, and evidence refs.
Execution status/exit code and policy outcome are stored separately. The UI labels
an accepted exception distinctly from a clean command pass.

Reports are bounded to 65,536 characters. `/artifacts/` references must be readable text
inside that session's artifact root (no traversal); up to 5 MiB total is copied
into the retained command log before session cleanup. Missing local evidence
blocks the result. Each read passes the remaining byte allowance to the worker,
which bounds file reads before decoding; the client also bounds the streamed response.
Other references are opaque references to evaluator-owned
durable storage, not URLs for the host to fetch. The platform validates identity
and transport, not the scientific adequacy of a project's comparison algorithm.

## Retained diagnostics

Command output, structured decisions and referenced local text evidence are
retained before cleanup under `runtime/release-verification-logs`, addressed by
the job ID and owner-readable through the release UI or `execution logs` CLI.
Interrupted-generation jobs remain addressable. Retention defaults to 30 days,
configurable as `verification_log_retention_days` (1–365), frozen per manifest.
Reads enforce expiry; physical pruning occurs at subsequent verifier starts.
Each log has a 10 MiB text cap with an explicit truncation marker, restrictive
filesystem permissions, gzip storage and atomic replacement.

Conventional credential forms and known host environment secret values are
redacted before persistence. This is best-effort redaction, not a guarantee that
arbitrary application data contains no secrets. Do not print credentials or put
secrets in evaluator argv. Worker/toolchain mount identities are stored with the
command result. Retention is not a complete live heartbeat or an archival backup;
an orchestrator crash before output capture can still lose that command's tail.

## Immutable worker SDK rollout

The .NET Ansible role stages SDK and tools together in a fresh bundle, validates
host and runner-container execution, then atomically replaces the `dotnet-current`
symlink. Identical selected archive inputs reuse the validated bundle. Existing
bundles are not removed. `evander_worker_dotnet_bundle_selector` configures the
selector; the SDK/tools source paths must be its `sdk` and `tools` children.
When `evander_worker_dotnet_toolchain_enabled` is false, default SDK/tools mounts
are omitted so container jobs cannot create directories at the future selector path.

The worker pins resolved read-only mount sources in a host-only session snapshot.
All jobs in that session keep those paths across selector switches and daemon
restarts. Shared selector ancestors are resolved once to avoid mixed SDK/tools
versions. Removed configuration grants are not reintroduced by the snapshot.
A removed pinned bundle fails closed instead of mounting an empty replacement.

Deploy worker pinning **before** switching toolchain selectors. Drain existing
sessions that have no snapshot: their original toolchain cannot be reconstructed
after a legacy mutable directory has already changed. Keep legacy directories
and old bundles until no session refers to them. Rollback selects a previously
validated bundle atomically; never replace its contents in place. Bundle garbage
collection is deliberately manual and requires a reference/impact check.

## Operator rollout

Rollout checklist (not executed by this branch):

1. Review and merge the branch; deploy compatible control-plane/worker code with
   active-session drain/pinning precautions above. Preserve existing evaluator
   configuration until any replacement has passed a canary.
2. Prepare any project-specific evaluators and reviewable command fragments in
   operator-owned storage outside this repository. Use the frozen candidate and
   base identities supplied by the protocol; do not infer the base from Git parents.
   Ensure the configured execution environment can access the evaluator without
   assuming an operator-local file path exists in the verifier session.
3. Read the latest project policy/revision through Admin Web or the supported
   service. Replace only the intended verification commands; retain all other
   verification commands, review requirements, gate order and publication rules.
   Save with `expected_revision`, then reread. Do not replace the whole policy with
   a command fragment, edit live pack files, or modify SQLite.
4. Run a fresh frozen canary: clean pass, matching baseline exception, candidate-only
   failure, interrupted prerequisite sequence, unreadable evidence and dependency
   retrieval failure. Include exception scenarios only when that policy is enabled.
   Confirm classification, preserved lineage and logs after session cleanup.
5. Roll back future policy selection if needed; keep existing immutable evidence
   and never mark baseline-exception acceptance as a clean build. Do not downgrade
   readers until snapshots containing the new fields have been accounted for.

This implementation changes no live release, project policy or workflow pack.
