# Classification

Classification is a core Evander capability for evaluating supplied input against named
questions. Jev, hosted by TypeSafe, and OpenAI Decisions use the same service, provider
registry, directory configuration, validation, and usage recording across workflows and agents.

## OpenAI Decisions

The `openai_decisions` provider implements the published
[Decisions API](https://developers.openai.com/api/docs/guides/decisions) through the fixed
`POST https://api.openai.com/v1/decisions` endpoint with host-owned Bearer authentication.
The API is in public beta; `gpt-6-luna` is its currently supported model. The adapter uses HTTPX
and does not require an OpenAI SDK upgrade. Its integration is available in the provider catalog,
while provider enablement defaults to `false`. Jev remains the directory default.

To use Decisions, provision `OPENAI_API_KEY` through the existing host secret/environment
mechanism for every gateway, effect orchestrator, or Admin Web process that will call it.
Restart those processes if their environment changed. Select the directory in Admin Web,
unlock privileged operations, and enable Decisions through Classification configuration.
Choose it for individual profiles or inline calls, or set it as the directory default.
Existing preparation configurations with `default_model: null` must select `gpt-6-luna`.
Live configuration saves use the existing revision checks.

A minimal directory configuration is:

```json
{
  "enabled": true,
  "default_provider": "openai_decisions",
  "providers": {
    "openai_decisions": {
      "enabled": true,
      "default_model": "gpt-6-luna",
      "api_key_env": "OPENAI_API_KEY"
    }
  }
}
```

Questions are sent as an ordered array with their Evander IDs in `name`:

| Evander question | Decisions request | Normalized answer |
| --- | --- | --- |
| `choice` | `choice` with named `choices` | `choice`, `confidence`, and option `probabilities` |
| `score` | `score` with ordered `levels` labeled `"0"`, `"1"`, etc. | Fractional `score`, `confidence`, level `probabilities`, and the original `legend` |
| `boolean` | `predicate`; optional boolean criteria become part of its instructions | `probability_true`, preserving the native probability without thresholding it |

Scores are probability-weighted averages of level indices, not discrete level selections.
Smart switch probability cutoffs use the selected choice's probability; confidence remains a
separate metric. A refusal for any question fails the evaluation through the ordinary provider
error path, so no partial result or fallback branch is produced. Malformed names, types,
probability distributions, score indices, and token usage fail without retry.

Text state becomes the shared `input` string. JSON state, structured instructions, and option
or rubric descriptions are serialized as JSON text. The shared classification input currently
supports text/JSON only: although OpenAI also supports inline image data URLs, this adapter does
not send image parts or fetch image URLs. Files, audio, tools, and conversation roles are not
forwarded to Decisions.

The adapter shares the existing bounded transport: request/response byte limits, a total deadline,
transient retries, privacy checks before every attempt, and redirects disabled. Usage is recorded
under native provider `openai`, endpoint `decisions`, and the configured host credential name.
Native token details are retained without recording input or answers in usage metadata. Configure
cost estimates separately through the model pricing catalog. The bundled seed includes a rule
scoped to `provider_endpoint_identifier: "decisions"` with $0.10 per million uncached input tokens
and zero cache-read, cache-write, and output-token charges. Endpoint-specific rules take priority
over generic model rules. Existing operator catalogs require adding that rule through revisioned
Admin Web/API saves; source changes do not overwrite a customized runtime catalog. See the
[official pricing guide](https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability).

To check an enabled Decisions provider explicitly without changing the directory default:

```bash
./.venv/bin/python scripts/smoke_classification.py --live --provider openai_decisions --runtime-dir runtime --directory-path root
```

This makes one billable request with synthetic input and does not edit configuration or run
workflows. Automated coverage uses HTTP transports to exercise the real adapter through Admin Web,
agent tools, workflow queries, and Smart switches. It does not establish live account access,
latency, or classification accuracy.

## Enable and configure

Classification is disabled by default. In Admin Web, select the directory, open
**Configuration → Models, Assistant & Knowledge → Classification**, and unlock privileged
operations. Configure the provider and enable classification. The credential setting is an
environment-variable name, not a secret value. Provision `TYPESAFE_API_KEY` through the existing
host secret/environment mechanism for the gateway, effect orchestrator, and Admin Web processes
that will call Jev, or `OPENAI_API_KEY` for Decisions. Restart those processes if their
environment changed. No additional service, guest port, guest credential, or guest internet
access is needed.

Example `classification` field in the directory's providers configuration:

```json
{
  "enabled": true,
  "default_provider": "jev",
  "providers": {
    "jev": {
      "enabled": true,
      "default_model": "jev-1.13.0",
      "api_key_env": "TYPESAFE_API_KEY",
      "timeout_seconds": 30,
      "max_retries": 2,
      "max_request_bytes": 1048576,
      "max_response_bytes": 2097152
    }
  },
  "profiles": {
    "triage": {
      "label": "Message triage",
      "description": "Choose a team and estimate urgency.",
      "enabled": true,
      "provider": "jev",
      "model": "jev-1.13.0",
      "questions": {
        "route": {
          "type": "choice",
          "instructions": "Choose the team that should handle this message.",
          "criteria": {"support": "An existing product needs help", "sales": "A prospective purchase"}
        },
        "severity": {
          "type": "score",
          "instructions": "Estimate the operational impact.",
          "criteria": ["Minor inconvenience", "Work impaired", "Work stopped"]
        },
        "urgent": {"type": "boolean", "instructions": "Does this need immediate attention?"}
      }
    }
  }
}
```

Use Admin Web/API saves for live configuration. Saves require the revision returned by
`GET /api/classification?directory_path=root/team`; stale saves return `409` and retain the
editor's draft. Parent changes invalidate the revision too. Provider settings inherit field by
field. A child profile replaces the entire inherited profile with the same ID, including its
questions and criteria. `{ "enabled": false }` disables an inherited profile; removing the local
override restores inheritance. Removing a root profile removes it from children without their
own override. Neither enabling classification nor creating profiles grants tools to sessions.

The structured editor supports questions, stable IDs, choice options, ordered score rubrics,
and optional boolean criteria. Advanced JSON preserves structured instructions and criteria.
**Test with sample input** evaluates draft questions or a selected profile with saved provider
settings. It incurs model usage, returns the answer, probabilities, confidence, usage, and
latency, and does not save the sample or execute downstream workflow nodes. Save provider
enablement/settings before testing them.

## Request and result contract

An evaluation takes `state` (text, JSON object, or JSON array) and exactly one of `questions` or
`profile_id`. Put both task context and the input to classify in `state`. Questions are evaluated
independently against the same state. Inline calls can select `provider` and `model`; otherwise
directory/provider defaults apply. Profile calls cannot override their provider, model, or
questions.

| Question type | Definition | Answer |
| --- | --- | --- |
| `choice` | At least two named options in `criteria` | `choice`: one of the supplied option IDs |
| `score` | At least two ordered levels in `criteria` | `score`: number from zero to the last level index; can be fractional; `legend` maps indices to levels |
| `boolean` | Optional `criteria` descriptions under `true` and `false` | `value`: boolean for finite-answer providers, or `probability_true`: number from zero to one for probability-producing providers |

Every question has `instructions`, which may be text or structured JSON. Answer maps use the
same question IDs as the request. Jev returns `confidence` and `probabilities` for choice and
score answers; other providers may omit them. Confidence is a separate provider metric, not the
winning option's probability. Discrete boolean values are not converted to probabilities.
Boolean probabilities are not thresholded by the service. Choose
thresholds explicitly in the consuming workflow or agent. There is no automatic model fallback.

The result also includes `success`, `provider`, upstream `model`, `usage.input_tokens`,
`usage.output_tokens`, `duration_ms`, and nullable `profile_id` / `profile_revision`. Jev's native
`noul` question/answer is mapped to the public `boolean` / `probability_true` contract.

## Workflows

Add the built-in **Classification evaluate** query (`query:classification:evaluate`), bind its
state to upstream data, and select a profile or edit inline questions. A persisted node is:

```json
{
  "id": "classify",
  "display_name": "Classify incoming message",
  "type": "query:classification:evaluate",
  "config": {
    "state": "${{incoming.message}}",
    "profile_id": "triage"
  }
}
```

Use `${{classify.answers.route.choice}}`, `${{classify.answers.severity.score}}`, and
`${{classify.answers.urgent.probability_true}}` in downstream gates or effects. Use
`${{classify.answers.urgent.value}}` for providers that return a discrete boolean.
Only bind fields that your selected provider actually returns. The editor offers
known answer fields as binding suggestions. After a saved profile changes, use **Refresh answer
suggestions** to update that node's hints. Dynamic definitions retain the generic `answers`
object contract. Use simple question IDs such as `route` or `is_urgent` for convenient bindings.
See [the example workflow](examples/classification-triage.json) for a cron-triggered demonstration
with a filter and map; importing it does not enable the provider.

Before a provider request, the effect orchestrator persists the resolved state, questions,
provider, model, and profile revision in the query execution record. Restarting a running query
uses that snapshot. Completed results, including pending completion publication, are reused.
Directory/provider disablement is rechecked on execution. An interrupted in-flight request can
be repeated and billed again; neither adapter assumes an upstream request-idempotency contract.
Provider/configuration errors take the ordinary query failure path; they are not converted to a
classification choice.

## Smart switch

**Smart switch** (`gate:classifier:switch`) selects a workflow branch directly using
classification. In the workflow editor, bind **Input and context**, enter routing instructions,
describe each branch, and choose a default port. The gate builds a single Choice question from
those branches, including the default. Provider and model settings inherit from the graph's
directory unless overridden on the node. Branches are defined in the node, not in a profile.

```json
{
  "id": "choose-level",
  "display_name": "Select assistant level",
  "type": "gate:classifier:switch",
  "config": {
    "state": {"message": "${{incoming.message}}"},
    "instructions": "Select level 2 only for a current explicit request to use it. Questions, quotations, and negated requests continue normal chat.",
    "cases": [{"port": "terra", "label": "Level 2", "description": "Requests assistant level 2"}],
    "default_port": "standard",
    "default_description": "No clear request to change to level 2"
  }
}
```

Connect downstream nodes using the branch's `source_port`. Port IDs must be unique, static,
nonempty, and distinct from `error`. At least one described branch and a distinct default are
required. The selected branch receives the original upstream payload; classification results
do not replace or add fields to it. The default description may be omitted, in which case it
means “None of the described routes clearly applies.”

The optional `min_probability` cutoff is off by default. With it enabled, a selected option's
probability below the cutoff routes to the default; equality passes. This uses the option's
probability, not the separate confidence metric. Missing probability data with a cutoff enabled
is an error. Provider/configuration failures use `error` when connected, otherwise the node fails;
they do not become a default classification.

**Test with sample input** replaces the bound state with your sample and shows the selected
choice, routed port, routing reason, probabilities, usage, and latency. It uses the same routing
logic as execution and incurs provider usage without saving or running downstream nodes.
The API is `POST /api/classification/switch/evaluate?directory_path=root`, with body
`{"config": { ...node configuration with literal sample state... }}`. It requires directory owner
access and unlocked privileged operations, like other classification tests.

Smart switch calls run on the worker executor so unrelated workflows can continue. Execution
history retains the prepared input, branches, cutoff, model, trusted directory, and configuration
revision before the call. Restart resumes that snapshot and rechecks directory/provider enablement.
The decision is stored under `resolved_config.decision`, including `selected_choice`,
`selected_port`, and `routing_reason` (`choice`, `default`, or `below_probability`). Completed
decisions and pending completion publications are reused. An interrupted upstream call can be
repeated and billed again. Usage records attribute attempts to the workflow directory and node.

The [disabled assistant-level example](examples/classifier-level-switch.json) routes levels 1–4
to `luna`, `terra`, `sol`, and `astra`, unsupported level requests to `invalid`, and other messages
to `standard`. Its downstream nodes only expose the route. It does not change an assistant or
enable classification. The [labeled English/Swedish samples](examples/classifier-level-samples.json)
include commands, natural requests, questions, negation, quotations, and unsupported levels.

To evaluate that corpus against your configured provider explicitly (one billable request per sample):

```bash
./.venv/bin/python scripts/smoke_classifier_switch.py --live --runtime-dir runtime --directory-path root
```

Mocked tests verify routing and recovery, not the accuracy of the provider on these language examples.

## Agents

Grant `classification_evaluate` and, optionally, `classification_list_profiles` through ordinary
session tool grants. Tools are available to any granted agent. No grants are added automatically.
Profile listing is restricted to enabled profiles with available integrations in the caller's
resolved directory; unavailable or disabled providers are excluded from discovery. Evaluation
also uses the authenticated session's directory; callers cannot choose a different directory or
supply provider credentials.

```json
{"state": {"message": "The export stopped working"}, "profile_id": "triage"}
```

The same request can be made inline by replacing `profile_id` with a `questions` map. An agent
can inspect choice probabilities or boolean probability before deciding what to do next.

## Provider behavior, usage, and verification

The Jev adapter calls the fixed `https://api.typesafe.ai/v1/systemone` endpoint with host-owned
Bearer authentication. It does not follow redirects or expose arbitrary provider URLs. The
default model is pinned to `jev-1.13.0`; explicitly choosing a moving model alias makes model
behavior subject to upstream updates. Jev currently accepts text/JSON, not image/audio input.

The default 30-second deadline covers all attempts and backoff. Only connection failures,
HTTP 429, and HTTP 5xx are retried, at most twice by default. `Retry-After` is honored within
the deadline. Malformed JSON, invalid answer IDs, out-of-range numbers, invalid distributions,
oversized payloads, and other HTTP errors fail without an automatic fallback. Error messages
exclude upstream bodies and credentials.

Each HTTP attempt produces a model usage event: successful calls include native token usage;
failed calls record unavailable usage when the provider did not return it. Agent calls carry
session and active dispatch correlation; workflow calls carry directory, workflow/trace, and
node IDs without inventing a session. Usage/audit metadata excludes raw state and answers.
Workflow execution records do retain resolved inputs and answers for replay and debugging.
Configure the model pricing catalog separately if cost estimates are needed.

Automated tests use fake providers and HTTP transports. An explicit live check is available:

```bash
./.venv/bin/python scripts/smoke_classification.py --live --runtime-dir runtime --directory-path root
```

This makes a billable request with synthetic input using the selected directory's saved settings;
it neither edits configuration nor runs workflows. Enable/configure the directory first.

Provider contract references: [OpenAI Decisions guide](https://developers.openai.com/api/docs/guides/decisions),
[Decisions API reference](https://developers.openai.com/api/reference/resources/decisions/methods/create),
[TypeSafe API](https://docs.typesafe.ai/api),
[models](https://docs.typesafe.ai/models), and [confidence](https://docs.typesafe.ai/confidence).

## Smart switch cost attribution

`gate:classifier:switch` accepts a bindable `cost_category` string in its node
configuration. This labels provider usage even when a switch runs before an
agent session exists. Omit it or use null to retain the supplied attribution;
use an empty string to explicitly select Uncategorized. Surrounding whitespace
is trimmed. The prepared gate snapshot retains the resolved category across
queueing and restarts, so later configuration changes affect new gate calls.
The label is host bookkeeping and is not sent as classification input.

This setting changes future usage attribution only. Historical corrections use
`ModelUsageAdminService.set_inference_cost_categories` with exact event IDs,
expected old categories, and a bounded time range; billing provenance and token
counts remain unchanged. Agent session and prompt categories remain separate.
