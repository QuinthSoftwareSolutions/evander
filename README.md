# Evander

Evander runs AI agents in isolated environments and gives operators control over the tools and
services they can use. It is a self-hosted control plane for agent sessions, workflows, approvals,
and audit records.

Agents work in disposable libvirt VMs or in containers on dedicated worker VMs. The host manages
credentials and mediates access to models, repositories, packages, and other services. Operators
can use Admin Web or the CLI to configure environments, start work, and follow its progress.

## What you can do

- **Run isolated agents.** Choose local libvirt VMs or containers on a separately deployed worker
  VM. Create, inspect, and manage sessions through the same control plane.
- **Grant access deliberately.** Give each session a defined set of tools and resources. Host-owned
  relays and connectors provide scoped access to models, Git, packages, and approved HTTP targets.
- **Review sensitive actions.** Configure policy and approval requirements for tool calls, and
  inspect their audit records. Mandatory approvals are single-use and bound to the requested payload.
- **Coordinate work.** Use durable workflows, triggers, and effects to start agents, handle events,
  and carry work through review and recovery. The optional Team Add-on supports persistent agents
  and task hierarchies.
- **Work with repositories.** Register managed Git repositories, give agents restricted access,
  and promote an exact commit for review or publication.
- **Add integrations when needed.** Optional add-ons provide capabilities such as shared browser
  sessions, application environments, messaging, and database or service tooling.

## How it fits together

The Evander host runs the control plane: Admin Web, the CLI, the MCP gateway, workflow workers,
relays, and durable stores. Agents run in their assigned VM or worker container and reach permitted
host services through scoped connections. The host owns the configuration and the credentials used
by its services; an agent does not need direct access to those credentials.

Guest and worker-container networking is default-deny, subject to the deployed firewall and worker
policy. Tool calls are checked against the session's manifest, while other services enforce their
own access rules. See [architecture](docs/architecture.md) for the components and trust boundaries.

## Quick start

Host deployment scripts target Ubuntu 24.04, and the Python package requires Python 3.12 or newer.
The default execution backend uses libvirt; container execution requires a separately deployed
worker VM. See [execution backends](docs/execution-backends.md) and the
[host requirements](docs/operations.md#host-packages) before provisioning.

1. From the repository root, bootstrap Evander and open the guided setup:

   ```bash
   ./scripts/setup -- --runtime-dir runtime
   source .venv/bin/activate
   ```

   The script creates or reuses `.venv`, installs the package, and starts the setup checklist.

2. Configure the host, credentials, policies, and integrations you intend to use. Host bootstrap
   settings live in `runtime/host-config.json`; directory settings can be managed through Admin Web
   or the CLI. The [operations guide](docs/operations.md#bootstrap-flow) covers the manual path.

3. Prepare an execution backend. For libvirt, install the host packages and create the isolated
   network and base image:

   ```bash
   sudo ./scripts/create_libvirt_network.sh
   sudo ./scripts/create_base_vm.sh
   ```

   For container execution, follow the [worker deployment guide](docs/container-worker-backend.md).

4. Configure and install the services you need:

   ```bash
   ./scripts/install_systemd_units.sh --preflight
   sudo ./scripts/install_systemd_units.sh
   ```

   The installer enables services but does not start them. Follow the
   [service runbook](docs/operations.md#systemd-services) to start the configured subset, then check
   the application view:

   ```bash
   evander-cli status --runtime-dir runtime --json
   ```

   Admin Web defaults to `http://127.0.0.1:8100`. Set up
   [authentication](docs/admin-ui-user-manual.md#open-the-admin-ui) before exposing it beyond the host.

5. Create an agent from Admin Web or a workflow. To give it code to work on, register a repository
   through [managed Git](docs/git-remotes.md), then grant the session the access it needs. Use the
   [live smoke tests](docs/live-smoke-tests.md) to verify your configured setup.

## Documentation

| If you want to... | Start here |
| --- | --- |
| Install, configure, or operate a host | [Operations](docs/operations.md) and [configuration reference](docs/configuration-reference.md) |
| Use Admin Web | [Admin Web manual](docs/admin-ui-user-manual.md) |
| Choose an execution backend | [Execution backends](docs/execution-backends.md) and [container workers](docs/container-worker-backend.md) |
| Start a workflow or coordinate agents | [Workflow starters](docs/workflow-starters.md) and [Team Add-on](docs/addons.md#team-module-guidance) |
| Connect a repository | [Managed Git](docs/git-remotes.md) |
| Understand the design | [Architecture](docs/architecture.md) |
| Find a subsystem or detailed recipe | [Documentation map](docs/README.md) and [operator reference](docs/operator-reference.md) |

## Development

For a development environment and the complete Python test suite:

```bash
uv sync --frozen --extra dev
./.venv/bin/python scripts/run_tests.py --workers 2 --timeout 1800
```

The runner separates performance-sensitive tests from parallel work and checks that the selected
suite completed. Admin Web also has unit and browser tests. See [testing](docs/testing.md) for
focused commands, prerequisites, and timeouts.

The control-plane code is in `src/evander/`, the web interface is in `frontend/admin-web/`, and
independently packaged add-ons are in `packages/`. Host scripts, configuration examples, and
operational documentation live in `scripts/`, `config/`, and `docs/` respectively.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and pull request guidance. Report
vulnerabilities privately using the instructions in [SECURITY.md](SECURITY.md).

## License

Evander's first-party contents, including its add-ons, are licensed under the
[GNU Affero General Public License, version 3 only](LICENSE) (`AGPL-3.0-only`). Bundled third-party
components retain their own licenses and notices, including the
[Team UI bundles](src/evander_team_addon/ui_static/THIRD_PARTY_LICENSES.txt),
[browser controller](packages/browser-sessions-addon/evander_browser_sessions_addon/controller/THIRD_PARTY_LICENSES.txt),
[Teams fonts](packages/teams-graph-addon/evander_teams_graph_addon/assets/fonts/LICENSE.txt),
and [Godot](docker/evander-runner-godot/LICENSE.godot.txt).
