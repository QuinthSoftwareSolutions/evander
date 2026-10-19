# Codex Security scan provider

This optional package implements `effect:security:scan-repo`. The node and its
result contract belong to Evander core; this package owns the Codex runtime.
Scans run inside an existing session, against an isolated Git clone. A completed
scan with vulnerabilities succeeds; use a downstream gate to apply a severity
policy. Partial, unknown, interrupted, or invalid results follow the error path.

## Installation

Install this Python project independently (`uv pip install ./packages/codex-security-addon`), then enable `codex-security-addon`
through Admin Web's Add-ons page. Its source discovery manifest works when the
project is under a configured `packages` directory. Restart the listed services
after enablement. Core does not install its Node dependencies or enable it.

Build the pinned runtime on a trusted Linux host with Python 3.12 and Node 22.13+,
using the same CPU architecture as the target VMs or workers:

```sh
bash packages/codex-security-addon/scripts/build-runtime.sh
```

The archive contains only the locked runtime and support scripts. It contains no
authentication files. Install it into VMs using the privileged setup account:

```sh
bash packages/codex-security-addon/scripts/install-vm-runtime.sh \
  agent-admin@PRIVATE_VM_ADDRESS \
  packages/codex-security-addon/dist/codex-security-runtime.tar.gz
```

For workers, first build the base runner archive, then build the optional scanner
image archive on the trusted deployment controller:

```sh
scripts/build_evander_runner_image.sh
python3 packages/codex-security-addon/scripts/build-worker-image.py
```

The builder writes `dist/evander-security-scan.tar` and its checksum manifest.
It reuses a verified archive when the base archive, pinned Node binary and npm
lockfile, runtime sources, Dockerfile and build scripts match. It performs no npm
install or Docker build on a cache hit. `--cache-key` prints a portable fingerprint
without installing or building; call it after preparing the base runner archive.
`--force` or `RUNNER_FORCE_REBUILD=true` forces a rebuild. Missing or damaged
archives are rebuilt; a failed build/save preserves the preceding cached archive.

Operator deployment pipelines should invoke this builder after the base builder,
cache its output separately, and append the archive to the worker inventory's
`evander_runner_image_archives`. Core does not implicitly build or deploy it.
`SECURITY_SCAN_BASE_ARCHIVE`, `SECURITY_SCAN_BASE_IMAGE`, `SECURITY_SCAN_IMAGE_NAME`
and `SECURITY_SCAN_ARCHIVE_PATH` select inputs and destination. The base archive
must contain the selected base image tag. `HOST_NODE_BINARY` selects the pinned
host Node executable; it otherwise resolves `node` on PATH. `DOCKER` selects the
Docker executable. Builds use the loaded base image with pulling and build
networking disabled.

Use a `.tar.gz` or `.tgz` destination to stream the image directly through gzip,
without an intermediate uncompressed image archive. Docker loads either format.
The builder releases the runtime build context before exporting. Runtime npm
dependencies are installed in temporary staging, and file permissions are set
before Docker copies them, avoiding a second runtime layer for `chmod`.

Deployment-specific Dockerfiles and scan isolation policy can be supplied with
`SECURITY_SCAN_DOCKERFILE` and `SECURITY_SCAN_CONFIGURE_PATH`. The latter replaces
`configure.mjs` in the image and preserves the packaged implementation as
`configure-stock.mjs`; both inputs participate in the fingerprint. Keep these
operator assets outside this repository. Dockerfiles must accept `RUNNER_IMAGE`
and copy the staged `runtime/` directory. Select the deployed image when creating
the session. Configure workers with
`EVANDER_CODEX_RUNTIME_PROFILES=/etc/evander/codex-runtime-profiles.json`, using the
example profile file (owned by the worker operator; not group/world writable).
Restart the worker after installing the profile. Existing subscription profiles,
the worker's restricted Codex egress proxy, and account access must already work.

For accounts approved for Daybreak, set `default_cyber_access_program` on the
worker-owned `codex-security` runtime profile to `daybreak_blue` or `daybreak_red`,
as supported by the selected model and account. `standard` explicitly selects
standard safeguards; omitting the field preserves the native default. The broker
passes this selection to each new or resumed native turn, including separate
SDK child executions. An explicit `--cyber-access-program` argument takes
precedence. The setting neither grants access nor changes the model; the server
must accept the selected account, model, and program. Restart the worker after
changing its profile file. Confirm a bounded scan in the selected runtime before
starting a full repository scan. Private `runtime.started` diagnostics record the
requested program; this records the request, not an entitlement check.

Operators can also select the session's **Cyber access program** through agent
creation, an existing container agent's **Edit Codex usage**, or **Sessions →
Model and access**. An explicit session choice overrides the worker profile
default for that session's scans. Automatic defers to the runtime default.
Saving a session choice affects subsequent turns and scans without interrupting
an active turn. See [agent Cyber access configuration](../../docs/configuration-reference.md#agent-cyber-access-program).

API scans use the session's existing model relay. Subscription scans use a
job-scoped Unix socket and the selected worker profile; neither access nor refresh
tokens enter the container or native Codex process. A worker-owned HTTP relay
attaches credentials only to approved upstream requests, preserves the selected
account's routing constraints, and refreshes that same profile on the worker.
Native Codex receives a nonfunctional placeholder through a local TLS adapter.
The same broker authenticates deep-scan
children. A missing runtime or profile is an error, without an API-billing fallback.
The scanner requires compatible Codex native permission-profile and managed
authentication-policy support. Worker jobs mount a read-only policy at
`/etc/codex` and verify it before supplying the placeholder. The broker
accepts scan CLI options and read-only SDK preflight calls; arbitrary app-server
commands and client-selected authentication routes are rejected. Scan permission
profiles, resume state and the private `CODEX_HOME` remain available.

## Workflow configuration

Use this node's `config` object in a workflow pack:

```json
{
  "session_id": "${{create_agent.session_id}}",
  "repo_path": "/home/agent/repos/example",
  "provider": "codex-security",
  "mode": "standard",
  "timeout_seconds": 3600
}
```

With neither commit supplied, scan the complete clean HEAD snapshot. `from_commit`
alone reviews through captured HEAD. `to_commit` alone reviews that commit against
its first parent; an initial commit reviews its complete tree. Supplying both
reviews their direct diff. Hashes must resolve locally; the scanner does not fetch
credentials or missing commits. Dirty and untracked work remains untouched and is
excluded. Empty diffs complete without calling a model.
Committed scans accept the native `diff`, `commit`, and `branch_diff` coverage
modes only when the sealed Git diff target matches the exact requested base and
head revisions. Repository and working-tree coverage cannot satisfy that range.

`mode: deep` is available only when both commit fields are absent. It uses Codex
Security's bounded independent scans; the node timeout and session expiry apply
to the whole operation. Provision session TTL accordingly. The scanner's own deep
time limit reserves thirty seconds within the node timeout for finalization.

Connect `completed` to the success branch and `error` to recovery. Outputs include
`scan_id`, `provider_scan_id`, `session_id`, `repo_path`, resolved `from_commit` and
`to_commit`, `status`, `coverage`, `findings`, `severity_counts`, and `artifacts`.
Findings expose identifiers, occurrence IDs, titles, severity and locations. Full native evidence
remains in the report and JSON artifacts. `artifacts.archive_key` identifies the
private host archive, retained independently of the session. This archive contains
canonical reports, exports, checkpoints and scan/target receipts. Raw logs and
diagnostic histories are excluded, including when an older scanner image returns
its legacy file list. Legacy directories are expanded using validated result
paths; their extra exports and checkpoints require the updated image.

## Private diagnostics

Container-worker scans opt into worker-owned diagnostics at
`<worker_root>/private-job-diagnostics/<job_id>/`, outside session storage and
container mounts. Operators inspect this directory on the worker; it has no
artifact download or workflow output API. It is never included in the findings
archive or sent to chat channels. Treat its contents as sensitive source and
tool output. Subscription credentials, request headers/bodies and account
configuration are not deliberately recorded.

`events.jsonl` and `events.previous.jsonl` retain recent brokered parent/child
notifications, completed tool items, stderr, context/token usage and terminal
turn status. Streaming text deltas are omitted. HTTP evidence contains status,
allowlisted request IDs, retry timing and rate-limit metadata, without response
bodies or request credentials. `summary.json` records the job outcome, container
exit/OOM evidence when inspection supplies it, capture errors and discarded data.

Before deleting its private Codex home, the runner stages native session/log
history and `execution.json` with timing, timeout/termination reason and exit
status. The worker copies these, raw runner logs and scan receipts to private
storage when the job ends or is removed. On worker restart it recovers evidence
from interrupted jobs; on abrupt container death it also looks in the remaining
native home. A runner history snapshot is limited to 64 MiB total, 8 MiB per file,
1024 entries and five seconds. Worker collection has the same per-file/entry/time
limits. Truncation and capture failures are recorded where storage permits; a
hard crash can still lose data not yet written to disk.

Defaults are seven days after completion, 128 MiB per job and 1 GiB across the
worker. Event files rotate; byte pressure evicts the oldest inactive jobs before
discarding incoming evidence. Active jobs are protected from eviction. Expiry is
checked at startup, on capture and every five minutes. Environment overrides are
documented in [worker diagnostics retention](../../docs/container-worker-backend.md#private-job-diagnostics).
Diagnostics failures do not turn a successful scan into a failure or block report
delivery. Deploy both worker code and the rebuilt scanner image for complete
capture; runner changes participate in the existing image cache fingerprint.

On stop or removal, the worker waits up to fifteen seconds for broker writers to
drain. If they take longer, the private capture stays open. The last writer then
triggers collection and finalization automatically, preserving terminal stderr
and native exit records without requiring another workflow poll. Shutdown
timeouts are recorded privately. The worker marks diagnostics as captured only
after the writers drain.

VM scans stage the same native history locally; worker retention applies only
to the container-worker backend. Raw diagnostic paths are not scan outputs.

Duplicate execution delivery and an orchestrator restart rejoin the original scan.
A worker restart interrupts brokered scans and reports failure; it never starts a
new paid scan implicitly. No code fixes, issue publication, or PR creation are run.
Cancellation prevents subsequent startup and preserves the cancelled receipt
even if scan cleanup finishes later.

## Validation

Run the project tests with the core source on PYTHONPATH. In a provisioned test
session, scan a small synthetic repository using API and subscription profiles,
including standard, deep, and two-commit diff targets. Confirm sealed output,
archive retrieval, original HEAD/index preservation, and timeout cleanup. A
synthetic provider or protocol test does not establish live model access.

The core opt-in native tests use synthetic account discovery, model metadata and
streaming responses in an isolated network namespace. They cover preflight,
execution and resume without live model requests. Run them on Linux with `bwrap` installed:

```sh
EVANDER_TEST_NATIVE_CODEX=/absolute/path/to/codex \
  PYTHONPATH=src .venv/bin/pytest -q tests/test_codex_runtime_native.py
```

Provider behavior follows the [Codex Security documentation](https://learn.chatgpt.com/docs/security/cli/reference).
