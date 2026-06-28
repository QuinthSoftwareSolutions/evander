# 7. Audit Trail And Tool Calls

Use the Audit Trail and Tool Calls view when you need evidence, not guesswork.
It shows what agents, workflows, and operators asked Evander to do, in a read-only timeline with
links to nearby records.

For approvals, workflow execution decisions, and common failure flow patterns,
use the deeper decision guidance in section 6, workflow execution details in section 11,
and end-to-end recovery patterns in section 20.

## 7.1 Tool Call Search

Use filters first, so you can answer a question before opening a long list.

1. Open **Tool Calls** from the left navigation or from the Dashboard quick link.
2. Set the correct directory in the shared header first.
3. Choose filters for what you need:
   - free-text search
   - session or agent identifier
   - tool name
   - status (`allowed`, `denied`, `error`)
   - directory scope
   - start/end time
   - result limit
4. Apply the most specific filter first (for example: session first, then tool, then time).
5. Sort by newest or oldest first based on whether you are checking a current issue or reconstructing
   a timeline.

Common search shortcuts:

- to verify one job: filter by session and then narrow by status
- to confirm a specific impact area: filter by tool name
- to investigate a gap: filter a narrow time window first, then widen if no rows appear

## 7.2 Reading A Tool Call

Open a row to confirm what was attempted and what happened.

1. Note the timestamp and event status (`allowed`, `denied`, or `error`).
2. Confirm who or what started it:
   - actor (agent or operator)
   - directory
   - session
3. Confirm what was requested:
   - tool name
   - request ID
   - correlation ID when one action is part of a larger flow
   - workflow run ID when automation called the tool
4. Check outcomes and safety gates:
   - decision
   - duration
   - payload digest and approval ID when approval was required
5. Review the redacted arguments and redacted result for context.

Notes for operators:

- Redaction is expected. Sensitive values are hidden by design.
- If the action was approved, the approval ID links the call back to the human decision trail.
- If the call was denied, the row is still usable evidence for what was blocked and why.

## 7.3 Following Related Records

Tool Calls are designed to move you quickly to the owning record.

1. In an open Tool Call row, use relation links to open:
   - session record
   - approvals
   - workflow run
   - agent
   - VM
   - reports
   - configuration
   - users
   - workers
2. Open links in the order that matches your question:
   - for "who requested it", open session/agent first
   - for "did approval matter", open approvals first
   - for "what run did it come from", open workflow first
3. Keep the same IDs with your notes (session, request, correlation).
   This makes handoff and follow-up updates much faster.

Tip: Use this section to avoid rebuilding state from memory; every related tab should point to the same
source IDs when you need follow-up evidence.

## 7.4 Incident Review

Use this section when several calls are connected.

1. Set a narrow time window first.
2. Search by session, tool, or actor depending on the incident scope.
3. Add all relevant calls to a single view by status and sort order.
4. Read each row in timeline order and write down the key IDs:
   - request ID
   - correlation ID
   - session ID
   - workflow run ID
   - approval ID
5. Open linked records to confirm chain of custody:
   - approvals explain human gates and decision timing,
   - workflow links explain trigger and node context,
   - agent/session links explain who owned the active session.
6. Keep non-sensitive findings only in handover notes; redacted values are usually enough for
   operational decisions.

A short incident check works best in this order:

1) Was there an action at the expected time?
2) Was it allowed, denied, or errored?
3) Was a required approval present and valid?
4) Did any workflow step show a mismatch between request and execution?
5) What is the smallest follow-up action for a safe recovery?

## How-to: Find What An Agent Did

Use this when you need a direct, auditable statement about one agent's behavior.

1. Open **Tool Calls**.
2. Filter by **Session** and, if needed, exact **actor/agent**.
3. Limit the time range to the period when the agent was active.
4. Sort newest-first for quick discovery, then open likely rows.
5. For each row, record:
   1. timestamp,
   2. tool,
   3. status,
   4. request ID,
   5. workflow run ID (if present).
6. Open the linked Session or Agent record to confirm ownership and context.
7. Open the linked Workflow run if the action was produced by automation.

Expected result:

- A clear list of what the agent attempted, when, and with what execution outcome.

## How-to: Confirm Whether A Sensitive Action Ran

Use this when verifying if a risky action happened before escalation or rollout.

1. Open **Tool Calls**.
2. Filter by the sensitive tool name.
3. Set status to show both `allowed` and `denied` to catch policy blocks.
4. Narrow by session or actor to avoid false positives.
5. Identify the most relevant entry and confirm the status:
   - `allowed` means Evander executed (or executed past the gate path).
   - `denied` means execution did not proceed in that attempt.
   - `error` means execution failed after permission was granted.
6. Open the linked approval row if an approval ID appears.
7. Copy the request ID and correlation ID into the incident notes for follow-up.

Decision point:

- If status is not what policy expected, use section 6 for approval logic and section 20 for recovery
  flow.

## How-to: Build A Timeline For An Incident

Use this when multiple tools, sessions, or runs overlap.

1. Pick an incident window and enter it in the time filters.
2. Filter to the affected directory and one key session.
3. Add status filters so both successful and failed attempts are visible.
4. Sort oldest-to-newest to reconstruct cause and effect.
5. In each row, capture the stable IDs:
   1. request ID,
   2. correlation ID,
   3. session ID,
   4. workflow run ID,
   5. approval ID.
6. Open links from each row in this order:
   - Session
   - Approvals
   - Workflow
   - Agent/VM
7. Build a plain-language sequence:
   1. what started,
   2. what gate or approval was expected,
   3. what actually ran,
   4. where it failed or succeeded.
8. Add one line of handoff summary: "next action", "open owner", "decision owner", and "current risk".

Expected result:

- A clean chronological account you can hand to another operator without relying on memory.
