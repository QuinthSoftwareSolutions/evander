# 14. Semantic Memory, Model Usage, Skills, and Add-ons

> **At a glance.** Evander can be extended with searchable knowledge, model-usage estimates, reusable agent instructions, and packaged add-ons. Each feature adds something useful, but none is allowed to become an invisible second source of truth or an unrestricted path into the control plane. This chapter explains what these extensions are good for and, just as importantly, what they do not replace.

## Four extension layers

These four facilities solve different problems. Search helps an agent find relevant material. Usage records help an operator understand model activity. Skills give an agent reviewed instructions. Add-ons give Evander a packaged new capability. None of them silently changes the authority rules described earlier in the manual.

| Facility | Helps with | Must not be mistaken for |
|---|---|---|
| Semantic store | Searchable vector projection | Canonical database or access-control bypass |
| Model usage | Provider-neutral usage/cost observations | Provider billing ledger |
| Managed skill | Reviewed `SKILL.md` installed into selected execution | Executable host plugin or secret carrier |
| Add-on | Packaged control-plane integration | Permission to modify arbitrary core state |

For Northstar Platform, an operator might index approved architecture notes, attach a reviewed telemetry-debugging skill to the implementation execution, inspect model use for the `mission-control` workflow, and use Team Add-on to inspect the coordinated assignment. Each action still carries directory, session, and audit context.

## Semantic storage is a projection

Semantic search turns approved text into a form that can be searched by meaning rather than exact wording. It is useful for finding a likely architecture note or previous decision, but the result is still only a pointer back to the authoritative source. A close match does not make the text current, true, permitted, or relevant to the present directory.

For Northstar, the approved review note remains in Team or artifact storage. The semantic store holds a searchable copy with enough source information to find that note again. If the searchable copy is deleted or rebuilt, the approved note remains.

> <span class="sidebar-kicker">Technical sidebar</span> **Collection and embedding contract**
>
> The current semantic-store provider is Qdrant and the current embedding provider is OpenAI-compatible embeddings. Defaults target host-local Qdrant at `http://127.0.0.1:6333` and `text-embedding-3-small` with 1,536 dimensions and cosine distance. Provider configuration is directory-resolved; API keys are named by environment variable and resolved on the host.
>
> The v1 collection contract is strict:
>
> - a collection has one unnamed vector, not a named-vector schema;
> - its dimension and distance must match the effective embedding configuration;
> - a point ID is an unsigned 64-bit integer or a UUID string;
> - upsert embeds the supplied text and records that source text under the reserved payload key `_semantic_text`;
> - user payload must not counterfeit reserved fields;
> - get/search return normalized points and scores, while delete names explicit IDs.
>
> `effect:semantic:ensure-collection` is idempotent only when an existing collection has the expected schema. It refuses a mismatch instead of silently re-embedding old points into an incompatible space. Changing model or dimensions therefore requires an explicit migration: create a new collection, re-index from canonical sources, verify it, then switch consumers.
>
> Queries and effects are available both to workflows and, where policy allows, through gateway tools. Deletion is destructive and may require approval. A similarity result is evidence of proximity in the selected embedding space, not a statement of truth or authorization.

### A safe indexing pattern

Use a deterministic point ID derived outside the semantic store and retain the canonical record ID in payload metadata. For example, an approved review note remains authoritative in Team or the artifact store; Qdrant receives its text plus `{team_id, project_id, source_type, source_id}`. Re-indexing the same point updates its vector, while deleting Qdrant never deletes the source note.

## Model-usage events and estimates

Evander records what a model provider reported about a request and can estimate cost using a versioned price catalog. This helps answer questions such as “which workflow used the most model capacity?” It is not the provider's invoice, and missing usage information is shown as missing rather than silently counted as zero.

> <span class="sidebar-kicker">Deeper detail</span> **Usage records, attribution, and pricing**
>
> Relay and execution adapters normalize provider observations into `ModelInferenceUsageEvent` records in `runtime/model-usage.db`. An event can include provider/endpoint, model or deployment, input/output/total tokens, request status, duration, body byte counts and hashes, and correlation fields such as session, workflow run, effect, node, dispatch, agent harness (`agent_harness`), backend, directory, trace, and request IDs.
>
> Two cases must remain distinguishable:
>
> - `available`: the provider supplied usable token data;
> - `usage_unavailable`: a request completed or failed without a usable usage block.
>
> Evander does not invent missing token counts. Correlation is best effort within an explicitly bounded context; unattributed events remain useful provider observations rather than being assigned to the nearest-looking Team task.
>
> Cost calculation uses `runtime/model-pricing.openai.json` by default, or `EVANDER_MODEL_PRICING_PATH`. `evander-cli init-state` seeds the runtime catalog from `config/model-pricing.openai.json.example`; the example is not a live price feed. Rules can match provider, exact model or pattern, effective date, service tier, cached input, cache writes, batch multiplier, and long-context adjustments.
>
> An estimate reports statuses such as `estimated`, `pricing_unavailable`, `usage_unavailable`, `unsupported_provider`, or `insufficient_token_breakdown`. It is an operational estimate under a named catalog version and rule—not a substitute for the provider invoice. Operators must update pricing with revision checks when provider prices change.

## Managed skills

A managed skill is a reviewed set of written instructions that Evander can place in a selected execution. It can teach the agent how this organization performs a task, but it does not install a host plugin or grant access. The job must already be allowed to use every tool, route, and resource named by the instructions.

Treat skill review like prompt and code review: look for requests to disclose data, make unbounded changes, assume authority the agent does not have, or use obsolete commands.

> <span class="sidebar-kicker">Technical sidebar</span> **Skill storage and provisioning**
>
> The skill library stores one reviewed instruction document at:
>
> ```text
> runtime/skills/<skill-name>/SKILL.md
> ```
>
> Metadata lives in `runtime/skills-library.json`. Names may contain letters, numbers, dots, underscores, and dashes, but no traversal segments. Content must be non-empty. Creation uses a temporary file and atomic replacement before metadata is committed.
>
> When a container execution requests skills, Evander copies the selected files to:
>
> ```text
> /home/agent/.config/evander/codex/skills/<skill-name>/SKILL.md
> ```
>
> The agent receives text, not a host path. Unknown or missing skill content fails provisioning. Admin deletion is blocked when active configuration or workflow references would make the deletion unsafe.
>
> A skill can instruct an agent to call tools it already has; it cannot itself grant a tool, network route, host secret, or policy exemption.

## Add-ons

An add-on packages a larger extension to Evander. It may contribute its own Admin Web area, tools, workflow building blocks, and private records. Being present on disk is not the same as being enabled, and being enabled is not proof that it loaded successfully. Because several Evander processes assemble their capabilities at startup, an add-on change may require the services shown on the Add-ons page to restart.

Disabling an add-on prevents it from loading next time; it does not silently erase its saved records.

> <span class="sidebar-kicker">Technical sidebar</span> **Registration, discovery, and private state**
>
> An add-on returns an `AddonRegistration` with a validated ID, display name, optional Admin navigation, API/UI route factories, MCP registration hook and static MCP tool descriptors, workflow providers, and metadata. Enabled add-on descriptors are merged into the core Sessions, MCP Tools, workflow-editor, and Tool Visibility catalogs; the gateway verifies declared tool names against registered names. Catalog visibility does not grant authority. It receives an `AddonContext` containing its runtime directory and supported core services. Private state belongs under:
>
> ```text
> runtime/addons/<addon-id>/
> ```
>
> Storage helpers reject absolute paths and traversal. Removing private add-on state must not corrupt core state, although it can intentionally erase that add-on's records.
>
> Discovery scans configured local package directories—`addons/` and `src/` by default—plus explicitly named modules. Entry-point discovery is disabled by default. Discovery and enablement are different: Admin Web can show a package that is present but disabled or failed to load.
>
> `addon_loader.enabled_addon_ids` is revision-edited configuration. Enabling or disabling is restart-gated because Admin routes, MCP tools, and workflow registries are assembled at process startup. Disabling prevents the next load; it does not delete private state.

### The Team Add-on boundary

The bundled Team Add-on registers its own `/api/addons/team-addon/...` API, `/addons/team-addon/` UI, MCP tools, workflow primitives, and relational state at `runtime/addons/team-addon/state.db`. A legacy `state.json` can be imported once. Core Evander remains responsible for sessions, execution, policy, Git, artifacts, workflows, audit, communication, and model usage; Team records refer to those core identities instead of duplicating their implementation.

The add-on is disabled by default. Enable `team-addon`, restart every service listed by the Add-ons page (including the gateway when MCP registration changes), then verify loaded state. Team-specific chapters describe its organization, task, review, and release semantics.

The bundled Bokio add-on uses the same catalog and policy path. Its operator page is a prepared-action review workspace, while `bokio_create_supplier_invoice` is a narrower direct MCP write with a required durable operation key. A session may be allowed to use that supplier-invoice tool autonomously without granting any direct autonomous bank-payment tool; Bokio bank-payment creation stays prepared/approved and still requires BankID signing.

## Failure behavior

| Symptom | Likely cause | Safe response |
|---|---|---|
| Semantic ensure fails with schema mismatch | Model dimensions/distance changed or collection is not v1 single-vector | Create and backfill a new collection; do not alter config to misdescribe old vectors |
| Semantic search returns irrelevant results | Wrong source text/model/filter or stale projection | Inspect `_semantic_text` and payload; re-index from canonical records |
| Cost is unavailable | Missing usage, model rule, provider support, or token breakdown | Preserve the status and update the catalog/adapter; do not treat zero as cost |
| Skill appears in Admin but execution cannot install it | Metadata/content mismatch or skill not selected | Repair through the skill service and reprovision; do not copy ad hoc host files |
| Add-on says “pending restart” | Configuration changed after process assembly | Restart the listed services and verify loaded state |
| Add-on load error | Bad manifest/module/registration, duplicate ID, or missing asset | Inspect discovery and the journal; an explicitly enabled/mapped add-on can fail process assembly rather than start with an incomplete contract |
| Team UI works but Team MCP tools are missing | Admin restarted but gateway did not | Restart gateway after confirming enablement |

## Troubleshooting and operator handoff

An operator can begin in Admin Web. Record the selected directory, which of the four facilities is involved, what was expected, what appeared instead, and any visible session, workflow, skill, add-on, or Team identifier. For model usage, also record the time window and model name. For semantic search, retain the returned source reference rather than copying a result and treating it as authoritative.

If a skill or add-on is missing from a running session, remember that an existing execution may still reflect the configuration with which it started. Do not work around that difference by copying host files into the sandbox or editing add-on state directly.

## Administrator runbook

1. **Semantic:** inspect effective directory settings, Qdrant health, collection size/distance, and embedding endpoint. Test store status before changing data.
2. **Projection integrity:** retrieve a point by exact ID and compare its source metadata to the canonical record. Rebuild rather than hand-edit vectors.
3. **Usage:** filter by provider, session/workflow/effect, and time. Check `normalized_usage_status` before interpreting token or cost totals.
4. **Pricing:** open the active runtime catalog in Admin Web, note its revision/version/effective dates, make a revision-checked update, and re-query estimates.
5. **Skills:** validate the library entry and every configuration/workflow reference before adding or deleting it. Start a fresh execution to verify installed content.
6. **Add-ons:** open Add-ons, distinguish discovered/enabled/loaded/pending-restart states, inspect load errors, restart the listed services, and verify `/api/admin/state` plus the add-on's health/read endpoint.
7. **Team Add-on:** verify `runtime/addons/team-addon/state.db` exists and is readable through the add-on API. Never repair it by copying a stale JSON snapshot over the database.

## Source trail

- `src/evander/semantic/settings.py`
- `src/evander/semantic/qdrant.py`
- `src/evander/semantic/workflow_helpers.py`
- `src/evander/gateway/semantic_tools.py`
- `src/evander/model_usage/models.py`
- `src/evander/model_usage/store.py`
- `src/evander/model_usage/pricing.py`
- `src/evander/repositories/skills_library.py`
- `src/evander/admin/services/skill_admin_service.py`
- `src/evander/addons.py`
- `src/evander_team_addon/__init__.py`
- `docs/addons.md`
- `docs/configuration-reference.md`
- `config/model-pricing.openai.json.example`
- `tests/test_semantic_store.py`
- `tests/test_gateway_semantic_tools.py`
- `tests/test_model_usage_store.py`
- `tests/test_model_usage_pricing.py`
- `tests/test_skill_library_repository.py`
- `tests/test_addons.py`
- `tests/test_team_addon_repository.py`
