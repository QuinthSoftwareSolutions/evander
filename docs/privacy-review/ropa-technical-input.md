# ROPA technical input

**Status:** draft technical input for the Article 30 record. Legal/DPO review
is required before use. See [the pack overview](README.md).

## Record control

| Field | Value |
| --- | --- |
| Record owner | `[OWNER]` |
| Version / effective date | `0.1 draft / 2026-09-21` |
| Product/service | Evander control plane and enabled workers/add-ons |
| Technical evidence | [Privacy data map](../privacy-data-map.md) |
| Controller(s) represented by this instance | `[CUSTOMER INPUT: legal name, address, privacy contact, DPO]` |
| Processor | `[Evander legal entity, address, privacy contact, DPO if applicable]` |
| EU/EEA representative | `[LEGAL DECISION: if applicable]` |
| Deployment identifier / environment | `[DEPLOYMENT EVIDENCE]` |
| Last live configuration review | `[DATE, REVIEWER, SAFE EVIDENCE REFERENCE]` |

## Role boundary to confirm

| Processing | Provisional technical role | Decision required |
| --- | --- | --- |
| Customer tenant content: prompts, mail/chat/call content, repository content, customer contacts and workflows | Processor when Evander processes only on documented customer instructions | Customer/controller and counsel must confirm scope and instructions in the DPA. |
| Evander's own sales, contracting, billing, security monitoring, and support administration | Potential independent controller | Legal owner must maintain separate records, notices, lawful bases, and retention. |
| Provider account connection made by a customer user | Depends on who determines purpose/means and account context | Confirm for each integration; do not assume a single role covers every flow. |

## Processor record: categories of processing performed for a customer

The rows below are technical candidates. For each customer, retain only enabled
rows and complete the transfer and security fields. The controller decides the
purpose/lawful basis; this document records the processing operation, not its
legal justification.

| ID | Category of processing | Data subjects | Personal-data categories | Source and operation | Recipients/subprocessors | Storage/lifecycle | Transfers | Technical measures/evidence | Customer/DPO decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P-01 | Tenant administration and access | Customer administrators, operators and people using agent workflows | Login/email-style ID, display name, role/group membership, authentication/session metadata | Admin Web account administration; session policy and authorization | Hosting provider `[VERIFY]`; Entra ID if SSO enabled | `admin-web.db`, session/config stores; account deletion exists but related-record retention is not centralised | `[VERIFY region and mechanism]` | Scoped permissions, local-password verifier, optional OIDC/PKCE, audit trail | `[CUSTOMER INPUT / LEGAL DECISION]` |
| P-02 | Session, workflow, and approval execution | Operators, customer personnel referenced in work, external contacts | Session/VM IDs, grants, task metadata, approval payload/summary, approver identity, workflow events and outputs | Create/operate agents, workflows, human approvals, scheduled wakes | Enabled model/communication/Git/HTTP providers | Session stores; `orchestration.db`; approvals and wake stores | Provider-specific `[VERIFY]` | Least-privilege grants, approval gates, directory scoping, redacted audit fields | `[CUSTOMER INPUT]` |
| P-03 | Mail and external chat handling | Mailbox owners, senders, recipients, channel/chat participants | Addresses, account IDs, display names, bodies/subjects, attachments, message/thread/channel IDs, reactions | Read, draft, send, ingest, route, and retain messages only within configured grants | Microsoft Graph/Teams, Gmail, Discord; only if enabled | Email/draft/cursor stores, Teams/Discord inbox stores, Team state, artifacts | `[VERIFY each provider]` | Mailbox/folder/channel grants, recipient controls, sending approval options, conditional, operation-triggered 90-day Teams/Discord cleanup, 7-day artifact TTL | `[CUSTOMER INPUT: allowed mailboxes/folders/channels and retention]` |
| P-04 | Calls, voice sessions, and meetings | Callers, contacts, meeting participants | Contact/phone-related identifiers, prompts, transcript text, summary, decisions/action items, meeting metadata | Originate/receive calls; join meeting; transcribe/reconcile conversation | OpenAI Realtime, Retell, DIDWW/Asterisk, Teams meeting bridge, Graph; conditional | Voice-session and Teams-meeting stores, DIDWW bridge calls/request index, Retell local call store | `[VERIFY every media/transcript route]` | Caller allowlists, session binding, redaction/hashing of certain number forms; specified DIDWW paths avoid raw-audio persistence; every other path requires separate verification | Proposed release control: complete a configuration-specific high-risk screen and verify a supported or reviewed manual transcript lifecycle; contractual wording alone does not prove cleanup. |
| P-05 | AI inference and agent collaboration | Any persons represented in prompt/context/content | Prompt content, model output, code snippets, conversation context, agent/session/task identifiers | Route explicitly selected agent/context to model and return output; agent-to-agent prompts | OpenAI API/Realtime, ChatGPT subscription, Anthropic, configured local endpoint, Jev/Typesafe classifier | Agent prompt store (responded/closed records updated over 90 days ago, operation-triggered), consultation records (default 30 days after full cleanup), workflow/context/workspaces | `[VERIFY model account, region, retention/training setting and transfer basis]` | Host-held keys, relays/proxies, restricted worker egress, session scopes; no content-classification guarantee | `[CUSTOMER INPUT: allowed model routes and content classes]` |
| P-06 | Team collaboration, memory, and work management | Operators, customer staff and people represented in agent work, persons named in work items | Chat body, notes, task/case/deliverable data, attachments, memories, external-chat linkage, activity metadata | Team task and chat coordination, semantic memory/indexing, reporting and review | Configured models, chat/Git/connector providers, vector/index service if external | `addons/team-addon/state.db`, attachment roots, semantic memory/index, usage store | `[VERIFY]` | Role/directory visibility, session-scoped actions, activity retention (30 days) | Define retention and erasure for Team records and semantic copies before production use. |
| P-07 | Repository, workspace, and code-index operations | Repository contributors and any persons described in source/docs/issues | Commit author names/emails, repository content, issue/PR comments, build output, code-index text/embeddings | Clone/prepare worktree, run agents, create branches/PRs, index/search code | Customer Git upstream, Azure DevOps or other configured Git provider, vector/index service | Managed bare repos/worktrees, worker/VM workspaces, code-index snapshots | `[VERIFY]` | Exact-repository/session ref scopes, restricted Git keys, code-index retention default 30 days with active/protected exceptions | Customer must decide whether source may contain personal/special-category data and set deletion/hold rules. |
| P-08 | Website/provider credential operation | Named account owners and administrators | Account identifier, profile metadata, access/refresh tokens, PATs, credential fields | Store and use customer-authorised connections to access specified provider/website | Connected target/provider; no guest secret disclosure | Store-specific encryption and host secret references; Teams encrypted envelope after first-access migration in this branch | `[VERIFY]` | Host-side encryption and restrictive file modes for supported stores; removal does not itself revoke upstream grants | Define account ownership, consent/authority, revocation SLA, and offboarding instructions. |
| P-09 | Security, audit, support, and incident handling | Operators, users referenced in agent/session events, reporters | Actor/session IDs, timestamps, provider/repo context, redacted arguments/results, support/incident evidence | Authorise, diagnose, investigate incidents, demonstrate operations | Hosting/monitoring/support providers `[VERIFY]` | `audit.jsonl` and index; privacy incident/evidence records; operational logs/backups `[VERIFY]` | `[VERIFY]` | Key-based recursive redaction, authorization, audit visibility | Approve audit retention, access roles, redaction coverage, and evidence/backup holds. |

## Additional processing copies to record

Include `model-usage.db` in P-05/P-09: provider/model and request/session/workflow
correlation, timestamps, status, usage metadata and request/response hashes.
Jev classification sends state and questions to the Typesafe endpoint; its legal
entity and role remain unverified. P-06/P-07 must include durable published
archives, artifact-space versions and guest source artifacts, not just seven-day
communication staging. P-09 must include the privacy cases, holds, export content
and incident records introduced by this branch.

See [privacy operations](../privacy-operations.md) for implemented capabilities.
Structured location/export is partial, automated erasure is staging-only, and
privacy holds do not govern every existing compactor. These rows describe
processing scope, not a completed ROPA or a claim of complete rights fulfilment.

## Customer-instance completion checklist

For every retained row, record:

1. customer/controller contact and documented instruction reference;
2. purpose and lawful basis supplied by the controller where required;
3. enabled feature, provider, tenant/account, scopes, and environment;
4. data categories actually permitted, including any prohibited classes;
5. recipient, subprocessor authorisation status, country/region, and transfer
   mechanism;
6. retention period, deletion trigger, legal-hold owner, and backup deletion lag;
7. security annex version and exceptions accepted by the controller;
8. DSR and breach-notification contacts and contractual timeframes.

## Controller-only record prompts

If the legal review concludes that Evander is controller for a row, create a
separate controller ROPA entry containing the controller identity/DPO,
processing purpose and lawful basis, data categories/subjects, recipients,
third-country transfer data, retention, and technical/organisational measures.
Do not mix that record with customer-content processor activity.

## Approval log

| Review | Name/role | Decision | Date | Evidence/reference |
| --- | --- | --- | --- | --- |
| Technical owner | `[OWNER]` | `[PENDING]` | `[DATE]` | `[REF]` |
| Security owner | `[OWNER]` | `[PENDING]` | `[DATE]` | `[REF]` |
| Privacy/DPO | `[OWNER]` | `[PENDING]` | `[DATE]` | `[REF]` |
| Customer controller | `[OWNER]` | `[PENDING]` | `[DATE]` | `[REF]` |
