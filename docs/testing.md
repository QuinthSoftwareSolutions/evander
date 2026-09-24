# Running and maintaining the test suite

## Complete Python run

Install the locked development dependencies and use the complete runner from the repository root:

```bash
uv sync --frozen --extra dev
./.venv/bin/python scripts/run_tests.py
```

The default is at most four pytest-xdist workers. Work stealing balances independent cases
across workers, including cases from large modules. After those workers exit, every test marked
`performance` runs serially. Those tests assert real elapsed-time limits, and their limits and
workloads are unchanged. Documentation checks remain in the complete run.

```bash
# The same complete selection in one process, for debugging or comparison.
./.venv/bin/python scripts/run_tests.py --workers 0

# A larger or smaller parallel pool, with a total deadline.
./.venv/bin/python scripts/run_tests.py --workers 8 --timeout 900

# Focused cases retain normal pytest selection and output.
timeout --kill-after=5s 120s ./.venv/bin/python -m pytest -q tests/test_trigger_queue.py
```

The runner defaults to a 900-second deadline for the entire selection. Timeout, Ctrl-C, and
SIGTERM cancellation stop the pytest process group and its fixture descendants. It continues
the performance phase after ordinary assertion failures so the complete result remains useful.

Each invocation stores selected node IDs, deselected IDs, setup/call/teardown outcomes, durations,
and conformance reports in a unique directory under `.pytest_cache/test-runs/`.
Use `--artifacts-dir /path/to/run` to preserve a named run. The runner rejects overlapping
lanes, changed collection between phases, omitted cases, and cases without terminal results.
Skipped cases remain visible in its summary; a skip is not evidence of preserved behavioral
coverage.

Plain `./.venv/bin/pytest -q` remains a complete serial run. For direct parallel debugging,
`pytest -n 4 --dist=worksteal -m 'not performance'` selects only the parallel phase; it is not
a replacement for the complete runner. See the
[pytest-xdist distribution documentation](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)
for worker scheduling options.

## Isolation and conformance

Every test retains fresh mutable runtime, database, browser-context, and fake-service state.
Reuse immutable seeds or executable infrastructure only when tests explicitly verify that
mutations cannot carry into the next case. Do not disable SQLite durability or replace database
integration with mocks merely to improve timing.

Conformance cases record evidence in a function-scoped collector. The controlling pytest process
aggregates actual passed-case evidence and atomically writes the reports after execution.
Complete module runs fail if a requirement is missing; reordered or distributed tests have the
same rule. Report-formatting tests use their own inputs and can run independently.

Ordinary runs write conformance reports into their run directory or a unique pytest-cache
directory. To publish the operator artifacts, use the explicit environment variables documented
in [conformance-reports.md](conformance-reports.md). Partial selections produce incomplete reports,
never false passing conformance. Add `--require-conformance=trigger` or
`--require-conformance=effect` to require a complete matrix for a custom selection.

Tests marked `documentation` include documentation modules, manual publication contracts,
and the field-help inventory. `pytest -m documentation` can run them separately, but neither
the complete runner nor plain pytest excludes them.

## Frontend

From `frontend/admin-web`:

```bash
npm run test:unit       # Function and source-contract checks.
npm run test:browser    # The browser cases formerly mixed into npm test.
npm test               # Both of the above; preserves the previous default selection.
npm run build          # Prepare packaged New and Classic UI assets for smoke tests.
timeout --kill-after=10s 600s npm run test:all
```

`test:all` includes every optional smoke file as well. Loopback servers and Chromium must be
available; browser startup failure is a failure, not a skip. The build prepares local generated
assets; keep generated build changes out of unrelated source patches. Browser processes may be
shared within a file, while every case gets a new browser context and mutable fixture state.
See [frontend test notes](../frontend/admin-web/test/README.md).

## Preserving coverage during cleanup

- A deletion must identify the retained test and identical invariant. Similar names or setup
  are insufficient evidence that two cases are redundant.
- Parameterization preserves every original payload, negative case, boundary, and assertion.
  Compare collected node IDs and document renamed or split cases.
- Shared builders keep setup explicit and do not hide assertions or share mutable objects.
- Replace source-text checks with behavior checks before removing the old assertions.
- Keep authorization, failure, retries, concurrency, migrations, and performance workloads
  intact. Never weaken thresholds or skip failures to report a faster run.
- Compare elapsed time only after changes settle and other test jobs have stopped. Record the
  exact command, selection, worker count, pass/fail/skip totals, and known baseline failures.
