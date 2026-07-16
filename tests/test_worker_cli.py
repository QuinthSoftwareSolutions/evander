from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from evander.worker import cli


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
            "evander-worker-daemon",
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
    assert not any("egress" in key for key in captured["create_app"])


def test_worker_cli_accepts_strict_profile_network_and_container_tuning(
    monkeypatch, tmp_path: Path
) -> None:
    captured = _run_cli(
        monkeypatch,
        [
            "evander-worker-daemon",
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


def test_worker_cli_requires_explicit_unsafe_flag_for_host_network(
    monkeypatch, tmp_path: Path
) -> None:
    captured = _run_cli(
        monkeypatch,
        [
            "evander-worker-daemon",
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--worker-root",
            str(tmp_path / "worker-root"),
            "--docker-network",
            "host",
            "--unsafe-allow-host-network",
        ],
    )

    profile = captured["create_app"]["sandbox_profile"]
    assert profile.network_mode == "host"
    assert profile.allow_host_network is True


def test_worker_cli_accepts_managed_git_source_restriction_flags(
    monkeypatch, tmp_path: Path
) -> None:
    ssh_dir = tmp_path / "ssh"
    captured = _run_cli(
        monkeypatch,
        [
            "evander-worker-daemon",
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--worker-root",
            str(tmp_path / "worker-root"),
            "--allowed-git-source-prefix",
            "ssh://evander-git-host/opt/evander/runtime/managed-git/",
            "--allowed-git-source-prefix",
            "file:///opt/evander/runtime/managed-git/",
            "--git-ssh-source-dir",
            str(ssh_dir),
        ],
    )

    assert captured["create_app"]["allowed_git_source_prefixes"] == [
        "ssh://evander-git-host/opt/evander/runtime/managed-git/",
        "file:///opt/evander/runtime/managed-git/",
    ]
    assert captured["create_app"]["git_ssh_source_dir"] == ssh_dir


def test_worker_cli_accepts_mssql_flags(monkeypatch, tmp_path: Path) -> None:
    captured = _run_cli(
        monkeypatch,
        [
            "evander-worker-daemon",
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--worker-root",
            str(tmp_path / "worker-root"),
            "--mssql-enabled",
            "--mssql-admin-user",
            "sa",
            "--mssql-admin-password-env",
            "WORKER_SQL_PASSWORD",
            "--mssql-admin-host",
            "127.0.0.1",
            "--mssql-port",
            "14330",
            "--mssql-container-host",
            "172.18.0.1",
            "--mssql-sqlcmd-path",
            "/opt/mssql-tools18/bin/sqlcmd",
        ],
    )

    assert captured["create_app"]["mssql_enabled"] is True
    assert captured["create_app"]["mssql_admin_user"] == "sa"
    assert captured["create_app"]["mssql_admin_password_env"] == "WORKER_SQL_PASSWORD"
    assert captured["create_app"]["mssql_admin_host"] == "127.0.0.1"
    assert captured["create_app"]["mssql_port"] == 14330
    assert captured["create_app"]["mssql_container_host"] == "172.18.0.1"
    assert captured["create_app"]["mssql_sqlcmd_path"] == "/opt/mssql-tools18/bin/sqlcmd"


def test_worker_cli_reads_sandbox_profile_from_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EVANDER_WORKER_SANDBOX_PROFILE", "strict")
    captured = _run_cli(
        monkeypatch,
        [
            "evander-worker-daemon",
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
