# 4. System Topology, Services, and Ports

> **At a glance.** Evander is a set of small host services around shared runtime state, not one monolithic daemon. Admin Web and the CLI assemble the control plane; the MCP gateway and relays face isolated workloads; provider workers ingest external change; trigger and effect orchestrators advance workflows; and an optional worker daemon runs on a separate worker VM. Ports describe reachability, while sessions, policy, target allowlists, and credentials describe authority.

## The topology in one view

For a standard libvirt deployment, the important paths are:

```text
operator browser/CLI
        |
        v
Admin Web + shared admin runtime ---- runtime stores/databases
        |                                      ^
        v                                      |
trigger/effect workers <---- provider workers/events
        |
        v
libvirt VM ---- MCP / model / package / HTTP relays ---- external providers
        `------ restricted SSH/Git -------------------- managed bare repos
```

With `container_worker_vm`, agent effects call `evander-worker-daemon` on a dedicated worker VM. The disposable Docker job then calls back to the control-plane gateway and relays through explicitly configured guest URLs.

Processes share domain services and stores, but they do not all need to be running for every installation. A host used only for local VM sessions may not configure Teams, Discord, DIDWW, Azure DevOps, or a container worker. “Installed by the systemd installer” and “required for this workflow” are different statements.

## Operator and guest-facing services

These are the default listeners in current configuration. Live environment files can override them.

| Surface | Entry point or service | Default bind/port | Intended caller | Notes |
|---|---|---:|---|---|
| Admin Web | `evander-admin-web` | `127.0.0.1:8100` | Operator browser or reverse proxy | Dashboard, sessions, approvals, workflows, config, users, chat, and audit. `/healthz` is the health endpoint. |
| MCP gateway | `evander-gateway` | `0.0.0.0:8000` | Guest/container MCP client | Streamable MCP at `/mcp`; a plain GET may return `406` and still prove the endpoint is listening. |
| OpenAI relay | `evander-openai-relay` | `0.0.0.0:11434` | Guest/container model client | Keeps host model credentials; forwards streaming SSE without buffering away the stream lifetime. |
| NuGet relay | `evander-nuget-relay` | `0.0.0.0:11666` | Guest/container package client | Narrow NuGet package surface; `/healthz` is available. |
| HTTP target relay | `evander-http-target-relay` | `0.0.0.0:11777` | Session with configured target alias | Constrains external HTTP targets; forwards guest-managed auth rather than injecting a host secret. |
| npm audit facade | `evander-npm-audit-facade` | `0.0.0.0:11881` | Guest/container npm tooling | Narrow audit compatibility surface. |
| DIDWW SIP bridge | `evander-didww-sip-bridge` | Configurable; example is loopback `11888` | Host telephony integration | Optional, host-local integration; do not confuse it with the worker daemon's port on a separate host. |
| Worker daemon | `evander-worker-daemon` | CLI default `127.0.0.1:11888`; Ansible worker default `0.0.0.0:11888` | Authenticated control plane on the constrained worker network | Deployed by `ansible/worker.yml`, not the control-plane systemd installer. |

Admin Web deliberately defaults to loopback. Exposing it through a reverse proxy requires the operator to preserve authentication, cookie, TLS, and network controls; changing its bind address alone is not a complete exposure design.

## Firewall defaults

`config/firewall.env.example` permits guest-to-host TCP only on an explicit list:

| Port | Expected use |
|---:|---|
| `22` | SSH and restricted managed Git |
| `8000` | MCP gateway |
| `11434` | OpenAI-compatible model relay |
| `11666` | NuGet relay |
| `11777` | HTTP target relay |
| `11881` | npm audit facade |
| `8081` | Optional Nexus PyPI/npm cache |
| `3142` | Optional apt-cacher-ng provisioning cache |

The last two are firewall allowances for optional services, not services started by Evander. Conversely, loopback Admin Web and host-local support databases are not guest firewall entries. Qdrant, where used for semantic storage, defaults to a host-local `127.0.0.1:6333` provider endpoint rather than a general guest-facing database.

Opening a port establishes a route. The service still applies its own session proof, target, repository, or upstream-authentication rules.

## Workers without public listeners

Most background services are polling or queue consumers:

| Worker | Responsibility |
|---|---|
| `evander-trigger-orchestrator.service` | Poll enabled workflow triggers, publish durable events, advance cursors, and wake the effect worker. Its command is `evander-cli trigger-orchestrator`, not a separate `evander-trigger-orchestrator` console script. |
| `evander-effect-orchestrator.service` | Route queued events through graphs and execute gates, queries, and effects. |
| `evander-ado-worker.service` | Poll Azure DevOps PR feedback/completion; work-item intake remains workflow-owned. |
| `evander-proxy-issue-sync.service` | Refresh local proxy issue state from Azure DevOps. |
| `evander-discord-ingest.service` | Ingest Discord data. |
| `evander-discord-wake.service` | Turn eligible Discord messages into agent wake activity. |
| `evander-scheduled-wake.service` | Resume sessions whose scheduled wake is due. |
| `evander-teams-ingest.service` | Run `evander-cli teams-ingest` through `scripts/run_teams_ingest.sh`. |
| `evander-teams-meeting-ingest.service` | Ingest configured Teams meeting media/events. |

There is no current `evander-host-heartbeat.service`. Older references to it are stale. Azure DevOps feedback polling belongs to the ADO worker; Discord and scheduled wakeups belong to their dedicated workers. Optional guest-local heartbeat timer assets are a different mechanism and are disabled by default by provider bootstraps.

## Control-plane installation set

`scripts/install_systemd_units.sh` renders and enables the firewall, gateway, Admin Web, OpenAI relay, DIDWW bridge, NuGet relay, npm audit facade, HTTP target relay, ADO and proxy workers, Discord ingest/wake, scheduled wake, Teams ingest/meeting ingest, and both orchestrators.

Enabling a unit does not make an unconfigured integration healthy. Before install or start, populate the required local files for the services in use, including `config/firewall.env`, `config/admin-web.env`, provider worker JSON, trigger-orchestrator JSON, optional relay env files, and optional repository-root `secrets.env`.

The unit templates generally order processes after network readiness and load their configured environment. They are deliberately separate failure domains. The gateway can be healthy while a Teams credential is invalid; Admin Web can render while the effect worker is stopped; a package relay can be absent without changing workflow queue durability.

## Runtime state shared across services

All control-plane processes must agree on the runtime root. Important shared surfaces include:

- `host-config.json` and `directory-config/` for bootstrap and effective configuration;
- session, approval, directory, wake, prompt, and provider state stores;
- `orchestration.db` for the default trigger queue and workflow execution state;
- `audit.jsonl` plus `audit.db` for canonical audit and its query index;
- `managed-git-repositories.json`, `managed-git/`, and `git-worktrees/` for managed source;
- add-on state below `runtime/addons/` when an add-on is installed.

If two services point at different runtime roots, each may look internally consistent while the system appears split-brained. Always inspect the unit's effective command and environment before editing the repository's example configuration.

## A Northstar request across processes

For the telemetry-retry work item, a typical process sequence is:

1. A provider worker or workflow trigger sees Northstar's source event using credentials resolved for `root/northstar-platform/mission-control`.
2. The trigger orchestrator writes a queue record to `orchestration.db` and signals the effect worker's local wake mechanism.
3. The effect worker creates Imani's session and calls the selected backend.
4. Imani's VM reaches ports `8000`, `11434`, and package surfaces through the host firewall; it uses port `22` for restricted managed Git.
5. Operators inspect state through Admin Web on `8100`, normally via the host or its reverse proxy.
6. Review feedback returns through a provider worker or trigger and re-enters the same durable event path.

No request needs to pass through Admin Web to reach the gateway, and Admin Web is not the workflow engine. It is an operator surface over the same domain services and stores.

## Failure behavior

| Symptom | Layer to inspect first | Important distinction |
|---|---|---|
| Connection refused | Listener/process and bind address | The service is not reachable; policy has not run yet. |
| Timeout from guest | Firewall, routing, service bind, or worker network | A port may listen on the wrong interface. |
| HTTP `401` from MCP | Session proof | The network path works; authentication failed. |
| HTTP `406` from plain GET `/mcp` | MCP content negotiation | This can be expected liveness evidence, not a dead gateway. |
| Tool absent or denied | Session and effective policy | Gateway reachability does not grant the tool. |
| Admin Web healthy, runs stalled | Trigger/effect services and queue | The UI is not the executor. |
| Trigger healthy, effects absent | Effect worker, graph, queue, completion state | Trigger publication and effect execution are distinct stages. |
| Optional cache port open, restore fails | Actual cache service and client URL | Firewall allowance does not start Nexus or apt-cacher-ng. |
| Worker daemon unreachable | Control-plane-to-worker routing/token/service | Guest firewall settings are not the same path. |
| All stores appear empty after restart | Runtime-root mismatch | The process may be using `/var/lib/evander` while the operator inspects repository `runtime`. |

## Practical runbook

Begin with the application view and then descend to systemd and sockets:

```bash
evander-cli status --runtime-dir runtime --json
sudo systemctl --failed
sudo systemctl status evander-gateway.service
sudo systemctl status evander-admin-web.service
sudo systemctl status evander-trigger-orchestrator.service
sudo systemctl status evander-effect-orchestrator.service
sudo ss -ltnp
```

Check the narrow health paths:

```bash
curl -i http://127.0.0.1:8100/healthz
curl -i http://127.0.0.1:11666/healthz
curl -i http://127.0.0.1:8000/mcp
```

For the gateway probe, record the status and headers; do not require `200` from a content-negotiation-free GET. For service failures, inspect the actual unit and recent logs:

```bash
sudo systemctl cat evander-gateway.service
sudo journalctl -u evander-gateway.service -n 200 --no-pager
```

Before reinstalling units, run the supported preflight:

```bash
./scripts/install_systemd_units.sh --preflight
```

Do not start every optional worker merely to make `systemctl --failed` empty. Configure and run only the integrations the deployment intends to use, while keeping required core surfaces healthy.

## Source trail

- `pyproject.toml` — installed console entry points.
- `systemd/*.service.template` and `scripts/install_systemd_units.sh` — current control-plane units, commands, environment files, and installation set.
- `config/firewall.env.example` and `scripts/apply_firewall.sh` — guest-to-host port defaults and firewall realization.
- `src/evander/host_config.py`, `src/evander/admin/web/app.py`, `src/evander/worker/cli.py`, and `src/evander/didww_sip_bridge.py` — listener defaults.
- `src/evander/daemons/trigger_orchestrator.py` and `src/evander/daemons/effect_orchestrator.py` — orchestration worker topology.
- `docs/operations.md`, `docs/architecture.md`, and `docs/container-worker-backend.md` — supported deployment and worker layout.
- `tests/test_install_systemd_units_script.py`, `tests/test_systemd_docs.py`, and `tests/test_firewall_defaults.py` — executable service and port documentation checks.
