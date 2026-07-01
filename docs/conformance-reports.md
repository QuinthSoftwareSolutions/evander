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

Trigger suite:

```bash
PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py
```

Optional custom output path for the trigger report:

```bash
EVANDER_TRIGGER_CONFORMANCE_REPORT=runtime/trigger-conformance-report.json \
PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py
```

Effect suite:

```bash
PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py
```

Optional custom output path for the effect report:

```bash
EVANDER_EFFECT_CONFORMANCE_REPORT=runtime/effect-conformance-report.json \
PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py
```

The optional environment variables only change where the suites write their JSON output. Admin Web
still reads the fixed runtime-directory paths listed above.

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
