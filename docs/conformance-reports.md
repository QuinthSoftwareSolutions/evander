# Conformance Reports

Admin Web exposes trigger and effect conformance snapshots in the existing Reports tab. The
read-only view is backed by `/api/admin/reports/conformance` and reads only the two standard
runtime artifacts below from the configured `runtime_dir`:

- `runtime/trigger-conformance-report.json`
- `runtime/effect-conformance-report.json`

The Admin Web surface does not accept caller-provided artifact paths. If a report file is missing,
the UI and API report that as a normal operator-visible state instead of exposing a filesystem
error.

## Regenerate The Reports

Publish the trigger report for Admin Web:

```bash
EVANDER_TRIGGER_CONFORMANCE_REPORT=runtime/trigger-conformance-report.json \
PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py
```

Use a custom path when preparing a report elsewhere:

```bash
EVANDER_TRIGGER_CONFORMANCE_REPORT=/tmp/trigger-conformance-report.json \
PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py
```

Publish the effect report for Admin Web:

```bash
EVANDER_EFFECT_CONFORMANCE_REPORT=runtime/effect-conformance-report.json \
PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py
```

Optional custom output path for the effect report:

```bash
EVANDER_EFFECT_CONFORMANCE_REPORT=/tmp/effect-conformance-report.json \
PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py
```

Without an explicit environment variable, ordinary pytest runs use a unique directory under
`.pytest_cache/d/conformance/`; the complete test runner uses its own run directory. The path is
printed in the test summary. Admin Web still reads the fixed runtime-directory paths listed above,
and its Run action already supplies the configured runtime path explicitly.

Reports are written atomically by the controlling pytest process after all selected tests finish.
Each case records its own evidence, so test order and parallel workers do not change aggregation.
A complete module run fails if the traceability matrix lacks successful evidence. A partial
selection may pass its selected tests but its report remains incomplete; use
`--require-conformance=trigger` or `--require-conformance=effect` to enforce the complete matrix.

## Report States

The Admin Web report records use a separate `state` field from the raw report `status` field:

- `pass` and `fail` come from the report status when the file is valid and recent.
- `missing` means the expected report file is absent.
- `invalid` means the JSON cannot be parsed or does not match the expected shape.
- `stale` means the report parsed successfully, but `generated_at` is older than the backend
  staleness threshold.

The backend preserves the raw `status` value even when a report becomes `stale`. The current stale
threshold is seven days.

## What The UI Shows

Each report record includes:

- `kind` and `label`
- `artifact_path`
- `state` and raw `status`
- `generated_at`
- `requirement_counts` and `requirement_count`
- `missing_coverage_count` and `missing_coverage`
- `traceability_task_count`
- bounded `failing_requirements` and `remediation_snippets`
- `error` and `guidance` when the report is missing or invalid

The UI intentionally keeps these records compact so operators can inspect conformance without
opening raw JSON blobs.
