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

For workers, extract the archive into a `runtime` directory beside the example
Dockerfile and build a derived runner image. Select that image when creating the
session. The default base runner remains unchanged. Configure workers with
`EVANDER_CODEX_RUNTIME_PROFILES=/etc/evander/codex-runtime-profiles.json`, using the
example profile file (owned by the worker operator; not group/world writable).
Restart the worker after installing the profile. Existing subscription profiles,
the worker's restricted Codex egress proxy, and account access must already work.

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
private host archive, retained independently of the session. Native files are
also under `/artifacts/security-scans/<scan_id>/`. Incomplete collection after
session expiry may leave only the session-local diagnostics.

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
