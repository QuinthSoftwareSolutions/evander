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
