# 12. Reports And Operational Insight

Reports are where operators check "is this working well?" versus "do we have an outage or drift signal now?"

Use this section to read workflow health patterns, repeated failures, PR integration safety checks, conformance guarantees, and cost signals before you make release or retry decisions.

For detailed step-level workflow diagnosis, see section 11. For evidence trail rebuilds, see section 7. For incident handoff and safe action decisions across multiple pages, see section 20.

## 12.1 Workflow Graph Activity

The workflow graph activity report helps you answer:

1. Which workflow graphs are running successfully and which are not.
2. Whether activity has shifted toward one graph unexpectedly.
3. Whether run count or node count changed in a way that indicates new noise, retries, or duplicate routing.

What the report usually shows:

1. A ranked list of graphs for the selected time window.
2. Activity and run counts.
3. Node-level totals and latest activity time.
4. Status breakdowns and active node status.

Use this report during handoff windows when a dashboard warning is non-specific and you need objective evidence first.

## 12.2 Workflow Failure Clusters

Failure clusters group similar workflow failures so you can separate one-off incidents from repeat issues.

You use this when a spike is suspected or when many failures appear to share the same root cause.

What the cluster view does:

1. Group failures by graph, node, and normalized error signature.
2. Remove volatile IDs and timestamps from the grouping signature.
3. Show active, recent, and resolved labels as triage signals.
4. Link each cluster to representative workflow run examples.

Label meaning is a starting point, not a full verdict. For root cause and external context, open a representative run in the workflow detail view from section 11.

## 12.3 PR Integration Preflight

This report gives a dry-run read-only check before PRs are combined in branch order.

Use it when:

1. You are planning multiple merges into the same target branch.
2. A conflict is suspected but no conflict has been applied yet.
3. You need a quick safety signal before human merge sequencing decisions.

Expected read-only status meanings:

1. `clean`: merge simulation succeeded.
2. `conflict`: one or more content conflicts were found.
3. `missing_ref`: source or target commit/ref could not be resolved.
4. `stale_target_branch`: target changed after recorded upstream branch state.
5. `repo_error`: repository resolution failed.
6. `provider_error`: provider lookup failed.

The report does not change branch state, merge policy, or completion status.

## 12.4 Conformance Reports

Conformance reports show whether workflow contracts are still complete and what requirements are missing.

Two standard snapshots are exposed:

1. Trigger conformance snapshot.
2. Effect conformance snapshot.

Each record includes status fields such as:

1. `pass` or `fail` from report state,
2. `missing`, `invalid`, and `stale` when artifacts are unavailable, broken, or old,
3. generated time, requirement counts, missing coverage, and traceability task counts,
4. compact remediation hints when coverage gaps exist.

Treat this as an operational health signal. Use it with your CI or change-control flow, and investigate failing items before changing workflow packs.

## 12.5 Usage And Cost Awareness

This report section helps operators understand inference volume and expected spend before a work burst becomes costly.

Use it to:

1. Compare usage over selected time windows.
2. Spot unusual token spikes per session, model, or directory.
3. Estimate cost trends with catalog-driven model rates.

Cost values here are operational estimates:

1. They are based on local pricing rules and available token metadata.
2. They are not a billing invoice.
3. They may not include all provider-specific surcharge patterns.
4. OpenAI billing totals can differ from per-model estimate totals.

Before using these numbers for approval or budget messaging, confirm current catalog and pricing context in the pricing controls referenced by operations.

## How-to: Run A Workflow Activity Report

Use this when a deployment warning says "activity changed" but not where.

Prerequisites:

1. Admin Web is open and you are in the correct directory.
2. You have read access to Reports.
3. You know the desired time span (24h, 7d, 30d, or custom).

Steps:

1. Open **Reports**.
2. Select **Workflow graph activity**.
3. Choose a time preset or custom range.
4. Click **Run report**.
5. Scan ranked graphs and identify unusual changes.
6. Select a graph with unusual activity.
7. Check status split and latest activity time.
8. Open the most relevant graph detail in section 11 for node-level context.

Expected result:

1. You can point to a specific graph that changed materially.
2. You get a clear starting hypothesis for escalation or mitigation.

If the report returns no rows:

1. Check directory scope.
2. Confirm the selected window overlaps with expected activity.
3. Re-run with a wider custom range.

## How-to: Find Repeated Workflow Failures

Use this when repeated failed rows are visible across current or latest runs.

Prerequisites:

1. You have workflow run examples from section 11 or the Reports tab.
2. You can open representative run detail rows.
3. You have enough window size to capture repeated patterns.

Steps:

1. Open **Reports**.
2. Select **Workflow failure clusters**.
3. Choose a range that includes multiple runs (start with 7d).
4. Click **Run report**.
5. Sort by count and scan for highest frequency clusters.
6. Open one representative run from the cluster.
7. Compare failure signature with the node and status details.
8. Verify if the failures are:
   1. isolated (single node, single session),
   2. systemic (multiple nodes, broad graph surface), or
   3. stale (already labeled and no new activity).
9. Link to section 7 for action-level evidence and section 11 for run detail.
10. Decide whether to contain with scope changes, retry policy, or deeper root-cause triage.

Expected result:

1. You can state if the issue is isolated or systemic.
2. You have a representative run to support the next operational decision.

If the cluster view looks inconsistent:

1. Re-run with a wider range.
2. Confirm the same directory and selection is used consistently.
3. Open the run details directly and verify cluster labeling is a signal only.

## How-to: Check Whether PRs Are Safe To Combine

Use this before you merge or queue PR merge order.

Prerequisites:

1. You are reviewing Azure DevOps PR IDs for one repository.
2. You know your target directory policy and base branch.
3. You can use reports mode that supports PR simulation.

Steps:

1. Open **Reports**.
2. Select **PR integration preflight**.
3. Enter:
   1. provider (`azure_devops`),
   2. repo,
   3. directory path,
   4. managed repository ID,
   5. mode (`single`, `pairwise`, or `ordered`),
   6. PR ID list.
4. Confirm the managed repository is visible inside the selected directory boundary.
5. Click **Run report**.
6. Read the overall status and `summary_text`.
7. Review participants and checks to see where the conflicts or branch drift appear.
8. If needed, expand PR order checks in `ordered` mode.
9. Only proceed with actual merge or merge scheduling when status is clean and findings are low risk.

Expected result:

1. You get an explicit go/no-go signal before branch changes happen.

If status is unsafe:

1. Share the exact conflict paths and participant list with the owning team.
2. Investigate using section 7 and 11 where the linked run context exists.
3. Defer combination until conflicts are resolved or sequencing is adjusted.

## How-to: Regenerate A Conformance Snapshot

Use this when conformance is stale, missing, or stale policy changes are expected.

Prerequisites:

1. You have unlocked privileges if your deployment requires mutation permissions for report actions.
2. You can access the **Reports** tab.
3. You understand trigger/effect separation.

Steps:

1. Open **Reports**.
2. Select **Conformance**.
3. Choose **trigger** or **effect**.
4. Click **Run report**.
5. Wait for `state`, `status`, and `generated_at` fields to refresh.
6. Review:
   1. requirement count,
   2. missing coverage,
   3. traceability task count,
   4. remediation snippets.
7. If any key requirement remains missing, confirm whether it is expected by current workflow behavior.
8. If needed, use the fallback command path for manual regeneration from the control host using existing conformance suite commands in the conformance report documentation.

Expected result:

1. Conformance state moves from missing/invalid/stale to a fresh pass/fail state.
2. The refreshed snapshot matches your latest workflow runtime shape.

If report regeneration fails:

1. Confirm the runtime directory is writable and correct.
2. Re-run after checking active locks and service health.
3. Inspect service and report errors, then correlate to section 7 for exact action history.
