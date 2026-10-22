# Installation, cutover and qualification

## Independent deployment

Install the core and this optional package on the control plane and assigned
workers. Install SQL Server separately only where database recipes are enabled.
Use the package's `deploy/worker.yml` or `deploy/install.sh`; the core installer
does not install optional units. The listener defaults to private port 11893 and
uses `/etc/evander/application-worker.env` and `application-worker.json`. Keep
service tokens root-readable and distinct from browser relay/worker tokens.
The supplied unit binds to `127.0.0.1`. For a remote control plane, use a private
TLS reverse proxy or a systemd override binding the listener to the worker's
private address, with caller restrictions. Configure the control-plane worker
origin to match that listener; `callback_url` must remain reachable by trusted
cleanup processes on the worker.

Seed approved images and toolchain caches before allocations. Use numeric non-root
container identities. The supervisor needs Docker, iptables, bridge netfilter,
util-linux mount tools and host mount-namespace access. No guest receives Docker
access. `examples/worker.json` and `examples/control-plane.json` show configuration
without secrets; configure identical application profiles in both locations.

Application and browser workers share the actual assigned worker's
`worker_root/runtime` endpoint/workload registry. SQL worker `worker_jobs_db` must
point into that same runtime directory. Core's resource binding code recognizes
only trusted registered container IDs and verifies their Docker ownership labels.
Do not expose the registry to agent-owned mounts. For database profiles, configure
the SQL worker origin, token reference and approved resource profile on the
application worker before enabling allocations.

Enable through the normal loader and restart the gateway, Admin Web and both
orchestrators. Standard Ansible deployments refresh already-provisioned optional
application packages and running services; absent or stopped units stay untouched.
Admin application management requires sign-in and directory-scoped
`application_environments.view` / `application_environments.manage` permissions.
Tool grants remain explicit.

## Coordinated migration

There is no compatibility creation facade. Historical browser records remain
readable, including closed `hosted_demo` and `hosted_development` sessions.

1. Pause legacy hosted creation at its callers. Keep existing cleanup services and
   credentials available; do not disable the whole Browser Sessions integration.
2. Close legacy hosted previews and verify both control-plane and worker leases,
   networks, mounts and checkout dependency records are released. Resolve every
   `cleanup_failed` record first. Existing local Chrome sessions need no migration.
3. Preserve configuration revisions and back up the control-plane and worker state
   databases in operator storage. Take fresh worker snapshots while creation is
   paused. Run `evander-application-environment-migrate --runtime-dir <runtime>`
   with `--worker-state worker-id=<worker-runtime-snapshot>` for every configured worker.
   Add `--sql-profile application-profile=sql-profile` for database-enabled profiles.
   Exit 2 means blocked; the JSON output explains why. The checker reads state only.
4. Review the generated configuration proposals. Configure new application worker
   origins/tokens and SQL resource mappings explicitly. Browser profiles now hold
   only browser image, memory/CPU, cache mounts and optional Playwright enablement.
5. Deploy matching core/SQL workers first, followed by the optional application and
   browser packages and control-plane services. Keep allocations disabled.
6. Save split settings with expected revisions through Admin Web/APIs. Update
   launch-and-share callers to create an application, await readiness and create
   its browser. Replace `browser_session_workspaces` grants with the chosen
   application tools; grant Playwright and managed-login tools only when desired.
7. Qualify on a disposable worker, then enable allocations through normal operator
   deployment. Verify loaded module paths rather than assuming an editable checkout
   or installed wheel won loader precedence.

Before enabling new allocations, rollback means restoring backed-up configuration
and matching packages. After new environments exist, drain and verify their cleanup
before rollback; never point old workers at new active state or delete the registries.

## Disposable-worker qualification

Use dedicated synthetic repositories and a dedicated SQL container/server. Do not
run privileged filtering checks against a worker hosting unrelated sessions.

- Install both optional packages separately: browser-only external target browsing
  must work without this package; an application must start and report readiness
  without Browser Sessions or SQL installed.
- Compose the two using returned endpoint references. Verify shared login, two
  tabs, typed tools, Playwright, viewer streaming, chat and control handover.
- End the authoring agent's job; confirm the application/browser remain available.
  Start a subsequent authorized turn and reconnect using a fresh observation.
- Verify approved HTTP targets work through the proxy while direct egress,
  undeclared ports, other environments, bridge host services and stale target
  references fail. Confirm source changes still reach the mounted application.
- Create a database recipe through the SQL add-on. Verify scoped connection files,
  separate databases between owners, application readiness, and no administrator
  credentials in Docker environment/arguments or ordinary receipts.
- Restart each service; interrupt startup; stop the application; expire its lease
  while the control plane is unavailable. Confirm no migration/launch replay and
  durable resource release/revocation.
- Inject release and unmount failures. Confirm `cleanup_failed`, retained dependency
  records and preserved checkouts. Recover the dependency and verify cleanup retry.
- Finish by checking actual containers, networks, mounts, SQL resources and registry
  records. An accepted cleanup request is not proof of release.

Browser close releases only browser resources. Application close withdraws endpoint
bindings before application/network removal; dependent browsers remain alive with
that target unavailable. Failed withdrawal blocks deletion until it can be verified.
