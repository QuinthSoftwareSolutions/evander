# Short browser objectives

Any authorized owning agent can use `browser_session_run` for a short multi-step
objective in its existing shared browser. A guarded `plan` executes actions already
chosen by the owner without a decision provider. Without a plan, Jev or OpenAI
Decisions selects intermediate actions from current controls and application-built arguments. The
owning agent keeps the task and conversation, supplies exact text values, and
verifies the final result. No
application-specific prepared workflow or extra support agent is required.

## Enable after qualification

Deploy Browser Sessions 0.9.2 for control-aware decision questions and the operating
guide. Fast execution requires a hosted controller supporting capability version
1, Chrome extension 0.5.0 or later, or native helper 0.4.0 or later when selected.
Use Chrome extension 0.6.2 for the native-picker mouse-down/mouse-up correction in
local sharing, including when using native helper 0.4.0. Hosted controllers receive
the same correction from the updated server package/image. The decision questions
and diagnostics are control-plane changes; the native helper needs no update.
Connections advertise `fast_execution: 1`; old endpoints keep their direct
tools and return `endpoint_upgrade_required` for a run.

In the directory's Browser Sessions settings, enable **Faster browser actions**.
For objectives without a plan, configure and enable the directory's classification provider (Jev or OpenAI
Decisions) through the existing provider settings and host-owned credential.
The loop inherits that provider and its model. It never changes provider on failure.
Existing dispatch privacy policy still applies before each provider request.

Grant `browser_session_run`, `browser_session_cancel_run`,
`browser_session_observe` and `browser_session_act` to the owning agent. Neither
enabling the add-on nor deploying this release changes live tool grants. Local
hosts must still grant input; hosted viewers still require Admin sign-in.

Settings default to:

```json
{
  "fast_execution": {
    "enabled": false,
    "max_steps": 20,
    "timeout_seconds": 30,
    "min_probability": {"jev": 0.8, "openai_decisions": 0.8}
  }
}
```

Administrators can raise the caps to 40 steps and 60 seconds. Requests can lower
them. Each decision has at most five seconds, bounded further by the remaining
run budget and provider timeout. Automatic provider retries are disabled.
The selected option's probability must meet the threshold for every relevant
choice; vendor confidence fields do not override that check.

Operation descriptions distinguish ordinary field replacement from requested
character-key typing and dropdown keyboard interaction. Each target advertises
its allowed actions; missing literals, readonly fields and non-editable
comboboxes cannot become fill/type commands. The server checks the selected
action against the target before dispatch in addition to endpoint revalidation.
The platform uses no application-specific names, label rules or prepared workflows.

Operation, target, supplied input, key and scroll direction remain separate
questions batched in one provider request. Only choices needed by the selected
operation are scored. Waiting, readiness for verification and assistance do not
need a target or input. Every conditional question includes an explicit
no-operation option. Controls occur once in `page.elements`; their context IDs
refer to shared `page.contexts`, and target choices refer back to those controls.
On larger pages, up to 100 ordinary controls remain in view and overflow controls
and rows/cells are exposed as regions of at most 50 targets. The model can choose
`inspect` with a listed region to reveal its targets on a fresh observation,
without sending browser input. Omitted targets are counted explicitly, so a
projected view cannot establish absence. The complete observation remains the
authority for input validation and the returned page. Truncated observations or
a decision payload exceeding 64 KiB yield to the owning agent.

When a relevant choice is below threshold, the durable receipt includes a
`decision` object with `question`, `choice`, `probability`, `threshold` and at most
three `alternatives` (`choice`/`probability`). These contain bounded option IDs,
never option descriptions, page labels, screenshots or supplied text values.
Target choices can include `target_ref`, the original endpoint reference
associated with that model-local option. An implicit keyboard target is resolved
only from a uniquely focused observed control and is sent as an explicitly
referenced, revalidated command.
Identical retries and session status retain those diagnostics for the owning
agent. A confidence failure still yields before input; probabilities are neither
combined across alternatives nor relaxed.

Decision evidence contains semantic page state, snapshot-local target aliases and
completed action outcomes. The server alone maps aliases back to original endpoint
references; prior actions whose controls are no longer present retain their action
and outcome with a previous-target marker. Random observation/command receipt IDs,
document epochs and shared-page IDs stay in the execution and accounting paths.
The original observation IDs, generations and target checks still fence every
browser command.
Native mouse-down may change its own control's mutable selection/expansion before
mouse-up. That transition is permitted within the click; other form changes and
target identity, document, origin and generation changes still stop further input.

## Agent guidance

The add-on packages its complete [shared browser operating guide](../evander_browser_sessions_addon/agent-guide.md).
Profile discovery and session creation return it as `agent_guidance`, including
the launch-manifest schema. Any owning agent can retrieve it again through
`browser_session_status` with `include_guidance: true`. Ordinary status polling
omits it. No personal-assistant workflow or AGENTS.md prompt is required.

Use this tool automatically for a short objective needing several UI actions.
Use the existing direct tools for a single known click or entry. Split larger
requests into bounded objectives, retaining domain context in the owning agent.
Use current visible control names, explicit ordering dependencies and the expected
result. Describe the next local objective, letting the model select intermediate
actions from current page state; no prepared application workflow is required.
Resolve domain choices and author text, such as form instructions, before calling.
Supply every exact literal in `request.values`, with meaningful descriptions;
text in `goal` alone cannot be entered. Include an empty-string literal to clear
a field. An empty values map is appropriate only when no text entry is needed.
Never put secrets in the goal; mark private values `sensitive: true`. The decision model sees their
labels rather than their literals. Observations and history sent for decisions
mask those literals; action receipts omit all entered text. Password values in
accessibility observations remain masked. The gateway records tool decisions and
correlation metadata for this tool without persisting its input/output payloads.

Example arguments for `browser_session_run`:

```json
{
  "browser_session_id": "SESSION_ID",
  "request": {
    "request_id": "create-example-account",
    "generation": 4,
    "goal": "Create an account using the supplied account name, then stop for verification",
    "values": {"name": {"description": "New account name", "value": "Example"}},
    "completion": {"kind": "present", "target": {"role": "alert", "name": "Account saved"}},
    "max_steps": 12,
    "timeout_seconds": 20
  }
}
```

The model can choose click, hover, fill, character typing, bounded keyboard
navigation, scrolling, region inspection, waiting, done or help. It cannot generate text, scripts,
selectors, coordinates, URLs or new values. Missing data, unsupported surfaces,
truncation, ambiguous decisions and repeated lack of progress return to the owning agent.
Dropdowns use the existing click/key paths. Rich observations include current
field state, surrounding row/form/dialog context and bounded page text. The
human's stream and pointer continue; intermediate observations omit images.

## Completion conditions and checkpoint evidence

Supply `request.completion` with the observable result of each bounded objective.
It uses the same typed predicates as checkpoints, without an action trigger.
For example, `{"kind":"present","target":{"role":"heading","name":"Example editor"}}`
can identify a destination after navigation without depending on old element
refs. The controller checks full fresh observations before deciding and after
input, including the last budgeted action, and stops for owning-agent verification
when the predicate holds. No separate model decision about `done` is needed.
The condition may already hold initially, but cannot bypass required checkpoints.
In plan mode it also cannot skip any explicit planned action.
Choose a predicate that distinguishes the requested result from an intermediate
state. Explicitly use `null` only when the result is not expressible or a final
stopping checkpoint is used. A new request that omits both completion and a
stopping checkpoint is rejected before observing or acquiring an automation
lease. Requests for already-saved receipts retain their earlier retry identity.
Model `done` cannot override an unsatisfied completion condition.

The receipt's optional `completion` evidence contains only the condition kind,
status, observation/page identities, time and matched counts/references. It does
not persist predicate text, target names or literal values. Identical retries
return that receipt without repeating input. Older saved-run retries remain compatible.

An optional ordered `request.checkpoints` list binds observation predicates to
completed actions. A checkpoint fires once after its matching action and blocks
all later input until its condition holds. For example, after filling a known
field, require its value to equal the exact supplied literal:

```json
{
  "id": "name-entered",
  "after": {"action": "fill", "ref": "node-123", "input": "name"},
  "condition": {"kind": "value", "target": {"ref": "node-123"}, "input": "name"},
  "timeout_seconds": 5
}
```

Use actual observed references and the keys of `request.values`, not model-local
aliases. Fill/type triggers require an input ID and can constrain the target ref;
click/key triggers require a ref, and key triggers also require the exact key.
Conditions support unique element presence, element absence, exact supplied
values, explicit checked/expanded/disabled state, positive page-text matching,
and bounded counts of matching observed elements. Targets match an observed ref
or role, optionally with an exact name and surrounding context name. Multiple
matches for a unique-element condition return assistance. Missing state is not
evidence of a false boolean. Only roles exposed by the snapshot are accepted;
an unsupported role cannot establish absence or a zero count. Exact-value checks
cannot establish equality at the 1,000 UTF-16-unit clipping boundary; use another
application result predicate when the value may be clipped.
Counts include all matching elements, including
header rows; they do not infer application data-row semantics. Use a known text
sentinel or direct verification when the observation cannot express the condition.

The controller obtains fresh, text-only observations and checks the predicate,
polling at most every 200 ms between completed observations without calling the
decision model. Each checkpoint has a default five-second timeout (maximum 15),
bounded by the run deadline. There can be at most eight checkpoints. Truncation,
ambiguity, timeout or loss of authority stops further input. A later checkpoint
cannot fire before an earlier one, and `done` cannot bypass an unreached check.
Ordinary `wait` without a triggered checkpoint keeps its existing model-guided
behavior.

Read-only observations invalidated by navigation (`stale_observation`) get up to
three attempts with a short bounded delay and fresh authority/deadline checks.
This applies inside checkpoint waits too. Other errors and uncertain input still
yield without replay; repeated stale reads remain bounded by the run deadline.

Set `stop_after: true` on the final checkpoint when it defines the end of this
bounded objective. Once observed, the run returns `needs_verification` without
another model decision about `done`. It cannot bypass any earlier checkpoint and
does not establish success beyond the stated predicate. Other checkpoints default
to `stop_after: false`, allowing the model to select further actions.

Receipts persist checkpoint IDs, outcomes, observation/page identities, times,
observation counts and matched counts/references where relevant. They omit
condition descriptions, page text and literal values. This preserves evidence
of intermediate states that may disappear before the final observation, and
identical retries return it without replay. A satisfied checkpoint establishes
only its stated predicate; the owning agent still verifies the complete goal.

A `needs_verification` receipt is not proof of success. Verify the returned actual
page state before reporting success. If `observation_current` is false, or a
retry receipt has no observation, obtain a fresh observation first. `needs_pa`
is the historical API name for returning to the owning agent, regardless of its
role; inspect and decide the next step. Never blindly repeat a submission after
`outcome_unknown`.

## Stop, retries and recovery

### Guarded owner-authored plans

Use `request.plan` for one to eight known actions grounded in the owner's latest
`request.observation_id`. See the complete [plan example and usage guidance](../evander_browser_sessions_addon/agent-guide.md#batch-actions-already-grounded-in-the-current-page).
Plans execute through the existing run/command path with a maximum five-second
lease, directory caps, current grants and endpoint capability checks. They bypass
classification configuration, credentials and inference. All targets must exist
and resolve uniquely before any input; fill/type use named `values`, keys are an
enumerated set, and scrolling is bounded. There is no scripting or navigation API.

Observation completion atomically records a digest of bounded observed semantic
state with its actor, generation and time. The claim consumes that grounding
record atomically with the run lease. An intervening command, expired observation,
incomplete snapshot or missing page identity prevents a new plan. A fresh initial
observation must match the owner's digest; subsequent observations fence document,
URL, target identity and all observed controls/context. Exact fill/append outcomes,
deterministic click transitions and explicit satisfied value/state checkpoints
can update only the touched target's expected state. Unexpected changes return
for owner inspection. These checks describe observable state, not hidden app data.
Current endpoints include an engine-instance page identity; hosted controllers
use their existing shared-page identity. Older endpoints without `page_id` or
`page_epoch` return `batch_identity_required` and keep direct-tool access.

Plans support ordered checkpoints within the same total deadline, but reject
`stop_after`. Completion is checked after all planned actions and checkpoints.
The controller cannot infer generic application submission boundaries; the owner
must explicitly choose any submit action and verify it. Prefer separate preparation
and final commitment when review is required.

Input is written to the command ledger before dispatch. Command completion and
per-step receipt metadata commit together. A crash between submission and durable
completion yields an unknown input outcome even if the input was never sent.
Terminal settlement retires submitted commands atomically, preserves confirmed
progress and prevents late callbacks from rewriting settled receipts. After process
restart, identical retries only read the receipt; the existing lease remains until
its original deadline. No process takes over or resumes the plan. Request IDs
with changed arguments conflict. New request IDs require fresh owner observation
and explicit decisions about any already-applied input.

### Shared lease behavior

One automation lease covers the run's observations and actions. Competing agent
commands are rejected while it is running. Status, chat, streaming and human
controls remain available. Ordinary Teams/browser follow-ups are handled by the
owning agent after this short tool call. Stop, Pause, Take control, closure and expiry
interrupt input. A stop pauses agent control and does not authorize automatic
resumption; wait for a new user instruction.

`browser_session_cancel_run(browser_session_id, run_id)` is safe to repeat. The
viewer and extension offer **Stop current task** while a run is active. Their
Stop action atomically targets the currently active run and pauses input, even
when the displayed state is stale. Named MCP cancellation affects only its run.
Generation and deadline fences apply between input primitives, including in the helper.
Already issued input cannot be undone; its result may be uncertain.

The same request ID and identical arguments return the existing receipt without
executing another run. Changed arguments with the same ID are rejected. A process
restart does not resume a run; its lease expires at the recorded deadline, and
status exposes interruption or uncertain commands. Durable command completion and
run-step metadata are recorded together. Receipts follow session retention and
do not retain screenshots, page observations or literal input values.

## Qualification and benchmarks

Run the add-on Python, browser and helper suites before deployment. They use
synthetic pages and mocked decision responses, exercising the real provider
adapters without consuming API credits. Live provider speed is a separate check.

Use `scripts/benchmark_fast_execution.py --help` for the opt-in paired benchmark.
An operator-supplied agent driver runs identical cases using direct tools, Jev and
OpenAI Decisions. It must reset the synthetic application between runs, configure
only the requested strategy, read actual final application state independently
of the agent's claim, and return usage telemetry. The harness verifies expected
state and reports success rate, median/p95 time, model/browser calls and cost.
Keep automatic use opt-in until matched runs show lower multi-step latency
without reducing completion rate. Never substitute a scripted click sequence
for the full agent direct-tools baseline.

The driver is an explicit command, executed without a shell:

```bash
python3 scripts/benchmark_fast_execution.py --output /operator-owned/browser-benchmark.json --repeats 5 -- /operator-owned/run-pa-benchmark
```

Its stdin is one JSON object containing `case_id`, `goal`, `values`, `strategy`,
`iteration`, `reset`, and `limits`. `direct` requires the complete agent turn using
observe/act tools; the two provider strategies require the same agent task with
`browser_session_run` and that directory provider. Include agent verification in all
three. Start timing consistently; the harness measures the complete driver call.
The driver's single JSON stdout result contains `verified_state`, `model_calls`,
`browser_calls` and `cost_usd`. Logs belong on stderr. Consult `CASES` in the script
for initial fixture expectations and expected final persisted states; do not
provide expected states to the agent as evidence of success. The harness compares
observed state independently and stores only timings, outcomes and aggregate
usage. Run only against disposable fixture data, using operator-owned driver
configuration. No real provider benchmark is part of the default test suite.
