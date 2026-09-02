from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from subprocess import CompletedProcess

import pytest

from evander.runtime_generation import (
    TEAM_RUNTIME_COMPONENTS,
    read_runtime_generation_status,
    register_runtime_generation,
    require_current_runtime_generation,
    resolve_runtime_generation,
)


def test_configured_runtime_generation_labels_but_does_not_replace_code_identity(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("evander.runtime_generation._package_version", lambda: "1.2.3")
    identity = resolve_runtime_generation(
        tmp_path,
        env={"EVANDER_RUNTIME_GENERATION": "release-2026-08-24"},
    )

    assert identity.generation.startswith("release-2026-08-24:")
    assert identity.source == "env-label:EVANDER_RUNTIME_GENERATION+package-version"
    assert identity.commit_sha is None

    monkeypatch.setattr("evander.runtime_generation._package_version", lambda: "1.2.4")
    changed_binary = resolve_runtime_generation(
        tmp_path,
        env={"EVANDER_RUNTIME_GENERATION": "release-2026-08-24"},
    )
    assert changed_binary.generation != identity.generation


def test_runtime_generation_fingerprints_tracked_and_untracked_runtime_sources(
    tmp_path,
    monkeypatch,
) -> None:
    source = tmp_path / "src" / "evander"
    admin_dist = tmp_path / "frontend" / "admin-web" / "dist"
    source.mkdir(parents=True)
    admin_dist.mkdir(parents=True)
    tracked_source = source / "service.py"
    tracked_admin = admin_dist / "index.html"
    tracked_source.write_text("GENERATION = 1\n", encoding="utf-8")
    tracked_admin.write_text("<main>one</main>\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'runtime-test'\nversion = '1.0.0'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Runtime Test",
            "-c",
            "user.email=runtime@example.invalid",
            "commit",
            "-q",
            "-m",
            "initial runtime",
        ],
        check=True,
    )
    monkeypatch.setattr("evander.runtime_generation._package_version", lambda: "0.9.0")

    clean = resolve_runtime_generation(tmp_path)
    assert clean.version == "1.0.0"
    assert clean.dirty_fingerprint is None

    tracked_source.write_text("GENERATION = 2\n", encoding="utf-8")
    tracked = resolve_runtime_generation(tmp_path)
    assert tracked.dirty_fingerprint is not None
    assert tracked.generation != clean.generation
    subprocess.run(
        ["git", "-C", str(tmp_path), "restore", "src/evander/service.py"],
        check=True,
    )
    assert resolve_runtime_generation(tmp_path) == clean

    untracked_source = source / "new_runtime.py"
    untracked_source.write_text("ENABLED = True\n", encoding="utf-8")
    untracked = resolve_runtime_generation(tmp_path)
    assert untracked.dirty_fingerprint is not None
    assert untracked.generation != clean.generation
    untracked_source.unlink()
    assert resolve_runtime_generation(tmp_path) == clean

    tracked_admin.write_text("<main>two</main>\n", encoding="utf-8")
    admin_change = resolve_runtime_generation(tmp_path)
    assert admin_change.dirty_fingerprint is not None
    assert admin_change.generation != clean.generation


def test_runtime_generation_registry_detects_split_services(tmp_path) -> None:
    runtime_dir = tmp_path / "runtime"
    registered_at = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)
    for component in TEAM_RUNTIME_COMPONENTS:
        register_runtime_generation(
            project_dir=tmp_path,
            runtime_dir=runtime_dir,
            component=component,
            env={"EVANDER_RUNTIME_GENERATION": "generation-a"},
            now=registered_at,
        )

    coherent = read_runtime_generation_status(runtime_dir)
    assert coherent["state"] == "coherent"
    assert len(coherent["generations"]) == 1
    assert coherent["generations"][0].startswith("generation-a:")

    register_runtime_generation(
        project_dir=tmp_path,
        runtime_dir=runtime_dir,
        component="effect-orchestrator",
        env={"EVANDER_RUNTIME_GENERATION": "generation-b"},
        now=registered_at,
    )

    split = read_runtime_generation_status(runtime_dir)
    assert split["state"] == "split"
    assert split["coherent"] is False
    assert len(split["generations"]) == 2
    assert any(value.startswith("generation-a:") for value in split["generations"])
    assert any(value.startswith("generation-b:") for value in split["generations"])


def test_runtime_generation_registry_reports_missing_components(tmp_path) -> None:
    register_runtime_generation(
        project_dir=tmp_path,
        runtime_dir=tmp_path / "runtime",
        component="gateway",
        env={"EVANDER_RUNTIME_GENERATION": "generation-a"},
    )

    status = read_runtime_generation_status(tmp_path / "runtime")
    assert status["state"] == "incomplete"
    assert status["missing_components"] == [
        "admin-web",
        "trigger-orchestrator",
        "effect-orchestrator",
    ]


def test_runtime_generation_registry_detects_coherent_but_stale_services(
    tmp_path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr("evander.runtime_generation._package_version", lambda: "1.0.0")
    for component in TEAM_RUNTIME_COMPONENTS:
        register_runtime_generation(
            project_dir=tmp_path,
            runtime_dir=runtime_dir,
            component=component,
        )

    monkeypatch.setattr("evander.runtime_generation._package_version", lambda: "1.0.1")
    status = read_runtime_generation_status(runtime_dir)

    assert status["state"] == "stale"
    assert status["coherent"] is False
    assert status["stale_components"] == sorted(TEAM_RUNTIME_COMPONENTS)
    assert status["current_generation"]["version"] == "1.0.1"
    with pytest.raises(RuntimeError, match="restart evander-team-runtime.target"):
        require_current_runtime_generation(runtime_dir)


def test_runtime_generation_registry_rejects_previous_boot_records(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("evander.runtime_generation._current_boot_id", lambda: "boot-a")
    register_runtime_generation(
        project_dir=tmp_path,
        runtime_dir=tmp_path / "runtime",
        component="gateway",
    )

    monkeypatch.setattr("evander.runtime_generation._current_boot_id", lambda: "boot-b")
    status = read_runtime_generation_status(
        tmp_path / "runtime",
        components=("gateway",),
    )

    assert status["state"] == "incomplete"
    assert status["components"] == {}
    assert "previous host boot" in status["errors"]["gateway"]


@pytest.mark.parametrize("failed_command", ["diff", "ls-files"])
def test_runtime_generation_fails_closed_when_source_fingerprint_cannot_be_read(
    tmp_path,
    monkeypatch,
    failed_command: str,
) -> None:
    def _run_git(_project_dir, args):
        command = args[0]
        if command == "rev-parse":
            return CompletedProcess(args, 0, stdout=b"a" * 40 + b"\n", stderr=b"")
        if command == failed_command:
            return CompletedProcess(args, 1, stdout=b"", stderr=b"damaged index")
        return CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("evander.runtime_generation._run_git", _run_git)

    with pytest.raises(RuntimeError, match=f"git {failed_command} failed"):
        resolve_runtime_generation(tmp_path)
