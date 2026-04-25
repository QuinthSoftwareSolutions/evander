from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agent_sandbox.worker import cli


def _run_cli(monkeypatch, argv: list[str]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_create_app(**kwargs):
        captured["create_app"] = kwargs
        return object()

    def fake_uvicorn_run(app, **kwargs):
        captured["app"] = app
        captured["uvicorn"] = kwargs

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli.uvicorn, "run", fake_uvicorn_run)
    cli.main()
    return captured


def test_worker_cli_uses_default_sandbox_profile(monkeypatch, tmp_path: Path) -> None:
    captured = _run_cli(
        monkeypatch,
        [
            "agent-sandbox-worker-daemon",
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--worker-root",
            str(tmp_path / "worker-root"),
        ],
    )

    profile = captured["create_app"]["sandbox_profile"]
    assert profile.name == "default"
    assert profile.network_mode == "none"
    assert profile.security_opt == ("no-new-privileges",)
    assert not profile.read_only_rootfs
    assert captured["create_app"]["docker_network"] is None


def test_worker_cli_accepts_strict_profile_network_and_container_tuning(monkeypatch, tmp_path: Path) -> None:
    captured = _run_cli(
        monkeypatch,
        [
            "agent-sandbox-worker-daemon",
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--worker-root",
            str(tmp_path / "worker-root"),
            "--sandbox-profile",
            "strict",
            "--docker-network",
            "agent-worker-net",
            "--read-only-rootfs",
            "--tmpfs",
            "/run:rw,noexec,nosuid,size=64m",
            "--container-user",
            "1000:1000",
        ],
    )

    profile = captured["create_app"]["sandbox_profile"]
    assert profile.name == "strict"
    assert profile.network_mode == "agent-worker-net"
    assert profile.read_only_rootfs
    assert profile.tmpfs == ("/run:rw,noexec,nosuid,size=64m",)
    assert profile.user == "1000:1000"


def test_worker_cli_reads_sandbox_profile_from_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_WORKER_SANDBOX_PROFILE", "strict")
    captured = _run_cli(
        monkeypatch,
        [
            "agent-sandbox-worker-daemon",
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--worker-root",
            str(tmp_path / "worker-root"),
        ],
    )

    profile = captured["create_app"]["sandbox_profile"]
    assert profile.name == "strict"
    assert profile.read_only_rootfs
    assert profile.default_pids_limit == 512
