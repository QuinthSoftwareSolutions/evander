# 12. Issues and Provider Integrations

> **At a glance.** Evander keeps a local issue record as the control-plane anchor for work, while provider adapters read or change the corresponding Azure DevOps or GitHub object through host-owned credentials. A local issue can be authored in Evander or act as a cache-backed proxy for a remote work item. Workflow triggers own intake; provider queries and effects own external API calls; the ADO worker owns Azure DevOps pull-request feedback and completion polling. Those responsibilities are deliberately separate.

## The mental model: anchor, projection, provider

Three records that look like “the issue” serve different purposes:

| Record | Authority | Purpose |
|---|---|---|
| Local issue | Evander runtime | Stable identity, directory scope, priority, state, VM/session association |
| Proxy cache | Evander runtime, refreshed from provider | Fast display of selected remote fields and synchronization diagnostics |
| Remote issue or pull request | Azure DevOps or GitHub | Provider-native discussion, status, reviewers, commits, and links |

The local record never smuggles a provider token into a guest. When an agent uses an issue tool, the gateway resolves its session and directory, applies policy, and calls a host provider. Provider-normalized responses then cross the boundary.

Northstar Platform's `mission-control` project, for example, can track “add telemetry retry behavior” as a local proxy for Azure DevOps work item 14084. Imani's execution session refers to the local record; the proxy cache shows the remote title and state; a workflow effect posts a progress comment to Azure DevOps.

## Local issue identity and lifecycle

A `LocalIssueRecord` has an internal `issue_id`, a human-facing `issue_number`, directory path, title, description, state, source type, timestamps, and metadata. Identity is scoped. The same visible number can exist in different directories or provider projects, so callers should use the internal ID whenever a number could be ambiguous.

The supported local states are:

```text
New -> Approved -> Committed -> In Progress -> Completed -> Closed
```

These are allowed values, not a promise that every transition must visit every intermediate state. State-changing services enforce the operation-specific invariant. `claim_next_issue`, for example, selects an eligible local manual issue by priority and age and moves it to `In Progress`. `complete_issue` refuses an issue that is not already `In Progress`.

Priority values are `P0` through `P3`; omitted priority behaves as `P3` for queue ordering. `claim_next_issue` considers `Committed` records in the calling session's directory scope, orders them by priority and age, and moves the winner to `In Progress`. Only `local_manual` issues participate in the local claim/complete queue. A `remote_proxy` remains governed by its remote system and is changed through provider-aware operations.

Issue VM creation and repository bootstrap are separate actions. Creating the VM first makes failures and cleanup visible; cloning a managed repository later gives the session only the repository identity and access mode it needs. Deleting a local issue is blocked while a VM or session still depends on it.

## Remote proxies and freshness

A proxy stores its binding in `metadata.remote`, including the normalized provider and remote issue ID. `metadata.proxy_cache` contains the most recently materialized provider payload and refresh time. Provider errors belong in synchronization metadata; they must not be disguised as fresh remote state.

Current proxy mutation support is Azure DevOps-specific. Provider-neutral workflow queries can read remote issues from Azure DevOps and GitHub, but this does not imply that every mutation exists for every provider. Unsupported combinations fail explicitly.

The `evander-proxy-issue-sync` worker periodically batches Azure DevOps summaries for known proxies. It refreshes changed cache entries and records diagnostics. A missing remote item does not automatically delete its local proxy: disappearance can mean permissions, a transient provider failure, or deletion, and requires an operator decision.

Remote intake is workflow-owned. A remote-issue trigger or query discovers matching work and an explicit proxy-upsert step creates or refreshes the local anchor. The proxy sync worker maintains already-known records; it is not a second intake engine.

## Provider-normalized workflow nodes

Provider adapters translate external shapes into stable workflow contracts:

- remote issue queries accept provider-specific filters and return canonical issue items;
- Azure DevOps effects create, update, delete, and comment on work items where configured;
- pull-request list/get queries normalize identifiers, branches, SHAs, status, reviewers, commits, and raw provider context;
- pull-request effects update supported metadata or tags;
- reply and publish services expose provider-specific discussion and PR publication behind normalized calls.

Normalization is intentionally lossy at the portable layer. A workflow that needs a provider-only field should declare that dependency rather than assuming every provider has it. Raw response data is available only where the node contract explicitly exposes it.

Inline provider media is also a trust boundary. Media URLs must use the expected HTTPS provider host and pass size and content checks before Evander makes them available. Arbitrary HTML or image URLs in an issue body are not fetched merely because a provider returned them.

## Pull requests, feedback, and merge preflight

Pull requests have two operating modes:

1. **Explicit streams.** Current workflow configuration names provider, repository, and matching rules. The ADO worker lists and polls those Azure DevOps PRs, records feedback cursors, and publishes new-feedback or completion events idempotently.
2. **Legacy PR-map bindings.** Older sessions may be associated with entries in `runtime/pr-map.json`. This compatibility path remains readable, but new workflow design should prefer explicit PR triggers and managed repository identity.

The ADO worker owns Azure DevOps review feedback and completion pickup. It does not own general work-item intake. GitHub PR reads, replies, and publication use their provider services; do not infer a GitHub polling worker from the ADO service name.

Merge preflight is read-only analysis. It resolves the provider PR snapshot, verifies that expected refs and commits exist in the authorized managed repository, and uses Git's merge-tree machinery for single, pairwise, or ordered checks. Results distinguish a clean merge from conflicts, missing refs, a stale target branch, repository errors, and provider errors. Preflight never merges, pushes, or updates the PR.

For Rowan's review of Imani's retry-telemetry change, a stale-target result means the target SHA reported by the PR no longer matches the current target ref. Rowan should refresh the PR snapshot and rerun preflight—not treat an earlier clean report as permission to publish.

## Failure behavior

| Symptom | Likely invariant or boundary | Safe response |
|---|---|---|
| Issue number resolves to several records | Number is not globally unique | Supply directory/provider context or internal `issue_id` |
| Claim returns no work | No eligible `local_manual` issue, or state/priority filter excludes it | Inspect source type, directory, state, and priority |
| Proxy fields look old | Sync has not succeeded or provider is unavailable | Inspect `proxy_cache` time and `proxy_sync` diagnostics; refresh explicitly |
| Provider mutation is rejected | Provider/operation pair is unsupported, policy denies it, or credentials are missing | Confirm the node's provider contract and host credential configuration |
| PR feedback repeats or disappears | Cursor/stream identity changed or legacy and explicit modes were mixed | Inspect the trigger stream, worker state, and published event IDs |
| Preflight reports `missing_ref` | Required SHA was not fetched into the managed repository | Refresh the managed repository and rerun; do not substitute branch tips |
| Preflight reports `stale_target_branch` | Provider target SHA and current target ref differ | Refresh provider and Git state before deciding |

Provider failures should leave enough context to retry without fabricating success. Workflow effects record attempts and outcomes; polling workers preserve cursors only at their defined success boundary; proxy refresh keeps the last known cache with an explicit diagnostic.

## Practical runbook

1. **Identify the record.** In Admin Web, select the directory and record the internal issue ID, visible number, `source_type`, and current state.
2. **Separate local from remote truth.** For a proxy, inspect `metadata.remote`, cache timestamp, and sync diagnostics. Open the provider URL when remote authority matters.
3. **Check host configuration.** Verify the selected directory inherits the intended provider organization/project/repository and that its token environment variable exists on the host.
4. **Check the responsible component.** Intake failures belong to the trigger workflow; known-proxy freshness belongs to `evander-proxy-issue-sync`; Azure DevOps PR feedback/completion belongs to `evander-ado-worker`.
5. **Inspect journals and workflow records.** Use `journalctl -u evander-proxy-issue-sync.service` or `journalctl -u evander-ado-worker.service`, then correlate the issue/PR identity with trigger and effect records in Admin Web.
6. **Refresh before mutating.** Re-read the remote object and, for code changes, refresh the managed repository. Preserve the exact provider ID and commit SHA.
7. **Retry the narrow operation.** Refresh a proxy, replay the failed workflow effect under its normal idempotency rules, or rerun merge preflight. Never edit provider/cache JSON to simulate success.

## Source trail

- `src/evander/models.py`
- `src/evander/admin/services/issue_helpers.py`
- `src/evander/admin/services/issue_git_service.py`
- `src/evander/issues/service.py`
- `src/evander/daemons/ado_issue_worker.py`
- `src/evander/daemons/proxy_issue_sync.py`
- `src/evander/pull_request_automation.py`
- `src/evander/pr_tracking.py`
- `src/evander/pr_merge_preflight.py`
- `src/evander/queries/providers/remote_issues.py`
- `src/evander/queries/providers/pull_request_get.py`
- `src/evander/effects/providers/pull_request_update.py`
- `tests/test_issue_git_admin_service.py`
- `tests/test_proxy_issue_sync.py`
- `tests/test_ado_issue_worker_state.py`
- `tests/test_pr_query_service.py`
- `tests/test_pr_merge_preflight.py`
