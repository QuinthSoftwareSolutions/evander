# Changelog

## 2026-10-25 - Compact retained journal metadata (0.6.3)

- Require Evander 0.129.4. Update compact lifecycle and status projections in the journal transaction, avoiding repeated full-payload JSON extraction during metadata and retention lookups.
- Backfill legacy projections one journal at a time with a typed decoder that skips retained findings and proof bodies. Preserve original journal bytes, evidence, cleanup anchors, source pins and scoped metadata behavior.
- Invalidate cached projections when older processes write journals, and fence backfill against concurrent changes. No SDK or scanner-image change is required.

## 2026-10-25 - Separate source coverage and candidate validation (0.6.2)

- Require Evander 0.129.2. Distinguish positive sealed file-review evidence from unverified vulnerability candidates and external validation. Unread source and unknown or contradictory deferred work remain blocking; candidate labels alone cannot grant coverage.
- Keep certified files out of repeated mixed-batch retries, recover those facts from immutable retained proofs on continuation, and require explicit source-completion evidence for component-local boundary validation limitations. Preserve candidates, uncertainty and native partial reports without counting them as validated findings.
- Reject unknown native coverage for file credit and source completion, including reports with terminal file surfaces and candidate hypotheses.
- Publish pending validation counts and honest source-review semantics. Upgrade continuation journals to version 3 while preserving accepted version 1/2 evidence and historical reports. This is a host-only change; no SDK or scanner-image rebuild is required.

## 2026-10-25 - Confirm cleanup and completion with exclusions (0.6.1)

- Require Evander 0.129.1 and use the worker's durable, session-bound cancellation protocol for submitted launches with no saved handle. Only a matching receipt confirming both the launch fence and container cleanup releases the cleanup hold; older workers, lost responses and failed cleanup remain unconfirmed. Deploy the control plane and worker for this protocol; no SDK or scanner-image change is required.
- Return successful completion once every non-excluded file, boundary scope and required checkpoint is finished. Expose `scope_complete` and exact `excluded_paths` in results and reports, retain partial repository coverage and the original denominator, and never grant review credit to exclusions. Stops and unresolved obligations remain unsuccessful.

## 2026-10-25 - Persistent file blockers and pattern exclusions (0.6.0)

- Require Evander 0.129.0 and add `exclude_patterns`, including extension globs such as `*.pfx` and root-relative paths such as `bin/*`. Resolve exact matches against the pinned inventory, retain exclusions through the lineage and report them without granting new review credit. New scans and pending continuation scopes support exclusions; completed scopes and accepted findings stay intact.
- Add the opt-in `isolate_blockers` policy. Read exact in-scope blockers from sealed native coverage and required non-text dispositions, retain their evidence and reasons, and create one fresh review of the remainder without crediting partial reports. Unattributed incomplete file scopes require attention after one attempt. A continuation can import diagnostics from hash-checked retained native reports before scheduling new work.
- Keep isolated files and dependent required questions parked across resumes and batch rebuilding. `retry_blocked_paths` explicitly reopens selected files after prerequisites are supplied; completed boundary reviews remain accepted. Ordinary reviews receive the blocked-file context and cannot silently satisfy those outstanding limitations.
- Write version-2 coverage journals while accepting retained version-1 sources. Older runtimes cannot resume version-2 sources and discard quarantine state. Preserve original snapshots, immutable archives, route validation, scheduling limits, cleanup and fixed run budgets. The pinned SDK and scanner image are unchanged.
- Preserve one required checkpoint per saved identity across retries, and fold legacy alias chains while retaining their original references and closure receipts. Project broad requirements onto each component's assigned paths; local closure never certifies the repository-wide remainder. Newly named paths reopen the corresponding requirement. Unsupported nonempty references remain required for scope clarification.
- Apply the same exact/pattern exclusions to file reviews, boundary reviews and their required checkpoint inputs. Ignore only approved native exclusion notes wholly outside the current assignment; exact native paths stay literal. Never turn a partial native report into complete coverage. Reuse a complete hash-checked boundary proof previously rejected only for outside exclusion notes when its local obligations are already closed.
- Give genuinely unclosed work bounded pending owners without resetting completed scopes, blocked retries or the deadline. Follow-up splits preserve historical scopes and retry fences. Report distinct/pending checkpoint and folded-alias counts.

## 2026-10-24 - Explicit continuation exclusions and batch rebuilding (0.5.0)

- Require Evander 0.128.0 and support `exclude_paths`, `rebuild_pending_batches` and `incomplete_review_policy`. Defaults retain existing planning and split behavior; omitted exclusions and retry policies inherit the source settings.
- Exclude only exact tracked files in a retained mapped checkpoint before boundary planning. Preserve accepted credit and findings, report excluded files explicitly, and keep aggregate coverage partial. Exclusions persist through the lineage. Native questions wholly owned by excluded paths are labelled excluded; only pathless questions can use their original review scope. Repository-wide, mixed and unrecognized references remain required unless every referenced file is explicitly excluded.
- Rebuild unfinished file scopes only in an explicit linked continuation, retaining original source journals, proofs, snapshot identity and the existing byte and file caps. `retry_scope` retries an incomplete batch at most three times without recursively halving it; partial reports never grant file credit.
- Supply outstanding obligations only to the boundary scopes that own them. Ordinary file reviews no longer inherit unrelated repository-wide work. No scanner-image rebuild or SDK change is needed.

## 2026-10-24 - Per-scan tracked-review parallelism (0.4.0)

- Require Evander 0.127.0 and support the bindable `max_parallel_reviews` input from one to four, defaulting to two for new scans and inheriting the saved limit on linked continuation when omitted.
- Freeze the effective limit in the journal, staged worker inputs, progress, coverage ledger and manifest. Historical journals without a setting retain two slots; active request identities cannot be reused with a different setting.
- Apply the limit to file and boundary reviews while keeping architecture mapping serial. Uncertain launch, retention and cleanup states continue consuming slots across restarts. Preserve retained evidence, batching, original snapshots, access routes and fixed deadlines; the SDK and scanner image are unchanged.

## 2026-10-23 - Per-scan tracked-review text budgets (0.3.0)

- Require Evander 0.125.0 and support the workflow input `max_batch_text_bytes`, from 1 MiB to 2 MiB. New scans default to 1 MiB; omitted continuation inputs inherit the saved budget. Freeze the effective limit in version-4 journals, progress, aggregate coverage ledgers and manifests.
- An explicit budget change on continuation regroups only untouched pending root scopes, preserving accepted credit, findings, native archives, attempted scopes, split refinements and outstanding boundary assignments. Keep historical journals immutable, legacy active budgets unchanged, strict snapshot/route compatibility, cleanup and request idempotency. No scanner-image or pinned-SDK change is required.

## 2026-10-23 - Larger tracked-coverage batches (0.2.1)

- Raise the text budget per tracked review from 512 KiB to 1 MiB, retaining the 100-entry limit, intact oversized files, explicit coverage requirements and two-child concurrency bound.
- On explicit continuation, regroup only untouched root scopes from batching versions 1 and 2 into the version-3 plan. Preserve accepted file credit, findings, native archives, attempted scopes, split children, boundary assignments and immutable source journals. Already active runs keep their saved assignments and original boundary-planning byte budget.
- Keep the pinned SDK, scanner-image runtime, subscription and other route validation unchanged. Deploy this host-only policy with matching release metadata; no scanner-image rebuild is needed.

## 2026-10-23 - Explicit subscription handoff (0.2.0)

- Permit a linked tracked-coverage continuation to use an explicitly named destination ChatGPT subscription. Require Evander 0.122.0, the matching new session profile and otherwise identical public-subscription route settings.
- Reuse accepted coverage, findings and verified native reports at the original snapshot without rewriting source journals or archives. Record the handoff in the new journal, aggregate manifest and coverage ledger; retain cleanup, reservation and idempotency safeguards.
- Keep default resumes strict and in-flight routes frozen. This host-only change leaves the pinned SDK and scanner-image runtime unchanged and adds no API fallback or reset-credit operation.
