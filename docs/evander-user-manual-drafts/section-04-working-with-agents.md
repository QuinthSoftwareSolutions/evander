# 4. Working With Agents

The Agents page is where operators create, prompt, steer, and close day-to-day agent work.
Use this section when you need to act on a specific agent session; for safety limits and approvals, continue to **Sessions, Access, And Policy** (section 5). For what requires human sign-off, see **Approvals And Human Decisions** (section 6). For backend-specific target differences, use **Execution Targets** (section 9).

This section uses plain workflow language. You can perform these actions from the main navigation in Admin Web.

## 4.1 Agent List And Details

In **Agents**, each row usually represents an active or recently completed execution target linked to a session.

You use this view to:

1. Confirm the selected directory and active lock state.
2. Locate the right agent by session, name, target type, or labels.
3. Read live status before acting.
4. Open an agent to inspect:
   - attached session information
   - current VM or container-worker context
   - labels and metadata
   - policy summary
   - recent logs
   - any steering/discovery notes
5. Follow links to related records (session, tool calls, approvals, or related runs).

What to expect:

- The list refresh is tuned for quick browsing and may not reflect every sub-second execution change.
- Use **Refresh** before taking any decision.
- If the agent list does not match your working directory, pause and reselect the directory.

Notes:

- The selected agent context can help decide if you should send a prompt, steer, pause, or retire.
- For target-specific behavior (VM versus container worker), defer to **Execution Targets** (section 9) before changing target-level assumptions.

## 4.2 Create An Agent

Use this flow to start supervised work for a task, issue, or investigation.

When to create:

- You need a controlled environment to run repository changes or automation.
- You need policy-based constraints while the agent works.
- You need an agent tied to a specific directory scope and session.

When not to create:

- If a session already exists for the same task, reuse and resume it instead of creating a duplicate.
- If the work is purely informational and not allowed in the selected scope, use the correct directory/scope first.

## 4.3 Prompt Or Resume An Agent

Once an agent is created, use this flow for new instructions and follow-up work.

Use prompts to:

- ask the next step directly
- recover from partial or ambiguous outputs
- continue from context when handoffs are needed
- resume with explicit thread continuity when needed

You can usually keep one continuous thread for simple work.

If the agent drifts across tasks, you can choose a fresh context in your prompt rather than forcing continuation.

## 4.4 Operator Steering

Steering is your way to correct an active run before it goes too far.

For container worker sessions, steering interrupts the current execution and immediately launches a corrected replacement dispatch behind the same operation context so work can continue without restarting the whole target.

For non-container sessions, steering is queued as a host note and consumed on the next eligible model call for that running flow.

Use this distinction to set your expectations:

- Container worker steering is immediate and interruption-based.
- Relay-style steering is gentler but may wait for a model request to take effect.

## 4.5 Lifecycle Actions

Lifecycle actions include routine and cleanup controls used during normal supervision:

- start
- stop or shutdown
- revoke session access
- destroy
- verify cleanup state

Operators should always treat lifecycle changes as state transitions with recovery steps:

- verify the right agent and directory
- confirm no active approvals are required to proceed
- confirm related outputs and logs are captured before cleanup

## 4.6 Skills For Agents

Skills are reusable agent instruction packages that can be selected at creation or attached later to an existing session.

Use managed skills for:

- repeating the same behavior across many agents
- codified safety patterns (coding style, response format, escalation rules)
- reducing the need for large one-off prompts

If a skill is too broad for one task, prefer a short follow-up prompt plus scoped allowed tools instead of global skill changes.

## How-to: Create A Basic Agent Session

Use this when you need a fresh agent to begin new work.

Prerequisites:

- You are in the correct directory.
- You have unlocked privilege mode if your environment requires it.
- You know which target class you expect (for example standard Codex task execution).

Steps:

1. Open **Agents** from the left navigation.
2. Click **Create**.
3. Confirm the directory is correct.
4. Enter or accept the proposed **session ID**.
5. Choose the model and model relay/mode.
6. Select the execution target shape (use the same choice as your directory and policy expects).
7. Set policy controls needed for this task:
   - allowed tools
   - communication allowlists
   - bootstrap options
8. Add one or more managed skills only if they are required for the task.
9. Click **Create** and wait for the page to show the created agent.
10. Open the new agent row and verify:
   - status is correct
   - session link is visible
   - policy restrictions match your intent

Expected result:

- A new agent row appears with attached session context and a safe starting status.

If it does not appear:

1. Click **Refresh**.
2. Verify directory lock/scope and required privileges.
3. Check if creation failed due to missing session or policy requirements; then review section 5 before retrying.

## How-to: Send A Follow-Up Prompt To An Existing Agent

Use this when the first response is complete but more work is still needed.

Prerequisites:

- You can open the target agent.
- The agent is associated with the expected session.

Steps:

1. Open **Agents** and select the active agent row.
2. Use the **Prompt** action.
3. Write a concise instruction and include exactly what has changed.
4. Keep one objective per prompt.
5. Decide whether to continue in the current thread or start fresh context:
   - keep continuity by default
   - start fresh if you need a clean restart on a different plan
6. (Optional) provide the resume thread explicitly if the UI does not prefill as expected.
7. Send the prompt and wait for a response cycle to start.
8. Watch logs and follow-up entries from the same agent record.

Expected result:

- A prompt dispatch appears, and the next output or action reflects the instruction direction.

If no action appears:

1. Confirm the session allows prompt dispatch in section 5.
2. Check the active lock and required privileges.
3. For no-response cases, inspect related approval/state paths (section 6 and section 7).

## How-to: Install A Managed Skill Into A Session

Use this when you need to add reusable instructions to a session already in progress.

Steps:

1. Open **Agents** and select the target agent.
2. Open the skill install action for the selected session.
3. Choose a skill from the managed library.
4. Confirm no duplicate instruction conflicts with the existing session plan.
5. Install the skill.
6. Open the agent details and verify the skill is now listed.

Expected result:

- The selected session gains the skill behavior for future prompts and work cycles.

If install fails:

1. Confirm the session is unlocked for mutation.
2. Ensure the skill exists in **Skills** and is visible in the current directory context.
3. Retry after refresh; if unavailable, ask an administrator to check managed skill publishing.

## How-to: Steer An Agent That Is Going Off Track

Use this when the current output is clearly wrong direction, unsafe, or out of scope.

Steps:

1. Open the agent and review recent logs to confirm the wrong path.
2. Open the **Operator steering** control.
3. Send one short corrective message with desired outcome, constraints, and boundaries.
4. If the run is a container-worker execution, continue to monitor for a replacement dispatch.
5. If non-container execution is in use, monitor for effect on the next model request and keep the correction visible in context.
6. If the correction is not honored:
   - send one stronger, specific follow-up steering note
   - consider stopping and restarting with a clearer follow-up prompt

Expected result:

- The very next relevant action aligns with the steering intent, or the session is clearly blocked for safe manual intervention.

Common recovery:

- If steering does not change behavior, open related tool call and approval context, then decide whether to pause/stop and route a safe restart sequence.
- For repeated off-track behavior, treat this as possible session design issue and review in section 5 before continuing.

## How-to: Retire Or Destroy An Agent Safely

Use this when work is complete, or when the session can no longer be trusted to continue safely.

Steps:

1. Select the target agent and verify directory, session ID, and target identity.
2. Review recent logs or tool call references so you do not lose critical evidence.
3. Confirm outstanding approvals are handled or irrelevant.
4. Stop or shut down active execution first if it is still running.
5. Revoke session access if no future prompts are needed.
6. Destroy when no recovery or rerun context is required.
7. Confirm destroy result and cleanup state through the agent and sessions views.
8. Download or note the relevant evidence before cleanup if required by your team process.

Expected result:

- The agent is no longer active and is marked as retired/removed per the platform behavior.

If the cleanup action fails:

1. Do not force repeated destructive actions.
2. Re-check directory and lock state.
3. Review the failure path and retry once after a short interval.
4. Record unresolved cleanup in handover notes and escalate through incident notes if needed.

If you need recurring cleanup habits for production directories, use **Lifecycle Actions** as your routine pattern before deleting any historical context needed for audit.
