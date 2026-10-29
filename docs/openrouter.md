# OpenRouter for Codex sessions

OpenRouter is an optional metered model provider behind Evander's existing host
relay. It supports Codex on libvirt VMs and container workers. Public OpenAI
remains the default and `openrouter_model_routes` defaults to an empty object.
Installing this feature does not enable OpenRouter or change existing sessions.

## Configuration

In Admin Web, use Configuration → Models, assistant and knowledge → LLM providers
to save the directory's settings through the revision-checked configuration API.
These fields belong to the root or child directory's `providers` domain:

| Field | Default | Meaning |
|---|---|---|
| `model_provider_mode` | `openai_public` | Select `openrouter` for the directory, or use a session override. |
| `openrouter_api_base_url` | `https://openrouter.ai/api/v1` | Host-facing upstream API base. |
| `openrouter_api_key_env` | `OPENROUTER_API_KEY` | Host environment variable containing the real API key. |
| `openrouter_model_routes` | `{}` | JSON object mapping exact model IDs to one provider endpoint slug each. |

A route entry has the form `"author/model-id": "provider/endpoint"`. Resolve both
values against OpenRouter's current model and provider catalog before approving
them. This shape is illustrative, not an enabled or validated route. Model IDs
must use `namespace/model` syntax; routers such as `openrouter/auto`, bare aliases, wildcard patterns,
model variants such as `:online`, and presets are rejected.

Use a specific endpoint slug when restricting an endpoint variant. OpenRouter
documents that a base provider slug also matches its endpoint variants; Evander
passes the configured slug unchanged in both `order` and `only`.
[Provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)
describes these matching rules.

Set the session's explicit `model` and select OpenRouter under Model relay.
Session metadata `model_provider_mode: openrouter` overrides the directory mode.
Container workers use `codex_auth_mode: metered_api`; an explicit subscription
selection is rejected for OpenRouter. An inherited subscription default does not
apply to OpenRouter turns. Keep the normal generated session relay URL and
Responses wire API configuration; the guest needs no OpenRouter key or internet.

## Relay policy

Only `GET /models`, `POST /responses`, and `POST /responses/compact` are exposed
through the existing authenticated session paths. Model discovery is filtered to
the configured route IDs. Chat Completions, embeddings, Messages, direct image
endpoints, files, realtime and WebSockets are unsupported in this mode.
Host assistant inference, workflow generation, and semantic embeddings retain
their existing provider configuration and are not routed through this feature.

Each POST must name the exact session model. The relay rejects body fields
`models`, `route`, `provider`, `plugins`, and `preset`, and all
`openrouter:*` tool declarations. Existing Evander web-search and image tool
permission checks still apply to ordinary Responses tool declarations. Query
parameters and guest OpenRouter control headers are not forwarded.

The relay injects:

```json
{
  "provider": {
    "order": ["configured-provider-endpoint"],
    "only": ["configured-provider-endpoint"],
    "allow_fallbacks": false,
    "require_parameters": true,
    "zdr": true
  }
}
```

The upstream bearer key stays on the host. Routing metadata is requested using
`X-OpenRouter-Metadata: enabled`. Upstream errors and retry headers are forwarded;
Evander does not switch providers, relax ZDR, retry on public OpenAI, or silently
translate compaction into a different operation.

ZDR is an upstream routing requirement, not a residency guarantee. OpenRouter
allows implicit in-memory prompt caching under its ZDR policy. Verify account
prompt logging and any account-level plugins or presets before activation.
See [OpenRouter ZDR](https://openrouter.ai/docs/guides/features/zdr).

## Usage and cost

Usage events are recorded with provider `openrouter`. Token counters and
`usage.cost_details.upstream_inference_cost` are retained. The authoritative
charge is `usage.cost` in USD; upstream inference cost is not added a second time,
including for BYOK requests.

The usage query exposes `reported_cost`, `estimated_cost`, `cost`,
`cost_source`, `total_reported_cost`, `total_estimated_cost`, and `total_cost`.
For provider-reported charges, `cost_status` is `reported` and
`estimated_cost` is null. Without a reported charge, an explicitly configured
OpenRouter catalog rule can supply an estimate; absent pricing is unavailable,
not a zero-cost inference. Team budgets and cost displays include reported
charges plus catalog estimates, retaining separate breakdowns.

Audit response metadata includes selected provider/model, attempts, region, BYOK
status and request identifiers when supplied upstream. It excludes prompt and
response bodies and arbitrary pipeline data. Streaming forwards the original
SSE bytes and captures terminal usage and metadata. Metadata fields follow the
[OpenRouter routing metadata contract](https://openrouter.ai/docs/guides/features/router-metadata).

## Activation and verification

1. Approve a current model/endpoint pair supporting Responses tools and ZDR.
   Provision the named host environment variable for the relay service.
2. Save the route through Admin Web with its current revision. Start with one
   isolated Codex session, an explicit model, and metered API authentication.
3. Confirm authenticated model discovery, a text response, and a function-tool
   round trip through the actual guest relay URL.
4. Exercise streaming and compaction with the installed Codex version. Evander
   forwards the separate
   [Responses compaction endpoint](https://developers.openai.com/api/reference/java/resources/responses/methods/compact);
   upstream OpenRouter/model support must be verified before production use.
   A 404 or unsupported-parameter response is surfaced unchanged.
5. Check `model-usage.db` through the supported usage query for provider,
   selected endpoint, token counters, charge and routing metadata. Test a denied
   model and denied routing override, and confirm no upstream request occurs.
6. Expand only after the selected combination passes. To roll back, select the
   previous provider mode and its matching model through the session/config API.
   Removing a route immediately makes sessions using that route fail closed.

The automated tests use mocked upstreams. They establish Evander's policy,
forwarding, streaming and accounting behavior; they do not certify a live model
or provider endpoint.
