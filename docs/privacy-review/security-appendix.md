# Security appendix — technical and organisational measures

**Status:** evidence-based technical draft for DPA/customer security review.
It describes repository capabilities, not a guarantee that every control is
deployed, configured, monitored, or independently certified.

## Scope and evidence boundary

The controls below concern the Evander host control plane, guest VMs,
container-workers, Admin Web, relays, workflow runtime, Team add-on, and
configured integrations. Deployment evidence must confirm the actual version,
configuration, service identity, region, perimeter, backups, and vendor terms.

| Control area | Repository-supported measure | Operational evidence required | Known limitation / review point |
| --- | --- | --- | --- |
| Network segmentation | Guest/worker network is default-deny and host exposure is based on narrow allowed ports; dedicated relays/proxies constrain model, package, Git, and HTTP routes. | Firewall/NSG rules, host listening sockets, worker CIDRs, DNS/proxy policy, change history. | Actual firewall and cloud perimeter cannot be inferred from source. |
| Identity and authentication | Admin Web supports local accounts and optional Entra OIDC Authorization Code + PKCE; Admin users/groups and directory-scoped grants are persisted. | Enabled auth mode, MFA/conditional-access policy, bootstrap-admin record, access-review evidence, cookie/TLS settings. | Password policy, MFA, session duration, and IdP tenant policy require live review. |
| Authorisation / least privilege | Session manifests restrict tools, connectors, host directories, communications, mailboxes/folders, contacts and scripts; managed Git is session/ref scoped. | Effective per-session policy/configuration and periodic entitlement review. | Customer workflows can broaden processing through enabled integrations; policy must be reviewed per deployment. |
| Approval for sensitive actions | Tool and workflow approval gates, TTLs, exact-payload checks, and notification paths exist for configured actions. | List of approval-required actions, approver groups, segregation of duties, approval-log review. | Approval is not a substitute for a lawful basis, notice, or DPIA. |
| Secret isolation | Provider secrets are intended to stay host-side; guests receive explicit relay/capability access rather than unrestricted secrets. | Service-account ownership, environment-file permissions, secret-manager configuration, rotation evidence, no-secret scan results. | Host environment and deployment secret management are outside source proof. |
| Credential protection | Website credentials use Fernet with host-held keys; ADO supports encrypted credentials. This branch adds Fernet envelopes for Teams single/identity credentials, including account metadata, with JSON/key 0600 and secrets directory 0700. | Key custody/rotation/backup separation, operator access, revocation tests. | Teams plaintext migrates one-way on first access; stop/restart writers together and restore the matching key. This does not encrypt all runtime databases/logs/workspaces or pre-existing backups. |
| Input/output and audit redaction | Gateway audit redacts configured key names recursively; common token-like keys are included. Audit records tool decisions and context. | Effective `redacted_keys`, sample safe audit review, log access role, external log sinks. | Key-name redaction can miss personal data in free-form values or unlisted keys; audit retention is not yet centralised. |
| Data minimisation / isolation | Directory/session scopes, host-directory traversal controls, connector schemas, communication target/mailer grants, and restricted browser/HTTP target routes narrow access. | Customer scope design, target inventory, test evidence that unwanted paths/targets are denied. | Free-form prompts, code, mail, chat, and transcripts may contain arbitrary personal data. |
| Worker isolation and cleanup | Disposable VM/container execution is supported; session/worker cleanup revokes scoped access and removes worker-side state according to backend paths. | Image provenance, runtime hardening, cleanup logs, failed-cleanup alerts, volume/disk sanitisation, patch level. | Guest/workspace, Git, and provider copies require lifecycle verification; cleanup is not a universal erasure workflow. |
| Availability and resilience | Systemd/service health, audit, workflow state, idempotency and bounded retry mechanisms exist. | Monitoring/alerting, incident response, backup/restore tests, RTO/RPO, capacity and DDoS controls. | No production availability/SLA claim should be made from repository evidence alone. |
| Secure development | Python tests, focused integration tests, review gates, exact-SHA Git flows, and release evidence mechanisms are available. | SDLC policy, code-review enforcement, dependency/vulnerability scanning, change control, SBOM, penetration tests. | Tooling availability does not prove organisation-wide operation. |
| Incident response | Audit trails and scoped controls support investigation; this branch adds privacy incident record types and this pack supplies the manual breach runbook. | On-call roster, tabletop exercise, customer contacts, forensic preservation, notification tests. | Incident records do not send notifications or decide legal thresholds. Production on-call, contacts, clocks, exercises and evidence-retention policy remain unverified. |

## Data-protection-relevant product controls

| Need | Current technical position | Required evidence / gap |
| --- | --- | --- |
| Confidentiality | Host-held secrets, encrypted credential stores for supported surfaces, scoped relays, authentication/authorisation, and default-deny networking are designed controls. | Confirm encryption, TLS, access, and logging in every deployed environment. |
| Integrity | Approval flows, exact-payload checks, idempotency, revision checks, audit records and constrained Git push paths reduce unauthorised changes. Voice/meeting/DIDWW now use process-shared transaction locks and durable atomic writes; malformed store structures fail closed. | Define integrity monitoring, privileged-access review, rollback, and evidence retention. |
| Availability | Orchestrator locks, health/status surfaces, bounded workers, retries, and cleanup flows exist. | Confirm backup, restoration, DR, alerts, capacity and actual operating procedures. |
| Resilience/testing | Focused tests exist for many components and the architecture supports isolated execution. | Establish a documented security test programme and production control verification cadence. |
| Data lifecycle | Several TTL/compaction paths exist. | Automated privacy lifecycle covers communication staging only; voice/meeting/DIDWW retention and cross-store erasure/restriction remain unresolved engineering/operating gaps. |

## Implemented privacy controls and limits

See [privacy operations](../privacy-operations.md) for source-backed capabilities.
The dataset registry explicitly distinguishes supported operations from manual
ones. Staging-only retention/holds/erasure, selected structured locators and
allowlisted exports do not amount to complete DSR support. No general
rectification/restriction or backup/provider deletion capability is established.

Provider approval instrumentation covers model relay, classification and Codex
subscription routes only. It is not universal egress enforcement or a guarantee
that sensitive data is detected. Jev/Typesafe classification is itself an external
recipient. Model usage and durable published artifacts remain separate retained
datasets. Proposed evidence/release requirements in this pack are review controls,
not a certification or a blanket legal prerequisite to selling software.

## Minimum production evidence pack

Before a customer relies on this appendix, attach the following restricted
evidence references:

1. deployment architecture, data locations, cloud/hosting account and region;
2. current software version, change approval, service accounts, and environment
   configuration review (without secret values);
3. network/firewall/TLS/IdP/MFA/access-review evidence;
4. key/secret rotation, encryption, backup/restore, vulnerability/patch and
   endpoint-hardening evidence;
5. active provider/subprocessor register and data-transfer review;
6. retention cleanup/reconciliation and DSR deletion test results using
   synthetic data; and
7. incident exercise, customer notification contact, and support-access records.

## Customer shared-responsibility prompts

| Customer responsibility to confirm | Evander responsibility to document |
| --- | --- |
| Controller instructions, lawful basis, notices, permitted data classes, configured users/workflows and approval roles | Product configuration boundaries, security controls/limitations, processor assistance, incident notification route, and authorised subprocessors |
| Mailbox/Teams/Discord/Git account permissions and participant notices | Narrow host/guest access, provider integration configuration, audit/redaction behaviour, retention/deletion implementation |
| Customer-side identity, endpoint, network and repository controls | Control-plane hosting, service account/secret handling, operational monitoring and access management |

## Exceptions

Any exception to this appendix requires an owner, scope, risk assessment,
customer approval where relevant, compensating controls, expiry date, and an
entry in the DPIA/ROPA/retention/subprocessor records. The deployment owner must review expired exceptions and record renewal,
disablement or other approved action; source code does not establish automatic
enforcement for every exception or feature.
