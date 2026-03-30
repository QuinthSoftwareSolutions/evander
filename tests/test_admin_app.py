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


def test_selected_issue_state_filters_support_active_all_and_specific_states() -> None:
    active_window = SimpleNamespace(issue_state_filter=SimpleNamespace(currentText=lambda: "Active"))
    all_window = SimpleNamespace(issue_state_filter=SimpleNamespace(currentText=lambda: "All"))
    specific_window = SimpleNamespace(issue_state_filter=SimpleNamespace(currentText=lambda: "Completed"))

    assert admin_app.MainWindow._selected_issue_state_filters(active_window) == (
        None,
        {"Completed", "Closed"},
    )
    assert admin_app.MainWindow._selected_issue_state_filters(all_window) == (None, None)
    assert admin_app.MainWindow._selected_issue_state_filters(specific_window) == ({"Completed"}, None)


def test_refresh_issues_passes_selected_state_filters() -> None:
    captured: dict[str, object] = {}

    def _list_local_issues(**kwargs):
        captured.update(kwargs)
        return []

    def _start_refresh(key, fn, on_success):
        captured["key"] = key
        captured["issues"] = fn()
        captured["on_success"] = on_success

    window = SimpleNamespace(
        _selected_directory_path="root/team-a",
        _selected_issue_state_filters=lambda: (None, {"Completed", "Closed"}),
        service=SimpleNamespace(list_local_issues=_list_local_issues),
        _start_refresh=_start_refresh,
        _apply_issue_refresh="apply",
    )

    admin_app.MainWindow.refresh_issues(window)

    assert captured == {
        "key": "issues",
        "directory_path": "root/team-a",
        "direct_only": True,
        "states": None,
        "exclude_states": {"Completed", "Closed"},
        "issues": [],
        "on_success": "apply",
    }


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


def test_update_issue_action_bar_shows_yaml_button_for_selected_issue() -> None:
    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

    class DummyButton:
        def __init__(self) -> None:
            self.visible = None

        def show(self) -> None:
            self.visible = True

        def hide(self) -> None:
            self.visible = False

        def setVisible(self, value: bool) -> None:
            self.visible = value

    window = SimpleNamespace(
        issue_selection_hint=DummyLabel(),
        create_issue_vm_button=DummyButton(),
        edit_issue_button=DummyButton(),
        view_issue_yaml_button=DummyButton(),
        delete_issue_button=DummyButton(),
        selected_issue_record=lambda: SimpleNamespace(issue_number="14382", source_type="local_manual"),
    )

    admin_app.MainWindow.update_issue_action_bar(window)

    assert window.issue_selection_hint.text == "Selected: 14382"
    assert window.create_issue_vm_button.visible is True
    assert window.edit_issue_button.visible is True
    assert window.view_issue_yaml_button.visible is True
    assert window.delete_issue_button.visible is True


def test_format_issue_details_includes_reason_when_present() -> None:
    issue = SimpleNamespace(
        issue_number="14385",
        title="Deduplicate bootstrap logic",
        state="Closed",
        directory_path="root/agent-sandbox/test",
        repo_source="/tmp/repo",
        yaml_path="runtime/issues/issue-14385.yaml",
        reason="Out of scope for now",
        description="Needs a concrete defect before refactoring.",
        body="",
    )

    formatted = admin_app._format_issue_details(issue)

    assert "Issue: 14385" in formatted
    assert "State: Closed" in formatted
    assert "Reason: Out of scope for now" in formatted
    assert "Description:" in formatted


def test_refresh_selected_issue_detail_clears_when_no_issue_selected() -> None:
    class DummyTextEdit:
        def __init__(self) -> None:
            self.cleared = False
            self.text = None

        def clear(self) -> None:
            self.cleared = True
            self.text = ""

        def setPlainText(self, value: str) -> None:
            self.text = value

    window = SimpleNamespace(
        issue_detail_view=DummyTextEdit(),
        selected_issue_record=lambda: None,
    )

    admin_app.MainWindow.refresh_selected_issue_detail(window)

    assert window.issue_detail_view.cleared is True


def test_update_vm_action_bar_shows_destroy_for_non_issue_vm() -> None:
    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

        def show(self) -> None:
            pass

    class DummyButton:
        def __init__(self) -> None:
            self.visible = None

        def setVisible(self, value: bool) -> None:
            self.visible = value

        def hide(self) -> None:
            self.visible = False

    window = SimpleNamespace(
        vm_selection_hint=DummyLabel(),
        start_vm_button=DummyButton(),
        shutdown_vm_button=DummyButton(),
        enable_agent_button=DummyButton(),
        disable_agent_button=DummyButton(),
        resume_agent_button=DummyButton(),
        destroy_vm_button=DummyButton(),
        selected_vm_summary=lambda: SimpleNamespace(
            vm_name="lab-vm",
            libvirt_state="running",
            issue_number=None,
            agent_state="unknown",
        ),
    )

    admin_app.MainWindow.update_vm_action_bar(window)

    assert window.vm_selection_hint.text == "Selected: lab-vm"
    assert window.destroy_vm_button.visible is True
    assert window.enable_agent_button.visible is False
    assert window.disable_agent_button.visible is False
    assert window.resume_agent_button.visible is False


def test_show_new_vm_dialog_calls_generic_create_vm(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(admin_app, "QDialog", SimpleNamespace(DialogCode=SimpleNamespace(Accepted=1)))

    class FakeDialog:
        def __init__(self, parent=None) -> None:
            pass

        def exec(self) -> int:
            return admin_app.QDialog.DialogCode.Accepted

        def payload(self) -> dict[str, object]:
            return {
                "vm_name": "lab-vm",
                "session_id": "lab-session",
                "repo_source": "/src/repo",
                "python": True,
                "dotnet": True,
                "nuget": False,
                "playwright": True,
                "mssql": False,
                "mssql_init_root": None,
            }

    monkeypatch.setattr(admin_app, "CreateStandaloneVmDialog", FakeDialog)

    window = SimpleNamespace(
        _selected_directory_path="root/team",
        service=SimpleNamespace(create_vm="create-vm-fn"),
        run_action=lambda label, fn, **kwargs: captured.update({"label": label, "fn": fn, "kwargs": kwargs}),
    )

    admin_app.MainWindow.show_new_vm_dialog(window)

    assert captured["label"] == "Create VM lab-vm"
    assert captured["fn"] == "create-vm-fn"
    assert captured["kwargs"] == {
        "directory_path": "root/team",
        "vm_name": "lab-vm",
        "session_id": "lab-session",
        "repo_source": "/src/repo",
        "python": True,
        "dotnet": True,
        "nuget": False,
        "playwright": True,
        "mssql": False,
        "mssql_init_root": None,
    }


def test_handle_view_selected_issue_yaml_opens_readonly_dialog(tmp_path: Path) -> None:
    yaml_path = tmp_path / "14382.yaml"
    yaml_path.write_text("issue_number: 14382\ntitle: Example\n", encoding="utf-8")
    captured: dict[str, str] = {}

    window = SimpleNamespace(
        selected_issue_record=lambda: SimpleNamespace(issue_number="14382", yaml_path=str(yaml_path)),
        _show_read_only_text_dialog=lambda *, title, text: captured.update({"title": title, "text": text}),
    )

    admin_app.MainWindow.handle_view_selected_issue_yaml(window)

    assert captured == {
        "title": "Issue YAML: 14382",
        "text": "issue_number: 14382\ntitle: Example\n",
    }


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

        def privileged_operations_unlocked(self):
            return False

        def lock_privileged_operations(self):
            return None

    monkeypatch.setattr(admin_app.MainWindow, "_restore_selected_directory", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "_apply_directory_feature_state", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "_refresh_visible_data", lambda self, manual=False: None)
    monkeypatch.setattr(admin_app.MainWindow, "refresh_issues", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "refresh_approvals", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "refresh_sessions", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "refresh_configuration", lambda self: None)
    monkeypatch.setattr(admin_app.MainWindow, "refresh_workers", lambda self, manual=False: None)

    window = admin_app.MainWindow(FakeService())

    assert hasattr(window, "worker_splitter")
    assert window.worker_splitter is not None
    window.close()
    app.processEvents()


def test_update_privilege_status_tracks_service_state() -> None:
    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

    class DummyButton:
        def __init__(self) -> None:
            self.enabled = None

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

    unlocked_state = {"value": False}
    window = SimpleNamespace(
        service=SimpleNamespace(privileged_operations_unlocked=lambda: unlocked_state["value"]),
        privilege_status_label=DummyLabel(),
        unlock_privileges_button=DummyButton(),
    )

    admin_app.MainWindow._update_privilege_status(window)
    assert window.privilege_status_label.text == "Privileges: locked"
    assert window.unlock_privileges_button.enabled is True

    unlocked_state["value"] = True
    admin_app.MainWindow._update_privilege_status(window)
    assert window.privilege_status_label.text == "Privileges: unlocked"
    assert window.unlock_privileges_button.enabled is False


def test_handle_action_success_treats_unsuccessful_result_as_failure(monkeypatch) -> None:
    log_messages: list[str] = []
    refresh_calls: list[str] = []
    dialog_messages: list[tuple[str, str]] = []

    window = SimpleNamespace(
        append_log=lambda message: log_messages.append(message),
        _discard_worker=lambda worker: refresh_calls.append("discarded"),
        _update_privilege_status=lambda: refresh_calls.append("status"),
        _refresh_visible_data=lambda manual=False: refresh_calls.append(f"visible:{manual}"),
        refresh_issues=lambda: refresh_calls.append("issues"),
        refresh_approvals=lambda: refresh_calls.append("approvals"),
        refresh_workers=lambda: refresh_calls.append("workers"),
        _show_action_error_dialog=lambda message: dialog_messages.append(("Action Failed", message)),
    )

    result = SimpleNamespace(success=False, message="Failed to create VM idea-generator-vm: codex failed")

    admin_app.MainWindow._handle_action_success(window, "Create VM idea-generator-vm", result, None, object())

    assert log_messages == [
        "Create VM idea-generator-vm failed: Failed to create VM idea-generator-vm: codex failed"
    ]
    assert dialog_messages == [("Action Failed", "Failed to create VM idea-generator-vm: codex failed")]
    assert refresh_calls == ["discarded", "status", "visible:True", "issues", "approvals", "workers"]


def test_handle_action_failure_uses_scrollable_error_dialog() -> None:
    log_messages: list[str] = []
    dialog_messages: list[str] = []

    window = SimpleNamespace(
        _discard_worker=lambda worker: None,
        append_log=lambda message: log_messages.append(message),
        _update_privilege_status=lambda: None,
        _show_action_error_dialog=lambda message: dialog_messages.append(message),
    )

    admin_app.MainWindow._handle_action_failure(window, "Create VM idea-generator-vm", "very long error", object())

    assert log_messages == ["Create VM idea-generator-vm failed: very long error"]
    assert dialog_messages == ["very long error"]


def test_refresh_selected_approval_detail_uses_background_worker(monkeypatch) -> None:
    updates: list[str] = []
    started_workers: list[object] = []

    class DummySignal:
        def __init__(self) -> None:
            self.callbacks: list[object] = []

        def connect(self, callback) -> None:
            self.callbacks.append(callback)

    class DummyWorker:
        def __init__(self, fn, *args, **kwargs) -> None:
            self.fn = fn
            self.args = args
            self.kwargs = kwargs
            self.signals = SimpleNamespace(finished=DummySignal(), failed=DummySignal())

    monkeypatch.setattr(admin_app, "_ActionWorker", DummyWorker)
    monkeypatch.setattr(admin_app, "_set_text_preserving_scroll", lambda _view, text: updates.append(text))

    request = SimpleNamespace(request_id="approval-1")
    window = SimpleNamespace(
        selected_approval_request=lambda: request,
        approval_detail_view=object(),
        _approval_detail_request_token=0,
        _active_workers=set(),
        service=SimpleNamespace(get_approval_review=lambda request_id: {"request_id": request_id}),
        thread_pool=SimpleNamespace(start=lambda worker: started_workers.append(worker)),
        _handle_approval_detail_success=lambda *args, **kwargs: None,
        _handle_approval_detail_failure=lambda *args, **kwargs: None,
    )

    admin_app.MainWindow.refresh_selected_approval_detail(window)

    assert updates == ["Loading approval details for approval-1..."]
    assert window._approval_detail_request_token == 1
    assert len(started_workers) == 1
    assert len(window._active_workers) == 1
    worker = started_workers[0]
    assert worker.fn is window.service.get_approval_review
    assert worker.args == ("approval-1",)


def test_handle_approval_detail_success_ignores_stale_result(monkeypatch) -> None:
    updates: list[str] = []
    monkeypatch.setattr(admin_app, "_set_text_preserving_scroll", lambda _view, text: updates.append(text))

    request = SimpleNamespace(request_id="approval-2")
    discarded: list[object] = []
    window = SimpleNamespace(
        _discard_worker=lambda worker: discarded.append(worker),
        selected_approval_request=lambda: request,
        _approval_detail_request_token=2,
        approval_detail_view=object(),
    )

    admin_app.MainWindow._handle_approval_detail_success(
        window,
        {"request": {"request_id": "approval-1"}},
        object(),
        1,
        "approval-1",
    )

    assert discarded
    assert updates == []


def test_handle_approval_detail_failure_updates_selected_request(monkeypatch) -> None:
    updates: list[str] = []
    monkeypatch.setattr(admin_app, "_set_text_preserving_scroll", lambda _view, text: updates.append(text))

    request = SimpleNamespace(request_id="approval-1")
    discarded: list[object] = []
    window = SimpleNamespace(
        _discard_worker=lambda worker: discarded.append(worker),
        selected_approval_request=lambda: request,
        _approval_detail_request_token=1,
        approval_detail_view=object(),
    )

    admin_app.MainWindow._handle_approval_detail_failure(
        window,
        "boom",
        object(),
        1,
        "approval-1",
    )

    assert discarded
    assert updates == ["Approval details unavailable: boom"]
