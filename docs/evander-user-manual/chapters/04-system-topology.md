# 4. System Topology, Services, and Ports

> **At a glance.** Evander has a visible operator interface and several background parts. Admin Web is where people inspect work and make decisions, while workflow services notice events, start jobs, and move them forward. Temporary workspaces do the agent's work, and controlled connections provide only the tools and outside access that job needs. The first half of this chapter helps an operator recognize those parts and common failure patterns. The second half gives administrators the exact service, port, firewall, and storage map.

## Four parts to recognize

You do not need to memorize process names to understand where work is happening. Start with four parts:

1. **Admin Web is the operator surface.** It shows runs, sessions, approvals, configuration, workers, and audit records. It lets a person observe or decide; it is not the process that continuously executes workflows.
2. **Workflow services coordinate the work.** They notice eligible events, record them, decide which workflow path applies, and advance that path. They continue to matter even when nobody has Admin Web open.
3. **A temporary workspace performs the agent's job.** Depending on the configured backend, this is a disposable virtual machine or a disposable container inside a worker machine.
4. **Controlled connections provide capabilities.** The workspace reaches Evander tools, a model, managed Git, packages, or approved outside systems through narrow routes. A working route does not by itself grant permission to use everything behind it.

The relationship is easier to see without the service names:

```text
external event ---------> workflow services ---------> temporary workspace
                                |                              |
                                v                              v
operator <----> Admin Web <----> shared job records     controlled connections
```

An integration can deliver work directly to the workflow services; it does not have to pass through Admin Web. Admin Web and the background services meet through shared Evander state, which is why the interface can show work that arrived while no operator was signed in.

## What an operator may notice

Because these parts are separate, one can be healthy while another is waiting, denied, misconfigured, or unavailable. That is useful evidence rather than a contradiction.

| What you see | What it usually tells you | Useful information to capture |
|---|---|---|
| Admin Web opens, but a run does not advance | The operator interface is available; a workflow service, queue, approval, or execution backend may need attention | Directory, run ID, session ID, last status, and time of the last change |
| A request reaches Evander but is denied | The connection may be working; a job permission, target rule, argument rule, or approval check stopped the action | Tool or action, visible denial, target, session ID, and approximate time |
| An integration is configured, but no new work arrives | Its dedicated worker, credential, provider connection, or source-side filter may be unavailable | Directory, integration name, expected item or message, and last successful activity |
| A session is created, but no workspace starts | Coordination reached the execution step; virtual-machine or worker provisioning may have failed | Session ID, execution or VM ID, worker name if shown, and the visible failure |
| The agent works locally but cannot reach a model, package, repository, or approved target | The temporary workspace is alive; the relevant controlled connection needs inspection | Destination or capability, exact error, session ID, and whether the failure is repeatable |

The important distinction is **reachable versus permitted**. A refused connection means the request did not reach a service. A denial from that service means the route worked and a later check made a decision.

## The process topology in one view

The plain map above is enough for ordinary orientation. The exact process path is useful when reading logs, status output, or deployment configuration.

> <span class="sidebar-kicker">Deeper detail</span> **Standard and worker-backed paths**
>
> In a standard deployment using local disposable virtual machines, the major paths are:
>
> ```text
> operator browser/CLI
>         |
>         v
> Admin Web + shared admin runtime ---- runtime stores/databases
>         |                                      ^
>         v                                      |
> trigger/effect workers <---- provider workers/events
>         |
>         v
> libvirt VM ---- controlled relays ---- external systems
>         `------ restricted Git ------ managed repos
> ```
>
> Here, “controlled relays” covers MCP, model, package, and approved HTTP access.
>
> With the `container_worker_vm` backend, agent effects call `evander-worker-daemon` on a dedicated worker VM. The daemon creates a disposable Docker job, which calls back to the control-plane gateway and relays through explicitly configured guest URLs.

Processes share domain services and stores, but they do not all need to be running for every installation. A host used only for local sessions may not configure Teams, Discord, DIDWW, Azure DevOps, or a container worker. “Installed by the service installer” and “required for this workflow” are different statements.

## Services an operator may hear about

The names below often appear in status messages or an administrator's explanation. Their plain roles matter more than their command lines.

### Core services

“Core” here means part of the ordinary control-plane or agent path. A particular action may use only some of them—for example, opening Admin Web does not require a workflow run to be active.

| Role | Name you may see | Plain responsibility |
|---|---|---|
| Operator interface | `evander-admin-web` | Shows and changes supported Evander state for authenticated users. |
| Event intake | `evander-trigger-orchestrator.service` | Notices eligible trigger events and records them durably. |
| Workflow execution | `evander-effect-orchestrator.service` | Advances recorded events through workflow decisions and effects. |
| Tool access | `evander-gateway` | Presents the job's allowed Evander tools and checks the session on each request. |
| Model access | `evander-openai-relay` | Lets the agent harness call the configured model while the real provider credential stays on the host. |
| Execution backend | Local libvirt support or `evander-worker-daemon` | Creates and removes the temporary workspace selected for the job. |

### Optional integration services

| Capability | Services or components you may see | When they matter |
|---|---|---|
| Azure DevOps feedback and proxy issue state | `evander-ado-worker.service`, `evander-proxy-issue-sync.service` | When Azure DevOps supplies review feedback or locally mirrored issue state. |
| Discord intake | `evander-discord-ingest.service` | Stores Discord messages for workflow triggers and downstream prompt effects. |
| Scheduled resumes | `evander-scheduled-wake.service` | When a session should resume at a recorded future time. |
| Teams intake | `evander-teams-ingest.service`, `evander-teams-notifications.service`, `evander-teams-meeting-ingest.service` | When Teams messages, optional Graph callbacks, meetings, or media enter configured flows. |
| Packages and approved HTTP targets | NuGet relay, npm-audit facade, optional package caches, HTTP target relay | When a workspace needs one of those controlled outside routes. |
| Telephony | `evander-didww-sip-bridge.service` | When the DIDWW integration is configured. |
| Remote container execution | `evander-worker-daemon` on a worker VM | Only when the selected backend uses disposable worker containers. |

Optional does not mean unimportant. It means the service is needed only when the deployment uses that capability.

## A Northstar request across the system

Return to the telemetry-retry work item from Chapter 2. Without process names, the trip looks like this:

1. A configured integration notices the source event.
2. Workflow services record the event before treating it as accepted work.
3. The workflow decides what should happen and asks the selected execution backend for a temporary workspace.
4. The agent works there, using only the controlled tool, model, package, HTTP, and Git routes provided for the job.
5. An operator follows progress and handles any approval through Admin Web.
6. Review feedback or another source event returns through its integration and enters the same durable workflow path.

Admin Web can display and influence this work, but the request does not travel through the browser on its way to the gateway. Closing the browser does not stop the background workflow services.

> <span class="sidebar-kicker">Technical sidebar</span> **The same request with process and port names**
>
> 1. A provider worker or workflow trigger sees Northstar's source event using credentials resolved for `root/northstar-platform/mission-control`.
> 2. The trigger orchestrator writes a queue record to `orchestration.db` and signals the effect worker's local wake mechanism.
> 3. The effect worker creates the implementation session and calls the selected backend.
> 4. A local implementation VM reaches ports `8000`, `11434`, and configured package surfaces through the host firewall; it uses port `22` for restricted managed Git.
> 5. Operators inspect state through Admin Web on `8100`, normally from the host or through its reverse proxy.
> 6. Review feedback returns through a provider worker or trigger and re-enters the same durable event path.

## Reading common failures

An operator can use the first column to describe the symptom. The remaining columns help an administrator choose the first layer to inspect.

| Symptom | Layer to inspect first | Important distinction |
|---|---|---|
| Connection refused | Listener process and bind address | The service is not reachable; policy has not run yet. |
| Timeout from a workspace | Firewall, routing, service bind, or worker network | A port may listen on the wrong interface. |
| HTTP `401` from MCP | Session proof | The network path works; authentication failed. |
| HTTP `406` from plain GET `/mcp` | MCP content negotiation | This can be expected liveness evidence, not a dead gateway. |
| Tool absent or denied | Session and effective policy | Gateway reachability does not grant the tool. |
| Admin Web healthy, runs stalled | Trigger/effect services and queue | The UI is not the executor. |
| Trigger healthy, effects absent | Effect worker, graph, queue, completion state | Trigger publication and effect execution are distinct stages. |
| Optional cache port open, restore fails | Actual cache service and client URL | Firewall allowance does not start Nexus or apt-cacher-ng. |
| Worker daemon unreachable | Control-plane-to-worker routing, token, or service | Guest firewall settings are not the same path. |
| All stores appear empty after restart | Runtime-root mismatch | The process may use `/var/lib/evander` while someone inspects repository `runtime`. |

## Troubleshooting: what to hand to an administrator

Start with what Admin Web can show. Capture what is available; a missing identifier should not prevent a useful handoff.

1. Confirm the selected directory and record its path.
2. Open the relevant run or session and record the run or trace ID, session ID, source item or event ID, last status, and time of the last change.
3. Copy the exact visible error or denial. If an approval, tool call, execution, VM, or worker is involved, record its ID too.
4. Note what still works: for example, “Admin Web opens and the session exists, but no workspace was created.” This helps locate the boundary that failed.
5. Give those details to an Evander administrator. Do not delete runtime files, open firewall ports, or start unrelated services to make the symptom disappear.

If Admin Web itself is unavailable, record the address used, the time, and the exact browser or proxy error. An operator normally does not need socket commands or host access to make the initial report.

The remainder of this chapter is administrator reference material.

## Administrator reference: listeners and ports

A **listener** is a service waiting for network requests. Its bind address determines which host interfaces accept those requests. Loopback (`127.0.0.1`) means the same host only; `0.0.0.0` means all configured interfaces, still subject to routing and firewall rules. Live environment files can override the defaults below.

### Core and commonly used listeners

| Surface | Entry point | Default bind/port | Intended caller | Notes |
|---|---|---:|---|---|
| Admin Web | `evander-admin-web` | `127.0.0.1:8100` | Operator browser or reverse proxy | Dashboard, sessions, approvals, workflows, config, users, chat, and audit. `/healthz` is the health endpoint. |
| MCP gateway | `evander-gateway` | `0.0.0.0:8000` | Workspace MCP client | Streamable MCP at `/mcp`; a plain GET may return `406` and still prove the endpoint is listening. |
| OpenAI relay | `evander-openai-relay` | `0.0.0.0:11434` | Workspace model client | Keeps host model credentials and forwards streamed responses as they arrive. |

Admin Web deliberately defaults to loopback. Exposing it through a reverse proxy requires authentication, cookie, TLS, and network controls; changing its bind address alone is not a complete exposure design.

### Optional listeners

| Surface | Entry point or service | Default bind/port | Intended caller | Notes |
|---|---|---:|---|---|
| Codex subscription egress proxy | `evander-codex-egress-proxy` | `127.0.0.1:11889`; private control-plane IP under Ansible | Configured container workers | Restricts worker source CIDRs and OpenAI/ChatGPT destinations; it is not a guest firewall surface. |
| NuGet relay | `evander-nuget-relay` | `0.0.0.0:11666` | Workspace package client | Narrow NuGet package surface; `/healthz` is available. |
| HTTP target relay | `evander-http-target-relay` | `0.0.0.0:11777` | Session with a configured target alias | Constrains target requests; ordinary requests forward guest-managed auth. Explicitly granted managed website sessions can obtain host-managed login state. |
| HTTP target browser proxy | `evander-http-target-browser-proxy` | `0.0.0.0:11778` | Authenticated session browser | Preserves real origins while restricting access to configured session targets. |
| Teams notifications | `evander-teams-notifications` | `127.0.0.1:11882` | Reverse proxy for configured Graph callbacks | Validates and durably queues notifications; an independent processor retrieves exact messages. |
| npm audit facade | `evander-npm-audit-facade` | `0.0.0.0:11881` | Workspace npm tooling | Narrow audit compatibility surface. |
| DIDWW SIP bridge | `evander-didww-sip-bridge` | Configurable; example is loopback `11888` | Host telephony integration | Host-local integration; do not confuse its example port with the worker daemon on a separate host. |
| Worker daemon | `evander-worker-daemon` | CLI default `127.0.0.1:11888`; Ansible worker default `0.0.0.0:11888` | Authenticated control plane on the constrained worker network | Deployed by `ansible/worker.yml`, not the control-plane service installer. |

## Administrator reference: firewall defaults

`config/firewall.env.example` permits guest-to-host TCP only on an explicit list:

| Port | Expected use |
|---:|---|
| `22` | SSH and restricted managed Git |
| `8000` | MCP gateway |
| `11434` | OpenAI-compatible model relay |
| `11666` | NuGet relay |
| `11777` | HTTP target relay |
| `11778` | HTTP target browser proxy |
| `11881` | npm audit facade |
| `8081` | Optional Nexus PyPI/npm cache |
| `3142` | Optional apt-cacher-ng provisioning cache |

The last two are firewall allowances for optional services, not services started by Evander. Conversely, loopback Admin Web and host-local support databases are not guest firewall entries. Qdrant, where used for semantic storage, defaults to a host-local `127.0.0.1:6333` provider endpoint rather than a general workspace-facing database.

Opening a port establishes a route. The service still applies its own session proof, target, repository, or upstream-authentication rules. Port reachability is not a capability grant.

## Administrator reference: background workers

### Core workflow workers

| Worker | Responsibility |
|---|---|
| `evander-trigger-orchestrator.service` | Poll enabled workflow triggers, publish durable events, advance cursors, and wake the effect worker. Its command is `evander-cli trigger-orchestrator`, not a separate `evander-trigger-orchestrator` console script. |
| `evander-effect-orchestrator.service` | Route queued events through graphs and execute gates, queries, and effects. |

### Optional integration and wake workers

| Worker | Responsibility |
|---|---|
| `evander-ado-worker.service` | Poll Azure DevOps PR feedback and completion; work-item intake remains workflow-owned. |
| `evander-proxy-issue-sync.service` | Refresh local proxy issue state from Azure DevOps. |
| `evander-discord-ingest.service` | Ingest Discord data. |
| `evander-scheduled-wake.service` | Resume sessions whose scheduled wake is due. |
| `evander-managed-git-refresh.service` | Refresh an authorized managed repository before clone, fetch, pull, or archive reads. |
| `evander-teams-ingest.service` | Run `evander-cli teams-ingest` through `scripts/run_teams_ingest.sh`; poll authorized targets and reconcile optional Graph subscriptions. |
| `evander-teams-notifications.service` | Validate Graph callbacks, persist a notification queue, and process exact-message retrieval independently of fallback polling. |
| `evander-teams-meeting-ingest.service` | Ingest configured Teams meeting media and events. |

Most background services poll a provider or consume a queue rather than accept public network connections.

There is no current `evander-host-heartbeat.service`. Older references to it are stale. Azure DevOps feedback polling belongs to the ADO worker; Discord and scheduled wakeups belong to their dedicated workers. Optional guest-local heartbeat timer assets are a different mechanism and are disabled by default by provider bootstraps.

## Administrator reference: installation and shared state

On a systemd-based host, `scripts/install_systemd_units.sh` renders and enables the control-plane
units listed in Chapter 28, including the HTTP browser proxy, managed Git refresh broker, and Teams
notification listener. It prints start commands without starting the services. The
`evander-team-runtime.target` groups the gateway, Admin Web, and both orchestrators so their Team
runtime generations can be restarted together; Team work runs within those processes.

> <span class="sidebar-kicker">Administrator note</span> **Enabled does not mean configured or healthy**
>
> Before installation or startup, populate the required local files for the services in use, including `config/firewall.env`, `config/admin-web.env`, provider-worker JSON, trigger-orchestrator JSON, optional relay environment files, and optional repository-root `secrets.env`. The unit templates generally start processes after network readiness and load their configured environment, but they cannot supply missing credentials or integration settings.

The separate units are deliberate failure boundaries. The gateway can be healthy while a Teams credential is invalid. Admin Web can render while the effect worker is stopped. A package relay can be absent without changing workflow queue durability.

All control-plane processes must also agree on the **runtime root**: the directory containing Evander's live configuration, records, databases, managed repositories, and add-on state. Important shared surfaces include:

- `host-config.json` and `directory-config/` for bootstrap and effective configuration;
- session, approval, directory, wake, prompt, and provider state stores;
- `orchestration.db` for the default trigger queue and workflow execution state;
- `audit.jsonl` plus `audit.db` for canonical audit and its query index;
- `managed-git-repositories.json`, `managed-git/`, and `git-worktrees/` for managed source;
- add-on state below `runtime/addons/` when an add-on is installed.

If two services point at different runtime roots, each may look internally consistent while the system appears split. Always inspect a unit's effective command and environment before editing the repository's example configuration.

## Administrator runbook

Begin with the application view and then descend to the Linux service manager and network listeners:

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

For the gateway probe, record the status and headers; do not require `200` from a GET that omits MCP content negotiation. For service failures, inspect the effective unit and recent logs:

```bash
sudo systemctl cat evander-gateway.service
sudo journalctl -u evander-gateway.service -n 200 --no-pager
```

Before reinstalling units, run the supported preflight:

```bash
./scripts/install_systemd_units.sh --preflight
```

Do not start every optional worker merely to make `systemctl --failed` empty. Configure and run only the integrations the deployment intends to use, while keeping the core surfaces required by its workflows healthy.

## Source trail

- `pyproject.toml`, `systemd/*.service.template`, and `scripts/install_systemd_units.sh` — installed entry points and current control-plane units.
- `config/firewall.env.example` and `scripts/apply_firewall.sh` — workspace-to-host port defaults and firewall realization.
- `src/evander/host_config.py`, `src/evander/admin/web/app.py`, `src/evander/worker/cli.py`, and `src/evander/didww_sip_bridge.py` — listener defaults.
- `src/evander/daemons/trigger_orchestrator.py` and `src/evander/daemons/effect_orchestrator.py` — orchestration worker topology.
- `docs/operations.md`, `docs/architecture.md`, and `docs/container-worker-backend.md` — supported deployment and worker layout.
- `tests/test_install_systemd_units_script.py` and `tests/test_firewall_defaults.py` — executable service and port checks.
