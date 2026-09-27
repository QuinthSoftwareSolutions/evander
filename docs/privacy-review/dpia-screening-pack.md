# DPIA screening pack

**Status:** technical screening draft. The customer controller/DPO decides
whether a DPIA is required and owns any completed DPIA. This is not a completed
DPIA or authorisation to start processing.

The technical basis is [the privacy data map](../privacy-data-map.md) and the
[ROPA input](ropa-technical-input.md). IMY notes that a DPIA may be needed when
planned processing meets at least two listed high-risk criteria, while the
European Commission describes DPIAs as required where processing is likely to
create high risk. Review the applicable IMY list and customer jurisdiction before
deciding. See [IMY's guidance](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/konsekvensbedomning/nar-ska-en-konsekvensbedomning-genomforas/).

## Screening record

| Field | Value |
| --- | --- |
| Customer/controller | `[CUSTOMER INPUT]` |
| System/deployment | `[DEPLOYMENT EVIDENCE]` |
| Feature set screened | `[Enabled providers, model routes, communication channels, Team/code-index features]` |
| Purpose supplied by controller | `[CUSTOMER INPUT]` |
| Screening owner | `[OWNER]` |
| Privacy reviewer / DPO where applicable consulted | `[YES/NO, name, date]` |
| Review date / next review trigger | `[DATE] / new provider, new data type, scale, monitoring, model route, or incident]` |
| Decision | `PENDING — do not treat this draft as a no-DPIA decision` |

## Step 1 — describe the actual processing

Complete this with configuration evidence, not a generic product description.

| Topic | Required facts | Instance-specific answer |
| --- | --- | --- |
| People affected | Operators, employees, customers, callers, meeting participants, mailbox/chat participants, repository contributors, or others | `[CUSTOMER INPUT]` |
| Data | Identity/contact data; communication content; call/meeting transcript; source code; authentication/provider data; model output; audit data | `[CUSTOMER INPUT: permitted and prohibited categories]` |
| Sources | Customer users, mailboxes, Teams/Discord, phone/meeting bridge, Git/ADO, configured HTTP targets, support channels | `[DEPLOYMENT EVIDENCE]` |
| Operations | Ingest/read, route, prompt/model inference, store/index/search, send/act, audit, retain/delete | `[Enabled operations]` |
| Scale/duration | Active users, messages/calls/repositories, geographic scope, expected retention | `[CUSTOMER INPUT]` |
| Recipients/transfers | Every enabled provider, model endpoint, hosting/backup, support and subprocessor | `[SUBPROCESSOR REGISTER REFERENCES]` |
| Automated outcomes | Identify any workflow action affecting an individual; distinguish operational automation from a decision about a person | `[CUSTOMER INPUT + workflow review]` |
| Alternatives | Manual operation, no retention, local model, a narrower scope, disabled features | `[ASSESS]` |

## Step 2 — high-risk criteria screen

`Possible` is not a decision. The controller/DPO must replace it with `Yes`,
`No`, or `Not applicable with evidence` for the actual deployment.

| IMY screening criterion | Repository-relevant fact | Initial status | Evidence/decision required |
| --- | --- | --- | --- |
| Evaluation or scoring of people | Workflows/models can summarise, classify, and route information. No product-wide people-scoring purpose is established. | Possible | Review each model/workflow prompt and output use. |
| Automated decisions with legal or similarly significant effect | Workflows may initiate external actions; approval gates exist for certain actions, but a deployment could automate a consequential person-related action. | Possible | Identify decisions, human intervention, appeal/override, and affected persons. |
| Systematic monitoring | Continuous mailbox/chat ingestion, meeting/call flows, or employee-workflow monitoring can be configured. | Possible | Establish cadence, audience, notice, and whether monitoring is systematic. |
| Sensitive or highly personal data | Product accepts free-form content; mail/chat/calls/code may contain special-category, health, financial, credential, or similarly sensitive data. | Possible | Specify allowed/prohibited data and technical enforcement. |
| Large-scale processing | Scale is configuration/customer dependent. | Unknown | Provide volumes, users, duration, geographic scope, and data variety. |
| Matching/combining datasets | Team/workflow features can connect communications, work items, repositories, contacts, and model context. | Possible | Identify every joined source and whether people would reasonably expect the combination. |
| Vulnerable people | The product can be used with employee, customer, child, patient, or other vulnerable-person data unless contractually/configurationally limited. | Possible | Identify affected groups and prohibit unsuitable deployments. |
| Innovative technology or organisational solution | Agentic workflows, semantic indexing, model inference, and call/meeting transcription are enabled capabilities. | Likely | DPO must assess novelty in the customer context. |
| Excluding a person from a service/contract | No default product purpose establishes this, but a customer workflow could use outputs in access/employment/service decisions. | Possible | Prohibit or complete a specific assessment. |

### Preliminary technical conclusion

The product is capable of configurations that meet multiple criteria (for
example, novel AI-assisted processing plus combining communications and
repositories, or continuous transcript handling). The screening outcome remains **undetermined** until the controller and
privacy reviewer assess the actual processing. Proposed internal control: do not
enable a configuration identified as potentially high-risk until that assessment
and any resulting actions are recorded. Product capabilities or use of AI alone
are not a completed determination for every deployment.

## Implementation coverage relevant to screening

The branch now provides a capability-declared privacy registry, cross-process-safe
voice/meeting/DIDWW writes, encrypted Teams credential migration and bounded
provider-approval instrumentation. See [privacy operations](../privacy-operations.md).

Do not reduce residual risk merely because these controls exist in source.
Voice/meeting/DIDWW retention, complete subject location/export, cross-store
erasure/restriction, backup restoration suppression and most recipient paths
remain incomplete or manual. Include Jev/Typesafe classification, the separate
DIDWW store, model usage and published artifacts when describing copies and
recipients. Model-relay/classification/subscription gating is not universal
provider enforcement or a content classifier.

## Step 3 — necessity and proportionality prompts

The controller must complete these; engineering supplies the factual answer and
alternatives.

| Question | Required evidence |
| --- | --- |
| What exact objective cannot be met with less data or a less intrusive flow? | Feature/workflow ID, user story, data minimisation analysis, rejected alternative. |
| What data is prohibited from prompts, transcripts, attachments, and indexes? | Policy/configuration, validation tests, operator instructions, escalation path. |
| Who can access each store and provider, and why? | Role/grant matrix, service-account list, provider scopes, access-review record. |
| How are people informed, and who handles their rights? | Customer privacy notice/DPA instruction, DSR contact and procedure reference. |
| Why is each retention period necessary? | Approved retention schedule, deletion trigger, hold exception, backup lag. |
| How can a person avoid or challenge a consequential automated outcome? | Human-review/approval/override design and customer procedure. |

## Step 4 — initial risk register

Scores are placeholders: `Likelihood` and `Impact` must be set per deployment
using the customer's approved methodology. Residual risk requires DPO/legal
review and may require prior consultation before processing begins.

| ID | Threat / impact on people | Existing technical measures | Required additional measure | Inherent L/I | Residual L/I | Owner / acceptance |
| --- | --- | --- | --- | --- | --- | --- |
| R-01 | Broad or unintended model disclosure of communication/code content | Host-owned model keys, selected relay routes, scoped workers | Model/provider allowlist, account/region/training verification, prompt data policy, per-customer model instruction | `[TBD]` | `[TBD]` | `[OWNER]` |
| R-02 | Excessive retention of transcripts, meeting records, Team chat/memory, drafts, or audit content | Some conditional component TTLs; staging expiry and new staging-only privacy adapter | Implement missing voice/meeting and cross-store retention; deletion evidence and backup-lag policy | `[TBD]` | `[TBD]` | `[OWNER]` |
| R-03 | Unauthorised access or overbroad action by agent/operator | Session/directory grants, tool policies, approvals, host-held secrets | Access reviews, MFA/SSO policy, production configuration verification, security monitoring | `[TBD]` | `[TBD]` | `[OWNER]` |
| R-04 | Combining communication, work, repository, and model data beyond reasonable expectations | Scoped tools and directories | Customer-defined purposes, notices, feature opt-in, data-source/minimisation controls | `[TBD]` | `[TBD]` | `[OWNER]` |
| R-05 | Data-subject request cannot find/delete every local and provider copy | Individual deletion paths, bounded structured locators and partial export fields | Case-managed locator/export/erasure workflow, hold handling, provider follow-up | `[TBD]` | `[TBD]` | `[OWNER]` |
| R-06 | Call/meeting participant lacks appropriate notice or raw-audio/transcript treatment is unclear | Caller policy, number redaction/hashing in specified logs, documented non-persistence on specified DIDWW paths only | Recorded-call/meeting notice, provider/bridge verification, transcript retention, opt-out/escalation policy | `[TBD]` | `[TBD]` | `[OWNER]` |
| R-07 | Cross-border or unauthorised subprocessor transfer | Configured narrow host routes | Approved subprocessor register, DPA/transfer assessment, change notification and blocking gate | `[TBD]` | `[TBD]` | `[OWNER]` |
| R-08 | Inaccurate model output causes unfair or harmful action | Human approvals on some actions; audit evidence | Human review for any person-affecting result, accuracy review, correction/appeal procedure, prompt/workflow tests | `[TBD]` | `[TBD]` | `[OWNER]` |

## Step 5 — decision and change control

| Decision | Approver | Date | Evidence |
| --- | --- | --- | --- |
| DPIA not required | `[Controller/DPO only]` | `[DATE]` | Criteria analysis and jurisdictional basis |
| DPIA required and complete | `[Controller/DPO]` | `[DATE]` | Restricted DPIA reference and residual-risk decision |
| Prior consultation required | `[Controller/DPO/legal]` | `[DATE]` | Restricted legal record |
| Feature blocked pending controls | `[Service owner]` | `[DATE]` | Linked backlog/release gate |

Re-screen before a material change to data types, data subjects, scale, recipient,
model route, retention, automated outcome, or security posture, and after a
relevant incident.
