import json
import os
from pathlib import Path
import subprocess

import pytest
from typer.testing import CliRunner

import evander.setup.engine as setup_engine
from evander.cli import app
from evander.setup import tui as setup_tui
from evander.setup.engine import (
    SetupDependencyInstallRejected,
    SetupManager,
    SetupPreflightError,
)


ROOT = Path(__file__).resolve().parents[1]


def test_setup_checklist_requires_provider_secret_without_storing_value(tmp_path: Path) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")
    manager.acknowledge_secret_storage(state)
    state = manager.load_state()

    manager.configure_admin_web(
        state,
        host="127.0.0.1",
        port="8100",
        username="operator",
        password="admin-secret",
    )
    state = manager.configure_execution(manager.load_state(), model="container_same_host")
    state = manager.configure_ai_providers(state, providers=["openai"], secrets_by_env={})

    checklist = {item.id: item for item in manager.checklist(state)}
    assert checklist["ai_providers"].status == "needs input"
    assert "missing secrets: openai" in checklist["ai_providers"].summary
    assert state.execution["container_worker_base_url"] == "http://127.0.0.1:11890"

    state = manager.configure_ai_providers(
        manager.load_state(),
        providers=["openai"],
        secrets_by_env={"OPENAI_API_KEY": "sk-test-secret"},
    )
    checklist = {item.id: item for item in manager.checklist(state)}
    assert checklist["ai_providers"].status == "completed"
    assert "sk-test-secret" not in manager.state_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in (tmp_path / "local-secrets.env").read_text(encoding="utf-8")


def test_setup_secret_status_uses_sudo_aware_read_for_default_secret_file(
    tmp_path: Path, monkeypatch
) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.secret_file = "/etc/evander/local-secrets.env"
    state.ai_providers = ["openai"]
    reads: list[tuple[Path, bool]] = []

    def fake_read_env(path: Path, *, root_owned: bool) -> dict[str, str]:
        reads.append((path, root_owned))
        return {"OPENAI_API_KEY": "sk-existing"}

    monkeypatch.setattr("evander.setup.engine._read_env_for_update", fake_read_env)

    status = manager.secret_status(state)

    assert reads == [(Path("/etc/evander/local-secrets.env"), True)]
    assert status["required"]["openai"]["set"] is True


def test_setup_root_owned_secret_grants_git_ssh_user_acl(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_dir))
    monkeypatch.setenv("SUDO_USER", "operator")
    monkeypatch.setenv("EVANDER_GIT_USER", "git-user")
    monkeypatch.setattr(setup_engine.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(setup_engine.shutil, "which", lambda command: "/usr/bin/setfacl")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(setup_engine.subprocess, "run", fake_run)

    setup_engine._upsert_env_file(
        Path("/etc/evander/local-secrets.env"),
        {"AZURE_DEVOPS_PR_PAT": "token"},
        root_owned=True,
    )

    assert ["sudo", "setfacl", "-m", "u:git-user:--x", "/etc/evander"] in calls
    assert ["sudo", "setfacl", "-m", "u:git-user:r--", "/etc/evander/local-secrets.env"] in calls


def test_setup_admin_web_can_use_no_auth_without_password(tmp_path: Path) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")

    state = manager.configure_admin_web(
        state,
        host="127.0.0.1",
        port="8100",
        username="operator",
        auth_mode="none",
    )

    checklist = {item.id: item for item in manager.checklist(state)}
    secret_text = (tmp_path / "local-secrets.env").read_text(encoding="utf-8")
    assert checklist["admin_web"].status == "completed"
    assert checklist["admin_web"].summary == "127.0.0.1:8100, no authentication"
    assert state.admin_web["auth_mode"] == "none"
    assert state.admin_web["password_configured"] is False
    assert "EVANDER_ADMIN_WEB_SESSION_SECRET" in secret_text
    assert "EVANDER_ADMIN_WEB_PASSWORD" not in secret_text


def test_setup_plan_is_ready_after_required_items_and_preflight(tmp_path: Path) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")
    manager.acknowledge_secret_storage(state)
    state = manager.load_state()
    state = manager.configure_admin_web(
        state,
        host="127.0.0.1",
        port="8100",
        username="operator",
        password="admin-secret",
    )
    state = manager.configure_execution(state, model="libvirt_vm")
    state = manager.configure_ai_providers(
        state,
        providers=["openai"],
        secrets_by_env={"OPENAI_API_KEY": "sk-test-secret"},
    )
    state = manager.configure_default_model(state, provider="openai", model="gpt-5.4")
    state.install_preflight_passed_at = "2026-06-28T00:00:00+00:00"
    manager.save_state(state)

    plan = manager.plan()

    assert plan["ready"] is True
    assert plan["execution_model"] == "libvirt_vm"
    assert plan["default_model"] == "gpt-5.4"
    assert "sk-test-secret" not in json.dumps(plan)
    assert plan["secrets"]["required"]["openai"]["set"] is True


def test_setup_install_preflight_uses_setup_profile_and_clears_failed_pass(
    tmp_path: Path, monkeypatch
) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.execution = {"execution_backend": "libvirt_vm", "model": "libvirt_vm"}
    state.install_preflight_passed_at = "2026-06-28T00:00:00+00:00"
    manager.save_state(state)
    calls = []

    monkeypatch.setattr(manager, "_write_config_files", lambda state: None)

    def fake_run(args, *, cwd, env, check):
        calls.append({"args": args, "cwd": cwd, "env": env, "check": check})
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr("evander.setup.engine.subprocess.run", fake_run)

    with pytest.raises(SetupPreflightError) as exc_info:
        manager.run_install_preflight(state)

    assert exc_info.value.returncode == 1
    assert calls[0]["check"] is False
    assert calls[0]["env"]["EVANDER_SERVICE_PROFILE"] == "setup"
    assert calls[0]["env"]["EVANDER_SETUP_EXECUTION_BACKEND"] == "libvirt_vm"
    assert calls[0]["env"]["EVANDER_SETUP_EXECUTION_MODEL"] == "libvirt_vm"
    assert manager.load_state().install_preflight_passed_at == ""


def test_setup_tui_install_preflight_failure_returns_to_checklist(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    monkeypatch.setattr(manager, "configuration_ready", lambda state: True)
    monkeypatch.setattr(setup_tui.typer, "confirm", lambda *args, **kwargs: True)

    def fail_preflight(state):
        raise SetupPreflightError(returncode=1)

    monkeypatch.setattr(manager, "run_install_preflight", fail_preflight)

    result = setup_tui._configure_item(manager, state, "install_preflight", interactive=False)

    output = capsys.readouterr().out
    assert "Install preflight failed with exit code 1." in output
    assert "Fix the failed checks above" in output
    assert result == manager.load_state()


def test_setup_tui_launch_failure_returns_to_checklist(tmp_path: Path, monkeypatch, capsys) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    monkeypatch.setattr(manager, "required_ready", lambda state: True)
    monkeypatch.setattr(manager, "plan", lambda: {"ready": True})
    monkeypatch.setattr(setup_tui.typer, "confirm", lambda *args, **kwargs: True)

    def fail_launch(state, **kwargs):
        raise RuntimeError("worker daemon is not reachable")

    monkeypatch.setattr(manager, "launch", fail_launch)

    result = setup_tui._launch(manager, state)

    output = capsys.readouterr().out
    assert "Launch failed: worker daemon is not reachable" in output
    assert "rerun Launch and Admin Assistant verification" in output
    assert result == manager.load_state()


def test_setup_doctor_json_reports_initial_checklist(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "setup",
            "doctor",
            "--config",
            str(tmp_path / "runtime"),
            "--project-dir",
            str(ROOT),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    statuses = {item["id"]: item["status"] for item in payload["checklist"]}
    assert payload["paths"]["project_dir"] == str(ROOT)
    assert "setup_profile" not in statuses
    assert statuses["secret_storage"] == "needs input"
    assert statuses["execution_model"] == "needs input"
    assert statuses["launch_verify"] == "blocked"


def test_setup_writes_admin_web_no_auth_config_without_placeholder_password(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    config_dir = project_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "admin-web.env.example").write_text(
        (ROOT / "config" / "admin-web.env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (config_dir / "firewall.env.example").write_text("", encoding="utf-8")
    manager = SetupManager(project_dir=project_dir, runtime_dir=project_dir / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")
    state = manager.configure_admin_web(
        state,
        host="127.0.0.1",
        port="8100",
        username="operator",
        auth_mode="none",
    )

    manager._write_config_files(state)

    admin_env = (config_dir / "admin-web.env").read_text(encoding="utf-8")
    assert 'ADMIN_WEB_HOST="127.0.0.1"' in admin_env
    assert 'EVANDER_ADMIN_WEB_AUTH_MODE="none"' in admin_env
    assert 'EVANDER_ADMIN_WEB_USERNAME="operator"' in admin_env
    assert 'EVANDER_ADMIN_WEB_PASSWORD=""' in admin_env
    assert 'EVANDER_ADMIN_WEB_SESSION_SECRET=""' in admin_env
    assert "replace-with-real-password" not in admin_env


def test_setup_writes_admin_web_password_secrets_outside_repo_config(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    config_dir = project_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "admin-web.env.example").write_text(
        (ROOT / "config" / "admin-web.env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (config_dir / "firewall.env.example").write_text("", encoding="utf-8")
    manager = SetupManager(project_dir=project_dir, runtime_dir=project_dir / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")
    state = manager.configure_admin_web(
        state,
        host="127.0.0.1",
        port="8100",
        username="operator",
        password="admin-secret",
    )

    manager._write_config_files(state)

    admin_env = (config_dir / "admin-web.env").read_text(encoding="utf-8")
    secret_text = (tmp_path / "local-secrets.env").read_text(encoding="utf-8")
    assert 'ADMIN_WEB_HOST="127.0.0.1"' in admin_env
    assert 'EVANDER_ADMIN_WEB_AUTH_MODE="local_password"' in admin_env
    assert 'EVANDER_ADMIN_WEB_USERNAME="operator"' in admin_env
    assert 'EVANDER_ADMIN_WEB_PASSWORD=""' in admin_env
    assert 'EVANDER_ADMIN_WEB_SESSION_SECRET=""' in admin_env
    assert "admin-secret" not in admin_env
    assert "EVANDER_ADMIN_WEB_PASSWORD" in secret_text
    assert "EVANDER_ADMIN_WEB_SESSION_SECRET" in secret_text


def test_setup_writes_container_worker_token_secret_without_state_leak(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("TEST_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(
        "evander.setup.engine.secrets.token_urlsafe",
        lambda length: "worker-token",
    )
    project_dir = tmp_path / "project"
    config_dir = project_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "admin-web.env.example").write_text(
        (ROOT / "config" / "admin-web.env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (config_dir / "firewall.env.example").write_text("", encoding="utf-8")
    manager = SetupManager(project_dir=project_dir, runtime_dir=project_dir / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")
    state.execution = {
        "execution_backend": "container_worker_vm",
        "container_worker_token_env": "TEST_WORKER_TOKEN",
    }
    manager.save_state(state)

    manager._write_config_files(state)

    secret_text = (tmp_path / "local-secrets.env").read_text(encoding="utf-8")
    assert 'TEST_WORKER_TOKEN="worker-token"' in secret_text
    assert os.environ["TEST_WORKER_TOKEN"] == "worker-token"
    assert "worker-token" not in manager.state_path.read_text(encoding="utf-8")


def test_setup_local_container_worker_unit_uses_secret_and_worker_root(tmp_path: Path) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")

    unit = manager._local_container_worker_unit_content(state, run_user="operator")

    assert "User=operator" in unit
    assert "SupplementaryGroups=docker" in unit
    assert f"EnvironmentFile=-{tmp_path / 'local-secrets.env'}" in unit
    assert "evander-worker-daemon --host 127.0.0.1 --port 11890" in unit
    assert "--runtime-dir /var/lib/agent-worker/runtime" in unit
    assert "--worker-root /var/lib/agent-worker" in unit
    assert "--container-user " in unit
    assert (
        "--allowed-git-source-prefix "
        f"ssh://evander-git-host{tmp_path / 'runtime' / 'managed-git'}/"
    ) in unit
    assert (
        "--allowed-git-source-prefix "
        f"ssh://evander-git-host-write{tmp_path / 'runtime' / 'managed-git'}/"
    ) in unit
    assert "--git-ssh-source-dir /home/operator/.ssh" in unit
    assert (
        "--readonly-mount "
        "/var/lib/agent-worker/toolchain-cache/dotnet:/opt/evander/toolchains/dotnet"
    ) in unit
    assert (
        "--readonly-mount "
        "/var/lib/agent-worker/toolchain-cache/node-v24.14.0:"
        "/opt/evander/toolchains/node-v24.14.0"
    ) in unit
    assert (
        "--readonly-mount "
        "/var/lib/agent-worker/toolchain-cache/playwright-runtime:"
        "/opt/evander/playwright-runtime"
    ) in unit
    assert (
        "--readonly-mount "
        "/var/lib/agent-worker/toolchain-cache/ms-playwright:/opt/evander/ms-playwright"
    ) in unit
    assert (
        "--readonly-mount "
        "/var/lib/agent-worker/toolchain-cache/nuget-cache-ro:/opt/evander/nuget-cache-ro"
    ) in unit
    assert "Requires=docker.service" in unit


def test_setup_local_container_worker_git_access_generates_client_key_without_prefix_auth(
    tmp_path: Path, monkeypatch
) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    home = tmp_path / "home" / "operator"
    generated_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGenerated generated"
    calls: list[list[str]] = []

    monkeypatch.setattr(setup_engine, "_home_for_user", lambda user: home)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert args[:6] == ["ssh-keygen", "-t", "ed25519", "-N", "", "-C"]
        key_path = Path(args[-1])
        key_path.write_text("private key\n", encoding="utf-8")
        key_path.with_suffix(key_path.suffix + ".pub").write_text(
            generated_public_key + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(setup_engine.subprocess, "run", fake_run)

    manager._ensure_local_container_worker_git_access(
        git_user="git-user",
        git_ssh_alias="evander-git-host",
        git_ssh_source_dir=setup_engine._local_worker_git_ssh_source_dir("operator"),
    )

    ssh_dir = home / ".ssh"
    assert calls
    assert (ssh_dir / "evander-git").read_text(encoding="utf-8") == "private key\n"
    assert (ssh_dir / "evander-git.pub").read_text(encoding="utf-8").strip() == (
        generated_public_key
    )
    assert oct((ssh_dir / "evander-git").stat().st_mode & 0o777) == "0o600"
    config_text = (ssh_dir / "config").read_text(encoding="utf-8")
    assert "Host evander-git-host" in config_text
    assert "EVANDER_GIT_AUTH_REPO_PREFIX_B64=" not in config_text


def test_setup_local_container_worker_git_access_repairs_legacy_key_name(
    tmp_path: Path, monkeypatch
) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    home = tmp_path / "home" / "operator"
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "agent-sandbox-git").write_text("legacy private key\n", encoding="utf-8")
    legacy_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILegacy legacy"
    (ssh_dir / "agent-sandbox-git.pub").write_text(legacy_public_key + "\n", encoding="utf-8")
    (ssh_dir / "config").write_text(
        "\n".join(
            [
                "Host existing",
                "  HostName example.test",
                "",
            ]
        ),
        encoding="utf-8",
    )
    manager._ensure_local_container_worker_git_access(
        git_user="git-user",
        git_ssh_alias="agent-sandbox-git-host",
        git_ssh_source_dir=ssh_dir,
    )

    assert (ssh_dir / "evander-git").read_text(encoding="utf-8") == "legacy private key\n"
    assert (ssh_dir / "evander-git.pub").read_text(encoding="utf-8").strip() == legacy_public_key
    config_text = (ssh_dir / "config").read_text(encoding="utf-8")
    assert config_text.index("Host agent-sandbox-git-host") < config_text.index("Host existing")
    assert "IdentityFile ~/.ssh/evander-git" in config_text
    assert "EVANDER_GIT_AUTH_REPO_PREFIX_B64=" not in config_text


def test_setup_local_container_worker_unit_uses_configured_base_url(tmp_path: Path) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")
    state.execution = {
        "execution_backend": "container_worker_vm",
        "model": "container_same_host",
        "container_worker_base_url": "http://127.0.0.1:11990",
    }

    unit = manager._local_container_worker_unit_content(state, run_user="operator")

    assert "evander-worker-daemon --host 127.0.0.1 --port 11990" in unit


def test_setup_local_container_worker_unit_requires_explicit_port(tmp_path: Path) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.execution = {
        "execution_backend": "container_worker_vm",
        "model": "container_same_host",
        "container_worker_base_url": "http://127.0.0.1",
    }

    with pytest.raises(ValueError, match="explicit port"):
        manager._local_container_worker_unit_content(state, run_user="operator")


def test_setup_builds_admin_frontend_assets_when_references_are_missing(
    tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "project"
    frontend_dir = project_dir / "frontend" / "workflow-editor"
    dist_dir = frontend_dir / "dist"
    dist_dir.mkdir(parents=True)
    (frontend_dir / "package.json").write_text("{}", encoding="utf-8")
    (frontend_dir / "package-lock.json").write_text("{}", encoding="utf-8")
    (dist_dir / "index.html").write_text(
        '<script type="module" src="/assets/index-missing.js"></script>'
        '<link rel="stylesheet" href="/assets/index-missing.css">',
        encoding="utf-8",
    )
    manager = SetupManager(project_dir=project_dir, runtime_dir=tmp_path / "runtime")
    run_calls = []

    monkeypatch.setattr("evander.setup.engine.shutil.which", lambda command: "/bin/npm")

    def fake_run(args, *, cwd, check, **kwargs):
        run_calls.append({"args": args, "cwd": cwd, "check": check, **kwargs})
        if args == ["/bin/npm", "run", "build"]:
            assets_dir = dist_dir / "assets"
            assets_dir.mkdir()
            (assets_dir / "index-missing.js").write_text("console.log('ok')", encoding="utf-8")
            (assets_dir / "index-missing.css").write_text("body{}", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("evander.setup.engine.subprocess.run", fake_run)

    manager._ensure_admin_frontend_assets()

    assert [call["args"] for call in run_calls] == [
        ["/bin/npm", "ci", "--no-audit", "--no-fund"],
        ["/bin/npm", "run", "build"],
    ]
    assert all(call["cwd"] == frontend_dir for call in run_calls)
    assert all(call["check"] is True for call in run_calls)


def test_setup_admin_frontend_asset_build_installs_approved_nodejs_npm(
    tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "project"
    frontend_dir = project_dir / "frontend" / "workflow-editor"
    dist_dir = frontend_dir / "dist"
    dist_dir.mkdir(parents=True)
    (frontend_dir / "package.json").write_text("{}", encoding="utf-8")
    (frontend_dir / "package-lock.json").write_text("{}", encoding="utf-8")
    (dist_dir / "index.html").write_text(
        '<script type="module" src="/assets/index-missing.js"></script>',
        encoding="utf-8",
    )
    manager = SetupManager(project_dir=project_dir, runtime_dir=tmp_path / "runtime")
    which_calls = {"npm": 0}
    run_calls = []
    approvals = []

    def fake_which(command):
        if command == "npm":
            which_calls["npm"] += 1
            return None if which_calls["npm"] == 1 else "/bin/npm"
        return {
            "apt-cache": "/usr/bin/apt-cache",
            "apt-get": "/usr/bin/apt-get",
            "sudo": "/usr/bin/sudo",
        }.get(command)

    def fake_run(args, **kwargs):
        run_calls.append({"args": args, **kwargs})
        if args == ["/usr/bin/apt-cache", "policy", "nodejs"]:
            return subprocess.CompletedProcess(args, 0, stdout="Candidate: 18.19.1\n", stderr="")
        if args == ["/usr/bin/apt-cache", "policy", "npm"]:
            return subprocess.CompletedProcess(args, 0, stdout="Candidate: 9.2.0\n", stderr="")
        if args == ["/bin/npm", "run", "build"]:
            assets_dir = dist_dir / "assets"
            assets_dir.mkdir()
            (assets_dir / "index-missing.js").write_text("console.log('ok')", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("evander.setup.engine.shutil.which", fake_which)
    monkeypatch.setattr("evander.setup.engine.subprocess.run", fake_run)
    monkeypatch.setattr("evander.setup.engine.os.geteuid", lambda: 1000)

    manager._ensure_admin_frontend_assets(
        approve_nodejs_npm_install=lambda versions: approvals.append(versions) or True
    )

    assert approvals == [{"nodejs": "18.19.1", "npm": "9.2.0"}]
    assert [
        "/usr/bin/sudo",
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "/usr/bin/apt-get",
        "install",
        "-y",
        "nodejs=18.19.1",
        "npm=9.2.0",
    ] in [call["args"] for call in run_calls]
    assert ["/bin/npm", "ci", "--no-audit", "--no-fund"] in [call["args"] for call in run_calls]
    assert ["/bin/npm", "run", "build"] in [call["args"] for call in run_calls]


def test_setup_admin_frontend_tooling_supports_apt_node_18() -> None:
    frontend_dir = ROOT / "frontend" / "workflow-editor"
    package_json = json.loads((frontend_dir / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((frontend_dir / "package-lock.json").read_text(encoding="utf-8"))

    assert package_json["devDependencies"]["vite"].startswith("^6.")
    assert package_json["devDependencies"]["@vitejs/plugin-react"].startswith("^4.")
    assert "^18.0.0" in package_lock["packages"]["node_modules/vite"]["engines"]["node"]
    assert (
        "^14.18.0 || >=16.0.0"
        == package_lock["packages"]["node_modules/@vitejs/plugin-react"]["engines"]["node"]
    )


def test_setup_admin_frontend_asset_build_stops_when_nodejs_npm_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "project"
    frontend_dir = project_dir / "frontend" / "workflow-editor"
    dist_dir = frontend_dir / "dist"
    dist_dir.mkdir(parents=True)
    (frontend_dir / "package.json").write_text("{}", encoding="utf-8")
    (dist_dir / "index.html").write_text(
        '<script type="module" src="/assets/index-missing.js"></script>',
        encoding="utf-8",
    )
    manager = SetupManager(project_dir=project_dir, runtime_dir=tmp_path / "runtime")

    monkeypatch.setattr(
        "evander.setup.engine.shutil.which",
        lambda command: {
            "apt-cache": "/usr/bin/apt-cache",
            "apt-get": "/usr/bin/apt-get",
            "sudo": "/usr/bin/sudo",
        }.get(command),
    )
    monkeypatch.setattr(
        manager,
        "_apt_candidate_versions",
        lambda apt_cache, packages: {"nodejs": "18.19.1", "npm": "9.2.0"},
    )

    with pytest.raises(SetupDependencyInstallRejected):
        manager._ensure_admin_frontend_assets(approve_nodejs_npm_install=lambda versions: False)


def test_setup_tui_launch_exits_when_dependency_install_is_rejected(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    monkeypatch.setattr(manager, "required_ready", lambda state: True)
    monkeypatch.setattr(manager, "plan", lambda: {"ready": True})
    monkeypatch.setattr(setup_tui.typer, "confirm", lambda *args, **kwargs: True)

    def reject_launch(state, **kwargs):
        raise SetupDependencyInstallRejected("Node.js/npm installation was not approved.")

    monkeypatch.setattr(manager, "launch", reject_launch)

    with pytest.raises(setup_tui.typer.Exit):
        setup_tui._launch(manager, state)

    assert "Node.js/npm installation was not approved." in capsys.readouterr().out


def test_setup_launch_starts_local_worker_for_same_host_container(
    tmp_path: Path, monkeypatch
) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.execution = {
        "execution_backend": "container_worker_vm",
        "model": "container_same_host",
    }
    local_worker_installs = []
    run_calls = []

    monkeypatch.setattr(manager, "required_ready", lambda state: True)
    monkeypatch.setattr(manager, "_write_config_files", lambda state: None)
    monkeypatch.setattr(manager, "_ensure_admin_frontend_assets", lambda **kwargs: None)
    monkeypatch.setattr(manager, "_install_secret_dropins", lambda state: None)
    monkeypatch.setattr(manager, "_wait_admin_web", lambda state: None)
    monkeypatch.setattr(manager, "_verify_admin_assistant", lambda state: None)
    monkeypatch.setattr(
        manager,
        "_install_local_container_worker_daemon",
        lambda state: local_worker_installs.append(state),
    )

    def fake_run(args, **kwargs):
        run_calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("evander.setup.engine.subprocess.run", fake_run)

    manager.launch(state)

    assert local_worker_installs == [state]
    assert ["sudo", "systemctl", "restart", "evander-worker-daemon.service"] in run_calls


def test_setup_tui_omits_nonfunctional_setup_profile_choice(tmp_path: Path, monkeypatch) -> None:
    clear_calls = []
    monkeypatch.setattr("evander.setup.tui.click.clear", lambda: clear_calls.append(True))
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "setup",
            "--config",
            str(tmp_path / "runtime"),
            "--project-dir",
            str(ROOT),
        ],
        input="y\nq\n",
    )

    assert result.exit_code == 0
    assert "Setup profile" not in result.output
    assert "Local workstation / evaluation" not in result.output
    assert "Dedicated single-machine host" not in result.output
    assert len(clear_calls) >= 2


def test_setup_tui_supports_discouraged_no_auth_admin_web(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("evander.setup.tui.click.clear", lambda: None)
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")
    manager.save_state(state)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "setup",
            "--config",
            str(tmp_path / "runtime"),
            "--project-dir",
            str(ROOT),
        ],
        input="y\n2\n\n\noperator\n2\ny\nq\n",
    )

    assert result.exit_code == 0
    assert "No authentication (discouraged)" in result.output
    assert "grants full admin access to anyone" in result.output
    payload = json.loads((tmp_path / "runtime" / "setup-wizard" / "state.json").read_text())
    assert payload["admin_web"]["auth_mode"] == "none"
    assert payload["admin_web"]["password_configured"] is False


def _patch_tui_keys(monkeypatch, *keys: str) -> None:
    key_iter = iter(keys)
    monkeypatch.setattr(setup_tui, "_read_key", lambda: next(key_iter))


def test_setup_tui_checklist_enter_opens_highlighted_item(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("evander.setup.tui.click.clear", lambda: None)
    _patch_tui_keys(monkeypatch, "enter")
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()

    action = setup_tui._prompt_checklist_action(manager, state)

    assert action.kind == "item"
    assert action.item_id == "secret_storage"
    assert action.selected_index == 0


def test_setup_tui_checklist_arrow_navigation_wraps(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("evander.setup.tui.click.clear", lambda: None)
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()

    _patch_tui_keys(monkeypatch, "down", "enter")
    action = setup_tui._prompt_checklist_action(manager, state)
    assert action.item_id == "admin_web"
    assert action.selected_index == 1

    _patch_tui_keys(monkeypatch, "up", "enter")
    wrapped_action = setup_tui._prompt_checklist_action(manager, state)
    assert wrapped_action.item_id == "launch_verify"
    assert wrapped_action.selected_index == len(manager.checklist(state)) - 1


def test_setup_tui_raw_key_reader_collects_arrow_escape_sequence(monkeypatch) -> None:
    reads = iter([b"\x1b", b"[", b"B"])
    monkeypatch.setattr(setup_tui.os, "read", lambda fd, count: next(reads))
    monkeypatch.setattr(
        setup_tui.select, "select", lambda reads, writes, errors, timeout: (reads, [], [])
    )

    assert setup_tui._read_key_bytes(0) == b"\x1b[B"


def test_setup_tui_normalizes_arrow_escape_sequence() -> None:
    assert setup_tui._normalize_key("\x1b[B") == "down"
    assert setup_tui._normalize_key("\x1b[A") == "up"


@pytest.mark.parametrize(
    ("key", "kind"),
    [
        ("q", "quit"),
        ("p", "plan"),
        ("d", "doctor"),
        ("l", "launch"),
    ],
)
def test_setup_tui_checklist_hotkeys_route(
    tmp_path: Path, monkeypatch, key: str, kind: str
) -> None:
    monkeypatch.setattr("evander.setup.tui.click.clear", lambda: None)
    _patch_tui_keys(monkeypatch, key)
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()

    action = setup_tui._prompt_checklist_action(manager, state)

    assert action.kind == kind
    assert action.selected_index == 0


def test_setup_tui_interactive_admin_no_auth_requires_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("evander.setup.tui.click.clear", lambda: None)
    monkeypatch.setattr(setup_tui, "_is_interactive_terminal", lambda: True)
    _patch_tui_keys(monkeypatch, "down", "enter", "down", "enter", "q")
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")
    state.secret_storage_acknowledged = True
    manager.save_state(state)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "setup",
            "--config",
            str(tmp_path / "runtime"),
            "--project-dir",
            str(ROOT),
        ],
        input="\n\noperator\ny\n",
    )

    assert result.exit_code == 0
    assert "Use Admin Web without authentication?" in result.output
    payload = json.loads((tmp_path / "runtime" / "setup-wizard" / "state.json").read_text())
    assert payload["admin_web"]["auth_mode"] == "none"
    assert payload["admin_web"]["password_configured"] is False


def test_setup_tui_interactive_execution_model_single_select(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("evander.setup.tui.click.clear", lambda: None)
    _patch_tui_keys(monkeypatch, "down", "enter")
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()

    state = setup_tui._configure_item(manager, state, "execution_model", interactive=True)

    assert state.execution["model"] == "container_worker_host"
    assert state.execution["execution_backend"] == "container_worker_vm"


def test_setup_tui_interactive_ai_provider_multiselect_requires_one_and_toggles(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("evander.setup.tui.click.clear", lambda: None)
    monkeypatch.setattr(setup_tui, "_is_interactive_terminal", lambda: True)
    _patch_tui_keys(
        monkeypatch,
        "down",
        "down",
        "down",
        "enter",
        "space",
        "enter",
        "space",
        "down",
        "down",
        "space",
        "enter",
        "q",
    )
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.secret_file = str(tmp_path / "local-secrets.env")
    state.secret_storage_acknowledged = True
    manager.save_state(state)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "setup",
            "--config",
            str(tmp_path / "runtime"),
            "--project-dir",
            str(ROOT),
        ],
        input="n\n\n",
    )

    assert result.exit_code == 0
    assert "Select at least one provider before continuing." in result.output
    payload = json.loads((tmp_path / "runtime" / "setup-wizard" / "state.json").read_text())
    assert payload["ai_providers"] == ["openai", "ollama"]
    assert payload["provider_config"]["local_openai_api_base_url"]


def test_setup_admin_assistant_verification_queues_smoke_message(
    tmp_path: Path, monkeypatch
) -> None:
    created_services = []

    class FakeVmAgentAdmin:
        def resolve_chat_assistant_session(self, **kwargs):
            self.resolve_kwargs = kwargs
            return {"session_id": "assistant-operator"}

    class FakeAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            self.runtime_dir = runtime_dir
            self.vm_agent_admin = FakeVmAgentAdmin()
            self.operations_admin = self
            self.published = []
            created_services.append(self)

        def publish_chat_assistant_message(self, **kwargs):
            self.published.append(kwargs)
            return {"event_id": f"chat:{kwargs['user_message_id']}"}

    monkeypatch.setattr(
        "evander.setup.engine.build_admin_runtime",
        lambda runtime_dir: FakeAdminService(runtime_dir),
    )

    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    state.admin_web["username"] = "operator"
    state.execution = {"execution_backend": "container_worker_vm"}
    state.default_model = "gpt-5.4"

    manager._verify_admin_assistant(state)

    service = created_services[0]
    assert service.vm_agent_admin.resolve_kwargs["user_id"] == "operator"
    assert service.vm_agent_admin.resolve_kwargs["backend"] == "container_worker_vm"
    assert service.published[0]["thread_id"] == "setup-smoke"
    assert service.published[0]["assistant_capability_mode"] == "read-only"
    assert state.assistant_session_id == "assistant-operator"
    assert state.assistant_smoke_event_id.startswith("chat:setup-smoke-")
