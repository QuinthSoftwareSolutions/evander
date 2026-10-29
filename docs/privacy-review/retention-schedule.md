# Retention and deletion schedule

**Status:** technical draft. Periods marked `CURRENT` are repository-observed
defaults, not approved legal retention periods. Periods marked `PROPOSED` must
be approved by legal/privacy and the customer controller before becoming a
contract or production policy.

This schedule is designed to resolve the storage findings in the
[privacy data map](../privacy-data-map.md). It does not authorise irreversible
deletion while a legal hold, security investigation, or controller instruction
requires preservation.

## Implemented coverage

See [privacy operations](../privacy-operations.md). The registry exposes per-store
capabilities; only communication staging currently supports automated
retention/hold/erasure. Other TTLs remain owned by their existing services and are
not automatically governed by privacy holds. Do not record an effective hold for
an unsupported dataset without a verified manual preservation mechanism.

Voice/meeting/DIDWW transaction safety is implemented, but their lifecycle is
still unresolved. Model usage, published artifacts, Team/index/Git/workspace
copies, audit, providers and backups require explicit review. Existing
operation-triggered 90-day thresholds are eligibility rules, not upper bounds
on retention. Proposed periods and gates below remain unapproved.

## Policy controls to approve

| Control | Proposed rule | Decision/owner |
| --- | --- | --- |
| Capability-declared retention registry | Every persistent store must have a data class, owner, purpose, customer-configurable period or approved default, cleanup job, hold behavior, and test. | `[LEGAL/PRIVACY + ENGINEERING]` |
| Legal hold | A documented hold suspends ordinary deletion for the specific record set, has a named owner/reason/review date, is access-restricted, and is reviewed periodically; a review date does not automatically release it. | `[LEGAL DECISION]` |
| Deletion evidence | Cleanup records only identifiers, policy version, count, time, actor/job, hold exception, and result—not deleted content. | `[SECURITY/PRIVACY]` |
| Backup deletion | Primary deletion must create a tracked backup-expiry target. Restore must not silently reintroduce erased data after that target. | `[INFRASTRUCTURE/LEGAL]` |
| Provider copies | The controller or DPA-defined responsible party initiates/records upstream deletion where Evander cannot delete provider-held copies. | `[CUSTOMER/DPA]` |
| Configuration change | Retention may only be changed through revisioned configuration and must not shorten an active legal hold or a contractually agreed period without approval. | `[ENGINEERING/PRIVACY]` |

## Store schedule

| Data set / source | Current repository behaviour | Proposed policy target | Deletion / hold propagation | Status and owner |
| --- | --- | --- | --- | --- |
| Admin user identity and local password verifier (`admin-web.db`) | Account deletion removes the local user and local password credential; no account-retention period established. | Retain while account is active plus `[LEGAL DECISION]`; remove/disable promptly on offboarding. | Preserve only separately approved audit/security identity references; do not use deleted account as a new login. | GAP — `[OWNER]` |
| Session manifests and VM/container session state | Session expiry and explicit delete paths exist; shared Git is intentionally retained separately. | Customer-configured operational TTL; delete session state after terminal cleanup unless hold. | Worker/VM session files, SSH grants, queues, wake/approval references, browser state, and session-bound artifacts. | PARTIAL — `[OWNER]` |
| Approvals (`approvals.json`) | Expiry exists; no demonstrated general periodic purge. | `[LEGAL DECISION]` based on accountability/fraud/security purpose; store minimal payload. | Linked notification delivery, audit references, and workflow copies; retain only a restricted minimal decision record when justified. | GAP — `[OWNER]` |
| Workflow full history (`orchestration.db`) | CURRENT: completed full records default to 30 days. | Keep current 30 days only if purpose/necessity is approved; make customer override/hold explicit. | Full event/execution rows, queued/scheduled events, cursors where they reproduce content. | PARTIAL — `[OWNER]` |
| Workflow compact history | CURRENT: sanitized compact inputs/outputs and provenance default to 365 days. | Treat as personal data unless a documented field-level anonymisation assessment says otherwise; approve period and fields. | Compact rows, indexes, exports/report views, backup copies. | GAP — `[OWNER]` |
| Tool audit (`audit.jsonl` and index) | Append-only source reviewed; key-name redaction; no configured retention found. | `[LEGAL/SECURITY DECISION]` with a documented purpose and access restriction. | Audit JSONL, SQLite/FTS index, external log sinks, backups; exclusions and holds must be documented. | GAP — `[OWNER]` |
| Email records, drafts, cursors | Draft and email records contain content and recipients; no automatic retention established. | Customer-selected mailbox/content period; drafts shorter where feasible. | Local draft/email/cursor stores, attachments, provider draft/message where instructed and possible. | GAP — `[OWNER]` |
| Teams and Discord inbox messages | CURRENT: 90-day age threshold plus qualifying consumption cursors; cleanup occurs on store operations, so records may remain longer. | Retain 90 days only after customer approves; make deployment/customer override and hold behavior explicit. | Message rows, attachment staging, cursors, Team external-chat copies, model/workflow copies. | PARTIAL — `[OWNER]` |
| Communication artifacts | CURRENT: default seven-day TTL plus opportunistic cleanup; this branch adds the staging-only retention/hold/erase adapter. | Review the existing seven-day expiry; approve and verify bounded scheduled cleanup. Do not apply the staging TTL to published or delivered copies. | Blob and metadata; signed links; backup expiration. | PARTIAL — `[OWNER]` |
| Agent-to-agent prompts and wake records | CURRENT: prompts require responded/closed status and updated_at older than 90 days. Wake dispatch/messages use 90-day age thresholds; requests must also be disabled. Cleanup is operation-triggered. | Approve/shorten per customer purpose; ensure queues/retry ledgers use the same policy. | Prompt store, queued/retry records, steering-note/workflow/model-context copies. | PARTIAL — `[OWNER]` |
| Codebase consultations | CURRENT: fully cleaned terminal records compact after configured retention, default 30 days. | Confirm period and cleanup-complete definition; retain failure evidence only where justified. | Consultation database, worker session/job, temporary refs, capacity records, model/provider copies. | PARTIAL — `[OWNER]` |
| Voice sessions | Prompt/context, transcripts, summaries, decisions and metadata persist in `voice-sessions-store.json`; no automatic retention found. | Proposed engineering gate: approve the lifecycle and demonstrate cleanup, through a supported implementation or a reviewed scoped manual procedure; otherwise keep this feature disabled. | Voice store, meeting mirror, wake/model context, provider/bridge/telephony copies, backups. | UNRESOLVED — `[OWNER]` |
| Teams meeting events | Transcript-capable events persist in `teams-meetings.json`; no automatic retention found. | Proposed engineering gate: same review as voice, including separate event and transcript mirrors. | Meeting store, voice-session mirror, bridge/provider copies, workflows and backups. | UNRESOLVED — `[OWNER]` |
| DIDWW bridge call state | Separate `didww-sip-bridge-store.json` retains transcript-capable calls and request indexes; no automatic lifecycle adapter. | Apply approved voice/telephony policy through explicit linked-copy review. | Local bridge call state, voice mirror, active media resources, provider and backup copies. | UNRESOLVED — `[OWNER]` |
| Retell local call records | CURRENT: store mutations compact records with created_at older than 90 days; no hard deletion deadline is assured. | Approve period and reconcile it with voice-session and provider records. | Local store, provider call records and any transcript/media route. | PARTIAL — `[OWNER]` |
| Team state: chat, notes, cases, tasks, memories, attachments | CURRENT: insert-triggered dispatch activity cleanup uses 30 days; Git work contexts use 30 days with operational exclusions. No single lifecycle found for the broader state. | Set separate policy classes for operational task state, chat body, attachment bytes, memory body, review evidence, and agent/worker metadata. | SQLite rows, add-on attachment roots, semantic vectors, external-chat linkage, wake/model prompt copies. | GAP — `[OWNER]` |
| Semantic memories and code index | Individual memory deletion removes a semantic point. CURRENT: index default is 30 days with active/protected/newest-snapshot exceptions. | Treat text/embeddings as personal where source can contain personal data; document exception/hold conditions and deletion/rebuild. | Index/vector points, source snapshots, managed-Git binding, worker resources, backups. | PARTIAL — `[OWNER]` |
| Managed Git, worktrees, VM/container workspaces | Shared repositories and unpublished refs remain after session deletion pending explicit removal. | Customer contract and repository policy determine retention; ephemeral workspaces should be cleaned at session end unless hold. | Bare repo, refs, worktrees, VM disks/container volumes, build/artifacts, code-index copies. | GAP — `[OWNER]` |
| Model usage and classification attribution | Separate `runtime/model-usage.db` retains usage/correlation metadata; no automated privacy lifecycle adapter. | `[LEGAL/PRIVACY DECISION]` based on purpose and minimal retained fields; hashes are not assumed anonymous. | Usage rows, request/session/workflow correlations, provider records and backups. | GAP — `[OWNER]` |
| Published artifacts and artifact spaces | Durable archives/metadata in `runtime/published-artifacts/`, source files and space versions; distinct from communication staging TTL. | `[CUSTOMER INPUT]` for each artifact class and publication purpose. | Blob/archive, metadata, versions, source workspace, delivered copies and backups. | GAP — `[OWNER]` |
| Privacy case, incident and cleanup evidence | New `runtime/privacy.db` and restricted exports contain request identifiers, references and selected content. | Approve case/evidence lifecycle; exports default to seven days in privacy settings, subject to deployed execution verification. | Cases, jobs, holds, follow-ups, incidents, export bytes/metadata and backups. | PARTIAL — `[OWNER]` |
| Website/provider credentials | Individual connection deletion exists; Teams JSON is encrypted on first access in this branch, not in the original baseline. Upstream revocation and plaintext backups are separate. | Retain only while connection is authorised; remove and revoke on offboarding/rotation. | Encrypted credential row/key reference, cached token, provider connection, logs. | PARTIAL — `[OWNER]` |
| Backups, snapshots, service/OS logs, external providers | Not established by source review. | `[DEPLOYMENT/LEGAL DECISION]` with documented maximum restoration window and region. | Backup catalog, immutable snapshots, DR replicas, monitoring/logging vendors, processors. | UNRESOLVED — `[OWNER]` |

## Proposed cleanup implementation contract

Each cleanup implementation must:

1. select only records past the approved cutoff and outside an active hold;
2. remove primary content and correlated indexes/files according to the approved
   store contract; claim cryptographic erasure only where key/copy destruction
   has actually been demonstrated;
3. preserve a minimal, non-content deletion evidence record only where approved;
4. be idempotent, bounded, observable, and safe to retry;
5. report failures without exposing content or secrets;
6. have focused tests for cutoff, hold, partial failure, retry, cross-store
   propagation, and backup/provider follow-up; and
7. be reconciled periodically against record counts and the live configuration.

## Approval and exception log

| Data class | Approved period / trigger | Hold exception | Customer override | Approver | Effective date | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `[CLASS]` | `[TBD]` | `[TBD]` | `[TBD]` | `[OWNER]` | `[DATE]` | `[REF]` |

Approve a row only with a completed decision record and evidence for its deployed
cleanup mechanism or explicitly reviewed manual procedure. Repository defaults
and a future implementation target are not that evidence.
