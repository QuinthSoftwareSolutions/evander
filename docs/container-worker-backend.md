# Container Worker Backend

`evander-worker-daemon` is the worker-VM side of the container execution backend. It exposes a small HTTP API for starting, inspecting, stopping, deleting, and reading logs for jobs that run inside disposable Docker containers.

The daemon is intentionally primarily an execution envelope. It does not receive control-plane
API keys, publish pull requests, decide policy, or turn platform summaries into agent memory. When
ChatGPT subscription execution is enabled, the worker additionally owns the one durable Codex
account file used by its auth broker; that file is never mounted into job containers.

## Control-Plane Configuration

`container_worker_vm` is wired for early single-worker control-plane use. Configure the host gateway with the worker URL and the environment variable that contains the bearer token:

```yaml
execution_backend: container_worker_vm
container_worker_base_url: "http://10.0.2.10:11888"
container_worker_token_env: "EVANDER_WORKER_TOKEN"
container_worker_default_image: "evander-runner:latest"
container_worker_codex_auth_mode: "metered_api"
container_worker_images:
  default: "evander-runner:latest"
  claude: "evander-runner-claude:latest"
  dotnet-playwright: "evander-runner-dotnet-playwright:2026-06-05"
container_worker_job_timeout_seconds: 1800
container_worker_guest_openai_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_anthropic_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_gateway_url: "http://<control-plane-private-ip>:8000/mcp"
container_worker_guest_http_target_relay_base_url: "http://<control-plane-private-ip>:11777"
container_worker_guest_nuget_relay_base_url: "http://<control-plane-private-ip>:11666"
container_worker_guest_pypi_index_url: "http://<control-plane-private-ip>:8081/repository/pypi-all/simple"
container_worker_guest_npm_registry_url: "http://<control-plane-private-ip>:8081/repository/npm-all/"
container_worker_mssql_enabled: true
container_worker_mssql_host: "172.17.0.1"
container_worker_mssql_port: 1433
```

`container_worker_default_image` is the fallback when a session does not select an approved
alias. `container_worker_images` maps operator-safe aliases to exact runner image references. The
selector `default` in the catalog above resolves to the default image reference with no alias
recorded, while `claude` and `dotnet-playwright` resolve to additional approved images. Runtime
dispatch does not pull or build images, so approved refs must already be present on the worker VM.
For Anthropic-backed Claude sessions, set `model_provider_mode: "anthropic"` in the effective
provider config and select the `claude` image alias when creating the container-backed session.
`container_worker_codex_auth_mode` sets the inherited Codex usage source. Keep `metered_api` for
the existing OpenAI relay, or select `chatgpt_subscription` after configuring and signing in the
worker account. A session-level selection overrides this default. The subscription selection is
effective only for container Codex turns whose effective `model_provider_mode` is
`openai_public`. A `local_openai` or `azure_openai` turn uses its configured private/API-key route
without rewriting the stored selection, and non-Codex agents do not inherit this Codex default.

The same image catalog is used by request-scoped codebase consultations. A named
`agent_consultation_profiles` entry selects the model, provider mode, image alias, deadline, and
concurrency. Every request receives a new session and workspace; there is no persistent junior
agent to keep synchronized. Managed Git grants clone/fetch-only credentials, the checkout is
detached at the recorded commit, and submitted evidence is validated against that immutable
snapshot before the source agent can read it.

Directory-scoped `providers.json` overlays can inherit the root catalog and override just the
fallback, or replace both together when a subtree needs a different default:

```yaml
# runtime/directory-config/root/providers.json
execution_backend: container_worker_vm
container_worker_default_image: "evander-runner:latest"
container_worker_images:
  default: "evander-runner:latest"
  claude: "evander-runner-claude:latest"
  dotnet-playwright: "evander-runner-dotnet-playwright:2026-06-05"

# runtime/directory-config/root/design/docs/providers.json
container_worker_default_image: "evander-runner-dotnet-playwright:2026-06-05"
container_worker_images:
  default: "evander-runner:latest"
  claude: "evander-runner-claude:latest"
  dotnet-playwright: "evander-runner-dotnet-playwright:2026-06-05"
```

This is the Phase 5 first slice: one configured worker VM. The worker daemon still treats `none`
as the safe default for disposable job containers, and the supported non-`none` path is the single
worker VM deployment with an explicit egress policy. If the worker VM uses a non-`none` Docker
network, the deployment must enable `evander_worker_egress_policy_enabled` and provide an
allowlist rather than relying on Docker networking alone.

For directory-scoped runtime config, put these values on the root `providers` domain. Start the worker daemon on the worker VM with a matching token:

```bash
EVANDER_WORKER_TOKEN=... evander-worker-daemon --host 0.0.0.0 --port 11888 --runtime-dir runtime --worker-root /var/lib/agent-worker
```

For a reproducible worker VM deployment, use the dedicated Ansible playbook:

```bash
./scripts/build_evander_runner_image.sh
./scripts/build_evander_claude_runner_image.sh
# `prod.yml` is operator-local; create it once from the checked-in template.
test -f ansible/inventory/prod.yml || cp ansible/inventory/hosts.example.yml ansible/inventory/prod.yml
${EDITOR:-vi} ansible/inventory/prod.yml
ansible-playbook -i ansible/inventory/prod.yml ansible/worker.yml
```

The Codex build script writes a sidecar manifest at `dist/evander-runner.tar.manifest` next to
`dist/evander-runner.tar`. The Claude build script writes
`dist/evander-runner-claude.tar.manifest` next to
`dist/evander-runner-claude.tar`. If those manifests still match the tarball inputs, rerunning
the scripts reuse the cached archives, print that the cached archive was reused, and skip Docker
build/save entirely. The cache keys include the Dockerfile text, the build script text,
`RUNNER_IMAGE_NAME`, the resolved agent executable path and SHA-256, provider package identity where
applicable, and the Dockerfile base-image policy from the `FROM` line. Use
`RUNNER_FORCE_REBUILD=1 ./scripts/build_evander_runner_image.sh` or
`./scripts/build_evander_runner_image.sh --force` for Codex, and
`RUNNER_FORCE_REBUILD=1 ./scripts/build_evander_claude_runner_image.sh` or
`./scripts/build_evander_claude_runner_image.sh --force` for Claude, to force a rebuild.
Moving tags such as
`ubuntu:24.04` require a force rebuild when you want a base-image refresh, because the Dockerfile
text can stay the same while the upstream image changes.

The worker playbook copies each configured local runner image archive to the worker VM, runs
`docker load`, verifies every configured image tag exists, installs Docker, deploys the repo
package, renders `config/worker-daemon.env`, installs `evander-worker-daemon.service`,
creates `/var/lib/agent-worker`, and verifies the local `/healthz` endpoint. This slice keeps the
existing tar-copy/load deployment model and does not do worker-side remote manifest comparison yet.
That remote skip path is deferred because it needs a separate trusted manifest storage/read path on
the worker, while the host-side cache already avoids the expensive local `docker build`/`docker
save` path when inputs are unchanged. It intentionally does not install the control-plane services.
When worker MSSQL is enabled and `evander_worker_mssql_seed_sidecar_enabled` is true, the same
worker playbook also renders `config/mssql-seed-sidecar.env`, installs
`evander-mssql-seed-sidecar.service`, creates the configured seed backup directory, and verifies the
sidecar health endpoint. The sidecar is disabled by default.

## Cleanup Visibility

Container session cleanup is intentionally non-destructive on the control plane. When an operator
or workflow cleans up a container-backed session, the worker daemon removes worker-side session
files and jobs, but the host keeps the session manifest and container execution mappings as
tombstones for audit and recovery decisions. Operators can inspect the lifecycle in Admin Web >
Sessions; the session list and detail views show whether a container session is `stopped`,
`failed`, `abandoned`, `cleanup_failed`, or `cleaned_up`, plus the last worker observation or
completion signal and any sanitized cleanup error.

Manual cleanup is only offered for states the backend can clean up, such as stopped, failed,
abandoned, or cleanup-failed sessions. A cleanup failure records `cleanup_failed` state and keeps
the original session and execution context visible so the operator can retry cleanup or inspect
logs and metadata before taking manual action.

Worker host OS packages are provisioned through the control-plane apt-cacher-ng relay on `3142`.
The worker playbook writes `/etc/apt/apt.conf.d/01evander-proxy` before its first
`host_prereqs` apt update. Container-session `pip` and `npm` traffic remains separate and resolves
through Nexus on `8081`. The worker host's own `uv pip install -e .` step also uses that Nexus
PyPI group through `UV_DEFAULT_INDEX` and `PIP_INDEX_URL`, so the worker deploy does not need direct
`pypi.org` egress.
The control plane also exposes `evander-npm-audit-facade` on `11881` for npm audit POSTs;
it is separate from the worker registry setting and routes package metadata/tarballs through
Nexus while sending audit requests to public npm.

For Azure-style deployments that include `evander_hosts`, the worker playbook also prepares
restricted SSH before `repo_checkout` and checks out the deployment repo through the root-owned
managed control-plane repository instead of talking to GitHub directly. Deploy the control plane
first so the worker playbook can publish its exact requested revision to the repository's
worker-specific `refs/evander/deploy/` ref. The playbook never recreates the retired
`git-remotes/evander-deploy.git` mirror.

If the host Docker bridge cannot reach Ubuntu apt mirrors during the image build, run the same build
with `RUNNER_BUILD_NETWORK=host`. That option only affects the host-side artifact build; worker
runtime containers still use the daemon sandbox profile and must not use Docker `host` networking.

The runner image intentionally contains only stable base tools, the vendored Codex binary, and
Playwright operating-system libraries. .NET, Node, Playwright packages, browser caches, and NuGet
fallback packages are worker-level caches mounted read-only into containers:

- `/opt/evander/toolchains/dotnet`
- `/opt/evander/toolchains/dotnet-tools`
- `/opt/evander/toolchains/node-v24.14.0`
- `/opt/evander/playwright-runtime`
- `/opt/evander/ms-playwright`
- `/opt/evander/nuget-cache-ro`

The worker playbook creates the cache directories under `evander_worker_toolchain_cache_root`
and wires them into the daemon with repeated `--readonly-mount` flags. It also seeds the .NET,
Node, and Playwright caches from the Ansible control machine and verifies them inside
`evander-runner:latest`.
By default dotnet reads `HOST_DOTNET_ROOT` or `/usr/lib/dotnet`; set
`evander_worker_dotnet_toolchain_archive_path` to copy a prebuilt tarball instead. The worker
role also installs `dotnet-ef` into a separate shared tools cache and verifies
`dotnet ef --version` in the runner container; set `HOST_DOTNET_EF_VERSION` to pin a different
version, or set `HOST_DOTNET_TOOLS_ARCHIVE` /
`evander_worker_dotnet_tools_archive_path` to copy a prebuilt dotnet tool-path tarball.
The same role seeds `Microsoft.Extensions.FileSystemGlobbing` into the worker NuGet fallback cache
and runs an offline `dotnet restore` smoke test inside the runner. Use
`HOST_NUGET_FALLBACK_ARCHIVE` /
`evander_worker_nuget_fallback_archive_path` when deploys must use a prebuilt NuGet
global-packages tarball instead of restoring only that support package from the Ansible control
machine. The older `HOST_DOTNET_RESTORE_SUPPORT_ARCHIVE` /
`evander_worker_dotnet_restore_support_archive_path` names remain accepted for compatibility.
Node reads `HOST_NODE_ROOT` or `~/.nvm/versions/node/v24.14.0`; set
`evander_worker_node_toolchain_archive_path` to copy a prebuilt tarball instead. By default
Playwright reads `HOST_PLAYWRIGHT_NODE_MODULES` or the repo frontend `node_modules` plus
`HOST_PLAYWRIGHT_BROWSERS` or `~/.cache/ms-playwright`; set the Playwright archive variables to copy
prebuilt tarballs instead. Playwright-enabled container sessions export
`PLAYWRIGHT_MCP_EXECUTABLE_PATH` from that mounted browser cache and
`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`, and also set `CHROME_BIN` and `CHROMIUM_BIN` to the same
seeded Chromium executable for Karma-style browser launchers. This lets browser tools use the seeded
binary instead of looking for system Chrome under `/opt/google/chrome` or downloading a browser at
task time.
Project-specific NuGet fallback packages remain operator-controlled and
are not baked into the image. Populate them from the control plane with
`scripts/prewarm_container_worker_nuget_cache.sh --worker <worker-host> --target <solution-or-project>`
so restores use the control-plane NuGet relay and workers receive an additive global-packages
archive. If the restore target and .NET toolchain are on the worker itself, run the script there
with `--local --dotnet <worker-dotnet-path>` and point `NUGET_RELAY_BASE_URL` at the control-plane
relay. Secrets are not mounted. When `--container-user` is numeric, the worker daemon writes a per-session
`/etc/passwd` and `/etc/group` bind mount so tools such as OpenSSH can resolve the active uid
without the runner image hard-coding a host-specific user id.

Container sessions write `/home/agent/.config/pip/pip.conf` and `/home/agent/.npmrc` so `pip` and
`npm` resolve through the control-plane Nexus package cache. The same URLs are exported as
`PIP_INDEX_URL` and `NPM_CONFIG_REGISTRY` from `/home/agent/.evander/session.env`, with
`PIP_TRUSTED_HOST` added for plain HTTP Nexus endpoints.

## Worker Egress Policy

The Docker sandbox profile still starts from `--network none`. Worker deploys can override the
daemon network for the supported single-worker slice, but `host` is refused. Any non-`none`
network on the worker VM must be paired with `evander_worker_egress_policy_enabled: true`
and an explicit allowlist. The worker-managed policy installs rules in `DOCKER-USER`, blocks
`169.254.0.0/16` including `169.254.169.254`, and only then permits named private endpoints.

The default allowlist is intentionally small and private:

- control-plane Git SSH on `22`
- control-plane MCP gateway on `8000`
- control-plane OpenAI relay on `11434`
- control-plane NuGet relay on `11666`
- control-plane HTTP target relay on `11777`
- control-plane npm audit facade on `11881` when npm audit support is enabled
- control-plane Nexus package cache on `8081`
- control-plane apt-cacher-ng on `3142`

Operators can append extra private endpoints with
`evander_worker_egress_policy_extra_allow_tcp_endpoints`, but the policy should not be used
as a general internet egress path.

The daemon defaults to the `default` sandbox profile. To select the stricter profile:

```bash
EVANDER_WORKER_TOKEN=... evander-worker-daemon --host 0.0.0.0 --port 11888 --runtime-dir runtime --worker-root /var/lib/agent-worker --sandbox-profile strict
```

The host reads the token from `container_worker_token_env` when it calls the worker. Token values and request headers are not written to the execution mapping store.

The host-side `ExecutionSpec` uses first-class fields for container behavior: `command`, `image`,
the internally resolved clone source and branch, `resource_limits`, `persistence_mode`, and
`workspace_mode`. The backend does not read those values from `metadata`; metadata is only
sanitized caller context. Public agent workflows provide `managed_repo_id`; the adapter resolves
the private clone source before building this lower-level worker request.

## Security Boundaries

The worker is treated as an untrusted execution surface. The host sends only explicit job fields:
session id, stable job id, image, command, optional managed-repository SSH clone source and branch,
resource limits, and sanitized caller metadata. It does not send raw environment variables, OpenAI
keys, Azure DevOps PATs, GitHub tokens, host credentials, or request headers.

Secret-like metadata keys are redacted before the host sends the request and before the host
mapping is stored. Raw `env`, `environment`, and `environment_variables` fields remain rejected by
the worker request model.

The worker and its containers should not have direct GitHub/Azure DevOps egress in this mode.
Required private egress is limited to the explicitly allowlisted control-plane SSH endpoint for Git
mirrors, gateway, OpenAI relay, HTTP target relay, NuGet relay, npm audit facade on `11881`, Nexus
package cache, and apt-cacher-ng. Worker-host SQL Server access, when enabled, remains separate and
is handled by the worker-host SQL rules rather than by the container-forwarded egress policy.

The container sandbox profile reduces accidental and straightforward container privilege exposure.
It does not turn containers into a VM-equivalent trust boundary.

The worker VM only covers this bounded first slice: one configured worker, one explicit egress
policy, and no assumption that Docker networking by itself forms a boundary.

npm and pip need their own registry proxy or mirror; they are not handled by the managed Git path.

## Operator Runbook

For the Azure/single-worker slice, set the worker network override and enable the egress policy in
inventory:

```yaml
evander_worker_docker_network: "bridge"
evander_worker_egress_policy_enabled: true
```

Then deploy the worker playbook and verify the worker-side policy on the VM:

```bash
ansible-playbook -i ansible/inventory/prod.yml ansible/worker.yml
sudo systemctl status evander-worker-egress-policy.service
sudo iptables -S DOCKER-USER
evander-cli execution smoke-container-worker --json
```

## Codex ChatGPT Subscription Authentication

This optional path lets container-worker Codex sessions use the operator's ChatGPT subscription
instead of the metered OpenAI relay when their effective `model_provider_mode` is
`openai_public`. It does not add another worker profile or assign an account to one agent: all
subscription-mode agents on the worker share the single signed-in operator account. Metered and
subscription agents can run on the same worker.

The worker auth broker is the only owner of
`<worker-root>/codex-auth/auth.json`. It holds an exclusive lock at
`<worker-root>/codex-auth/.broker.lock`, serializes login, logout, account reads, and refreshes, and
coalesces concurrent refresh requests. A second broker for the same directory refuses to start.
The managed auth app-server is the only process allowed to write the file. The utility container
that hosts that app-server mounts only `codex-auth/`; it is not a separate Evander worker and does
not mount any session workspace. It runs as the configured worker container UID/GID so its files
remain owned and readable only by the worker service account. Its deterministic Docker name also
prevents a replacement daemon from starting a second writer if a hard crash leaves the previous
utility container alive; the replacement fails closed. After confirming the old worker daemon is
gone, remove that stale `evander-codex-auth-*` container before restarting authentication.

Each subscription job starts its own Codex app-server inside the disposable runner container and
logs in through Codex's external-token mode. Those credentials remain in that process's memory:
the job receives no `auth.json` mount, environment token, command-line token, or persisted token
metadata. When Codex rejects an access token, the job asks the broker for one serialized refresh
and receives only the new in-memory snapshot. Auth and refresh responses are redacted from worker
logs. This integration uses Codex app-server's experimental external-auth API and should be
revalidated when upgrading Codex.

Set the worker's runner image and restricted proxy settings in inventory. The URL must use an
address that runner containers can reach; do not use loopback unless the containers share that
network namespace:

```yaml
evander_worker_docker_network: "bridge"
evander_worker_egress_policy_enabled: true
evander_worker_codex_auth_image: "evander-runner:latest"
evander_worker_codex_egress_proxy_bind_host: "172.17.0.1"
evander_worker_codex_egress_proxy_port: 11889
evander_worker_codex_egress_proxy_url: "http://172.17.0.1:11889"
```

The job container receives that URL only as `HTTP_PROXY`/`HTTPS_PROXY`. The worker proxy accepts
HTTPS `CONNECT` requests only to its configured OpenAI/ChatGPT hostname allowlist and port 443.
The existing `DOCKER-USER` default-deny policy must permit containers to reach the proxy endpoint
but must not grant them direct public egress. Keep the proxy's allowlist narrow and review it when
Codex's service endpoints change.

After deployment, sign in to Admin Web as an Admin with `admin.owner` permission at `root`, unlock
privileged operations, open **Agents**, and use **Worker ChatGPT subscription** to start device
login. Complete the displayed link and code, then wait for the status to show signed in. Account
status, login start/status, cancellation, and logout all require both checks; ownership of a child
directory is insufficient because this is one worker-global account. Login start, cancellation,
and logout are audited without recording tokens.

Choose **Metered API** or **ChatGPT subscription** when creating a container Codex agent, or edit
an existing agent's policy. The new selection applies to the next turn and preserves that
session's Codex thread. Multiple agents can use the account concurrently; only account-file and
token-refresh operations are serialized.

`chatgpt_subscription` is effective only for a container Codex turn on the public OpenAI route.
If the effective `model_provider_mode` is `local_openai` or `azure_openai`, Evander runs the turn
through that configured private/API-key route and preserves the stored subscription preference;
switching the session back to `openai_public` makes the preference effective again. Other agent
harnesses do not inherit the Codex auth default, and an explicit `chatgpt_subscription` override
on a non-Codex agent is rejected.

That route selection is separate from subscription failure handling. Once a public Codex turn
actually selects `chatgpt_subscription`, missing or expired worker credentials fail that turn. Evander never
falls back from subscription mode to metered usage. Logging out removes the worker account, so
subsequent public subscription turns fail closed until an authorized root owner signs in again.

## Session Durability

Durable state lives under:

```text
/var/lib/agent-worker/sessions/<session_id>/
```

Each session contains:

- `agent_home/`: mounted into containers at `/home/agent`
- `agent_home/repos/`: in-container repository root at `/home/agent/repos`
- `artifacts/`: mounted into containers at `/artifacts`; agents should write
  workflow-readable text artifacts here, preferably by calling the
  `stage_artifact` MCP tool from their workspace
- `logs/`: platform-owned job logs
- `metadata.json`: platform bookkeeping for paths and creation time

Containers are disposable. Continuity comes from mounting the same `agent_home/` into each run for a session. The runner image currently sets `CODEX_HOME=/home/agent/.config/evander/codex`, so native Codex resume state is persisted under `/var/lib/agent-worker/sessions/<session_id>/agent_home/.config/evander/codex/sessions`. The bootstrap also writes `/var/lib/agent-worker/sessions/<session_id>/agent_home/.codex/config.toml` for compatibility, but `.codex/sessions` is not the active session store unless `CODEX_HOME` is changed. Metered turns use the `evander_openai_relay` Codex provider with `supports_websockets = false`, while subscription turns use an external-token app-server and retain thread state in the same session home. Job homes do not contain the worker's subscription `auth.json`.

The daemon does not rewrite commands into `codex resume`. Callers must send the exact command they want to execute. The daemon preserves `/home/agent` so Codex state, identity files, and checked-out repos can resume naturally.

Prompt effects invoke Codex with `codex exec --json` and translate the JSONL event stream back into the legacy human-readable job log. The control plane emits `session id: <thread_id>` as soon as Codex reports either `session_meta` or the older `thread.started` event, then prints assistant messages from the current `event_msg` / `response_item` stream and the older `item.completed` event. This keeps existing log consumers on the old text shape while using Codex's supported non-interactive thread-id contract.

Scheduled wake dispatches and queued prompt records follow the same container prompt construction rules. When a wake request is marked `same_context` and no usable explicit thread id is present, the host emits `codex exec resume --last`; when the request is `new_context`, the host emits a fresh `codex exec` run instead. That keeps container wake behavior aligned with the persisted session home while preserving backwards compatibility for older queued prompt records.

Expired container sessions are not queued or started. Wake dispatch records a
failed session-expired result, and queued prompt replay leaves the queued record
in place until the session is renewed. Git SSH access to host-local remotes
remains available for recovery even while execution is blocked.

If a request includes `repo_source`, the daemon clones it under `/home/agent/repos/<repo-name>`. Existing non-empty target directories are rejected unless they are already Git checkouts for the same requested source. Worker Git intake is prefix-gated: deployed workers should be started with one or more `--allowed-git-source-prefix` values, and a missing prefix list means all `repo_source` values are rejected.

The worker API also exposes a session-preparation surface used by portable
`effect:agent:*` workflow nodes:

- `POST /v1/sessions` creates or reconciles durable session directories without starting a job.
- `POST /v1/sessions/{session_id}/repo` clones a repo under `/home/agent/repos/<repo-name>`.
- `POST /v1/sessions/{session_id}/files` writes files under `/home/agent`.

Those endpoints do not accept raw environment variables or secrets. Prompt and exec effects still
use typed container jobs: the host builds an `ExecutionSpec.command`, starts the job, waits through
the relevant completion path, then removes the container while preserving the session directory.

## Migrating Existing VM Sessions

The VM-to-container migration path is intentionally staged. Stop the effect and trigger
orchestrators before copying active sessions so no agent writes new state while rsync is running:

```bash
sudo systemctl stop evander-effect-orchestrator.service
sudo systemctl stop evander-trigger-orchestrator.service
systemctl is-active evander-effect-orchestrator.service evander-trigger-orchestrator.service
```

Dry-run selection first. With no phase flag, the command only reports which libvirt-backed sessions
would migrate and where their worker state would land:

```bash
evander-cli migrate-vm-sessions-to-container --all
```

Copy VM state into the local worker root without changing manifests:

```bash
evander-cli migrate-vm-sessions-to-container --all --copy-only --skip-logs
```

The copy maps VM `/home/agent` to
`<worker-root>/sessions/<session_id>/agent_home`, VM `/artifacts` to
`<worker-root>/sessions/<session_id>/artifacts`, and optionally VM run logs to
`<worker-root>/sessions/<session_id>/logs/migrated-vm-agent-runs`. It mirrors Codex session files
between the legacy `.codex/sessions` tree and the active container
`.config/evander/codex/sessions` tree so old `.codex` history is not stranded. It also
refreshes container-side session files such as `.evander/session.env`, shell profiles, Codex
config, Claude config, and package-manager config. Any overwritten file is first copied under
`/home/agent/.evander/migration-originals` inside the migrated worker session home.

Verify copied sessions before cutover:

```bash
evander-cli migrate-vm-sessions-to-container --all --verify-only
```

Cutover writes a manifest backup under
`runtime/migration-backups/vm-to-container/<timestamp>-vm-to-container/`, then upserts only the
selected session manifests as `container_worker_vm`. It preserves the original manifest fields,
allowed tools, model, approval settings, and metadata, and adds `metadata.previous_libvirt`,
`metadata.migrated_from_libvirt_at`, and `metadata.migration_backup_id` for audit and rollback:

```bash
evander-cli migrate-vm-sessions-to-container --all --cutover
```

If agents might have written VM state after an earlier copy, run a final delta while the
orchestrators are still stopped:

```bash
evander-cli migrate-vm-sessions-to-container --all --copy-only --final-delta --skip-logs
evander-cli migrate-vm-sessions-to-container --all --cutover
```

Rollback restores the selected original manifests from a cutover backup. It does not delete worker
session directories, so copied state remains available for inspection or a later retry:

```bash
evander-cli migrate-vm-sessions-to-container \
  --rollback runtime/migration-backups/vm-to-container/<backup-id>
```

## Worker Telemetry

`GET /v1/telemetry` requires the same bearer token as the rest of the `/v1/...` API. It returns a
machine-readable worker snapshot with `generated_at`, `disk.worker_root`, `session_storage`, and
`jobs` fields. The storage view reports session-relative labels such as `sessions/<session_id>`
rather than raw worker-root paths, so the payload can be forwarded into the control-plane
capacity-health aggregate without exposing host filesystem layout.

The telemetry payload is intentionally scoped to operational counters:

- `disk.worker_root` includes disk usage plus threshold metadata for the worker root.
- `session_storage.total` summarizes durable session storage under the worker root, and
  `largest_sessions` / `oldest_sessions` surface only `session_id`, `backend`, relative
  `directory_path`, byte size, and timestamps.
- `jobs` includes `total_jobs`, `counts_by_status`, `running_job_count`, `queued_job_count`,
  `queued_job_status`, `failed_start_count`, and `cleanup_failure_status`.

`queued_job_status` currently remains `unsupported` because the worker daemon starts jobs
synchronously and does not persist a worker-side queue. `cleanup_failure_status` also remains
`unsupported` because cleanup-failure history is not persisted separately from the live job records.
The host-side Admin Web capacity-health endpoint uses this telemetry when it can reach a configured
worker; otherwise it reports missing or permission-denied worker telemetry explicitly.
Worker session storage telemetry is cached inside the daemon with stale-while-revalidate behavior:
normal `GET /v1/telemetry` calls return cached or warming storage data quickly, while
`GET /v1/telemetry?refresh=1` performs a blocking deep storage scan for explicit refresh.

For `container_worker_vm`, `effect:agent:clone-repo` requires `managed_repo_id`. The control plane
resolves and refreshes that directory-scoped repository, then sends only its internal
restricted-SSH clone URL to the worker. It does not send the canonical GitHub/Azure DevOps URL,
credentials, or a host path through the workflow result. VM and container sessions reuse the same
managed bare repository; the container path does not create a second cache mirror. Team projects
also persist only the managed ID, base-branch override, and release-branch policy.

Clone jobs default to writable Git access. The control plane asks the worker daemon to generate a
per-session SSH key, installs that public key on the control-plane Git user with a forced command
scoped to the managed repository, `refs/heads/agents/<session-id>/...`, and that session's PR notes
ref, then configures the repo-local `git push-agent` alias. Set `git_access_mode` to `read-only` on
`effect:agent:clone-repo` when a container session should only fetch. Read-only authorization is
still scoped to that managed repository and directory boundary; a worker key must not provide
cross-directory repository access.

`effect:agent:create` on `container_worker_vm` writes the session bootstrap files under
`/home/agent`: `.evander/session.env`, shell profiles, Codex config in both supported
locations, Claude Code settings and MCP config, and NuGet config when `nuget=true`. The Codex
config points at the session-scoped OpenAI relay URL and MCP gateway URL, while the Claude Code
settings disable commit-message attribution trailers. If `dotnet=true` or `playwright=true`, the
profile points at the read-only worker cache mounts and keeps writable per-session cache paths
under `/home/agent`.
With `playwright=true`, the profile resolves the seeded Playwright Chromium binary and exports it as
`PLAYWRIGHT_MCP_EXECUTABLE_PATH`, `CHROME_BIN`, and `CHROMIUM_BIN`.

SQL Server for container workers is a controlled provisioning capability, not an automatic
`effect:agent:create` bootstrap. Requests that set `mssql=true` or `mssql_init_root` still fail
clearly. Sessions that are allowed to use the session-owned SQL tools call
`mssql_provision_database` through MCP.
The gateway checks the session policy and backend config, then calls the worker daemon. The worker
creates or reconciles a SQL login for that session, creates a physical database name derived from
`session_id` and the requested logical name, grants the session login `db_owner` only inside that
database, and returns a scoped connection string for normal app/test commands inside containers.
`mssql_list_databases` and `mssql_drop_database` operate only on databases recorded as owned by the
calling session. `DELETE /v1/sessions/{session_id}` drops all worker-recorded SQL databases and the
session login before removing durable files; SQL cleanup failure blocks session-file deletion.

For `.bak` seed data and rollback-style resets, use the optional
`evander_mssql_seed_addon` rather than extending the worker daemon contract. The
add-on keeps a control-plane catalog of approved seed aliases, calls the Core
worker MSSQL provisioning path to create or reconcile the session-owned database,
and then asks a worker-side `evander-mssql-seed-sidecar` to restore the approved
backup into that database. The sidecar exposes only `/healthz` and
`POST /v1/databases/restore`, requires a bearer token, validates Evander-owned
database/login names, and reattaches the already-created session login after
restore. Agents use `mssql_seed_provision_database` to create a seeded database
and `mssql_seed_reset_database` to restore it back to the approved initial seed.
Enable the worker service with:

```yaml
evander_worker_mssql_enabled: true
evander_worker_mssql_seed_sidecar_enabled: true
evander_worker_mssql_seed_sidecar_token: "{{ vault_evander_worker_mssql_seed_sidecar_token }}"
evander_worker_mssql_seed_sidecar_port: 11990
evander_worker_mssql_seed_sidecar_backup_dir: "/var/lib/evander/mssql-seeds"
```

Point the add-on sidecar catalog at
`http://<worker-private-ip>:{{ evander_worker_mssql_seed_sidecar_port }}` and use
`EVANDER_MSSQL_SEED_SIDECAR_TOKEN` as the token env name. Keep the sidecar port
reachable only from the control plane. The optional `restore_timeout_seconds`
sidecar setting defaults to 3600 seconds so larger `.bak` restores can complete
before the MCP call times out.

The host-side backend records the control-plane mapping in:

```text
<runtime-dir>/container-worker-executions.db
```

That SQLite store maps the host execution id and session id to the worker job id, worker URL,
target name, timestamps, last observed status, directory path, and sanitized metadata. Existing
configuration and code paths still pass `<runtime-dir>/container-worker-executions.json`; on
startup the repository resolves that legacy path to the sibling SQLite database and imports the
JSON file once if the database is empty. The JSON file is left untouched as migration input.
Operators can target an execution by execution id, worker job id, target name, or an unambiguous
session id.

## Operator CLI

Container worker execution is exposed as an explicit operator-only path:

```bash
evander-cli execution start-container-job \
  --session-id demo-session \
  --directory root \
  --target-name demo-container-1 \
  --image evander-runner:latest \
  --memory 512m \
  --cpus 1 \
  --pids-limit 256 \
  --metadata purpose=manual-smoke \
  --command 'echo hello from /home/agent' \
  --json
```

This low-level command starts an execution without a repository. Use
`effect:agent:clone-repo.managed_repo_id` for managed agent code transport; do not pass an upstream
URL directly to a worker as a substitute for registration.

Follow-up operations can target the execution id, worker job id, target name, or an unambiguous
session id. They auto-route to the container backend when the target exists in the container worker
execution mapping database; use `--backend container_worker_vm` or `--backend libvirt_vm` to force
a backend:

```bash
evander-cli execution status demo-container-1
evander-cli execution logs demo-container-1 --lines 100
evander-cli execution stop demo-container-1 --timeout-seconds 30
evander-cli execution destroy demo-container-1
evander-cli execution list-container-worker-executions --json
```

`destroy` deletes the worker job/container record. It does not delete durable session state under `/var/lib/agent-worker/sessions/<session_id>/`.

The smoke command runs two unique jobs with the same session id. The first writes markers under `/home/agent` and `/home/agent/repos`; the second reads them back and fails if continuity or logs are missing:

```bash
evander-cli execution smoke-container-worker \
  --session-id smoke-session \
  --image evander-runner:latest \
  --json
```

The CLI does not expose raw environment injection. Container behavior is sent through first-class `ExecutionSpec` fields, and metadata keys `env`, `environment`, and `environment_variables` are rejected.

## HTTP API

All routes except `GET /healthz` require:

```text
Authorization: Bearer <EVANDER_WORKER_TOKEN>
```

Routes:

- `GET /v1/telemetry`
- `GET /v1/codex-auth`
- `POST /v1/codex-auth/device-login`
- `GET /v1/codex-auth/device-login/{login_id}`
- `DELETE /v1/codex-auth/device-login/{login_id}`
- `DELETE /v1/codex-auth`
- `POST /v1/jobs`
- `GET /v1/jobs/{id}`
- `POST /v1/jobs/{id}/stop`
- `DELETE /v1/jobs/{id}`
- `GET /v1/jobs/{id}/logs`
- `GET /v1/jobs/{id}/artifacts`
- `POST /v1/sessions`
- `POST /v1/sessions/{session_id}/repo`
- `POST /v1/sessions/{session_id}/files`
- `POST /v1/sessions/{session_id}/worktrees`
- `POST /v1/sessions/{session_id}/git/write-key`
- `DELETE /v1/sessions/{session_id}`
- `GET /v1/sessions/{session_id}/artifacts/{artifact_path:path}`
- `POST /v1/sessions/{session_id}/mssql/databases`
- `GET /v1/sessions/{session_id}/mssql/databases`
- `DELETE /v1/sessions/{session_id}/mssql/databases/{logical_name}`
- `GET /healthz`

`POST /v1/jobs` accepts `session_id`, optional `job_id`, `image`, `command`, optional `codex_turn`, optional `repo_source`, optional `clone_branch`, optional `working_directory`, `resource_limits`, and sanitized `metadata`. `codex_turn` is the structured prompt/auth-mode contract used by subscription execution; it never contains credentials. Raw `env`, `environment`, and `environment_variables` request fields are rejected. If `repo_source` is present, it must start with a configured allowed Git source prefix.

This route list is an operational inventory, not a frozen wire schema. The authoritative request and response models are [`src/evander/worker/models.py`](../src/evander/worker/models.py), and route registration is authoritative in [`src/evander/worker/api.py`](../src/evander/worker/api.py). Client implementations should use those sources so route-specific models and newly added fields are not silently omitted.

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

The Docker network can be overridden with worker config or `--docker-network`, but `host` is
refused. In the Ansible-managed single-worker path, any non-`none` worker Docker network must be
paired with `evander_worker_egress_policy_enabled: true`. That policy installs a
default-deny `DOCKER-USER` chain for the configured container network, blocks `169.254.0.0/16`, and
allows only the configured private control-plane endpoints. Workers should not have direct
outbound access to Ubuntu mirrors, `pypi.org`, or `registry.npmjs.org`; apt-cacher-ng is the
private apt egress point and Nexus is the private PyPI/npm egress point.

For single-host local experiments only, the daemon has an explicit escape hatch:
`--unsafe-allow-host-network --docker-network host` or
`EVANDER_WORKER_UNSAFE_ALLOW_HOST_NETWORK=1`. This is intended for an operator-owned
workstation where the worker daemon and control plane run on the same host and Docker bridge
traffic is not usable. It weakens the container isolation boundary and should not be used for
customer or shared deployments.

The `strict` sandbox profile keeps the default profile behavior and also adds:

- `--read-only`
- `--tmpfs /tmp:rw,noexec,nosuid,size=4g`
- `--pids-limit 512` when the job did not provide its own pids limit

Operators can tune worker daemon container flags with `--read-only-rootfs`, repeated `--tmpfs`, and `--container-user`. `--docker-network` and `EVANDER_WORKER_DOCKER_NETWORK` override the selected profile network, but `host` remains rejected.

Non-`none` networking requires the worker egress policy before it can protect cloud metadata or
adjacent infrastructure endpoints. Do not treat a Docker network override as a
metadata-protection boundary.

## Job Store

The active runtime store is `<runtime-dir>/worker-jobs.db`. Worker daemon code still constructs
`WorkerJobStore(runtime_dir / "worker-jobs.json")` for compatibility; the store resolves that
legacy JSON path to the sibling SQLite database and imports an existing JSON file only when the
SQLite table is empty. The legacy JSON file is left untouched after import and remains only a
migration input or troubleshooting artifact.

The SQLite `worker_jobs` table records query columns for job id, session id, container name,
status, cleanup status, timestamps, and a full JSON payload for `WorkerJobRecord` round trips.
Lifecycle updates write one SQLite row instead of rewriting the complete job history.

The job store must not contain request headers, bearer tokens, host credentials, or full environment variables.

## Current Limitations

The current implementation provides the worker-side daemon, durable session model, explicit worker
container sandbox profile, and host control-plane backend for a single configured worker URL. It
does not replace the existing libvirt backend, implement scheduling, implement AKS/Kubernetes,
autoscale workers, publish PRs, add secret transport, or migrate the full issue/prompt
orchestration stack.
