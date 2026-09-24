"""Execution ledgers and order-independent, controller-owned conformance reports."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from conformance_support import MATRICES, build_report, write_report

CONFORMANCE_FILES = {
    "test_effect_conformance_suite.py": "effect",
    "test_trigger_conformance_suite.py": "trigger",
}
REQUIREMENTS_PROPERTY = "evander_conformance_requirements"


def conformance_kind(nodeid: str) -> str | None:
    return CONFORMANCE_FILES.get(Path(nodeid.split("::", 1)[0]).name)


@pytest.fixture
def report_collector() -> dict[str, Any]:
    """Each case records its own evidence; workers never share a mutable collector."""
    return {"requirements": []}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("suite reporting")
    group.addoption("--execution-ledger", help="Write selected cases and phase outcomes as JSON.")
    group.addoption(
        "--conformance-output-dir", help="Directory for this run's conformance reports."
    )
    group.addoption(
        "--require-conformance",
        action="append",
        choices=sorted(MATRICES),
        default=[],
        help="Fail the run unless this conformance matrix is completely satisfied.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(SuiteReporter(config), "evander-suite-reporter")


class SuiteReporter:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.started = time.monotonic()
        self.available: dict[str, set[str]] = {}
        self.selected: list[str] = []
        self.deselected: set[str] = set()
        self.complete: set[str] = set()
        self.outcomes: dict[str, list[dict[str, Any]]] = {}
        self.requirements: dict[str, list[dict[str, Any]]] = {}
        self.paths: list[Path] = []
        self.failed_matrices: list[str] = []

    @pytest.hookimpl(hookwrapper=True)
    def pytest_make_collect_report(self, collector: pytest.Collector):
        outcome = yield
        report = outcome.get_result()
        kind = conformance_kind(report.nodeid)
        if kind is None or "::" in report.nodeid:
            return
        # Module collection precedes node selection and -k/-m deselection. Retain
        # every parameter ID, so a partial selection cannot claim full coverage.
        self.available[kind] = {
            item.nodeid for item in report.result if isinstance(item, pytest.Function)
        }

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.selected = [item.nodeid for item in session.items]
        selected = set(self.selected)
        self.complete = {
            kind
            for kind, available in self.available.items()
            if available and available <= selected
        }

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            name = item.path.name
            if (
                name.endswith("_docs.py")
                or name.startswith("test_documentation")
                or name
                in {
                    "test_evander_user_manual.py",
                    "test_operator_field_help_inventory.py",
                }
            ):
                item.add_marker(pytest.mark.documentation)

    def pytest_deselected(self, items: list[pytest.Item]) -> None:
        self.deselected.update(item.nodeid for item in items)

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node: Any, ids: list[str]) -> None:
        self.selected = list(ids)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: Any, error: Any) -> None:
        state = node.workeroutput.get("evander_suite_collection", {})
        for kind, nodeids in state.get("available", {}).items():
            self.available.setdefault(kind, set()).update(nodeids)
        self.complete.update(state.get("complete", []))
        self.deselected.update(state.get("deselected", []))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: Any):
        outcome = yield
        report = outcome.get_result()
        if report.when != "call" or not report.passed:
            return
        collector = getattr(item, "funcargs", {}).get("report_collector")
        if collector is not None:
            # user_properties are serialized by xdist with the individual report.
            report.user_properties.append(
                (
                    REQUIREMENTS_PROPERTY,
                    collector["requirements"],
                )
            )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.outcomes.setdefault(report.nodeid, []).append(
            {
                "phase": report.when,
                "outcome": report.outcome,
                "duration": report.duration,
            }
        )
        kind = conformance_kind(report.nodeid)
        if kind is not None and report.when == "call" and report.passed:
            for name, value in report.user_properties:
                if name == REQUIREMENTS_PROPERTY:
                    self.requirements.setdefault(kind, []).extend(value)

    def _report_directory(self) -> Path:
        configured = self.config.getoption("--conformance-output-dir")
        if configured:
            return Path(configured)
        return self.config.cache.mkdir("conformance") / uuid.uuid4().hex

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if hasattr(self.config, "workerinput"):
            self.config.workeroutput["evander_suite_collection"] = {
                "available": {kind: sorted(nodeids) for kind, nodeids in self.available.items()},
                "complete": sorted(self.complete),
                "deselected": sorted(self.deselected),
            }
            return
        if not self.config.getoption("collectonly"):
            required = self.complete | set(self.config.getoption("--require-conformance"))
            observed = {conformance_kind(nodeid) for nodeid in self.outcomes} - {None}
            directory = self._report_directory() if observed or required else None
            for kind in sorted(observed | required):
                failed = [
                    nodeid
                    for nodeid, phases in self.outcomes.items()
                    if conformance_kind(nodeid) == kind
                    and (
                        any(phase["outcome"] != "passed" for phase in phases)
                        or not {"setup", "call", "teardown"}
                        <= {phase["phase"] for phase in phases}
                    )
                ]
                summary = build_report(
                    self.requirements.get(kind, []),
                    MATRICES[kind],
                    failures=failed,
                    missing_tests=sorted(self.available.get(kind, set()) - self.outcomes.keys()),
                )
                override = os.environ.get(f"EVANDER_{kind.upper()}_CONFORMANCE_REPORT")
                path = Path(override) if override else directory / f"{kind}-conformance-report.json"
                write_report(path, summary)
                self.paths.append(path)
                if kind in required and summary["status"] != "pass":
                    self.failed_matrices.append(kind)
            if self.failed_matrices and session.exitstatus == pytest.ExitCode.OK:
                session.exitstatus = pytest.ExitCode.TESTS_FAILED

        ledger = self.config.getoption("--execution-ledger")
        if ledger:
            write_report(
                Path(ledger),
                {
                    "collected": self.selected,
                    "deselected": sorted(self.deselected),
                    "tests": self.outcomes,
                    "exitstatus": int(session.exitstatus),
                    "duration": time.monotonic() - self.started,
                    "conformance_reports": [str(path) for path in self.paths],
                },
            )

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if self.paths:
            terminalreporter.write_line(
                "Conformance reports: " + ", ".join(str(path) for path in self.paths),
            )
        if self.failed_matrices:
            terminalreporter.write_sep(
                "=",
                "Incomplete or failed conformance: " + ", ".join(self.failed_matrices),
                red=True,
            )
