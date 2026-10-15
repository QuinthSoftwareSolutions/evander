# Test Fixtures add-on

Optional on-demand test infrastructure for authorized Team tasks on container
workers. One installation supplies subsequent tasks automatically. New allocation
starts disabled on both the control plane and worker.

Install this project separately on the control plane and worker; the core package
does not depend on it. Enable the `test-fixtures-addon` loader ID and
`evander_test_fixtures_addon` module alongside `team-addon` in the gateway and
orchestrator configurations. See [deployment and operations](docs/operations.md).

| Profile | Resources |
| --- | --- |
| `sqlserver` | Two empty databases, aliases `source` and `target`, a distinct lease login |
| `rabbitmq` | Dedicated internal broker, vhost and credentials |
| `sqlserver-rabbitmq` | Both |

MCP tools: `test_fixtures_acquire(task_id, profile, request_id)`,
`test_fixtures_get(lease_id)`, `test_fixtures_reset(lease_id)`,
`test_fixtures_control(lease_id, action, duration_seconds=60)` and
`test_fixtures_release(lease_id)`. Acquisition reserves a durable lease and starts
provisioning immediately. It returns `ready` or a durable pending/failure state;
the reconciliation workflow finishes accepted work and retries failures. Private
configuration and network access require a running job whose session and dispatch
match the task authority. If the requesting job exits during broker startup,
resources finish provisioning without renewal and a task-bound continuation asks
the owner to reacquire the same bundle in its next job (`awaiting_job`). Agents can
poll readiness without extending the lease. A ready receipt provides nonsecret
endpoints and `/tmp/evander-test-fixtures/<lease>/connection.json` in the current job.

Reset is queued and reports `resetting`; wait for `ready` and no pending
`operation` before seeding again. If its requesting job ends, an `awaiting_job`
continuation wakes the owning task. Reacquire the same task/profile in the new
job to finish the reset after permissions are checked again. Fresh resets and
provisioning handoffs can send new continuations; ordinary rebinds and status
polling do not repeat readiness notices.
Broker control attempts an immediate bounded
worker acknowledgement. Check that no `operation` is pending, that
`last_control.action` matches the request, `last_control.status == "applied"`, and the active
`fault_until` deadline before asserting application behavior during a fault.
Pending, failed or unknown outcomes do not prove the requested fault occurred.

The private JSON contains SQL records under `source`/`target` (including
`connection_string`) and RabbitMQ `host`, `port`, `username`, `password`, `vhost`.
The job's unprivileged user creates the mode0600 file. A root helper prepares
and seals its root-owned directory before readiness, without adding container
capabilities or receiving the credentials.
Application tests seed their own schema and synthetic data. Never copy this file
into artifacts, logs, Team records or Git. Fixture readiness is independent of
application tests, plan approval and release authorization.

Each task may hold one bundle; each worker defaults to two. A broker has 1 GiB
memory, one CPU, 256 pids, bounded logs and a lease-owned data volume. Only its currently authorized
job joins its dedicated internal network. No broker ports are published. SQL
uses the worker's existing native SQL Server and grants only the lease databases.
The long-lived agent session's SQL principal is never reused.

Active authorized jobs renew leases during reconciliation. Otherwise resources
remain for two idle hours; status reads do not renew them. Ownership loss,
cancellation, release or removal from the allowed teams initiates cleanup.
Job-private files and RabbitMQ routes are withdrawn when the job exits. `stop`
and `pause` require a duration of 1–120 seconds; the worker watchdog resumes the
broker at its durable deadline even when the control plane is unavailable.
Broker data survives a stop/start fault and is discarded at reset or release.

Operator actions and lifecycle intents are durable. The Admin Web Test fixtures
view shows ownership, readiness, resources, expiry and failures, with revision
guarded release/retry cleanup. Allocation can be disabled while cleanup continues.
After changing Directory in the operator view, refresh before saving settings or
changing leases. Responses from previous directory loads are discarded.

Run focused tests from the repository root:

```bash
PYTHONPATH=src:packages/test-fixtures-addon timeout 180 .venv/bin/pytest -q packages/test-fixtures-addon/tests tests/test_task_resource_scope.py
```

The optional [real-service smoke](tests/test_live_services.py) runs inside an
explicitly provisioned disposable worker job. Unit tests use synthetic adapters
and do not provision remote resources.
Operator UI behavior regressions use Node.js 18 or later when available, with
its built-in test runner and no npm dependencies.
