# Classification

Classification is a core Evander capability for evaluating supplied input against named
questions. Jev, hosted by TypeSafe, is the first provider. Workflows and agents use the same
service, provider registry, directory configuration, validation, and usage recording.

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
| `boolean` | Optional `criteria` descriptions under `true` and `false` | `probability_true`: number from zero to one |

Every question has `instructions`, which may be text or structured JSON. Answer maps use the
same question IDs as the request. Jev returns `confidence` and `probabilities` for choice and
score answers; other providers may omit them. Confidence is a separate provider metric, not the
winning option's probability. Boolean probabilities are not thresholded by the service. Choose
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
`${{classify.answers.urgent.probability_true}}` in downstream gates or effects. The editor offers
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

## Agents

Grant `classification_evaluate` and, optionally, `classification_list_profiles` through ordinary
session tool grants. Tools are available to any granted agent. No grants are added automatically.
Profile listing is restricted to enabled profiles in the caller's resolved directory. Evaluation
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

Provider contract references: [TypeSafe API](https://docs.typesafe.ai/api),
[models](https://docs.typesafe.ai/models), and [confidence](https://docs.typesafe.ai/confidence).
