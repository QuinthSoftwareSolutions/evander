# Managed credentials

Evander's first managed-credential release covers delegated Teams identities and generic
connector bearer tokens, API keys, Basic authentication, and OAuth client credentials.
Configuration contains opaque `cred_…` references. Only host adapters resolve values.
The directory owner manages values through **Configuration → Access, Approvals & Audit →
Credentials**; mutations also require unlocked privileged operations.

This is an in-process credential domain. Code with access to the master key can decrypt the
whole database. Shared service UIDs remain a shared trust boundary. This release does not move
personal ADO connections, website sessions, model keys, worker subscription credentials,
password verifiers, or session tokens. Copilot token export and historical payload retention
remain separate work.

## Storage and restart

`runtime/credentials/credentials.db` holds AES-256-GCM encrypted bundles and public metadata.
Authenticated metadata includes the owner, type, consumer bindings, status, expiry and both
revision counters. Files are private, directories are mode `0700`, and symlinked storage paths
are rejected. SQLite transactions serialize writers; updates compare the submitted revision.
Teams refresh also takes a process-shared per-credential lock.

The default 32-byte master key is `/etc/evander/credentials/master.key`, outside runtime
backups. Initialization is explicit and refuses to generate a replacement key for an existing
database. Use the deployed checkout and actual service user in these examples:

```bash
sudo /opt/evander/.venv/bin/evander-cli credentials init \
  --runtime-dir /opt/evander/runtime --store-user evander
sudo /opt/evander/scripts/install_systemd_units.sh
```

The installer registers the protected key under `/etc/credstore/evander-credentials-key` and
adds `ImportCredential=evander-credentials-key` only to gateway, Admin Web, trigger/effect
orchestrators, Teams ingest, Teams notifications and Teams meeting ingest. Ubuntu 24.04's
systemd supports this mechanism. No credential port or new host daemon is added, and worker
containers receive neither the store nor its key. Restart the affected units after installation
so systemd supplies their private credential files. See [systemd credentials](https://systemd.io/CREDENTIALS/).

Key selection, in order:

1. CLI `--key-file` (a protected file path, never a value).
2. `EVANDER_CREDENTIALS_KEY_FILE` (path only, for an explicitly provisioned host process).
3. `$CREDENTIALS_DIRECTORY/evander-credentials-key` supplied by systemd.
4. `/etc/evander/credentials/master.key` for privileged operator commands.

There is no fallback after a selected key is missing or incorrect. Metadata and diagnostics
remain accessible when the key is missing; credential-dependent operations fail closed.
Existing installations continue using legacy credentials until explicitly migrated.
The setup wizard retains legacy bootstrap secrets; credential-store initialization uses the
command above. Ansible can initialize the store before rendering services with
`evander_initialize_credential_store: true`; its default is `false`. It preserves existing keys
and fails on a missing key for an existing store.

## Connector ownership and use

First configure the connector and its authentication protocol on the Connectors page. For a
legacy profile, Credentials accepts the required values and atomically switches that directory's
profile to managed authentication using the configuration revision. Values are never returned
by the admin API. Replacing values requires the displayed credential revision. Failed UI
submissions clear value inputs.

Managed profile examples (the reference comes from Credentials):

```json
{"type":"bearer","credential_ref":"cred_0123456789abcdef0123456789abcdef"}
```

```json
{
  "type":"oauth2_client_credentials",
  "credential_ref":"cred_0123456789abcdef0123456789abcdef",
  "token_url":"https://identity.example.test/oauth/token",
  "scopes":["documents.read"],
  "client_auth_method":"client_secret_basic"
}
```

Other managed types are `api_key_header` with `header_name`, and `basic`. Managed profiles
cannot also name environment sources. When overriding an inherited environment profile,
the UI writes explicit `null` environment fields to clear inherited sources.
`bearer_env`, `api_key_header_env`, `basic_env`, and environment-backed OAuth profiles remain
explicit compatibility modes. A managed reference never falls back to them.

Response reflection checks protect tokens, client secrets, Basic passwords and encoded
Basic authorization, not public OAuth client IDs or ordinary Basic usernames. For
API-key-as-username Basic authentication with the conventional dummy password `X`
(including Freshservice), the username is protected as the secret and `X` is not
treated as a secret substring in ticket or attachment content.

Bindings cover connector identity, destinations, OAuth parameters and operation definitions.
A child can inherit an approved reference, but cannot redeclare it as its own, change its
destination and reuse the parent's authority, or edit the parent's stored values. It can
create its own credential. Changed consumer configuration stays unavailable until the owner
reviews **current consumers** and approves that exact preview. Approval uses both a credential
revision and a consumer-preview revision. The Connectors page can select locally owned,
already-bound references.

OAuth caches check current credential status and revision before use, including after token
acquisition. Disable/delete stops future selection; an already-dispatched request may finish.
Provider-side revocation remains a provider operation. Known managed values reflected in REST
responses are rejected before JSON/text results or artifacts are persisted, and managed
authentication errors omit provider payloads. This is not a general data-loss-prevention system
for arbitrary transformations or trusted custom drivers.

## Teams lifecycle

For a new delegated identity, configure Teams client, tenant, authority, Graph destination and
scopes, then choose **Prepare managed Teams sign-in** in Credentials. Use **Teams sign-in** on
that credential's row to start or complete sign-in, or sign out. Each row addresses its own
credential, including shared identities whose owner is not a Teams consumer. The owner's
directory configuration and other Teams accounts are unaffected. The existing Teams sign-in
panel continues to address the identity configured for the selected directory.
The Teams identity list marks a disabled, deleted or otherwise unusable managed
credential as `unavailable` and continues listing the remaining identities. Token
retrieval still fails closed for the unavailable credential.
An existing JSON file containing credentials must go through maintenance migration.
An empty `{}` placeholder created by runtime assembly, or an empty encrypted legacy
envelope with its valid key, is signed out and can prepare managed sign-in directly.
Maintenance migration also accepts empty storage without inventing an identity conflict.
Malformed sources and nonempty files missing the configured identity still require
reconciliation. The provider config
field `teams_delegated_credential_ref` selects managed custody; the legacy JSON path is then
unused. Access/refresh tokens and account metadata remain in the encrypted bundle.
Legacy input may be either the original plaintext document or an
`evander.teams-credentials.fernet.v1` transitional envelope created by an earlier privacy build.
That envelope is accepted only with its matching
`secrets/<credential-JSON-filename>.key`; missing, unsafe or incorrect keys fail closed.

Sign-in, explicit sign-out, replacement and policy changes belong to the credential's owning
directory. Approved descendants can use and refresh the identity. A refresh rereads persisted
state under the shared lock. Compare-and-swap prevents a late refresh or device-code result
from undoing logout, disable, deletion or another sign-in. Pending logins also retain their
exact credential reference, management directory and approved binding; a replacement record
requires a new login even if its revision counter matches. Revoked refresh grants clear only
the revision that failed; ordinary transport failures preserve the bundle.

After changing client/tenant/authority/Graph destination/scopes or adding an inherited consumer, review and
approve the new consumers in the owning directory. Teams approval clears the previous bundle
and requires a new sign-in. Consumers sharing a credential must agree on client, tenant,
authority, Graph destination and scopes; use separate credentials where they differ.
Destination-less bindings from an earlier implementation fail closed and require explicit
owner reapproval and a new sign-in.

## Migration

Inspect the inventory first. These commands display metadata and conflicts, never values:

```bash
sudo /opt/evander/.venv/bin/evander-cli credentials status --runtime-dir /opt/evander/runtime
sudo /opt/evander/.venv/bin/evander-cli credentials migrate \
  --runtime-dir /opt/evander/runtime --family teams
```

Dry runs do not import values, change configuration or source permissions, or initialize a
store. Applying migration requires `--apply --maintenance`. The maintenance flag acknowledges
that the operator has stopped affected consumers; it does not stop services itself. Stop the
Team runtime target and every other Teams/connector consumer, including standalone CLI jobs,
before applying. Keep them stopped until migration and metadata verification succeed.

Run application-config mutations as the service account. A transient systemd unit can supply
the protected key without giving that account access to its source file:

```bash
sudo systemd-run --wait --pipe --collect \
  -p User=evander -p WorkingDirectory=/opt/evander \
  -p LoadCredential=evander-credentials-key:/etc/evander/credentials/master.key \
  /opt/evander/.venv/bin/evander-cli credentials migrate \
  --runtime-dir /opt/evander/runtime --family teams --apply --maintenance
```

Teams migration groups directories by legacy file and identity, rejects conflicting
client/tenant bindings, imports each group with a durable migration marker, and takes a fresh
encrypted recovery snapshot before switching references through configuration services.
Shared identities have a common owner and explicit directory bindings. An interrupted apply
can be retried: it reuses imported records, checks source/configuration drift, and completes
the remaining switches. A child temporarily inheriting a migrated parent's reference is
recognized through that parent's migration marker; explicit conflicting references and
changes to imported credential policy still require reconciliation.
A legacy file is removed only after all configured identities in it
are migrated and verified. Files containing unconfigured identities or independently prepared
references are retained. A changed source after import requires operator reconciliation.
When a transitional privacy envelope is completely imported, its per-file key is also removed.
If key cleanup cannot be completed, the command reports `retained_legacy_keys`; remove those
orphaned keys through the reviewed maintenance procedure after verifying the managed snapshot.

For connectors, migrate one profile at a time. Preview uses configuration alone; apply reads
only the configured environment variables in the migration process. Supply its existing
protected service EnvironmentFile through systemd, rather than putting values in shell
arguments or history:

```bash
sudo systemd-run --wait --pipe --collect \
  -p User=evander -p WorkingDirectory=/opt/evander \
  -p EnvironmentFile=/etc/evander/local-secrets.env \
  -p LoadCredential=evander-credentials-key:/etc/evander/credentials/master.key \
  /opt/evander/.venv/bin/evander-cli credentials migrate \
  --runtime-dir /opt/evander/runtime --family connector \
  --directory root --profile-id documents --apply --maintenance
```

Omit `--apply --maintenance` for preview. Missing required environment values abort the import.
An encrypted snapshot precedes the configuration switch. Repeating a completed migration is
an evidenced no-op. Shared environment files are retained: other providers or operator
scripts can still depend on their entries. Retire an old variable only after checking all
consumers, then restart the services that formerly inherited it.

## Key rotation and recovery

Keep a protected copy of the master key separately from runtime backups. Test restoring a
matched encrypted database and key in a temporary runtime before relying on the backup.
No database can recover a lost key. Recovery snapshots contain ciphertext and metadata, and
need the key that was active when the snapshot was created.

For offline key rotation, stop all credential consumers and acknowledge maintenance:

```bash
sudo /opt/evander/.venv/bin/evander-cli credentials rotate-key \
  --runtime-dir /opt/evander/runtime --maintenance \
  --new-key-file /etc/evander/credentials/master-next.key
sudo /opt/evander/.venv/bin/evander-cli credentials status \
  --runtime-dir /opt/evander/runtime --key-file /etc/evander/credentials/master-next.key
```

The target key must not exist. Rotation creates a fresh encrypted recovery snapshot, writes
and fsyncs the new key, then reencrypts all bundles in one database transaction. The current
key source is retained. A failed transaction leaves the old database usable; a crash after
commit requires the new key. Check status with the corresponding key while consumers remain
stopped. Preserve both keys and the reported snapshot until recovery has been verified.

After successful verification, archive the previous key to a new protected file and install
the new key at the configured source path (`master.key` by default), preserving root ownership
and mode `0600`. Restart affected units so they receive the new key. Check Credentials status
and a read-only operation for each migrated family. Rotation changes encryption only; it
does not issue or revoke provider tokens.

To restore, keep consumers stopped, select a matching snapshot/key pair, replace the database
using a temporary file and atomic rename, and preserve the service account's database
ownership and mode `0600`. Restore the matching protected key source before restarting.
Preserve migration references and current configuration; never enable legacy fallback to
roll back a managed reference. Old application versions do not understand managed references.
