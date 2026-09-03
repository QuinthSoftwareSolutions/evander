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
    assert captured["create_app"]["codex_egress_proxy_url"] is None
    assert captured["create_app"]["codex_egress_proxy_bind_host"] is None
    assert captured["create_app"]["codex_egress_allowed_hosts"] is None


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


def test_worker_cli_accepts_codex_subscription_broker_and_proxy_flags(
    monkeypatch, tmp_path: Path
) -> None:
    worker_root = tmp_path / "worker-root"
    captured = _run_cli(
        monkeypatch,
        [
            "evander-worker-daemon",
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--worker-root",
            str(worker_root),
            "--docker-network",
            "agent-worker-net",
            "--codex-auth-image",
            "evander-runner:latest",
            "--container-user",
            "1200:1200",
            "--codex-egress-proxy-url",
            "http://172.18.0.1:11889",
            "--codex-egress-proxy-bind-host",
            "172.18.0.1",
            "--codex-egress-proxy-port",
            "11889",
            "--codex-egress-host",
            "auth.openai.com",
            "--codex-egress-host",
            "*.chatgpt.com",
        ],
    )

    command = captured["create_app"]["codex_auth_command"]
    assert command[:4] == ("docker", "run", "--rm", "-i")
    assert command[command.index("--name") + 1].startswith("evander-codex-auth-")
    assert f"{worker_root / 'codex-auth'}:/codex-auth" in command
    assert "CODEX_HOME=/codex-auth" in command
    assert command[command.index("--user") + 1] == "1200:1200"
    assert "http://172.18.0.1:11889" in " ".join(command)
    assert captured["create_app"]["codex_egress_proxy_url"] == ("http://172.18.0.1:11889")
    assert captured["create_app"]["codex_egress_proxy_bind_host"] == "172.18.0.1"
    assert captured["create_app"]["codex_egress_proxy_port"] == 11889
    assert captured["create_app"]["codex_egress_allowed_hosts"] == [
        "auth.openai.com",
        "*.chatgpt.com",
    ]
    command_factory = captured["create_app"]["codex_auth_command_factory"]
    named_home = worker_root / "codex-auth" / "profiles" / "team-a"
    named_command = command_factory("team-a", named_home)
    assert (
        named_command[named_command.index("--name") + 1] != (command[command.index("--name") + 1])
    )
    assert f"{named_home}:/codex-auth" in named_command
    assert "http://172.18.0.1:11889" in " ".join(named_command)


def test_worker_cli_reads_codex_auth_image_from_environment(monkeypatch, tmp_path: Path) -> None:
    worker_root = tmp_path / "worker-root"
    monkeypatch.setenv("EVANDER_WORKER_CODEX_AUTH_IMAGE", "evander-runner:latest")

    captured = _run_cli(
        monkeypatch,
        [
            "evander-worker-daemon",
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--worker-root",
            str(worker_root),
        ],
    )

    command = captured["create_app"]["codex_auth_command"]
    assert command[:4] == ("docker", "run", "--rm", "-i")
    assert command[-4:] == ("evander-runner:latest", "codex", "app-server", "--stdio")


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
