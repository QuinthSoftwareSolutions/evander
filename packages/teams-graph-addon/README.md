# Microsoft Teams and Graph add-on

This optional distribution owns Teams messaging, Microsoft device-code authentication,
Graph mail, Graph notification subscriptions, and Teams meeting/voice adapters. Core Evander
does not install it. It uses the existing host-owned secrets, policy checks, directory scopes,
and workflow runtime.

## Install or upgrade

Version 0.2 requires Evander 0.89 or later and contributes the **Respond to Teams direct
messages** starter. Once this add-on is loaded, open **Workflow starters** in Admin Web.
See the [starter guide](../../docs/workflow-starters.md#respond-to-teams-direct-messages).

From an Evander source checkout, install both projects into the same host virtualenv:

```bash
uv pip install --python .venv/bin/python -e . -e ./packages/teams-graph-addon
```

To include the integration in the add-on inventory, append
`evander_teams_graph_addon` to `addon_loader.modules`, or enable entry-point discovery with
`addon_loader.enabled_entry_points: true`. If `addon_loader.enabled_addon_ids` is an explicit
list, also append `teams-graph`; preserve the other entries. Configure this through the existing
operator configuration interfaces. The legacy default with no explicit list continues to permit
Teams/Graph when the package is installed.

After changing enablement, restart every service listed by Admin Web, including
Teams ingest, notifications, and meeting ingest. A disabled Teams worker exits
successfully at startup before opening a listener or performing provider work.

Reinstall the service definitions after installing the package:

```bash
./scripts/install_systemd_units.sh --preflight
sudo ./scripts/install_systemd_units.sh
```

The installer resolves the package's own service templates and includes the three Teams
services only when the package is available and enabled. It does not start, stop, remove, or
delete existing services. Preflight reads existing host configuration without initializing
runtime state. Restart the services listed by Admin during your normal maintenance window;
enabling or disabling this integration includes the gateway because it registers Teams tools
at startup.
The existing `evander-cli teams-ingest`, `teams-auth`, and `teams-meeting` commands remain valid.
The package supplies `evander-teams-ingest`, `evander-teams-notifications`, and
`evander-teams-meeting-ingest` console commands.

Ansible installs this project when a Teams service is selected. The existing full-stack defaults
therefore preserve their behavior. Set `evander_teams_graph_addon_enabled: true` for Graph mail
without Teams workers. To omit the integration, disable its three service toggles and set
`evander_teams_graph_addon_enabled: false`.

## Compatibility and data

No data migration is required. Keep existing directory provider settings, environment secret
references, managed credential references, encrypted identity stores, Teams message databases,
subscription state, meeting bindings, and notification URLs. Existing workflow node and tool IDs
are unchanged. Legacy Python import paths delegate to this package and retain class identity.

Core retains the persisted data models, local stores, policy vocabulary, and workflow schemas
needed to read saved sessions and workflows. Local event consumers remain in core. Network
clients, auth implementation, workers, Teams admin handlers, MCP handlers, and deployment assets
live in this project. This boundary deliberately avoids changing existing workflow definitions
or introducing a separate execution engine.

Without the package, core runtime, CLI help, and unrelated providers remain usable. Configured
Teams/Graph calls fail with installation guidance; Teams MCP handlers are absent and existing
auth URLs return an authenticated `503`. Re-enable and restart after reinstalling the package.
Before uninstalling, stop the Teams services and remove this add-on from explicit loader lists.
The installer never deletes credentials or runtime records.

## Extension contract

Registration contributes Teams settings, a host-only credential resolver, configuration readiness,
and three worker factories through the Stage 1 contract. Registration and readiness make no
network requests, decrypt no credentials, and start no workers. Readiness confirms local
configuration only: it does not prove Microsoft consent, grant validity, or connectivity. Existing
auth commands and mocked provider tests cover those boundaries separately.

Worker factories produce cooperative, host-supervised workers. The established systemd commands
continue to use the existing worker loops, file locks, retries, and durable state. Workflow starters
and guided setup are separate work; any existing custom workflow can keep using these providers.

## Tests

From the repository root with core's test dependencies installed:

```bash
PYTHONPATH=src:packages/teams-graph-addon .venv/bin/pytest -q packages/teams-graph-addon/tests
PYTHONPATH=src .venv/bin/pytest -q tests/test_optional_integrations.py
```

Provider tests live here. Core tests use local data contracts and synthetic adapters, and the
absence tests explicitly hide the add-on. These are local tests, not evidence of a live Microsoft
tenant sign-in, callback delivery, or meeting connection.
