# Changelog

## 0.1.3 - 2026-10-16

- Give the fixture worker and independent reaper private writable temporary directories while retaining strict filesystem protection. SQLite can then sort larger worker-job histories without `SQLITE_IOERR_GETTEMPPATH` preventing fixture reconciliation.

## 0.1.2 - 2026-10-15

- Select the fixture vhost with `--vhost` when applying RabbitMQ limits so broker readiness succeeds.
- Exercise native broker provisioning, queue limits, AMQP round trips, pause/stop recovery, reset and cleanup in an opt-in local Docker regression.

## 0.1.1 - 2026-10-15

- Recognize Docker's absent-network inspection response during first allocation and cleanup retries. Daemon outages and unrelated errors still fail explicitly.

## 0.1.0 - 2026-10-15

- Task-scoped disposable SQL Server and RabbitMQ leases, private configuration,
  MCP controls, worker watchdog, workflow reconciliation and operator view.
- Create private configuration as the job user without `CAP_CHOWN`; seal its
  root-owned directory on completion/failure and clean up interrupted writes.
- Wake the owning task for a queued reset after its job exits and send fresh
  continuations for subsequent resets and provisioning handoffs without waking
  tasks again for ordinary rebinds.
- Recheck current allocation policy and quota under the settings/admission lock.
- Keep operator settings and lease actions bound to the loaded directory; reject
  stale scopes and discard responses from superseded refreshes.
