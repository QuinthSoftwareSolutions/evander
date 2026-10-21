# Conversation context reset

A workflow can reset one Teams assistant conversation before its next turn.
An active turn finishes normally. Queued requests retain their text, order and
delivery identities. The next eligible dispatch creates a fresh provider thread;
later turns resume that thread. The model, workspace files, installed tools,
saved preferences and persistent memory remain available.

## Workflow effect

Use `effect:session:reset-context` with these bindable string fields:

| Field | Meaning |
| --- | --- |
| `target` | The assistant session selected by the host's user mapping. |
| `conversation_key` | The same key used by prompt enqueue and resume metadata: `teams:<normalized-target>:agent_thread_id`. |
| `request_id` | A stable source-message identity, reused for retries. |
| `communication_target_id` | A directory-scoped Teams target readable by the selected session. |

The effect checks the workflow directory, session policy, configured target and
conversation key before recording the reset. It returns `success`, `session_id`,
`conversation_key`, `generation`, `pending` and `duplicate`. A duplicate returns
the original receipt without clearing a newer accepted thread.

Recording a reset clears only the corresponding `agent_thread_ids` entry.
Once a conversation has been reset, the context tables in
`runtime/agent-message-deliveries.db` own its resume pointer. Session metadata is
a compatibility mirror. Late completions from older generations cannot restore
their pointer. A canonical context row is added on the first reset; admitted
launches have separate receipts that preserve their original context.

The reset remains pending until a new native thread is observed. Admission,
worker acceptance without a native thread, failed launches and restarts do not
consume it. An uncertain steering delivery is reconciled against its original
worker receipt rather than sent again into a new thread. Admitted launches have
durable context receipts, including generation zero before a conversation's
first reset. Accepted or uncertain launches keep their original generation and
resume settings across retries. A structured worker subscription preflight
rejection confirms no job was created: release that launch's context binding
before releasing admission claims. Its queued request keeps the same delivery
identity and binds to the current generation on retry, capturing a new history
cutoff if a reset is pending. Generic failures and timeouts retain their binding.

## History

Both classification queries and default agent Teams reads respect the boundary.
Opt a `query:communication:messages` node into it by supplying
`context_session_id` and `conversation_key` together. This mode supports Teams;
other providers keep their existing query behavior.

While a reset is pending, classification receives no earlier chat history.
The running turn retains its existing history boundary. After earlier work
finishes, dispatch captures the new cutoff before starting the fresh turn.
Messages created before that cutoff are filtered before limits and pagination,
including old messages ingested later. Stored Teams messages are retained.

Queued requests can predate the cutoff: their full prompt remains queued and is
delivered explicitly. References that depended solely on discarded conversation
context may require clarification from the user.

## Preparing the personal-assistant workflow

`evander.workflow.context_reset.prepare_context_reset_pack` is an offline
transformation. It takes the existing pack plus graph/node identifiers and
trusted session, message, conversation, model, communication-target and language
bindings. It preserves operator user mappings and other graphs.

The candidate adds exact case-insensitive `!RESET` handling, English/Swedish
reset intent to the existing Decisions evaluation, the existing 70% confidence
threshold, localized host confirmations, failure routing and accompanying-task
handling. Quoted, negated, hypothetical and unrelated reset requests are
classified as ordinary conversation. Model changes run before resets; a
successful reset with another task continues exactly once even if confirmation
delivery fails. Reset-only requests do not enqueue an agent turn.

Prepare a candidate outside the repository:

```bash
python scripts/prepare_context_reset.py \
  --pack /path/to/operator/pack-snapshot.json \
  --bindings /path/to/operator/reset-bindings.json \
  --output /path/to/operator/reset-candidate.json
```

The bindings JSON contains the keyword arguments documented by
`prepare_context_reset_pack`. Include both the normal and busy enqueue nodes,
and both model-selection and reasoning history queries. Bind the current model
through the existing effective-model map. The transformer projects reset and
task fields through the model and request merge gates so branch-dependent query
outputs are never required after a merge. The output must differ from either
input. This command does not apply the candidate.

Deploy compatible control-plane code first. Read the latest workflow document
under the editor's lock/revision mechanisms, regenerate the candidate from that
document, compare validation diagnostics, review the graph delta, and save through
`WorkflowConfigAdminService.save_workflow_editor_document` with
`expected_revision`. Never copy the candidate over a live pack file. No new
worker protocol or guest capability is required.

Verify idle and busy cases using a synthetic assistant. Inspect reset receipts,
dispatch generations and native thread IDs; prove another assistant remains
unchanged. Real-provider intent evaluation and production activation are separate
from synthetic routing tests.

For rollback, disable new reset routing through the same revision-checked editor,
let pending resets complete while compatible code is still running, and preserve
context receipts and the new resume pointers.
