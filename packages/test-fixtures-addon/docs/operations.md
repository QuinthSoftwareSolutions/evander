# Deployment and operations

Deploy core 0.93.0 or later and this package 0.1.0 at one exact Git revision.
Install `packages/test-fixtures-addon` into the control-plane and worker virtual
environments explicitly (`uv pip install --python <venv>/bin/python ./packages/test-fixtures-addon`).
Retain existing worker deployment and SQL setup. This add-on does not install
SQL Server, change production databases or install itself with core. SQL profiles
require the runner's existing approved route to its worker SQL endpoint. Runner
images must include `/usr/bin/python3` and `/usr/bin/id`; configuration projection
uses an isolated interpreter and never imports code from the agent repository.

1. On the worker, install Docker, IPv4 iptables and the existing native SQL Server
   provisioning prerequisites, including `sqlcmd`. Pre-seed an operator-approved
   RabbitMQ 4.x image with UID/GID 999, built-in definitions import and diagnostics.
   Pin the actual repository digest in `worker.json`; tags and automatic image
   pulls are rejected. Definition imports use RabbitMQ's
   [built-in local import](https://www.rabbitmq.com/docs/definitions#import-on-boot).
2. Copy `examples/worker.json` and a populated root-owned mode-0600 environment file
   to `/etc/evander-test-fixtures/`. Set the actual worker ID and job database path.
   SQL credentials remain on the worker. Keep `allocations_enabled` false.
   Create `/var/lib/evander-test-fixtures` root-owned mode0700. Its SQLite registry,
   SQL intent file and broker definition files must share its durable filesystem.
   Back it up with the worker resource registry; never replace it while leases exist.
3. Install the three example systemd units explicitly. Adapt the venv paths and
   bind address to the deployment. The API defaults to loopback; bind only the
   worker's control-plane-facing private interface when remote access is needed.
   Allow TCP11991 only from the control plane. Do not add it to guest egress ports.
   Give the control plane a distinct fixture bearer token via its environment.
   Start the service and independent reap timer. Both use the same process lock.
   Docker retains privileged host operations; agents receive neither a Docker
   socket nor a general host command surface.
4. Enable `test-fixtures-addon` and `evander_test_fixtures_addon` explicitly in
   both live add-on loader configurations. The Team add-on must also be enabled.
   Restart the gateway/trigger/effect workers through the supported deployment.
   The core installer deliberately does not install the fixture service.
5. In Admin Web Test fixtures, save the control-plane example with actual worker
   origins and token environment variable names. Worker origins must match the
   actual persisted session assignment. Settings updates require the current
   `expected_revision`, directory owner permission and unlocked privileged
   operations. Start with no allowed teams and allocation disabled.
6. Import the reconciliation workflow through Admin Web/API using the live pack
   revision and lock. Keep new allocation disabled. Enable the reconciliation
   graph in each participating directory so pending operations and cleanup run
   every 30 seconds. Never write live pack files directly. Keep this graph enabled
   when pausing allocations. The worker timer still expires resources during a
   complete control-plane outage.

After the deployment owner confirms deployment, verify the exact runtime revision,
authenticated `/healthz` protocol/version/worker ID/capabilities and pre-seeded
digest on every participating worker. Verify the five tool names through actual
MCP discovery for the pilot session, including inherited policy grants. Grant the
five fixture tools through the selected Team role's existing tool policy once;
subsequent tasks use the same guarded capability. Set the
worker allocation switch true only after these checks. Save the control-plane
policy with the authorized team's exact ID and enable new allocation using its
current settings revision. Pilot identifiers and evidence belong in operator-owned
storage outside this repository.

The pilot acquires a combined profile for an existing authorized task, waits for
ready, runs the live-service smoke in its current job, tests bounded pause/resume,
and releases the lease. The Python smoke needs the add-on's `dev` extras and
ODBC Driver 18 inside that disposable job; seed these through the approved runner
image/package cache, or use the application's existing SQL and AMQP clients.
Verify SQL logins/databases, broker, network, routes and
private configuration are gone. Also verify two-hour expiry with a shortened
operator-configured test idle limit (minimum 300 seconds), cleanup retries after a
simulated failure, and cancellation/reassignment. Return policy to allocation
disabled if acceptance fails; leave cleanup active. No code deployment, service
activation or pilot operation is performed by installing/importing this package.

## Recovery

`cleanup_failed` retains quota and resource identity. The worker timer retries;
the operator view can queue release/retry with the latest lease revision.
Failures are deliberately redacted; use root-only service diagnostics and resource
inspection for details. A lost HTTP response is reconciled using the same lease
and operation IDs. Reset empties only the lease's two databases and broker; it
does not restore customer data or touch application repositories.

Normal get/list operations never expose passwords or renew expiry. Credentials
are kept in worker-owned mode0600 stores and projected through stdin into a
mode0600 file owned by the job's actual numeric UID. The parent directory is
root-owned and checked against symlink replacement; the file lives in private
container tmpfs, not the persistent agent home. A replacement dispatch must
revalidate scope and receive a fresh binding. Task reassignment releases the old
lease; a new owner acquires new SQL and broker identities.

The worker watchdog checks deadlines every 2 seconds while its API is running,
with an independent systemd timer every 5 seconds after a completed reap. Recovery
can be delayed by host/daemon unavailability or an in-flight bounded operation;
the deadline is retained and retried. Idle resources are never renewed locally.
Uninstall only after all leases are released; disabling the allocation switch is
the supported reversible pause.
