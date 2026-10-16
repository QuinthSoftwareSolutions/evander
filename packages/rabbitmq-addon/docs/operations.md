# RabbitMQ operations

Install core and this project explicitly on the control plane. Install this project on each selected container worker when worker-local resources or job bindings are required. Core does not install optional projects automatically.

```bash
uv pip install -e . -e packages/rabbitmq-addon
```

Enable `rabbitmq-addon` through Admin Web and restart its listed control-plane services. Enabling discovery does not grant tools, start workers, or permit allocations. Configure a directory's connections and profiles through this add-on's page. Settings writes and operator lease release require current revisions.

Use the host credential store for named connection identities. Initialize custody through the documented [core credential procedure](../../../docs/credentials.md), then use the add-on's credential form to save username/password identities. Its settings retain only references. Host environment pairs remain supported for existing deployments. Credential references bind to this add-on, connection, purpose, directory and configuration fingerprint; connection changes may require rebinding. Control-plane and worker credentials are separate host installations.

Production server deployment, storage, upgrades, scheduled backups and recovery remain externally managed. Persistent operations are explicit connection grants and have no automatic deletion or lease expiry. Allocation enablement controls only new disposable resources. Disabling allocations retains existing leases and cleanup.

## Worker deployment

Use the project-owned systemd examples. Install `worker.json` and `worker.env` under `/etc/evander-rabbitmq/`, with root-owned private permissions. Create `/var/lib/evander-rabbitmq-addon` mode 0700. The API binds loopback by default on 11993; a remote control plane needs an explicitly selected private bind address and firewall access restricted to the control plane. Do not add this API port to guest defaults. Worker authentication uses a separate host-held token.

Install the worker service and its independent reap service/timer together. Both use `PrivateTmp=yes` with `ProtectSystem=strict`; otherwise SQLite temporary-file operations can fail. The worker and reaper share a durable lock and journal. Keep the worker job database accessible at the configured path. Approved RabbitMQ images must already exist locally. No allocation pulls images.

Use the exact instance settings appropriate to the executing host. Application endpoints must be routable through approved worker networking. Production TLS certificates must be trusted on both the administration host and application runner, and application hostnames must match those certificates. Registered connection settings never come from an agent-supplied URL. New worker allocations also start disabled.

Active authorized jobs renew leases. Private files and exact source/destination network grants are withdrawn at job exit. Idle leases expire after two hours by default. Ownership or grant loss initiates cleanup; failed cleanup remains visible and retries even while allocation is disabled. Cleanup verifies provider ownership and removal. Ambiguous or foreign objects remain for explicit operator reconciliation.

Activate the add-on's **Resource lifecycle** workflow starter once in each configured directory through the normal preview/revision-checked workflow gallery. This graph is required for continuous authority refresh during long worker jobs. It uses the ordinary providers: `trigger:rabbitmq:pending` -> `effect:rabbitmq:operate` with operation `reconcile` and arguments bound to the trigger's lease ID. The maintenance operation needs no production or data grants; it only advances existing accepted intents in its own directory. Install and activate the graph through the normal revision-checked workflow editor. No graph is enabled automatically. The independent worker timer still expires resources and recovers faults during a control-plane outage.

Workflow queries use `query:rabbitmq:read`; mutations use `effect:rabbitmq:operate`. Configure operation, connection ID and arguments, and explicitly grant selected operations in `workflow_operations`. Use lifecycle `acquire/reset/release` in the same trusted workflow run. For lease data access pass `lease_id` inside arguments. A plain workflow without an assigned worker session can use direct registered instances; worker-private binding requires an actual assigned session job.

Uncertain message publishing and retrieval operations are journaled before dispatch and are never replayed automatically. Repeating the same request ID returns its recorded disposition. Inspect actual provider state and create an explicit new request only after deciding the outcome. Lifecycle creation and cleanup reconcile their owned resources idempotently. Reset and broker control require ready resources and a live job for worker resources.

## Migration and rollout

Upgrade Test Fixtures to its drain-capable release. Generate a read-only preview with its `evander-test-fixtures-migrate preview` command, supplying the current control-plane settings and every worker configuration. Preserve actual endpoints, scope and image digests; confirm the suggested new API origins. The preview enables neither allocation nor production operations. Select lifecycle and discovery tool grants separately from data and management grants.

Enable `drain_only` on the old control plane and workers through supported configuration paths. Existing bundles can still rebind, renew, reset and release. Do not move their records into these new journals. Pilot newly configured profiles with disposable resources, then observe ordinary task acquisition, use and cleanup.

Run the legacy retirement journal check and independently verify provider absence on every old worker. Keep its services, secrets, grants and cleanup workflow available while any resource, binding or cleanup failure remains. Only then remove its active configuration, tools and services and uninstall the legacy package. Preserve required audit/backups in operator-owned storage. Jonathan owns deployment and activation; repository examples contain no customer configuration.

## Validation

Unit tests run against synthetic adapters without service provisioning. Native tests are opt-in and create only randomly named local resources from explicitly selected preseeded image digests. They never publish ports or pull images.

```bash
PYTHONPATH=src:packages/rabbitmq-addon timeout 180 .venv/bin/pytest -q packages/rabbitmq-addon/tests
EVANDER_RABBITMQ_SMOKE_IMAGE=rabbitmq@sha256:<approved-digest> \
  PYTHONPATH=src:packages/rabbitmq-addon timeout -k 20s 240s .venv/bin/pytest -q packages/rabbitmq-addon/tests/test_rabbitmq_live.py
```

Native validation additionally requires `pika` and Docker. `pika` is a development-only client; the add-on itself uses the Management HTTP API.

Set `EVANDER_RESOURCE_SMOKE_RUNNER_IMAGE` to a preseeded runner to include the native job binding test. It uses real containers, network attachments and private files with a synthetic host firewall executor. Verify actual privileged firewall application and removal in the deployment pilot before activation.
