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

## Development

From the repository root:

```sh
uv pip install -e '.[dev]' -e packages/azure-devops-addon
PYTHONPATH=src:tests:packages/azure-devops-addon .venv/bin/pytest -q packages/azure-devops-addon/tests
```

Tests use synthetic identities and mocked provider requests. They do not create
work items, PRs, builds, or comments in a live Azure DevOps organization.
