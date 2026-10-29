# Persistent independent resource cleanup

RabbitMQ, SQL Server and legacy Test Fixtures each run a separate `*-reap.service`
with `--reap-loop`. Each process constructs its service once, runs a cleanup pass
immediately, and waits five seconds after the pass finishes. Passes never overlap
or accumulate catch-up work. The API worker continues its existing two-second
checks; independent cleanup remains available when that API is down.

Provider failures retain the existing durable intent and retry behavior. An
unexpected pass failure is logged without provider exception text and retried
after five seconds. SIGTERM/SIGINT interrupts the wait; an in-flight operation
retains its existing timeout and journal guarantees. Systemd allows 30 seconds
for shutdown, then terminates remaining processes; unfinished work is recovered
from the journal. Process failures restart after five seconds.

The five-second wait is a scheduling interval, not a guarantee that an external
operation completes in five seconds. Existing provider deadlines, lock contention
and host availability still apply. No lease schema or authorization changes are
required. `--reap` remains a single-pass operator command; it cannot be combined
with `--reap-loop`.

## Reviewed deployment and rollback

The normal `ansible/worker.yml` deployment now performs this migration automatically
for already provisioned, unmasked cleanup services. It refreshes only those add-on
packages from the same reviewed checkout as the core, verifies installed versions
against that checkout, migrates the units, and restarts their running API workers.
It does not provision absent add-ons or unmask services. Deployment fails if a
selected reaper is not persistent, enabled and running without restart failures,
or if an old timer remains enabled or active. No separate operator pipeline step
is needed when the pipeline already invokes the worker playbook.

The playbook invokes `--packages-installed`, which verifies package versions and
command compatibility before touching service schedules. This mode and `--wheel`
are mutually exclusive. Keep package installation and migration within the same
reviewed worker deployment.

### Separate wheel-based deployment

Use the established CI/CD pipeline after human review. Do not install wheels,
replace units or restart production services through an ad hoc SSH session.
Build core and selected add-on wheels from the same reviewed revision and stage
them with the repository source on the worker using the pipeline's artifact
transport. Dependencies must already be installed; the migration uses local
wheels without dependency downloads.

In the privileged worker deployment step, run from the reviewed repository root:

```bash
python3 scripts/migrate_resource_cleanup.py \
  --addon rabbitmq \
  --wheel "$CORE_WHEEL" \
  --wheel "$RABBITMQ_WHEEL"
```

Repeat `--addon` and `--wheel` for other installed selected add-ons. Allowed add-ons
are `rabbitmq`, `sqlserver` and `test-fixtures`. The script discovers their existing
reaper services and skips uninstalled selections. Supply exactly the core wheel
and the selected installed add-on wheels; extra distributions are rejected.

The migration validates unit inputs, installs the wheels, verifies command
compatibility, disables/stops legacy timers, stops existing reapers, installs
persistent units, removes old timer files, reloads systemd, then enables/starts
the reapers. Repeating it converges on the same state. Existing API-worker checks
remain active during the transition. Host-owned configuration, credentials,
drop-ins, grants and journals are preserved.

Configuration and environment changes require a managed restart of the affected
API and cleanup services. Persistent cleanup no longer rereads startup settings
through process launches. Keep legacy Test Fixtures services until its existing
leases and cleanup failures have drained.

Rollback is also reviewed and pipeline-driven. Stage the previous approved wheels
and a previous source snapshot containing its service and timer definitions, then
run the same migration script from this release:

```bash
python3 scripts/migrate_resource_cleanup.py --mode timer \
  --unit-source "$PREVIOUS_SOURCE" \
  --addon rabbitmq \
  --wheel "$PREVIOUS_CORE_WHEEL" \
  --wheel "$PREVIOUS_RABBITMQ_WHEEL"
```

This restores the previous packages and timer units, disables persistent-service
startup, and enables the original timers. Retain both source snapshots and wheel
sets until rollout is accepted. A failed compatibility check leaves the old
cleanup schedule running; a later deployment failure requires pipeline retry or
the reviewed rollback. Never remove cleanup merely to reduce CPU usage.

## Acceptance checks

Before production rollout, use synthetic leases in staging and verify expiry,
access withdrawal, fault recovery, failed-cleanup retry and contention while the
API worker is unavailable. Confirm one persistent PID per installed reaper, no
enabled reaper timers, and successful recovery after process termination.

Measure service-cgroup CPU deltas for 60 seconds, including child processes. With
no active leases, combined cleanup CPU must stay below 5% of one core. Under the
same representative active workload, require at least 80% less cleanup CPU than
the previous release and no lost recovery behavior. Record pass duration and
lease completion separately from scheduling delay. Repeat read-only checks after
the approved production pipeline completes; conversation/model startup latency
is a separate measurement.
