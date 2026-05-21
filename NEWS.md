# News

## 2026-05-22 - The Control Plane Starts to Explain Itself

Truman has crossed an important line: it is no longer just a way to send agents into work. It is becoming a control plane that can show operators what happened, what is happening, and where human judgment belongs.

The recent run-inspector and Admin Web work made historical workflow runs more visible: selected runs can now expose node activity, resolved context, outputs, errors, related sessions, and the operational state around them. The dashboard work added a practical landing surface for health, capacity, recent automation, version context, and direct operator action. The workflow editor also started moving away from raw JSON as the default experience, with structured forms and better interaction paths replacing some of the internal machinery operators previously had to understand.

The safety story moved forward as well. MCP tool calls now have stronger audit and policy foundations, including approval-gated invocation and parameter-level constraints. Container-backed sessions gained cleanup and recovery visibility, matching the reality that containers are the practical default execution backend in the current Example environment. Together, these changes make capability boundaries, backend behavior, and recovery state easier to inspect instead of leaving them buried in logs or assumptions.

The remaining lesson is clear: speed is useful only when the system stays reviewable. The next work should keep tightening the path from automation event to explainable run, from agent action to auditable tool call, and from failed execution to operator recovery. That is the product bar Truman has to keep pushing toward: safe automation that is powerful because it is inspectable.

-Truman

## 2026-05-21 - Safer workflows, clearer operations

We have been tightening the control plane around the moments where operators need the most confidence: seeing what is running, understanding why a workflow moved, and handing useful context back to an agent without opening broad access. This update brings those threads together in the dashboard and in the workflow runtime.

The operations dashboard now does more than summarize health. Capacity health shows storage pressure, session age, worker reachability, queue age, and unread prompt pressure with explicit missing states, so a stale or unconfigured source is visible instead of quietly guessed. The product context panel now keeps the full changelog one click away and shows this News feed for the human-readable story behind the release.

Workflow automation is also easier to reason about. External workflow-event ingress lets trusted systems publish durable `external.*` events into Admin Web, while workflow run inspection connects traces, trigger events, sessions, agents, approvals, audit links, and artifacts in one place. When a run needs to hand off files, artifact publishing gives operators an immutable record with Admin Web list, detail, and download surfaces.

For teams running agents against sensitive systems, the latest policy work should make day-to-day operations safer. MCP tool calls can now be approval-gated, narrowed with argument policies, and leased to a workflow run without permanently broadening a session manifest. A good starting point is to allow only the tools a workflow needs, make the risky ones approval-required, and use the tool-call audit panel as the review trail.

A few practical tips: update the top of `CHANGELOG.md` before deployment, use this `NEWS.md` entry to explain the user-facing value in plain language, and try the dashboard Refresh button when you need a live read instead of the short-lived dashboard cache. When investigating a workflow, start from the run detail view, then follow the deep links into sessions, approvals, tool calls, and artifacts rather than switching tabs by hand.

Next up, we are aiming for smoother workflow templates, richer run debugging, and better worker visibility so operators can move from "something happened" to "here is the next action" with less digging. Thanks for using Agent Sandbox and for continuing to push it toward safer, more transparent automation.

-Truman
