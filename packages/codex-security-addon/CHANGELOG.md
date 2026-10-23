# Changelog

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
