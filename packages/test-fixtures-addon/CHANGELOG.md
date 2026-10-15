# Changelog

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
