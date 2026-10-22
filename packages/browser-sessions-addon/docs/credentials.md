# Managed browser credentials

Browser managers can use approved connections and change session limits or profiles.
Only an owner of the selected directory, with privileged operations unlocked, can
change connection addresses or store replacement credentials. A child owner cannot
reuse an inherited credential for a different destination.

The platform credential store encrypts worker, relay and speech bearer tokens. Each
reference authorizes one complete connection: its purpose, URL including scheme,
port and base path, and, for workers, its worker identity and placement. Every HTTP,
WebSocket, speech and cleanup request checks that binding before using the token.
Changing an address without provisioning an appropriate credential fails closed.
Application URLs and leased preview endpoints remain independent of these service
connections and retain their existing session grants.

## New connections

Initialize the platform store using the existing `evander-cli credentials init`
workflow. Preserve and back up its original encryption key. Deliver the named
`evander-credentials-key` systemd credential to Admin Web, gateway, orchestrators
and the browser relay, and give their service account access to the encrypted
runtime store. The optional relay unit imports that named credential; it must be
available from the host credential store. Reinstall the updated optional relay
unit (or apply the equivalent `ImportCredential=evander-credentials-key` drop-in)
and reload systemd before restarting it. Do not put the key inside the runtime
directory or expose it to workers or browsers.

In **Shared Browsers → Configure**, save the connection address first. Then enter
the credential value and select **Store credential**. The value is not returned in
API responses or stored in directory settings. Workers and the private relay still
verify their own protected, host-configured receiver token. Provision the same
value on both ends; coordinate token rotation with those receivers. Replacing a
sender credential creates a new reference, preserving prior references needed by
existing sessions. Disable obsolete credentials only after their sessions drain.

## Existing installations

Do not remove old selectors or start upgraded consumers before migration. Stop
Admin Web, the browser relay, gateway and orchestrators that use the affected
runtime, retaining the workers needed for eventual cleanup. Run the migration
from the upgraded optional package in that same host environment.

First generate a plan; this reads configuration and metadata only:

```sh
python -m evander_browser_sessions_addon.migrate_credentials \
  --runtime-dir /path/to/runtime > /operator-owned/browser-credentials-plan.json
```

Review **every** entry's directory, purpose, complete destination, worker identity
and environment-variable name. The plan includes historical pinned connections
and cleanup callbacks, even when absent from current settings. Confirm these are
intended credentials for those destinations; never approve an unexpected selector
or destination merely because it appears in old configuration. Correct or
investigate unexpected records before continuing. Set `approved` to `true` on each
approved entry; leave the other fields unchanged. Resolve any listed conflicts.
An entry with `required: false` is unused voice configuration: if its environment
variable is absent, migration clears the old selector and leaves voice unconfigured.
An enabled voice provider always requires its approved credential.

Make the explicitly approved legacy variables available only to the trusted
migration process. Initialize or unlock the existing platform credential store,
then apply the reviewed plan:

```sh
python -m evander_browser_sessions_addon.migrate_credentials \
  --runtime-dir /path/to/runtime \
  --plan /operator-owned/browser-credentials-plan.json --apply --maintenance
```

Apply checks the original state hash, creates private SQLite recovery backups and
an encrypted credential-store backup, imports the approved values, and rewrites
settings, private snapshots and core cleanup callbacks. Its output lists counts
and backup paths, never secret values. Existing capabilities, session ownership,
placement, application grants and host-control consent remain intact. Legacy
worker-local cleanup callbacks are unchanged.

Keep consumers stopped if migration fails. Imported credentials have deterministic
references so an interrupted attempt can reuse matching imports. Generate and
review a fresh plan before retrying; state changes invalidate previous plans.
Restore both runtime databases from the reported backups when rolling back, and
retain the credential store and original encryption key. Never resume an older
consumer against a partially converted runtime.

Restart the upgraded consumers through normal deployment. Verify a hosted preview,
local Chrome sharing, voice if enabled, and cleanup before retiring backups or old
credentials. The migration does not rotate receiver tokens, access a provider,
grant an agent new tools or deploy software.
