# Short browser objectives

Any authorized owning agent can use `browser_session_run` for a short multi-step
objective in its existing shared browser. Jev or OpenAI Decisions selects
intermediate actions from current controls and application-built arguments. The
owning agent keeps the task and conversation, supplies exact text values, and
verifies the final result. No
application-specific prepared workflow or extra support agent is required.

## Enable after qualification

Deploy Browser Sessions 0.8.1 for the updated decision questions and operating
guide. Fast execution requires a hosted controller supporting capability version
1, Chrome extension 0.5.0 or later, or native helper 0.4.0 or later when selected.
This server-side correction does not require an extension or helper update.
Connections advertise `fast_execution: 1`; old endpoints keep their direct
tools and return `endpoint_upgrade_required` for a run.

In the directory's Browser Sessions settings, enable **Faster browser actions**.
Configure and enable the directory's classification provider (Jev or OpenAI
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

## Agent guidance

The add-on packages its complete [shared browser operating guide](../evander_browser_sessions_addon/agent-guide.md).
Profile discovery and session creation return it as `agent_guidance`, including
the launch-manifest schema. Any owning agent can retrieve it again through
`browser_session_status` with `include_guidance: true`. Ordinary status polling
omits it. No personal-assistant workflow or AGENTS.md prompt is required.

Use this tool automatically for a short objective needing several UI actions.
Use the existing direct tools for a single known click or entry. Split larger
requests into bounded objectives, retaining domain context in the owning agent.
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
    "max_steps": 12,
    "timeout_seconds": 20
  }
}
```

The model can choose click, hover, fill, character typing, bounded keyboard
navigation, scrolling, waiting, done or help. It cannot generate text, scripts,
selectors, coordinates, URLs or new values. Missing data, unsupported surfaces,
truncation, ambiguous decisions and repeated lack of progress return to the owning agent.
Dropdowns use the existing click/key paths. Rich observations include current
field state, surrounding row/form/dialog context and bounded page text. The
human's stream and pointer continue; intermediate observations omit images.

A `needs_verification` receipt is not proof of success. Verify the returned actual
page state before reporting success. If `observation_current` is false, or a
retry receipt has no observation, obtain a fresh observation first. `needs_pa`
is the historical API name for returning to the owning agent, regardless of its
role; inspect and decide the next step. Never blindly repeat a submission after
`outcome_unknown`.

## Stop, retries and recovery

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
