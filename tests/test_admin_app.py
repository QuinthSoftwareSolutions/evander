from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox import admin_app


class _FakeApp:
    def __init__(self, argv) -> None:
        self.argv = list(argv)

    def exec(self) -> int:
        return 0


class _FakeWindow:
    def __init__(self, service) -> None:
        self.service = service
        self.shown = False

    def show(self) -> None:
        self.shown = True


def test_main_uses_repo_local_runtime_dir_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(admin_app, "QApplication", _FakeApp)
    monkeypatch.setattr(admin_app, "MainWindow", _FakeWindow)
    monkeypatch.setattr(admin_app, "sys", type("FakeSys", (), {"argv": ["agent-sandbox-admin"]}))

    def fake_admin_service(runtime_dir):
        captured["runtime_dir"] = runtime_dir
        return object()

    monkeypatch.setattr(admin_app, "AdminService", fake_admin_service)

    with pytest.raises(SystemExit) as exc:
        admin_app.main()

    assert exc.value.code == 0
    assert captured["runtime_dir"] == Path("runtime")


def test_main_accepts_runtime_dir_override(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    runtime_dir = tmp_path / "custom-runtime"

    monkeypatch.setattr(admin_app, "QApplication", _FakeApp)
    monkeypatch.setattr(admin_app, "MainWindow", _FakeWindow)
    monkeypatch.setattr(
        admin_app,
        "sys",
        type("FakeSys", (), {"argv": ["agent-sandbox-admin", "--runtime-dir", str(runtime_dir)]}),
    )

    def fake_admin_service(selected_runtime_dir):
        captured["runtime_dir"] = selected_runtime_dir
        return object()

    monkeypatch.setattr(admin_app, "AdminService", fake_admin_service)

    with pytest.raises(SystemExit) as exc:
        admin_app.main()

    assert exc.value.code == 0
    assert captured["runtime_dir"] == runtime_dir


def test_config_field_kind_uses_multiline_editor_for_prompt_fields() -> None:
    assert admin_app._config_field_kind("host_heartbeat_resume_prompt") == ("multiline_text", ())
    assert admin_app._config_field_kind("pr_template_markdown") == ("multiline_text", ())
    assert admin_app._config_field_kind("host_resume_script_path") == ("text", ())


def test_config_field_kind_supports_issue_setup_fields() -> None:
    assert admin_app._config_field_kind("git_root") == ("text", ())
    assert admin_app._config_field_kind("enable_guest_heartbeat") == ("bool", ())


def test_config_field_kind_supports_ado_worker_global_fields() -> None:
    assert admin_app._config_field_kind("poll_interval_seconds") == ("float", ())
    assert admin_app._config_field_kind("runtime_dir") == ("text", ())
    assert admin_app._config_field_kind("directories") == ("string_list", ())
    assert admin_app._config_field_kind("include_comments") == ("bool", ())
    assert admin_app._config_field_kind("ado_work_items_azure_devops_pat_env") == ("text", ())


def test_config_field_kind_supports_host_heartbeat_worker_global_fields() -> None:
    assert admin_app._config_field_kind("host_heartbeat_poll_interval_seconds") == ("float", ())
    assert admin_app._config_field_kind("host_busy_check_script_path") == ("text", ())
    assert admin_app._config_field_kind("host_heartbeat_wake_state_path") == ("text", ())
    assert admin_app._config_field_kind("host_heartbeat_directory_paths") == ("string_list", ())


def test_directory_config_domains_include_ado_worker_and_issue_setup_tabs() -> None:
    assert "ado_worker" in admin_app.DIRECTORY_CONFIG_DOMAIN_FILENAMES
    assert "issue_setup" in admin_app.DIRECTORY_CONFIG_DOMAIN_FILENAMES
    assert "pr_template" in admin_app.DIRECTORY_CONFIG_DOMAIN_FILENAMES

    for field_name in admin_app.DIRECTORY_CONFIG_DOMAIN_FIELDS["ado_worker"]:
        assert field_name in admin_app.CONFIG_FIELD_TOOLTIPS
    for field_name in admin_app.DIRECTORY_CONFIG_DOMAIN_FIELDS["issue_setup"]:
        assert field_name in admin_app.CONFIG_FIELD_TOOLTIPS
    for field_name in admin_app.DIRECTORY_CONFIG_DOMAIN_FIELDS["pr_template"]:
        assert field_name in admin_app.CONFIG_FIELD_TOOLTIPS


def test_worker_global_settings_tooltips_include_ado_and_host_heartbeat_fields() -> None:
    for field_name in {
        "directories",
        "ado_work_items_azure_devops_pat_env",
        "host_heartbeat_directory_paths",
        "host_heartbeat_poll_interval_seconds",
        "host_heartbeat_jitter_seconds",
        "host_busy_check_script_path",
        "host_resume_script_path",
    }:
        assert field_name in admin_app.WORKER_GLOBAL_CONFIG_FIELD_TOOLTIPS


def test_preferred_agent_log_repo_name_uses_single_detected_repo() -> None:
    summary = SimpleNamespace(agent_repo_names=["ExampleIndustryAPP"])
    assert admin_app.MainWindow._preferred_agent_log_repo_name(summary) == "ExampleIndustryAPP"


def test_preferred_agent_log_repo_name_skips_ambiguous_or_empty_repo_lists() -> None:
    assert admin_app.MainWindow._preferred_agent_log_repo_name(SimpleNamespace(agent_repo_names=[])) is None
    assert (
        admin_app.MainWindow._preferred_agent_log_repo_name(
            SimpleNamespace(agent_repo_names=["repo-one", "repo-two"])
        )
        is None
    )


def test_apply_vm_refresh_always_refreshes_selected_agent_log() -> None:
    captured: list[str] = []

    class DummyTable:
        def blockSignals(self, _value) -> None:
            pass

        def clearSelection(self) -> None:
            pass

        def setRowCount(self, _value) -> None:
            pass

        def setItem(self, *_args) -> None:
            pass

        def selectRow(self, _row) -> None:
            pass

        def setCurrentCell(self, *_args) -> None:
            pass

    window = SimpleNamespace(
        _vm_summaries=[],
        vm_table=DummyTable(),
        selected_vm_summary=lambda: SimpleNamespace(vm_name="vm-14382"),
        update_vm_action_bar=lambda: None,
        refresh_selected_agent_log=lambda: captured.append("refreshed"),
    )

    admin_app.MainWindow._apply_vm_refresh(
        window,
        [
            SimpleNamespace(
                vm_name="vm-14382",
                issue_number="14382",
                session_ids=[],
                libvirt_state="running",
                ip_address="192.168.250.10",
                agent_state="running",
                agent_repo_names=["ExampleIndustryAPP"],
                last_state_update_at=None,
            )
        ],
    )

    assert captured == ["refreshed"]


def test_pr_template_root_field_starts_editable_without_existing_root_file() -> None:
    assert admin_app._root_config_field_starts_editable("pr_template", "pr_template_markdown", {}) is True
    assert admin_app._root_config_field_starts_editable("pr_template", "pr_template_markdown", {"pr_template_markdown": "x"}) is False
    assert admin_app._root_config_field_starts_editable("policy", "default_session_ttl_seconds", {}) is False


def test_main_window_initializes_worker_splitter(monkeypatch) -> None:
    if admin_app.QApplication is None:
        pytest.skip("PySide6 is not available")

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = admin_app.QApplication.instance() or admin_app.QApplication([])

    class FakeService:
        directory_scoped_config_enabled = True

        def get_directory(self, directory_path):
            return SimpleNamespace(
                ok=True,
                data=SimpleNamespace(
                    details=SimpleNamespace(
                        directory=SimpleNamespace(path="root"),
                    )
                ),
            )

    monkeypatch.setattr(admin_app.MainWindow, "_restore_selected_directory", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "_apply_directory_feature_state", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "_refresh_visible_data", lambda self, manual=False: None)
    monkeypatch.setattr(admin_app.MainWindow, "refresh_issues", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "refresh_sessions", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "refresh_configuration", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "refresh_workers", lambda self, manual=False: None)

    window = admin_app.MainWindow(FakeService())

    assert hasattr(window, "worker_splitter")
    assert window.worker_splitter is not None
    window.close()
    app.processEvents()
