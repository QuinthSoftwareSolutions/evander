from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from evander.effects.providers.agent_create import AgentCreateEffectProvider
from evander.effects.providers.agent_destroy import AgentDestroyEffectProvider
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.execution import ExecutionStatusKind
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander_team_addon.services.release_verification_effects import (
    EffectReleaseVerificationExecutor,
)
from evander_team_addon.services.release_verification_runner import TeamReleaseVerificationRunner
from test_container_worker_session_image import _ContainerDispatchBackendStub, _context, _session
from test_team_addon_git_effects import _seed_release_verification, _seed_team


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout.strip()


class _LocalGitBackend(_ContainerDispatchBackendStub):
    """Exercise the real clone effect using local Git instead of a worker VM."""

    def __init__(self, checkout: Path) -> None:
        super().__init__()
        self.checkout = checkout
        self.logs = ""

    def clone_repo(self, **kwargs):
        self.clone_repo_requests.append(dict(kwargs))
        args = ["clone", "--no-local"]
        if kwargs["clone_branch"]:
            args.extend(["--branch", kwargs["clone_branch"]])
        _git(self.checkout.parent, *args, kwargs["repo_source"], str(self.checkout))
        return SimpleNamespace(
            cloned=True,
            target_dir=str(self.checkout),
            guest_repo_name=kwargs["repo_name"],
            remote_url=kwargs["repo_source"],
        )

    def start(self, spec):
        result = subprocess.run(
            ["bash", "-c", spec.command], capture_output=True, text=True, timeout=20
        )
        self.logs = result.stdout + result.stderr
        self.status = SimpleNamespace(
            status=(
                ExecutionStatusKind.STOPPED
                if result.returncode == 0
                else ExecutionStatusKind.FAILED
            ),
            message=self.logs,
            metadata={},
        )
        return super().start(spec)

    def get_log_snapshot(self, *args, **kwargs):
        return self.logs


@pytest.mark.parametrize(
    ("release_branch", "stored_ref", "fault"),
    [
        ("team/team-a/releases/one", "refs/evander/team/team-a/releases/one", None),
        ("refs/evander/team/team-a/releases/one", "refs/evander/team/team-a/releases/one", None),
        ("releases/one", "refs/heads/releases/one", None),
        ("team/team-a/releases/one", "refs/evander/team/team-a/releases/one", "moved"),
        ("team/team-a/releases/one", "refs/evander/team/team-a/releases/one", "missing"),
    ],
)
def test_verifier_clones_exact_release_ref_before_running_frozen_commands(
    tmp_path, monkeypatch, release_branch, stored_ref, fault
):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "--initial-branch=main")
    _git(source, "config", "user.name", "Test Developer")
    _git(source, "config", "user.email", "developer@example.invalid")
    content = source / "candidate.txt"
    content.write_text("base\n")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "Base")
    bare = tmp_path / "managed.git"
    _git(tmp_path, "clone", "--bare", str(source), str(bare))
    content.write_text("reviewed candidate\n")
    _git(source, "commit", "-am", "Candidate")
    candidate_sha = _git(source, "rev-parse", "HEAD")
    if fault == "moved":
        content.write_text("unreviewed successor\n")
        _git(source, "commit", "-am", "Successor")
    if fault != "missing":
        _git(source, "push", str(bare), f"HEAD:{stored_ref}")
    # The candidate is absent from the ordinary clone's main branch.
    assert _git(bare, "rev-parse", "refs/heads/main") != candidate_sha
    if stored_ref.startswith("refs/evander/"):
        assert "refs/heads/team/" not in _git(bare, "show-ref")

    runtime_dir = tmp_path / "runtime"
    repo = _seed_team(runtime_dir)
    _seed_release_verification(repo)

    def freeze_candidate(state):
        release = state.release_packages["release-1"]
        release.release_branch = release_branch
        release.candidate_commit_sha = candidate_sha
        release.manifest.release_branch = release_branch
        release.manifest.candidate_commit_sha = candidate_sha
        command = release.manifest.verification_commands[0]
        command.cwd = "."
        command.argv = ["git", "rev-parse", "HEAD"]
        state.release_verification_runs["verification-1"].candidate_commit_sha = candidate_sha
        return state

    repo.update(freeze_candidate)
    sessions = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    _session(sessions, session_id="verification-session")
    context = _context(runtime_dir, sessions)
    context.admin_service.resolve_managed_git_clone = lambda **kwargs: {
        "managed_repo_id": "mr_0123456789abcdefabcd",
        "clone_url": str(bare),
        "display_name": "repo",
        "default_branch": "main",
    }
    backend = _LocalGitBackend(tmp_path / "checkout")
    monkeypatch.setattr(
        "evander.effects.providers.agent_clone_repo.container_backend",
        lambda context, config: backend,
    )
    # Run the production Git setup script without sourcing a container user's
    # profile or altering the test host's HOME.
    monkeypatch.setattr(
        "evander.effects.providers.agent_clone_repo.container_shell_preamble",
        lambda: "set -euo pipefail\n",
    )
    monkeypatch.setattr(AgentCreateEffectProvider, "execute", lambda self: {"success": True})
    commands = []

    def execute(self):
        effect = self.effect_definition
        commands.append(list(effect.command))
        result = subprocess.run(
            effect.command,
            cwd=effect.working_directory,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", execute)
    cleanup = []

    def destroy(self):
        cleanup.append(self.effect_definition.session_id)
        return {"success": True}

    monkeypatch.setattr(AgentDestroyEffectProvider, "execute", destroy)
    TeamReleaseVerificationRunner(repo, EffectReleaseVerificationExecutor(context)).run(
        release_package_id="release-1",
        verification_run_id="verification-1",
        candidate_commit_sha=candidate_sha,
    )

    run = repo.read().release_verification_runs["verification-1"]
    assert cleanup == ["verification-session"]
    assert run.candidate_commit_sha == candidate_sha
    if fault:
        assert run.state == "failed"
        assert run.failure_kind == "infrastructure"
        assert run.correction_task_id is None
        assert run.command_results[0].state == "pending"
        assert commands == []
    else:
        assert run.state == "passed", run.error
        assert run.command_results[0].state == "passed"
        assert ["git", "rev-parse", "HEAD"] in commands
        assert _git(backend.checkout, "rev-parse", "HEAD") == candidate_sha
        assert _git(backend.checkout, "branch", "--show-current") == ""
        assert (backend.checkout / "candidate.txt").read_text() == "reviewed candidate\n"
        assert _git(backend.checkout, "config", "evander.gitAccessMode") == "read-only"
        assert _git(backend.checkout, "config", "evander.pushAgentAvailable") == "false"
