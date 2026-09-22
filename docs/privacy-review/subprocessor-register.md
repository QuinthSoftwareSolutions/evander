# Subprocessor and recipient register

**Status:** technical discovery register — it does not authorise a provider or
represent that any listed party is a subprocessor. Legal/procurement must
determine the role, contract, location, transfer mechanism, and approval status
for every customer instance.

The processor must not appoint a subprocessor outside the customer/controller's
documented authorisation. The register supports that decision and the associated
change-notification process. See [IMY's processor-agreement guidance](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/personuppgiftsansvariga-och-personuppgiftsbitraden/personuppgiftsbitradesavtal/).

## Register control

| Field | Value |
| --- | --- |
| Register owner | `[OWNER]` |
| Customer / environment | `[CUSTOMER INPUT / DEPLOYMENT EVIDENCE]` |
| DPA authorisation model | `[SPECIFIC / GENERAL / NOT YET AGREED]` |
| Customer objection contact | `[CUSTOMER INPUT]` |
| Change notification period | `[DPA / LEGAL DECISION]` |
| Last configuration reconciliation | `[DATE / SAFE EVIDENCE REFERENCE]` |

## Candidate recipients discovered from the product

`UNVERIFIED` means the repository contains an integration but live configuration
and the legal role have not been checked. It must not be changed to `N/A` without
recording the disabled configuration evidence.

| Candidate | Integration/use condition | Data that may be disclosed | Provisional role | Status | Contract, location, and transfer evidence required | Customer authorisation |
| --- | --- | --- | --- | --- | --- | --- |
| Hosting / infrastructure provider | Always, once production hosting is selected | Any locally stored runtime, backups, service logs, encrypted credential stores | Potential subprocessor | UNVERIFIED | Legal entity, regions, support access, DPA, subprocessor chain, backup/deletion commitments | `[TBD]` |
| OpenAI API / Realtime | Model relay or Realtime voice/meeting route enabled | Prompt/context, output, code snippets; Realtime audio/transcript-related data | Potential subprocessor | UNVERIFIED | Exact product/account, DPA, data-use/training setting, region, transfer basis, incident contact | `[TBD]` |
| ChatGPT subscription route | Subscription worker profile enabled | Agent prompt/context and tool/model interaction as configured | Potential subprocessor | UNVERIFIED | Exact route/tenant, permitted use, DPA/enterprise terms, region, retention, transfer basis | `[TBD]` |
| Anthropic / local model endpoint | Selected model relay/profile enabled | Prompt/context and output | Potential subprocessor or customer-controlled recipient | UNVERIFIED | Endpoint owner, terms, location, DPA, access and retention | `[TBD]` |
| Jev / Typesafe classification | Classification provider enabled/invoked; source endpoint `https://api.typesafe.ai/v1/systemone` | Submitted state, classification questions/choices, model selection; responses and usage | Potential subprocessor or customer-selected recipient; actual legal entity unverified | UNVERIFIED | Receiving entity/account, DPA/role, country/region, purpose, retention/training terms, transfers and deletion route | `[TBD]` |
| Microsoft Entra ID / Graph / Teams / Azure DevOps | SSO, email, Teams, meeting, Git, or work-item feature enabled | Account/identity data, messages, meetings, mailbox data, Git/PR/work-item content | Potential subprocessor or customer-selected provider | UNVERIFIED | Tenant, delegated/application scopes, regional terms, DPA, deletion boundary, transfer analysis | `[TBD]` |
| Google / Gmail | Gmail mailbox provider enabled | Mail content, addresses, drafts, attachments, account metadata | Potential subprocessor or customer-selected provider | UNVERIFIED | Workspace/consumer account type, OAuth scopes, DPA, region/transfer, retention | `[TBD]` |
| Discord | Bot/webhook communication target enabled | Channel content, participant IDs, attachments, webhook/persona metadata | Potential subprocessor or customer-selected provider | UNVERIFIED | Server scope, terms/DPA position, retention and transfer assessment | `[TBD]` |
| Retell | Retell phone backend enabled | Call metadata, selected contact/prompt/context and provider result | Potential subprocessor | UNVERIFIED | DPA, media/transcript behavior, region, retention, incident/deletion support | `[TBD]` |
| DIDWW / telephony carrier / Asterisk host | DIDWW SIP or hosted telephony route enabled | Calling/called routing data, audio/media metadata; possibly media | Potential subprocessor or communications provider | UNVERIFIED | Carrier role, hosting, CDR/media retention, locations, lawful call notice, DPA | `[TBD]` |
| Teams meeting media bridge | Meeting-media bridge enabled | Meeting joining data, media/transcript events, session/meeting IDs | Potential subprocessor | UNVERIFIED | Bridge operator/host, Microsoft permissions, audio/transcript route, location, DPA | `[TBD]` |
| Git hosting / issue tracker | Managed Git or work-item integration enabled | Repository content, commit authors, comments, attachments, work items | Customer-selected provider or potential subprocessor | UNVERIFIED | Exact provider/account, customer authority, repository/data retention and transfer terms | `[TBD]` |
| Vector/index service | Code index or Team semantic memory is external rather than self-hosted | Indexed source/memory text, embeddings, metadata | Potential subprocessor | UNVERIFIED | Deployment ownership, region, encryption/access, deletion/rebuild, DPA | `[TBD]` |
| Configured HTTP target / browser proxy / connector | Any target or add-on is enabled | Request/response data, cookies/headers under customer configuration | Usually customer-designated recipient; role must be assessed | UNVERIFIED | Target owner, purpose, allowed data, contract/transfer and authentication boundary | `[TBD]` |
| Support/monitoring/backup provider | Production support, logs, backups, telemetry or alerting enabled | Operational metadata and potentially stored/runtime content | Potential subprocessor | UNVERIFIED | Provider, support access, regions, retention, DPA and incident obligations | `[TBD]` |

## Proposed onboarding approval evidence

Before setting a row to `APPROVED`, attach restricted references—not secrets—to:

1. product and legal entity name, registered address, service description, and
   role assessment;
2. data categories, affected customer environments, and processing purpose;
3. signed/current DPA or data-protection terms and required flow-down terms;
4. hosting/processing locations, onward-subprocessor chain, transfer mechanism,
   and transfer-risk assessment where applicable;
5. security assurance, incident notification contact/commitment, audit/support
   access boundary, retention/deletion and backup treatment;
6. customer written authorisation and, for general authorisation, the change
   notice and objection process;
7. operational owner, review date, and disable/exit plan.

## Implementation coverage

[Privacy operations](../privacy-operations.md) describes the provider approval
configuration and current dispatch boundaries. Instrumentation is limited to
model-relay, classification and Codex-subscription capabilities; enabling policy
enforcement does not cover every communication, Git, HTTP/browser, realtime or
add-on route. Review those paths separately and disable unsuitable routes or use
a verified scoped operating control.

Approvals bind technical connection references and endpoints, not proof of a
vendor legal entity, region or signed terms. Data categories are declared labels,
not detected guarantees. Codex subscription approval is installation-wide for
the declared enabled categories, not per-session attribution. Default report-only
operation is not enforcement. No external provider deletion or customer change
notification is automated by this register.

## Change-control log

| Change ID | Provider/recipient | Change | Impacted customers | Notice sent | Objection deadline | Decision / effective date | Owner |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `[ID]` | `[NAME]` | `[NEW / MATERIAL CHANGE / REMOVAL]` | `[LIST]` | `[DATE]` | `[DATE]` | `[PENDING]` | `[OWNER]` |

## Proposed engineering production gate

Proposed internal control: the deployment owner reconciles the register with
effective configuration before enabling an integration and escalates unknown
recipients for a documented decision. A broad “third-party services” label is not
a verified inventory. This gate does not by itself determine legal selling or
processing eligibility for every deployment model.
