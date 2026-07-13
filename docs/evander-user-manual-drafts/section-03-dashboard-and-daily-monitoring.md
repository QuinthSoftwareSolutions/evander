# 3. Dashboard And Daily Monitoring

The Dashboard is your first check-in point at the start of each shift and whenever something feels wrong. It is a consolidated view meant to answer one question: *What needs attention now?*

Use the same directory and header settings from section 2 for all Dashboard checks. The dashboard is scoped to the directory you have selected, so it is normal to see different traffic in different scopes.

## 3.1 Dashboard Panels

Open the Dashboard from the left navigation or default landing view after sign-in.

1. Health and deployment overview
   - Use this panel to confirm the control plane itself is healthy.
   - Good signals are expected run state, expected worker reachability, and no persistent "degraded" alerts.
   - If this panel is missing or reports unknown state, jump to the Workers section next.

2. Worker state
   - This panel helps you see if background workers are up, sleeping, failing, or blocked.
   - A healthy worker set matters before expecting workflow activity, approvals, wake jobs, and Git/review automation to execute.
   - If a worker looks unhealthy, continue to section 18.2 before changing workflows.

3. Agents and sessions
   - Use this area to see whether active work is balanced, stalled, or empty for your directory.
   - High active counts plus no fresh session transitions can indicate queue pressure.
   - If you need the exact list, open Agents (section 4) or Sessions (section 5).

4. Workflow activity
   - This is the quickest signal for live automation.
   - Confirm there are recent successful runs and no unexplained red failure clusters.
   - If a run fails repeatedly, use section 20 for the end-to-end failure investigation flow and start with Workflow in section 11.

5. Approval queue
   - This shows whether people or systems are waiting for human review.
   - Pending approvals should be expected during active sensitive work, but a growing queue is an early sign of process backlog.
   - Review these from section 6 when they exceed your normal daily range.

6. Sessions and audit summary
   - Use this for a high-level sense of who is running what, and whether tool-call evidence is being produced.
   - If you need evidence links, move to Tool Calls (section 7) and follow record relations.

7. Reports, version, and news
   - The Reports shortcuts help you decide if a spike is isolated or systemic.
   - The version/changelog card tells you what build is currently visible to operators.
   - The news area usually carries release notes and notices for product updates.

8. Capacity and queue context
   - Capacity signals indicate whether demand is growing faster than control-plane throughput.
   - Persistent saturation here is a precursor to longer response times across sessions and approvals.

If a panel appears empty, missing, or clearly stale, do not assume a UI bug first. Confirm directory scope and then verify with the linked detail page before you make any changes.

## 3.2 Refresh And Staleness

Dashboard tiles are meant for speed. In practice, this means some values can remain valid for a short period but not always live at millisecond precision.

Use these rules:

1. First pass in normal monitoring: read at a glance and note any warning badges.
2. Before any decision that affects real action (e.g., starting extra intervention), click **Refresh**.
3. If something still looks wrong after Refresh, open the detail page linked from the panel.
4. Treat the detail page as the current source for action and deeper investigation.

When to refresh:

- At shift start or after a system update.
- Right after switching directory scope.
- Before paging any colleague about a failed run, blocked worker, or approval backlog.
- When a panel looks inconsistent with a nearby page.

What to expect from refresh:

- Fresh card data for all visible panels.
- Bypassing stale cached summaries for dashboard readbacks.
- No immediate remediation by itself; refresh is for accurate visibility, not for fixing state.

If refresh does not change a suspicious value:

- Open the related panel's detail page.
- Verify that your directory is correct.
- Confirm you have the privileges needed to view restricted records.
- If visibility remains confusing, move to section 21 with the exact page, time, and IDs you observed.

## 3.3 Attention Signals

The following conditions usually require immediate follow-up, even when the interface still looks otherwise normal.

1. Missing data or unavailable panels
   - Meaning: data path could not be read, API path was denied, or scoped data is empty by policy.
   - First action: verify directory, sign-in state, and read permission, then open the corresponding detail page. The privilege lock gates mutations, not reads.

2. Unhealthy or stopped workers
   - Meaning: background automation may stop moving.
   - First action: check Workers (section 18) and then the related logs.

3. Failed workflow runs
   - Meaning: one or more triggers/effects did not complete.
   - First action: open Workflow and then run the end-to-end incident flow in section 20.

4. Pending approvals not moving
   - Meaning: people or configured thresholds are delaying execution.
   - First action: open Approvals (section 6) and verify queue filters, ages, and required actions.

5. High queue age / slow processing
   - Meaning: requests are waiting too long, usually from staffing delay, backend pressure, or dependency issues.
   - First action: compare queue age in Dashboard against worker health and session activity before escalating.

6. Prompt pressure
   - Meaning: many items waiting for the same decision point (agents, approvals, or workflow triggers) while no recent completions occur.
   - First action: review approval and workflow context and confirm there are no blocking external dependencies.

7. Repeated warning banners with no clear source
   - Meaning: a systemic condition is changing between refreshes.
   - First action: use the linked detail pages and open a focused report in Reports (section 12).

For every signal, avoid changing live workflow definitions until you confirm the actual impacted records from detail pages. The Dashboard is for triage, not execution.

## 3.4 Release And Product Information

The Dashboard is also the safest place to confirm what release level and operational notices apply to your current Evander operator session.

You should check:

- Version: current running build identity and whether it matches your deployment expectations.
- Changelog: detailed changes in current and prior updates.
- News: operational announcements tied to product use and updates.
- Deployment banner and build notices: active or pending update state visible near header or dashboard context.

Use this section during shift handover so outgoing operators and incoming operators align on the same release baseline.

Keep in mind:

- If the changelog points to behavior you did not expect, avoid assumptions and confirm the effective config in Configuration (section 16) before action.
- New operator behavior and new surfaces are most often due to release-level changes plus config scope differences.

## How-to: Run A Morning Operations Check

When to use: start-of-shift routine, after returning from an incident, and after scheduled maintenance.

Prerequisites:

- You are signed in and on the correct directory (section 2).
- You know whether you are in normal monitoring mode or an incident mode.

Steps:

1. Open Dashboard and immediately note the active directory in the header.
2. Click **Refresh** once.
3. Read the deployment/health card for global service condition.
4. Open the Workers card and verify major background services are up.
5. Open the Sessions and Agents summary and check that recent work exists where expected.
6. Open Workflow activity and confirm recent successful runs exist.
7. Open Approvals and confirm pending count is manageable.
8. Open Reports or queue snapshots if the day historically has recurring spikes.
9. Open Version and News, then check the changelog date range before your first action.
10. Record your findings in a short handover note: "all green", "watch items", or "investigate".

Expected result:

- You have a single-screen snapshot and a clean decision on whether the system is ready for routine work.

Common recovery:

- If any core signal is yellow or red, do not skip to changes.
- Open the relevant detail page and validate by ID/timestamps before taking the first corrective action.

## How-to: Follow A Dashboard Warning To The Detail Page

When to use: any time a Dashboard card shows a warning, unavailable marker, or unusual spike.

Prerequisites:

- Use the same directory as shown on the header.
- Ensure you know whether your user can view and act in the target section.

Steps:

1. Note the exact warning text and the panel that shows it.
2. Use the panel's quick link (for example Open workflow, Open approvals, Open sessions, Open tool calls, Open workers, Open reports, Open config).
3. In the detail page, apply filters to the same scope and time range implied by the warning.
4. Confirm at least one concrete record that matches what you saw in the Dashboard card.
5. Follow any relation links to the next page, if available.
6. Return to Dashboard and recheck the warning after actions.

Expected result:

- You move from a summary warning to one specific record or event chain.

Common recovery paths:

- If the detail page is empty, verify directory scope and permissions first.
- If it is still empty and you expect data, collect timestamp, directory, and warning details and use section 21 for evidence capture.

## How-to: Download The Changelog

When to use: before shift handover, after a deployment notice, or before changing config/workflow behavior.

Prerequisites:

- Access to the Dashboard version/changelog panel.

Steps:

1. Open Dashboard and find the version/changelog panel.
2. Locate the release or changelog link.
3. Click **Download** (or the page equivalent control).
4. Save the file with a clear name including date and directory.
5. Read the release notes entries relevant to your directory scope.
6. Keep the changelog next to your handover notes.

Expected result:

- You have the full release note text available locally for shift context and incident handoff.

If the changelog cannot be downloaded:

1. Use the dashboard news entry as a temporary reference.
2. Check if the deployment banner indicates an active update state.
3. Confirm visibility in Configuration and version settings (section 16), then retry once service health is stable.
