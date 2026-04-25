# Container Worker Backend

`agent-sandbox-worker-daemon` is the worker-VM side of the container execution backend. It exposes a small HTTP API for starting, inspecting, stopping, deleting, and reading logs for jobs that run inside disposable Docker containers.

The daemon is intentionally only an execution envelope. It does not store host secrets, publish pull requests, decide policy, or turn platform summaries into agent memory.

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

## Current Limitations

This phase adds the worker-side daemon and durable session model. It does not replace the existing libvirt backend, implement Kubernetes, or wire `GatewayConfig.execution_backend: container_worker_vm` into gateway orchestration.
