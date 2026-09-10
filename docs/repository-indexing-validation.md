# APP repository index validation

Validated on 2026-09-10 against APP commit
`967d8971734a57c77c4379742415c805b45bb6ef`, using a disposable local compiler run,
control-plane import and portable export. No live deployment or APP source changes
were made.

## Measured results

| Measure | Result |
| --- | --- |
| Permitted source documents | 5,385 |
| Search chunks | 19,585 |
| Symbol occurrences | 773,987 |
| Documents with symbol occurrences | 4,359 |
| Project configurations attempted | 47 C# and 8 TypeScript |
| Project coverage | 42 ready, 8 partial, 5 failed |
| Compiler project time, summed | 650.4 seconds |
| Verified SQLite import time | 27.089 seconds |
| Worker JSON artifact | 361,891,314 bytes |
| Control-plane SQLite database | 710,189,056 bytes |
| Final portable ZIP | 54,850,545 bytes, about 52.3 MiB |
| Expected path among first five search hits | 25 of 30 cases |
| Median lexical index query | 45.905 ms |
| Median `rg` query | 24.045 ms |

The [query cases](../tests/fixtures/code-index/app-queries.json) mix identifiers and
natural-language phrases. The misses were `plc device contract tag mapping`, `sub
order splitting`, `station status`, `operator login tag`, and `material refill`.
At least one symbol definition was available for 29 query result sets; this is a
smoke check, not a complete assessment of navigation accuracy.

The index returned five bounded snippets; `rg` returned all matching lines from the
same permitted files. The index was slower in this local latency sample. Response
byte differences do not establish agent token or cost savings. Semantic relevance
and provider costs were not evaluated on the full APP corpus.

## Compiler coverage limits

The container used `--network=none`, 8 GiB memory, four CPUs, and read-only host
NuGet/npm caches. Two existing, untracked frontend lockfiles were copied into the
disposable checkout to seed Clients and AdminClient restores; their hashes are in
project metadata. This run therefore does not establish reproducibility from the
Git commit alone. Configured package relays may resolve more dependencies, but
that was not tested here.

These compiler timings predate the one-CPU default and do not measure performance on
Truman. Team/review preparation now reuses existing exact or ancestor coverage without
requesting compiler work. Regression fixtures exercise unchanged-file reuse, filtering
of modified/deleted/renamed files, semantic baseline reuse, commit scope, refresh
intervals and runtime-wide compiler capacity including uncertain worker submissions.

The follow-up index suites passed 36 tests, including the Admin API test run separately
outside the sandbox because its thread-pool path previously hung inside it. Frontend
TypeScript and targeted Ruff checks passed. A broader Team/workflow run passed 228
tests and found one unrelated release-UI fixture failure:
`test_release_progress_only_flags_git_packages_without_candidate_inputs` extracts
JavaScript without its `normalizedReleaseGateSequence` dependency. That test body
and the production release-UI source are unchanged from HEAD.

- Two OEE projects retained partial navigation after SourceLink rejected the
  disposable clone's local `file://` origin as an invalid host URI.
- Six Artifex configurations built successfully, but SCIP reported MSBuild workspace
  failures concerning package pruning. Navigation is conservatively marked partial.
- Two SpeedGroup projects could not restore required System.ServiceModel packages
  from the offline cache.
- OperatorClient main/spec and e2e TypeScript configurations lacked npm metadata
  in the offline cache. AdminClient main/spec, Shared main/spec and Customers succeeded.

Text retrieval remains available for permitted files in projects with incomplete
compiler coverage. Generated sources, excluded configuration/credential files and
oversized sources are omitted from retrieval. Angular templates have text retrieval
without compiler navigation.

## Portable bundle verification

Local bundle: `output/repository-indexing/app-967d897-index.zip`.
SHA-256: `185ef28a3805f543df2eb9bb05a1813f84796537a56875124f7971f1612aa2ab`.
Adjacent files contain the checksum, benchmark results, export verification and an
Admin Web screenshot. These generated artifacts are ignored by Git.

All 49 manifest file checksums were verified. The extracted tool ran with `python3
-I` from `/tmp`, without importing Evander, and searched for
`DurableEventDeliveryService` and resolved its class definition. This APP bundle
contains text and symbol data without embedding vectors. A separate regression
exports cached vectors and queries them in isolated Python using a supplied vector,
without an embedding service or Qdrant.

## Implementation checks

The final focused Python run passed **330 tests** in 27.81 seconds:

```bash
timeout 120s ./.venv/bin/pytest -q \
  tests/test_repository_index.py tests/test_repository_index_integration.py \
  tests/test_managed_git_service.py tests/test_container_worker_client.py \
  tests/test_semantic_store.py tests/test_gateway_semantic_tools.py \
  tests/test_team_addon_git_effects.py tests/test_team_dispatch_lifecycle.py \
  tests/test_effect_conformance_suite.py tests/test_workflow_editor_addon_catalog.py \
  tests/test_workflow_packs.py
```

The API checks ran outside the restricted sandbox after its test threadpool stalled.
Ruff, `git diff --check`, Admin Web TypeScript checking and a Vite production build
passed. Build output went to a temporary directory. Playwright against an isolated
API fixture exercised multiline settings, enablement, queue/cancel and bundle download.

The pinned indexing image built and passed its tool version checks. Image ID:
`sha256:b0658607f0f5c217964b4cf6a8a2a1428b1ffb0a807728136a6e02a345b5fe3c`.
Its archive is staged under `/tmp/evander-code-index-validation/`; deployment uses
the existing worker mechanism in [the operator guide](repository-indexing.md).

To repeat the retrieval benchmark after producing an isolated compiler artifact:

```bash
./.venv/bin/python scripts/benchmark_code_index.py \
  --repo /path/to/unchanged-app-checkout \
  --artifact /path/to/code-index.json \
  --settings /path/to/index-settings.json \
  --output /tmp/app-index-benchmark
```

The benchmark verifies the artifact's commit and source blob hashes before importing.
It writes detailed project coverage, per-query results and a portable ZIP.
