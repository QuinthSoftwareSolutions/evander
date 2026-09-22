# Evander privacy legal-review pack

**Status:** technical draft — not approved policy, legal advice, a DPA, or a
claim of GDPR compliance.

**Prepared:** 2026-09-21 from repository evidence. The baseline technical
inventory is [the privacy data map](../privacy-data-map.md).

This pack turns that map into reviewable operational material. It assumes that
Evander may act as a processor for customer-controlled tenant content and may
act independently as a controller for its own business, account, security, and
support processing. That allocation is an issue for the parties' actual roles,
contracts, and factual operations; it must not be inferred from this template.

## Implementation and legal boundary

This pack now incorporates this branch's privacy-readiness changes, not merely the
original source snapshot. See [privacy operations](../privacy-operations.md) and
the data map's implementation-coverage section. No live deployment or signed
provider terms have been verified.

The implemented dataset registry deliberately reports unsupported operations.
Automated retention/hold/erasure is limited to local communication staging;
selected JSON stores have structured locators and allowlisted field exports.
Case/incident records, provider approval records and safe storage are assistance
tools, not proof that all copies can be exported, erased or restricted.

Customer-managed software and dedicated managed deployments need distinct role
and responsibility records. This pack assumes one customer per installation;
directory scope is not a demonstrated shared-host tenant boundary. Requirements
and release gates below are proposed operating controls for review, not blanket
conditions on selling software. A privacy lead is needed to own decisions; DPO
references mean the DPO where one is appointed or required.

## Contents

| Document | What it supplies | Required approver/owner |
| --- | --- | --- |
| [ROPA technical input](ropa-technical-input.md) | Article-30-style processing rows and evidence fields | Privacy lead/DPO and service owner |
| [DPIA screening pack](dpia-screening-pack.md) | Configuration-specific high-risk screen, risk register, and DPIA handoff | Customer controller/DPO; Evander privacy lead |
| [Subprocessor register](subprocessor-register.md) | Candidate recipient register, onboarding/change gates | Procurement, security, privacy, customer-contract owner |
| [Retention schedule](retention-schedule.md) | Current evidence, proposed lifecycle controls, and approval fields | Privacy/legal, engineering, security, customer owner |
| [Security appendix](security-appendix.md) | Evidence-backed technical and organisational measures for contract review | Security and engineering owner |
| [DSR procedure](dsr-procedure.md) | Processor-assistance workflow for access, correction, erasure, etc. | Privacy lead/DPO and support owner |
| [Personal-data breach runbook](personal-data-breach-runbook.md) | Processor/controller incident workflow and evidence template | Incident commander, privacy lead/DPO |

## Review rules

1. Replace every `[LEGAL DECISION]`, `[CUSTOMER INPUT]`, `[DEPLOYMENT
   EVIDENCE]`, and `[OWNER]` marker before externally relying on a document.
2. Do not include secret values, raw customer content, or live credentials in
   the pack. Reference a restricted evidence record instead.
3. Each signed DPA and customer deployment needs an instance-specific annex:
   controller/processor roles, enabled features, hosting location, processors,
   data-transfer basis, retention overrides, and incident contacts.
4. A non-applicable integration must be recorded as disabled from live
   configuration; a merely unchecked provider remains `UNVERIFIED`, not
   `NOT APPLICABLE`.
5. Legal counsel or the DPO must decide lawful bases, transparency wording,
   whether a DPIA is required, international-transfer mechanisms, record
   retention periods, legal holds, and whether/when to notify authorities or
   individuals.

## Legal-review references

These sources are included to orient the reviewer; the agreement and operations
must be assessed against the applicable law and facts.

- [IMY: records of processing](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/fora-register-over-behandling/)
  summarises the Article 30 record fields for controllers and processors.
- [IMY: processor agreements](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/personuppgiftsansvariga-och-personuppgiftsbitraden/personuppgiftsbitradesavtal/)
  describes the Article 28 processor-contract minimums.
- [European Commission: DPIAs](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/obligations_en)
  describes the high-risk threshold and prior consultation concept.
- [IMY: when to conduct a DPIA](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/konsekvensbedomning/nar-ska-en-konsekvensbedomning-genomforas/)
  provides Swedish criteria for screening.
- [European Commission: handling individual requests](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/dealing-requests-individuals_en)
  summarises response and identity-verification considerations.
- [IMY: personal-data incidents](https://www.imy.se/pui) and
  [processor duties](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/personuppgiftsincidenter/personuppgiftsbitradets-skyldigheter/)
  describe controller/processor notification responsibilities.

## Proposed engineering release gate

Proposed internal control: review new recipients, stores, data categories, model
routes and retention exceptions against this pack before production enablement.
Record owner approval, deployment evidence and any scoped manual procedures or
compensating controls. An unresolved entry is an engineering/operating decision
to escalate, not by itself a legal conclusion that all sales are prohibited.
