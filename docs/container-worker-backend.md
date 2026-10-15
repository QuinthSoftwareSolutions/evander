# Container Worker Backend

`evander-worker-daemon` is the worker-VM side of the container execution backend. It exposes a small HTTP API for starting, inspecting, stopping, deleting, and reading logs for jobs that run inside disposable Docker containers.

The daemon is intentionally primarily an execution envelope. It does not receive control-plane
API keys, publish pull requests, decide policy, or turn platform summaries into agent memory. When
ChatGPT subscription execution is enabled, the worker additionally owns a registry of named Codex
authentication profiles and their durable credential homes; those files are never mounted into job
containers.

Start with [control-plane configuration](#control-plane-configuration) and the
[operator runbook](#operator-runbook). For existing deployments, read
[changing the default worker](#changing-the-default-worker) before changing placement and
[session durability](#session-durability) before cleanup.

Read-only toolchain mount sources are pinned per session. The .NET provisioner validates
immutable bundles before atomically selecting one for future sessions. See
[release recovery and toolchain rollout](release-recovery-and-verifier-policy.md#immutable-worker-sdk-rollout)
for migration, drain, retention, and rollback precautions.

## Control-Plane Configuration

Workers advertise `git_recovery: 1` and `git_salvage: 1` in `/healthz` capabilities.
Recovery preparation uses an explicit protocol requirement; incompatible workers are
rejected without dropping preparation identity. The authenticated
`POST /v1/sessions/{session_id}/worktrees/salvage` endpoint publishes immutable snapshots
through scoped Git transport while retaining original files. See
[autonomous Git recovery](autonomous-git-recovery.md) for policy, limits, and rollout.

`container_worker_vm` selects a default worker for new sessions. Existing sessions retain
their assigned worker when that default changes. Configure the host gateway with the worker
URL and the environment variable that contains the bearer token through the directory's
`providers` domain. Examples in YAML show the field names; persisted `providers.json` overlays
use JSON, and legacy gateway YAML is not accepted.

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
container_worker_active_tool_timeout_seconds: 300
container_worker_job_timeout_seconds: 1800
container_worker_guest_openai_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_anthropic_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_gateway_url: "http://<control-plane-private-ip>:8000/mcp"
container_worker_guest_http_target_relay_base_url: "http://<control-plane-private-ip>:11777"
container_worker_guest_http_target_browser_proxy_url: "http://<control-plane-private-ip>:11778"
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
For Codex through OpenRouter, use `model_provider_mode: "openrouter"`, an explicit approved
`namespace/model` session model, and the host-owned `openrouter_model_routes` mapping.
Workers still use the existing host relay; no OpenRouter key or internet access is added to
containers. See [OpenRouter setup and limitations](openrouter.md).

`container_worker_active_tool_timeout_seconds` is a narrower safeguard than the job deadline.
When the effect reconciler sees an active Codex tool call that has exceeded this budget, it asks
the worker to send `SIGTERM` only to a recognized test process group. It does not stop
the agent container, accepts no caller-supplied command or PID, and records the attempted
interruption in execution metadata. The Admin Web execution list exposes the active tool and an
operator control for the same safe interruption. Keep `container_worker_job_timeout_seconds` as
the final, whole-job deadline if the agent cannot recover.
`container_worker_codex_auth_mode` remains the inherited Codex usage source for compatibility.
Keep `metered_api` for the existing OpenAI relay, or use `chatgpt_subscription` with an explicitly
selected session profile. The directory-selected `container_worker_base_url` determines the
worker for new sessions; existing sessions use their saved worker assignment. Directory
configuration does not select, default, grant, or restrict subscription profiles.
The subscription selection is effective only for container Codex turns whose effective
`model_provider_mode` is `openai_public`. A `local_openai` or `azure_openai` turn uses its
configured private/API-key route without rewriting stored session state, and non-Codex agents do
not inherit this Codex default.
OpenRouter always uses metered API routing; an explicit session `chatgpt_subscription`
override is rejected. An inherited subscription default is not used for OpenRouter turns.

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

Each worker VM is deployed independently. The worker daemon still treats `none`
as the safe default for disposable job containers, and the supported non-`none` path is a
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

The worker playbook compares the running daemon's startup revision with the deployed Git
checkout. It restarts the daemon when they differ, including after code-only upgrades or an
interrupted deployment that already wrote the new environment file. Repeating a completed
deployment leaves a matching daemon running. Verification requires `/healthz` to report the
deployed revision and the Codex Fast and Ultrafast mode capabilities, so an older process returning HTTP 200
cannot make the deployment pass. Deploy workers before enabling new control-plane turn fields.

Both build scripts first reconcile `evander-runner-base:latest`, which owns their common Ubuntu and
Playwright operating-system dependencies. Sequential Codex and Claude archive builds therefore run
the expensive apt install once and reuse the resulting local Docker layer for the second image. If
`ubuntu:24.04` is absent, its pull uses three attempts with a 120-second timeout per attempt, and the
shared-base build has a 900-second timeout. Override those bounds with
`RUNNER_BASE_PULL_ATTEMPTS`, `RUNNER_BASE_PULL_TIMEOUT_SECONDS`, and
`RUNNER_BASE_BUILD_TIMEOUT_SECONDS`. Set `RUNNER_OS_BASE_IMAGE` to an authenticated registry mirror
or cache when the build host should avoid direct Docker Hub pulls.

The Codex build copies both `codex` and its matching `codex-code-mode-host` executable from the
resolved platform package. The build fails when either executable is missing so a code-mode-only
model cannot deploy without its MCP tool host. The worker playbook repeats that check inside the
loaded image before restarting the daemon. The script writes a sidecar manifest at
`dist/evander-runner.tar.manifest` next to
`dist/evander-runner.tar`. The Claude build script writes
`dist/evander-runner-claude.tar.manifest` next to
`dist/evander-runner-claude.tar`. If those manifests still match the tarball inputs, rerunning
the scripts reuse the cached archives, print that the cached archive was reused, and skip Docker
build/save entirely. The cache keys include the provider and shared-base Dockerfile and build-script
text, `RUNNER_IMAGE_NAME`, `RUNNER_BASE_IMAGE`, `RUNNER_OS_BASE_IMAGE`, the resolved agent executable
paths and SHA-256 values, and provider package identity where applicable. Hosted agents only get
this archive reuse when the operator pipeline restores `dist/` from a persistent cache. Use
`RUNNER_FORCE_REBUILD=1 ./scripts/build_evander_runner_image.sh` or
`./scripts/build_evander_runner_image.sh --force` for Codex, and
`RUNNER_FORCE_REBUILD=1 ./scripts/build_evander_claude_runner_image.sh` or
`./scripts/build_evander_claude_runner_image.sh --force` for Claude, to force a rebuild and refresh
the upstream operating-system image.

Container session bootstrap marks the Evander MCP server as required, and prompt commands repeat
that requirement alongside their session-scoped gateway URL. Codex therefore waits for tool
discovery before starting the model turn instead of allowing a fast model response to race optional
MCP startup. Subscription-mode overlays apply the same requirement.

### Optional Godot runner images

Three additive images provide progressively broader Godot capabilities without enlarging the
default runner:

- `evander-runner-godot:4.7.1` adds the Godot 4.7.1 Linux editor/runtime and matching export
  templates for GDScript projects, headless imports, tests, and exports.
- `evander-runner-godot-native:4.7.1` extends that image with C/C++ compilation, Clang, CMake,
  Ninja, pkg-config, and SCons for GDExtension and engine/module source work.
- `evander-runner-godot-render:4.7.1` extends the standard Godot image with Xvfb, Mesa
  software-rendering libraries, and `godot-render` for tests that need a real display and rendered
  pixels rather than Godot's dummy headless renderer.

The builder is host-seeded: provide the official Linux x86_64 Godot executable and its matching
export-template directory or `.tpz` archive, then run:

```bash
HOST_GODOT_BINARY_PATH=/path/to/Godot_v4.7.1-stable_linux.x86_64 \
HOST_GODOT_EXPORT_TEMPLATES_PATH=/path/to/Godot_v4.7.1-stable_export_templates.tpz \
./scripts/build_evander_godot_runner_images.sh
```

Use `--variant godot`, `--variant godot-native`, or `--variant godot-render` to build one
capability family. The native and render variants always rebuild or reuse the standard Godot
parent locally. The standard image's `godot` wrapper links the read-only templates into the
session's persistent home on first use. The render image adds a `godot-render` wrapper that starts
Xvfb and defaults to the X11 display driver, dummy audio, Mesa's `llvmpipe`, and Godot's
`gl_compatibility` renderer.

These images are manual opt-ins. The script creates local Docker images only and does not alter
`container_worker_images`, `evander_runner_image_archives`, or
`evander_worker_preload_images`. It also does not create archives by default. Therefore the
checked-in Ansible deployment and its documented Azure pipeline command list do not build, copy,
load, pre-pull, or select any Godot image. An operator who later chooses to deploy them must take
both explicit steps:

1. Set `SAVE_GODOT_RUNNER_ARCHIVES=1` and arrange manual transfer or add an operator-owned archive
   entry.
2. Add only the approved aliases to the operator-owned `container_worker_images` configuration.

For example, a later opt-in catalog would map the short names discussed here to their exact local
tags:

```yaml
container_worker_images:
  godot: "evander-runner-godot:4.7.1"
  godot-native: "evander-runner-godot-native:4.7.1"
  godot-render: "evander-runner-godot-render:4.7.1"
```

That example is documentation only; none of those entries is present in the checked-in defaults.
Optional tarballs are written under ignored `dist/optional/` and are not consumed by the default
worker playbook. Operator-owned pipeline overlays live outside this product repository and must
preserve the same exclusion if automatic Godot deployment is not wanted.

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

Worker MSSQL also enables a host-seeded client cache by default. The worker playbook packages
`sqlcmd`, ODBC Driver 18, and their required UnixODBC libraries on the control machine, mounts the
result read-only at `/opt/evander/toolchains/mssql-client`, and verifies `sqlcmd -?` inside the
runner image. This adds only a client to disposable containers; the SQL Server engine and database
files remain on the worker VM. Database connectivity still requires a session-scoped login returned
by the existing MSSQL provisioning tools.

## Changing the Default Worker

A session's worker assignment is saved on the control plane before its first worker
operation. It retains the worker URL, bearer-token environment-variable name, Git source
restriction, image configuration, relay addresses (including their fallback gateway settings),
workspace/authentication defaults, and database endpoint. Secret values are resolved on the
host at request time. Model policy, tool permissions, resource limits, and job deadlines
continue to resolve normally.

Changing `container_worker_base_url` and related connection settings affects new sessions.
Existing sessions, resumed turns, and create retries remain on their original worker. Job
inspection, logs, Git, files, database provisioning, and cleanup use the saved assignment too.
An unavailable assigned worker produces an error/retry on that worker; there is no automatic
fallback or workspace migration.

Admin directory configuration saves pin existing container sessions in the affected directory
and descendants before changing worker settings. If an older session names an unknown worker
whose connection settings cannot be recovered, the save fails without changing the default.

For upgrades or configuration managed outside Admin, preview and apply the backfill while
the original worker settings are still configured, before changing any defaults:

```bash
evander-cli execution pin-worker-assignments --runtime-dir runtime
evander-cli execution pin-worker-assignments --runtime-dir runtime --apply
```

Use `--directory-path root/example` to limit either command to that subtree. The command
includes sessions found only in execution records, such as legacy `start-container-job` jobs,
and validates all selected sessions before writing assignments. It can recover worker identity
from execution records or older session metadata; agents with neither are pinned to their
current effective directory worker during this pre-cutover step. It does not contact workers,
move workspaces, or restart jobs. Deploy this version to every control-plane process before
switching defaults, so older processes cannot bypass assignment handling.

Keep the original token environment variable available to every control-plane service that
uses the worker. Use a distinct environment-variable name for the new worker if it has a
different token. Rotate secret values normally; changing the default token variable does
not update existing assignments. Assigned image and connection settings likewise remain
captured for existing agents. Moving an existing agent or replacing its saved connection
settings requires a separate migration; changing session `worker_url` metadata is insufficient.
Use a new session ID for an agent that should use the new worker.

Include `runtime/container-worker-assignments.db` in control-plane backups. Assignments are
retained after cleanup alongside execution tombstones, so late status or cleanup requests
cannot be redirected by a later default change. Each worker keeps its own session storage.

## Selecting a Worker When Creating an Agent

Configure named choices in the directory's `providers` configuration. Existing
`container_worker_*` settings remain the directory default. For example:

```json
{
  "container_worker_base_url": "http://worker-one.internal:11888",
  "container_worker_token_env": "WORKER_ONE_TOKEN",
  "container_workers": {
    "fast": {
      "display_name": "Fast worker",
      "base_url": "http://worker-two.internal:11888",
      "token_env": "WORKER_TWO_TOKEN",
      "guest_gateway_url": "http://control-plane.internal:8000/mcp",
      "guest_openai_base_url": "http://control-plane.internal:11434/v1",
      "git_allowed_from": "10.50.0.2",
      "default_image": "evander-runner:latest"
    }
  }
}
```

Every entry requires `base_url` and `token_env`; only the name of a host environment
variable is stored. `display_name` is optional. Connection overrides use the matching
`container_worker_*` setting name without that prefix: `images`,
`workspace_persistence`, `codex_auth_mode`, `guest_*` relay/package URLs,
`git_allowed_from`, and `mssql_enabled`, `mssql_host`, `mssql_port`. Omitted settings
inherit the effective directory defaults; configure all endpoints needed from the
new worker's network. Child directories inherit the worker catalog and can add or
replace named entries, with a complete address and token reference for each entry.

Admin Web and Team's shared creation dialog offer **Worker → Directory default**
or a configured worker. Changing the selection reloads that worker's image and
subscription-profile choices. The manual create API and asynchronous create-operation
API accept `"worker_id": "fast"`. Workflow `effect:agent:create` accepts the same
field as a literal selector or runtime binding:

```json
{
  "id": "create-fast-agent",
  "display_name": "Create agent on fast worker",
  "type": "effect:agent:create",
  "execution_backend": "container_worker_vm",
  "session_id": "new-agent",
  "worker_id": "fast"
}
```

For a manually started container job, use
`evander-cli execution start-container-job --directory-path root --worker-id fast --session-id new-job --command 'echo hello'`.
The CLI resolves and pins the selected worker before checking its token or starting work.

Omitting `worker_id` uses the directory default for a new session. Unknown IDs and
worker overrides on `libvirt_vm` are rejected. Existing sessions always retain their
saved assignment: omitting the override or repeating the original ID preserves it,
even if its catalog entry was later edited or removed. A different ID is rejected,
including on workflow upserts and no-op retries. Legacy sessions without a named
assignment must keep their existing worker; create a new session to choose a worker.

The catalog is placement configuration, not worker provisioning or load balancing.
Each worker needs its own daemon, state, authentication, and network isolation. To
coexist with another control plane's worker on the same physical host, use a separate
worker VM with separate storage and network addresses. Keep both control planes'
worker credentials and session stores separate.

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
worker-specific `refs/evander/deploy/` ref. The checkout explicitly fetches that private ref before
checking out the requested commit SHA. The playbook never recreates the retired
`git-remotes/evander-deploy.git` mirror.

If the host Docker bridge cannot reach Ubuntu apt mirrors during the shared-base build, run the same
build with `RUNNER_BUILD_NETWORK=host`. That option only affects the host-side artifact build;
worker runtime containers still use the daemon sandbox profile and must not use Docker `host`
networking.

The runner image intentionally contains only stable base tools, the vendored Codex binary and
matching code-mode host, and Playwright operating-system libraries. .NET, Node, Playwright
packages, browser caches, and NuGet
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
Before replacing the live Playwright mounts, the playbook unpacks the proposed package and browser
archives into an isolated staging cache and resolves `chromium.executablePath()` with downloads
disabled. A missing or mismatched build fails provisioning while the existing live cache remains
intact; the error reports both the expected executable and the Chromium build directories that were
actually seeded.
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
relay. For private packages, pass the same patterns configured in the target directory with
repeatable `--private-package-pattern` options. Secrets are not mounted. When `--container-user` is numeric, the worker daemon writes a per-session
`/etc/passwd` and `/etc/group` bind mount so tools such as OpenSSH can resolve the active uid
without the runner image hard-coding a host-specific user id.

Container sessions write `/home/agent/.config/pip/pip.conf` and `/home/agent/.npmrc` so `pip` and
`npm` resolve through the control-plane Nexus package cache. The same URLs are exported as
`PIP_INDEX_URL` and `NPM_CONFIG_REGISTRY` from `/home/agent/.evander/session.env`, with
`PIP_TRUSTED_HOST` added for plain HTTP Nexus endpoints.

## Worker Egress Policy

The Docker sandbox profile starts from `--network none`. Ansible worker deployments can override
the daemon network, but do not allow `host`. Any non-`none`
network on the worker VM must be paired with `evander_worker_egress_policy_enabled: true`
and an explicit allowlist. The worker-managed policy installs rules in `DOCKER-USER`, blocks
`169.254.0.0/16` including `169.254.169.254`, and only then permits named private endpoints.

The default allowlist is intentionally small and private:

- control-plane Git SSH on `22`
- control-plane MCP gateway on `8000`
- control-plane OpenAI relay on `11434`
- control-plane Codex subscription egress proxy on `11889`
- control-plane NuGet relay on `11666`
- control-plane HTTP target relay on `11777`
- control-plane HTTP target browser proxy on `11778`
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

When a job has a process/thread limit, the worker watches that job's own Linux cgroup
`pids.events` counter while the container is alive. A positive kernel `max` event is persisted
before container cleanup and returned as the structured failure cause
`pids_limit_exhausted`; Docker's `OOMKilled` state is returned as
`memory_limit_exhausted`. Unsupported cgroup layouts fail safely by omitting the cause, and
callers must not infer resource exhaustion from command output text.

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

For an Ansible-managed worker, set the worker network override and enable the egress policy in
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
`openai_public`. Each agent session selects a named profile on its effective worker. Different
profiles may run concurrently; multiple sessions and workflows may also share one profile.

The registry at `<worker-root>/codex-auth/profiles.json` stores only immutable `profile_id`,
editable display name, enabled state, pinned account ID, and timestamps. It never stores access or
refresh tokens. Profile IDs are validated lowercase slugs and cannot be renamed or deleted in V1.
The reserved profile `default` preserves old profile-less sessions and continues to use the legacy
`<worker-root>/codex-auth/auth.json` home without moving credentials. Named profiles use
`<worker-root>/codex-auth/profiles/<profile-id>/auth.json`.

Each profile lazily owns a separate auth broker, exclusive file lock, and managed App Server
process. Broker operations serialize within one profile, while independent profiles can read and
refresh concurrently. When the App Server runs in a utility container, its deterministic name and
mount are unique to the profile. It mounts only that profile's auth home, never a session
workspace, and runs as the configured worker container UID/GID. All profile brokers share the same
restricted OpenAI egress proxy. After confirming an old worker daemon is gone, remove only the
specific stale `evander-codex-auth-*` utility container before retrying that profile.
The compatibility broker lock remains at `<worker-root>/codex-auth/.broker.lock`; named-profile
locks live at `<worker-root>/codex-auth/profiles/<profile-id>/.broker.lock`.

The utility container uses a writable 16 MiB tmpfs at `/home/agent` for `HOME` and mounts the
durable profile at `/codex-auth` for `CODEX_HOME`. This lets Codex initialize its home when the
configured UID/GID differs from the image's `agent` user. If device login fails with a home-directory
permission error after an upgrade, verify the worker is running the current generated auth
command and that its configured user can write the profile directory. Do not mount a session
home or broaden access to other profiles as a workaround.

The first successful device login pins the OpenAI account ID in the registry. A later login must
resolve to the same account; a mismatch is logged out and rejected. Use a new profile ID for a
different account. Broker status uses App Server `account/read`, `account/rateLimits/read`, and
`account/usage/read` and returns only safe identity, plan, limit/reset, and summarized usage fields.
Tokens are never returned, and reset credits are reported but never consumed automatically.

Workflows can read this telemetry with `query:codex:subscription-quota`. The
node defaults to the seven-day Codex window and returns a remaining percentage,
reset time, and observation timestamp, or an explicit unavailable result.
It uses the directory's worker credentials on the host and does not wake queued
jobs. See the [query configuration reference](configuration-reference.md#query-definition-schema-keys)
for selectors, output bindings, and unavailable-result behavior.

Each subscription job starts its own Codex app-server inside the disposable runner container and
logs in through Codex's external-token mode. Those credentials remain in that process's memory:
the job receives no `auth.json` mount, environment token, command-line token, or persisted token
metadata. When Codex rejects an access token, the job asks the broker for one serialized refresh
and receives only the new in-memory snapshot. Auth and refresh responses are redacted from worker
logs. This integration uses Codex app-server's experimental external-auth API and should be
revalidated when upgrading Codex.

Startup emits `codex_subscription_startup` timing records and stores a
`codex_subscription_startup_ms` map in job metadata. Its `process_start`, `account_login`,
`thread_open` and `turn_start` durations isolate app-server initialization, authentication,
thread creation/resumption and turn submission. Completed or failed phases retain their timings.
These durations exclude container creation and do not measure time to the first model response.
Turn preflight checks authentication and rate limits without fetching display-only account-usage
totals; `worker_subscription_preflight` logs its duration separately, before job creation.
Operator status requests continue to include usage telemetry.

Subscription containers retain the session's normal `CODEX_HOME` thread and skill directories,
but the worker overlays its metered relay `config.toml` and legacy `/home/agent/.codex/config.toml`
with worker-owned read-only subscription configuration. The overlay forces ChatGPT authentication,
keeps the image-managed update policy, and contains no custom provider or API-key reference. This
prevents the app-server model catalog from polling the metered relay and prevents the legacy copy
from being interpreted as project-local provider configuration. Repository-owned `.codex` settings
remain visible. The runner image also installs the system `bubblewrap` package so Codex does not
need to warn and fall back to its bundled helper.

Deploy the restricted proxy on the control plane and point the worker at its private address. The
default Ansible inventory derives the worker URL from `evander_git_host` whenever the worker uses
a networked Docker mode; an explicit override remains available. The listener must not be exposed
to public networks:

```yaml
# Control-plane inventory.
evander_services:
  codex_egress_proxy: true
evander_codex_egress_proxy_bind_host: "<control-plane-private-ip>"
evander_codex_egress_proxy_allowed_client_cidrs:
  - "<worker-private-ip>/32"

# Worker inventory.
evander_worker_docker_network: "bridge"
evander_worker_egress_policy_enabled: true
evander_worker_private_ip: "<worker-private-ip>"  # Required when ansible_host is DNS.
evander_worker_codex_auth_image: "evander-runner:latest"
evander_worker_codex_egress_proxy_url: "http://<control-plane-private-ip>:11889"
evander_worker_codex_egress_proxy_bind_host: ""
```

The job container receives that URL only as `HTTP_PROXY`/`HTTPS_PROXY`. The worker proxy accepts
HTTPS `CONNECT` requests only from configured worker CIDRs and only to its configured
OpenAI/ChatGPT hostname allowlist on port 443. Azure and equivalent cloud network policy must
allow private TCP `11889` from the worker subnet to the control plane.
The existing `DOCKER-USER` default-deny policy must permit containers to reach the proxy endpoint
but must not grant them direct public egress. Keep the proxy's allowlist narrow and review it when
Codex's service endpoints change. The daemon's older in-process worker-local proxy remains
available as an explicit override for worker hosts that themselves have deliberately restricted
public egress, but it is not the networked deployment default.

After deployment, sign in to Admin Web as an Admin with `admin.owner` permission at `root`, unlock
privileged operations, and open **Subscriptions**. Create a profile, start device login, complete
the displayed link and code, and wait for the profile to show signed in with its pinned account.
Creation, rename, enable/disable, login, cancellation, logout, and reset redemption are privileged
worker operations; ownership of a child directory is insufficient. Mutations are audited without
recording tokens.

The profile list shows **7-day remaining** and **Banked resets**. Reset counts come from the
provider's authoritative available count; **Unavailable** means the provider did not return a
valid count, rather than zero. Expand an enabled, signed-in profile and choose **Redeem one
reset** to consume one available earned reset after confirmation. Evander rereads limits and
reset counts after redemption. **Retry reset redemption** checks the same attempt after a
transport error, retaining its idempotency key so a completed redemption cannot consume twice.
Unresolved attempts are saved in the browser before submission, scoped by worker, profile, and
account. They survive tab changes and reloads, and are removed only after a definitive response.
If the browser cannot retain retry information, Evander does not submit a new redemption.
Requests retain the original worker and account; a directory worker change rejects the request
before forwarding rather than applying it to the replacement worker.
If there is no eligible window or no credit, the page reports that outcome without claiming a reset.

Choose **Metered API** or **ChatGPT subscription** when creating a container Codex agent. New UI
subscription agents must select one enabled profile. An existing session can switch between
metered API usage and subscription usage, or select another enabled subscription profile, without
being replaced. The new selection applies to the next turn; a turn already running keeps the route
with which it started. An existing-agent upsert that omits the profile preserves its current
subscription selection. Changing the route also releases that session's prompts from any delay
caused by the previous subscription profile so they can be reconsidered immediately.

`chatgpt_subscription` is effective only for a container Codex turn on the public OpenAI route.
If the effective `model_provider_mode` is `local_openai` or `azure_openai`, Evander runs the turn
through that configured private/API-key route and preserves the stored subscription preference;
switching the session back to `openai_public` makes the preference effective again. Other agent
harnesses do not inherit the Codex auth default, and an explicit `chatgpt_subscription` override
on a non-Codex agent is rejected.

That route selection is separate from subscription capacity handling. Before creating a job, the
worker checks the selected broker. Signed-out, disabled, unavailable, and rate-limited profiles
return a structured deferral with profile ID, reason, and reset-aware retry time. The control plane
persists the original prompt without consuming the ordinary retry ledger. If App Server reports
rate-limit exhaustion during a turn, the terminal execution enters the same durable replay path.

Subscription queues have no automatic expiry. They preserve FIFO ordering within one worker
profile while other profiles dispatch concurrently. A reported reset timestamp is authoritative;
without one, backoff grows exponentially to a 15-minute cap. Profile login, re-enable, and manual
refresh and successful reset redemption wake reconciliation, and ordinary effect-orchestrator polling is the fallback. Admin Web
shows the profile, queue reason, and retry time on agent and workflow state and permits an
authorized manual cancellation. Evander never
falls back from subscription mode to metered usage.

V1 supports ChatGPT device login only. Business and Enterprise access tokens are not accepted as
profile credentials in this release, even though OpenAI supports them for trusted automation. See
the [OpenAI access-token documentation](https://learn.chatgpt.com/docs/enterprise/access-tokens)
and the [OpenAI App Server documentation](https://learn.chatgpt.com/docs/app-server).

The control plane mirrors profile list/create/read/update, device-login, logout, and reset redemption operations
under `/api/admin/codex-subscriptions`. The older `/api/admin/codex-auth` surface remains a
compatibility alias for `default`.

Reset redemption uses `POST /api/admin/codex-subscriptions/{profile_id}/reset-credits/consume`,
forwarded to the selected worker's `/v1/codex-auth/profiles/{profile_id}/reset-credits/consume`.
Send a non-empty `idempotency_key` (a UUID is recommended), `expected_worker_url` from the profile
list, and `expected_account_id` from the selected profile. Optionally send a non-empty `credit_id`
from the reported reset details. Reuse the same key and destination when retrying the same attempt.
The control plane compares the expected worker to the current directory configuration and returns
HTTP 409 without forwarding if it changed. The worker checks the expected account before consuming
a reset. The expected worker URL is a comparison value, never an operator-supplied routing override.
The response contains `outcome` (`reset`, `alreadyRedeemed`, `nothingToReset`, or `noCredit`)
and the refreshed `profile`. Provider selection applies when `credit_id` is omitted. This requires
a worker Codex App Server version supporting `account/rateLimitResetCredit/consume`.

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

Codex `fast_mode` session defaults and per-prompt overrides apply to both metered
CLI turns and subscription app-server turns on public OpenAI. Every turn sets
Fast or standard explicitly, including resumes; queue replay, context retries,
and steering retain the captured setting. `fast_mode: false` overrides a Fast
session default. Upgrade worker daemons before sending these new structured turn
fields from the control plane. See the
[Fast mode reference](configuration-reference.md#agent-fast-mode).

Scheduled wake dispatches and queued prompt records follow the same container prompt construction rules. When a wake request is marked `same_context` and no usable explicit thread id is present, the host emits `codex exec resume --last`; when the request is `new_context`, the host emits a fresh `codex exec` run instead. That keeps container wake behavior aligned with the persisted session home while preserving backwards compatibility for older queued prompt records.

Expired container sessions are not queued or started. Wake dispatch records a
failed session-expired result, and queued prompt replay leaves the queued record
in place until the session is renewed. Git SSH access to host-local remotes
remains available for recovery even while execution is blocked.

If a request includes `repo_source`, the daemon clones it under
`/home/agent/repos/<repo-name>`. Existing non-empty target directories are rejected unless they
are already Git checkouts for the same requested source. Worker Git intake is prefix-gated:
deployed workers should be started with one or more `--allowed-git-source-prefix` values, and a
missing prefix list means all `repo_source` values are rejected. SSH sources are parsed before
matching. A managed repository may use the repository-specific
`<configured-host>-<grant-slot>` alias only when that slot matches the `managed_repo_id` in the
repository filename and the path remains under the configured prefix.

The worker API also exposes a session-preparation surface used by portable
`effect:agent:*` workflow nodes:

- `POST /v1/sessions` creates or reconciles durable session directories without starting a job.
- `POST /v1/sessions/{session_id}/repo` clones a repo under `/home/agent/repos/<repo-name>`.
- `POST /v1/sessions/{session_id}/files` writes files under `/home/agent`.
- `DELETE /v1/sessions/{session_id}/git/keys/{key_name}` removes one repository key and its exact
  SSH config block without changing other session Git grants.

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
per-session, per-repository SSH key, installs that public key on the control-plane Git user with a forced command
scoped to the managed repository, `refs/evander/agents/<session-id>/...`, and that session's PR notes
ref, then configures the repo-local `git push-agent` alias. Set `git_access_mode` to `read-only` on
`effect:agent:clone-repo` when a container session should only fetch. Read-only authorization is
still scoped to that managed repository and directory boundary; a worker key must not provide
cross-directory repository access.

Session-lifetime read-only credentials remain in the persistent agent home after the initial clone,
so the checkout can fetch and pull later. One session can hold several repository grants because
key files and SSH config blocks use repository-specific names. `git_access_lifetime: operation`
removes only the affected key, config block, host authorization, and binding after cloning; it is
used for disposable consultations. Exact revocation never removes an unrelated repository grant.

`effect:agent:create` on `container_worker_vm` writes the session bootstrap files under
`/home/agent`: `.evander/session.env`, shell profiles, Codex config in both supported
locations, Claude Code settings and MCP config, and NuGet config when `nuget=true`. The Codex
config points at the session-scoped OpenAI relay URL and MCP gateway URL, while the Claude Code
settings disable commit-message attribution trailers. If `dotnet=true` or `playwright=true`, the
profile points at the read-only worker cache mounts and keeps writable per-session cache paths
under `/home/agent`.
With `playwright=true`, the profile resolves the seeded Playwright Chromium binary and exports it as
`PLAYWRIGHT_MCP_EXECUTABLE_PATH`, `CHROME_BIN`, and `CHROMIUM_BIN`. If that package-selected
executable is absent, profile loading fails with a reprovisioning error rather than letting the
agent hard-code a different cached browser. The session manifest also records the Playwright
capability, and all container jobs for that session receive an explicit `pids_limit` of `1024`.
Non-Playwright sessions continue to inherit the strict worker default of `512`.

An operator can override the limits for later jobs of one persistent container-worker session
without changing worker-wide configuration. In Admin Web, unlock privileged operations, open
**Sessions**, select the session, and add or update the
`container_worker_resource_limits` key in **Metadata** with this JSON object value:

```json
{
  "memory": "8g",
  "cpus": 8,
  "pids_limit": 8192
}
```

The metadata editor treats the nested value as a JSON object snippet. Supported fields are
`memory` (a non-empty Docker-compatible memory string), `cpus` (a positive number), and
`pids_limit` (a positive integer); unknown fields and non-positive numeric values are rejected.
Saving the session changes newly dispatched jobs only and does not resize a running container.
The control plane validates this host-owned session setting and sends the result to the worker as
the first-class `resource_limits` job field; the worker does not interpret arbitrary request
metadata.

For an ordinary session, a non-empty `container_worker_resource_limits` object is the complete
per-job override. Therefore a Playwright session whose object contains only `cpus` no longer gets
the Playwright-specific `1024` value and falls back to the strict worker PID default of `512`;
include `pids_limit` when changing another limit. Team release-verification sessions instead merge
their `2048` PID default with any explicit session fields and do not impose a CPU quota unless the
project configures one. Their project-wide operator control is the separate Team project
`release_policy.resource_limits` setting.

Repository-local Playwright packages keep resolving normally; the worker does not replace or
shadow them. Because Playwright's ordinary `chromium.launch()` does not consume
`PLAYWRIGHT_MCP_EXECUTABLE_PATH`, Playwright-enabled agent prompts include the offline launch
contract and the session writes a dependency-neutral helper at
`$EVANDER_PLAYWRIGHT_LAUNCH_HELPER`:

```js
import { chromium } from "playwright";

const { launchSeededChromium } = await import(
  process.env.EVANDER_PLAYWRIGHT_LAUNCH_HELPER,
);
const browser = await launchSeededChromium(chromium, { headless: true });
```

The helper accepts the repository's own `chromium` export, adds the seeded executable path, and
automatically applies the authenticated session browser proxy when configured. Do not replace its
proxy option or hard-code
a `chromium-<revision>` cache directory: package revisions can differ while the worker intentionally
remains offline.

After changing either `HOST_PLAYWRIGHT_NODE_MODULES` / the runtime archive or
`HOST_PLAYWRIGHT_BROWSERS` / the browser archive, rerun the worker playbook so it can validate and
promote the pair together. Existing sessions created before the Playwright capability marker was
introduced must be recreated or reprovisioned with `playwright=true` before their jobs receive the
UI-test PID budget.

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

For query-plan evidence, provision a versioned seed alias, enable Query Store in the disposable
database, warm the query with a recorded repetition count, and save actual plan XML plus
`STATISTICS IO`, `STATISTICS TIME`, Query Store IDs, parameters, row counts, and index/statistics
metadata under `/artifacts`. Record the seed alias and SHA-256 with the evidence. These results
describe the approved fixture, not a production workload or production data distribution.

The host-side backend records the control-plane mapping in:

```text
<runtime-dir>/container-worker-executions.db
```

That SQLite store maps the host execution id and session id to the worker job id, worker URL,
target name, timestamps, last observed status, directory path, and sanitized metadata.
Operators can target an execution by execution id, worker job id, target name, or an unambiguous
session id.

## Operator CLI

Container worker execution is exposed as an explicit operator-only path:

```bash
evander-cli execution start-container-job \
  --session-id demo-session \
  --directory-path root \
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
- `GET /v1/codex-auth/profiles`
- `POST /v1/codex-auth/profiles`
- `GET /v1/codex-auth/profiles/{profile_id}`
- `PATCH /v1/codex-auth/profiles/{profile_id}`
- `POST /v1/codex-auth/profiles/{profile_id}/device-login`
- `GET /v1/codex-auth/profiles/{profile_id}/device-login/{login_id}`
- `DELETE /v1/codex-auth/profiles/{profile_id}/device-login/{login_id}`
- `DELETE /v1/codex-auth/profiles/{profile_id}/session`
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
- `POST /v1/sessions/{session_id}/worktrees/cleanup`
- `POST /v1/sessions/{session_id}/git/write-key`
- `DELETE /v1/sessions/{session_id}/git/keys/{key_name}`
- `DELETE /v1/sessions/{session_id}`
- `GET /v1/sessions/{session_id}/artifacts/{artifact_path:path}`
- `POST /v1/sessions/{session_id}/mssql/databases`
- `GET /v1/sessions/{session_id}/mssql/databases`
- `DELETE /v1/sessions/{session_id}/mssql/databases/{logical_name}`
- `GET /healthz`

The singleton `/v1/codex-auth` routes are compatibility aliases for reserved profile `default`.
Profiles have no hard-delete endpoint in V1.

`POST /v1/jobs` accepts `session_id`, optional `job_id`, `image`, `command`, optional `codex_turn`,
optional `repo_source`, optional `clone_branch`, optional `working_directory`, `resource_limits`,
and sanitized `metadata`. `codex_turn` is the structured prompt/auth-mode/profile contract used by
subscription execution; it never contains credentials. A capacity preflight can return HTTP 429
with a `deferral` object containing `profile_id`, `reason`, and optional `retry_at`. Raw `env`, `environment`, and
`environment_variables` request fields are rejected. If `repo_source` is present, it must match a
configured allowed Git source prefix. Repository-specific managed Git SSH aliases are accepted
only for their corresponding managed repository beneath that prefix.

Task worktree requests also accept paired `git_author_name` and `git_author_email`
values. The worker writes them with `git config --worktree` for new and retained
checkouts. These settings affect future commits without changing the shared clone,
the committer, or preparation provenance. Team resolves the task's `authored_by`
first, then the project author, and passes the effective identity on each preparation.

Task-worktree cleanup requires the deterministic target path, branch, and full expected commit.
Task-worktree preparation may also carry ordered `dependency_git_sources`. The worker fetches
each named managed branch, requires its tip to equal the accepted full commit ID, and composes
those commits before task work begins. A single or dominant commit is adopted directly; divergent
inputs produce one multi-parent commit using the optional composition subject and author identity.
That commit pins author and committer dates to the newest parent committer timestamp plus one
second UTC, making the resulting object reproducible for identical inputs. The optional
`dependency_composition_mode` distinguishes ordinary `automatic` composition from
`manual_recovery` (verify/fetch exact sources without merging) and `adopt_resolved` (verify an
exact resolved branch tip contains the base and every source before a clean reset). Recovery
modes require `dependency_composition_recovery_id`; adoption also requires
`resolved_dependency_branch` and `resolved_dependency_commit_sha`. Automatic merge conflicts
return HTTP 409 with structured base, source, failed-input, and conflict-path evidence.
The worker records compact immutable provenance in worktree-local Git configuration. Reuse
requires the same provenance; conflicts roll the clean checkout back to its
pre-composition base and return an error instead of exposing partial dependency state.
The worker serializes preparation and cleanup for the session repository, rejects tracked changes,
non-ignored untracked files, unexpected branches, commits, paths, or registrations, then repeats
the safety inspection immediately before removal. Ignored files are inventoried with reclaimed
bytes and may be force-removed only after both inspections prove they are the only remaining
non-Git files. Dry-run and already-absent results use the same structured response.

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
refused by default. In the Ansible-managed path, any non-`none` worker Docker network must be
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

Operators can tune worker daemon container flags with `--read-only-rootfs`, repeated `--tmpfs`, and
`--container-user`. `--docker-network` and `EVANDER_WORKER_DOCKER_NETWORK` override the selected
profile network. `host` requires the explicit unsafe local-experiment override described above.

Non-`none` networking requires the worker egress policy before it can protect cloud metadata or
adjacent infrastructure endpoints. Do not treat a Docker network override as a
metadata-protection boundary.

## Job Store

The active runtime store is `<runtime-dir>/worker-jobs.db`.

The SQLite `worker_jobs` table records query columns for job id, session id, container name,
status, cleanup status, timestamps, and a full JSON payload for `WorkerJobRecord` round trips.
Lifecycle updates write one SQLite row instead of rewriting the complete job history.

The job store must not contain request headers, bearer tokens, host credentials, or full environment variables.

## Current Limitations

The backend supports a directory default plus named workers and durable per-session placement.
The catalog does not provision, schedule, load-balance, or autoscale workers, and there is no
AKS/Kubernetes backend. Each worker needs its own deployment and isolation policy. The daemon
runs jobs; control-plane workflows retain issue intake, prompt orchestration, and PR publication.
It does not replace libvirt or provide general secret transport or internet access.
