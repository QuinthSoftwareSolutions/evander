# Classification

Classification is a core Evander capability for evaluating supplied input against named
questions. Jev, hosted by TypeSafe, is the first provider. Workflows and agents use the same
service, provider registry, directory configuration, validation, and usage recording.
The reserved `openai_decisions` provider prepares this same service for OpenAI Decisions;
its transport remains unavailable until a supported API contract is implemented.

## Decisions preparation

OpenAI [announced Decisions API on September 29, 2026](https://openai.com/index/devday-2026-recap/)
in limited preview, with broader release planned in the following days. The announcement
describes text/image context and questions with finite, predefined answers. It does not publish
an endpoint, model identifier, request/response schema, probability contract, or pricing.

Evander reserves provider ID `openai_decisions` alongside Jev across Classification evaluate,
Smart switch, agent tools, and Admin Web sample evaluations. Its provider settings default to
disabled, `api_key_env: "OPENAI_API_KEY"`, and `default_model: null`. That environment-variable
name is an Evander host configuration choice, not a claim about the unpublished API's auth
contract. Jev remains the directory default. Admin Web shows the pending integration and lets
operators prepare disabled profiles without copying Jev's model or credential setting.
Unavailable profiles are excluded from agent profile discovery. Enabling the provider through
advanced configuration still fails before secret resolution or outbound dispatch.

The internal finite-answer mapping preserves choice IDs, maps score rubric levels to string
indices (`"0"`, `"1"`, ...), and maps booleans to `"true"`/`"false"` selections. These are
Evander's adapter objects, not an OpenAI HTTP payload. A selection becomes `choice`, a discrete
rubric `score`, or a boolean `value`. It never creates confidence, probability distributions,
or `probability_true`. Jev keeps its existing fractional scores and boolean probabilities.
Workflows using `probability_true` must keep a probability-producing provider or explicitly
change their bindings to consume `value`. A Smart switch without a cutoff can consume finite
choices; a probability cutoff is rejected before calling a provider known not to supply them.

To activate Decisions after documentation and access become available:

1. Implement its fixed host-side endpoint, authentication, schema translation, bounded requests,
   responses, retries, and usage parsing using the published contract. Reuse the internal finite
   mapping where it fits. Verify probability support before changing the capability flag.
2. Set the documented model and integration availability only after adapter conformance tests
   pass. Keep provider/model snapshots, privacy dispatch checks, native OpenAI usage attribution,
   and host-owned credentials on the existing shared service path.
3. Provision host credentials and complete an explicit live check with synthetic input, then
   enable the provider and selected directory profiles through revisioned Admin Web/API saves.
   Check downstream probability bindings and cutoffs before switching existing Jev profiles.

Current automated coverage exercises the shared paths with a synthetic finite provider; it
does not establish OpenAI access, latency, accuracy, billing, or a live Decisions integration.
Image transport will be added once its documented input representation is known; the current
public classification input remains text/JSON.

## Enable and configure

Classification is disabled by default. In Admin Web, select the directory, open
**Configuration → Models, Assistant & Knowledge → Classification**, and unlock privileged
operations. Configure the provider and enable classification. The credential setting is an
environment-variable name, not a secret value. Provision `TYPESAFE_API_KEY` through the existing
host secret/environment mechanism for the gateway, effect orchestrator, and Admin Web processes
that will call Jev, then restart those processes if their environment changed. No additional
service, guest port, guest credential, or guest internet access is needed.

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
be repeated and billed again because Jev does not expose a request-idempotency contract.
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
resolved directory; pending providers are excluded from discovery. Evaluation
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

Provider contract references: [OpenAI Decisions announcement](https://openai.com/index/devday-2026-recap/),
[TypeSafe API](https://docs.typesafe.ai/api),
[models](https://docs.typesafe.ai/models), and [confidence](https://docs.typesafe.ai/confidence).
