from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from evander.code_index import runner
from evander.code_index.models import IndexSettings, TOOLCHAIN


@pytest.fixture
def npm_repo(tmp_path, monkeypatch):
    if shutil.which("npm") is None:
        pytest.skip("npm is required for the offline workspace restore regression")
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("npm_config_cache", str(tmp_path / "npm-cache"))
    monkeypatch.setenv("npm_config_offline", "true")
    monkeypatch.setenv("npm_config_registry", "http://127.0.0.1:1")
    monkeypatch.setenv("npm_config_userconfig", str(tmp_path / "empty-npmrc"))
    (tmp_path / "empty-npmrc").write_text("")

    def write(path, payload):
        destination = repo / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload))

    write("package.json", {"name": "root", "private": True, "workspaces": ["packages/*"]})
    write("packages/shared/package.json", {"name": "shared", "version": "1.0.0"})
    write(
        "packages/client/package.json",
        {
            "name": "client",
            "version": "1.0.0",
            "dependencies": {"shared": "1.0.0"},
        },
    )
    write("packages/client/tsconfig.json", {})
    write("packages/client/tsconfig.test.json", {})
    (repo / "packages/client/index.ts").write_text("export const value = 1;\n")
    return repo, write


def index_fixture(repo, tmp_path, monkeypatch, *, projects=None, npm_restore="locked"):
    def git(*args):
        return (
            subprocess.check_output(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "commit.gpgsign=false",
                    "-c",
                    "user.name=Index test",
                    "-c",
                    "user.email=index@example.test",
                    *args,
                ],
                cwd=repo,
                stderr=subprocess.STDOUT,
                timeout=10,
            )
            .decode()
            .strip()
        )

    git("init", "--initial-branch=main")
    git("add", "package.json", "package-lock.json", "packages")
    git("commit", "-m", "Workspace fixture")
    sha = git("rev-parse", "HEAD")
    installs, indexes = [], []
    original_run, original_output = runner.run, subprocess.check_output

    def command_output(command, **kwargs):
        if command[0] in {"scip-dotnet", "scip-typescript"}:
            return TOOLCHAIN[command[0]].encode()
        return original_output(command, **kwargs)

    def run(command, **kwargs):
        if command[0] == "scip-typescript":
            indexes.append(command[2])
            # Minimal SCIP document: source path only. Compiler behavior is outside this test.
            path = b"packages/client/index.ts"
            document = b"\x0a" + bytes([len(path)]) + path
            Path(command[-1]).write_bytes(b"\x12" + bytes([len(document)]) + document)
        else:
            installs.append((command, kwargs["cwd"]))
            original_run(command, **kwargs)

    monkeypatch.setattr(runner.subprocess, "check_output", command_output)
    monkeypatch.setattr(runner, "run", run)
    artifact = runner.build_artifact(
        repo,
        sha,
        IndexSettings(semantic=False, typescript_projects=projects or [], npm_restore=npm_restore),
        tmp_path / "artifacts" / "code-index.json",
    )
    return artifact, installs, indexes


def lock_workspace(repo):
    subprocess.run(
        ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "declaration",
    [
        ["packages/*"],
        {"packages": ["packages/*"]},
        ["packages/{client,shared}"],
        ["packages/**", "!packages/excluded"],
    ],
)
def test_workspace_projects_share_root_lock_and_single_restore(
    npm_repo, tmp_path, monkeypatch, declaration
):
    repo, write = npm_repo
    write("package.json", {"name": "root", "private": True, "workspaces": declaration})
    lock_workspace(repo)
    assert not (repo / "node_modules").exists()
    artifact, installs, indexes = index_fixture(repo, tmp_path, monkeypatch)
    assert len(indexes) == 2
    assert [p["status"] for p in artifact.projects] == ["ready", "ready"]
    assert len(installs) == 1
    assert installs[0][0][1] == "ci"
    assert installs[0][1] == repo
    assert (repo / "node_modules/shared/package.json").is_file()
    expected = hashlib.sha256((repo / "package-lock.json").read_bytes()).hexdigest()
    assert all(p["dependency_lock_sha256:."] == expected for p in artifact.projects)
    assert not (repo / "packages/client/package-lock.json").exists()


@pytest.mark.parametrize("restore", ["locked", "install"])
def test_non_workspace_nested_package_needs_own_restore(npm_repo, tmp_path, monkeypatch, restore):
    repo, write = npm_repo
    write(
        "package.json",
        {
            "name": "root",
            "private": True,
            "workspaces": ["packages/*", "!packages/excluded"],
        },
    )
    write(
        "packages/excluded/package.json",
        {
            "name": "excluded",
            "version": "1.0.0",
            "dependencies": {"shared": "file:../shared"},
        },
    )
    write("packages/excluded/tsconfig.json", {})
    lock_workspace(repo)
    artifact, installs, indexes = index_fixture(
        repo,
        tmp_path,
        monkeypatch,
        projects=["packages/excluded/tsconfig.json"],
        npm_restore=restore,
    )
    if restore == "locked":
        assert not indexes
        assert artifact.projects[0]["status"] == "failed"
        assert len(installs) == 1
    else:
        assert indexes == ["packages/excluded/tsconfig.json"]
        assert artifact.projects[0]["status"] == "ready"
        assert installs[-1][0][1] == "install"
        assert installs[-1][1] == repo / "packages/excluded"
        assert (repo / "packages/excluded/node_modules/shared/package.json").is_file()


def test_workspace_query_failure_does_not_claim_successful_restore(npm_repo, tmp_path, monkeypatch):
    repo, _ = npm_repo
    lock_workspace(repo)

    def unavailable(*args):
        raise subprocess.CalledProcessError(1, ["npm", "query"])

    monkeypatch.setattr(runner, "npm_workspaces", unavailable)
    artifact, _, indexes = index_fixture(repo, tmp_path, monkeypatch)
    assert not indexes
    assert all(p["status"] == "failed" for p in artifact.projects)
