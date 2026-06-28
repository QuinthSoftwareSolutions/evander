import json
from pathlib import Path

from typer.testing import CliRunner

from agent_sandbox.cli import app
from agent_sandbox.setup.engine import SetupManager


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
    assert statuses["secret_storage"] == "needs input"
    assert statuses["execution_model"] == "needs input"
    assert statuses["launch_verify"] == "blocked"


def test_setup_tui_explains_setup_profile_choice(tmp_path: Path, monkeypatch) -> None:
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
        input="y\n2\n1\nq\n",
    )

    assert result.exit_code == 0
    assert "This choice tells Evander whether to optimize defaults" in result.output
    assert "Choose local workstation when you are evaluating Evander" in result.output
    assert "Choose dedicated single-machine host" in result.output
    assert result.output.index("This choice tells Evander") < result.output.index(
        "1. Local workstation / evaluation"
    )
    assert len(clear_calls) >= 4


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
            self.published = []
            created_services.append(self)

        def publish_chat_assistant_message(self, **kwargs):
            self.published.append(kwargs)
            return {"event_id": f"chat:{kwargs['user_message_id']}"}

    monkeypatch.setattr("agent_sandbox.setup.engine.AdminService", FakeAdminService)

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
