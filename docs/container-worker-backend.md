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
```

For directory-scoped runtime config, put these values on the root `providers` domain. Start the worker daemon on the worker VM with a matching token:

```bash
AGENT_SANDBOX_WORKER_TOKEN=... agent-sandbox-worker-daemon --host 0.0.0.0 --port 11888 --runtime-dir runtime --worker-root /var/lib/agent-worker
```

The host reads the token from `container_worker_token_env` when it calls the worker. Token values and request headers are not written to the execution mapping store.

The host-side `ExecutionSpec` uses first-class fields for container behavior: `command`, `image`, `repo_source`, `clone_branch`, `resource_limits`, `persistence_mode`, and `workspace_mode`. The backend does not read those values from `metadata`; metadata is only sanitized caller context.

## Session Durability

Durable state lives under:

```text
/var/lib/agent-worker/sessions/<session_id>/
```

Each session contains:

- `workspace/`: mounted into containers at `/workspace`
- `agent_home/`: mounted into containers at `/home/agent`
- `artifacts/`: mounted into containers at `/artifacts`
- `logs/`: platform-owned job logs
- `metadata.json`: platform bookkeeping for paths and creation time

Containers are disposable. Continuity comes from mounting the same `workspace/` and `agent_home/` into each run for a session. In particular, `/var/lib/agent-worker/sessions/<session_id>/agent_home/.codex` is preserved across container removal, so native Codex resume behavior can use its own state.

The daemon does not rewrite commands into `codex resume`. Callers must send the exact command they want to execute. The daemon preserves `/home/agent` and `/workspace` so those commands can resume naturally.

If a request includes `repo_source`, the daemon clones it only when `workspace/` is empty. Existing workspace contents are left untouched.

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
  --command 'echo hello from /workspace' \
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

The smoke command runs two unique jobs with the same session id. The first writes markers under `/home/agent` and `/workspace`; the second reads them back and fails if continuity or logs are missing:

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

`POST /v1/jobs` accepts `session_id`, optional `job_id`, `image`, `command`, optional `repo_source`, optional `clone_branch`, `resource_limits`, and sanitized `metadata`. Raw `env`, `environment`, and `environment_variables` request fields are rejected.

## Docker Constraints

Docker is used as a local container runtime on the worker VM. The daemon starts containers with:

- `--rm=false`
- `--cap-drop ALL`
- default `--network none`
- no privileged mode
- no Docker socket mount
- no host network
- persistent mounts for `/workspace`, `/home/agent`, and `/artifacts`
- working directory `/workspace`

The Docker network can be overridden with worker config or `--docker-network`, but `host` is refused.

## Job Store

The runtime store is `<runtime-dir>/worker-jobs.json`. It records job id, session id, image, command, container name/id, status, timestamps, persistent paths, and sanitized metadata.

The job store must not contain request headers, bearer tokens, host credentials, or full environment variables.

## Security Boundaries

The worker is treated as an untrusted execution surface. The host sends only explicit job fields: session id, stable job id, image, command, optional repo source, optional clone branch, resource limits, and sanitized caller metadata. It does not send raw environment variables, OpenAI keys, Azure DevOps PATs, GitHub tokens, host credentials, or request headers.

Secret-like metadata keys are redacted before the host sends the request and before the host mapping is stored. Raw `env`, `environment`, and `environment_variables` fields remain rejected by the worker request model.

## Current Limitations

This phase adds the worker-side daemon, durable session model, and host control-plane backend for a single configured worker URL. It does not replace the existing libvirt backend, implement scheduling, implement AKS/Kubernetes, autoscale workers, publish PRs, or migrate the full issue/prompt orchestration stack.
