# Reset assistant context

Use the existing `effect:agent:enqueue-prompt` with `resume_mode: new_context`.
This starts a fresh provider conversation instead of resuming an explicit or
saved thread. Keep the conversation's usual `resume_thread_metadata_key` so the
normal completion path saves the new thread for later requests.

```json
{
  "id": "fresh-context",
  "display_name": "Start fresh",
  "type": "effect:agent:enqueue-prompt",
  "session_id": "assistant-session",
  "conversation_key": "conversation-a",
  "dispatch_id": "source-message-id",
  "delivery_mode": "steer_if_running",
  "resume_mode": "new_context",
  "resume_thread_metadata_key": "conversation-a",
  "prompt": "This turn starts fresh. Briefly confirm the context reset."
}
```

The existing queue handles busy assistants. Current work finishes; requests
already ahead of the reset run first. The reset request starts a fresh turn at
its queue position, and later requests resume its saved thread. Normal delivery
identities, retries and queue recovery apply. No reset service, new database,
generation counter or communication-provider dependency is needed.

A reset-only request uses a brief confirmation turn. For a reset with another
task, pass the original message to the fresh turn and tell the assistant that the
reset has already happened. Keep existing model-selection routing before enqueue.
Model selection, saved preferences, persistent memory, tools and workspace files
are retained. Stored chat history remains available through existing tools;
a fresh-turn prompt should not automatically reload earlier conversation history.

In the operator workflow, recognize standalone case-insensitive `!RESET` and add
reset intent to the existing classification question set. Use the existing
confidence threshold and reject quoted, negated, hypothetical or explanatory
mentions. Route recognized requests to `new_context`; ordinary requests retain
`same_context`. Treat a reset as work when deciding whether a model-only request
can finish without enqueueing. Apply workflow edits through the existing editor
revision and lock mechanisms after deploying the compatible dispatch fix.

Rollback consists of disabling the reset route. No reset-specific persisted state
or data migration needs to be retained.
