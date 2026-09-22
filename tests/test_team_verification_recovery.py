import json
from types import SimpleNamespace

import pytest

from evander_team_addon.models import ReleaseVerificationCommand, ReleaseVerificationCommandResult
from evander_team_addon.services.release_verification_runner import TeamReleaseVerificationRunner
from evander_team_addon.services.verification_evidence import (
    RESULT_PREFIX,
    read_verification_evidence,
)
from test_team_addon_git_effects import (
    _seed_team,
    _seed_release_verification,
    _stub_release_verifier_setup,
    _verify_release_provider,
)


def test_interrupted_sequence_restarts_prerequisites_in_new_generation(tmp_path, monkeypatch):
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)

    def interrupt(state):
        run = state.release_verification_runs["verification-1"]
        run.state = "running"
        run.execution_generation = 1
        run.command_results[0] = ReleaseVerificationCommandResult(
            name="frozen-check",
            state="passed",
            execution_generation=1,
            exit_code=0,
        )
        command = ReleaseVerificationCommand(name="dependent", argv=["python", "dependent.py"])
        state.release_packages["release-1"].manifest.verification_commands.append(command)
        run.command_results.append(
            ReleaseVerificationCommandResult(
                name="dependent", state="running", execution_generation=1
            )
        )
        return state

    repo.update(interrupt)
    commands, _, _ = _stub_release_verifier_setup(monkeypatch, destroy_result={"success": True})
    assert _verify_release_provider(tmp_path)["success"]
    run = repo.read().release_verification_runs["verification-1"]
    assert run.execution_generation == 2
    assert run.session_id != "verification-session"
    assert run.generation_history[0]["command_results"][0]["state"] == "passed"
    assert commands.index(["python", "-m", "frozen_check"]) < commands.index(
        ["python", "dependent.py"]
    )


def test_recovery_exhaustion_blocks_without_candidate_correction(tmp_path):
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)

    def interrupt(state):
        run = state.release_verification_runs["verification-1"]
        run.execution_generation = 3
        run.command_results[0].execution_generation = 3
        run.state = "running"
        return state

    repo.update(interrupt)
    executor = SimpleNamespace(destroy_verifier=lambda **kwargs: None)
    TeamReleaseVerificationRunner(repo, executor).run(
        release_package_id="release-1",
        verification_run_id="verification-1",
        candidate_commit_sha="candidate-sha",
    )
    run = repo.read().release_verification_runs["verification-1"]
    assert run.failure_kind == "infrastructure"
    assert run.correction_task_id is None
    assert "limit exhausted" in run.error


def evidence(**updates):
    return RESULT_PREFIX + json.dumps(
        dict(
            schema_version=1,
            outcome="baseline_exception",
            explanation="Same observed failures",
            candidate_commit_sha="a" * 40,
            base_commit_sha="b" * 40,
            artifact_refs=["/artifacts/comparison.json"],
            **updates,
        )
    )


@pytest.mark.parametrize(
    "change", ["not_opted_in", "missing", "duplicate", "wrong_candidate", "wrong_base", "no_refs"]
)
def test_evidence_cannot_grant_unproven_exception(change):
    command = ReleaseVerificationCommand(
        name="check",
        argv=["check"],
        result_protocol="json-v1",
        allow_baseline_exceptions=change != "not_opted_in",
    )
    payload = json.loads(evidence().removeprefix(RESULT_PREFIX))
    if change == "wrong_candidate":
        payload["candidate_commit_sha"] = "c" * 40
    if change == "wrong_base":
        payload["base_commit_sha"] = "c" * 40
    if change == "no_refs":
        payload["artifact_refs"] = []
    output = RESULT_PREFIX + json.dumps(payload)
    if change == "missing":
        output = ""
    if change == "duplicate":
        output += "\n" + output
    with pytest.raises(ValueError):
        read_verification_evidence(
            output, command=command, candidate_commit_sha="a" * 40, base_commit_sha="b" * 40
        )


def test_explicit_baseline_policy_accepts_bound_evidence():
    command = ReleaseVerificationCommand(
        name="check", argv=["check"], result_protocol="json-v1", allow_baseline_exceptions=True
    )
    assert (
        read_verification_evidence(
            evidence(), command=command, candidate_commit_sha="a" * 40, base_commit_sha="b" * 40
        ).outcome
        == "baseline_exception"
    )


def run_structured(repo, output, *, artifacts=None):
    def configure(state):
        release = state.release_packages["release-1"]
        release.manifest.base_commit_sha = "b" * 40
        release.manifest.verification_commands[0] = ReleaseVerificationCommand(
            name="frozen-check",
            argv=["check"],
            result_protocol="json-v1",
            allow_baseline_exceptions=True,
            command_failure_action="block",
        )
        return state

    repo.update(configure)
    calls = []

    def execute(**kwargs):
        calls.append(kwargs["command"].argv)
        kwargs["on_started"]("structured-job")
        return output

    def artifact(**kwargs):
        calls.append("retain")
        if artifacts is None:
            raise FileNotFoundError("missing report")
        return artifacts

    executor = SimpleNamespace(
        create_verifier=lambda **kwargs: None,
        clone_candidate=lambda **kwargs: "/repo",
        pin_candidate=lambda **kwargs: None,
        execute_command=execute,
        read_artifact=artifact,
        destroy_verifier=lambda **kwargs: calls.append("destroy"),
    )
    TeamReleaseVerificationRunner(repo, executor).run(
        release_package_id="release-1",
        verification_run_id="verification-1",
        candidate_commit_sha="candidate-sha",
    )
    return repo.read().release_verification_runs["verification-1"], calls


@pytest.mark.parametrize(
    "raw_success,report_outcome,expected",
    [
        (True, "baseline_exception", "passed"),
        (False, "baseline_exception", "failed"),
        (True, "failed", "failed"),
        (True, "infrastructure", "failed"),
    ],
)
def test_structured_execution_and_policy_outcomes_remain_separate(
    tmp_path, raw_success, report_outcome, expected
):
    from evander_team_addon.services.verification_logs import read_verification_log

    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    output = RESULT_PREFIX + json.dumps(
        dict(
            schema_version=1,
            outcome=report_outcome,
            explanation="Compared pinned revisions",
            candidate_commit_sha="candidate-sha",
            base_commit_sha="b" * 40,
            artifact_refs=["/artifacts/comparison.json"],
        )
    )
    run, calls = run_structured(
        repo,
        dict(
            success=raw_success,
            failure_kind="command",
            exit_code=0 if raw_success else 1,
            stdout=output,
        ),
        artifacts='{"comparison":"retained", "token":"secret-value"}',
    )
    assert run.state == expected
    assert run.correction_task_id is None  # The frozen command requests block, not correction.
    result = run.command_results[0]
    assert result.execution_outcome == ("success" if raw_success else "command_failure")
    assert result.policy_outcome != "baseline_exception" or run.state == "passed"
    assert calls.index("retain") < calls.index("destroy")
    assert "EVANDER_VERIFICATION_BASE_SHA=" + "b" * 40 in calls[0]
    assert "comparison" in read_verification_log(tmp_path, "structured-job")
    assert "secret-value" not in read_verification_log(tmp_path, "structured-job")


def test_missing_artifact_blocks_baseline_exception(tmp_path):
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    output = evidence().replace("a" * 40, "candidate-sha")
    run, _ = run_structured(repo, dict(success=True, exit_code=0, stdout=output))
    assert run.failure_kind == "infrastructure"
    assert run.command_results[0].policy_outcome == "infrastructure"


def test_worker_failure_cannot_be_overridden_by_success_report(tmp_path):
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    run, calls = run_structured(
        repo,
        dict(
            success=False,
            failure_kind="infrastructure",
            stdout=evidence().replace("a" * 40, "candidate-sha"),
        ),
    )
    assert run.failure_kind == "infrastructure"
    assert "retain" not in calls


def test_old_generation_progress_and_final_result_are_fenced(tmp_path):
    from evander_team_addon.services.release_gate_service import TeamReleaseGateService

    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)

    def advance(state):
        run = state.release_verification_runs["verification-1"]
        run.state = "running"
        run.execution_generation = 2
        run.command_results[0].execution_generation = 2
        return state

    repo.update(advance)
    revision = repo.read().revision
    service = TeamReleaseGateService(repo)
    identity = dict(
        release_package_id="release-1",
        verification_run_id="verification-1",
        candidate_commit_sha="candidate-sha",
    )
    old = dict(name="frozen-check", state="passed", execution_generation=1)
    with pytest.raises(ValueError, match="stale"):
        service.record_verification_command_progress(
            **identity, command_index=0, command_result=old
        )
    with pytest.raises(ValueError, match="stale"):
        service.record_verification_result(
            **identity, verification_state="passed", command_results=[old], execution_generation=1
        )
    assert repo.read().revision == revision


def test_retained_logs_redact_credentials_expire_and_survive_session_cleanup(tmp_path, monkeypatch):
    from evander_team_addon.services import verification_logs as logs

    monkeypatch.setenv("VERIFIER_API_TOKEN", "env-secret-value")
    key = logs.retain_verification_log(
        tmp_path,
        job_id="job/../../x",
        text="Bearer abcdef token=hidden https://user:pass@host env-secret-value",
        retention_days=1,
    )
    path = tmp_path / "release-verification-logs" / f"{key}.json.gz"
    assert path.stat().st_mode & 0o777 == 0o600
    output = logs.read_verification_log(tmp_path, "job/../../x")
    assert all(
        secret not in output for secret in ["abcdef", "hidden", "user:pass", "env-secret-value"]
    )
    monkeypatch.setattr(logs.time, "time", lambda: 1e20)
    assert logs.read_verification_log(tmp_path, "job/../../x") is None
    assert logs.prune_verification_logs(tmp_path) == 1
    assert not path.exists()


def test_cancellation_does_not_start_remaining_commands(tmp_path):
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)

    def sequence(state):
        release = state.release_packages["release-1"]
        release.manifest.verification_commands.append(
            ReleaseVerificationCommand(name="never-run", argv=["later"])
        )
        state.release_verification_runs["verification-1"].command_results.append(
            ReleaseVerificationCommandResult(name="never-run")
        )
        return state

    repo.update(sequence)
    calls = []

    def execute(**kwargs):
        calls.append(kwargs["command"].name)

        def cancel(state):
            from evander_team_addon.services.domain.release_cancellation import cancel_release

            cancel_release(
                state,
                release=state.release_packages["release-1"],
                actor_id="operator:test",
                reason="Stop verification",
            )
            return state

        repo.update(cancel)
        return {"success": True, "exit_code": 0, "stdout": "done"}

    executor = SimpleNamespace(
        create_verifier=lambda **kwargs: None,
        clone_candidate=lambda **kwargs: "/repo",
        pin_candidate=lambda **kwargs: None,
        execute_command=execute,
        destroy_verifier=lambda **kwargs: calls.append("cleanup"),
    )
    result = TeamReleaseVerificationRunner(repo, executor).run(
        release_package_id="release-1",
        verification_run_id="verification-1",
        candidate_commit_sha="candidate-sha",
    )
    assert calls == ["frozen-check", "cleanup"]
    assert result["verification_state"] == "cancelled"


def test_cli_reads_retained_logs_without_worker_or_session(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from evander import cli
    from evander_team_addon.services.verification_logs import retain_verification_log

    retain_verification_log(
        tmp_path, job_id="removed-worker-job", text="first\nlast", retention_days=1
    )
    monkeypatch.setattr(
        cli,
        "_build_execution_backend_for_kind",
        lambda *args, **kwargs: pytest.fail("must not contact a deleted worker"),
    )
    result = CliRunner().invoke(
        cli.app,
        ["execution", "logs", "--runtime-dir", str(tmp_path), "removed-worker-job", "--lines", "1"],
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "last"
