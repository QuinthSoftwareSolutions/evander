# Repository index validation

Validate a repository index against a fixed commit and representative retrieval
tasks. Keep repository-specific query cases, source exports, screenshots, and
measurement reports in operator-owned storage outside this repository. This guide
defines a repeatable procedure; it does not claim a current deployment or benchmark
result.

## Prepare a reproducible input

1. Build the index in a disposable worker using the image and policy described in
   [repository indexing](repository-indexing.md). Record the exact commit, image
   digest, toolchain, settings, CPU/memory limits, and dependency sources.
2. Preserve the worker's `code-index.json` artifact and matching settings. Use an
   unchanged checkout at that commit for validation. Record any added lockfiles,
   package-cache seeds, or generated inputs: those inputs affect reproducibility.
3. Write nonempty query cases with independently chosen expected paths. Include
   exact identifiers, natural-language requests, and queries likely to miss.

A synthetic query-case file has this shape:

```json
[
  {
    "query": "durable event retry",
    "expected_paths": ["src/events/retry.py", "src/events/retry/*.py"]
  }
]
```

Paths are repository-relative glob patterns. Choose expectations from the source
and task being evaluated before inspecting the index results.

## Run the lexical benchmark

From the Evander checkout, pass the query-case file explicitly:

```bash
timeout --kill-after=5s 300s ./.venv/bin/python scripts/benchmark_code_index.py \
  --repo /path/to/unchanged-checkout \
  --artifact /path/to/code-index.json \
  --settings /path/to/index-settings.json \
  --cases /path/to/query-cases.json \
  --output /tmp/repository-index-benchmark
```

The script verifies the checkout commit and tracked source blob hashes, imports
into an isolated runtime below the output directory, and writes `report.json` and
`index.zip`. It does not compile the repository or deploy a worker. Its settings
disable semantic indexing for this lexical benchmark.

The report records coverage, summed compiler-project time from the artifact,
measured import time, artifact/database/export sizes, per-query latency, expected
path presence in the first five hits, and a symbol-definition smoke check. Compiler
project durations are not an end-to-end build duration. An available definition
does not establish complete or correct reference and implementation navigation.

The index returns five bounded snippets while the `rg` baseline returns every
matching line in permitted files. Their response sizes represent different output
contracts. Neither response bytes nor lexical-query latency establish agent token,
cost, or task-completion savings. Evaluate semantic relevance and provider usage
separately if those are part of the intended use.

## Check coverage and portable export

Review text, semantic, and navigation coverage independently. Investigate failed
restores, partial compiler workspaces, source exclusions, oversized files, and
missing build-generated inputs. When SCIP is disabled, navigation is intentionally
`disabled`. Angular templates can have text retrieval without symbol navigation.

Extract the portable bundle into a separate directory and verify its manifest
checksums. Then run the included standard-library query tool in isolated Python:

```bash
unzip /tmp/repository-index-benchmark/index.zip -d /tmp/repository-index-portable
python3 -I /tmp/repository-index-portable/query.py \
  --db /tmp/repository-index-portable/index.sqlite status
python3 -I /tmp/repository-index-portable/query.py \
  --db /tmp/repository-index-portable/index.sqlite search 'durable event retry'
python3 -I /tmp/repository-index-portable/query.py \
  --db /tmp/repository-index-portable/index.sqlite navigate SYMBOL --kind definitions
```

Replace `SYMBOL` with a symbol returned by a search in that bundle. Compare paths,
line ranges, and definitions with the fixed checkout. Check that excluded source
and credentials are absent. Vector portability requires a separate export with
cached embeddings and a query vector matching the manifest's model and dimensions.

## Verify implementation changes

Focused regression suites cover settings and retrieval, compiler behavior, Git
baseline reuse, worker retirement, and import/export integration:

```bash
timeout --kill-after=5s 180s ./.venv/bin/pytest -q \
  tests/test_repository_index.py tests/test_repository_index_runner.py \
  tests/test_repository_index_reuse.py tests/test_repository_index_lifecycle.py \
  tests/test_repository_index_integration.py
```

Use the current test output as evidence; historical pass counts do not establish
the current checkout's state. For UI changes, include focused settings, queue/cancel,
and download checks with bounded timeouts. See [testing](testing.md) for the complete
runner and browser requirements.

A validation report should state the exact inputs, commands, measured outcomes,
failed or omitted checks, and environment limits. Keep compiler coverage, retrieval
quality, portability, and performance conclusions separate so a partial success
does not imply untested capabilities.
