import json
import os
from pathlib import Path
import subprocess

import pytest
from typer.testing import CliRunner

from agent_sandbox.cli import app
from agent_sandbox.setup import tui as setup_tui
from agent_sandbox.setup.engine import SetupManager, SetupPreflightError


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

    state = manager.configure_ai_providers(
        manager.load_state(),
        providers=["openai"],
        secrets_by_env={"OPENAI_API_KEY": "sk-test-secret"},
    )
    checklist = {item.id: item for item in manager.checklist(state)}
    assert checklist["ai_providers"].status == "completed"
    assert "sk-test-secret" not in manager.state_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in (tmp_path / "local-secrets.env").read_text(encoding="utf-8")


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
    assert "AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET" in secret_text
    assert "AGENT_SANDBOX_ADMIN_WEB_PASSWORD" not in secret_text


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
    state.execution = {"execution_backend": "libvirt_vm"}
    state.install_preflight_passed_at = "2026-06-28T00:00:00+00:00"
    manager.save_state(state)
    calls = []

    monkeypatch.setattr(manager, "_write_config_files", lambda state: None)

    def fake_run(args, *, cwd, env, check):
        calls.append({"args": args, "cwd": cwd, "env": env, "check": check})
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr("agent_sandbox.setup.engine.subprocess.run", fake_run)

    with pytest.raises(SetupPreflightError) as exc_info:
        manager.run_install_preflight(state)

    assert exc_info.value.returncode == 1
    assert calls[0]["check"] is False
    assert calls[0]["env"]["AGENT_SANDBOX_SERVICE_PROFILE"] == "setup"
    assert calls[0]["env"]["AGENT_SANDBOX_SETUP_EXECUTION_BACKEND"] == "libvirt_vm"
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


def test_setup_tui_launch_failure_returns_to_checklist(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()
    monkeypatch.setattr(manager, "required_ready", lambda state: True)
    monkeypatch.setattr(manager, "plan", lambda: {"ready": True})
    monkeypatch.setattr(setup_tui.typer, "confirm", lambda *args, **kwargs: True)

    def fail_launch(state):
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
    assert 'AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE="none"' in admin_env
    assert 'AGENT_SANDBOX_ADMIN_WEB_USERNAME="operator"' in admin_env
    assert 'AGENT_SANDBOX_ADMIN_WEB_PASSWORD=""' in admin_env
    assert 'AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET=""' in admin_env
    assert "replace-with-real-password" not in admin_env


def test_setup_writes_container_worker_token_secret_without_state_leak(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("TEST_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(
        "agent_sandbox.setup.engine.secrets.token_urlsafe",
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


def test_setup_tui_omits_nonfunctional_setup_profile_choice(
    tmp_path: Path, monkeypatch
) -> None:
    clear_calls = []
    monkeypatch.setattr("agent_sandbox.setup.tui.click.clear", lambda: clear_calls.append(True))
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


def test_setup_tui_supports_discouraged_no_auth_admin_web(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("agent_sandbox.setup.tui.click.clear", lambda: None)
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
    monkeypatch.setattr("agent_sandbox.setup.tui.click.clear", lambda: None)
    _patch_tui_keys(monkeypatch, "enter")
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()

    action = setup_tui._prompt_checklist_action(manager, state)

    assert action.kind == "item"
    assert action.item_id == "secret_storage"
    assert action.selected_index == 0


def test_setup_tui_checklist_arrow_navigation_wraps(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("agent_sandbox.setup.tui.click.clear", lambda: None)
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
    monkeypatch.setattr(setup_tui.select, "select", lambda reads, writes, errors, timeout: (reads, [], []))

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
    monkeypatch.setattr("agent_sandbox.setup.tui.click.clear", lambda: None)
    _patch_tui_keys(monkeypatch, key)
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()

    action = setup_tui._prompt_checklist_action(manager, state)

    assert action.kind == kind
    assert action.selected_index == 0


def test_setup_tui_interactive_admin_no_auth_requires_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("agent_sandbox.setup.tui.click.clear", lambda: None)
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


def test_setup_tui_interactive_execution_model_single_select(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("agent_sandbox.setup.tui.click.clear", lambda: None)
    _patch_tui_keys(monkeypatch, "down", "enter")
    manager = SetupManager(project_dir=ROOT, runtime_dir=tmp_path / "runtime")
    state = manager.load_state()

    state = setup_tui._configure_item(manager, state, "execution_model", interactive=True)

    assert state.execution["model"] == "container_worker_host"
    assert state.execution["execution_backend"] == "container_worker_vm"


def test_setup_tui_interactive_ai_provider_multiselect_requires_one_and_toggles(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("agent_sandbox.setup.tui.click.clear", lambda: None)
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
        "agent_sandbox.setup.engine.build_admin_runtime",
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
