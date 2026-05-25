# Trigger Spec Conformance Suite

The trigger conformance suite validates that the trigger implementation still matches the agreed
contract across schema validation, provider behavior, event envelopes, and orchestrator
integration.

Current coverage includes inbox-backed review request dispatch for `trigger:review:new-request`,
CI completion polling for `trigger:ci:run:completed`, and the existing provider-backed trigger set.

The Admin Web Reports tab reads the generated trigger artifact alongside the paired effect report;
see [conformance-reports.md](conformance-reports.md) for the shared operator view, report states,
and exact artifact names.

## Run the suite

```bash
PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py
```

Optional report output path:

```bash
AGENT_SANDBOX_TRIGGER_CONFORMANCE_REPORT=runtime/trigger-conformance-report.json \
PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py
```

Paired effect suite:

```bash
PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py
```

Optional report output path:

```bash
AGENT_SANDBOX_EFFECT_CONFORMANCE_REPORT=runtime/effect-conformance-report.json \
PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py
```

## Conformance report artifact

On every successful run, the suite writes a machine-readable JSON report to:

- `runtime/trigger-conformance-report.json` (default), or
- the path in `AGENT_SANDBOX_TRIGGER_CONFORMANCE_REPORT`.

The paired effect suite writes the same style of report to:

- `runtime/effect-conformance-report.json` (default), or
- the path in `AGENT_SANDBOX_EFFECT_CONFORMANCE_REPORT`.

The report includes:

- requirement-level pass/fail records by trigger type,
- traceability matrix entries mapping prior trigger tasks to concrete tests,
- missing coverage failures (`missing_coverage`), and
- remediation text for future gaps.

The trigger and effect reports use the same top-level shape so Admin Web can render them through a
shared read-only contract.

## Interpreting failures

- **Schema/config failures** indicate a trigger config no longer enforces required fields or type
  constraints.
- **Event contract failures** indicate envelope/payload keys diverged from the contract or leaked
  sensitive data.
- **Provider behavior failures** indicate trigger polling/filtering/cursor semantics regressed.
- **Traceability failures** indicate at least one planned capability is no longer covered by tests.
