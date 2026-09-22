# Data-subject request procedure

**Status:** draft processor-assistance procedure for legal/DPO review. It does
not decide whether a request must be granted, which role applies, or what law
requires preservation. The controller remains responsible for responding to
data subjects unless the parties expressly agree otherwise.

The European Commission states that organisations should respond to rights
requests without undue delay and in principle within one month; an organisation
may seek additional information to verify identity where appropriate. IMY notes
that identity verification must not collect more data than necessary. See
[Commission guidance](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/dealing-requests-individuals_en)
and [IMY guidance](https://www.imy.se/vanliga-fragor-och-svar/jag-har-gjort-en-begaran-om-tillgangraderingrattelse-men-de-kraver-att-jag-identifierar-mig.-far-de-gora-det).

## Operating principles

1. **Route by role first.** Determine whether Evander is acting on a customer
   controller's instructions or responding about Evander's own independent
   processing. Do not disclose customer content to a requester merely because
   they contacted the processor directly.
2. **Minimise the request case.** Store a case ID, request type, contact channel,
   identity-verification result, controller/environment, dates, decisions, and
   evidence references. Do not copy the requested content into the case unless
   required and approved.
3. **Verify proportionately.** Request only the minimum additional proof when
   there are reasonable doubts about identity. Never use a response to reveal
   whether a person appears in sensitive customer data.
4. **Preserve when justified.** Record any necessary preservation decision and
   apply a narrowly scoped hold only through a supported mechanism. A case marker
   alone does not stop existing cleanup or processing. Review holds; do not
   release them automatically on their review date.
5. **Separate assessment from execution.** A privacy/legal owner or authorised
   controller approves exceptions, redactions, restriction, and erase actions;
   an engineer/support operator performs only the approved scoped work.
6. **Record proof, not content.** Log sources searched, records affected,
   provider follow-ups, holds/exceptions, and completion time without copying
   personal content into the operational ticket.

## Intake and routing

| Step | Action | Evidence / SLA target |
| --- | --- | --- |
| 1. Receive | Accept request through `[PUBLISHED EMAIL/PORTAL]`, support, controller escalation, or DPO channel. Issue a case ID. | Receipt time, channel, minimal requester contact. |
| 2. Classify | Mark access, rectification, erasure, restriction, objection, portability, or other; record claimed controller/environment. | Request classification; do not decide eligibility yet. |
| 3. Determine role | Check customer/DPA/deployment record. If processor content, promptly route to named controller contact and follow documented instructions. | DPA/instruction reference and handoff timestamp. |
| 4. Verify identity | Use existing authenticated account or minimal proportionate proof only if needed. | Verification method/result; do not retain extra proof longer than `[LEGAL DECISION]`. |
| 5. Start clock/escalate | Record statutory/contractual deadline set by the controller. **Proposed processor target:** acknowledge and route within one business day, then supply available technical assistance on the DPA timeframe. | Deadline, owner, escalation contact. |
| 6. Apply preservation | Restrict ordinary cleanup only for sources reasonably related to the case and approved hold scope. | Hold ID, sources, owner, review date and verified mechanism; unsupported sources require manual preservation. |

## Technical data-locator plan

Search must use approved identifiers and avoid broad free-text searches unless
necessary and authorised. The case owner chooses one or more correlation keys:
account/login, customer user ID, mailbox address, Teams/Discord/provider ID,
phone-derived approved identifier, session/agent ID, repository account, or
other controller-supplied key.

| Source class | Search/location evidence | Likely key(s) | Access/export action | Erasure/restriction action | Current limitation |
| --- | --- | --- | --- | --- | --- |
| Admin identities | `admin-web.db`, SSO/account records | Login, account ID, email-style identifier | Account fields, roles/groups, login metadata | Delete/disable local account if authorised; retain approved audit references | Does not automatically clean content records. |
| Sessions, approvals, workflows | Session and approval JSON stores; `orchestration.db`; wake/queue stores | Session/VM ID, actor ID, request/correlation ID | Export scoped rows and metadata; separate other people's data | Apply approved restriction/delete with linked queue/index handling | New exact structured locator covers selected JSON stores; workflows/queues and free-text references remain manual. |
| Audit | `audit.jsonl` and audit index | Actor/session/request/correlation/provider IDs | Export/redact only approved personal data; preserve audit integrity | Use approved retention/exception policy; do not edit raw audit history ad hoc | No central retention/subject erasure policy. |
| Email and external chat | Draft/email stores, Teams/Discord inboxes, Team external-chat rows | Address, provider message ID, user/channel/chat ID | Export scoped messages/metadata with third-party redaction review | Local deletion/restriction plus customer/provider follow-up | Provider and attachment copies need separate action. |
| Voice and meetings | Voice session, Teams meeting, DIDWW bridge and Retell stores | Session/call/meeting ID, approved contact identifier | Allowlisted voice/voice-query fields are available; review participant data and manually collect meeting events/bridge details | Delete/restrict primary and mirrored local copies when authorised | No automated retention or cross-store erasure path for voice/meeting stores. |
| Team state and semantic memory | Team state, attachment roots, semantic vector/index service | Operator/agent ID, conversation/message/memory/task IDs | Export record set and relevant index metadata | Delete/restrict row, files, vector points and linked context as approved | No unified lifecycle across this set. |
| Git, workspaces, code index | Managed Git, worktrees, worker/VM state, code index | Repository account/email, commit, path, session ID | Only under controller instruction; preserve third-party/confidential material | Explicit repository/ref/workspace/index operation with hold review | Git history and upstream copies cannot be silently rewritten as a generic DSR response. |
| Model usage, published artifacts and privacy evidence | `model-usage.db`, published archives/metadata, artifact-space versions, privacy cases/exports/incidents | Session/workflow/request IDs, artifact metadata and approved case identifiers | Reviewed manual extraction; do not assume hashes are anonymous or artifact bytes have staging TTL | Separate retention/erasure review for each source and derived copy | No complete subject locator or automated lifecycle across these datasets. |
| Credentials and provider accounts | Encrypted credential/connection records | Account/profile ID | Usually provide account metadata, not secrets | Remove local credential and revoke/disable upstream access per owner instruction | Do not reveal or export secret values. |
| Backups and providers | Backup catalog, provider admin/support path | Store/provider record IDs | Report retention/restoration constraints | Record scheduled expiry/upstream request and outcome | Live inventory and contractual routes are not yet established. |

## Implementation coverage and manual handoffs

Use [privacy operations](../privacy-operations.md) to establish which adapter
operations are supported in the deployed version. The branch adds case and
follow-up records, exact structured locators for selected JSON stores and
allowlisted exports for sessions, voice, voice queries and communication-artifact
metadata. Those exports omit attachment bytes and unlisted fields; they are not
automatically complete responses.

Only communication staging has automated privacy retention/hold/erasure.
Voice/meeting/DIDWW, audit, inboxes, Team, usage, published artifacts, indexes,
Git/workspaces, credentials, providers and backups need reviewed manual work or
additional implementation. Unsupported operations must remain unresolved, not be
reported as successfully executed.

Restriction and objection case types do not themselves inhibit producers,
retrieval, queues or outbound calls. Assign a verified manual pause/control where
needed, including replay/reindex/restore paths. Rectification uses the owning
source's approved procedure; do not present a follow-up ticket as the correction.
Before closing a case, distinguish local execution, external/backup follow-ups
and approved exceptions, and review the response for third-party disclosure.

## Fulfilment paths

### Access and portability

1. Confirm role, instruction, identity, scope and deadline.
2. Collect records using the locator plan; identify third-party personal data,
   confidential information, security details, and legal exceptions for
   controller/legal review.
3. Produce an encrypted, access-controlled export in a commonly used format
   where required/appropriate. Do not place it in agent workspaces, public links,
   or a normal audit record.
4. Provide the controller with a source list, extraction date, known omissions,
   provider/backup boundaries, and content-review flags.
5. Controller sends the response or expressly instructs Evander to do so.

### Rectification

1. Identify the authoritative source. Do not modify a provider/source system
   without the controller's instruction.
2. Correct local structured data where authorised; preserve an approved minimal
   audit of the correction and propagate/reindex dependent copies.
3. Where free-form historical content is accurate as a historical record,
   obtain legal/controller direction before alteration; add a correction or
   restriction record if that is the approved response.

### Erasure and restriction

1. Obtain controller/legal approval listing sources, hold exceptions, provider
   actions, and backup treatment.
2. Stop further non-essential processing and disable automation/recipients that
   would recreate the data where restriction is required.
3. Execute idempotent, scoped cleanup. Include local stores, attachments,
   indexes/vectors, queued/retry work, worker/VM state, and mirrored transcripts.
4. Record counts/identifiers and failures. For providers and backups, record the
   request/reference and scheduled expiry rather than claiming immediate erasure
   where it cannot be technically verified.
5. Reconcile after cleanup and send the controller a completion/exception report.

## Case record template

| Field | Value |
| --- | --- |
| Case ID | `[ID]` |
| Received / deadline | `[TIMESTAMP] / [CONTROLLER-SET DEADLINE]` |
| Request type | `[ACCESS / RECTIFICATION / ERASURE / RESTRICTION / OBJECTION / PORTABILITY]` |
| Role and controller | `[PROCESSOR/CONTROLLER + REFERENCE]` |
| Identity verification | `[METHOD / RESULT / EXPIRY]` |
| Scope / correlation keys | `[MINIMUM NECESSARY]` |
| Hold / exception | `[ID / OWNER / REVIEW DATE / RELEASE DECISION]` |
| Sources searched | `[SOURCE + RESULT COUNT/REFERENCE]` |
| Provider/backup actions | `[REFERENCE + STATUS]` |
| Approval | `[CONTROLLER/LEGAL/PRIVACY OWNER]` |
| Outcome / response owner | `[COMPLETED / PARTIAL / REFUSED + REASONS]` |
| Closed / review | `[TIMESTAMP / REVIEWER]` |

## Proposed operational quality controls

- Test the workflow quarterly with synthetic identities and content.
- Require two-person review for exports containing communication, transcript,
  credential-adjacent, or source-repository data.
- Reconcile open cases weekly against approaching deadlines and hold review dates.
- Treat a request that exposes a missing store, recipient, or deletion path as a
  privacy engineering defect and update the data map/retention register.
