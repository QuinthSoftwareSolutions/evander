# 11. Workflows

Workflows connect events, decisions, and actions so that Evander can run recurring automation safely and with human oversight.

Use this section to open, inspect, and adjust workflows without needing deep developer knowledge.
For operator details about action logging, use Section 7: Audit Trail and Tool Calls.
For repeated-failure trends and reporting, use Section 12: Reports And Operational Insight.
For safe config revision handling, see Section 16: Configuration.
For end-to-end incident flow, use Section 20: Common End-To-End Tasks.

## 11.1 Workflow Basics

Think of a workflow as a flow chart.

1. A trigger starts the flow when a specific event occurs.
2. Gates decide whether the flow should continue, split, wait, or stop.
3. Queries pull additional context from systems that the flow needs.
4. Effects perform an action, such as sending notifications, starting an Agent session, or updating external state.

You can read this like a human process:

1. "Something happened." (trigger)
2. "Is this the right kind of something?" (gate)
3. "What extra details do we need first?" (query)
4. "Do the approved action." (effect)

The lines between boxes are edges, and each edge carries output from one node to the next.

For operators, the key mindset is this:

1. Every node is accountable for one clear behavior.
2. Edges are where data moves, so broken edges usually show as missing values or later node errors.
3. You can disable a trigger instead of deleting it when you only want to pause a workflow safely.

## 11.2 The Workflow Editor

Use the Workflow tab to manage both graph design and execution viewing.

1. Open the Workflow tab from the left navigation.
2. Select the correct directory so you are editing the right scope.
3. Unlock privileges before editing.
4. Use the Graphs panel to choose the graph and review its name, description, and scope.
5. Use the Create panel to add nodes, then place them on the canvas.
6. Draw or confirm edges that move data between related nodes.
7. Open a node in the Inspector to edit its fields, display label, and binding expressions.
8. Use the Outputs section to verify what values the node sends forward.
9. Watch diagnostics for missing fields, unresolved bindings, or schema mismatches.
10. Use Save workflow only after the graph is clean and the node states are accepted.

The editor keeps your local, unsaved edits staged in the browser until you save.
If you leave the page with unsaved edits, the UI warns before discarding.
If you need a quick graph-level run-through, use the Tutorials control in this tab.

Common editor notes:

1. Disabled triggers appear muted and do not start new work.
2. Query nodes can be used without heavy mental overhead if their input requirements are clearly set.
3. Effects that touch external systems are the places where Section 7 and approvals workflows matter most.

## 11.3 Safe Editing

Safe editing is mostly about avoiding accidental overwrite and unintended side effects.

1. Keep the workflow editor in one directory at a time.
2. Make one change at a time when multiple operators are active.
3. Use Reset changes for a node if a single node became messy.
4. Check diagnostics after each meaningful change.
5. Save in short cycles, especially before moving to a different graph.

What can happen when changes are not staged correctly:

1. You can lose local changes when navigating away without confirmation.
2. Concurrent edits can create revision conflicts and force a reload.
3. A valid change can be delayed if a different operator updated the same graph first.

If this happens, follow the recovery pattern in this manual's how-to "Recover From A Save Conflict".
For the deeper revision model and why it happens, see Section 16.3 Safe Save Behavior.

## 11.4 Workflow Runs

The workflow page also shows runtime execution data so you can diagnose what happened.

1. Use Current runs for active and recently active executions.
2. Use Latest runs when you need a broader history view by graph or node scope.
3. Use event, trace, session, artifact, and other IDs to locate a specific execution quickly.
4. Select Inspect mode to see node-level execution status.
5. In inspect mode, review node state, duration, inputs, outputs, errors, and binding trace.
6. Use links on run rows to open related sessions, tool calls, approvals, or artifacts when the run touches external systems.
7. Exit inspect mode to return to graph editing.

A failed run is usually easiest to understand by checking:

1. Which node started the failure.
2. Whether the failure is from missing input or an external effect failure.
3. Whether upstream binding paths resolved correctly.
4. Whether this looks like a one-off versus a repeat across multiple runs.

Use Section 12 for failure frequency and pattern reporting, and Section 20.1 for the full incident chain.

## 11.5 Generated Draft Workflows

The generator can create a draft graph from a plain-language description.

Important behavior for operators:

1. The draft is a preview. It is not live until you review and save.
2. Draft generation does not run workflows.
3. Accepting the draft imports it as unsaved edits into the normal editor.
4. You can still review each node and binding before saving.

Use this when:

1. You need a fast starter graph for a known pattern.
2. You want to compare an idea against current operator style before manual drafting.
3. You need to avoid repeated boilerplate for common automations.

Use careful review for all effect and query nodes after accepting a draft, especially in production directories.

## 11.6 Workflow Tutorials

Use built-in tutorials as a fast reorientation tool, not a substitute for production review.

1. Open Tutorials from the workflow page.
2. Start First steps for a guided micro-graph.
3. Use Skip if your team has already completed the onboarding walkthrough.
4. Use Restart later if you need to refresh the operator sequence after a long gap.

Because workflows impact live automation, treat tutorial changes and production changes separately.
Do not carry assumptions from tutorial graphs into production graphs without an explicit review pass.

## How-to: Inspect A Failed Workflow Run

Use this when an automation job shows an error in the Dashboard or operators report a missed action.

Prerequisites:

1. Correct directory selected.
2. Read access to Workflow and Tool Calls.
3. Optionally, unlocked privileges for any related follow-up actions.

Steps:

1. Open **Workflow**.
2. Open **Current runs** and locate the failing run.
3. Copy the known reference (event ID, trace ID, session ID, or artifact ID) if needed.
4. Click **Inspect**.
5. Select the failing node in the run graph.
6. Review the node status, duration, and error text.
7. Open binding trace and compare expected versus provided inputs.
8. Open linked session/approval/Tool Call records if the node calls outside systems.
9. Confirm whether this is a one-time data error or a repeated pattern.
10. Record one root cause and next action before continuing.

Expected result:

1. You can name the first node that failed.
2. You can determine whether missing data, bad trigger source, or external dependency failure caused the break.
3. You have links to supporting evidence in Tool Calls or Approvals.

If no output appears:

1. Re-check directory scope.
2. Confirm the run reference is in the current runtime set.
3. Switch to Latest runs for a wider search window.

## How-to: Add A Simple Node To A Workflow

Use this when you need a small enhancement, such as one extra check or one extra notification.

Prerequisites:

1. Correct directory selected.
2. Workflow page open.
3. Privileges unlocked.

Steps:

1. Open the graph you want to change.
2. Click **Create** and choose a node type (trigger, gate, query, or effect).
3. Place the node on canvas near related nodes.
4. Connect one incoming edge from the source and one outgoing edge to the next target.
5. Open the node inspector and set:
   1. display name,
   2. required settings,
   3. any downstream binding uses.
6. Review Outputs for the exact fields you expect to pass forward.
7. Check diagnostics for unresolved placeholders or missing required fields.
8. Save workflow.
9. Run the quick verification loop:
   1. open Latest runs,
   2. trigger a known event if possible,
   3. confirm the new node executes as expected.

If save blocks:

1. Fix diagnostics first.
2. If blocked by a stale revision, follow Recover From A Save Conflict.

## How-to: Change A Trigger Without Breaking The Graph

Use this when an event source, event type, or trigger condition must be adjusted.

Prerequisites:

1. The graph is not currently under active edit by multiple operators.
2. Lock/unlock state is understood.
3. You have identified at least one recent successful run path.

Steps:

1. Open the target graph and select the trigger node.
2. Note current downstream node targets in the inspector panel.
3. If needed, disable the trigger temporarily while editing.
4. Update trigger fields (for example filter, event source, polling, or route selectors).
5. Save a draft with the trigger change only.
6. Re-run diagnostics and confirm no edges become unresolved.
7. Use Current runs to confirm no new unexpected failures appear.
8. Re-enable the trigger and save.

Expected result:

1. The graph keeps the same downstream shape.
2. Existing downstream nodes still receive compatible inputs.
3. Old and new trigger events behave according to the new definition.

Common recovery:

1. If failures appear at the first downstream node, check whether the output contract changed.
2. If needed, revert to saved state and reapply trigger edits with narrower field changes.

## How-to: Recover From A Save Conflict

Use this when the UI reports the graph was changed by someone else before your save completed.

Prerequisites:

1. Unsaved local edits still visible in the editor.
2. You can read the latest graph state from the server.
3. You have identified your original intent (for example one changed field).

Steps:

1. Stop and pause additional edits while the recovery runs.
2. Reload the latest workflow revision from the server.
3. Compare required changes against the new baseline.
4. Re-apply only intentional edits, one node at a time.
5. Re-check diagnostics before saving.
6. Save workflow.
7. Open the graph in Current or Latest runs and confirm normal behavior.

Expected result:

1. Your intended change is preserved.
2. The graph remains consistent with the latest shared version.
3. No unrelated edits from other operators are overwritten.

If you need the full process and implications:

1. See Section 16.3 Safe Save Behavior.
2. Keep a short note in your operational handoff if conflict frequency increases.

## How-to: Generate A Draft Workflow From A Description

Use this when you have a business requirement but want the editor to draft a first pass.

Prerequisites:

1. No unsaved edits in the current editor (or you are ready to confirm they can be discarded).
2. Access to **Generate workflow** in the workflow page.

Steps:

1. Open the workflow generator.
2. Enter a clear plain-language brief in one or two short paragraphs.
3. Start generation and wait for the draft preview.
4. Open the draft and inspect:
   1. node list order,
   2. trigger source,
   3. branch flow,
   4. outputs and bindings.
5. Accept the draft into the editor as unsaved changes.
6. Review every node for production safety:
   1. trigger scope,
   2. effect actions,
   3. query dependencies,
   4. output mapping.
7. Save workflow.
8. Run at least one validation run path from Current or Latest runs.

Expected result:

1. A reviewable draft replaces the empty space in the graph with edits.
2. You either save a safe production graph or keep changes staged for later.

If the draft is not acceptable:

1. Return without saving and keep the current graph untouched.
2. Re-run generation with tighter wording, or build the graph manually.
