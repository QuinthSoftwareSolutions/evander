# 19. Assistant

The Assistant is an optional in-app support assistant available from Admin Web. It helps you ask for guidance, summarize operational context, and sometimes recommend next actions.

Use this feature for speed, not replacement. Any suggested action that changes workflow state, agents, approvals, sessions, issues, or system settings still needs your confirmation against the live Evander pages.

For broad incident workflows, use section 20 (Common End-To-End Tasks). For session, tool-call, and workflow evidence, see section 7 (Audit Trail And Tool Calls). For configuration and access behavior, see section 16 (Configuration).

## 19.1 Open The Assistant

The Assistant launcher is shown in the lower corner of Admin Web when assistant integration is enabled.

1. Open the lower-right assistant button or card.
2. If available, click it to expand the chat panel.
3. If needed, click **New chat** to reset the thread.
4. Use the minimize control to hide the panel while you continue operator tasks.

Possible states and what they mean:

1. **Hidden**: integration is not enabled for this deployment.
2. **Loading**: interface is still connecting to the assistant service.
3. **Disabled**: the backend currently blocks or cannot start new chats.
4. **Error**: request path is failing; use the troubleshooting guidance below.

## 19.2 Assistant Modes

The assistant displays its current mode clearly in the panel. Confirm the mode before asking it to do anything.

1. **Read only mode**
   1. Safe for investigation and explanation.
   2. It can summarize likely causes, read context, and suggest checks.
   3. It should not be relied on as a permission for direct system changes.
2. **Full access mode**
   1. Can request or prepare actions using your current directory context and available tools.
   2. Still use operator judgment before approving risky changes.
   3. Keep prompts specific and explicit about safety boundaries.

Before asking for direct changes, confirm:

1. You are in the correct directory for the issue.
2. The assistant mode is the one you expect.
3. You have unlocked privileges only if your workflow requires action (for example approving, saving, or editing).

## 19.3 Chat Practices

To get useful results, keep messages concise and context-rich.

1. State the scope first: directory, session ID, workflow/run ID, or issue number.
2. Describe the objective in one sentence before the details.
3. Mention what you already checked to avoid repeated investigation steps.
4. Ask for concrete next steps, not broad theory.
5. Use **Shift + Enter** for multiline input, and **Enter** to send.

Useful investigation pattern:

1. Give the assistant the symptom and affected scope.
2. Ask for probable causes ranked by likely probability.
3. Ask which page to check next.
4. Confirm each recommendation with sectioned evidence.

When the assistant suggests action:

1. Verify the recommendation against current context in section 3 (dashboard), section 7 (tool calls), or workflow pages.
2. Prefer one narrow verification step before moving to the next.
3. Keep separate investigations in separate chats (new thread).

## 19.4 Assistant Troubleshooting

If the launcher is missing, disabled, or failing:

1. Reload the page and wait briefly for reconnect.
2. Sign out and sign in again if the panel still does not appear.
3. Check whether the current deployment has assistant integration enabled.
4. Confirm you are on the page for the correct directory scope.
5. If it still fails, request an operator-admin check of chat session service logs and runtime service health.
6. Switch to manual workflows from section 3 (Dashboard and Daily Monitoring), section 7 (Audit Trail And Tool Calls), and section 16 (Configuration) while the assistant is unavailable.

## How-to: Ask The Assistant To Investigate A Problem

Use this for fast issue triage before a full manual investigation.

1. Open the assistant.
2. Start a new message and set the scope:
   1. directory
   2. affected session or VM
   3. workflow/run ID if known
   4. tool call ID when investigating a failing action
3. Describe the observed behavior and when it started.
4. Ask for likely root causes and the shortest verification path.
5. Ask for the exact pages to open next.
6. After receiving guidance, validate each step in Admin Web.

Expected result:

1. You get a short list of likely causes.
2. You get 1-3 concrete checks to run.
3. You now have a validated next step instead of guessing.

## How-to: Start A Fresh Assistant Thread

Use a new thread when one conversation grows too broad or mixes unrelated incidents.

1. Open the assistant panel.
2. Click **New chat**.
3. Enter a short label for the problem in your first message (for example: "workflow run investigation").
4. Keep messages in that thread focused to the same incident.
5. If a second topic is unrelated, start a second new thread.
6. Close the old thread only after you copy useful context notes if needed later.

Expected result:

1. New issue context stays separate.
2. The assistant response quality improves because prompts are focused.
3. You reduce accidental mixing of unrelated operational contexts.
