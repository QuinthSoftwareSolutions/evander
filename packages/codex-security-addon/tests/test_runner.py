import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from evander_codex_security_addon import runner


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    source = home / "repo"
    source.mkdir(parents=True)
    git(source, "init", "-q")
    git(source, "config", "user.name", "Synthetic")
    git(source, "config", "user.email", "synthetic@example.invalid")
    (source / "code.py").write_text("value = 1\n")
    git(source, "add", "code.py")
    git(source, "commit", "-qm", "Initial")
    first = git(source, "rev-parse", "HEAD")
    (source / "code.py").write_text("value = 2\n")
    git(source, "commit", "-qam", "Second")
    second = git(source, "rev-parse", "HEAD")
    monkeypatch.setattr(runner, "HOME", home)
    monkeypatch.setattr(runner, "SCAN_ROOT", tmp_path / "artifacts" / "security-scans")
    return source, first, second


def test_full_scan_pins_head_without_reading_or_modifying_dirty_work(repo):
    source, first, second = repo
    (source / "code.py").write_text("staged = True\n")
    git(source, "add", "code.py")
    (source / "code.py").write_text("unstaged = True\n")
    (source / "untracked.py").write_text("private working file\n")
    before = git(source, "status", "--porcelain=v1")
    index_digest = hashlib.sha256((source / ".git/index").read_bytes()).hexdigest()
    root = runner.scan_directory("1" * 32)
    target, base, head = runner.snapshot({"repo_path": str(source)}, root)
    assert head == second and base is None
    assert (target / "code.py").read_text() == "value = 2\n"
    assert not (target / "untracked.py").exists()
    assert git(source, "status", "--porcelain=v1") == before
    assert hashlib.sha256((source / ".git/index").read_bytes()).hexdigest() == index_digest
    assert git(source, "rev-parse", "HEAD") == second
    assert not git(target, "remote")


@pytest.mark.parametrize("selection", ["from", "to", "both", "root"])
def test_optional_commit_selection(repo, selection):
    source, first, second = repo
    args = {"repo_path": str(source)}
    if selection in {"from", "both"}:
        args["from_commit"] = first
    if selection in {"to", "both"}:
        args["to_commit"] = second
    if selection == "root":
        args["to_commit"] = first
    _, base, head = runner.snapshot(args, runner.scan_directory("2" * 32))
    assert base == (None if selection == "root" else first)
    assert head == (first if selection == "root" else second)


def test_repo_symlink_and_invalid_hash_rejected(repo, tmp_path):
    source, _, _ = repo
    link = source.parent / "linked"
    link.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="contained"):
        runner.snapshot({"repo_path": str(link)}, runner.scan_directory("3" * 32))
    with pytest.raises(ValueError, match="Invalid commit"):
        runner.snapshot(
            {"repo_path": str(source), "to_commit": "HEAD; invalid"},
            runner.scan_directory("4" * 32),
        )


def test_empty_diff_needs_no_model_and_produces_completed_receipt(repo, monkeypatch):
    source, _, second = repo
    monkeypatch.setattr(runner.os, "getpgrp", lambda: runner.os.getpid())
    runner.run(
        {
            "repo_path": str(source),
            "scan_id": "5" * 32,
            "mode": "standard",
            "from_commit": second,
            "to_commit": second,
        }
    )
    result = runner.status("5" * 32)
    assert result["status"] == "completed" and result["coverage"] == "complete"
    assert result["findings"] == []


@pytest.mark.parametrize("outcome", ["completed", "partial", "timeout", "invalid_contract"])
def test_native_history_and_execution_outcome_retained_before_cleanup(repo, monkeypatch, outcome):
    source, _, head = repo
    scan_id = "a" * 32
    root = runner.scan_directory(scan_id)
    session = root / "codex-home/sessions/day"
    session.mkdir(parents=True)
    (session / "rollout.jsonl").write_text('{"event":"private native history"}\n')
    (root / "codex-home/auth.json").write_text("never retain auth")

    class Child:
        pid = 987654321
        code = None

        def poll(self):
            return self.code

        def wait(self, timeout=None):
            if outcome == "timeout" and self.code is None:
                self.code = -15
                raise subprocess.TimeoutExpired("synthetic scanner", timeout)
            self.code = self.code if self.code is not None else 0
            return self.code

    def canonical(*args):
        if outcome == "invalid_contract":
            raise ValueError("sealed report missing")
        return {
            "status": "failed" if outcome == "partial" else "completed",
            "coverage": "partial" if outcome == "partial" else "complete",
        }

    monkeypatch.setattr(runner.os, "getpgrp", runner.os.getpid)
    monkeypatch.setattr(runner.os, "killpg", lambda *args: None)
    monkeypatch.setattr(runner, "snapshot", lambda *args: (source, None, head))
    monkeypatch.setattr(runner, "git", lambda *args: "")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout=json.dumps({"plugin_root": "/unused", "environment": {}})
        ),
    )
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: Child())
    monkeypatch.setattr(runner, "canonical_result", canonical)
    runner.run(
        {"scan_id": scan_id, "repo_path": str(source), "mode": "standard", "timeout_seconds": 10}
    )
    assert not (root / "codex-home").exists()
    assert (
        "private native history"
        in (root / "diagnostics/native/sessions/day/rollout.jsonl").read_text()
    )
    assert not list((root / "diagnostics").rglob("auth.json"))
    execution = runner.read_json(root / "execution.json")
    assert (
        execution["status"]
        == {
            "completed": "completed",
            "partial": "failed",
            "timeout": "timed_out",
            "invalid_contract": "failed",
        }[outcome]
    )
    assert execution["child_exit_code"] == (-15 if outcome == "timeout" else 0)
    if outcome == "timeout":
        assert execution["termination_reason"] == "timeout"
    if outcome == "invalid_contract":
        assert execution["exception_class"] == "ValueError"


def test_native_capture_caps_files_and_skips_linked_evidence(repo):
    root = runner.scan_directory("b" * 32)
    sessions = root / "codex-home/sessions"
    sessions.mkdir(parents=True)
    oversized = sessions / "rollout.jsonl"
    oversized.write_bytes(b"x" * (9 * 1024 * 1024) + b"final event")
    outside = root / "outside.log"
    outside.write_text("must not copy")
    (sessions / "linked.log").symlink_to(outside)
    os.link(outside, sessions / "hard.log")
    os.mkfifo(sessions / "pipe.log")
    receipt = runner.capture_native_diagnostics(root)
    copies = [p for p in (root / "diagnostics").rglob("*") if p.is_file()]
    assert [p.name for p in copies] == ["rollout.jsonl"]
    assert copies[0].stat().st_size == 8 * 1024 * 1024
    assert copies[0].read_bytes().endswith(b"final event")
    assert receipt["truncated"]


def test_public_files_never_include_raw_history_logs_or_directory_sweeps(repo):
    root = runner.scan_directory("c" * 32)
    expected = [
        "result.json",
        "target.json",
        "reports/scan/report.md",
        "reports/scan/findings.json",
        "reports/scan/coverage.json",
        "reports/scan/scan-manifest.json",
        "reports/scan/exports/results.sarif",
        "reports/scan/checkpoints/commit.json",
        "reports/scan/findings/missing-check/missing-check.md",
        "reports/scan/hardening/hardening.md",
    ]
    for name in [
        *expected,
        "scan.log",
        "launcher.log",
        "execution.json",
        "diagnostics/native/sessions/rollout.jsonl",
        "reports/scan/debug.log",
    ]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic")
    files = runner.public_report_files(root)
    assert set(files) == {
        str((root / name).relative_to(runner.SCAN_ROOT.parent)) for name in expected
    }
    assert not (runner.scan_directory("5" * 32) / "checkout").exists()


def test_unsealed_artifact_never_becomes_success(repo):
    root = runner.scan_directory("6" * 32)
    reports = root / "reports"
    reports.mkdir()
    (reports / "scan-manifest.json").write_text(
        json.dumps({"scan": {"id": "candidate", "status": "completed"}})
    )
    with pytest.raises(ValueError, match="sealed result"):
        runner.canonical_result(root, root / "unused-plugin", {"mode": "standard"})


def test_snapshot_includes_locally_available_unreferenced_commit(repo):
    source, first, _ = repo
    git(source, "checkout", "--detach", first)
    (source / "detached.py").write_text("detached = True\n")
    git(source, "add", "detached.py")
    git(source, "commit", "-qm", "Detached revision")
    detached = git(source, "rev-parse", "HEAD")
    git(source, "checkout", "-")
    target, _, head = runner.snapshot(
        {"repo_path": str(source), "to_commit": detached}, runner.scan_directory("7" * 32)
    )
    assert head == detached
    assert (target / "detached.py").read_text() == "detached = True\n"


def test_status_does_not_check_process_ids_in_another_container(repo):
    root = runner.scan_directory("8" * 32)
    runner.write_json(root / "process.json", {"pid": 99999999, "start": "1"})
    assert runner.status("8" * 32, check_process=False) == {"status": "running"}
    assert runner.status("8" * 32, check_process=True)["status"] == "failed"


def test_direct_runner_uses_session_token_without_executing_profile(repo, monkeypatch):
    source, _, _ = repo
    env_file = source.parent / ".evander/session.env"
    env_file.parent.mkdir()
    env_file.write_text("EVANDER_MCP_SESSION_TOKEN='synthetic scoped token'\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EVANDER_MCP_SESSION_TOKEN", raising=False)
    runner.load_session_environment()
    assert runner.os.environ["OPENAI_API_KEY"] == "synthetic scoped token"


@pytest.fixture
def sealed_report(repo):
    plugin = (
        Path(__file__).resolve().parents[1] / "node_modules/@openai/codex-security/_bundled_plugin"
    )
    if not plugin.is_dir():
        pytest.skip("Run npm ci to test the pinned provider artifact contract")
    root = runner.scan_directory("9" * 32)
    reports = root / "reports/scan"
    shutil.copytree(plugin / "examples/completed-scan", reports)
    runner.write_json(root / "target.json", {"from_commit": None, "to_commit": "deadbeef"})
    return root, plugin, reports


def test_native_sealed_report_maps_stable_finding_ids_and_severity(sealed_report):
    root, plugin, _ = sealed_report
    result = runner.canonical_result(root, plugin, {"mode": "standard"})
    assert result["status"] == "completed"
    assert result["coverage"] == "complete"
    assert result["findings"][0]["id"] == "csf_852f90d6e1177502ff113d4a"
    assert result["findings"][0]["occurrence_id"] == "occ_e79cb19591e696572a1c22be"
    assert result["severity_counts"]["high"] == 1
    assert (
        result["artifacts"]["report.md"] == "security-scans/" + "9" * 32 + "/reports/scan/report.md"
    )


def test_sealed_report_for_another_commit_is_rejected(sealed_report):
    root, plugin, _ = sealed_report
    runner.write_json(root / "target.json", {"from_commit": None, "to_commit": "a" * 40})
    with pytest.raises(ValueError, match="does not match"):
        runner.canonical_result(root, plugin, {"mode": "standard"})


def test_tampered_sealed_findings_are_rejected(sealed_report):
    root, plugin, reports = sealed_report
    findings = reports / "findings.json"
    findings.write_text(findings.read_text().replace("Unsafe archive", "Modified archive"))
    with pytest.raises(ValueError):
        runner.canonical_result(root, plugin, {"mode": "standard"})


@pytest.fixture
def sealed_diff_report(sealed_report, repo):
    """Build committed coverage using the pinned producer's sealed contract."""
    root, plugin, reports = sealed_report
    _, base, head = repo
    runner.write_json(root / "target.json", {"from_commit": base, "to_commit": head})

    def configure(mode="branch_diff", *, reported_base=None, reported_head=None):
        coverage = runner.read_json(reports / "coverage.json")
        coverage.update(mode=mode, inventoryStrategy="diff")
        runner.write_json(reports / "coverage.json", coverage)
        manifest = runner.read_json(reports / "scan-manifest.json")
        target = manifest["scan"]["target"]
        target.pop("revision", None)
        target.update(
            kind="git_diff", baseRevision=reported_base or base, headRevision=reported_head or head
        )
        for artifact in manifest["scan"]["artifacts"]:
            if artifact["path"] == "coverage.json":
                artifact["sha256"] = hashlib.sha256(
                    (reports / "coverage.json").read_bytes()
                ).hexdigest()
        runner.write_json(reports / "scan-manifest.json", manifest)
        return root, plugin, base, head

    return configure


@pytest.mark.parametrize("mode", ["diff", "commit", "branch_diff"])
def test_sealed_committed_diff_accepts_native_coverage_modes(sealed_diff_report, mode):
    root, plugin, base, head = sealed_diff_report(mode)
    result = runner.canonical_result(root, plugin, {"mode": "standard"})
    assert result["status"] == "completed" and result["coverage"] == "complete"
    assert result["from_commit"] == base and result["to_commit"] == head
    assert result["severity_counts"]["high"] == 1


@pytest.mark.parametrize("mode", ["repository", "working_tree"])
def test_committed_diff_rejects_other_coverage_modes(sealed_diff_report, mode):
    root, plugin, _, _ = sealed_diff_report(mode)
    with pytest.raises(ValueError, match="Expected committed diff coverage"):
        runner.canonical_result(root, plugin, {"mode": "standard"})


@pytest.mark.parametrize("field", ["reported_base", "reported_head"])
def test_committed_diff_rejects_incorrect_sealed_bounds(sealed_diff_report, field):
    root, plugin, _, _ = sealed_diff_report(**{field: "a" * 40})
    with pytest.raises(ValueError, match="does not match the requested commits"):
        runner.canonical_result(root, plugin, {"mode": "standard"})


def test_cancel_stops_only_the_matching_scan_process_group(repo):
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        root = runner.scan_directory("a" * 32)
        runner.write_json(
            root / "process.json",
            {
                "pid": unrelated.pid,
                "start": "wrong-process-generation",
                "child_pid": child.pid,
                "child_start": runner.process_start(child.pid),
            },
        )
        assert runner.cancel("a" * 32)["status"] == "cancelled"
        assert child.wait(timeout=2) < 0
        assert unrelated.poll() is None
    finally:
        for process in (child, unrelated):
            if process.poll() is None:
                process.kill()
            process.wait()


def test_cancel_before_start_is_terminal_for_run_and_launch(repo, monkeypatch):
    source, _, _ = repo
    request = {"scan_id": "b" * 32, "repo_path": str(source), "mode": "standard"}
    monkeypatch.setattr(runner.os, "getpgrp", runner.os.getpid)
    monkeypatch.setattr(runner, "snapshot", lambda *args: pytest.fail("Cancelled scan started"))
    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Cancelled scan launched")
    )
    receipt = runner.cancel(request["scan_id"])
    runner.run(request)
    assert runner.launch(request) == receipt
    assert runner.cancel(request["scan_id"]) == receipt


def test_cancel_waits_for_startup_registration_and_cannot_be_overwritten(repo, monkeypatch):
    source, _, _ = repo
    scan_id = "c" * 32
    publishing = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    original_write = runner.write_json
    results = []

    def write(path, value):
        if path.name == "process.json":
            publishing.set()
            assert release.wait(5)
        original_write(path, value)

    def snapshot(*args):
        assert cancelled.wait(5)
        raise RuntimeError("Stopped during preparation")

    def cancel():
        results.append(runner.cancel(scan_id))
        cancelled.set()

    monkeypatch.setattr(runner.os, "getpgrp", runner.os.getpid)
    monkeypatch.setattr(runner, "write_json", write)
    monkeypatch.setattr(runner, "snapshot", snapshot)
    monkeypatch.setattr(runner, "terminate_group", lambda *args: None)
    worker = threading.Thread(
        target=runner.run,
        args=({"scan_id": scan_id, "repo_path": str(source), "mode": "standard"},),
    )
    canceller = threading.Thread(target=cancel)
    worker.start()
    try:
        assert publishing.wait(5)
        canceller.start()
        assert not cancelled.wait(0.1)
    finally:
        release.set()
        worker.join(5)
        canceller.join(5)
    assert not worker.is_alive() and not canceller.is_alive()
    assert results[0]["status"] == "cancelled"
    assert runner.status(scan_id) == results[0]


def test_cancel_coordinates_detached_child_creation_and_terminal_commit(repo, monkeypatch):
    source, _, head = repo
    scan_id = "d" * 32
    spawning = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    cancelled = threading.Event()
    terminated = []
    results = []

    class Child:
        pid = 987654321

        def wait(self, **kwargs):
            assert stopped.wait(5)
            return 0

        def poll(self):
            return 0 if stopped.is_set() else None

    def popen(*args, **kwargs):
        spawning.set()
        assert release.wait(5)
        return Child()

    def terminate(pid, started):
        terminated.append(pid)
        stopped.set()

    def cancel():
        results.append(runner.cancel(scan_id))
        cancelled.set()

    monkeypatch.setattr(runner.os, "getpgrp", runner.os.getpid)
    monkeypatch.setattr(runner, "snapshot", lambda *args: (source, None, head))
    monkeypatch.setattr(runner, "git", lambda *args: "")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps({"plugin_root": "/unused", "environment": {}})
        ),
    )
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner, "process_start", lambda pid: "generation")
    monkeypatch.setattr(runner, "terminate_group", terminate)
    monkeypatch.setattr(runner, "canonical_result", lambda *args: {"status": "completed"})
    worker = threading.Thread(
        target=runner.run,
        args=(
            {
                "scan_id": scan_id,
                "repo_path": str(source),
                "mode": "standard",
                "timeout_seconds": 10,
            },
        ),
    )
    canceller = threading.Thread(target=cancel)
    worker.start()
    try:
        assert spawning.wait(5)
        canceller.start()
        assert not cancelled.wait(0.1)
    finally:
        release.set()
        worker.join(5)
        canceller.join(5)
    assert not worker.is_alive() and not canceller.is_alive()
    assert terminated[0] == Child.pid
    assert results[0]["status"] == "cancelled"
    assert runner.status(scan_id) == results[0]


def test_cancel_after_completion_preserves_the_result(repo, monkeypatch):
    root = runner.scan_directory("e" * 32)
    result = {"status": "completed", "findings": [{"id": "known-finding"}]}
    runner.write_json(root / "result.json", result)
    monkeypatch.setattr(
        runner, "terminate_group", lambda *args: pytest.fail("Completed scan was terminated")
    )
    assert runner.cancel("e" * 32) == result


def test_staged_request_is_read_as_data_inside_session_and_dispatched(repo, monkeypatch):
    from types import SimpleNamespace
    import sys

    source, _, _ = repo
    captured = []
    monkeypatch.setitem(
        sys.modules,
        "coverage_runner",
        SimpleNamespace(run_task=lambda value: captured.append(value)),
    )
    request = {"scan_id": "a" * 32, "coverage_task": {"paths": ["space and\nnewline.py"]}}
    staged = source.parent / "request.json"
    staged.write_text(json.dumps(request))
    runner.run({"_request_file": str(staged)})
    assert captured == [request]
    with pytest.raises(ValueError, match="escapes"):
        runner.run({"_request_file": "/etc/hosts"})


@pytest.mark.parametrize(
    "interruption", ["before_validation", "after_validation", "changed_head", "dirty"]
)
def test_interrupted_scoped_sdk_result_recovers_only_with_unchanged_snapshot(
    repo, monkeypatch, interruption
):
    import shutil
    from evander_codex_security_addon import coverage_runner

    monkeypatch.setattr(coverage_runner, "runner", runner)
    source, first, second = repo
    scan_id = "e" * 32
    root = runner.scan_directory(scan_id)
    request = {
        "scan_id": scan_id,
        "repo_path": str(source),
        "snapshot_commit": second,
        "coverage_task": {"paths": ["app.py"]},
    }
    runner.write_json(root / "request.json", request)
    target, _, _ = runner.snapshot(request, root)
    saved = {
        "status": "completed",
        "coverage": "complete",
        "coverage_unit": {"kind": "files", "paths": ["app.py"], "revision": second},
    }
    runner.write_json(root / "sdk-result.json", saved)
    if interruption == "after_validation":
        coverage_runner.validate_snapshot(root, request)
        shutil.rmtree(target)
    elif interruption == "changed_head":
        git(target, "checkout", "--detach", first)
    elif interruption == "dirty":
        (target / "untracked-change.txt").write_text("unexpected reviewer write")
    result = coverage_runner.recover(scan_id)
    if interruption in {"changed_head", "dirty"}:
        assert result["status"] == "failed"
        assert "coverage_unit" not in result
    else:
        assert result["status"] == "completed"
        assert result["coverage_unit"] == saved["coverage_unit"]
        assert result["recovered_from_saved_sdk_result"]
        assert runner.read_json(root / "result.json") == result
