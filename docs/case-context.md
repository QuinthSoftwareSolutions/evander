# Case context

Case context preserves why work happened, not just its resulting repository diff.
It is available to any Team workflow; a formal release is optional. Notes remain
advisory: recording one does not count as workflow progress, and omitting one
never prevents task completion, review or release.

## Journal and sources

`team_record_note(body, request_id, ...)` is backward compatible. Optional fields:

- `kind` (for example `decision`, `clarification`, `implementation`, `review`,
  `correction`, `verification`), `summary`, `rationale`;
- `case_id`, `task_ids`, `deliverable_ids`, `review_request_ids`,
  `source_evidence_snapshot_ids`, `source_refs`;
- `supersedes_note_id` to correct an earlier note without overwriting it.

The server supplies the author, dispatch and timestamp. Typed references must be
visible to the caller and consistent with the case/project. A single dispatch
task, or several tasks sharing one case, permits conservative inference. Otherwise
the note stays unassigned; no attribution is guessed from prose. The original
author or case owner may supersede a note. Old notes retain their original IDs
and timestamps, and superseded notes remain retrievable.

Record concise conclusions and reasons, relevant alternatives, clarification
references, verification outcomes and unresolved limitations—not private reasoning
traces, credentials or copies of every tool response. `team_list_notes` returns
paged summaries; `team_get_note` returns a complete authorized note.

This journal is separate from Team Memory. Case-specific decisions belong here.
Only explicitly selected, reusable lessons should be promoted through the existing
memory tools. No automatic cross-case memory publication occurs.

Source captures reuse `ExternalSourceEvidenceSnapshot`. The generic
`effect:team:record-source-evidence` takes normalized `evidence`, `team_id`, optional
`project_id`, and explicit `case_ids`/`task_ids`. Same-identity retries preserve
the original capture; differing content needs a new external version.

`effect:team:capture-ado-case-source` accepts `case_id`, `ado_project` and
`work_item_id`. It checks the case's `ado:organization:project:id` or standard
`https://dev.azure.com/organization/project/_workitems/edit/id` identity against
the item actually fetched by the directory's host connector. No credentials are
sent to Team agents. It captures title, description, acceptance criteria and
comments, preserving authors, timestamps, comment versions and source links.
The version includes a content digest, so comment-only edits create new captures.
Comments are paged up to ten pages using the adapter's configured page size
(normally 50). Incomplete discussion is explicitly reported. Attachments and
arbitrary linked websites are not fetched.

`effect:team:refresh-case-sources(case_ids)` invokes supported adapters (ADO in
this version). Other source types are reported as unsupported; the generic effect
can ingest them separately. Connector failure retains earlier evidence and records
an unavailable warning, rather than representing the old capture as a successful
refresh. Queries and wake assembly never contact ADO.

## Views, retrieval and snapshots

All readers share `CaseContextService`. Select a case, a task with a linked case,
or a release package. The assembler follows explicit task ancestry/dependencies,
deliverable contributions, predecessor releases, review attempts and generated
corrections. It does not collect arbitrary sibling tasks for release scope.
Parent task artifacts can supply the implementation plan. Available UTF-8
Markdown/text/RST from authorized published bundles is captured with archive,
version and document digests; no archive extraction or code execution occurs.

The eight section types are `request`, `sources`, `plans`, `decisions`, `delivery`,
`review`, `verification` and `conversation`. New named views require configuration,
not new code. A recipe controls section sequence, record ordering, kinds/statuses,
per-section record limits, per-record text limits and an overall character budget:

```json
{
  "view_id": "customer-handoff",
  "title": "Customer handoff",
  "sections": [
    {"name": "request", "limit": 5},
    {"name": "decisions", "kinds": ["decision", "clarification"],
     "statuses": ["active"], "order": "newest_first", "limit": 10},
    {"name": "verification", "limit": 5}
  ],
  "text_budget": 6000
}
```

There is no arbitrary SQL, template execution or LLM summarization in recipes.
Each directory can define/override a view; the closest ancestor definition wins.
Saves append a new version under optimistic revision checking. A pinned version
refers to that nearest definition, not a same-numbered version in another directory.
Built-in starting points are `case-overview`, `task-brief`, `review-brief` and
`app-release-brief`; all can be overridden. Per-section limits are 1–100 records
and 100–16,000 characters per record; total recipe budgets are 1,000–64,000 characters.

`team_get_case_context` and `query:team:case-context` are read-only live projections.
They report source revision, record provenance, truncation, missing/restricted
sources, stale evidence and pagination. An integer `cursor` advances the bounded
page in each selected section. A live page can change between revisions; use a
snapshot when a repeatable read matters. Existing note/source/artifact tools and
operator evidence pages provide detail beyond the brief.

`effect:team:snapshot-case-context` persists selected full evidence separately
from bounded display text. Its `request_id` is idempotent within directory/creator
scope; reusing it with different selection, recipe or grant inputs fails. The
snapshot records the recipe version, source revision, content digest and optional
release/candidate/analysis-base references. Candidate SHA must match the release;
the existing packaging Git checks remain responsible for validating the analysis
base. Later source, journal, review, recipe or artifact changes cannot modify the
capture. Recipe filters also filter stored evidence, so a narrow snapshot does
not expose excluded sections via detail paging.

Read it with `query:team:case-context-snapshot(snapshot_id)` or
`team_get_case_context_snapshot(snapshot_id, section?, cursor?, limit?)`.
The latter allows a detached session only when the snapshot has an exact
`recipient_session_id` grant and session policy allows that tool. Grants do not
confer Team membership, general ADO access or case search. Ordinary agents retain
existing task/case visibility and dispatch-frozen source permissions. Expired
sessions and inactive Team agents cannot use grants. A snapshot grant is a
deliberate disclosure of that frozen evidence; subsequent source changes do not
revoke it. Use a short-lived, appropriately scoped consumer session.

Operator context endpoints require directory ownership because composed source
evidence can contain restricted content. Linked private Chat messages additionally
retain existing conversation-owner/chat-read-all restrictions. Bulk Team state
does not contain journal or context bodies. Source material is data, not execution
authority; captured instructions cannot override the agent's current mandate.

## Automatic wake context and cost

Case-linked ordinary and review/manager-fallback dispatches freeze one combined
context, not one context per case. Fresh/reset contexts receive a bounded baseline.
Retained wakes receive only records whose content is new or changed since the
previous completed dispatch known to have run on the same native conversation
thread. An unchanged wake carries a small status and the full-snapshot retrieval
reference. Removed/restricted records are counted without replaying their old text.

Failed/preparing dispatches are never comparison baselines. Unknown threads,
lineage-thread changes, pending resets and changed recipe versions fall back to a
baseline. A retry in a new context always gets the baseline, not a stale delta.
Changes are advisory evidence, not new completion gates or reconstructed authority.
Full snapshot retrieval remains available even when only a delta is injected.

The entire injected `case_contexts` JSON field is capped at **6,000 characters**,
including metadata, formatting and escaped text, independently of recipe size.
This is a character ceiling, not a tokenizer guarantee; a typical full English
brief is roughly 1,500–2,500 tokens. Wakes without linked cases add nothing.
Full source records are fetched on demand. Standalone Chat wakes without explicit
case/task links continue to use existing case indexes and on-demand tools; they do
not eagerly inject every case in the project.

Deliverable submission records available case note IDs and dispatch snapshot IDs;
release manifest evidence carries those references. These are provenance links,
not proof that every captured record was read. Release-focused records created
after the candidate manifest are labeled accordingly. Current source refreshes
provide packaging context, not evidence of what the implementer knew at the time.

## Operator controls and API

Open **Cases → Context** to select a view, inspect warnings and full evidence,
preview/edit a recipe, save a new version, inspect version history, freeze a saved
view, or page through snapshots. Draft preview is read-only. Saving/freeze requests
use the current Team revision; conflicts must be refreshed, not overwritten.

All paths below are relative to `/api/addons/team-addon`:

| Purpose | Endpoint |
|---|---|
| Live brief | `GET /cases/{id}/context?view_id=case-overview` |
| Full selected records | `GET /cases/{id}/context/evidence?section=decisions&cursor=0` |
| Saved snapshots | `GET /cases/{id}/context-snapshots?cursor=0` |
| Frozen evidence | `GET /case-context/snapshots/{id}?section=sources&cursor=0` |
| Effective recipes and history | `GET /case-context/views?directory_path=root/example` |
| Save recipe version | `PUT /case-context/views` with `view`, `directory_path`, `expected_revision` |
| Preview recipe | `POST /case-context/preview` with `view`, `case_id`, `directory_path` |
| Freeze saved view | `POST /case-context/snapshots` with selection, `request_id`, `expected_revision` |
| Legacy-note audit/backfill | `POST /case-context/notes/backfill` with `dry_run` (default `true`) |

Backfill requires root ownership. Apply with `dry_run:false` and the audited
`expected_revision`. Pure reads already project old note actions; backfill merely
persists the same IDs/timestamps and reports unassigned records. It does not invent
historical ADO conversations, implementation decisions or plans. Existing databases
get additive tables/columns; keep normal pre-upgrade backups. Immutable context
history is durable and currently retained until an operator-managed retention
policy is introduced—there is no automatic pruning in this version.

## APP rollout boundary

This change supplies application code, tests and an opt-in
[workflow example](../config/workflow-packs/case-context.v1-example.json).
It does **not** deploy services or edit the live `truman-control-plane` workflows.

After Jonathan's CI/CD deployment is confirmed:

1. Re-read deployed capabilities, the APP agent/session configuration, and current
   intake, owner-comment and release workflow documents with their revisions/locks.
2. Add source capture after intake creates/resolves its case, and after relevant
   ADO owner/Trudy discussion updates, before dispatching dependent work. Use the
   existing case identity, host connector and directory; do not derive authority
   or recipient sessions from comment text. Update the APP planner/implementer/
   reviewer guidance to record concise decisions and rationale at useful handoffs.
3. Before the main release-packager prompt, query context by the **exact release
   package**, refresh its returned case IDs, then snapshot using the exact
   candidate SHA and already validated analysis-base SHA. Pin/review the
   `app-release-brief` recipe and use a deterministic request key containing that
   release/candidate/base and packaging contract version. This includes predecessor
   and correction history, not just the latest top-level deliverable.
4. Grant `app-release-packager` only `team_get_case_context_snapshot` in addition to
   its existing tools, and grant the exact snapshot to that session. Pass the
   snapshot ID, bounded text, omissions and pinned Git identities in the prompt.
   Require source-supported intent plus independent diff inspection. Preserve the
   existing v5 output JSON schema, size limits, artifact paths and verification
   policy; do not introduce a release-description test rerun.
5. Keep snapshot identity across packaging retries/repair prompts. Do not reuse the
   APP-specific change for another packager path (for example OPC) without reviewing
   that workflow's scope. Save through supported revision-pinned APIs, validate and
   read back. Never overwrite live pack files from a stale local copy.
6. Run a bounded synthetic case through capture, note, correction, resumed-wake
   delta and packaging snapshot reads. Verify grant denial from another session,
   source-failure warnings, unchanged v5 output acceptance, and non-release use.

Workflow application and any necessary orchestrator reload/restart belong to that
separate, explicitly authorized post-deployment phase.
