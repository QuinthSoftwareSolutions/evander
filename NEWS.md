# News

## 2026-05-21 - Safer workflows, clearer operations

We have been tightening the control plane around the moments where operators need the most confidence: seeing what is running, understanding why a workflow moved, and handing useful context back to an agent without opening broad access. This update brings those threads together in the dashboard and in the workflow runtime.

The operations dashboard now does more than summarize health. Capacity health shows storage pressure, session age, worker reachability, queue age, and unread prompt pressure with explicit missing states, so a stale or unconfigured source is visible instead of quietly guessed. The product context panel now keeps the full changelog one click away and shows this News feed for the human-readable story behind the release.

Workflow automation is also easier to reason about. External workflow-event ingress lets trusted systems publish durable `external.*` events into Admin Web, while workflow run inspection connects traces, trigger events, sessions, agents, approvals, audit links, and artifacts in one place. When a run needs to hand off files, artifact publishing gives operators an immutable record with Admin Web list, detail, and download surfaces.

For teams running agents against sensitive systems, the latest policy work should make day-to-day operations safer. MCP tool calls can now be approval-gated, narrowed with argument policies, and leased to a workflow run without permanently broadening a session manifest. A good starting point is to allow only the tools a workflow needs, make the risky ones approval-required, and use the tool-call audit panel as the review trail.

A few practical tips: update the top of `CHANGELOG.md` before deployment, use this `NEWS.md` entry to explain the user-facing value in plain language, and try the dashboard Refresh button when you need a live read instead of the short-lived dashboard cache. When investigating a workflow, start from the run detail view, then follow the deep links into sessions, approvals, tool calls, and artifacts rather than switching tabs by hand.

Next up, we are aiming for smoother workflow templates, richer run debugging, and better worker visibility so operators can move from "something happened" to "here is the next action" with less digging. Thanks for using Agent Sandbox and for continuing to push it toward safer, more transparent automation.

-Truman
