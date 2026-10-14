# Optional integration contracts

An integration is an opt-in contribution from an existing Evander add-on. It
can describe configuration, host credentials, readiness checks, background
workers and useful starting workflows without making a vendor or business
domain part of core. Existing tools, connector drivers, workflow providers,
routes and arbitrary custom workflows continue to use their current contracts.

The modular-workflows release adds contracts, host-side accessors and read-only
operator APIs. Teams/Graph is now separately installed, and [Workflow starters](workflow-starters.md)
provides explicit preview, activation, and first-run verification. Registration itself
never changes enabled add-ons, installs packages, starts workers, or saves settings.

## Registration

Import the types from `evander.integration_contracts`, then supply the optional
`integration` field on `AddonRegistration`. It also accepts an equivalent
mapping, as do the contribution collections. Old registrations, including
positional registrations, need no changes.

An add-on can declare `required_restart_services` on its registration to include
its own `evander-...` workers in enable/disable restart guidance. Use unit names
without the `.service` suffix. The host combines these with its standard restart
list; each worker must honor disabled configuration when it restarts.

```python
from evander.addons import AddonRegistration
from evander.integration_contracts import (
    AddonIntegration, IntegrationCheckResult, IntegrationReadinessCheck,
)

def check_connection(context):
    # Use context.directory_path when resolving configuration and credentials.
    # Real network checks must use explicit, bounded I/O timeouts.
    return IntegrationCheckResult("unknown", "not_configured")

def register_addon():
    return AddonRegistration(
        addon_id="example-service",
        display_name="Example service",
        integration=AddonIntegration(
            readiness_checks=(
                IntegrationReadinessCheck("connection", "Connection", check_connection),
            ),
        ),
    )
```

Contract version 1 has these collections:

| Collection | Contract | Purpose |
| --- | --- | --- |
| `settings` | `IntegrationConfigSection` | An object JSON Schema for a configuration section. Validation is available through `IntegrationRegistry.validate_settings`; it never writes configuration. |
| `credentials` | `IntegrationCredentialProvider` | Separate `is_configured(context)` and host-only `resolve(context)` callbacks. Resolution returns a mapping of strings; never return it through Admin Web, MCP or prompts. |
| `readiness_checks` | `IntegrationReadinessCheck` | Explicit read-only checks returning `IntegrationCheckResult(status, code)`. |
| `workers` | `IntegrationBackgroundWorker` | An inert factory returning an `IntegrationWorker` with `run(stop_event)`. A host supervisor owns execution and shutdown. |
| `workflow_starters` | `IntegrationWorkflowStarter` | An object input schema and a builder returning the existing `WorkflowPack` model or its mapping. |

IDs are unique within each collection and scoped by the owning add-on. Different
add-ons may use the same local ID. Unsupported contract versions, duplicate IDs,
self-dependencies and nonlocal JSON Schema references are rejected. Schemas use
Draft 2020-12 and require `type: object`. Validation errors report rule names,
not submitted values; schemas themselves must contain no embedded credentials.

`required_addons` lists installed-and-enabled dependencies; it does not cause
automatic loading or installation. The registry reports missing transitive
dependencies and cycles. An add-on without a new integration declaration can
still satisfy an enabled dependency. Dependency availability is distinct from
provider connectivity; an integration's own readiness checks must verify any
required cross-provider behavior.

## Context and ownership

Callbacks receive `AddonContext`, now including `directory_path` (default
`root` for existing callers), alongside the owner ID, runtime paths and
host-provided services. Registry operations reject a context belonging to a
different add-on. The Admin service resolves directory aliases through the
existing directory service before invoking checks.

Add-on Python code remains trusted host code, not a sandbox or tenant isolation
boundary. Contributions must use existing scoped credential, policy, approval
and audit services. An integration declaration grants no permissions and does
not bypass session capabilities. Secrets remain in the existing host-owned
credential infrastructure; a new credential provider is an adapter, not another
credential database.

## Operator APIs

- `GET /api/admin/integrations` describes integration contracts from the loaded
  add-on catalog. It invokes no contribution callbacks.
- `POST /api/admin/integrations/{addon_id}/readiness` runs read-only checks with
  `{"directory_path": "root"}` (the directory defaults to `root`). Both APIs
  require an authenticated `admin.owner`. There is no credential-resolution or
  worker-launch HTTP endpoint.

Readiness returns `ready`, `blocked` or `unknown`, per-check codes and dependency
issues. A required blocked check makes the integration blocked; otherwise a
required unknown check makes it unknown. With no required checks, readiness is
unknown. Optional checks remain visible without gating required readiness.
Credential presence only verifies configuration, not provider connectivity.

The registry never invokes a credential resolver while checking readiness.
Exceptions become stable `credential_check_failed` or `readiness_check_failed`
codes, without upstream exception text. Checks must be read-only, bounded and
must return only safe machine-readable codes. Results are observations at the
time of the request, not durable authorization to execute later.

Missing dependencies block callbacks for that integration. They do not suppress
other integrations' observations or change existing workflow execution. Existing
add-on loading and error behavior is retained; this stage does not alter loader
failure policy or silently skip active workflow nodes.

## Workers and workflow starters

`IntegrationRegistry.create_worker` explicitly constructs a worker but does not
run it. A future supervisor or existing owning host process must run it, request
shutdown through its stop event and await termination. Registration, startup of
Admin Web and inventory reads never implicitly launch these workers. This stage
does not replace systemd or create a second scheduler.

`IntegrationRegistry.build_workflow_starter` validates inputs before invoking
the builder. The returned pack must account for all nodes in its graph metadata
and assign every graph to the requested directory. It is an ordinary editable
Evander workflow, not a second workflow language or an execution state machine.
Builders must be pure, with no writes, provisioning or external effects. Before
installation, callers must run normal provider/graph validation and use the
existing revision-checked workflow save path. Building a pack does neither.

New integration packages follow [the add-on project layout](addons.md#project-ownership)
and remain separate installations. Core contract tests use synthetic add-ons;
provider migrations and supervision remain owned by their integration projects.
The starter gallery uses these contracts, validates the combined workflow graph,
and installs only after an explicit, revision-checked activation. Builders can supply
an optional `IntegrationStarterVerification` callback describing a completion node,
trigger IDs, instructions, and required successful tool-call evidence.

## Teams and Graph

[`packages/teams-graph-addon`](../packages/teams-graph-addon/README.md) is the first extracted
provider integration. Its registration exposes local configuration readiness, credential access,
and host-supervised workers. Existing workflows continue using their current node and tool IDs.
The core distribution does not depend on or install this project.
