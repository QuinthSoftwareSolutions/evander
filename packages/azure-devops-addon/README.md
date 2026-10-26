# Azure DevOps add-on

Optional Azure DevOps work items, PR feedback and publication, builds, personal
connections, and a guided issue-to-merge workflow. The core Evander distribution
does not install this project. Azure OpenAI and Azure infrastructure provisioning
are separate capabilities and are not extracted by this package.

## Install and enable

In the same Python environment as Evander, install the package explicitly:

```sh
uv pip install -e packages/azure-devops-addon
```

Enable **Azure DevOps** in Admin Web's add-on settings. Installations with an
explicit `enabled_addon_ids` list must include `azure-devops`. Restart the gateway,
Admin Web, and workflow orchestrators after changing enabled integrations. The
add-on's restart guidance also lists `evander-ado-worker` and
`evander-proxy-issue-sync`. Their existing unit names and command-line interfaces
are preserved. The systemd installer discovers these units only when this package
is installed and enabled. Standalone workers exit successfully when disabled.

Existing directory provider settings, `AZURE_DEVOPS_PAT` and
`AZURE_DEVOPS_PR_PAT` references, personal connection storage, work-item proxy
records, PR maps, trigger cursors, and legacy workflow node IDs remain valid.
Credentials stay on the host. A missing integration does not prevent core startup;
explicit Azure DevOps operations report how to install or enable it.

The existing ADO Activity add-on declares a dependency on this integration. It
remains separately configured; its extraction is not part of this change.

## Find a build by number

With Evander 0.124.0 or later, grant `list_ado_builds` explicitly to allow read-only build
discovery. Upgrading does not grant it to existing agents. The tool uses the session
directory's configured organization, project and host-side `azure_devops_pat_env` credential.

For example, call `list_ado_builds` with `build_number="release-42"`. Optional `pipeline_id`,
`repository_id` and `branch` filters narrow the search; `pipeline_id` is a positive build
definition ID and `branch` is a full ref such as `refs/heads/main`. Build numbers match exactly
unless a trailing `*` requests a prefix search. Omitting filters lists recent builds.

The response contains `project`, `builds`, `count` and `continuation_token`. Results are newest
queued first, with a default `limit` of 25 and a maximum of 100 per call. Pass the opaque token
unchanged with the same filters to retrieve the next page, until the token is null. Each match
includes its `run_id`, `build_number`, `run_url`, source branch/commit, timestamps and status,
plus pipeline and repository IDs/names when available. Multiple pipelines may share a build
number, so the tool returns all matches on each page without choosing a build for the caller.

After resolving the intended match, pass its `run_id` as `build_id` to `get_ado_build` or
`list_ado_build_work_items`. These tools require their own grants. Build associations alone
do not establish which build was deployed. See the [build read contract](../../docs/operations.md#host-side-azure-devops-build-read-tools).

## Diagnose a failed build

With Evander 0.133.0 or later, grant `get_ado_build_timeline` and `get_ado_build_log`
independently. Both are read-only, use the session directory's build credential and project,
and accept positive build IDs. No connector or guest-held credential is needed.

1. Use `get_ado_build(build_id=42)` to confirm the result, source version and branch.
2. Call `get_ado_build_timeline(build_id=42)`. Follow task `parent_id` values to locate the
   failed job/stage, inspect `issues`, and select its `log_id`. Follow `next_skip` for more
   records; pass a `details_timeline_id` as `timeline_id` to inspect a nested timeline.
3. Call `get_ado_build_log(build_id=42, log_id=7)`. This returns the last 200 lines by default.
   Set `start_line=0, max_lines=100` for a forward excerpt. Offsets are zero-based and
   `end_line` is exclusive. `next_start_line` identifies the next forward excerpt.

Timeline pages default to 200 records and allow at most 1,000; upstream timeline and log-index
JSON is bounded to 2 MiB. Log excerpts allow at most 1,000 lines and 64 KiB of UTF-8 text.
The response includes `total_lines`, `returned_lines`, `truncated`, `byte_truncated` and
`partial_last_line`. If the byte cap cuts a long line, `next_start_line` advances past that
line; its omitted remainder cannot be retrieved through line paging. Empty logs return empty
text. Unavailable logs and provider failures return errors without upstream error bodies.

Diagnostic contents are untrusted build output. Host authentication reflected by the provider
is rejected; audit summaries contain identifiers, counts and truncation information rather
than log text or issue messages. Reading a failed build does not authorize code changes.
Continue following the human's instructions for the PR, including any earlier authorization
to fix build failures, and check the current PR revision before acting.

### Feed build results to an existing PR agent

An operator workflow can connect `trigger:ci:run:completed` (`provider: ado_pipelines`)
through session lookup and renewal to `effect:agent:enqueue-prompt`. Use one watcher per
project and one shared handler. Set `branches: []`, `top: 100` and
`include_existing: false` to watch future completions; PR builds use
`refs/pull/<id>/merge`. Filter out events without `pull_requests[].number`, then match
the existing agent by organization scope, project, repository, PR ID and the intake
workflow's ownership marker. Never select arbitrarily between duplicate bindings.

The prompt should carry the build and pipeline IDs, raw and normalized result, tested
merge SHA, and build URL. Read the PR's current merge SHA to mark an older result as
superseded; pass unknown context explicitly when it cannot be established. Preserve
conversation continuity and use distinct, stable build/session dispatch and queue keys
so a busy agent retains results from different builds. Bound preparation retries with
scheduled internal events. Grant diagnostic tools to existing sessions through the
supported policy service and to future agents in every creation branch.

Deliver every outcome, with failures investigated through the native tools. By default,
report problems and actionable cancellations on the PR, and keep successes quiet unless
they resolve a reported problem. Honor earlier human instructions, including advance
authorization to fix failures, and recheck the current revision before making changes.
The current ADO completion trigger treats a build ID as one run; rerunning jobs inside
the same build ID does not produce a second notification. Operator workflow files and
customer-specific mappings belong outside this package and must use revision-checked saves.

`query:ado:pr-build-context` provides the deterministic eligibility, revision and deadline
checks for this flow. Pass `query:pull-request:get`'s `raw` output as `pull_request`, request
`include_labels: true` on that PR query, and supply `tested_commit`, configured `intake_tags`
and `reviewer_ids`. ADO's main PR response does not include labels; the opt-in label read
preserves the existing default query behavior. The context query itself performs no I/O.

Use `intake_eligible` before retrying a missing agent; already associated agents do not need
another tag or reviewer assignment. The first context check returns a 15-minute
`preparation_deadline`. Carry that exact value through every scheduled retry and pass it
back to the query. Continue only while `within_preparation_window` is true, and schedule
the next check using `retry_delay_seconds` (at most 60 seconds, shortened at the deadline).
A delayed or restarted worker cannot extend the deadline. `revision_context` is `current`,
`superseded` or `unknown`, comparing the tested merge with `lastMergeCommit.commitId`.

## Cast a PR review vote

Grant `submit_pr_review` and bind the target provider, repository, and PR in the session's
`metadata.pr_feedback_streams`. Once the directory's PR credential identity is assigned as
an optional or required reviewer, the agent can submit a summary with `event=APPROVE`,
`APPROVE_WITH_SUGGESTIONS`, `WAITING_FOR_AUTHOR`, or `RESET`. `REQUEST_CHANGES` is an alias
for Waiting for author. Reject is unsupported. Omitted or `COMMENT` events publish comments
without changing a vote. The host resolves its own reviewer identity, publishes the comments,
then casts and confirms that identity's vote; agents never receive the credential or choose
another reviewer. See the [review workflow contract](../../docs/operations.md#review-request-workflow)
for response fields, permissions, and retry behavior.

For draft PRs, a voting submission publishes the review comments without changing the vote.
The successful response reports `event=COMMENT`, the original `requested_event`,
`vote_deferred=true` and `vote_deferred_reason=draft_pull_request`; no `vote` is claimed.
Retries with the same idempotency key reuse that publication, including after the PR is
published. Recheck the source commit and submit a new review with a new key before voting
after publication; draft votes are not replayed automatically.

## Deliver one issue

1. Configure the organization's name, project, work-item/build credential and PR
   credential in the selected directory. Readiness checks inspect configuration
   only; they do not call Azure DevOps or decrypt personal credentials.
2. Register an Azure DevOps repository under **Git Repositories** and clone it into
   an existing agent with the desired model and toolchain. Grant writable managed-Git
   access and the `submit_git_changes` MCP capability. The starter validates these
   existing permissions without broadening them. Submission requires Evander 0.94.0
   or later and includes the complete Markdown summary.
3. Open **Workflow starters**, choose **Deliver an Azure DevOps issue**, select that
   repository and agent, and enter a work-item number, base branch, and existing CI
   pipeline number. Set the reviewer and repair limits.
4. Preview and activate. **Run task once now** starts the selected issue. Repeated
   clicks use the same task identity, rather than assigning the issue again.
5. Review the ordinary workflow run report for progress and blockers. The workflow
   watches only `agents/<session>/<workflow-name>`, pins the pushed SHA, publishes
   a PR, and queues the selected pipeline. A one-minute workflow schedule checks
   the exact build, review votes, open review threads, and blocking branch policies.
6. Approve the pending merge in Evander's existing **Approvals** view. The merge
   step rechecks the current PR and consumes the approval for its exact payload.
   It never bypasses Azure DevOps branch policies. Verification requires a completed
   PR and the returned merge commit at the target branch tip.

The generated graph remains fully editable. Intake, agent work, Git publication,
CI, status checks, repair budgeting, approval, merge, and verification are separate
nodes. Workflow state uses the existing directory/graph-scoped state store; there
is no additional delivery daemon. The PR worker is needed by existing event-based
PR feedback/completion workflows; this starter checks its selected PR itself.
Its independently triggered stages fit the default worker execution-depth limit.

A queued pipeline or accepted merge request is not success. A changed source
commit, missing or mismatched build, missing reviewer, unresolved thread, failed
policy, denied/expired approval, or exhausted repair budget remains visible in
node outputs. After a timeout during merge, inspect the PR and run report before
retrying; a completed merge is read back without submitting a second merge.

Repair requests are deduplicated by commit. Their budget persists across new
commits; reinstalling a starter under a different name creates a separate workflow.
The selected pipeline must validate the repository's published source commit;
builds for another SHA or repository never satisfy the check. Existing server-side
policies remain responsible for any additional merge-build and review-reset rules.

See [examples/issue-to-merge.json](examples/issue-to-merge.json) for form values.
An add-on upgrade never rewrites an activated workflow.

## Let a pipeline wait for an agent

Use an agentless `InvokeRESTAPI@1` job with `waitForCompletion: true` after
deployment. The [pipeline example](examples/pipeline-task.yml) sends a non-secret
environment handoff and task identifiers to Evander's existing workflow-event API.
Replace its `DeployEnvironment` dependency with your deployment job's name so the
event is sent only after deployment succeeds.
Configure a generic service connection for the Evander HTTPS origin and a secret
pipeline variable `EVANDER_WORKFLOW_EVENT_TOKEN`. Configure that token on Admin Web
as `EVANDER_WORKFLOW_EVENT_MACHINE_TOKENS=example-ci:<token>`. The service connection
needs no username/password; the task supplies the bearer header. Its explicit
headers replace Azure's defaults, so `System.AccessToken` is never forwarded.

The request uses `event_type: ci.agent_test.requested`; the workflow subscribes to
`external.ci.agent_test.requested`. Validate the event's machine `source_name`,
pipeline ID and environment contract before starting work. Event ingress supplies
the trusted source identity separately from the request payload.

The add-on provides two host-side effects:

- `effect:ado:task-log-sync` takes `task`, `session_id`, `claim_start` (default
  false), and `timeout_seconds` (default and maximum 7200). `task` contains positive
  `pipeline_id`/`run_id` and UUID `project_id`, `plan_id`, `job_id`, `timeline_id`,
  `task_id`. It validates ownership against Azure, forwards rendered agent progress,
  and returns `done`, `start_required`, `retry_delay_seconds`, `synced_lines`,
  `status`, and `success`.
- `effect:ado:task-complete` takes the same task/session references plus `result`
  (`succeeded` or `failed`) and `summary`. The workflow maps a validated test result
  to Azure success only after durable evidence publication. Agent exit alone is
  insufficient. Missing, failed or inconclusive results should map to `failed`.

Call log sync with `claim_start: true` on intake, then start the agent only when
`start_required` is true. Persist the same task/session references in scheduled
workflow events and call log sync every five seconds until `done` is true. A new
waiting-task attempt reuses the run and its saved result. Replaying the original
effect retains its start claim so orchestration recovery can continue safely.

On every terminal path call task complete. If it returns `done: false`, schedule
log sync after `retry_delay_seconds`; it will flush remaining batches or retry the
saved completion. Completion attempts stop after three failures, fifteen seconds
apart, with `status: callback_exhausted`. Set the pipeline job timeout to 130 minutes
so a missing callback also has a bounded failure path. Cancelled Azure tasks stop
forwarding. Report delivery and resource cleanup remain separate flows.
If a final log upload fails, the completion callback still proceeds. Continue
scheduling while `done` is false: saved log uploads retry after that callback,
without sending another completion event or a live feed to the finished task.
These log recovery attempts also stop after three failures, fifteen seconds apart,
with `status: log_exhausted`.
Callback intent is saved before sending it. If Azure accepts completion but its
reply is lost, a matching completed task result lets the next tick recover the
receipt and finish pending saved logs without sending another callback.
Terminal attempt budgets are saved before requests, so process interruptions
consume retries too. Successful log batches reset the log failure budget, allowing
long final transcripts to drain across more than three batches.
After the last unsuccessful callback response, one final receipt check can recover
an accepted completion; it never sends a fourth callback. Missing or expired agent
sessions do not block callbacks, while stored manifests still enforce directory
ownership.

Log forwarding currently supports container-worker sessions. Callbacks also work
for libvirt sessions or setup failures without an agent. Deploy Evander 0.99.0 or
later to both the control plane and worker before enabling this feature. Pipeline
logging uses the worker's `session-history` view, which excludes temporary active
tool indicators so ordinary tool completion does not replay the transcript.

Both effects resolve the existing directory-scoped `azure_devops_pat_env` on the
host. The PAT needs permission to read the selected build/timeline and write task
logs and completion events. Callback URLs and credentials are not accepted in the
task object. State uses the existing workflow store under the directory's shared
`ado-pipeline-tasks` scope, keyed by project, pipeline and run, so a separate
scheduled-event graph can forward logs and retry terminal callbacks. No new daemon is
required. Log uploads retry independently of agent execution. Known host secrets
and common credential fields are masked before upload; this is not a guarantee
that arbitrary application output contains no confidential data. An ambiguous
upload response can repeat the last batch. Log reset markers make transcript
replacement visible instead of silently skipping output.

## Development

From the repository root:

```sh
uv pip install -e '.[dev]' -e packages/azure-devops-addon
PYTHONPATH=src:tests:packages/azure-devops-addon .venv/bin/pytest -q packages/azure-devops-addon/tests
```

Tests use synthetic identities and mocked provider requests. They do not create
work items, PRs, builds, or comments in a live Azure DevOps organization.
