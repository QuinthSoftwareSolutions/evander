from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from evander.code_index import runner
from evander.admin.services.repository_index_service import RepositoryIndexService
from evander.code_index.importer import import_artifact
from evander.code_index.models import IndexSettings, TOOLCHAIN
from evander.code_index.portable_query import navigate, open_index, search
from evander.code_index.scip import _encode_field, read_scip
from test_repository_index import ManagedGit, REPO


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
            # Minimal SCIP document with one definition. Compiler behavior is outside this test.
            path = b"packages/client/index.ts"
            occurrence = (
                _encode_field(1, bytes([0, 13, 18])) + _encode_field(2, b"value.") + b"\x18\x01"
            )
            document = _encode_field(1, path) + _encode_field(2, occurrence)
            Path(command[-1]).write_bytes(_encode_field(2, document))
        else:
            installs.append((command, kwargs["cwd"]))
            original_run(command, **kwargs)

    monkeypatch.setattr(runner.subprocess, "check_output", command_output)
    monkeypatch.setattr(runner, "run", run)
    settings = IndexSettings(
        semantic=False, typescript_projects=projects or [], npm_restore=npm_restore
    )
    settings.excludes.remove("**/packages/**")
    artifact = runner.build_artifact(
        repo,
        sha,
        settings,
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
    source_path = "packages/client/index.ts"
    assert [document.path for document in artifact.documents] == [source_path]
    assert artifact.occurrences[0].symbol == "value."
    assert artifact.occurrences[0].path == source_path

    # Exercise the host importer and portable export too: merely reaching SCIP
    # does not prove the workspace's source survived filtering.
    service = RepositoryIndexService(tmp_path / "runtime", ManagedGit())
    settings = IndexSettings(enabled=True, semantic=False)
    settings.excludes.remove("**/packages/**")
    service.save_settings(REPO, "root/app", settings.model_dump(), expected_revision=0)
    build = service.store.get(service.ensure(REPO, "root/app", artifact.commit_sha)["id"])
    source = (repo / source_path).read_bytes()
    coverage = import_artifact(
        service.store,
        build,
        artifact,
        tracked_blobs={
            source_path: hashlib.sha1(f"blob {len(source)}\0".encode() + source).hexdigest(),
        },
    )
    service.store.update(build["id"], status="ready", coverage=coverage)
    assert (
        service.search(REPO, "root/app", artifact.commit_sha, "value")["hits"][0]["path"]
        == source_path
    )
    assert (
        service.navigate(REPO, "root/app", artifact.commit_sha, "value.")["hits"][0]["path"]
        == source_path
    )
    destination = tmp_path / "workspace.zip"
    service.export(REPO, "root/app", build["id"], destination)
    with zipfile.ZipFile(destination) as bundle:
        bundle.extractall(tmp_path / "exported")
        scip_path = next(name for name in bundle.namelist() if name.endswith(".scip"))
        assert read_scip(bundle.read(scip_path))[2] == {source_path}
    with open_index(tmp_path / "exported/index.sqlite") as db:
        assert search(db, "value")["hits"][0]["path"] == source_path
        assert navigate(db, "value.")["hits"][0]["path"] == source_path


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


def dotnet_index_fixture(tmp_path, monkeypatch, *, build_fails=False):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Example.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>'
    )
    (repo / "Example.cs").write_text("public sealed class Example {}\n")
    subprocess.run(
        ["git", "init", "--initial-branch=main"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
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
            "add",
            ".",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
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
            "commit",
            "-m",
            "Fixture",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    calls = []
    original_output = subprocess.check_output

    def command_output(command, **kwargs):
        if command[0] in {"scip-dotnet", "scip-typescript"}:
            return TOOLCHAIN[command[0]].encode()
        return original_output(command, **kwargs)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[:2] == ["dotnet", "build"] and build_fails:
            raise subprocess.CalledProcessError(1, command)
        if command[0] == "scip-dotnet":
            path = b"Example.cs"
            occurrence = (
                _encode_field(1, bytes([0, 20, 27])) + _encode_field(2, b"Example.") + b"\x18\x01"
            )
            document = _encode_field(1, path) + _encode_field(2, occurrence)
            destination = Path(command[command.index("--output") + 1])
            destination.write_bytes(_encode_field(2, document))

    monkeypatch.setattr(runner.subprocess, "check_output", command_output)
    monkeypatch.setattr(runner, "run", run)
    monkeypatch.setenv("NoWarn", "CS1591")
    artifact = runner.build_artifact(
        repo,
        sha,
        IndexSettings(semantic=False, dotnet_projects=["Example.csproj"]),
        tmp_path / "artifacts" / "code-index.json",
    )
    return artifact, calls


def test_dotnet_navigation_disables_transport_metadata_and_pruning_warning(tmp_path, monkeypatch):
    artifact, calls = dotnet_index_fixture(tmp_path, monkeypatch)

    assert artifact.projects[0]["status"] == "ready"
    compiler_calls = [call for call in calls if call[0][0] in {"dotnet", "scip-dotnet"}]
    assert [call[0][:2] for call in compiler_calls] == [
        ["dotnet", "restore"],
        ["dotnet", "build"],
        ["scip-dotnet", "index"],
    ]
    for _, kwargs in compiler_calls:
        assert kwargs["env"]["EnableSourceLink"] == "false"
        assert kwargs["env"]["NoWarn"] == "CS1591;NU1510"


def test_dotnet_navigation_remains_partial_after_real_compilation_failure(tmp_path, monkeypatch):
    artifact, _ = dotnet_index_fixture(tmp_path, monkeypatch, build_fails=True)

    assert artifact.projects[0]["status"] == "partial"
    assert artifact.projects[0]["compilation"] == "failed"
