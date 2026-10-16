# Draining Test Fixtures

The SQL Server and RabbitMQ add-ons replace new bundle provisioning. Keep this package and its worker/reaper installed while existing leases or cleanup failures remain.

1. Upgrade the legacy control-plane and workers to the drain-capable package. Take consistent private state backups through normal operator procedures.
2. Export current settings through the supported Admin API. Collect each worker's configuration privately. Run a read-only preview:

   ```bash
   evander-test-fixtures-migrate preview --settings /operator/legacy-settings.json \
     --worker worker-a=/operator/worker-a.json
   ```

   Confirm new worker API origins. Actual worker assignments, SQL endpoints, environment references, team scope and image digests are preserved. Output is a candidate, not a live configuration update. No data or management permissions are translated.
3. Install and configure the new add-ons disabled, using their project-owned instructions. Configure explicit lifecycle/discovery grants and adapt consumers to separate resource receipts. Save settings through revision-checked Admin APIs. Enable only after disposable pilot verification.
4. Set `drain_only: true` on legacy directory and worker settings. Existing leases still rebind, renew, reset and release; creation of a new lease is rejected. Do not copy active lease rows into a new implementation.
5. Keep reconciliation, worker/reaper services, credentials and legacy tool grants available until cleanup completes. Check all journals read-only:

   ```bash
   evander-test-fixtures-migrate retirement-check --control-plane /operator/control-plane-journal \
     --worker worker-a=/operator/worker-a-journal
   ```

   Every configured or historically referenced worker must be supplied. A drained journal is insufficient proof of actual infrastructure absence. The command never authorizes retirement.
6. Independently verify the recorded databases/logins, broker containers, networks, volumes, job-private files and scoped firewall rules are absent on every worker. Only then remove legacy configuration, workflow nodes, tool grants and services and uninstall the package. Preserve required audit and backup evidence in operator-owned storage.

The migration command never saves configuration, provisions services, transfers leases or performs retirement. Jonathan owns deployment and activation.
