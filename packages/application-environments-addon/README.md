# Application Environments

An optional application runtime independent of Browser Sessions and agent turns.
Applications run in approved images on the owning session's assigned container
worker. Recipes define commands, declared HTTP ports and readiness checks.
Creating an environment never creates a browser.

Install this project explicitly with `uv pip install -e ./packages/application-environments-addon`.
Core does not install it. Register `evander_application_environments_addon` through
the normal add-on loader on the control plane and orchestrators. See
[operations and coordinated migration](docs/operations.md).

## Agent use

1. Grant the desired `application_environment_*` tools explicitly.
2. Discover approved profiles and opaque workspace references.
3. Prepare a repository-relative recipe, for example `.evander/preview.json` from
   the [synthetic example](examples/preview/README.md).
4. Call `application_environment_create` with `request_id`, `profile_id`,
   `workspace_ref` and `launch_manifest_path`. Reuse the request ID only for the
   identical request. Startup uncertainty reserves the existing environment.
5. Poll `application_environment_status` through a bounded readiness loop. Only
   `state=ready` receipts contain usable endpoint references.
6. Optionally pass those references and an endpoint entry URL to
   `browser_session_create` in `mode=hosted`, using a separate browser profile.
7. Close the browser and application explicitly when finished. Closing only the
   browser leaves the application running until its own deadline or explicit close.

Workflow nodes are `effect:application-environment:ensure`,
`query:application-environment:status` and `effect:application-environment:close`.
The [composition example](examples/workflow-nodes.json) contains node fragments;
wire readiness, error and finalization branches using the workflow's normal
control flow. The viewer URL is the browser receipt's `viewer_url`.

## Resources and bounds

The default hard lifetime is four hours, capped by the owning session's expiry.
There is no application inactivity extension from viewer frames or status reads.
Defaults allow one application per worker and owning session. Startup never pulls
images or grants general Internet access. Workspaces retain inode-checked mount
pins and durable cleanup dependencies until release is confirmed.

Applications without database recipes do not need the SQL Server add-on. For a
recipe's `database` alias, the application profile selects an operator-approved
`database_profile`. The worker's `resource_profiles` entry delegates provisioning
to the SQL Server worker API; it contains that worker's origin/token reference and
resource profile, not SQL administrator credentials. Its `allowed_owner_providers`
must include `application-environments-addon`, and the database alias must exist
in the resource profile's options. The application image needs Python 3 and
`/usr/bin/timeout`. A trusted entrypoint waits for its private resource binding
before running the recipe. Connection strings are projected into tmpfs rather
than Docker arguments or ordinary receipts. Existing recipes receive
`EVANDER_PREVIEW_DATABASE` in the launched process environment.

SQL resources bind to the registered application container, independent of an
active agent job. Both workers must use the same worker runtime registry and
matching core version. Use worker-reachable IP addresses for SQL `application_host`
when the application image has no DNS. Resource expiry is capped by environment
ownership; release failures retain durable intent and checkout guards.

Tests use synthetic identities and adapters. Run the package tests with the
repository Python environment and `PYTHONPATH=src:packages/application-environments-addon`.
Real kernel filtering and SQL require the disposable-worker qualification in the
operations guide. Operator-specific recipes, credentials and evidence belong
outside this repository.
