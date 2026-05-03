# Container Worker Backend

`agent-sandbox-worker-daemon` is the worker-VM side of the container execution backend. It exposes a small HTTP API for starting, inspecting, stopping, deleting, and reading logs for jobs that run inside disposable Docker containers.

The daemon is intentionally only an execution envelope. It does not store host secrets, publish pull requests, decide policy, or turn platform summaries into agent memory.

## Control-Plane Configuration

`container_worker_vm` is wired for early single-worker control-plane use. Configure the host gateway with the worker URL and the environment variable that contains the bearer token:

```yaml
execution_backend: container_worker_vm
container_worker_base_url: "http://10.0.2.10:11888"
container_worker_token_env: "AGENT_SANDBOX_WORKER_TOKEN"
container_worker_default_image: "agent-sandbox-runner:latest"
container_worker_job_timeout_seconds: 1800
container_worker_guest_openai_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_gateway_url: "http://<control-plane-private-ip>:8000/mcp"
container_worker_guest_nuget_relay_base_url: "http://<control-plane-private-ip>:11666"
container_worker_guest_pypi_index_url: "http://<control-plane-private-ip>:8081/repository/pypi-all/simple"
container_worker_guest_npm_registry_url: "http://<control-plane-private-ip>:8081/repository/npm-all/"
```

For directory-scoped runtime config, put these values on the root `providers` domain. Start the worker daemon on the worker VM with a matching token:

```bash
AGENT_SANDBOX_WORKER_TOKEN=... agent-sandbox-worker-daemon --host 0.0.0.0 --port 11888 --runtime-dir runtime --worker-root /var/lib/agent-worker
```

For a reproducible worker VM deployment, use the dedicated Ansible playbook:

```bash
./scripts/build_agent_sandbox_runner_image.sh
ansible-playbook -i ansible/inventory/prod.yml ansible/worker.yml
```

The build script creates `agent-sandbox-runner:latest` locally and saves
`dist/agent-sandbox-runner.tar`. The worker playbook copies that archive to the worker VM, runs
`docker load`, verifies the image tag exists, installs Docker, deploys the repo package, renders
`config/worker-daemon.env`, installs `agent-sandbox-worker-daemon.service`, creates
`/var/lib/agent-worker`, and verifies the local `/healthz` endpoint. It intentionally does not
install the control-plane services.

Worker host OS packages are provisioned through the control-plane apt-cacher-ng relay on `3142`.
The worker playbook writes `/etc/apt/apt.conf.d/01agent-sandbox-proxy` before its first
`host_prereqs` apt update. Container-session `pip` and `npm` traffic remains separate and resolves
through Nexus on `8081`.

For Azure-style deployments that include `agent_sandbox_hosts`, the worker playbook also prepares
restricted SSH before `repo_checkout` and checks out the deployment repo through a control-plane bare
mirror instead of talking to GitHub directly. Deploy the control plane first so that mirror can serve
the requested worker revision from the control-plane checkout.

If the host Docker bridge cannot reach Ubuntu apt mirrors during the image build, run the same build
with `RUNNER_BUILD_NETWORK=host`. That option only affects the host-side artifact build; worker
runtime containers still use the daemon sandbox profile and must not use Docker `host` networking.

The runner image intentionally contains only stable base tools, the vendored Codex binary, and
Playwright operating-system libraries. .NET, Node, Playwright packages, browser caches, and NuGet
fallback packages are worker-level caches mounted read-only into containers:

- `/opt/agent-sandbox/toolchains/dotnet`
- `/opt/agent-sandbox/toolchains/node-v24.14.0`
- `/opt/agent-sandbox/playwright-runtime`
- `/opt/agent-sandbox/ms-playwright`
- `/opt/agent-sandbox/nuget-cache-ro`

The Ansible role creates the cache directories under `agent_sandbox_worker_toolchain_cache_root`
and wires them into the daemon with repeated `--readonly-mount` flags. Populate those caches through
an operator-controlled path; they are not baked into the image and secrets are not mounted.
The Node cache must be populated before npm workflows are expected to pass; the session profile adds
that mounted Node/npm path to `PATH`.

Container sessions write `/home/agent/.config/pip/pip.conf` and `/home/agent/.npmrc` so `pip` and
`npm` resolve through the control-plane Nexus package cache. The same URLs are exported as
`PIP_INDEX_URL` and `NPM_CONFIG_REGISTRY` from `/home/agent/.agent-sandbox/session.env`, with
`PIP_TRUSTED_HOST` added for plain HTTP Nexus endpoints.

The daemon defaults to the `default` sandbox profile. To select the stricter profile:

```bash
AGENT_SANDBOX_WORKER_TOKEN=... agent-sandbox-worker-daemon --host 0.0.0.0 --port 11888 --runtime-dir runtime --worker-root /var/lib/agent-worker --sandbox-profile strict
```

The host reads the token from `container_worker_token_env` when it calls the worker. Token values and request headers are not written to the execution mapping store.

The host-side `ExecutionSpec` uses first-class fields for container behavior: `command`, `image`, `repo_source`, `clone_branch`, `resource_limits`, `persistence_mode`, and `workspace_mode`. The backend does not read those values from `metadata`; metadata is only sanitized caller context.

## Session Durability

Durable state lives under:

```text
/var/lib/agent-worker/sessions/<session_id>/
```

Each session contains:

- `agent_home/`: mounted into containers at `/home/agent`
- `agent_home/repos/`: in-container repository root at `/home/agent/repos`
- `artifacts/`: mounted into containers at `/artifacts`
- `logs/`: platform-owned job logs
- `metadata.json`: platform bookkeeping for paths and creation time

Containers are disposable. Continuity comes from mounting the same `agent_home/` into each run for a session. The runner image currently sets `CODEX_HOME=/home/agent/.config/agent-sandbox/codex`, so native Codex resume state is persisted under `/var/lib/agent-worker/sessions/<session_id>/agent_home/.config/agent-sandbox/codex/sessions`. The bootstrap also writes `/var/lib/agent-worker/sessions/<session_id>/agent_home/.codex/config.toml` for compatibility, but `.codex/sessions` is not the active session store unless `CODEX_HOME` is changed.

The daemon does not rewrite commands into `codex resume`. Callers must send the exact command they want to execute. The daemon preserves `/home/agent` so Codex state, identity files, and checked-out repos can resume naturally.

Prompt effects invoke Codex with `codex exec --json` and translate the JSONL event stream back into the legacy human-readable job log. The control plane emits `session id: <thread_id>` as soon as Codex reports `thread.started`, then prints completed assistant messages from `item.completed` events. This keeps existing log consumers on the old text shape while using Codex's supported non-interactive thread-id contract.

If a request includes `repo_source`, the daemon clones it under `/home/agent/repos/<repo-name>`. Existing non-empty target directories are rejected unless they are already Git checkouts for the same requested source. Worker Git intake is prefix-gated: deployed workers should be started with one or more `--allowed-git-source-prefix` values, and a missing prefix list means all `repo_source` values are rejected.

The worker API also exposes a session-preparation surface used by portable
`effect:agent:*` workflow nodes:

- `POST /v1/sessions` creates or reconciles durable session directories without starting a job.
- `POST /v1/sessions/{session_id}/repo` clones a repo under `/home/agent/repos/<repo-name>`.
- `POST /v1/sessions/{session_id}/files` writes files under `/home/agent`.

Those endpoints do not accept raw environment variables or secrets. Prompt and exec effects still
use typed container jobs: the host builds an `ExecutionSpec.command`, starts the job, waits through
the relevant completion path, then removes the container while preserving the session directory.

For `container_worker_vm`, `effect:agent:clone-repo` does not send upstream GitHub or Azure DevOps
URLs to the clone API. The control plane resolves and refreshes a deterministic bare mirror under
the configured `git_root`, rewrites the worker clone URL to `ssh://<git_ssh_alias><git_root>/<mirror>.git`,
and fails before contacting the worker if the upstream refresh fails or the requested branch is not
present. The in-container repo can still record the canonical upstream URL with Git `insteadOf`, but
all fetch and push traffic goes through the private restricted-SSH mirror.

`effect:agent:create` on `container_worker_vm` writes the session bootstrap files under
`/home/agent`: `.agent-sandbox/session.env`, shell profiles, Codex config in both supported
locations, and NuGet config when `nuget=true`. The Codex config points at the session-scoped OpenAI
relay URL and MCP gateway URL. If `dotnet=true` or `playwright=true`, the profile points at the
read-only worker cache mounts and keeps writable per-session cache paths under `/home/agent`.

MSSQL is intentionally unsupported for container workers in this phase. Requests that set
`mssql=true` or `mssql_init_root` fail clearly instead of being ignored.

The host-side backend records the control-plane mapping in:

```text
<runtime-dir>/container-worker-executions.json
```

That store maps the host execution id and session id to the worker job id, worker URL, target name, timestamps, last observed status, directory path, and sanitized metadata. Operators can target an execution by execution id, worker job id, target name, or an unambiguous session id.

## Operator CLI

Container worker execution is exposed as an explicit operator-only path:

```bash
agent-sandbox-cli execution start-container-job \
  --session-id demo-session \
  --directory root \
  --target-name demo-container-1 \
  --image agent-sandbox-runner:latest \
  --repo-source git@example.test/repo.git \
  --clone-branch main \
  --memory 512m \
  --cpus 1 \
  --pids-limit 256 \
  --metadata purpose=manual-smoke \
  --command 'echo hello from /home/agent' \
  --json
```

Follow-up operations can target the execution id, worker job id, target name, or an unambiguous session id. They auto-route to the container backend when the target exists in `<runtime-dir>/container-worker-executions.json`; use `--backend container_worker_vm` or `--backend libvirt_vm` to force a backend:

```bash
agent-sandbox-cli execution status demo-container-1
agent-sandbox-cli execution logs demo-container-1 --lines 100
agent-sandbox-cli execution stop demo-container-1 --timeout-seconds 30
agent-sandbox-cli execution destroy demo-container-1
agent-sandbox-cli execution list-container-worker-executions --json
```

`destroy` deletes the worker job/container record. It does not delete durable session state under `/var/lib/agent-worker/sessions/<session_id>/`.

The smoke command runs two unique jobs with the same session id. The first writes markers under `/home/agent` and `/home/agent/repos`; the second reads them back and fails if continuity or logs are missing:

```bash
agent-sandbox-cli execution smoke-container-worker \
  --session-id smoke-session \
  --image agent-sandbox-runner:latest \
  --json
```

The CLI does not expose raw environment injection. Container behavior is sent through first-class `ExecutionSpec` fields, and metadata keys `env`, `environment`, and `environment_variables` are rejected.

## HTTP API

All routes except `GET /healthz` require:

```text
Authorization: Bearer <AGENT_SANDBOX_WORKER_TOKEN>
```

Routes:

- `POST /v1/jobs`
- `GET /v1/jobs/{id}`
- `POST /v1/jobs/{id}/stop`
- `DELETE /v1/jobs/{id}`
- `GET /v1/jobs/{id}/logs`
- `GET /v1/jobs/{id}/artifacts`
- `GET /healthz`

`POST /v1/jobs` accepts `session_id`, optional `job_id`, `image`, `command`, optional `repo_source`, optional `clone_branch`, `resource_limits`, and sanitized `metadata`. Raw `env`, `environment`, and `environment_variables` request fields are rejected. If `repo_source` is present, it must start with a configured allowed Git source prefix.

## Docker Constraints

Docker is used as a local container runtime on the worker VM. The default sandbox profile starts containers with:

- `--rm=false`
- `--cap-drop ALL`
- `--security-opt no-new-privileges`
- default `--network none`
- no privileged mode
- no Docker socket mount
- no host network
- persistent mounts for `/home/agent` and `/artifacts`
- worker-level read-only cache mounts configured with `--readonly-mount`
- working directory `/home/agent`

The Docker network can be overridden with worker config or `--docker-network`, but `host` is refused.
When Codex or package restores need the control-plane gateway, OpenAI relay, NuGet relay,
apt-cacher-ng relay, or Nexus package cache, choose a non-host Docker network and worker VM
routing/firewall rules that allow only those specific private control-plane endpoints. Workers
should not have direct outbound access to Ubuntu mirrors, `pypi.org`, or `registry.npmjs.org`;
apt-cacher-ng is the private apt egress point and Nexus is the private PyPI/npm egress point.

The `strict` sandbox profile keeps the default profile behavior and also adds:

- `--read-only`
- `--tmpfs /tmp:rw,noexec,nosuid,size=512m`
- `--pids-limit 512` when the job did not provide its own pids limit

Operators can tune worker daemon container flags with `--read-only-rootfs`, repeated `--tmpfs`, and `--container-user`. `--docker-network` and `AGENT_SANDBOX_WORKER_DOCKER_NETWORK` override the selected profile network, but `host` remains rejected.

Non-`none` networking requires Phase 5 worker VM firewall and network controls before it can protect cloud metadata or adjacent infrastructure endpoints. Do not treat a Docker network override as a metadata-protection boundary.

## Job Store

The runtime store is `<runtime-dir>/worker-jobs.json`. It records job id, session id, image, command, container name/id, status, timestamps, persistent paths, and sanitized metadata.

The job store must not contain request headers, bearer tokens, host credentials, or full environment variables.

## Security Boundaries

The worker is treated as an untrusted execution surface. The host sends only explicit job fields: session id, stable job id, image, command, optional mirror repo source, optional clone branch, resource limits, and sanitized caller metadata. It does not send raw environment variables, OpenAI keys, Azure DevOps PATs, GitHub tokens, host credentials, or request headers.

Secret-like metadata keys are redacted before the host sends the request and before the host mapping is stored. Raw `env`, `environment`, and `environment_variables` fields remain rejected by the worker request model.

The container sandbox profile reduces accidental and straightforward container privilege exposure. It does not turn containers into a VM-equivalent trust boundary.

The worker and its containers should not have direct GitHub/Azure DevOps egress in this mode.
Required private egress is limited to the control-plane SSH endpoint for Git mirrors and the
configured control-plane gateway, OpenAI relay, HTTP target relay, and package relay endpoints.
npm and pip need their own registry proxy or mirror; they are not handled by the Git mirror path.

## Current Limitations

This phase adds the worker-side daemon, durable session model, explicit worker container sandbox profile, and host control-plane backend for a single configured worker URL. It does not replace the existing libvirt backend, implement scheduling, implement AKS/Kubernetes, autoscale workers, publish PRs, add secret transport, or migrate the full issue/prompt orchestration stack.
