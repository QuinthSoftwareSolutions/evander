# Trigger Spec Conformance Suite

The trigger conformance suite validates that the trigger implementation still matches the agreed
contract across schema validation, provider behavior, event envelopes, and orchestrator
integration.

Current coverage includes inbox-backed review request dispatch for `trigger:review:new-request`,
durable approval polling for `trigger:approval:pending-created`, CI completion polling for
`trigger:ci:run:completed`, and the existing provider-backed trigger set.

The Admin Web Reports tab reads the generated trigger artifact alongside the paired effect report;
see [conformance-reports.md](conformance-reports.md) for the shared operator view, report states,
and exact artifact names.

## Run the suite

```bash
PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py
```

Explicit report publication path:

```bash
EVANDER_TRIGGER_CONFORMANCE_REPORT=runtime/trigger-conformance-report.json \
PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py
```

Paired effect suite:

```bash
PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py
```

Explicit report publication path:

```bash
EVANDER_EFFECT_CONFORMANCE_REPORT=runtime/effect-conformance-report.json \
PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py
```

## Conformance report artifact

Ordinary runs write reports into a unique directory under `.pytest_cache/d/conformance/`;
the complete runner stores them with its execution ledgers. The test summary prints the paths.
To publish reports to Admin Web, set `EVANDER_TRIGGER_CONFORMANCE_REPORT` or
`EVANDER_EFFECT_CONFORMANCE_REPORT` explicitly as shown above.

The controlling pytest process writes reports after all selected tests finish. Complete suites
fail if requirements are missing or tests fail. Partial selections can run independently and
produce an incomplete report rather than claiming full conformance.

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
