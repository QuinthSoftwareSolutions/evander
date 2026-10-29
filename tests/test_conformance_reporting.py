from __future__ import annotations

import json
from pathlib import Path
import stat

import pytest

from conformance_support import build_report, write_report

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def isolate_nested_report_publication(monkeypatch):
    # Synthetic pytester projects must never publish over an operator's real
    # report. The fixture restores these overrides before parent publication.
    monkeypatch.delenv("EVANDER_EFFECT_CONFORMANCE_REPORT", raising=False)
    monkeypatch.delenv("EVANDER_TRIGGER_CONFORMANCE_REPORT", raising=False)


def test_report_requires_successful_evidence_and_preserves_details(tmp_path):
    matrix = {"one": ["a", "b"]}
    requirements = [
        {"requirement_id": "b", "status": "fail", "details": "provider error"},
        {"requirement_id": "a", "status": "pass", "details": "observed behavior"},
    ]
    report = build_report(requirements, matrix)
    assert report["status"] == "fail"
    assert report["missing_coverage"] == {"one": ["b"]}
    assert [entry["requirement_id"] for entry in report["requirements"]] == ["a", "b"]
    path = tmp_path / "nested" / "report.json"
    write_report(path, report)
    assert json.loads(path.read_text()) == report
    assert list(path.parent.iterdir()) == [path]


def test_report_cannot_pass_when_a_test_failed_after_recording_evidence():
    report = build_report(
        [{"requirement_id": "a", "status": "pass"}],
        {"one": ["a"]},
        failures=["test_a"],
    )
    assert report["status"] == "fail"
    assert report["test_failures"] == ["test_a"]
    assert report["missing_coverage"] == {}


def test_report_publication_preserves_operator_file_permissions(tmp_path):
    existing = tmp_path / "existing.json"
    existing.write_text("old")
    existing.chmod(0o640)
    write_report(existing, {"status": "pass"})
    assert stat.S_IMODE(existing.stat().st_mode) == 0o640

    reference = tmp_path / "reference.json"
    reference.write_text("ordinary new file")
    created = tmp_path / "new.json"
    write_report(created, {"status": "pass"})
    assert stat.S_IMODE(created.stat().st_mode) == stat.S_IMODE(reference.stat().st_mode)


def test_report_publication_failure_retains_previous_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_text('{"status": "old"}')

    def failed_replace(*_):
        raise OSError("publication failed")

    monkeypatch.setattr("conformance_support.os.replace", failed_replace)
    with pytest.raises(OSError, match="publication failed"):
        write_report(path, {"status": "new"})
    assert json.loads(path.read_text()) == {"status": "old"}
    assert list(tmp_path.iterdir()) == [path]


def _project(pytester, *, second_record=True, fail_first=False):
    support = Path(__file__).parent
    pytester.makeconftest(
        f"import sys\nsys.path.insert(0, {str(support)!r})\n"
        "pytest_plugins = ['suite_reporting']\n"
        "import suite_reporting\n"
        "suite_reporting.MATRICES = {'effect': {'behavior': ['a', 'b']}}\n"
    )
    pytester.makepyfile(
        test_effect_conformance_suite=f"""
def test_first(report_collector):
    report_collector["requirements"].append({{"requirement_id": "a", "status": "pass"}})
    {"assert False, 'deliberate failure'" if fail_first else "assert True"}

def test_second(report_collector):
    {('report_collector["requirements"].append({"requirement_id": "b", "status": "pass"})' if second_record else "assert True")}

def test_formatter():
    from conformance_support import build_report
    assert build_report([{{"requirement_id": "sample", "status": "pass"}}],
                        {{"format": ["sample"]}})["status"] == "pass"
"""
    )
    return [
        "-q",
        "--execution-ledger=execution.json",
        "--conformance-output-dir=reports",
    ]


@pytest.mark.parametrize("workers", [0, 2])
def test_conformance_aggregates_actual_evidence_in_any_order(pytester, workers):
    args = _project(pytester)
    result = pytester.runpytest_subprocess(
        *args,
        "-n",
        str(workers),
        "--dist=load",
        "test_effect_conformance_suite.py::test_formatter",
        "test_effect_conformance_suite.py::test_second",
        "test_effect_conformance_suite.py::test_first",
        timeout=45,
    )
    result.assert_outcomes(passed=3)
    assert result.ret == 0
    report = json.loads((pytester.path / "reports/effect-conformance-report.json").read_text())
    assert report["status"] == "pass"
    assert report["missing_coverage"] == {}
    assert [entry["requirement_id"] for entry in report["requirements"]] == ["a", "b"]
    ledger = json.loads((pytester.path / "execution.json").read_text())
    assert len(ledger["collected"]) == len(ledger["tests"]) == 3


@pytest.mark.parametrize("workers", [0, 2])
def test_complete_module_fails_if_requirement_evidence_is_removed(pytester, workers):
    args = _project(pytester, second_record=False)
    result = pytester.runpytest_subprocess(*args, "-n", str(workers), timeout=45)
    result.assert_outcomes(passed=3)
    assert result.ret == 1
    report = json.loads((pytester.path / "reports/effect-conformance-report.json").read_text())
    assert report["status"] == "fail"
    assert report["missing_coverage"] == {"behavior": ["b"]}


def test_selected_formatter_does_not_depend_on_other_tests(pytester):
    args = _project(pytester)
    result = pytester.runpytest_subprocess(
        *args,
        "test_effect_conformance_suite.py::test_formatter",
        timeout=30,
    )
    result.assert_outcomes(passed=1)
    assert result.ret == 0
    report = json.loads((pytester.path / "reports/effect-conformance-report.json").read_text())
    assert report["status"] == "fail"  # A partial run must not publish false conformance.
    assert report["missing_coverage"] == {"behavior": ["a", "b"]}


@pytest.mark.parametrize("workers", [0, 2])
@pytest.mark.parametrize("require_complete", [False, True])
def test_partial_selection_cannot_publish_pass_even_with_all_requirement_ids(
    pytester, workers, require_complete
):
    args = _project(pytester)
    if require_complete:
        args.append("--require-conformance=effect")
    result = pytester.runpytest_subprocess(
        *args,
        "-n",
        str(workers),
        "test_effect_conformance_suite.py::test_first",
        "test_effect_conformance_suite.py::test_second",
        timeout=45,
    )
    result.assert_outcomes(passed=2)
    assert result.ret == (1 if require_complete else 0)
    report = json.loads((pytester.path / "reports/effect-conformance-report.json").read_text())
    assert report["status"] == "fail"
    assert report["missing_coverage"] == {}
    assert report["missing_tests"] == ["test_effect_conformance_suite.py::test_formatter"]


def test_failed_test_cannot_publish_its_partial_evidence(pytester):
    args = _project(pytester, fail_first=True)
    result = pytester.runpytest_subprocess(*args, timeout=30)
    result.assert_outcomes(passed=2, failed=1)
    report = json.loads((pytester.path / "reports/effect-conformance-report.json").read_text())
    assert report["status"] == "fail"
    assert report["missing_coverage"] == {"behavior": ["a"]}
    assert report["requirements"] == [{"requirement_id": "b", "status": "pass"}]
