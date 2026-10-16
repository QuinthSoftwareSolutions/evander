# SQL Server operations

Install core and this project explicitly on the control plane. Install this project on each selected container worker when worker-local resources or job bindings are required. Core does not install optional projects automatically.

```bash
uv pip install -e . -e packages/sqlserver-addon
```

Enable `sqlserver-addon` through Admin Web and restart its listed control-plane services. Enabling discovery does not grant tools, start workers, or permit allocations. Configure a directory's connections and profiles through this add-on's page. Settings writes and operator lease release require current revisions.

Use the host credential store for named connection identities. Initialize custody through the documented [core credential procedure](../../../docs/credentials.md), then use the add-on's credential form to save username/password identities. Its settings retain only references. Host environment pairs remain supported for existing deployments. Credential references bind to this add-on, connection, purpose, directory and configuration fingerprint; connection changes may require rebinding. Control-plane and worker credentials are separate host installations.

Production server deployment, storage, upgrades, scheduled backups and recovery remain externally managed. Persistent operations are explicit connection grants and have no automatic deletion or lease expiry. Allocation enablement controls only new disposable resources. Disabling allocations retains existing leases and cleanup.

Managed database creation assigns its own durable resource ID. Do not supply `resource_id` on a create request; that field selects an existing managed database only for drop. Reuse the returned ID for explicit removal in the same connection and directory.

## Worker deployment

Use the project-owned systemd examples. Install `worker.json` and `worker.env` under `/etc/evander-sqlserver/`, with root-owned private permissions. Create `/var/lib/evander-sqlserver-addon` mode 0700. The API binds loopback by default on 11992; a remote control plane needs an explicitly selected private bind address and firewall access restricted to the control plane. Do not add this API port to guest defaults. Worker authentication uses a separate host-held token.

Install the worker service and its independent reap service/timer together. Both use `PrivateTmp=yes` with `ProtectSystem=strict`; otherwise SQLite temporary-file operations can fail. The worker and reaper share a durable lock and journal. Keep the worker job database accessible at the configured path. SQL Server must already be installed and reachable; this package does not install or upgrade it.

Use the exact instance settings appropriate to the executing host. SQL `application_host` can differ from its worker-local administrative `host`. Application endpoints must be routable through approved worker networking. Production TLS certificates must be trusted on both the administration host and application runner, and application hostnames must match those certificates. Registered connection settings never come from an agent-supplied URL. New worker allocations also start disabled.

Deploy the matching core worker daemon and worker egress-policy role before the pilot. New jobs automatically mount `/run/evander-resources` as a private tmpfs, including jobs with writable root filesystems; credentials and interrupted staging files disappear on container stop. Already running jobs without this mount cannot receive credentials and must finish normally before a new job can bind. Legacy stopped bindings on persistent storage retain cleanup intent until file or container removal is verified.

The worker egress policy routes exact job grants through `EVANDER-RESOURCE-EGRESS` after its blocked ranges and before its default deny. Policy reapplication preserves this chain. Authorized safety ticks repair missing grants without reprovisioning databases. A custom worker firewall must provide the same scoped hook; do not replace it with a broad SQL port allowlist.

Active authorized jobs renew leases. Private files and exact source/destination network grants are withdrawn at job exit. Idle leases expire after two hours by default. Ownership or grant loss initiates cleanup; failed cleanup remains visible and retries even while allocation is disabled. Cleanup verifies provider ownership and removal. Ambiguous or foreign objects remain for explicit operator reconciliation.

Activate the add-on's **Resource lifecycle** workflow starter once in each configured directory through the normal preview/revision-checked workflow gallery. This graph is required for continuous authority refresh during long worker jobs. It uses the ordinary providers: `trigger:sqlserver:pending` -> `effect:sqlserver:operate` with operation `reconcile` and arguments bound to the trigger's lease ID. The maintenance operation needs no production or data grants; it only advances existing accepted intents in its own directory. Install and activate the graph through the normal revision-checked workflow editor. No graph is enabled automatically. The independent worker timer still expires resources and recovers faults during a control-plane outage.

Workflow queries use `query:sqlserver:read`; mutations use `effect:sqlserver:operate`. Configure operation, connection ID and arguments, and explicitly grant selected operations in `workflow_operations`. Use lifecycle `acquire/reset/release` in the same trusted workflow run. For lease data access pass `lease_id` inside arguments. A plain workflow without an assigned worker session can use direct registered instances; worker-private binding requires an actual assigned session job.

Uncertain SQL writes are journaled before dispatch and are never replayed automatically. Repeating the same request ID returns its recorded disposition. Inspect actual provider state and create an explicit new request only after deciding the outcome. Lifecycle creation and cleanup reconcile their owned resources idempotently. Reset and broker control require ready resources and a live job for worker resources.

SQL results are limited by rows, bytes and query timeout. Before retrieving a row, the add-on checks the driver's declared column types and sizes against the byte limit. Unbounded types such as `varchar(max)`, `nvarchar(max)`, `varbinary(max)` and XML, and rows whose declared sizes exceed the limit, are rejected before their values are fetched. Use explicit server-side casts such as `CAST(payload AS nvarchar(128))` to select a bounded preview. This may reject a wide declared column even when its current value is short; raising `max_bytes` within the supported limit or selecting narrower columns is an explicit operator/caller choice.

## Migration and rollout

Upgrade Test Fixtures to its drain-capable release. Generate a read-only preview with its `evander-test-fixtures-migrate preview` command, supplying the current control-plane settings and every worker configuration. Preserve actual endpoints, scope and image digests; confirm the suggested new API origins. The preview enables neither allocation nor production operations. Select lifecycle and discovery tool grants separately from data and management grants.

Enable `drain_only` on the old control plane and workers through supported configuration paths. Existing bundles can still rebind, renew, reset and release. Do not move their records into these new journals. Pilot newly configured profiles with disposable resources, then observe ordinary task acquisition, use and cleanup.

Run the legacy retirement journal check and independently verify provider absence on every old worker. Keep its services, secrets, grants and cleanup workflow available while any resource, binding or cleanup failure remains. Only then remove its active configuration, tools and services and uninstall the legacy package. Preserve required audit/backups in operator-owned storage. Jonathan owns deployment and activation; repository examples contain no customer configuration.

## Validation

Unit tests run against synthetic adapters without service provisioning. Native tests are opt-in and create only randomly named local resources from explicitly selected preseeded image digests. They never publish ports or pull images.

```bash
PYTHONPATH=src:packages/sqlserver-addon timeout 180 .venv/bin/pytest -q packages/sqlserver-addon/tests
EVANDER_SQLSERVER_SMOKE_IMAGE=mcr.microsoft.com/mssql/server@sha256:<approved-digest> \
  PYTHONPATH=src:packages/sqlserver-addon timeout -k 20s 240s .venv/bin/pytest -q packages/sqlserver-addon/tests/test_sqlserver_live.py
```

Native validation additionally requires the project runtime dependency `pyodbc`, ODBC Driver 18 and Docker.
