# Personal-data breach runbook

**Status:** draft operational runbook for legal/DPO review. It applies when a
security event may involve accidental or unlawful destruction, loss, alteration,
unauthorised disclosure of, or access to personal data. It does not decide that
an event is reportable or replace the customer's incident process.

IMY explains that a processor must notify the controller without undue delay;
the controller retains responsibility for notification to the supervisory
authority and, where applicable, affected people. The controller's notification
assessment may have a 72-hour deadline from awareness. See
[IMY's incident guidance](https://www.imy.se/pui) and
[processor duties](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/personuppgiftsincidenter/personuppgiftsbitradets-skyldigheter/).

## Activate this runbook for

- suspected access to a runtime store, credential, backup, worker/VM workspace,
  Git repository, transcript, mailbox/chat record, audit log, or provider account
  by an unauthorised person;
- disclosure to a wrong recipient, tenant, directory, provider, model route, or
  public link;
- loss, corruption, ransomware, accidental deletion, or unavailability that may
  affect personal data; or
- an external provider, customer, researcher, employee, agent, monitoring alert,
  or data subject reporting a possible privacy/security event.

When uncertain, open an incident and preserve evidence. The privacy lead decides
whether it is a personal-data breach; do not suppress a report because the
impact is not yet known.

## Implementation coverage

This branch introduces privacy incident records and evidence-reference fields.
See [privacy operations](../privacy-operations.md). Records do not automatically
detect/classify a breach, notify controllers/authorities, apply preservation to
every store, or prove restoration safety. The actions below remain an assigned
manual runbook requiring approved contacts, deadlines and deployment evidence.

Investigation scope must include the separate DIDWW bridge transcript store,
Jev/Typesafe classification recipient, model usage attribution, durable published
artifacts and the new privacy case/export evidence itself. Treat pre-migration
Teams credential JSON/backups as potentially plaintext. For migrated files,
confirm matching key custody and exposure; encryption in source is not proof
that the deployed key and data were protected in the incident.

## Roles and contact record

| Role | Responsibility | Named primary / backup |
| --- | --- | --- |
| Incident commander | Coordinates containment, timeline, decisions, and recovery | `[OWNER]` |
| Technical lead | Scopes systems/data, preserves evidence, executes safe containment | `[OWNER]` |
| Privacy lead / DPO | Advises on personal-data breach classification, controller notice, documentation and risk | `[OWNER]` |
| Customer/controller liaison | Uses the DPA contact path and tracks acknowledgement/requests | `[OWNER]` |
| Legal / communications | Approves statements, regulator/individual communications where authorised | `[OWNER]` |
| Provider liaison | Opens vendor cases and obtains their scope/timeline evidence | `[OWNER]` |

Maintain a restricted, tested contact list per customer/environment. If Evander is
the controller for a processing activity, name the applicable authority/DPO path
separately from the processor-customer liaison.

## Timeline and first actions

| Time from awareness | Action | Evidence to retain |
| --- | --- | --- |
| 0–30 minutes | Open incident, assign commander, record awareness time/source, protect ticket access, identify customer/environment and likely role. | Incident ID, reporter, immutable timeline start. |
| 0–2 hours | Contain safely: revoke/rotate exposed credential if appropriate, disable unsafe integration/route, stop public link or worker, preserve logs/snapshots before destructive cleanup. | Exact actions, approver, timestamps, affected configuration/version. |
| 0–4 hours | Establish preliminary scope: systems, records/people/categories, recipients, time window, ongoing exposure, encryption/redaction state, providers and backups. | Evidence references, known/unknown list, no raw content in broad channels. |
| As soon as processor awareness is confirmed | Notify every affected controller through the DPA contact route **without undue delay** and follow the agreed contract timeframe. Give preliminary facts and commit to updates. | Delivery/acknowledgement time and copy of approved notice. |
| Before controller decision deadline | Supply the controller with continuously updated scope, mitigation, risks and provider facts so it can assess supervisory-authority/individual notification. | Versioned controller-assistance report. |
| Recovery | Eradicate cause, restore safely, monitor, validate access/retention, and document residual risk. | Recovery plan, validation, rollback/corrective actions. |
| Closure | Privacy/legal approves incident record, customer update, lessons and control changes. | Final report, approvals, backlog links, review date. |

The intervals above are operational targets, not a replacement for the DPA or
the controller's legal assessment. Do not wait for perfect information: provide
incremental, clearly labelled updates.

## Containment guardrails

1. Do not delete logs, databases, workspaces, provider records, or backups merely
   to make evidence disappear. Preserve a restricted forensic copy before a
   lawful remediation action where feasible.
2. Do not paste secrets, raw transcripts, mail bodies, source code, or broad
   data-subject lists into chat, tickets, or an unencrypted email.
3. Do not restart, rotate, or revoke a service blindly if it would destroy the
   evidence needed to identify the scope; use a documented containment decision.
4. Use the normal host/provider change controls where they remain safe; record
   emergency deviations and validate them afterwards.
5. Do not independently notify a data subject, authority, or media about
   customer-controller content unless the contract and controller expressly
   authorise it.

## Investigation worksheet

| Topic | Required finding |
| --- | --- |
| Event | What happened, detection source, occurrence window, awareness time, current status |
| Processing role | Evander processor/controller role for each affected customer/data set; DPA/instruction reference |
| Systems | Runtime stores, hosts/workers, Git/index, models, communication/telephony, backups, logs and provider accounts involved |
| Personal data | Categories, approximate records and people, content sensitivity, pseudonymisation/encryption/redaction, special-category/children/vulnerable-person possibility |
| Exposure | Who actually or potentially accessed/received it, tenant/directory boundary, external transfer/countries, public availability, ongoing access |
| Impact | Confidentiality, integrity, availability and likely consequences for people; controller/DPO risk assessment input |
| Measures | Containment, credential/session changes, provider actions, recovery, prevention and remaining risk |
| Notifications | Controller(s), provider(s), authority/individual decisions owned by controller, contact names, times, delivery acknowledgement |
| Evidence | Restricted paths/ticket IDs/log hashes; chain of custody; retention/hold decision |

## Initial controller notification template

Send only through the approved DPA contact route. Mark uncertain facts as
preliminary; do not include secrets or excessive raw content.

```text
Subject: Preliminary personal-data incident notice — [customer/environment] — [incident ID]

We became aware of a suspected/confirmed personal-data incident at [time, timezone].

What is known: [plain-language event and affected Evander service].
Processing role and affected environment: [processor/controller context, tenant].
Potential data categories and approximate scope: [categories/counts or unknown].
Potential recipients/exposure: [known/unknown].
Containment taken: [actions and current status].
Current assessment: [confirmed facts, open questions, ongoing risk].
Requested controller action: [contact confirmation, instruction, preservation, other].

We are investigating and will provide the next update by [time].
Evander incident contact: [name/role/contact].
```

## Incident record and follow-up

| Field | Value |
| --- | --- |
| Incident ID / severity | `[ID / LEVEL]` |
| Awareness time / timezone | `[TIMESTAMP]` |
| Affected controller(s) / DPA contact | `[LIST]` |
| Processor/controller role determination | `[REFERENCE]` |
| Controller notices sent / acknowledged | `[TIMESTAMPS]` |
| Authority/data-subject notice decision | `[CONTROLLER/LEGAL DECISION + REFERENCE]` |
| Root cause | `[TECHNICAL FINDING]` |
| Corrective/preventive actions | `[OWNER / DUE DATE]` |
| Retention/hold / evidence location | `[RESTRICTED REFERENCE]` |
| Closure approval / lessons review | `[OWNER / DATE]` |

After every confirmed or near-miss privacy incident, update the data map,
subprocessor register, retention schedule, security appendix, and tests where
the investigation found a missing recipient, store, control, or deletion path.
