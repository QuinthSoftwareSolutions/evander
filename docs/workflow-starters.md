# Workflow starters

Starters provide a short path from a running Evander installation to a useful workflow.
They create ordinary, editable workflow packs. The workflow editor, custom providers,
directory policies, and existing automation remain available.

## First workflow

1. Finish the [host setup](../README.md#quick-start), including an execution backend,
   model access, and the trigger and effect orchestrators. Open Admin Web.
2. Select the directory that should own the workflow. Create an agent in **Agents**
   if that directory has none. Choose its model, tools, and permissions there.
3. Open **Workflow starters**, or use **Start a workflow** on the dashboard.
4. Choose an outcome and fill in the short form. **Check & preview** checks the
   generated graph and shows its prerequisites, schedule, target agent, and nodes.
5. Unlock privileged operations and choose **Activate workflow**. Activation requires
   workflow write permission for the directory and workflow pack management permission
   at root. Preview also requires these permissions but does not require unlocking.
6. Complete the verification step, then use **Open workflow editor** to customize it.

Preview does not create agents, resolve credentials, send messages, or install a pack.
Activation enables the new workflow without changing existing agent permissions.

## Run an agent task on a schedule

Choose an existing agent, enter task instructions, and select hourly, daily, or weekdays.
Daily and weekday schedules use the chosen time and IANA time zone. Hourly runs occur
on the hour. The agent keeps its configured model and tools; no external integration is
required by this starter.

After activation, **Run task once now** executes those instructions through the normal
workflow queue. It can perform the actions authorized for that agent. Queued and running
states are not verification: success requires the agent's completed lifecycle event and
the downstream completion node. Verification follows persisted continuation links
from the originating event and checks later failures in that chain. Open the run
report to inspect its actual result.

The generated workflow also has a normal `trigger:event` node for manual execution.
Use the editor for other schedules, additional steps, conditions, and retries.

## Respond to Teams direct messages

Install and enable the separate [Teams/Graph add-on](../packages/teams-graph-addon/README.md)
and configure its identity for the selected directory. Start its ingestion workers and
the workflow orchestrators. The starter appears when the add-on is loaded.

Choose an existing agent with `teams_post_status` permission and write access to the
selected chat. Supply that chat's `chat:...` target and the sender's UPN or Graph user ID.
Only new direct messages from that sender in that chat trigger the workflow. Existing
messages are not replayed on activation. The starter does not add tool or target grants.

Send a new message in Teams to test it. Verification requires a completed agent task
and a successful `teams_post_status` call to that same chat, correlated to that run.
If audit correlation is missing, the page asks you to inspect the run and reply; it does
not infer delivery from unrelated session activity. **Check again** refreshes the evidence.
Readiness checks inspect configuration; they are not live Graph connectivity tests.

## Deliver an Azure DevOps issue

Install and enable the optional [Azure DevOps add-on](../packages/azure-devops-addon/README.md).
Choose a registered repository already cloned into the selected agent, one work-item
number, a base branch, an existing CI pipeline, and reviewer/repair limits. Preview
and activate the editable graph, then start the issue once from the verification step.

The workflow accepts only that agent's dedicated delivery branch, pins the pushed
commit, publishes its PR, and checks CI, review, and blocking branch policies. Repairs
are bounded and deduplicated by commit. Approve the exact merge in **Approvals**.
Success requires the completed PR and confirmation of the merge commit on its target.
The run report shows pending checks and actionable blockers throughout.


## Ownership and changes

Each activation installs a uniquely named pack under `config/workflow-packs/` using
the normal configuration lock, validation, cursor seeding, and change history. A stale
preview is rejected if the configured workflows or checked prerequisites changed.
Names already in use are rejected. Retrying an accepted activation returns its original
receipt instead of overwriting an operator's changes.

Starter receipts are stored in `runtime/workflow-starter-instances.json` and contain
the pack reference and verification requirements, not credentials or task instructions.
Reopen an entry under **Started workflows** to resume verification. After editing the
workflow, use its ordinary run report to verify the changed version. Starter or add-on
upgrades never regenerate existing packs. Use the existing pack controls to disable or
remove a workflow.

Installing another workflow does not invalidate an unchanged starter's verification or
manual test. Preview also checks the configured worker execution depth before activation;
if it is too low, shorten the workflow or update that setting through Admin.

Legacy configurations with inline workflows continue to run. The starter installer
requires pack-based configuration and reports when inline workflows need to be moved
into packs through the workflow editor first.

## Add-on authors

Register `IntegrationWorkflowStarter` through the [integration contract](optional-integrations.md).
Use a flat object schema for guided forms, with titles, descriptions, defaults and required
fields. String enums render as choices; `format: evander-session` selects a scoped existing
agent and `format: textarea` provides task instructions. Keep builders read-only and validate
provider-specific target permissions without expanding them.

An optional `verification(context, values)` callback returns `IntegrationStarterVerification`:
the starter's trigger IDs, a completion node ID, operator instructions, and optionally a
manual event trigger and required successful tool call/arguments. Long-lived workflows may set `follow_workflow_completion=True` to find their own
completion node in subsequent events; evidence must postdate activation. Place the completion
node after real completion, such as the agent effect's `completed` port. Starters without
this callback remain usable and direct operators to the normal run report for verification.
