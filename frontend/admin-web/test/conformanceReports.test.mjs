import assert from "node:assert/strict";
import test from "node:test";

import {
  CONFORMANCE_REPORT_ENDPOINT,
  conformanceReportRunEndpoint,
  stateClassName,
  stateLabel,
  summarizeRequirementCounts,
} from "../src/features/reports/conformanceReports.ts";

test("conformance report endpoint stays stable", () => {
  assert.equal(CONFORMANCE_REPORT_ENDPOINT, "/api/admin/reports/conformance");
  assert.equal(
    conformanceReportRunEndpoint("trigger"),
    "/api/admin/reports/conformance/trigger/run",
  );
  assert.equal(
    conformanceReportRunEndpoint("effect"),
    "/api/admin/reports/conformance/effect/run",
  );
});

test("conformance states map to clear labels and classes", () => {
  assert.equal(stateLabel("pass"), "Pass");
  assert.equal(stateLabel("fail"), "Fail");
  assert.equal(stateLabel("missing"), "Missing");
  assert.equal(stateLabel("invalid"), "Invalid");
  assert.equal(stateLabel("stale"), "Stale");

  assert.equal(stateClassName("pass"), "state-pass");
  assert.equal(stateClassName("fail"), "state-fail");
  assert.equal(stateClassName("missing"), "state-missing");
  assert.equal(stateClassName("invalid"), "state-invalid");
  assert.equal(stateClassName("stale"), "state-stale");
});

test("conformance report payload keeps the UI contract small and explicit", () => {
  const payload = {
    generated_at: "2026-05-16T12:00:00Z",
    stale_after_seconds: 7 * 24 * 60 * 60,
    reports: [
      {
        kind: "trigger",
        label: "Trigger",
        artifact_path: "trigger-conformance-report.json",
        state: "pass",
        status: "pass",
        generated_at: "2026-05-16T12:00:00Z",
        requirement_counts: { pass: 5, unknown: 1 },
        requirement_count: 6,
        missing_coverage_count: 0,
        missing_coverage: {},
        traceability_task_count: 4,
        failing_requirements: [],
        remediation_snippets: [],
        error: null,
        guidance: null,
      },
      {
        kind: "effect",
        label: "Effect",
        artifact_path: "effect-conformance-report.json",
        state: "invalid",
        status: "fail",
        generated_at: "2026-05-15T12:00:00Z",
        requirement_counts: { fail: 1, missing: 2, stale: 1 },
        requirement_count: 4,
        missing_coverage_count: 3,
        missing_coverage: {
          task_binding: ["binding:resolution-and-chaining", "queue:idempotency"],
          task_runtime: ["regression:provider-failure"],
        },
        traceability_task_count: 2,
        failing_requirements: [
          {
            requirement_id: "binding:resolution-and-chaining",
            status: "fail",
            details: "binding mismatch",
            remediation: "update the binding expression",
          },
        ],
        remediation_snippets: [],
        error: "malformed report",
        guidance: "PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py",
      },
    ],
  };

  assert.equal(payload.generated_at, "2026-05-16T12:00:00Z");
  assert.equal(payload.stale_after_seconds, 7 * 24 * 60 * 60);
  assert.equal(payload.reports.length, 2);
  assert.deepEqual(
    payload.reports.map(
      ({
        artifact_path,
        generated_at,
        kind,
        missing_coverage_count,
        requirement_count,
        state,
        status,
        traceability_task_count,
      }) => ({
        artifact_path,
        generated_at,
        kind,
        missing_coverage_count,
        requirement_count,
        state,
        status,
        traceability_task_count,
      }),
    ),
    [
      {
        artifact_path: "trigger-conformance-report.json",
        generated_at: "2026-05-16T12:00:00Z",
        kind: "trigger",
        missing_coverage_count: 0,
        requirement_count: 6,
        state: "pass",
        status: "pass",
        traceability_task_count: 4,
      },
      {
        artifact_path: "effect-conformance-report.json",
        generated_at: "2026-05-15T12:00:00Z",
        kind: "effect",
        missing_coverage_count: 3,
        requirement_count: 4,
        state: "invalid",
        status: "fail",
        traceability_task_count: 2,
      },
    ],
  );
  assert.deepEqual(payload.reports[0].failing_requirements, []);
  assert.deepEqual(payload.reports[0].remediation_snippets, []);
  assert.equal(payload.reports[1].failing_requirements.length, 1);
  assert.equal(
    payload.reports[1].failing_requirements[0].requirement_id,
    "binding:resolution-and-chaining",
  );
  assert.equal(payload.reports[1].failing_requirements[0].status, "fail");
  assert.equal(payload.reports[1].failing_requirements[0].details, "binding mismatch");
  assert.equal(
    payload.reports[1].failing_requirements[0].remediation,
    "update the binding expression",
  );
  assert.deepEqual(payload.reports[1].remediation_snippets, []);
  assert.equal(payload.reports[1].missing_coverage.task_binding.length, 2);
  assert.equal(payload.reports[1].missing_coverage.task_runtime.length, 1);
  assert.equal(payload.reports[1].error, "malformed report");
  assert.equal(
    payload.reports[1].guidance,
    "PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py",
  );
});

test("requirement count summaries are stable and concise", () => {
  const payload = {
    pass: { pass: 5, unknown: 1 },
    fail: { fail: 1, missing: 2, stale: 1 },
  };

  assert.equal(
    summarizeRequirementCounts(payload.pass),
    "Pass 5, Unknown 1",
  );
  assert.equal(
    summarizeRequirementCounts(payload.fail),
    "Fail 1, Missing 2, Stale 1",
  );
});
