import importlib.util
import json
import sys
from pathlib import Path


def benchmark_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_fast_execution.py"
    spec = importlib.util.spec_from_file_location("browser_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_summarizes_all_attempts_including_failure_costs():
    benchmark = benchmark_module()
    rows = [
        {
            "strategy": "jev",
            "elapsed_seconds": seconds,
            "success": success,
            "model_calls": 2,
            "browser_calls": 3,
            "cost_usd": 0.01,
        }
        for seconds, success in [(1, True), (2, False), (9, True)]
    ]
    summary = benchmark.summarize(rows)["jev"]
    assert summary == {
        "attempts": 3,
        "success_rate": 2 / 3,
        "median_seconds": 2,
        "p95_seconds": 9,
        "model_calls": 6,
        "browser_calls": 9,
        "cost_usd": 0.03,
    }


def test_benchmark_verifies_actual_state_instead_of_trusting_claimed_success(tmp_path, monkeypatch):
    benchmark = benchmark_module()
    driver = tmp_path / "driver.py"
    driver.write_text(
        'import json,sys\nrequest=json.load(sys.stdin)\nprint(json.dumps({"success":True,"verified_state":{},"model_calls":1,"browser_calls":2,"cost_usd":0}))\n'
    )
    report = tmp_path / "benchmark.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["benchmark", "--output", str(report), "--repeats", "1", "--", sys.executable, str(driver)],
    )
    benchmark.main()
    result = json.loads(report.read_text())
    assert len(result["samples"]) == 9
    assert set(result["summary"]) == {"direct", "jev", "openai_decisions"}
    assert all(row["success"] is False for row in result["samples"])
