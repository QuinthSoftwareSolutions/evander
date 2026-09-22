# Privacy operations: implemented foundation and remaining work

This release supports one customer per installation. It is a staged engineering
foundation, **not completion of the privacy-readiness roadmap or a compliance
certification**. No legal retention periods, provider agreements, production
settings, or customer-data cleanup are approved by installing this release.

## Coverage

| Capability | Implemented | Boundary |
| --- | --- | --- |
| Inventory and policy | Revisioned privacy configuration and explicit per-dataset capability inventory | Inventory is not proof of complete discovery or deployed-provider coverage |
| Retention and holds | Communication attachment **staging** expiry, preview, approval, retry, and explicit-record holds | Does not erase already delivered attachments, source messages, worker copies, or backups |
| DSR workflow | Scoped cases, selected structured locators, verification/instruction references, jobs, encrypted allowlisted-field review exports, second-review release | Not a complete subject index; email/draft/prompt lookup, unstructured search, rectification, restriction, and broad erasure remain manual |
| Outbound approval checks | Model relay, classifier dispatch/retries, installation-wide Codex subscription egress | Other host-side outbound paths are not covered; installation-wide enforcement activation is refused by the privacy publish action |
| Operations | Scoped Admin Web/CLI, external follow-ups, incident references, bounded maintenance, minimal read-access evidence | No automatic controller/regulator notification, external deletion, backup suppression, or legal decision |
| Storage safety | Cross-process voice/meeting/DIDWW JSON transactions and encrypted Teams delegated credentials | No new voice, meeting, DIDWW or Team-wide deletion capability |

`coverage_complete` and readiness remain false. Unsupported operations return an
explicit error, never a successful empty deletion. Large/unknown source shapes
require manual extraction. Directory scope is an authorization boundary inside an
isolated installation, not a claim of shared-host tenant isolation.

### Why voice cleanup is not enabled

Terminal call flags do not establish cleanup completion: reconciliation can still
enqueue transcript prompts, stopped meeting bindings can still publish events,
and DIDWW still performs final gateway synchronization and telephony cleanup.
Queries also have durable wake/dispatch copies. Safe erasure requires coordinated
producer guards, durable completion markers, tombstones at creation/replay paths,
explicit bridge-store ownership, and a resumable cross-store cleanup ledger. The
new file locks are a prerequisite, not a substitute for those changes.

## Access and configuration

Use **Privacy** in Admin Web. Authentication is required even on otherwise
unauthenticated development deployments. Root-scope administration requires the
installation owner. Child-directory grants are separate: `privacy.read`,
`privacy.configure`, `privacy.cases`, `privacy.approve`, and `privacy.export`.
They are not automatically assigned to ordinary operators. Mutations also require
the existing privileged-operations unlock.

Privacy configuration is the `privacy` directory domain (`privacy.json`) with a
single `privacy` object. The existing `enable_directory_scoped_config` feature
must permit configuration writes. Every operator mutation requires the current
snapshot revision; conflicts require reloading and reviewing again.

The trusted-host CLI uses the operating-system UID as actor. Access to it confers
host-administrator authority; it is not a replacement for web RBAC.

```bash
evander-cli privacy status --config runtime --directory-path root
evander-cli privacy action retention.preview --config runtime --directory-path root \
  --expected-revision <current-revision> --payload-file <reviewed-request.json>
evander-cli privacy job <job-id> --config runtime --directory-path root
evander-cli privacy maintenance --config runtime --limit 50
```

For `retention.preview`, the payload is
`{"dataset":"communication_artifacts","limit":50}`. A preview changes no source
data. Inspect its exact record references, fingerprints and cutoff before using
`jobs.approve` with `{"job_id":"<job-id>"}`. `jobs.run` executes only an approved,
still-current selection. `jobs.cancel` stops remaining work; it cannot undo an
already completed deletion. The web approval button requires the matching current
job detail to have been loaded.

### Retention defaults

Provider mode defaults to `report_only`; no new scheduled deletion is configured.
Existing store-specific cleanup continues. Source-specific settings remain
authoritative: staging uses its stored `expires_at` (normally seven days), not a
second independently editable lifetime in the privacy policy.

An example **shape**, requiring a real controller/legal approval reference before
publication, is:

```json
{
  "config": {
    "mode": "report_only",
    "enabled_categories": ["unknown"],
    "retention": {
      "communication_artifacts": {
        "trigger": "source_expiry",
        "approved": true,
        "approval_reference": "REPLACE-WITH-APPROVED-POLICY-REFERENCE",
        "scheduled": false
      }
    }
  }
}
```

Publish through the `configure` action, not direct file edits. Set `scheduled` true
only for explicitly approved automatic cleanup. Scheduling is installation-owned
at root; scoped manual previews/jobs remain available. Other lifecycle policies
cannot be scheduled until their adapters implement them.

Holds require explicit record IDs, reason and review date. Their review date does
not release them. They also stop opportunistic staging cleanup; held expired files
are preserved but remain inaccessible through normal expired-artifact reads.

The installer renders `evander-privacy-maintenance.service` and its timer. The
timer runs after boot and one minute after each completed pass, with a 55-second
service timeout and no guest-facing port. Metadata scanning uses a durable cursor;
source scans and selections have explicit limits. Failed jobs retry at most three
automatic failed attempts before operator intervention. Check `errors`, job state,
source coverage and systemd failure status; a timeout is not successful cleanup.

## Cases, review exports and incidents

1. Record the controller, request type, approved identifier keys and deadline.
   Record identity verification as a reference, not a copied identity document.
2. Run `cases.locate`. Results are exact structured-identifier matches. Review
   manual/failed datasets and unattributed legacy records; absence of a match is
   not proof that a person is absent from free text or another system.
3. Record controller instructions and verification before selecting export or
   erasure. Existing record fingerprints and ownership are rechecked at approval
   and execution. Stale jobs must be cancelled and freshly prepared.
4. Exports contain allowlisted fields only. Attachment exports currently contain
   metadata, **not attachment bytes**; arbitrary bridge/prompt metadata and
   credential-store values are excluded. Free-form transcripts can still contain
   secrets or third-party information; inspect them and the listed omissions.
5. Export content is encrypted at rest under `privacy-exports/` with a protected
   `.key`. Authorized reviewers can download awaiting-review content. A different
   actor must supply a content/redaction review reference to release it. Release
   is a recorded decision, not automatic transmission to a data subject. If
   redaction is required, prepare a separate controlled response; this release has
   no in-product content-redaction editor and does not rewrite the reviewer archive.
6. Exports expire after seven days by default (configurable 1–30 days). Download
   and release revalidate current case authority/policy and expiry. The timer
   removes expired managed export files. Export copies downloaded elsewhere are
   the reviewer's responsibility.
7. Assign provider deletion, backup expiry and unsupported operations as external
   follow-ups with owners, deadlines and evidence. Full technical closure remains
   unavailable; controller-approved closure with explicit exceptions is supported.

`privacy export-review <job-id> --destination <new-file> --config runtime` creates
a mode-0600 local reviewer copy and refuses to overwrite an existing file. It is
plaintext after decryption; use an approved workstation and transfer method, and
remove this unmanaged copy under the review procedure.

Incident records capture references to restricted evidence, containment and
notification decisions. They send no messages. Follow the reviewed
[breach runbook](privacy-review/personal-data-breach-runbook.md) for roles and timing.

Cases, job references, holds and minimal access evidence are themselves personal
or linkable data in `privacy.db`; broad automatic evidence retention is not yet
implemented. Include them in the approved manual retention/backup procedure.

## Provider checks and deployment compatibility

Approvals bind an exact configured endpoint and credential reference, capabilities,
declared categories, review reference, reviewer, status and optional expiry. They
do not independently verify the external legal entity, account, residency or
contract. Verify those facts and reapprove account/credential changes.

Only the instrumented entrypoints currently enforce approvals. Semantic
embeddings, direct voice/telephony, workflow model calls, HTTP/browser targets,
connectors, email/chat, Git publication and add-on traffic still need coordinated
enforcement. Do not interpret a manually configured `mode: enforce` as a global
egress guarantee; the supported privacy configuration action refuses that claim.

Subscription egress has installation-wide approval only, for all enabled
categories. Its service explicitly selects `--runtime-dir <installation runtime>`;
custom deployments must do the same. Persistent relay/proxy connections recheck
policy; revocation cannot recall bytes already delivered.

Approved relay paths no longer use implicit environment HTTP proxies or follow
WebSocket redirects. Deployments depending on those behaviors need an explicitly
reviewed configured route before upgrading. Unknown categories cannot be silently
treated as public data.

## Upgrade, recovery and remaining milestones

- No live service deployment or customer-data deletion is performed by this code
  change. Validate in an isolated fixture installation before enabling schedules.
- Stop all Teams credential consumers during upgrade, initialize the managed
  credential store, preview `evander-cli credentials migrate --family teams`, and
  apply it only with `--apply --maintenance`. Managed custody in
  `runtime/credentials/credentials.db` is authoritative once a directory has a
  `teams_delegated_credential_ref`; there is no fallback to the legacy JSON file.
- Migration accepts both original plaintext JSON and the transitional
  `evander.teams-credentials.fernet.v1` envelope produced by earlier privacy builds.
  The latter requires its matching `secrets/<credential-JSON-filename>.key` and
  fails closed if the key is missing or invalid. A successful complete import
  retires the source and its transitional key after taking an encrypted managed-store
  recovery snapshot. Retained identities keep both files for a later reviewed pass.
- Back up the managed database and its external master key together under approved
  custody. Old binaries cannot read managed references; rollback requires controlled
  migration or reauthentication, not blind restoration of configuration.
- Test restoration in quarantine. Reconcile authoritative controller instructions,
  completed job evidence, restrictions and external actions **manually** before
  enabling ingest/workers/outbound connections. Automated restore suppression is
  not implemented and cannot be asserted by a successful restore alone.
- Remaining roadmap work: safe voice/meeting producer lifecycle; email/chat/Team/
  workflow/audit/Git/index lifecycle adapters; complete DSR correlation and
  restriction/replay suppression; audit minimization/retention; all-route provider
  enforcement; backup suppression and production DSR/incident/restore exercises.

Use the [data map](privacy-data-map.md) and [legal-review pack](privacy-review/README.md)
to record legal, customer and deployment decisions separately from these technical
capabilities.
